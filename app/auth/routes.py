from flask import render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from . import auth_bp
from ..extensions import db, login_manager
from ..models import User
from ..utils.security import log_event, check_new_login_location
from ..utils.settings import get_setting
from datetime import datetime, timedelta
from flask import current_app

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@auth_bp.route("/setup", methods=["GET","POST"])
def setup():
    if User.query.first():
        flash("Setup already completed.", "info")
        return redirect(url_for("auth.login"))
    if request.method=="POST":
        email=request.form["email"]
        username=request.form["username"]
        raw_pw=request.form["password"]

        ok, msg = validate_password(raw_pw)
        if not ok:
            flash(msg, "danger")
            return render_template("auth/setup.html")

        password=generate_password_hash(raw_pw)
        user=User(email=email, username=username, password=password, role="SUPER_ADMIN", approved=True)
        db.session.add(user)
        db.session.commit()
        flash("Super Admin created","success")
        return redirect(url_for("auth.login"))
    return render_template("auth/setup.html")


@auth_bp.route("/register", methods=["GET","POST"])
def register():
    from ..models import User
    if not User.query.first():
        # No users, redirect to setup wizard
        return redirect(url_for("auth.setup"))
    if get_setting("ALLOW_REGISTRATION","True")!="True":
        flash("Self registration disabled.","danger")
        return redirect(url_for("auth.login"))
    if request.method=="POST":
        email=request.form["email"]
        username=request.form["username"]
        raw_pw=request.form["password"]

        ok, msg = validate_password(raw_pw)
        if not ok:
            flash(msg, "danger")
            return render_template("auth/register.html")

        password=generate_password_hash(raw_pw)
        user=User(email=email, username=username, password=password, role="AGENT", approved=False)
        db.session.add(user)
        db.session.commit()

        from ..notifications.utils import notify_roles
        notify_roles(("ADMIN","SUPER_ADMIN"),
            f"🆕 New user registered → {username} ({email})")

        flash("Registered. Await admin approval.","info")
        return redirect(url_for("auth.login"))
    return render_template("auth/register.html")

@auth_bp.route("/login", methods=["GET","POST"])
def login():
    from ..models import User
    if not User.query.first():
        # No users, redirect to setup wizard
        return redirect(url_for("auth.setup"))
    if request.method=="POST":
        identifier=request.form["identifier"]
        password=request.form["password"]
        user=User.query.filter((User.email==identifier)|(User.username==identifier)).first()
        if user:
            if user.lockout_until and user.lockout_until>datetime.utcnow():
                flash("Locked. Try later","danger")
                log_event("login_fail",email_or_username=identifier,user_id=user.id,path=request.path,flagged=True)
                return render_template("auth/login.html")
            if check_password_hash(user.password,password) and user.can_login():
                login_user(user)
                user.failed_logins=0
                user.lockout_until=None
                db.session.commit()
                ip=request.remote_addr or "unknown"
                ua=request.headers.get("User-Agent","?")
                check_new_login_location(user,ip,ua)
                return redirect(url_for("dashboard.index"))
            else:
                user.failed_logins+=1; db.session.add(user); db.session.commit()
                if user.failed_logins>=current_app.config["MAX_FAILED_LOGINS"]:
                    user.lockout_until=datetime.utcnow()+timedelta(minutes=current_app.config["LOCKOUT_MINUTES"])
                    db.session.commit()
                    from ..notifications.utils import notify_roles
                    notify_roles(("ADMIN", "SUPER_ADMIN"),
                        f"⚠️ User {user.username} has been locked due to failed logins.")
                    flash(f"Account locked {current_app.config['LOCKOUT_MINUTES']}m","danger")
                log_event("login_fail",email_or_username=identifier,user_id=user.id,path=request.path)
        else:
            log_event("unknown_user_login", email_or_username=identifier, path=request.path)
            from ..notifications.utils import notify_roles
            notify_roles(("ADMIN","SUPER_ADMIN"),
                f"⚠️ Repeated login attempt for unknown identifier {identifier}")
            flash("Invalid login","danger")
    return render_template("auth/login.html")

@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out","info")
    return redirect(url_for("auth.login"))


import re
from ..utils.settings import get_setting

def validate_password(pw: str):
    min_len = int(get_setting("PASSWORD_MIN_LENGTH","8"))
    require_upper = get_setting("PASSWORD_REQUIRE_UPPER","True")=="True"
    require_number = get_setting("PASSWORD_REQUIRE_NUMBER","True")=="True"
    require_symbol = get_setting("PASSWORD_REQUIRE_SYMBOL","True")=="True"

    if len(pw) < min_len:
        return False, f"Password must be at least {min_len} characters long."
    if require_upper and not re.search(r"[A-Z]", pw):
        return False, "Password must include at least one uppercase letter."
    if require_number and not re.search(r"\d", pw):
        return False, "Password must include at least one number."
    if require_symbol and not re.search(r"[^A-Za-z0-9]", pw):
        return False, "Password must include at least one symbol."

    return True, ""


