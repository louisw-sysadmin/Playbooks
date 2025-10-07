from flask import Flask, render_template, request, redirect
from flask_mail import Mail, Message
import subprocess, random, string, os # string and random are no longer used for password, but kept for simplicity of edit

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
        username = request.form['username']
        fullname = request.form['fullname']
        email = request.form['email']
        
        # --- NEW: Email Validation ---
        if not email.lower().endswith('@wit.edu'):
            return "<h3>Error: Only '@wit.edu' emails are allowed.</h3><p><a href='/'>Go Back</a></p>"

        # --- MODIFIED: Password generation is now handled by Ansible, so no need to generate it here.
        
        # --- MODIFIED: The Ansible command now only passes username, fullname, and email ---
        cmd = [
            "ansible-playbook", "create_user.yml",
            "--extra-vars", f"username={username} full_name='{fullname}' email={email}"
        ]

        try:
            # Run the playbook
            process = subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            # --- NEW: Extract the generated password from Ansible's output ---
            # We assume the playbook will print the generated password in a specific, easily parsable format.
            # E.g., Ansible output: "PASS_GEN:my_new_password123"
            password_line = next((line for line in process.stdout.splitlines() if line.startswith('PASS_GEN:')), None)
            
            if not password_line:
                 raise Exception("Ansible playbook did not return the generated password.")

            generated_password = password_line.split(':')[1].strip()

            msg = Message(
                subject="Your Lambda Lab Account",
                recipients=[email],
                body=f"Hello {fullname},\n\nYour new Lambda Lab account has been created.\n\nUsername: {username}\nPassword: {generated_password}\n\nPlease change your password upon first login."
            )
            mail.send(msg)
            
            return redirect('/')
        except subprocess.CalledProcessError as e:
            # Include Ansible's error output for better debugging
            return f"<h3>Ansible failed:</h3><pre>{e}\n\n{e.stderr}</pre>"
        except Exception as e:
             return f"<h3>Application Error:</h3><pre>{e}</pre>"
    return render_template('index.html')

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)