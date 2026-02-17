#!/usr/bin/env python3
import os
import re
import json
import time
import secrets
import string
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from flask import Flask, request, render_template_string

app = Flask(__name__)

# -----------------------
# CONFIG (edit as needed)
# -----------------------
INVENTORY_PATH = "/etc/ansible/hosts"
PLAYBOOK_PATH  = "/home/sysadmin/Playbooks/playbooks/users/create_user_account.yml"

# FORCE target group here (this is the whole point)
TARGET_GROUP   = "lambda"

ANSIBLE_RUN_DIR = "/var/log/lambda_ansible_runs"

# If ansible/ssh ever hangs, keep it bounded.
PRECHECK_TIMEOUT_SEC = 20
PLAYBOOK_TIMEOUT_SEC = 600

# Make ssh non-interactive + fast fail
SSH_COMMON_ARGS = "-o BatchMode=yes -o ConnectTimeout=4 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# Optional: make usernames from email local-part (lambdaonly01 from lambdaonly01@wit.edu)
USERNAME_REGEX = re.compile(r"[^a-z0-9]+")  # keep lowercase letters + digits only


# -----------------------
# HTML (simple)
# -----------------------
FORM_HTML = """
<!doctype html>
<title>Lambda GPU Lab - Account Request</title>
<div style="max-width:520px;margin:40px auto;font-family:sans-serif;">
  <h2>Lambda GPU Lab Account Request</h2>
  <form method="POST">
    <label>Full name</label><br>
    <input name="fullname" required style="width:100%;padding:10px;margin:6px 0 14px;"><br>

    <label>Email</label><br>
    <input name="email" type="email" required style="width:100%;padding:10px;margin:6px 0 14px;"><br>

    <button type="submit" style="padding:10px 14px;">Create Account</button>
  </form>
</div>
"""

OK_HTML = """
<h2 style='font-family:sans-serif; color:#155724; text-align:center; margin-top:50px;'>
  ✅ Account creation initiated for {{ full_name }}<br>
  <small>{{ summary }}</small><br><br>
  Please check your WIT email for credentials.
</h2>
<div style='text-align:center; margin-top:20px;'>
  <a href='/' style='color:#007bff; text-decoration:none;'>← Back to form</a>
</div>
"""

WARN_HTML = """
<h2 style='font-family:sans-serif; color:#856404; text-align:center; margin-top:50px;'>
  ⚠️ {{ title }}<br>
  <small>{{ details }}</small><br><br>
  No changes were made.
</h2>
<div style='text-align:center; margin-top:20px;'>
  <a href='/' style='color:#007bff; text-decoration:none;'>← Back to form</a>
</div>
"""


# -----------------------
# Logging
# -----------------------
def ensure_log_dir():
    Path(ANSIBLE_RUN_DIR).mkdir(parents=True, exist_ok=True)

def logfile_for(username: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(ANSIBLE_RUN_DIR, f"{username}_{ts}.log")

def setup_logger():
    ensure_log_dir()
    log_path = os.path.join(ANSIBLE_RUN_DIR, "lambda_app.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(),
        ],
    )

setup_logger()


# -----------------------
# Helpers
# -----------------------
def make_username(email: str) -> str:
    local = email.split("@")[0].lower().strip()
    local = USERNAME_REGEX.sub("", local)
    # keep it sane
    return local[:16] if local else "user"

def gen_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

def run_cmd(cmd, timeout, log_file=None, env=None):
    """
    Run a command, capture output, optionally write to log file.
    """
    logging.info("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        env=env,
    )
    if log_file:
        with open(log_file, "a") as f:
            f.write("\n\n# CMD:\n")
            f.write(" ".join(cmd) + "\n")
            f.write("# OUTPUT:\n")
            f.write(result.stdout + "\n")
            f.write(f"# EXIT CODE: {result.returncode}\n")
    return result

def check_username_exists_on_lambda(username: str):
    """
    Check if username exists ONLY on lambda group.
    Returns: (exists_hosts_list, unreachable_hosts_list)
    """
    # Use ansible ad-hoc against *only* lambda
    cmd = [
        "ansible",
        TARGET_GROUP,
        "-i", INVENTORY_PATH,
        "--limit", TARGET_GROUP,
        "--ssh-common-args", SSH_COMMON_ARGS,
        "-m", "command",
        "-a", f"getent passwd {username}",
        "-o",
    ]

    env = os.environ.copy()
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"

    res = run_cmd(cmd, timeout=PRECHECK_TIMEOUT_SEC, env=env)

    exists_on = []
    unreachable = []

    # Parse simple "-o" output lines
    # Examples:
    # lambda1 | SUCCESS | rc=0 >> ...
    # lambda2 | FAILED | rc=2 >> ...
    # lambda3 | UNREACHABLE! => ...
    for line in res.stdout.splitlines():
        if " | SUCCESS |" in line:
            host = line.split(" | ", 1)[0].strip()
            exists_on.append(host)
        elif "UNREACHABLE!" in line:
            host = line.split(" ", 1)[0].strip()
            unreachable.append(host)

    return exists_on, unreachable

def run_create_user_playbook(extra_vars: dict, log_file: str):
    """
    Run the real playbook, hard-limited to lambda.
    """
    cmd = [
        "ansible-playbook",
        "-i", INVENTORY_PATH,
        PLAYBOOK_PATH,
        "--limit", TARGET_GROUP,                 # <-- HARD LIMIT
        "--ssh-common-args", SSH_COMMON_ARGS,
        "--extra-vars", json.dumps(extra_vars),  # <-- REAL JSON
    ]

    env = os.environ.copy()
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"

    return run_cmd(cmd, timeout=PLAYBOOK_TIMEOUT_SEC, log_file=log_file, env=env)


# -----------------------
# Routes
# -----------------------
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template_string(FORM_HTML)

    full_name = (request.form.get("fullname") or "").strip()
    email     = (request.form.get("email") or "").strip().lower()

    if not full_name or not email or "@" not in email:
        return render_template_string(WARN_HTML, title="Invalid input", details="Please provide a valid name + email."), 400

    username = make_username(email)
    logging.info("Pre-check: verifying username availability for '%s' (lambda only)", username)

    exists_on, unreachable = check_username_exists_on_lambda(username)
    if exists_on:
        details = f"Username '{username}' already exists on (lambda only): " + ", ".join(sorted(exists_on))
        logging.warning(details)
        return render_template_string(WARN_HTML, title="Username already exists", details=details), 409

    # Create user
    password = gen_password()
    extra_vars = {
        "username": username,
        "full_name": full_name,
        "email": email,
        "password": password,
    }

    log_file = logfile_for(username)
    logging.info("Starting account creation for %s (%s) as '%s' (lambda only)", full_name, email, username)

    try:
        res = run_create_user_playbook(extra_vars, log_file=log_file)
    except subprocess.TimeoutExpired:
        logging.error("Playbook timed out for user %s", username)
        return render_template_string(WARN_HTML, title="Timed out", details="Account creation took too long. Check logs."), 500

    if res.returncode == 0:
        summary = "✅ SUCCESS: Lambda hosts completed successfully."
        logging.info("Ansible success summary=%s", summary)
        return render_template_string(OK_HTML, full_name=full_name, summary=summary), 200

    # Non-zero means failures; keep response simple but useful
    summary = f"⚠️ FAILED: Some lambda hosts failed. See {log_file}"
    logging.warning("Ansible exit code=%s summary=%s", res.returncode, summary)
    return render_template_string(OK_HTML, full_name=full_name, summary=summary), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
