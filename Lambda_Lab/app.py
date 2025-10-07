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
# Email configuration (Postfix local relay)
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
    """Generate a random password."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def is_wit_email(raw):
    """Validate that email ends with @wit.edu."""
    name, addr = parseaddr((raw or "").strip())
    if not addr or "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    return bool(local) and domain.casefold() == "wit.edu"

def sanitize_input(value):
    """Prevent dangerous characters from being injected."""
    return re.sub(r"[^a-zA-Z0-9@.\-_' ]", "", value)

def send_email_notification(fullname, email, username, password):
    """Send admin and student notifications."""

    # Email to admin
    admin_msg = Message(
        subject=f"[Lambda GPU Labs] New User Added: {username}",
        recipients=["louisw@wit.edu"],  # Change to your admin email
        body=f"""
A new user has been created on the Lambda GPU systems.

Full Name: {fullname}
Email: {email}
Username: {username}
Temporary Password: {password}
"""
    )
    mail.send(admin_msg)

    # Email to student
    student_msg = Message(
        subject="Your Lambda GPU Lab Account Details",
        recipients=[email],
        body=f"""
Hello {fullname},

Your Lambda GPU Lab account has been created successfully.

Username: {username}
Temporary Password: {password}

Please log in and change your password at first login.

Thanks,
WIT School of Computing and Data Science
"""
    )
    mail.send(student_msg)

# ==============================
# Routes
# ==============================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        fullname = sanitize_input(request.form.get("fullname"))
        email = sanitize_input(request.form.get("email"))

        # Validate WIT email domain
        if not is_wit_email(email):
            return "Only @wit.edu email addresses are allowed.", 403

        username = email.split("@")[0]
        password = generate_password()

        # Save to CSV for recordkeeping
        file_exists = os.path.isfile("users.csv")
        with open("users.csv", "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            if not file_exists:
                writer.writerow(["Full Name", "Email", "Username", "Password"])
            writer.writerow([fullname, email, username, password])

        # Run Ansible playbook with inline extra vars
        extra_vars = (
            f"username='{username}' "
            f"full_name='{fullname}' "
            f"email='{email}' "
            f"password='{password}'"
        )

        try:
            subprocess.run(
                [
                    "ansible-playbook",
                    "-i", "/etc/ansible/hosts",
                    "create_user.yml",
                    "--extra-vars", extra_vars
                ],
                check=True
            )
            print(f"[INFO] User {username} created via Ansible.")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Ansible failed: {e}")
            return f"<h3>Ansible failed:</h3><pre>{e}</pre>", 500

        # Send notification emails
        send_email_notification(fullname, email, username, password)

        # Redirect back to form with confirmation
        return redirect("/success")

    return render_template("index.html")

@app.route("/success")
def success():
    return """
    <h2 style='font-family:sans-serif; color:#155724; text-align:center; margin-top:50px;'>
        ✅ Account created successfully!<br>
        Please check your WIT email for login details.
    </h2>
    <div style='text-align:center; margin-top:20px;'>
        <a href='/' style='color:#007bff; text-decoration:none;'>← Back to form</a>
    </div>
    """

# ==============================
# Start Flask app
# ==============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
