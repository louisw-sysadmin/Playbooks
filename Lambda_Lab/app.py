# /home/sysadmin/Playbooks/Lambda_Lab/app.py

from flask import Flask, render_template, request
from flask_mail import Mail, Message
from email.utils import parseaddr
import subprocess
import csv
import os
import random
import string
import re
import logging
import json

app = Flask(__name__)

# ==============================
# Logging configuration
# ==============================
LOG_FILE = "/var/log/lambda_app.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

file_handler = logging.FileHandler(LOG_FILE)
file_handler.setLevel(logging.WARNING)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    "%Y-%m-%d %H:%M:%S"
))

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

logging.getLogger().handlers = [file_handler, console_handler]
logging.getLogger().setLevel(logging.DEBUG)

logging.getLogger("werkzeug").setLevel(logging.ERROR)

# ==============================
# Email configuration (send-only via local postfix)
# ==============================
app.config.update(
    MAIL_SERVER="localhost",
    MAIL_PORT=25,
    MAIL_USE_TLS=False,
    MAIL_USE_SSL=False,
    MAIL_DEFAULT_SENDER="noreply@master.lambda.local"
)
mail = Mail(app)

# ==============================
# Helper functions
# ==============================
def generate_password(length=10):
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))

def is_wit_email(raw):
    _, addr = parseaddr((raw or "").strip())
    if not addr or "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    return bool(local) and domain.casefold() == "wit.edu"

def sanitize_input(value):
    return re.sub(r"[^a-zA-Z0-9@.\-_' ]", "", value or "").strip()

def parse_ansible_failures(ansible_text):
    """
    Extract real hostnames for failures/unreachable from Ansible output.
    Handles common formats:
      - fatal: [host]: FAILED! =>
      - host | UNREACHABLE! =>
      - host : FAILED! (some callback plugins)
    Returns (failed_hosts, unreachable_hosts) as sorted unique lists.
    """
    failed = set()
    unreachable = set()

    for raw_line in (ansible_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # fatal: [lambda7]: FAILED! =>
        m = re.search(r"^fatal:\s*\[(?P<host>[^\]]+)\]:\s*FAILED!", line, re.IGNORECASE)
        if m:
            failed.add(m.group("host"))
            continue

        # lambda7 | UNREACHABLE! =>
        m = re.search(r"^(?P<host>[^ \t|]+)\s*\|\s*UNREACHABLE!", line, re.IGNORECASE)
        if m:
            unreachable.add(m.group("host"))
            continue

        # fatal: [lambda7]: UNREACHABLE! =>
        m = re.search(r"^fatal:\s*\[(?P<host>[^\]]+)\]:\s*UNREACHABLE!", line, re.IGNORECASE)
        if m:
            unreachable.add(m.group("host"))
            continue

        # Some callbacks: host : FAILED! =>
        m = re.search(r"^(?P<host>[^ \t:]+)\s*:\s*FAILED!", line, re.IGNORECASE)
        if m:
            failed.add(m.group("host"))
            continue

    return sorted(failed), sorted(unreachable)

def build_summary(ansible_text, returncode):
    failed_hosts, unreachable_hosts = parse_ansible_failures(ansible_text)

    if returncode == 0 and not failed_hosts and not unreachable_hosts:
        return "✅ All hosts completed successfully."

    parts = []
    if failed_hosts:
        parts.append("FAILED: {0}".format(", ".join(failed_hosts)))
    if unreachable_hosts:
        parts.append("UNREACHABLE: {0}".format(", ".join(unreachable_hosts)))

    if not parts:
        # non-zero but no parsed failures (rare, but happens)
        parts.append("Ansible returned exit code {0}. Check logs.".format(returncode))

    return "⚠️ " + " | ".join(parts)

def send_email_notification(fullname, email, username, password, ansible_summary):
    try:
        admin_msg = Message(
            subject="[Lambda GPU Labs] New User Added: {0}".format(username),
            recipients=["louisw@wit.edu"],
            body=(
                "A new user has been created via the Lambda GPU Lab system.\n\n"
                "Full Name: {0}\n"
                "Email: {1}\n"
                "Username: {2}\n"
                "Temporary Password: {3}\n\n"
                "Ansible Summary:\n"
                "{4}\n\n"
                "---\n"
                "This email was sent automatically by the Lambda Flask provisioning app.\n"
            ).format(fullname, email, username, password, ansible_summary)
        )
        mail.send(admin_msg)

        student_msg = Message(
            subject="Your Lambda GPU Lab Account Details",
            recipients=[email],
            body=(
                "Hello {0},\n\n"
                "Your Lambda GPU Lab account has been created successfully.\n\n"
                "Username: {1}\n"
                "Temporary Password: {2}\n\n"
                "Please log in and change your password on first use.\n\n"
                "Thanks,\n"
                "WIT School of Computing and Data Science\n"
            ).format(fullname, username, password)
        )
        mail.send(student_msg)

        logging.info("Emails sent successfully to {0} and admin for {1}".format(email, username))
    except Exception as e:
        logging.error("Failed to send email for {0}: {1}".format(username, e))

def check_username_exists(username):
    try:
        result = subprocess.run(
            [
                "ansible",
                "all",
                "-i", "/etc/ansible/hosts",
                "-m", "shell",
                "-a", "id -u {0}".format(username),
                "--one-line"
            ],
            capture_output=True,
            text=True
        )

        exists_on = []
        unreachable = []

        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        for line in combined.splitlines():
            s = line.strip()
            if not s:
                continue
            if "UNREACHABLE!" in s:
                unreachable.append(s.split()[0])
            elif "rc=0" in s:
                exists_on.append(s.split()[0])

        return exists_on, unreachable
    except Exception as e:
        logging.error("Username existence check failed for {0}: {1}".format(username, e))
        return [], []

# ==============================
# Routes
# ==============================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        fullname = sanitize_input(request.form.get("fullname"))
        email = sanitize_input(request.form.get("email"))

        if not fullname or not email:
            return "Full name and email are required.", 400

        if not is_wit_email(email):
            logging.warning("Rejected non-WIT email attempt: {0}".format(email))
            return "Only @wit.edu email addresses are allowed.", 403

        username = email.split("@")[0].strip().lower()

        # Pre-check
        exists_on, _unreach = check_username_exists(username)
        if exists_on:
            msg = "Username '{0}' already exists on: {1}".format(username, ", ".join(exists_on))
            logging.warning(msg)
            return """
                <h2 style='font-family:sans-serif; color:#856404; text-align:center; margin-top:50px;'>
                    ⚠️ Username already exists<br>
                    <small>{0}</small><br><br>
                    No changes were made. Choose a different email/username.
                </h2>
                <div style='text-align:center; margin-top:20px;'>
                    <a href='/' style='color:#007bff; text-decoration:none;'>← Back to form</a>
                </div>
            """.format(msg), 409

        password = generate_password()
        logging.info("Starting account creation for {0} ({1}) as '{2}'".format(fullname, email, username))

        # Save CSV (best-effort)
        try:
            file_exists = os.path.isfile("users.csv")
            with open("users.csv", "a", newline="") as csvfile:
                writer = csv.writer(csvfile)
                if not file_exists:
                    writer.writerow(["Full Name", "Email", "Username", "Password"])
                writer.writerow([fullname, email, username, password])
        except Exception as e:
            logging.warning("Could not write users.csv: {0}".format(e))

        playbook_path = "/home/sysadmin/Playbooks/playbooks/users/create_user_account.yml"

        # IMPORTANT: JSON extra-vars avoids shell quoting issues
        extra_vars = json.dumps({
            "full_name": fullname,
            "email": email,
            "username": username,
            "password": password
        })

        result = subprocess.run(
            [
                "ansible-playbook",
                "-i", "/etc/ansible/hosts",
                playbook_path,
                "--extra-vars", extra_vars
            ],
            capture_output=True,
            text=True
        )

        ansible_output = (result.stdout or "") + "\n" + (result.stderr or "")
        summary = build_summary(ansible_output, result.returncode)

        if result.returncode != 0:
            logging.warning("Ansible exit code {0}".format(result.returncode))
            logging.warning(ansible_output)

        send_email_notification(fullname, email, username, password, summary)

        return """
        <h2 style='font-family:sans-serif; color:#155724; text-align:center; margin-top:50px;'>
            ✅ Account creation initiated for {0}<br>
            <small>{1}</small><br><br>
            Please check your WIT email for credentials.
        </h2>
        <div style='text-align:center; margin-top:20px;'>
            <a href='/' style='color:#007bff; text-decoration:none;'>← Back to form</a>
        </div>
        """.format(fullname, summary)

    return render_template("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
