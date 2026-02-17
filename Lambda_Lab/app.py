from flask import Flask, render_template, request
from flask_mail import Mail, Message
from email.utils import parseaddr
import subprocess
import csv
import os
import random
import string
import re
import json
import logging

app = Flask(__name__)

# ==============================
# Logging
# ==============================
LOG_FILE = "/var/log/lambda_app.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

file_handler = logging.FileHandler(LOG_FILE)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    "%Y-%m-%d %H:%M:%S"
))

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

logging.getLogger().handlers = [file_handler, console_handler]
logging.getLogger().setLevel(logging.INFO)

flask_log = logging.getLogger("werkzeug")
flask_log.setLevel(logging.ERROR)

# ==============================
# Email config (Postfix on localhost)
# ==============================
app.config.update(
    MAIL_SERVER="localhost",
    MAIL_PORT=25,
    MAIL_USE_TLS=False,
    MAIL_USE_SSL=False,
    MAIL_DEFAULT_SENDER="noreply@master.lambda.local",
)
mail = Mail(app)

ADMIN_RECIPIENTS = ["louisw@wit.edu"]

INVENTORY_PATH = "/etc/ansible/hosts"
PLAYBOOK_PATH = "/home/sysadmin/Playbooks/playbooks/users/create_user_account.yml"

# ==============================
# Helpers
# ==============================
def generate_password(length=12):
    # Safe for chpasswd + email + humans
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))

def is_wit_email(raw):
    _, addr = parseaddr((raw or "").strip())
    if not addr or "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    return bool(local) and domain.lower() == "wit.edu"

def sanitize_name(value):
    # Keep it readable but safe
    return re.sub(r"[^a-zA-Z0-9 .'\-]", "", value or "").strip()

def sanitize_email(value):
    return (value or "").strip().lower()

def parse_ansible_failures(ansible_text):
    failed = set()
    unreachable = set()

    for line in (ansible_text or "").splitlines():
        line = line.strip()
        if not line:
            continue

        # fatal: [lambda7]: FAILED! =>
        m = re.search(r"^fatal:\s*\[(?P<host>[^\]]+)\]:\s*FAILED!", line, re.IGNORECASE)
        if m:
            failed.add(m.group("host"))
            continue

        # fatal: [lambda7]: UNREACHABLE! =>
        m = re.search(r"^fatal:\s*\[(?P<host>[^\]]+)\]:\s*UNREACHABLE!", line, re.IGNORECASE)
        if m:
            unreachable.add(m.group("host"))
            continue

    return sorted(failed), sorted(unreachable)

def send_email_notification(fullname, email, username, temp_password, summary):
    admin_body = (
        "A new user has been created via the Lambda GPU Lab system.\n\n"
        f"Full Name: {fullname}\n"
        f"Email: {email}\n"
        f"Username: {username}\n"
        f"Temporary Password: {temp_password}\n\n"
        "Ansible Summary:\n"
        f"{summary}\n\n"
        "---\n"
        "This email was sent automatically by the Lambda Flask provisioning app.\n"
    )

    student_body = (
        f"Hello {fullname},\n\n"
        "Your Lambda GPU Lab account has been created.\n\n"
        f"Username: {username}\n"
        f"Temporary Password: {temp_password}\n\n"
        "Please log in and change your password on first use.\n\n"
        "Thanks,\n"
        "WIT School of Computing and Data Science\n"
    )

    mail.send(Message(
        subject=f"[Lambda GPU Labs] New User Added: {username}",
        recipients=ADMIN_RECIPIENTS,
        body=admin_body
    ))

    mail.send(Message(
        subject="Your Lambda GPU Lab Account Details",
        recipients=[email],
        body=student_body
    ))

def check_username_exists(username):
    # Checks reachable hosts for an existing username.
    try:
        result = subprocess.run(
            [
                "ansible", "all",
                "-i", INVENTORY_PATH,
                "-m", "shell",
                "-a", f"id -u {username}",
                "--one-line",
            ],
            capture_output=True,
            text=True,
        )

        exists_on = []
        unreachable = []

        for line in (result.stdout + "\n" + result.stderr).splitlines():
            line = line.strip()
            if not line:
                continue
            if "UNREACHABLE!" in line:
                host = line.split()[0]
                unreachable.append(host)
            elif "rc=0" in line:
                host = line.split()[0]
                exists_on.append(host)

        return sorted(set(exists_on)), sorted(set(unreachable))
    except Exception as e:
        logging.error("Username existence check failed: %s", e)
        return [], []

# ==============================
# Routes
# ==============================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        fullname = sanitize_name(request.form.get("fullname", ""))
        email = sanitize_email(request.form.get("email", ""))

        if not fullname or not is_wit_email(email):
            logging.warning("Rejected invalid submission fullname=%r email=%r", fullname, email)
            return "Only @wit.edu email addresses are allowed and name is required.", 403

        username = email.split("@")[0]

        # Pre-check
        exists_on, unreachable = check_username_exists(username)
        if exists_on:
            msg = f"Username '{username}' already exists on: {', '.join(exists_on)}"
            logging.warning(msg)
            return f"""
                <h2 style='font-family:sans-serif; color:#856404; text-align:center; margin-top:50px;'>
                    ⚠️ Username already exists<br>
                    <small>{msg}</small><br><br>
                    No changes were made. Use a different email/username.
                </h2>
                <div style='text-align:center; margin-top:20px;'>
                    <a href='/' style='color:#007bff; text-decoration:none;'>← Back to form</a>
                </div>
            """, 409

        temp_password = generate_password(12)
        logging.info("Starting account creation for %s (%s) as '%s'", fullname, email, username)

        # Save to CSV (optional)
        try:
            file_exists = os.path.isfile("users.csv")
            with open("users.csv", "a", newline="") as csvfile:
                writer = csv.writer(csvfile)
                if not file_exists:
                    writer.writerow(["Full Name", "Email", "Username", "Temp Password"])
                writer.writerow([fullname, email, username, temp_password])
        except Exception as e:
            logging.warning("CSV write failed: %s", e)

        # Run playbook with JSON extra-vars (no quoting hell)
        extra_vars = {
            "full_name": fullname,
            "email": email,
            "temp_password": temp_password,
        }

        result = subprocess.run(
            [
                "ansible-playbook",
                "-i", INVENTORY_PATH,
                PLAYBOOK_PATH,
                "--extra-vars", json.dumps(extra_vars),
            ],
            capture_output=True,
            text=True,
        )

        ansible_text = (result.stdout or "") + "\n" + (result.stderr or "")
        failed_hosts, unreachable_hosts = parse_ansible_failures(ansible_text)

        if result.returncode == 0 and not failed_hosts and not unreachable_hosts:
            summary = "✅ All hosts completed successfully."
        else:
            parts = []
            if failed_hosts:
                parts.append("FAILED: {0}".format(", ".join(failed_hosts)))
            if unreachable_hosts:
                parts.append("UNREACHABLE: {0}".format(", ".join(unreachable_hosts)))
            if not parts:
                parts.append("See logs for details.")
            summary = "⚠️ " + " | ".join(parts)

        logging.info("Account creation finished for %s (%s)", username, summary)

        # Email admin + student
        try:
            send_email_notification(fullname, email, username, temp_password, summary)
            logging.info("Emails sent successfully to %s and admin for %s", email, username)
        except Exception as e:
            logging.error("Email send failed for %s: %s", username, e)

        return f"""
        <h2 style='font-family:sans-serif; color:#155724; text-align:center; margin-top:50px;'>
            ✅ Account creation initiated for {fullname}<br>
            <small>{summary}</small><br><br>
            Please check your WIT email for credentials.
        </h2>
        <div style='text-align:center; margin-top:20px;'>
            <a href='/' style='color:#007bff; text-decoration:none;'>← Back to form</a>
        </div>
        """

    return render_template("index.html")


if __name__ == "__main__":
    # For dev only; systemd should run gunicorn
    app.run(host="0.0.0.0", port=5000, debug=True)
