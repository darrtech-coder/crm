from flask import render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_user, logout_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from . import auth_bp
from ..extensions import db, login_manager
from ..models import User
from ..utils.security import log_event, check_new_login_location
from ..utils.settings import get_setting
from datetime import datetime, timedelta
from sqlalchemy import inspect
import re, cryptography
import os
from sqlalchemy import inspect, create_engine
from sqlalchemy.sql import text
from sqlalchemy.orm import sessionmaker


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# -----------------------------
# Helper to ensure DB exists
# -----------------------------
def ensure_db_ready():
    """Check if DB and tables exist; redirect if missing."""
    inspector = inspect(db.engine)
    try:
        if 'user' not in inspector.get_table_names():
            # Database exists but no user table — setup needed
            return False
    except Exception:
        # Database file itself might not exist
        return False
    return True


@auth_bp.route("/restart_required")
def restart_required():
    """A page to instruct the user to restart the server."""
    return render_template("auth/restart_required.html")


# -----------------------------
# Setup Route
# -----------------------------
@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    # --- Step 0: Check if setup is already complete ---
    try:
        if os.path.exists('.env'):
            # A full check requires a DB query. If it succeeds and finds a user, we're done.
            if User.query.first():
                flash("Setup has already been completed.", "info")
                return redirect(url_for("auth.login"))
    except Exception:
        # This can happen if .env exists but the DB is not reachable.
        # We'll allow the user to proceed to re-configure.
        pass

    # --- Final Submission: Handle the POST from Step 3 ---
    if request.method == "POST" and 'final_setup' in request.form:
        try:
            # --- 1. Gather all configuration data ---
            db_uri = request.form.get("db_uri")
            
            env_content = f'DATABASE_URL="{db_uri}"\n'
            env_content += f'FS_BACKEND="{request.form.get("fs_backend")}"\n'
            env_content += f'STORAGE_PREFIX="{request.form.get("storage_prefix")}"\n'
            env_content += f'S3_KEY="{request.form.get("s3_key")}"\n'
            env_content += f'S3_SECRET="{request.form.get("s3_secret")}"\n'
            env_content += f'S3_BUCKET="{request.form.get("s3_bucket")}"\n'
            env_content += f'S3_REGION="{request.form.get("s3_region")}"\n'
            env_content += f'S3_ENDPOINT_URL="{request.form.get("s3_endpoint_url")}"\n'
            env_content += f'S3_CDN_URL="{request.form.get("s3_cdn_url")}"\n'
            env_content += f'GCS_BUCKET="{request.form.get("gcs_bucket")}"\n'
            env_content += f'GCS_CDN_URL="{request.form.get("gcs_cdn_url")}"\n'

            # --- 2. Handle GCS Keyfile Upload ---
            gcs_keyfile = request.files.get("gcs_keyfile_upload")
            if gcs_keyfile and gcs_keyfile.filename:
                instance_path = current_app.instance_path
                os.makedirs(instance_path, exist_ok=True)
                filepath = os.path.join(instance_path, "gcs_credentials.json")
                gcs_keyfile.save(filepath)
                env_content += f'GCS_KEYFILE_PATH="{filepath}"\n'

            # --- 3. Write the .env file ---
            with open(".env", "w") as f:
                f.write(env_content)

            # --- 4. Create Tables and Super Admin User ---
            temp_engine = create_engine(db_uri)
            db.metadata.create_all(bind=temp_engine)
            Session = sessionmaker(bind=temp_engine)
            temp_session = Session()

            if temp_session.query(User).first():
                temp_session.close()
                flash("Database already contains users. Setup aborted.", "warning")
                return redirect(url_for('auth.login'))

            email = request.form.get("email")
            username = request.form.get("username")
            raw_pw = request.form.get("password")
            
            user = User(
                email=email, username=username,
                password=generate_password_hash(raw_pw),
                role="SUPER_ADMIN", approved=True
            )
            temp_session.add(user)
            temp_session.commit()
            temp_session.close()

        except Exception as e:
            flash(f"An error occurred during final setup: {e}", "danger")
            if os.path.exists(".env"): os.remove(".env")
            return render_template("auth/setup.html")

        return redirect(url_for("auth.restart_required"))

    # --- GET Request: Show the initial setup form ---
    return render_template("auth/setup.html")




@auth_bp.route("/test_db_connection", methods=["POST"])
def test_db_connection():
    data = request.get_json()
    uri = data.get("db_uri")
    if not uri:
        return jsonify({"ok": False, "error": "No database URI provided."}), 400
    
    try:
        # [FIX] Only add connect_timeout for non-SQLite databases
        engine_args = {}
        if not uri.startswith('sqlite'):
            engine_args['connect_args'] = {'connect_timeout': 5}

        engine = create_engine(uri, **engine_args)
        connection = engine.connect()
        connection.close()
        return jsonify({"ok": True})
    except Exception as e:
        error_message = str(e).split('\n')[0]
        return jsonify({"ok": False, "error": error_message}), 400

# -----------------------------
# Register Route
# -----------------------------
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    # Check if DB is ready
    if not ensure_db_ready():
        return redirect(url_for("auth.setup"))

    if not User.query.first():
        return redirect(url_for("auth.setup"))

    if get_setting("ALLOW_REGISTRATION", "True") != "True":
        flash("Self registration disabled.", "danger")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        email = request.form["email"]
        username = request.form["username"]
        raw_pw = request.form["password"]

        ok, msg = validate_password(raw_pw)
        if not ok:
            flash(msg, "danger")
            return render_template("auth/register.html")

        password = generate_password_hash(raw_pw)
        user = User(
            email=email,
            username=username,
            password=password,
            role="AGENT",
            approved=False
        )
        db.session.add(user)
        db.session.commit()

        from ..notifications.utils import notify_roles
        notify_roles(
            ("ADMIN", "SUPER_ADMIN"),
            f"🆕 New user registered → {username} ({email})"
        )

        flash("Registered. Await admin approval.", "info")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")

# -----------------------------
# Login Route
# -----------------------------
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if not os.path.exists('.env'):
        return redirect(url_for('auth.setup'))
        
    if not ensure_db_ready():
        flash("Database tables not found. Please complete the setup or restart the server.", "warning")
        return redirect(url_for("auth.setup"))

    if not User.query.first():
        return redirect(url_for("auth.setup"))

    if request.method == "POST":
        identifier = request.form["identifier"]
        password = request.form["password"]
        user = User.query.filter(
            (User.email == identifier) | (User.username == identifier)
        ).first()

        if user:
            if user.lockout_until and user.lockout_until > datetime.utcnow():
                flash("Account locked due to too many failed login attempts. Please try again later.", "danger")
                log_event("login_fail", email_or_username=identifier, user_id=user.id, flagged=True)
                return render_template("auth/login.html")

            if check_password_hash(user.password, password) and user.can_login():
                login_user(user)
                user.failed_logins = 0
                user.lockout_until = None
                db.session.commit()
                ip = request.remote_addr or "unknown"
                ua = request.headers.get("User-Agent", "?")
                check_new_login_location(user, ip, ua)
                return redirect(url_for("dashboard.index"))
            else:
                user.failed_logins += 1
                if user.failed_logins >= current_app.config["MAX_FAILED_LOGINS"]:
                    user.lockout_until = datetime.utcnow() + timedelta(minutes=current_app.config["LOCKOUT_MINUTES"])
                    from ..notifications.utils import notify_roles
                    notify_roles(("ADMIN", "SUPER_ADMIN"), f"⚠️ User {user.username} locked due to failed logins.")
                    flash(f"Account locked for {current_app.config['LOCKOUT_MINUTES']} minutes.", "danger")
                db.session.commit()
                log_event("login_fail", email_or_username=identifier, user_id=user.id)
                flash("Invalid username or password.", "danger")
        else:
            log_event("unknown_user_login", email_or_username=identifier)
            flash("Invalid username or password.", "danger")
    
    return render_template("auth/login.html")

# -----------------------------
# Logout Route
# -----------------------------
@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out", "info")
    return redirect(url_for("auth.login"))

# -----------------------------
# Password Validator
# -----------------------------
def validate_password(pw: str):
    min_len = int(get_setting("PASSWORD_MIN_LENGTH", "8"))
    require_upper = get_setting("PASSWORD_REQUIRE_UPPER", "True") == "True"
    require_number = get_setting("PASSWORD_REQUIRE_NUMBER", "True") == "True"
    require_symbol = get_setting("PASSWORD_REQUIRE_SYMBOL", "True") == "True"

    if len(pw) < min_len:
        return False, f"Password must be at least {min_len} characters long."
    if require_upper and not re.search(r"[A-Z]", pw):
        return False, "Password must include at least one uppercase letter."
    if require_number and not re.search(r"\d", pw):
        return False, "Password must include at least one number."
    if require_symbol and not re.search(r"[^A-Za-z0-9]", pw):
        return False, "Password must include at least one symbol."

    return True, ""

