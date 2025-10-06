from flask import Flask, render_template, request, redirect
from flask_mail import Mail, Message
import subprocess
import csv
import os

app = Flask(__name__)
app.secret_key = "supersecretkey"

# ==============================
# Outlook Email Configuration
# ==============================
app.config.update(
    MAIL_SERVER='smtp.office365.com',
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USERNAME='your_outlook_email@outlook.com',
    MAIL_PASSWORD='your_outlook_app_password',  # Use an Outlook app password
    MAIL_DEFAULT_SENDER='your_outlook_email@outlook.com'
)

mail = Mail(app)

# ==============================
# Helper: Send Email Notification
# ==============================
def send_email_notification(name, email, username):
    msg = Message(
        subject="New user added",
        recipients=["your_outlook_email@outlook.com"]
    )
    msg.body = (
        f"A new user has been added.\n\n"
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"Username: {username}\n"
    )
    try:
        mail.send(msg)
        print(f"[INFO] Notification email sent for {username}")
    except Exception as e:
        print(f"[ERROR] Failed to send email: {e}")

# ==============================
# Routes
# ==============================
# ==============================
# Routes
# ==============================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # Match the field names in your HTML form
        name = request.form["fullname"]
        email = request.form["email"]
        username = email.split("@")[0]

        # Save user info to CSV
        file_exists = os.path.isfile("users.csv")
        with open("users.csv", "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            if not file_exists:
                writer.writerow(["Name", "Email", "Username"])
            writer.writerow([name, email, username])

        # Run Ansible playbook to create the user
        try:
            subprocess.run([
                "ansible-playbook",
                "create_user.yml",
                "--extra-vars",
                f"username={username}"
            ], check=True)
            print(f"[INFO] User {username} created via Ansible.")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Ansible failed: {e}")

        # Send notification email
        send_email_notification(name, email, username)

        return redirect("/")

    return render_template("index.html")


if __name__ == "__main__":
    # Run the Flask web app
    app.run(host="0.0.0.0", port=5000)

