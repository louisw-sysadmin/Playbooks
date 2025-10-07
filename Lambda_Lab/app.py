from flask import Flask, render_template, request, redirect, abort
from flask_mail import Mail, Message
from email.utils import parseaddr
import subprocess, random, string, json

app = Flask(__name__)

# Flask-Mail Configuration (uses local Postfix SMTP)
app.config.update(
    MAIL_SERVER='localhost',
    MAIL_PORT=25,
    MAIL_USE_TLS=False,
    MAIL_USE_SSL=False,
    MAIL_DEFAULT_SENDER='noreply@lab.cs.wit.edu'
)

mail = Mail(app)

# --- Utility functions ---

def is_wit_email(raw):
    """Return True if address ends with @wit.edu"""
    name, addr = parseaddr((raw or "").strip())
    if not addr or "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    return bool(local) and domain.casefold() == "wit.edu"

def generate_password(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

# --- Routes ---

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        username = request.form['username']
        fullname = request.form['fullname']
        email = request.form['email']

        # Validate email
        if not is_wit_email(email):
            abort(403, description="Email must end with @wit.edu")

        password = generate_password()

        # Send vars securely via stdin instead of cmd args
        extra = {
            "username": username,
            "full_name": fullname,
            "email": email,
            "password": password
        }

        try:
            subprocess.run(
                ["ansible-playbook", "create_user.yml", "--extra-vars", "@-"],
                input=json.dumps(extra).encode(),
                check=True
            )

            # Send email to user
            msg = Message(
                subject="Your Lambda Lab Account",
                recipients=[email],
                body=f"Hello {fullname},\n\nYour new Lambda Lab account has been created.\n\nUsername: {username}\nPassword: {password}\n\nPlease change your password upon first login."
            )
            mail.send(msg)
            return redirect('/')
        except subprocess.CalledProcessError as e:
            return f"<h3>Ansible failed:</h3><pre>{e}</pre>"

    return render_template('index.html')

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
