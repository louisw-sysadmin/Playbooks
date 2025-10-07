from flask import Flask, render_template, request, redirect
from flask_mail import Mail, Message
import subprocess
import csv
import os
import random
import string

app = Flask(__name__)

# ==============================
# Email configuration (Postfix local relay)
# ==============================
app.config["MAIL_SERVER"] = "localhost"
app.config["MAIL_PORT"] = 25
app.config["MAIL_USE_TLS"] = False
app.config["MAIL_USE_SSL"] = False
app.config["MAIL_USERNAME"] = None
app.config["MAIL_PASSWORD"] = None
app.config["MAIL_DEFAULT_SENDER"] = "noreply@master.lambda.local"

mail = Mail(app)

# ==============================
# Helper: generate a random default password
# ==============================
def generate_password(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

# ==============================
# Helper: send notification emails
# ==============================
def send_email_notification(fullname, email, username, password):
    admin_msg = Message(
        subject=f"[Lambda GPU Labs] New User Added: {username}",
        recipients=["louisw@wit.edu"],  # your admin email
        body=f"""
A new user has been created on the Linux systems.

Full Name: {fullname}
Email: {email}
Username: {username}
Default Password: {password}
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

# ==============================
# Routes
# ==============================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        fullname = request.form["fullname"]
        email = request.form["email"]
        username = email.split("@")[0]
        password = generate_password()

        # Save user info to CSV
        file_exists = os.path.isfile("users.csv")
        with open("users.csv", "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            if not file_exists:
                writer.writerow(["Full Name", "Email", "Username", "Password"])
            writer.writerow([fullname, email, username, password])

        # Run Ansible playbook to create the user
        try:
            subprocess.run([
                "ansible-playbook",
                "create_user.yml",
                "--extra-vars",
                f"username={username} full_name='{fullname}' email={email} password={password}"
            ], check=True)
            print(f"[INFO] User {username} created via Ansible.")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Ansible failed: {e}")

        # Send notification emails
        send_email_notification(fullname, email, username, password)

        return redirect("/")

    return render_template("index.html")

# ==============================
# Start Flask app
# ==============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
