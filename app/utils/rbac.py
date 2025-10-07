from functools import wraps
from flask import redirect, url_for, flash, request, current_app
from flask_login import current_user
from .security import log_event

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("Please log in.", "danger")
                return redirect(url_for("auth.login"))

            if current_user.role not in roles:
                flash("Access denied: insufficient permissions.", "danger")
                if current_app.config.get("LOG_UNAUTHORIZED", False):
                    log_event("unauth_access", email_or_username=current_user.email, path=request.path)
                return redirect(url_for("dashboard.index"))
            return f(*args, **kwargs)
        return wrapped
    return decorator
