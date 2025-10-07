from flask import Flask, render_template, request, redirect
from flask_mail import Mail, Message
from email.utils import parseaddr
import subprocess
import csv
import os
import random
import string
import re
import logging

app = Flask(__name__)

# ==============================
# Logging configuration (file: warnings+, console: info+)
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
console_handler.setFormatter(logging.Formatter(
    "[%(levelname)s] %(message)s"
))

logging.getLogger().handlers = [file_handler, console_handler]
logging.getLogger().setLevel(logging.DEBUG)

# Quiet Flask access logs in the file
flask_log = logging.getLogger('werkzeug')
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
# Helpers
# ==============================
def generate_password(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def is_wit_email(raw):
    name, addr = parseaddr((raw or "").strip())
    if not addr or "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    return bool(local) and domain.casefold() == "wit.edu"

def sanitize_input(value):
    return re.sub(r"[^a-zA-Z0-9@.\-_' ]", "", value or "")

def send_email_notification(fullname, email, username, password, ansible_summary):
    try:
        admin_msg = Message(
            subject=f"[Lambda GPU Labs] New User Added: {username}",
            recipients=["louisw@wit.edu"],  # admin
            body=f"""
A new user has been created via the Lambda GPU Lab system.

Full Name: {fullname}
Email: {email}
Username: {username}
Temporary Password: {password}

Ansible Summary:
{ansible_summary}

---
This email was sent automatically by the Flask provisioning app.
"""
        )
        mail.send(admin_msg)

        student_msg = Message(
            subject="Your Lambda GPU Lab Account Details",
            recipients=[email],
            body=f"""
Hello {fullname},

Your Lambda GPU Lab account has been created successfully.

Username: {username}
Temporary Password: {password}

Please log in and change your password on first use.

Thanks,
WIT School of Computing and Data Science
"""
        )
        mail.send(student_msg)

        logging.info(f"Emails sent successfully to {email} and admin for {username}")
    except Exception as e:
        logging.error(f"Failed to send email for {username}: {e}")
        print(f"[ERROR] Failed to send email: {e}")

def check_username_exists(username):
    """
    Ask Ansible across all hosts if the user exists.
    Returns (exists_on_hosts, unreachable_hosts).
    """
    try:
        # Use an ad-hoc Ansible command to check 'id -u <username>'
        res = subprocess.run(
            [
                "ansible",
                "-i", "/etc/ansible/hosts",
                "all",
                "-m", "command",
                "-a", f"id -u {username}"
            ],
            capture_output=True,
            text=True
        )
        stdout = res.stdout or ""
        stderr = res.stderr or ""
        exists = []
        unreachable = []

        # Parse typical ad-hoc output lines, e.g.:
        # lambda1 | SUCCESS | rc=0 >>
        # lambda15 | UNREACHABLE! => ...
        for line in (stdout + "\n" + stderr).splitlines():
            line = line.strip()
            if not line:
                continue
            # UNREACHABLE
            if "UNREACHABLE!" in line:
                host = line.split()[0]
                unreachable.append(host)
            # SUCCESS with rc=0 means user exists
            elif "| SUCCESS" in line and "rc=0" in line:
                host = line.split()[0]
                exists.append(host)
            # FAILURE with rc=1 typically means user does NOT exist on that host
            # We don't need to record non-existence.
        return exists, unreachable
    except Exception as e:
        logging.error(f"Username existence check failed for {username}: {e}")
        # On failure to check, assume unknown; don’t block creation
        return [], []

# ==============================
# Routes
# ==============================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        fullname = sanitize_input(request.form.get("fullname"))
        email = sanitize_input(request.form.get("email"))

        # Enforce WIT email
        if not is_wit_email(email):
            msg = f"Rejected non-WIT email attempt: {email}"
            logging.warning(msg)
            return "Only @wit.edu email addresses are allowed.", 403

        username = email.split("@")[0]
        logging.info(f"Pre-check: verifying username availability for '{username}'")

        # ====== NEW: pre-check for existing username across hosts ======
        exists_on, unreachable = check_username_exists(username)
        if exists_on:
            msg = f"Username '{username}' already exists on: {', '.join(exists_on)}"
            logging.warning(msg)
            # Show a friendly message and stop (no changes made)
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
        # Note: unreachable hosts don’t block; we continue but will report later.
        if unreachable:
            logging.warning(f"Pre-check: some hosts unreachable during existence check: {', '.join(unreachable)}")

        # Proceed with creation
        password = generate_password()
        logging.info(f"Starting account creation for {fullname} ({email}) as '{username}'")

        # Save to CSV
        file_exists = os.path.isfile("users.csv")
        with open("users.csv", "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            if not file_exists:
                writer.writerow(["Full Name", "Email", "Username", "Password"])
            writer.writerow([fullname, email, username, password])

        # Run Ansible playbook to create user (playbook is idempotent)
        extra_vars = (
            f"username='{username}' "
            f"full_name='{fullname}' "
            f"email='{email}' "
            f"password='{password}'"
        )

        result = subprocess.run(
            [
                "ansible-playbook",
                "-i", "/etc/ansible/hosts",
                "create_user.yml",
                "--extra-vars", extra_vars
            ],
            capture_output=True,
            text=True
        )

        # Parse Ansible results
        ansible_output = result.stdout + "\n" + result.stderr
        failed_hosts = []
        for line in ansible_output.splitlines():
            if "UNREACHABLE!" in line or "FAILED!" in line:
                host = line.split()[0]
                failed_hosts.append(host)

        if failed_hosts:
            summary = f"⚠️ Some hosts failed or unreachable: {', '.join(failed_hosts)}"
            logging.warning(f"Ansible completed with issues: {failed_hosts}")
        else:
            summary = "✅ All hosts completed successfully."
            logging.info("Ansible completed successfully on all hosts.")

        # Always send emails, even with partial host issues
        send_email_notification(fullname, email, username, password, summary)
        logging.info(f"Account creation finished for {username} ({summary})")

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

# ==============================
# Start Flask app
# ==============================
if __name__ == "__main__":
    # Make sure /var/log/lambda_app.log is writable by this user:
    #   sudo touch /var/log/lambda_app.log && sudo chown sysadmin:sysadmin /var/log/lambda_app.log && sudo chmod 664 /var/log/lambda_app.log
    app.run(host="0.0.0.0", port=5000, debug=True)
