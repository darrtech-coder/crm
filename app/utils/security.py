from flask import request, current_app
from ..extensions import db
from ..security.models import AccessLog
from datetime import datetime, timedelta

def log_event(event_type, email_or_username=None, user_id=None, path=None, flagged=False):
    ip = request.remote_addr or "unknown"
    agent = request.headers.get("User-Agent","?")
    log = AccessLog(event_type=event_type,
                    email_or_username=email_or_username,
                    user_id=user_id,
                    ip=ip,
                    user_agent=agent,
                    path=path,
                    flagged=flagged)
    db.session.add(log)
    db.session.commit()

def check_new_login_location(user, ip, ua):
    if user.last_login_ip and user.last_login_ip != ip:
        log_event("login_success", email_or_username=user.email, user_id=user.id, path="/login", flagged=True)
        current_app.logger.warning(f"⚠️ New IP login for {user.email}: {ip}")

        # 🔔 Notify admins + super admins about new IP login
        from ..notifications.utils import notify_roles
        notify_roles(("ADMIN","SUPER_ADMIN"),
            f"🌐 New login location detected for {user.username} – IP {ip}")

    elif user.last_login_ua and user.last_login_ua != ua:
        log_event("login_success", email_or_username=user.email, user_id=user.id, path="/login", flagged=True)
        current_app.logger.warning(f"⚠️ New device login for {user.email}: {ua}")

        # 🔔 Notify admins + super admins about new device login
        from ..notifications.utils import notify_roles
        notify_roles(("ADMIN","SUPER_ADMIN"),
            f"🖥 New device login detected for {user.username} – UA {ua}")

    else:
        log_event("login_success", email_or_username=user.email, user_id=user.id, path="/login")

    user.last_login_ip = ip
    user.last_login_ua = ua
    db.session.commit()
