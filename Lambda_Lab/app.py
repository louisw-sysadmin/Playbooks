from flask import Flask, render_template, request, redirect, abort
from flask_mail import Mail, Message
from email.utils import parseaddr
import subprocess, random, string, json

app = Flask(__name__)

# -------------------------------------------------
# Flask-Mail configuration (using local Postfix)
# -------------------------------------------------
app.config.update(
    MAIL_SERVER='localhost',
    MAIL_PORT=25,
    MAIL_USE_TLS=False,
    MAIL_USE_SSL=False,
    MAIL_DEFAULT_SENDER='noreply@lab.cs.wit.edu'
)
mail = Mail(app)

# -------------------------------------------------
# Utility functions
# -------------------------------------------------
def is_wit_email(raw):
    """Validate that email ends with @wit.edu"""
    name, addr = parseaddr((raw or "").strip())
    if not addr or "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    return bool(local) and domain.casefold() == "wit.edu"

def generate_password(length=10):
    """Generate a random password"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

# -------------------------------------------------
# Routes
# -------------------------------------------------
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        fullname = request.form.get('fullname')
        email = request.form.get('email')

        # Validate input
        if not fullname or not email:
            abort(400, description="Missing required fields.")
        if not is_wit_email(email):
            abort(403, description="Email must end with @wit.edu")

        # Auto-generate username and password
        username = email.split('@')[0]
        password = generate_password()

        # Build vars for Ansible
        extra = {
            "username": username,
            "full_name": fullname,
            "email": email,
            "password": password
        }

        try:
            # Run Ansible playbook using /etc/ansible/hosts inventory
            subprocess.run(
                ["ansible-playbook", "create_user.yml", "--extra-vars=@-"],
                input=json.dumps(extra).encode(),
                check=True
            )

            # Send email confirmation
            msg = Message(
                subject="Your Lambda Lab Account",
                recipients=[email],
                body=f"""Hello {fullname},

Your new Lambda Lab account has been created.

Username: {username}
Password: {password}

Please change your password upon first login.

-- 
WIT School of Computing and Data Science
Lambda GPU Labs"""
            )
            mail.send(msg)
            return redirect('/')
        except subprocess.CalledProcessError as e:
            return f"<h3>Ansible failed:</h3><pre>{e}</pre>"

    return render_template('index.html')

# -------------------------------------------------
# Entry point
# -------------------------------------------------
if __name__ == "__main__":
    # Host 0.0.0.0 lets it listen on all interfaces
    app.run(host='0.0.0.0', port=5000)
