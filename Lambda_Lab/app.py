import os
import re
import secrets
import string
import subprocess
import logging
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ====== PATHS / SETTINGS ======
INVENTORY_PATH = "/etc/ansible/hosts"
PLAYBOOK_PATH  = "/home/sysadmin/Playbooks/playbooks/users/create_user_account.yml"
LIMIT_GROUP    = "lambda"

LOG_DIR        = "/var/log/lambda_ansible_runs"

# Email via local postfix
SENDMAIL_PATH  = "/usr/sbin/sendmail"
FROM_EMAIL     = os.getenv("LAMBDA_FROM_EMAIL", "Lambda GPU Labs <no-reply@cs.wit.edu>")
ADMIN_COPY_EMAIL = os.getenv("LAMBDA_ADMIN_EMAIL", "")  # optional Bcc


def generate_username(full_name: str) -> str:
    clean = re.sub(r"[^a-zA-Z ]", "", full_name).strip().lower()
    parts = clean.split()
    if len(parts) < 2:
        raise ValueError("Full name must include first and last name")
    return (parts[0][0] + parts[-1])[:16]


def generate_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def ensure_log_dir() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)


def run_ansible(username: str, full_name: str, email: str, password: str, logfile: str) -> int:
    cmd = [
        "ansible-playbook",
        "-i", INVENTORY_PATH,
        PLAYBOOK_PATH,
        "-l", LIMIT_GROUP,
        "-e", "username={0}".format(username),
        "-e", "full_name={0}".format(full_name),
        "-e", "email={0}".format(email),
        "-e", "password={0}".format(password),
    ]

    with open(logfile, "w") as lf:
        lf.write("COMMAND:\n{0}\n\n".format(" ".join(cmd)))
        lf.flush()
        proc = subprocess.run(cmd, stdout=lf, stderr=lf)

    return proc.returncode


def send_credentials_email(to_email: str, full_name: str, username: str, password: str) -> None:
    if not os.path.exists(SENDMAIL_PATH):
        raise RuntimeError("sendmail not found at {0}".format(SENDMAIL_PATH))

    subject = "Lambda GPU Labs account credentials"
    body = (
        "Hi {0},\n\n"
        "Your Lambda GPU Labs account has been created.\n\n"
        "Username: {1}\n"
        "Temporary password: {2}\n\n"
        "You will be required to change your password on first login.\n\n"
        "--\n"
        "Lambda GPU Labs\n"
    ).format(full_name, username, password)

    headers = [
        "From: {0}".format(FROM_EMAIL),
        "To: {0}".format(to_email),
        "Subject: {0}".format(subject),
    ]
    if ADMIN_COPY_EMAIL:
        headers.append("Bcc: {0}".format(ADMIN_COPY_EMAIL))
    headers.append("")

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
        raise RuntimeError("sendmail failed: {0}".format(proc.stderr.strip()))


@app.route("/health")
def health():
    return "ok", 200


@app.route("/api/create", methods=["POST"])
def api_create():
    full_name = request.form.get("fullname", "").strip()
    email = request.form.get("email", "").strip().lower()

    if not full_name or not email:
        return jsonify({"error": "fullname and email are required"}), 400

    # If your playbook enforces @wit.edu, enforce it here too so users get a clean message
    if not email.endswith("@wit.edu"):
        return jsonify({"error": "Email must be @wit.edu"}), 400

    try:
        username = generate_username(full_name)
        password = generate_password()
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    try:
        ensure_log_dir()
    except Exception as e:
        return jsonify({"error": "Cannot create log dir: {0}".format(e)}), 500

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logfile = os.path.join(LOG_DIR, "{0}_{1}.log".format(username, ts))

    rc = run_ansible(username, full_name, email, password, logfile)
    if rc != 0:
        return jsonify({"error": "Account creation failed", "logfile": logfile}), 500

    # Send email (don’t fail account creation if email fails)
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

    return jsonify({"status": "ok", "username": username, "logfile": logfile}), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)