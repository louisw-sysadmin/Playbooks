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
# Paths / constants
# ==============================
PLAYBOOK_PATH = "/home/sysadmin/Playbooks/playbooks/users/create_user_account.yml"
INVENTORY_PATH = "/etc/ansible/hosts"

LOG_FILE = "/var/log/lambda_app.log"
ANSIBLE_RUN_DIR = "/var/log/lambda_ansible_runs"

ADMIN_RECIPIENTS = ["louisw@wit.edu"]  # add more if you want

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.makedirs(ANSIBLE_RUN_DIR, exist_ok=True)

# ==============================
# Logging configuration
# ==============================
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

# Quiet Flask access logs
flask_log = logging.getLogger('werkzeug')
flask_log.setLevel(logging.ERROR)

# ==============================
# Email configuration (send-only via localhost postfix)
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
    # decent entropy but still user-typable
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def is_wit_email(raw):
    _, addr = parseaddr((raw or "").strip())
    if not addr or "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    return bool(local) and domain.casefold() == "wit.edu"

def sanitize_input(value):
    return re.sub(r"[^a-zA-Z0-9@.\-_' ]", "", value or "").strip()

def derive_username_from_email(email):
    return re.sub(r"[^a-z0-9._-]", "", (email or "").lower().split("@")[0])

def parse_ansible_failures(ansible_text):
    """
    Returns (failed_hosts, unreachable_hosts) as sorted unique lists.
    Matches:
      fatal: [lambda7]: FAILED! =>
      fatal: [lambda7]: UNREACHABLE! =>
    """
    failed = set()
    unreachable = set()

    for line in (ansible_text or "").splitlines():
        line = line.strip()

        m = re.search(r"^fatal:\s*\[(?P<host>[^\]]+)\]:\s*FAILED!", line, re.IGNORECASE)
        if m:
            failed.add(m.group("host"))
            continue

        m = re.search(r"^fatal:\s*\[(?P<host>[^\]]+)\]:\s*UNREACHABLE!", line, re.IGNORECASE)
        if m:
            unreachable.add(m.group("host"))
            continue

    return sorted(failed), sorted(unreachable)

def check_username_exists(username):
    """
    Fast check: does user already exist anywhere reachable?
    """
    try:
        result = subprocess.run(
            [
                "ansible",
                "all",
                "-i", INVENTORY_PATH,
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
                # line begins with hostname in --one-line output
                host = line.split()[0]
                unreachable.append(host)
            elif "rc=0" in line:
                host = line.split()[0]
                exists_on.append(host)

        return sorted(set(exists_on)), sorted(set(unreachable))

    except Exception as e:
        logging.error("Username existence check failed for {0}: {1}".format(username, e))
        return [], []

def send_email_notification(fullname, email, username, password, ansible_summary, run_log_path=None):
    try:
        extra = ""
        if run_log_path:
            extra = "\nRun log: {0}\n".format(run_log_path)

        admin_msg = Message(
            subject="[Lambda GPU Labs] New User Added: {0}".format(username),
            recipients=ADMIN_RECIPIENTS,
            body=(
                "A new user has been created via the Lambda GPU Lab system.\n\n"
                "Full Name: {0}\n"
                "Email: {1}\n"
                "Username: {2}\n"
                "Temporary Password: {3}\n\n"
                "Ansible Summary:\n{4}\n"
                "{5}\n"
                "---\n"
                "This email was sent automatically by the Lambda Flask provisioning app.\n"
            ).format(fullname, email, username, password, ansible_summary, extra)
        )
        mail.send(admin_msg)

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

        logging.info("Emails sent successfully to {0} and admins for {1}".format(email, username))
    except Exception as e:
        logging.error("Failed to send email for {0}: {1}".format(username, e))

def run_ansible_create_user(username, full_name, email, password):
    """
    Runs the playbook and returns (returncode, summary_string, log_path)
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_log_path = os.path.join(ANSIBLE_RUN_DIR, "{0}-{1}.log".format(username, ts))

    extra_vars = {
        "username": username,
        "full_name": full_name,
        "email": email,
        "password": password,
    }

    cmd = [
        "ansible-playbook",
        "-i", INVENTORY_PATH,
        PLAYBOOK_PATH,
        "--extra-vars", str(extra_vars).replace("'", '"')  # JSON-ish; safe for simple strings
    ]

    logging.info("Running: {0}".format(" ".join(cmd)))

    result = subprocess.run(cmd, capture_output=True, text=True)
    ansible_output = (result.stdout or "") + "\n" + (result.stderr or "")

    # Always write full output for debugging
    try:
        with open(run_log_path, "w") as f:
            f.write(ansible_output)
    except Exception as e:
        logging.error("Could not write ansible run log {0}: {1}".format(run_log_path, e))
        run_log_path = None

    failed_hosts, unreachable_hosts = parse_ansible_failures(ansible_output)

    # Build a clean summary
    if result.returncode == 0 and not failed_hosts and not unreachable_hosts:
        summary = "✅ All hosts completed successfully."
    else:
        parts = []
        if failed_hosts:
            parts.append("FAILED: {0}".format(", ".join(failed_hosts)))
        if unreachable_hosts:
            parts.append("UNREACHABLE: {0}".format(", ".join(unreachable_hosts)))
        if not parts:
            parts.append("See run log for details.")
        summary = "⚠️ " + " | ".join(parts)

    logging.warning("Ansible exit code={0} summary={1}".format(result.returncode, summary))
    return result.returncode, summary, run_log_path

# ==============================
# Routes
# ==============================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        fullname = sanitize_input(request.form.get("fullname"))
        email = sanitize_input(request.form.get("email"))

        if not fullname:
            return "Full name is required.", 400

        if not is_wit_email(email):
            logging.warning("Rejected non-WIT email attempt: {0}".format(email))
            return "Only @wit.edu email addresses are allowed.", 403

        username = derive_username_from_email(email)
        if not username:
            return "Could not derive a valid username from email.", 400

        logging.info("Pre-check: verifying username availability for '{0}'".format(username))
        exists_on, unreachable = check_username_exists(username)
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

        # Save user to CSV (optional audit trail)
        try:
            csv_path = os.path.join(os.path.dirname(__file__), "users.csv")
            file_exists = os.path.isfile(csv_path)
            with open(csv_path, "a", newline="") as csvfile:
                writer = csv.writer(csvfile)
                if not file_exists:
                    writer.writerow(["Full Name", "Email", "Username", "Password"])
                writer.writerow([fullname, email, username, password])
        except Exception as e:
            logging.error("Failed writing users.csv: {0}".format(e))

        # Run Ansible
        rc, summary, run_log_path = run_ansible_create_user(username, fullname, email, password)

        # Email results (always)
        send_email_notification(fullname, email, username, password, summary, run_log_path=run_log_path)

        logging.info("Account creation finished for {0} ({1})".format(username, summary))

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
    # gunicorn runs this via app:app, but this is handy for local debug
    app.run(host="0.0.0.0", port=5000, debug=True)
