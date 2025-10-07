# 🧠 Lambda Lab User Management System Setup Guide

## 1. System Requirements
Make sure your server (or management node) has:
- Ubuntu 22.04+ or Debian 12+
- Python 3.10 or newer
- Ansible installed
- Internet access to install packages

---

## 2. Directory Structure

```bash
mkdir -p ~/Playbooks/Lambda_Lab
cd ~/Playbooks/Lambda_Lab
```

---

## 3. Create and Activate Python Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

If you ever exit the environment, re-activate it with:
```bash
source ~/Playbooks/Lambda_Lab/venv/bin/activate
```

---

## 4. Install Required Python Packages

Inside the virtual environment:
```bash
pip install flask ansible passlib flask-mail
```

---

## 5. Flask App Setup (app.py)

This web app allows you to add new student users, automatically create them across your Lambda hosts via Ansible, and email them their credentials.

Create the file:
```bash
nano app.py
```

Paste this code:
```python
from flask import Flask, render_template, request, redirect
from flask_mail import Mail, Message
import subprocess, random, string, os

app = Flask(__name__)

# Flask-Mail Configuration (uses local Postfix SMTP)
app.config.update(
    MAIL_SERVER='localhost',
    MAIL_PORT=25,
    MAIL_USE_TLS=False,
    MAIL_USE_SSL=False,
    MAIL_DEFAULT_SENDER='noreply@lab.cs.wit.edu'
)

mail = Mail(app)

def generate_password(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        username = request.form['username']
        fullname = request.form['fullname']
        email = request.form['email']
        password = generate_password()

        cmd = [
            "ansible-playbook", "create_user.yml",
            "--extra-vars", f"username={username} full_name='{fullname}' email={email} password={password}"
        ]

        try:
            subprocess.run(cmd, check=True)
            msg = Message(
                subject="Your Lambda Lab Account",
                recipients=[email],
                body=f"Hello {fullname},\n\nYour new Lambda Lab account has been created.\n\nUsername: {username}\nPassword: {password}\n\nPlease change your password upon first login."
            )
            mail.send(msg)
            return redirect('/')
        except subprocess.CalledProcessError as e:
            return f"<h3>Ansible failed:</h3><pre>{e}</pre>"
    return render_template('index.html')

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
```

---

## 6. HTML Template (templates/index.html)

```bash
mkdir templates
nano templates/index.html
```

Paste this:
```html
<!DOCTYPE html>
<html>
<head>
  <title>Add Student</title>
</head>
<body>
  <h1>Add a New Student</h1>
  <form method="POST">
    <label>Username:</label><br>
    <input name="username" required><br><br>

    <label>Full Name:</label><br>
    <input name="fullname" required><br><br>

    <label>Email:</label><br>
    <input name="email" required type="email"><br><br>

    <button type="submit">Create Student</button>
  </form>
</body>
</html>
```

---

## 7. Ansible Playbook (create_user.yml)

```bash
nano create_user.yml
```

Paste this:
```yaml
---
- name: Add new user to Linux servers
  hosts: all
  become: true
  vars:
    group: students
  tasks:
    - name: Ensure 'students' group exists
      ansible.builtin.group:
        name: "{{ group }}"
        state: present

    - name: Ensure user exists
      ansible.builtin.user:
        name: "{{ username }}"
        comment: "{{ full_name }}"
        shell: /bin/bash
        create_home: yes
        password: "{{ password | password_hash('sha512') }}"
        groups: "{{ group }}"
        append: yes
```

---

## 8. Configure Send-Only Postfix SMTP Server

```bash
sudo apt install postfix mailutils -y
```

Choose:
- **"Internet Site"**
- Mail name: `lab.cs.wit.edu`

Edit `/etc/postfix/main.cf`:
```
myhostname = lab.cs.wit.edu
myorigin = /etc/mailname
inet_interfaces = loopback-only
relayhost =
smtp_use_tls = yes
smtp_tls_security_level = encrypt
smtp_tls_loglevel = 1
smtp_tls_CAfile = /etc/ssl/certs/ca-certificates.crt
```

Restart Postfix:
```bash
sudo systemctl restart postfix
sudo systemctl enable postfix
```

Test email:
```bash
echo "Test mail" | mail -s "Test" your_email@domain.com
```

---

## 9. Run the Flask App

```bash
python3 app.py
```

Access in browser:
```
http://<server-ip>:5000
```

---

## 10. Optional: Auto-Start Flask on Boot

```bash
sudo nano /etc/systemd/system/lambda_flask.service
```

Add:
```
[Unit]
Description=Lambda Flask App
After=network.target

[Service]
User=sysadmin
WorkingDirectory=/home/sysadmin/Playbooks/Lambda_Lab
ExecStart=/home/sysadmin/Playbooks/Lambda_Lab/venv/bin/python3 app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable:
```bash
sudo systemctl enable lambda_flask
sudo systemctl start lambda_flask
```

---

✅ **Done!**
- Flask app running on port 5000  
- Ansible automation for user creation  
- Random passwords generated securely  
- Students automatically emailed their credentials  
- Send-only Postfix SMTP mail server configured
