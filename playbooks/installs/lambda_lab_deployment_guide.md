# Wentworth Lambda GPU Lab Deployment Guide
 
**Domain:** `lambdalab.cs.wit.edu` • **Public IP:** `69.43.73.8` • **Server (LAN IP):** `10.0.0.150`

> This runbook deploys a Flask+Ansible provisioning app that creates Linux accounts across Lambda hosts, enforces `@wit.edu` emails, sets initial passwords, forces password change on first login, emails credentials, and logs to file. It also covers hosting the app over HTTPS and publishing via OPNsense 1:1 NAT — while keeping UniFi private.

---

## 0) Topology & Goals
- **App VM/Server (10.0.0.150)** runs:
  - Flask provisioning app (with Ansible integration)
  - Gunicorn (WSGI) + Nginx reverse proxy
  - Local mail relay (Postfix) or external SMTP
  - UniFi controller (LAN-only)
- **Public exposure:** Only the Flask app over HTTPS
- **Firewall (OPNsense):** 1:1 NAT from `69.43.73.8 → 10.0.0.150`; WAN rules allow only TCP 80/443 to that public IP
- **Ansible inventory:** Lambda hosts reachable via SSH from the app server

---

## 1) Prerequisites
1. OS packages (Debian/Ubuntu family assumed):
   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv python3-pip        ansible git nginx certbot python3-certbot-nginx        mailutils postfix
   ```
   - Postfix installer: choose **Local only** (or use external SMTP later).

2. Ansible inventory at `/etc/ansible/hosts` (example):
   ```ini
   [lambda]
   lambda1 ansible_host=10.64.0.101
   lambda2 ansible_host=10.64.0.102
   # ... etc ...
   ```

3. Passwordless SSH from the app server to all Lambda hosts:
   ```bash
   ssh-keygen -t ed25519 -C "lambda-app@wit"  # if you don't have a key yet
   ssh-copy-id sysadmin@10.64.0.101           # repeat for all hosts
   ansible all -i /etc/ansible/hosts -m ping
   ```

4. System timezone (recommended EDT):
   ```bash
   sudo timedatectl set-timezone America/New_York
   timedatectl
   ```

---

## 2) Project Layout
Place the app under the sysadmin home:
```
/home/sysadmin/Playbooks/Lambda_Lab/
├── app.py                  # Flask app (keep separate from this doc)
├── ../playbooks/users/create_user_account.yml         # Ansible play to create users
├── templates/
│   └── index.html          # Simple form (name, email)
├── users.csv               # Append-only audit of created users
├── venv/                   # Python virtualenv
└── (optional) requirements.txt
```

> **Note:** Your latest `app.py` already includes:
> - `@wit.edu` domain enforcement
> - username pre-check across all hosts (`ansible ... id -u <user>`)
> - logging to `/var/log/lambda_app.log` (warnings+), live INFO to console
> - emails to admin + student
> - robust Ansible handling (emails even if some hosts unreachable)

---

## 3) Python Env & App Dependencies
```bash
cd ~/Playbooks/Lambda_Lab
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install flask flask-mail gunicorn
# Optional: freeze
pip freeze > requirements.txt
```

**Create log file and set permissions (once):**
```bash
sudo touch /var/log/lambda_app.log
sudo chown sysadmin:sysadmin /var/log/lambda_app.log
sudo chmod 664 /var/log/lambda_app.log
```

**Quick local test:**
```bash
# dev server (do NOT use in production)
python3 app.py
# visit http://10.0.0.150:5000
```

---

## 4) Ansible Playbook (user creation)
File: `~/Playbooks/playbooks/users/create_user_account.yml`
```yaml
---
- name: Add new user to Lambda servers
  hosts: all
  become: true

  vars:
    username: "{{ username }}"
    full_name: "{{ full_name }}"
    email: "{{ email }}"
    password: "{{ password }}"
    group: students

  tasks:
    - name: Ensure 'students' group exists
      ansible.builtin.group:
        name: "{{ group }}"
        state: present

    - name: Create or update user account
      ansible.builtin.user:
        name: "{{ username }}"
        shell: /bin/bash
        create_home: yes
        password: "{{ password | password_hash('sha512') }}"
        groups: "{{ group }}"
        append: yes

    - name: Set full name and email as comment
      ansible.builtin.command:
        cmd: "chfn -f '{{ full_name }} ({{ email }})' {{ username }}"

    - name: Force password change on first login
      ansible.builtin.command:
        cmd: "chage -d 0 {{ username }}"

    - name: Confirmation message
      ansible.builtin.debug:
        msg: "User {{ username }} created in group '{{ group }}' and must change password at first login."
```

**Manual test (bypass Flask):**
```bash
echo '{"username":"demo","full_name":"Demo User","email":"demo@wit.edu","password":"Temp123"}' | ansible-playbook -i /etc/ansible/hosts ~/Playbooks/playbooks/users/create_user_account.yml --extra-vars=@/dev/stdin
```

---

## 5) Email Delivery
**Option A: Local Postfix relay (internal use)**
- Ensure Postfix is running: `sudo systemctl status postfix`
- Test:
  ```bash
  echo "Postfix test" | mail -s "Test" your.name@wit.edu
  ```

**Option B: External SMTP (e.g., Gmail)**
- In `app.py`, set:
  ```python
  MAIL_SERVER="smtp.gmail.com"
  MAIL_PORT=587
  MAIL_USE_TLS=True
  MAIL_USERNAME="youraccount@gmail.com"
  MAIL_PASSWORD="app_password"  # from Google account 2FA App Passwords
  MAIL_DEFAULT_SENDER="youraccount@gmail.com"
  ```

**Troubleshooting:** Check `/var/log/lambda_app.log` and Flask console for `[ERROR] Failed to send email`.

---

## 6) Run via Gunicorn (WSGI) + systemd
**Create service: `/etc/systemd/system/lambda_app.service`**
```ini
[Unit]
Description=Lambda GPU Lab Flask App
After=network.target

[Service]
User=sysadmin
Group=sysadmin
WorkingDirectory=/home/sysadmin/Playbooks/Lambda_Lab
Environment="PATH=/home/sysadmin/Playbooks/Lambda_Lab/venv/bin"
ExecStart=/home/sysadmin/Playbooks/Lambda_Lab/venv/bin/gunicorn -b 127.0.0.1:5000 app:app
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Enable & start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable lambda_app
sudo systemctl start lambda_app
sudo systemctl status lambda_app
sudo journalctl -u lambda_app -f
```

---

## 7) Nginx Reverse Proxy + HTTPS (Let’s Encrypt)
**Site config:** `/etc/nginx/sites-available/lambda_app`
```nginx
server {
    listen 80;
    server_name lambdalab.cs.wit.edu;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Enable & reload:**
```bash
sudo ln -s /etc/nginx/sites-available/lambda_app /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

**Issue HTTPS cert:**
```bash
sudo certbot --nginx -d lambdalab.cs.wit.edu
sudo systemctl status certbot.timer
sudo certbot renew --dry-run
```

---

## 8) Publish via OPNsense (1:1 NAT) — Keep UniFi Private
**Goal:** Publicly expose only the Flask app over HTTPS using a dedicated public IP `69.43.73.8`, map it 1:1 to `10.0.0.150`, and allow only TCP 80/443 from WAN. UniFi remains LAN-only.

### A) OPNsense — 1:1 NAT
- **Firewall → NAT → 1:1 → Add**
  - Interface: **WAN**
  - Type: **1:1**
  - External subnet IP: **69.43.73.8**
  - Internal IP: **10.0.0.150**
  - Destination: **any**
  - Description: Public IP for Lambda Flask app
  - (Optional) NAT reflection: **enabled** (for inside-LAN testing)

### B) OPNsense — WAN Rules
- **Firewall → Rules → WAN → Add (Allow)**  
  - Action: **Pass**  
  - Protocol: **TCP**  
  - Destination: **69.43.73.8**  
  - Ports: **80, 443**  
  - Description: Allow public web to Flask app
- **Firewall → Rules → WAN → Add (Block)**  
  - Action: **Block**  
  - Protocol: **any**  
  - Destination: **69.43.73.8**  
  - Description: Block all other inbound to Flask app public IP

> **LAN subnet:** keep your internal rules generic to your environment (e.g., allow management from your **LAN subnet** only).

### C) Keep UniFi private
Bind UniFi to the LAN IP only (varies by install). In `/usr/lib/unifi/system.properties` (or vendor path), add:
```
bind.address=10.0.0.150
```
Then:
```bash
sudo systemctl restart unifi
```

---

## 9) Operations & Maintenance
**Health checks**
- App service:
  ```bash
  systemctl status lambda_app
  journalctl -u lambda_app -f
  ```
- Nginx:
  ```bash
  sudo nginx -t && sudo systemctl reload nginx
  sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log
  ```
- Certificates:
  ```bash
  sudo certbot renew --dry-run
  ```

**Logs**
- App file log (warnings+): `/var/log/lambda_app.log`
- Console (INFO live): `journalctl -u lambda_app -f`
- Ansible stdout for last run: available in `journalctl` output

**Backups**
- `~/Playbooks/Lambda_Lab/users.csv`
- `~/Playbooks/Lambda_Lab/app.py`, `~/Playbooks/playbooks/users/create_user_account.yml`, `templates/`
- `/etc/nginx/sites-available/lambda_app`
- `/etc/systemd/system/lambda_app.service`
- `/etc/ansible/hosts`

**Updates**
```bash
cd ~/Playbooks/Lambda_Lab
source venv/bin/activate
pip install --upgrade -r requirements.txt
sudo systemctl restart lambda_app
sudo systemctl reload nginx
```

---

## 10) Troubleshooting
**Ansible exit codes**
- `0` = OK
- `2` = OK (changes made)
- `4` = Unreachable hosts (app still emails; logged as warning)
- `8` = Task failed (check play, credentials, or permissions)

**Common issues**
- **Port in use (5000):** stop any old Flask dev servers, ensure Gunicorn binds to `127.0.0.1:5000`.
- **No email:** verify Postfix, or switch to external SMTP in `app.py`.
- **Time mismatch in logs:** `sudo timedatectl set-timezone America/New_York`.
- **Permissions writing log:** create `/var/log/lambda_app.log` and set ownership to `sysadmin`.
- **UniFi exposed publicly:** ensure it binds to `10.0.0.150` only; OPNsense rules block public access.

---

## Appendix A — File Reference
- **Flask app:** `/home/sysadmin/Playbooks/Lambda_Lab/app.py`  
  (contains username pre-check, WIT-only email enforcement, logging, email, Ansible integration)
- **Ansible playbook:** `/home/sysadmin/Playbooks/playbooks/users/create_user_account.yml`
- **Nginx site config:** `/etc/nginx/sites-available/lambda_app`
- **systemd unit:** `/etc/systemd/system/lambda_app.service`
- **Inventory:** `/etc/ansible/hosts`
- **Log file:** `/var/log/lambda_app.log`

---

## Appendix B — Security Checklist
- [ ] App reachable only via HTTPS at `https://lambdalab.cs.wit.edu`
- [ ] UniFi bound to LAN IP only
- [ ] OPNsense 1:1 NAT to `69.43.73.8` with WAN rules: **only 80/443 allowed**
- [ ] SSH allowed only from trusted management networks
- [ ] Regular updates: `apt upgrade` + `pip upgrade`
- [ ] Backups of critical configs and users.csv
- [ ] Certbot renew working (`--dry-run` passes)

---

**End of Runbook**  
Contact: School of Computing and Data Science — Infrastructure Team
