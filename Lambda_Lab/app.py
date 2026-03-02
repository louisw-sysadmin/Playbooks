import os
import re
import secrets
import string
import subprocess
import logging
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

# ====== CONFIG ======
ANSIBLE_PLAYBOOK = "/home/sysadmin/Playbooks/Lambda_Lab/create_user_account.yml"
ANSIBLE_INVENTORY = "/home/sysadmin/Playbooks/Lambda_Lab/inventory"
LOG_DIR = "/home/sysadmin/Playbooks/Lambda_Lab/logs"

SENDMAIL_PATH = "/usr/sbin/sendmail"
FROM_EMAIL = os.getenv("LAMBDA_FROM_EMAIL", "Lambda GPU Labs <no-reply@cs.wit.edu>")
ADMIN_COPY_EMAIL = os.getenv("LAMBDA_ADMIN_EMAIL", "")  # optional Bcc

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)


# ====== HELPERS ======

def generate_username(full_name: str) -> str:
    clean = re.sub(r"[^a-zA-Z ]", "", full_name).strip().lower()
    parts = clean.split()
    if len(parts) < 2:
        raise ValueError("Full name must include first and last name")
    return (parts[0][0] + parts[-1])[:16]


def generate_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def run_ansible(username: str, password: str, logfile: str):
    cmd = [
        "ansible-playbook",
        "-i", ANSIBLE_INVENTORY,
        ANSIBLE_PLAYBOOK,
        "-e", f"username={username}",
        "-e", f"user_password={password}",
    ]

    with open(logfile, "w") as lf:
        proc = subprocess.run(cmd, stdout=lf, stderr=lf)

    return proc.returncode


def send_credentials_email(to_email: str, full_name: str, username: str, password: str):
    if not os.path.exists(SENDMAIL_PATH):
        raise RuntimeError("sendmail not found")

    subject = "Lambda GPU Labs account credentials"

    body = (
        f"Hi {full_name},\n\n"
        "Your Lambda GPU Labs account has been created.\n\n"
        f"Username: {username}\n"
        f"Temporary password: {password}\n\n"
        "You will be required to change your password on first login.\n\n"
        "--\n"
        "Lambda GPU Labs\n"
    )

    headers = [
        f"From: {FROM_EMAIL}",
        f"To: {to_email}",
        f"Subject: {subject}",
    ]

    if ADMIN_COPY_EMAIL:
        headers.append(f"Bcc: {ADMIN_COPY_EMAIL}")

    headers.append("")  # separates headers from body

    msg = "\n".join(headers) + "\n" + body + "\n"

    proc = subprocess.run(
        [SENDMAIL_PATH, "-t", "-oi"],
        input=msg,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())


# ====== ROUTES ======

@app.route("/health")
def health():
    return "ok", 200


@app.route("/api/create", methods=["POST"])
def api_create():
    full_name = request.form.get("fullname", "").strip()
    email = request.form.get("email", "").strip().lower()

    if not full_name or not email:
        return jsonify({"error": "fullname and email are required"}), 400

    try:
        username = generate_username(full_name)
        password = generate_password()
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logfile = os.path.join(LOG_DIR, f"{username}_{timestamp}.log")

    rc = run_ansible(username, password, logfile)

    if rc != 0:
        return jsonify({
            "error": "Account creation failed",
            "logfile": logfile
        }), 500

    # Try sending email (do not fail account if email fails)
    try:
        send_credentials_email(email, full_name, username, password)
    except Exception as e:
        logging.exception("Email send failed: %s", e)
        return jsonify({
            "status": "ok",
            "username": username,
            "logfile": logfile,
            "email_warning": "Account created but email failed"
        }), 200

    return jsonify({
        "status": "ok",
        "username": username,
        "logfile": logfile
    }), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)