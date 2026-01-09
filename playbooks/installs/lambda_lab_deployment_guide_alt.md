## 🧩 Lambda GPU Lab Deployment Guide

### **Overview**
This document outlines the setup and deployment process for the **Lambda GPU Lab Flask Application**, hosted securely on-prem via **Nginx**, **Gunicorn**, and **OPNsense Firewall (HA Pair)**.  
The app supports HTTPS access externally using a dedicated public IP via **1:1 NAT and Port Forwarding**.

---

## **1. Server Configuration**

**Hostname:** `master.cs.wit.edu`  
**Internal IP:** `10.64.0.150`  
**External IP (NAT):** `69.43.73.8`  
**Domain (planned):** `lambdalab.cs.wit.edu`  

**Core Software:**
- Ubuntu 24.04 LTS
- Python 3.12 (with `venv`)
- Flask + Gunicorn
- Nginx (reverse proxy)
- OPNsense 25.7.4 (firewall + NAT)

---

## **2. Application Service Setup**

### **Python Environment**
```bash
cd ~/Playbooks/Lambda_Lab
python3 -m venv venv
source venv/bin/activate
pip install flask gunicorn
```

### **Gunicorn Systemd Service**
File: `/etc/systemd/system/lambda_app.service`
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

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable lambda_app
sudo systemctl start lambda_app
sudo systemctl status lambda_app
```

✅ **Gunicorn listens on**: `127.0.0.1:5000`

---

## **3. Nginx Reverse Proxy**

### **SSL Certificate**
```bash
sudo mkdir -p /etc/ssl/lambda
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048   -keyout /etc/ssl/lambda/lambda.key   -out /etc/ssl/lambda/lambda.crt   -subj "/CN=lambdalab.cs.wit.edu"
```

### **Site Configuration**
File: `/etc/nginx/sites-available/lambda_lab`
```nginx
server {
    listen 80;
    server_name lambdalab.cs.wit.edu;

    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name lambdalab.cs.wit.edu;

    ssl_certificate /etc/ssl/lambda/lambda.crt;
    ssl_certificate_key /etc/ssl/lambda/lambda.key;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and test:
```bash
sudo ln -s /etc/nginx/sites-available/lambda_lab /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/lambda_app
sudo nginx -t
sudo systemctl reload nginx
```

✅ **Nginx listens on:** ports **80** (HTTP redirect) and **443** (HTTPS proxy)

---

## **4. OPNsense Configuration**

### **Network Overview**
| Interface | Network | Description |
|------------|----------|-------------|
| igb1 | 69.43.73.0/24 | WAN |
| igb2 | 10.64.0.0/24 | LAN2 (App Network) |
| igb3 | 10.128.0.0/24 | LAN3 |
| igb0 | 10.0.1.0/24 | PFSYNC |
| ovpns1 | 172.16.1.0/24 | VPN Clients |

### **NAT Configuration**
✅ **1:1 NAT (for HTTPS)**  
```
Interface: WAN (igb1)
External IP: 69.43.73.8
Internal IP: 10.64.0.150
```

✅ **Port Forwarding**
```
WAN TCP 80  ->  10.64.0.150:80
WAN TCP 443 ->  10.64.0.150:443
```

✅ **Firewall Rules**
```
Allow TCP 80, 443 on WAN to 10.64.0.150
```

✅ **Verification**
```bash
nc -zv 10.64.0.150 80
nc -zv 10.64.0.150 443
```
HTTP succeeded, HTTPS verified after Nginx SSL configuration.

---

## **5. Persistence After Reboot**

| Component | Persistence | Notes |
|------------|-------------|-------|
| Flask App (Gunicorn) | ✅ via `systemd enable` | Restarts automatically |
| Nginx | ✅ | Loads site configs on boot |
| SSL Certificate | ✅ | Stored in `/etc/ssl/lambda` |
| OPNsense NAT + Firewall | ✅ | Config saved in HA sync |
| Domain (planned) | Pending | `lambdalab.cs.wit.edu` points to `69.43.73.8` |

---

## **6. Next Steps**

### **Optional Enhancements**
1. 🔒 **Let’s Encrypt SSL**
   - Use `certbot --nginx -d lambdalab.cs.wit.edu`
   - Auto-renews every 90 days.
2. 🚀 **HTTP → HTTPS redirect** already implemented.
3. 📊 **Monitoring**
   - Enable access logs in `/var/log/nginx/access.log`
   - Use `systemctl status lambda_app` for live service health.
4. 🧱 **Failover HA**
   - Ensure CARP VIP syncs correctly between OPNsense nodes.

---

## **7. Verification Checklist**

| Test | Command | Expected Result |
|------|----------|----------------|
| App running | `sudo systemctl status lambda_app` | Active (running) |
| Web listener | `sudo ss -tulpn | grep 443` | Nginx bound to 0.0.0.0:443 |
| Local HTTP | `curl http://127.0.0.1:5000` | Flask HTML |
| External HTTPS | `curl -I https://69.43.73.8 --insecure` | 200 OK / Redirect |
| Domain access | `https://lambdalab.cs.wit.edu` | Loads app securely |

---

✅ **Deployment Status:**  
**Operational** — public HTTPS configured, reverse proxy working, NAT verified, services persistent across reboot.  
Next phase: enable trusted SSL via Let’s Encrypt and domain DNS binding.
