from flask import Flask, render_template, request, redirect
from flask_mail import Mail, Message
from email.utils import parseaddr
import subprocess
import csv
import os
import random
import string
import re

app = Flask(__name__)

# ==============================
# Email configuration
# ==============================
app.config.update(
    MAIL_SERVER="localhost",          # Use Postfix local relay
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
    """Generate a random password."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def is_wit_email(raw):
    """Validate @wit.edu email."""
    name, addr = parseaddr((raw or "").strip())
    if not addr or "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    return bool(local) and domain.casefold() == "wit.edu"

def sanitize_input(value):
    """Prevent dangerous characters."""
    return re.sub(r"[^a-zA-Z0-9@.\-_' ]", "", value)

def send_email_notification(fullname, email, username, password, ansible_summary):
    """Send emails to admin and student with Ansible results."""
    try:
        # Admin email
        admin_msg = Message(
            subject=f"[Lambda GPU Labs] New User Added: {username}",
            recipients=["louisw@wit.edu"],  # Change to your admin email
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

        # Student email
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

        print(f"[INFO] Emails sent successfully to {email} and admin.")

    except Exception as e:
        print(f"[ERROR] Failed to send email: {e}")

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
            return "Only @wit.edu email addresses are allowed.", 403

        username = email.split("@")[0]
        password = generate_password()

        # Save user to CSV
        file_exists = os.path.isfile("users.csv")
        with open("users.csv", "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            if not file_exists:
                writer.writerow(["Full Name", "Email", "Username", "Password"])
            writer.writerow([fullname, email, username, password])

        # Run Ansible playbook
        extra_vars = (
            f"username='{username}' "
            f"full_name='{fullname}' "
            f"email='{email}' "
            f"password='{password}'"
        )

        print(f"[INFO] Running Ansible for {username}...")

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

        # Build readable Ansible summary
        ansible_output = result.stdout + "\n" + result.stderr
        failed_hosts = []
        for line in ansible_output.splitlines():
            if "UNREACHABLE!" in line or "FAILED!" in line:
                host = line.split()[0]
                failed_hosts.append(host)

        if failed_hosts:
            summary = f"⚠️ Some hosts failed or unreachable: {', '.join(failed_hosts)}"
        else:
            summary = "✅ All hosts completed successfully."

        print("[SUMMARY]:", summary)

        # Always send emails even if some nodes failed
        send_email_notification(fullname, email, username, password, summary)

        # Show success page to user
        return f"""
        <h2 style='font-family:sans-serif; color:#155724; text-align:center; margin-top:50px;'>
            ✅ Account created for {fullname}<br>
            <small>{summary}</small><br><br>
            Please check your WIT email for credentials.
        </h2>
        <div style='text-align:center; margin-top:20px;'>
            <a href='/' style='color:#007bff; text-decoration:none;'>← Back to form</a>
        </div>
        """

    return render_template("index.html")

# ==============================
# Start Flask
# ==============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
