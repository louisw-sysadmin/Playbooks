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
# Logging configuration
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

root_logger = logging.getLogger()
root_logger.handlers = [file_handler, console_handler]
root_logger.setLevel(logging.INFO)

# quiet flask access logs
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ==============================
# Email configuration (Postfix send-only)
# ==============================
app.config.update(
    MAIL_SERVER="localhost",
    MAIL_PORT=25,
    MAIL_USE_TLS=False,
    MAIL_USE_SSL=False,
    MAIL_DEFAULT_SENDER="noreply@master.lambda.local"
)
mail = Mail(app)

ADMIN_RECIPIENTS = ["louisw@wit.edu"]

# ==============================
# Paths
# ==============================
INVENTORY = "/etc/ansible/hosts"
PLAYBOOK = "/home/sysadmin/Playbooks/playbooks/users/create_user_account.yml"
CSV_AUDIT = "/home/sysadmin/Playbooks/Lambda_Lab/users.csv"

# ==============================
# Helpers
# ==============================
def generate_password(length=10):
    # avoid characters that can be confusing in emails
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))

def is_wit_email(raw):
    _, addr = parseaddr((raw or "").strip())
    if not addr or "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    return bool(local) and domain.casefold() == "wit.edu"

def sanitize_name(value):
    # allow letters, spaces, hyphen, apostrophe, period
    return re.sub(r"[^a-zA-Z .'\-]", "", (value or "")).strip()

def sanitize_email(value):
    return (value or "").strip().lower()

def derive_username(email):
    # take local part, keep safe chars only
    local = email.split("@")[0].lower()
    local = re.sub(r"[^a-z0-9._-]", "", local)
    return local

def parse_failed_hosts(ansible_text):
    failed = set()
    # lines often look like: "lambda3 : FAILED! => ..." or "lambda3 | UNREACHABLE! => ..."
    for line in ansible_text.splitlines():
        if "FAILED!" in line or "UNREACHABLE!" in line:
            host = line.split()[0].strip()
            if host:
                failed.add(host)
    return sorted(failed)

def send_email_notification(fullname, email, username, temp_password, summary):
    # Admin
    admin_msg = Message(
        subject=f"[Lambda GPU Labs] New User Added: {username}",
        recipients=ADMIN_RECIPIENTS,
        body=(
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
    )
    mail.send(admin_msg)

    # Student
    student_msg = Message(
        subject="Your Lambda GPU Lab Account Details",
        recipients=[email],
        body=(
            f"Hello {fullname},\n\n"
            "Your Lambda GPU Lab account has been created successfully.\n\n"
            f"Username: {username}\n"
            f"Temporary Password: {temp_password}\n\n"
            "Please log in and change your password on first use.\n\n"
            "Thanks,\n"
            "WIT School of Computing and Data Science\n"
        )
    )
    mail.send(student_msg)

def append_audit_csv(fullname, email, username, temp_password, summary):
    file_exists = os.path.isfile(CSV_AUDIT)
    os.makedirs(os.path.dirname(CSV_AUDIT), exist_ok=True)
    with open(CSV_AUDIT, "a", newline="") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["Full Name", "Email", "Username", "Temp Password", "Summary"])
        w.writerow([fullname, email, username, temp_password, summary])

# ==============================
# Routes
# ==============================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template("index.html")

    fullname = sanitize_name(request.form.get("fullname", ""))
    email = sanitize_email(request.form.get("email", ""))

    if not fullname:
        return "Full name is required.", 400

    if not is_wit_email(email):
        logging.warning("Rejected non-WIT email attempt: %s", email)
        return "Only @wit.edu email addresses are allowed.", 403

    username = derive_username(email)
    if not username:
        return "Invalid email (could not derive username).", 400

    temp_password = generate_password(10)

    logging.info("Starting provisioning for %s (%s) as '%s'", fullname, email, username)

    extra_vars = {
        "full_name": fullname,
        "email": email,
        "temp_password": temp_password
    }

    try:
        result = subprocess.run(
            [
                "ansible-playbook",
                "-i", INVENTORY,
                PLAYBOOK,
                "--extra-vars", json.dumps(extra_vars)
            ],
            capture_output=True,
            text=True
        )
    except Exception as e:
        logging.exception("Failed to launch ansible-playbook: %s", e)
        return "Internal error running provisioning job.", 500

    ansible_output = (result.stdout or "") + "\n" + (result.stderr or "")
    failed_hosts = parse_failed_hosts(ansible_output)

    if result.returncode != 0 or failed_hosts:
        summary = "⚠️ Some hosts failed or were unreachable: " + (", ".join(failed_hosts) if failed_hosts else "See logs.")
        logging.warning("Provisioning completed with errors. rc=%s failed=%s", result.returncode, failed_hosts)
    else:
        summary = "✅ All hosts completed successfully."
        logging.info("Provisioning completed successfully for %s", username)

    # Audit log (optional)
    try:
        append_audit_csv(fullname, email, username, temp_password, summary)
    except Exception as e:
        logging.warning("Failed to write CSV audit log: %s", e)

    # Email notifications
    try:
        send_email_notification(fullname, email, username, temp_password, summary)
        logging.info("Emails sent to student + admin for %s", username)
    except Exception as e:
        logging.exception("Email send failed for %s: %s", username, e)
        # still show the provisioning result
        return (
            f"Account created for {fullname} ({email}) but email sending failed. "
            f"Summary: {summary}"
        ), 500

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

if __name__ == "__main__":
    # keep debug off in production
    app.run(host="0.0.0.0", port=5000, debug=False)
