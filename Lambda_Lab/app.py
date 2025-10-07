from flask import Flask, render_template, request, redirect
from flask_mail import Mail, Message
import subprocess, os

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

# The original password generation function is REMOVED.

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        fullname = request.form['fullname']
        email = request.form['email']
        
        # --- NEW: Derive username from email (part before @) ---
        try:
            # Splits the email string at the '@' and takes the first element (the username part)
            username = email.split('@')[0].strip()
        except IndexError:
             # This handles cases where the email might not contain an @
             return "<h3>Error: Invalid email format.</h3><p><a href='/'>Go Back</a></p>"

        # --- NEW: Email Validation for @wit.edu ---
        if not email.lower().endswith('@wit.edu'):
            return "<h3>Error: Only '@wit.edu' emails are allowed.</h3><p><a href='/'>Go Back</a></p>"

        # The Ansible command now only passes username (derived), fullname, and email
        cmd = [
            "ansible-playbook", "create_user.yml",
            "--extra-vars", f"username={username} full_name='{fullname}' email={email}"
        ]

        try:
            # Run the playbook and capture output to get the generated password
            process = subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            # Extract the generated password from Ansible's output using the 'PASS_GEN:' tag
            password_line = next((line for line in process.stdout.splitlines() if line.startswith('PASS_GEN:')), None)
            
            if not password_line:
                 # If Ansible ran but didn't return the password as expected, raise an error
                 raise Exception("Ansible playbook did not return the generated password. Check playbook output.")

            # Isolate the plain-text password
            generated_password = password_line.split(':', 1)[1].strip()

            # Send the email with credentials
            msg = Message(
                subject="Your Lambda Lab Account",
                recipients=[email],
                body=f"Hello {fullname},\n\nYour new Lambda Lab account has been created.\n\nUsername: {username}\nPassword: {generated_password}\n\nPlease change your password upon first login."
            )
            mail.send(msg)
            
            return redirect('/')
        
        except subprocess.CalledProcessError as e:
            # Handle Ansible failure and include error output for debugging
            return f"<h3>Ansible failed:</h3><pre>{e}\n\n{e.stderr}</pre>"
        except Exception as e:
             # Handle other application errors
             return f"<h3>Application Error:</h3><pre>{e}</pre>"
             
    return render_template('index.html')

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)