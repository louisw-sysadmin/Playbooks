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
from datetime import datetime

app = Flask(__name__)

# ==============================
# Paths (ABSOLUTE + SAFE)
# ==============================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INVENTORY_PATH = "/etc/ansible/hosts"
PLAYBOOK_PATH = "/home/sysadmin/Playbooks/playbooks/users/create_user_account.yml"
CSV_PATH = os.path.join(BASE_DIR, "users.csv")

ANSIBLE_RUN_DIR = "/var/log/lambda_ansible_runs"
os.makedirs(ANSIBLE_RUN_DIR, exist_ok=True)

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

flask_log = logging.getLogger("werkzeug")
flask_log.setLevel(logging.ERROR)

# ==============================
# Email configuration
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
def generate_password(length=14):
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))

def is_wit_email(raw):
    _, addr = parseaddr((raw or "").strip())
    if not addr or "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    return bool(local) and domain.casefold() == "wit.edu"

def sanitize_input(value):
    return re.sub(r"[^a-zA-Z0-9@.\-_' ]", "", value or "")

def parse_ansible_failures(ansible_text):
    failed = set()
    unreachable = set()

    for line in ansible_text.splitlines():
        line = line.strip()

        m = re.search(r"^fatal:\s*\[(?P<host>[^\]]+)\]:\s*FAILED!", line, re.IGNORECASE)
        if m:
            failed.add(m.group("host").strip())
            continue

        m = re.search(r"^fatal:\s*\[(?P<host>[^\]]+)\]:\s*UNREACHABLE!", line, re.IGNORECASE)
        if m:
            unreachable.add(m.group("host").strip())
            continue

    return sorted(failed), sorted(unreachable)

def build_summary(returncode, failed_hosts, unreachable_hosts):
    if returncode == 0 and not failed_hosts and not unreachable_hosts:
        return "✅ SUCCESS: All hosts completed successfully."

    parts = []
    if failed_hosts:
        parts.append("FAILED: {0}".format(", ".join(failed_hosts)))
    if unreachable_hosts:
        parts.append("UNREACHABLE: {0}".format(", ".join(unreachable_hosts)))

    if not parts:
        parts.append("Ansible returned a non-zero exit code (no fatal/unreachable lines found).")

    return "⚠️ " + " | ".join(parts)

def send_email_notification(fullname, email, username, password, ansible_summary, run_log_path):
    try:
        admin_msg = Message(
            subject=f"[Lambda GPU Labs] New User Added: {username}",
            recipients=["louisw@wit.edu"],
            body=f"""A new user has been created via the Lambda GPU Lab system.

Full Name: {fullname}
Email: {email}
Username: {username}
Temporary Password: {password}

Ansible Summary:
{ansible_summary}

Run Log:
{run_log_path}

---
This email was sent automatically by the Lambda Flask provisioning app.
"""
        )
        mail.send(admin_msg)

        student_msg = Message(
            subject="Your Lambda GPU Lab Account Details",
            recipients=[email],
            body=f"""Hello {fullname},

Your Lambda GPU Lab account has been created successfully.

Username: {username}
Temporary Password: {password}

Please log in and change your password on first use.

Thanks,
WIT School of Computing and Data Science
"""
        )
        mail.send(student_msg)

        logging.info("Emails sent successfully to %s and admin for %s", email, username)
    except Exception as e:
        logging.error("Failed to send email for %s: %s", username, e)

def check_username_exists(username):
    try:
        result = subprocess.run(
            ["ansible", "all", "-i", INVENTORY_PATH, "-m", "shell", "-a", f"id -u {username}", "--one-line"],
            capture_output=True,
            text=True
        )

        exists_on = []
        unreachable = []

        for line in (result.stdout + result.stderr).splitlines():
            line = line.strip()
            if not line:
                continue
            if "UNREACHABLE!" in line:
                unreachable.append(line.split()[0])
            elif "rc=0" in line:
                exists_on.append(line.split()[0])

        return exists_on, unreachable
    except Exception as e:
        logging.error("Username existence check failed for %s: %s", username, e)
        return [], []

# ==============================
# Routes
# ==============================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        fullname = sanitize_input(request.form.get("fullname"))
        email = sanitize_input(request.form.get("email"))

        if not is_wit_email(email):
            logging.warning("Rejected non-WIT email attempt: %s", email)
            return "Only @wit.edu email addresses are allowed.", 403

        username = email.split("@")[0].strip().lower()
        logging.info("Pre-check: verifying username availability for '%s'", username)

        exists_on, unreachable = check_username_exists(username)
        if exists_on:
            msg = f"Username '{username}' already exists on: {', '.join(exists_on)}"
            logging.warning(msg)
            return f"""
                <h2 style='font-family:sans-serif; color:#856404; text-align:center; margin-top:50px;'>
                    ⚠️ Username already exists<br>
                    <small>{msg}</small><br><br>
                    No changes were made. Choose a different email/username.
                </h2>
                <div style='text-align:center; margin-top:20px;'>
                    <a href='/' style='color:#007bff; text-decoration:none;'>← Back to form</a>
                </div>
            """, 409

        password = generate_password()
        logging.info("Starting account creation for %s (%s) as '%s'", fullname, email, username)

        # Save user to CSV (local record)
        file_exists = os.path.isfile(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            if not file_exists:
                writer.writerow(["Full Name", "Email", "Username", "Password"])
            writer.writerow([fullname, email, username, password])

        # Per-run log file
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_log_path = os.path.join(ANSIBLE_RUN_DIR, f"{username}_{stamp}.log")

        # Pass vars as JSON (no quoting problems)
        extra_vars = {
            "username": username,
            "full_name": fullname,
            "email": email,
            "password": password
        }

        cmd = [
            "ansible-playbook",
            "-i", INVENTORY_PATH,
            PLAYBOOK_PATH,
            "--extra-vars", str(extra_vars).replace("'", '"')
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=BASE_DIR)

        ansible_output = (result.stdout or "") + "\n" + (result.stderr or "")
        with open(run_log_path, "w") as f:
            f.write(ansible_output)

        failed_hosts, unreachable_hosts = parse_ansible_failures(ansible_output)
        summary = build_summary(result.returncode, failed_hosts, unreachable_hosts)

        if result.returncode != 0:
            logging.warning("Ansible exit code=%s summary=%s", result.returncode, summary)
        else:
            logging.info("Ansible success summary=%s", summary)

        send_email_notification(fullname, email, username, password, summary, run_log_path)
        logging.info("Account creation finished for %s (%s)", username, summary)

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
    app.run(host="0.0.0.0", port=5000, debug=True)
