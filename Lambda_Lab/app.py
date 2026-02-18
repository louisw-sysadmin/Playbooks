# /home/sysadmin/Playbooks/Lambda_Lab/app.py
# API-only Flask app for Lambda GPU Labs account creation
# - Static HTML stays in Nginx (/var/www/lambda_lab)
# - Flask exposes /health + /api/create
# - Ansible is HARD-LIMITED to the "lambda" group so it never touches sc/debug/reinhart

from flask import Flask, request, jsonify
import os
import subprocess
import logging
from datetime import datetime
import secrets
import re

app = Flask(__name__)

# ====== CONFIG ======
INVENTORY_PATH = "/etc/ansible/hosts"
PLAYBOOK_PATH = "/home/sysadmin/Playbooks/playbooks/users/create_user_account.yml"
LIMIT_GROUP = "lambda"

ANSIBLE_RUN_DIR = "/var/log/lambda_ansible_runs"
os.makedirs(ANSIBLE_RUN_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ====== HELPERS ======
def email_to_username(email: str) -> str:
    # take left side of email and normalize
    user = email.split("@", 1)[0].strip().lower()
    # allow only safe linux username chars: a-z, 0-9, underscore, dash
    user = re.sub(r"[^a-z0-9_-]", "", user)
    # linux username max length is typically 32; keep it safe
    return user[:32]


def run_ansible_create(username: str, full_name: str, email: str, password: str) -> tuple[int, str, str]:
    """
    Runs the user creation playbook, limited to the lambda group only.
    Returns: (returncode, logfile_path, cmd_string)
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logfile = os.path.join(ANSIBLE_RUN_DIR, f"{username}_{ts}.log")

    extra_vars = {
        "username": username,
        "full_name": full_name,
        "email": email,
        "password": password,
    }

    cmd = [
        "ansible-playbook",
        "-i", INVENTORY_PATH,
        PLAYBOOK_PATH,
        "--limit", LIMIT_GROUP,          # ✅ hard safety gate: ONLY lambda hosts
        "--extra-vars", str(extra_vars).replace("'", '"'),
    ]

    cmd_str = " ".join(cmd)
    logging.info("Running: %s", cmd_str)

    with open(logfile, "w", encoding="utf-8") as f:
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, text=True)

    return proc.returncode, logfile, cmd_str


# ====== ROUTES ======
@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.post("/api/create")
def api_create():
    full_name = (request.form.get("fullname") or "").strip()
    email = (request.form.get("email") or "").strip().lower()

    if not full_name or not email:
        return jsonify({"status": "error", "message": "fullname and email are required"}), 400

    if not email.endswith("@wit.edu"):
        return jsonify({"status": "error", "message": "WIT email required (@wit.edu)"}), 400

    username = email_to_username(email)
    if not username:
        return jsonify({"status": "error", "message": "Could not derive a valid username from email"}), 400

    # Generate a temp password (user will be forced to change on first login by your playbook)
    password = secrets.token_urlsafe(12)

    rc, logfile, cmd_str = run_ansible_create(username, full_name, email, password)

    if rc != 0:
        return jsonify({
            "status": "error",
            "message": "ansible-playbook failed",
            "returncode": rc,
            "logfile": logfile,
            "cmd": cmd_str
        }), 500

    # If you already email credentials elsewhere, keep doing it there.
    # This API just returns success + logfile location.
    return jsonify({
        "status": "ok",
        "username": username,
        "logfile": logfile
    }), 200


if __name__ == "__main__":
    # for local debugging only; systemd runs gunicorn
    app.run(host="127.0.0.1", port=5000, debug=False)
