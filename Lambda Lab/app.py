from flask import Flask, render_template, request, redirect, flash
import subprocess, smtplib
from email.mime.text import MIMEText

app = Flask(__name__)
app.secret_key = "supersecret"

ADMIN_EMAIL = "louisw@email.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_PASSWORD = "YOUR_APP_PASSWORD"

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        full_name = request.form["fullname"].strip()
        email = request.form["email"].strip()

        # Require WIT email
        if not email.endswith("@wit.edu"):
            flash("Please use your WIT email address (@wit.edu).", "error")
            return redirect("/")

        username = email.split("@")[0]

        # Run Ansible playbook
        subprocess.run([
            "ansible-playbook", "create_user.yml",
            "--extra-vars", f"username={username} email={email} full_name='{full_name}'"
        ])

        # Send admin notification
        send_admin_email(username, full_name, email)
        flash(f"Account request for {full_name} ({username}) has been submitted.", "success")
        return redirect("/")
    return render_template("index.html")

def send_admin_email(username, full_name, email):
    msg = MIMEText(f"New GPU Labs user added:\n\nFull Name: {full_name}\nUsername: {username}\nEmail: {email}")
    msg["Subject"] = "New Lambda GPU Labs User Created"
    msg["From"] = ADMIN_EMAIL
    msg["To"] = ADMIN_EMAIL

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(ADMIN_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

