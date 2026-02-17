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

root_logger = logging.getLogger()
root_logger.handlers = [file_handler, console_handler]
root_logger.setLevel(logging.INFO)

logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ==============================
# Email (send-only via local postfix)
# ==============================
app.config.update(
    MAIL_SERVER="localhost",
    MAIL_PORT=25,
    MAIL_USE_TLS=False,
    MAIL_USE_SSL=False,
    MAIL_DEFAULT_SENDER="noreply@master.lambda.local"
)
mail = Mail(app)

ADMIN_RECIPIENTS = ["louisw@wit.edu"]  # add more if needed

# ==============================
# Helpers
# ==============================
def sanitize_input(value):
    # Keep it strict. No weird chars.
    return re.sub(r"[^a-zA-Z0-9@.\-_' ]", "", (value or "").strip())

def is_wit_email(raw):
    _, addr = parseaddr((raw or "").strip())
    if not addr or "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    return bool(local) and domain.casefold() == "wit.edu"

def derive_username(email_addr):
    # email local-part -> linux-friendly username
    local = (email_addr or "").split("@")[0].lower().strip()
    local = re.sub(r"[^a-z0-9._-]", "", local)
    return local

def generate_password(length=12):
    # simple but solid temp password; no symbols to avoid quoting issues
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))

def parse_ansible_failures(ansible_text):
    """
    Returns (failed_hosts, unreachable_hosts) as sorted unique lists.
    Supports common callbacks:
      - fatal: [host]: FAILED! =>
      - fatal: [host]: UNREACHABLE! =>
      - host | UNREACHABLE! =>
      - host : FAILED! =>
    """
    failed = set()
    unreachable = set()

    for raw_line in (ansible_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        m = re.search(r"^fatal:\s*\[(?P<host>[^\]]+)\]:\s*FAILED!", line, re.IGNORECASE)
        if m:
            failed.add(m.group("host"))
            continue

        m = re.search(r"^fatal:\s*\[(?P<host>[^\]]+)\]:\s*UNREACHABLE!", line, re.IGNORECASE)
        if m:
            unreachable.add(m.group("host"))
            continue

        m = re.search(r"^(?P<host>[^ \t|]+)\s*\|\s*UNREACHABLE!", line, re.IGNORECASE)
        if m:
            unreachable.add(m.group("host"))
            continue

        m = re.search(r"^(?P<host>[^ \t:]+)\s*:\s*FAILED!", line, re.IGNORECASE)
        if m:
            failed.add(m.group("host"))
            continue

    return sorted(failed), sorted(unreachable)

def summarize_ansible(ansible_text, returncode):
    failed_hosts, unreachable_hosts = parse_ansible_failures(ansible_text)

    if returncode == 0 and not failed_hosts and not unreachable_hosts:
        return "✅ All hosts completed successfully."

    parts = []
    if failed_hosts:
        parts.append("FAILED: {0}".format(", ".join(failed_hosts)))
    if unreachable_hosts:
        parts.append("UNREACHABLE: {0}".format(", ".join(unreachable_hosts)))

    if not parts:
        parts.append("Ansible returned exit code {0} (check logs)".format(returncode))

    return "⚠️ " + " | ".join(parts)

def send_email_notification(fullname, email, username, password, ansible_summary):
    # Admin email
    admin_msg = Message(
        subject="[Lambda GPU Labs] New User Request: {0}".format(username),
        recipients=ADMIN_RECIPIENTS,
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

    # Student email
    student_msg = Message(
        subject="Your Lambda GPU Lab Account Details",
        recipients=[email],
        body=(
            "Hello {0},\n\n"
            "Your Lambda GPU Lab account has been created.\n\n"
            "Username: {1}\n"
            "Temporary Password: {2}\n\n"
            "Please log in and change your password on first use.\n\n"
            "Thanks,\n"
            "WIT School of Computing and Data Science\n"
        ).format(fullname, username, password)
    )
    mail.send(student_msg)

# Optional pre-check: username exists anywhere
def check_username_exists(username):
    try:
        result = subprocess.run(
            [
                "ansible", "all",
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
            line = line.strip()
            if not line:
                continue
            if "UNREACHABLE!" in line:
                unreachable.append(line.split()[0])
            elif "rc=0" in line:
                exists_on.append(line.split()[0])

        return sorted(set(exists_on)), sorted(set(unreachable))
    except Exception as e:
        logging.warning("Username existence check failed: %s", e)
        return [], []

# ==============================
# Routes
# ==============================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        fullname = sanitize_input(request.form.get("fullname"))
        email = sanitize_input(request.form.get("email")).lower()

        if not fullname or not email:
            return "Full name and email are required.", 400

        if not is_wit_email(email):
            logging.warning("Rejected non-WIT email attempt: %s", email)
            return "Only @wit.edu email addresses are allowed.", 403

        username = derive_username(email)
        if not username:
            return "Could not derive a valid username from the email.", 400

        logging.info("Pre-check: verifying username availability for '%s'", username)
        exists_on, unreachable = check_username_exists(username)
        if exists_on:
            msg = "Username '{0}' already exists on: {1}".format(username, ", ".join(exists_on))
            logging.warning(msg)
            return (
                "<h2 style='font-family:sans-serif; color:#856404; text-align:center; margin-top:50px;'>"
                "⚠️ Username already exists<br>"
                "<small>{0}</small><br><br>"
                "No changes were made. Use a different email/username."
                "</h2>"
                "<div style='text-align:center; margin-top:20px;'>"
                "<a href='/' style='color:#007bff; text-decoration:none;'>← Back to form</a>"
                "</div>"
            ).format(msg), 409

        password = generate_password()
        logging.info("Starting account creation for %s (%s) as '%s'", fullname, email, username)

        # Save request to CSV (local audit)
        try:
            csv_path = os.path.join(os.path.dirname(__file__), "users.csv")
            file_exists = os.path.isfile(csv_path)
            with open(csv_path, "a", newline="") as csvfile:
                writer = csv.writer(csvfile)
                if not file_exists:
                    writer.writerow(["Full Name", "Email", "Username", "Password"])
                writer.writerow([fullname, email, username, password])
        except Exception as e:
            logging.warning("CSV write failed: %s", e)

        # Run Ansible playbook (ONLY needs full_name + email; playbook derives username and uses one shared password)
        extra_vars = (
            "full_name='{0}' "
            "email='{1}' "
            "temp_password='{2}'"
        ).format(fullname, email, password)

        playbook_path = "/home/sysadmin/Playbooks/playbooks/users/create_user_account.yml"

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

        ansible_text = (result.stdout or "") + "\n" + (result.stderr or "")
        summary = summarize_ansible(ansible_text, result.returncode)

        # Log a short summary; keep full output in journald logs if needed
        logging.info("Account creation finished for %s (%s)", username, summary)

        # Email admin + student
        try:
            send_email_notification(fullname, email, username, password, summary)
            logging.info("Emails sent successfully to %s and admins for %s", email, username)
        except Exception as e:
            logging.error("Email send failed for %s: %s", username, e)

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
    # for local debug only; systemd runs gunicorn
    app.run(host="127.0.0.1", port=5000, debug=True)
