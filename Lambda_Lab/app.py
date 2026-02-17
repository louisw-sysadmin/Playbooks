from flask import Flask, render_template, request
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

flask_log = logging.getLogger('werkzeug')
flask_log.setLevel(logging.ERROR)

# ==============================
# Mail (send-only) configuration
# ==============================
FROM_ADDR = "noreply@master.cs.wit.edu"
ADMIN_ADDR = "louisw@wit.edu"
SENDMAIL_BIN = "/usr/sbin/sendmail"   # Postfix provides this

# ==============================
# Helper functions
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

def _sendmail(to_addr, subject, body, from_addr=FROM_ADDR):
    # header injection protection
    for v in [to_addr, subject, from_addr]:
        if "\n" in v or "\r" in v:
            raise ValueError("Invalid header value")

    msg = (
        "From: {0}\n"
        "To: {1}\n"
        "Subject: {2}\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "\n"
        "{3}\n"
    ).format(from_addr, to_addr, subject, body)

    subprocess.run(
        [SENDMAIL_BIN, "-t", "-oi"],
        input=msg.encode("utf-8"),
        check=True
    )

def send_email_notification(fullname, email, username, password, ansible_summary):
    admin_subject = "[Lambda GPU Labs] New User Added: {0}".format(username)
    admin_body = (
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

    student_subject = "Your Lambda GPU Lab Account Details"
    student_body = (
        "Hello {0},\n\n"
        "Your Lambda GPU Lab account has been created successfully.\n\n"
        "Username: {1}\n"
        "Temporary Password: {2}\n\n"
        "Please log in and change your password on first use.\n\n"
        "Thanks,\n"
        "WIT School of Computing and Data Science\n"
    ).format(fullname, username, password)

    _sendmail(ADMIN_ADDR, admin_subject, admin_body)
    _sendmail(email, student_subject, student_body)

    logging.info("Emails sent successfully to {0} and admin for {1}".format(email, username))

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

        for line in (result.stdout + result.stderr).splitlines():
            line = line.strip()
            if not line:
                continue
            if "UNREACHABLE!" in line:
                host = line.split()[0]
                unreachable.append(host)
            elif "rc=0" in line:
                host = line.split()[0]
                exists_on.append(host)

        return exists_on, unreachable

    except Exception as e:
        logging.error("Username existence check failed for {0}: {1}".format(username, e))
        return [], []

def summarize_ansible_failures(ansible_output, returncode):
    failed_hosts = []

    for line in ansible_output.splitlines():
        if re.search(r"(UNREACHABLE!|FAILED!)", line, re.IGNORECASE):
            try:
                host = line.split()[0].strip()
                failed_hosts.append(host)
            except Exception:
                continue

    failed_hosts = sorted(list(set(failed_hosts)))

    if returncode != 0 or failed_hosts:
        if failed_hosts:
            return "⚠️ Some hosts failed or were unreachable: {0}".format(", ".join(failed_hosts))
        return "⚠️ Ansible returned non-zero exit code ({0}). See logs for details.".format(returncode)

    return "✅ All hosts completed successfully."

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
            msg = "Rejected non-WIT email attempt: {0}".format(email)
            logging.warning(msg)
            return "Only @wit.edu email addresses are allowed.", 403

        username = email.split("@")[0].strip()
        if not username:
            return "Invalid email username.", 400

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

        # Save user to CSV (WARNING: stores password in cleartext)
        file_exists = os.path.isfile("users.csv")
        with open("users.csv", "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            if not file_exists:
                writer.writerow(["Full Name", "Email", "Username", "Password"])
            writer.writerow([fullname, email, username, password])

        # Run Ansible playbook (ONLY fullname + email come from the website)
        # Username/password are derived here server-side.
        extra_vars = (
            "{{"
            "\"full_name\":\"{0}\","
            "\"email\":\"{1}\","
            "\"username\":\"{2}\","
            "\"password\":\"{3}\""
            "}}"
        ).format(
            fullname.replace('"', ""),
            email.replace('"', ""),
            username.replace('"', ""),
            password.replace('"', "")
        )

        result = subprocess.run(
            [
                "ansible-playbook",
                "-i", "/etc/ansible/hosts",
                "../playbooks/users/create_user_account.yml",
                "--extra-vars", extra_vars
            ],
            capture_output=True,
            text=True
        )

        ansible_output = (result.stdout or "") + "\n" + (result.stderr or "")
        summary = summarize_ansible_failures(ansible_output, result.returncode)

        try:
            send_email_notification(fullname, email, username, password, summary)
        except Exception as e:
            logging.error("Email sending failed: {0}".format(e))

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
    app.run(host="0.0.0.0", port=5000, debug=True)
