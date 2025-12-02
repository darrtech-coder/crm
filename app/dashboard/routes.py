from flask import render_template, redirect, url_for, jsonify, request, flash, current_app
from flask_login import login_required, current_user
from . import dashboard_bp    # use the blueprint from __init__.py
from ..models import User
from ..extensions import db
from werkzeug.security import generate_password_hash
from sqlalchemy import func
from ..security.models import AccessLog
from ..utils.settings import get_setting, set_setting
from ..teams.models import Team
from ..library.models import LibraryItem, LibraryView, LibraryRating, QuizAttempt
import secrets
from datetime import datetime, timedelta
from ..messaging.models import ChatRoom, ChatParticipant
from ..messaging.routes import ensure_manager_group, ensure_manager_admin_group, ensure_admin_group, add_user_to_room_if_not
from app.utils.datetime_tools import convert_for_render

from ..utils.storage import storage
from fs.copy import copy_fs


from ..activity.models import LibrarySession
from ..system_monitor.models import SystemMetric



from zoneinfo import ZoneInfo
try:
    from zoneinfo import available_timezones
    TZ_LIST = sorted(available_timezones())
except Exception:
    # Fallback list if Python <3.11 or missing tz index
    TZ_LIST = [
        "UTC","Europe/London","Europe/Berlin","Europe/Paris",
        "America/New_York","America/Chicago","America/Denver","America/Los_Angeles",
        "America/Sao_Paulo","Africa/Johannesburg",
        "Asia/Dubai","Asia/Kolkata","Asia/Singapore","Asia/Tokyo","Australia/Sydney"
    ]


@dashboard_bp.route("/")
@login_required
def index():
    if current_user.role=="SUPER_ADMIN":
        return redirect(url_for("dashboard.super_admin"))
    elif current_user.role=="ADMIN":
        return redirect(url_for("dashboard.admin"))
    elif current_user.role=="MANAGER":
        return redirect(url_for("dashboard.manager"))
    return redirect(url_for("dashboard.agent"))

@dashboard_bp.route("/super_admin")
@login_required
def super_admin():
    if current_user.role != "SUPER_ADMIN":
        return redirect(url_for("dashboard.index"))

    # Storage summary
    from ..utils.settings import get_setting

    total_bytes = int(get_setting("STORAGE_TOTAL_BYTES", "0") or 0)
    limit_bytes = int(get_setting("STORAGE_LIMIT_BYTES", "0") or 0)

    if limit_bytes > 0:
        percent = int(round((total_bytes / limit_bytes) * 100))
    else:
        percent = None

    return render_template(
        "dashboard/super_admin.html",
        user=current_user,
        storage_total_bytes=total_bytes,
        storage_limit_bytes=limit_bytes,
        storage_percent=percent,
    )

@dashboard_bp.route("/admin")
@login_required
def admin():
    if current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        return redirect(url_for("dashboard.index"))

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    pagination = User.query.order_by(User.id.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Storage summary (percentage only for Admins)
    from ..utils.settings import get_setting

    total_bytes = int(get_setting("STORAGE_TOTAL_BYTES", "0") or 0)
    limit_bytes = int(get_setting("STORAGE_LIMIT_BYTES", "0") or 0)

    if limit_bytes > 0:
        storage_percent = int(round((total_bytes / limit_bytes) * 100))
        has_storage_limit = True
    else:
        storage_percent = None
        has_storage_limit = False

    return render_template(
        "dashboard/admin.html",
        user=current_user,
        users=pagination.items,
        pagination=pagination,
        per_page=per_page,
        storage_percent=storage_percent,
        has_storage_limit=has_storage_limit,
    )
 

@dashboard_bp.route("/users")
@login_required
def users():
    if current_user.role not in ("MANAGER","ADMIN","SUPER_ADMIN"):
        return redirect(url_for("dashboard.index"))
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)
    pagination = User.query.order_by(User.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return render_template("dashboard/users.html",
                           user=current_user,
                           users=pagination.items,
                           pagination=pagination,
                           per_page=per_page)





@dashboard_bp.route("/manager")
@login_required
def manager():
    if current_user.role!="MANAGER": return redirect(url_for("dashboard.index"))
    return render_template("dashboard/manager.html", user=current_user)

@dashboard_bp.route("/agent")
@login_required
def agent():
    if current_user.role!="AGENT": return redirect(url_for("dashboard.index"))
    return render_template("dashboard/agent.html", user=current_user)

# User mgmt via AJAX
@dashboard_bp.route("/admin/users/<int:user_id>/approve", methods=["POST"])
@login_required
def approve_user(user_id):
    if current_user.role not in ("ADMIN","SUPER_ADMIN"): return jsonify({"ok":False}),403
    u=User.query.get_or_404(user_id)
    if u.role=="SUPER_ADMIN": return jsonify({"ok":False}),400
    u.approved=True; db.session.commit()
    return jsonify({"ok":True,"approved":True})

@dashboard_bp.route("/admin/users/<int:user_id>/disable", methods=["POST"])
@login_required
def disable_user(user_id):
    if current_user.role not in ("ADMIN","SUPER_ADMIN"): return jsonify({"ok":False}),403
    u=User.query.get_or_404(user_id)
    if u.role=="SUPER_ADMIN": return jsonify({"ok":False}),400
    u.disabled=not u.disabled; db.session.commit()
    return jsonify({"ok":True,"disabled":u.disabled})

@dashboard_bp.route("/admin/users/<int:user_id>/reset_pw", methods=["POST"])
@login_required
def reset_pw(user_id):
    if current_user.role not in ("ADMIN","SUPER_ADMIN"): return jsonify({"ok":False}),403
    u=User.query.get_or_404(user_id)
    if u.role=="SUPER_ADMIN": return jsonify({"ok":False}),400
    new_pw=secrets.token_hex(4); u.password=generate_password_hash(new_pw)
    db.session.commit()
    return jsonify({"ok":True,"new_pw":new_pw})

@dashboard_bp.route("/admin/users/<int:user_id>/unlock", methods=["POST"])
@login_required
def unlock_user(user_id):
    if current_user.role not in ("MANAGER","ADMIN","SUPER_ADMIN"): return jsonify({"ok":False}),403
    u=User.query.get_or_404(user_id); u.unlock(); db.session.commit()
    return jsonify({"ok":True,"unlocked":True})

# Logs
@dashboard_bp.route("/super_admin/logs")
@login_required
def super_admin_logs():
    if current_user.role != "SUPER_ADMIN":
        return redirect(url_for("dashboard.index"))

    # Read ?limit= from query string; default to 100 if missing/invalid
    limit = request.args.get("limit", 100, type=int)
    if not limit or limit <= 0:
        limit = 100

    logs = (AccessLog.query
            .order_by(AccessLog.timestamp.desc())
            .limit(limit)
            .all())

    # Convert all datetimes to the user’s tz, format them (kept as-is from your code)
    # logs = convert_for_render(logs, fmt="%Y-%m-%d %H:%M")

    return render_template("dashboard/logs.html", logs=logs, limit=limit)

# System Settings
@dashboard_bp.route("/super_admin/settings", methods=["GET","POST"])
@login_required
def super_admin_settings():
    if current_user.role != "SUPER_ADMIN":
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        # Security & User Settings
        set_setting("LOG_UNAUTHORIZED", "True" if request.form.get("log_unauth") else "False")
        set_setting("MAX_FAILED_LOGINS", request.form.get("max_failed", "5"))
        set_setting("LOCKOUT_MINUTES", request.form.get("lockout", "15"))
        set_setting("ALLOW_REGISTRATION", "True" if request.form.get("allow_reg") else "False")
        set_setting("PASSWORD_MIN_LENGTH", request.form.get("pw_length", "8"))
        set_setting("PASSWORD_REQUIRE_UPPER", "True" if "pw_upper" in request.form else "False")
        set_setting("PASSWORD_REQUIRE_NUMBER", "True" if "pw_number" in request.form else "False")
        set_setting("PASSWORD_REQUIRE_SYMBOL", "True" if "pw_symbol" in request.form else "False")

        # Infrastructure
        set_setting("TIMEZONE", request.form.get("timezone", "UTC").strip() or "UTC")
        set_setting("REDIS_MODE", request.form.get("redis_mode", "local"))
        set_setting("REDIS_URL", request.form.get("redis_url", "").strip())
        
        # Storage Settings
        set_setting("FS_BACKEND", request.form.get("fs_backend", "local"))
        set_setting("S3_KEY", request.form.get("s3_key", "").strip())
        set_setting("S3_SECRET", request.form.get("s3_secret", "").strip())
        set_setting("S3_BUCKET", request.form.get("s3_bucket", "").strip())
        set_setting("S3_REGION", request.form.get("s3_region", "").strip())
        set_setting("S3_ENDPOINT_URL", request.form.get("s3_endpoint_url", "").strip())
        set_setting("S3_CDN_URL", request.form.get("s3_cdn_url", "").strip())
        set_setting("GCS_BUCKET", request.form.get("gcs_bucket", "").strip())
        set_setting("GCS_CDN_URL", request.form.get("gcs_cdn_url", "").strip())

        # [NEW] Save storage prefix
        set_setting("STORAGE_PREFIX", request.form.get("storage_prefix", "crm").strip('/'))

        # Resource Monitor Settings
        set_setting("RESOURCE_SAMPLING_SECONDS", request.form.get("resource_interval", "60") or "60")
        set_setting("CPU_ALERT_PERCENT", request.form.get("cpu_alert_percent", "90") or "90")
        set_setting("MEM_ALERT_PERCENT", request.form.get("mem_alert_percent", "90") or "90")
        set_setting("DISK_ALERT_PERCENT", request.form.get("disk_alert_percent", "90") or "90")

        limit_gb_str = request.form.get("storage_limit_gb", "").strip()
        try:
            limit_gb = float(limit_gb_str) if limit_gb_str else 0
            limit_bytes = int(limit_gb * 1024**3)
        except ValueError:
            limit_bytes = 0

        set_setting("STORAGE_LIMIT_BYTES", limit_bytes)


        # NEW: optional override of current total storage usage
        override_gb_str = request.form.get("storage_total_gb", "").strip()
        if override_gb_str:
            try:
                override_gb = float(override_gb_str)
                override_bytes = int(override_gb * 1024**3)
                set_setting("STORAGE_TOTAL_BYTES", override_bytes)
                # Reset alert level so new thresholds are recalculated cleanly
                set_setting("STORAGE_LIMIT_ALERT_LEVEL", "none")
            except ValueError:
                flash("Invalid value for storage usage override; ignoring.", "warning")


        
        gcs_keyfile = request.files.get("gcs_keyfile_upload")
        if gcs_keyfile and gcs_keyfile.filename:
            instance_path = current_app.instance_path
            os.makedirs(instance_path, exist_ok=True)
            filename = secure_filename("gcs_credentials.json")
            filepath = os.path.join(instance_path, filename)
            gcs_keyfile.save(filepath)
            set_setting("GCS_KEYFILE_PATH", filepath)

        flash("Settings updated. A server restart may be required for some changes to take effect.", "success")
        return redirect(url_for("dashboard.super_admin_settings"))

    vals = {
        "log_unauth": get_setting("LOG_UNAUTHORIZED", "True"),
        "max_failed": get_setting("MAX_FAILED_LOGINS", "5"),
        "lockout": get_setting("LOCKOUT_MINUTES", "15"),
        "allow_reg": get_setting("ALLOW_REGISTRATION", "True"),
        "pw_length": int(get_setting("PASSWORD_MIN_LENGTH", "8")),
        "pw_upper": get_setting("PASSWORD_REQUIRE_UPPER", "True") == "True",
        "pw_number": get_setting("PASSWORD_REQUIRE_NUMBER", "True") == "True",
        "pw_symbol": get_setting("PASSWORD_REQUIRE_SYMBOL", "True") == "True",
        "timezone": get_setting("TIMEZONE", "UTC"),
        "redis_mode": get_setting("REDIS_MODE", "local"),
        "redis_url": get_setting("REDIS_URL", ""),
        "fs_backend": get_setting("FS_BACKEND", "local"),
        "s3_key": get_setting("S3_KEY", ""),
        "s3_secret": get_setting("S3_SECRET", ""),
        "s3_bucket": get_setting("S3_BUCKET", ""),
        "s3_region": get_setting("S3_REGION", ""),
        "s3_endpoint_url": get_setting("S3_ENDPOINT_URL", ""),
        "storage_prefix": get_setting("STORAGE_PREFIX", "crm"),
        "s3_cdn_url": get_setting("S3_CDN_URL", ""),
        "gcs_bucket": get_setting("GCS_BUCKET", ""),
        "gcs_cdn_url": get_setting("GCS_CDN_URL", ""),
        "gcs_keyfile_path": get_setting("GCS_KEYFILE_PATH", ""),
        "resource_interval": int(get_setting("RESOURCE_SAMPLING_SECONDS", "60")),
        "cpu_alert_percent": int(get_setting("CPU_ALERT_PERCENT", "90")),
        "mem_alert_percent": int(get_setting("MEM_ALERT_PERCENT", "90")),
        "disk_alert_percent": int(get_setting("DISK_ALERT_PERCENT", "90")),
        "storage_limit_bytes": int(get_setting("STORAGE_LIMIT_BYTES", "0") or 0),
        "storage_total_bytes": int(get_setting("STORAGE_TOTAL_BYTES", "0") or 0),
    }
    # [THIS IS THE FIX] Pass tz_list to the template
    return render_template("dashboard/settings.html", settings=vals, tz_list=TZ_LIST)

@dashboard_bp.route("/super_admin/import_csv", methods=["POST"])
@login_required
def import_csv():
    if current_user.role != "SUPER_ADMIN":
        return redirect(url_for("dashboard.index"))

    import csv, io
    from ..teams.models import Team, TeamMember
    from ..models import User
    from werkzeug.security import generate_password_hash

    file = request.files["csvfile"]
    if not file:
        flash("No file", "danger")
        return redirect(url_for("dashboard.users"))

    stream = io.StringIO(file.stream.read().decode("utf-8"))
    reader = csv.DictReader(stream)
    updated, added = 0, 0

    for row in reader:
        email = row.get("email")
        username = row.get("username")
        password = row.get("password")
        role = (row.get("role") or "AGENT").upper()
        theme = row.get("theme")  # custom column, e.g. "dark"/"light"
        team_name = row.get("team")

        if not email or not username:
            continue

        user = User.query.filter_by(email=email).first()
        if user:
            # --- UPDATE ---
            user.username = username
            user.role = role
            if password:
                user.password = generate_password_hash(password)
            if theme:
                user.theme = theme   # ➡ requires adding a `theme` field in User model
            updated += 1
        else:
            # --- CREATE ---
            user = User(
                email=email,
                username=username,
                password=generate_password_hash(password) if password else "",
                role=role,
                approved=True
            )
            db.session.add(user)
            db.session.flush()
            added += 1

        # --- Ensure team exists & membership updated ---
        if team_name:
            team = Team.query.filter_by(name=team_name).first()
            if not team:
                team = Team(name=team_name)
                db.session.add(team)
                db.session.flush()

            tm = TeamMember.query.filter_by(user_id=user.id, team_id=team.id).first()
            if not tm:
                tm = TeamMember(user_id=user.id, team_id=team.id, role="MANAGER" if role=="MANAGER" else "AGENT")
                db.session.add(tm)
            else:
                tm.role = "MANAGER" if role=="MANAGER" else "AGENT"

    db.session.commit()
    flash(f"Imported CSV: {added} new users, {updated} updated", "success")
    return redirect(url_for("dashboard.users"))


@dashboard_bp.route("/admin/teams/<int:team_id>/set_storage", methods=["POST"])
@login_required
def set_storage(team_id):
    if current_user.role not in ("ADMIN","SUPER_ADMIN"): return redirect(url_for("dashboard.index"))
    team=Team.query.get_or_404(team_id); team.storage_limit_mb=int(request.form["storage_limit"]); db.session.commit()
    flash("Storage updated","success")
    return redirect(url_for("teams.index"))

# Library analytics
@dashboard_bp.route("/super_admin/library_analytics")
@login_required
def library_analytics():
    if current_user.role not in ("SUPER_ADMIN","ADMIN","MANAGER"):
        return redirect(url_for("dashboard.index"))
    from ..library.models import LibraryItem, LibraryView, LibraryRating, QuizAttempt

    top_views = (
        db.session.query(LibraryItem.id, LibraryItem.title, func.count(LibraryView.id))
        .join(LibraryView, LibraryView.item_id == LibraryItem.id)
        .group_by(LibraryItem.id).order_by(func.count(LibraryView.id).desc()).limit(10).all()
    )

    avg_ratings = (
        db.session.query(LibraryItem.id, LibraryItem.title,
                         func.avg(LibraryRating.easy),
                         func.avg(LibraryRating.complete),
                         func.avg(LibraryRating.overall))
        .outerjoin(LibraryRating, LibraryRating.item_id == LibraryItem.id)
        .group_by(LibraryItem.id).all()
    )

    quiz_avgs = (
        db.session.query(LibraryItem.id, LibraryItem.title, func.avg(QuizAttempt.score))
        .outerjoin(QuizAttempt, QuizAttempt.item_id == LibraryItem.id)
        .group_by(LibraryItem.id).all()
    )

    lowest_rated = (
        db.session.query(LibraryItem.id, LibraryItem.title, func.avg(LibraryRating.overall).label("avg"))
        .join(LibraryRating).group_by(LibraryItem.id)
        .having(func.count(LibraryRating.id) >= 3)
        .order_by(func.avg(LibraryRating.overall).asc()).limit(10).all()
    )

    no_views = (
        db.session.query(LibraryItem.id, LibraryItem.title)
        .outerjoin(LibraryView, LibraryView.item_id == LibraryItem.id)
        .group_by(LibraryItem.id)
        .having(func.count(LibraryView.id) == 0).all()
    )

    # after existing queries:
    time_spent = (
        db.session.query(LibraryItem.id, LibraryItem.title, func.sum(LibrarySession.duration).label("secs"))
        .join(LibrarySession, LibrarySession.item_id == LibraryItem.id)
        .group_by(LibraryItem.id)
        .order_by(func.sum(LibrarySession.duration).desc())
        .limit(10).all()
    )

    return render_template("dashboard/library_analytics.html",
                           views=top_views, ratings=avg_ratings, quizzes=quiz_avgs,
                           lowest_rated=lowest_rated, no_views=no_views, time_spent=time_spent)


@dashboard_bp.route("/admin/users/<int:user_id>/set_role", methods=["POST"])
@login_required
def set_role(user_id):
    if current_user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 403

    u = User.query.get_or_404(user_id)
    if u.role == "SUPER_ADMIN":
        return jsonify({"ok": False, "error": "Cannot change SUPER_ADMIN"}), 400

    new_role = request.form.get("role")
    if new_role not in ("SUPER_ADMIN", "ADMIN", "MANAGER", "AGENT"):
        return jsonify({"ok": False, "error": "Invalid role"}), 400

    u.role = new_role
    db.session.commit()
    add_to_role_rooms(u)
    return jsonify({"ok": True, "role": new_role})

@dashboard_bp.route("/manager/users")
@login_required
def manager_users():
    if current_user.role != "MANAGER":
        return redirect(url_for("dashboard.index"))

    from ..teams.models import TeamMember
    team_ids = [tm.team_id for tm in TeamMember.query.filter_by(user_id=current_user.id)]
    from ..models import User
    # Only see agents in their teams
    users = User.query.join(TeamMember, TeamMember.user_id == User.id).filter(TeamMember.team_id.in_(team_ids)).all()

    return render_template("dashboard/manager_users.html", users=users)

@dashboard_bp.route("/manager/users/<int:user_id>/set_role", methods=["POST"])
@login_required
def manager_set_role(user_id):
    if current_user.role != "MANAGER":
        return jsonify({"ok": False, "error": "Unauthorized"}), 403

    from ..teams.models import TeamMember
    from ..models import User

    u = User.query.get_or_404(user_id)

    # Check team membership
    my_team_ids = [tm.team_id for tm in TeamMember.query.filter_by(user_id=current_user.id)]
    member = TeamMember.query.filter(TeamMember.user_id == u.id,
                                     TeamMember.team_id.in_(my_team_ids)).first()
    if not member:
        return jsonify({"ok": False, "error": "Not in your team"}), 403

    # Only AGENT <-> MANAGER allowed
    new_role = request.form.get("role")
    if u.role == "AGENT" and new_role == "MANAGER":
        u.role = "MANAGER"
    elif u.role == "MANAGER" and new_role == "AGENT":
        u.role = "AGENT"
    else:
        return jsonify({"ok": False, "error": "Invalid promotion/demotion"}), 400

    db.session.commit()
    add_to_role_rooms(u)
    return jsonify({"ok": True, "role": u.role})



@dashboard_bp.route("/unread_notifications")
@login_required
def unread_notifications():
    from ..notifications.models import Notification

    # Auto-delete read notifications older than 30 days for this user
    purge_old_read_notifications(current_user.id)

    unread = (Notification.query
              .filter_by(user_id=current_user.id, seen=False)
              .order_by(Notification.created_at.desc())
              .all())

    read = (Notification.query
            .filter_by(user_id=current_user.id, seen=True)
            .order_by(Notification.created_at.desc())
            .all())

    # Template expects 'unread' and 'read'
    return render_template("dashboard/unread_notifications.html", unread=unread, read=read)

def add_to_role_rooms(user):
    if user.role == "MANAGER":
        add_user_to_room_if_not(user.id, ensure_manager_group())
        add_user_to_room_if_not(user.id, ensure_manager_admin_group())
    if user.role == "ADMIN":
        add_user_to_room_if_not(user.id, ensure_admin_group())
        add_user_to_room_if_not(user.id, ensure_manager_admin_group())
    if user.role == "SUPER_ADMIN":
        # you might want super admins in all admin groups too
        add_user_to_room_if_not(user.id, ensure_admin_group())
        add_user_to_room_if_not(user.id, ensure_manager_admin_group())





@dashboard_bp.route("/admin/users/add", methods=["POST"])
@login_required
def add_user_modal():
    if current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        flash("Unauthorized", "danger")
        return redirect(url_for("dashboard.users"))
    from ..models import User
    from werkzeug.security import generate_password_hash

    email = request.form["email"].strip()
    username = request.form["username"].strip()
    raw_pw = request.form["password"]
    role = request.form.get("role", "AGENT").upper()

    # Same password‑validation logic used in auth
    from ..auth.routes import validate_password
    ok, msg = validate_password(raw_pw)
    if not ok:
        flash(msg, "danger")
        return redirect(url_for("dashboard.users"))

    user = User(email=email, username=username,
                password=generate_password_hash(raw_pw),
                role=role, approved=True)
    db.session.add(user)
    db.session.commit()
    flash(f"User {username} created successfully.", "success")
    return redirect(url_for("dashboard.users"))



def purge_old_read_notifications(user_id=None):
    # Lazy-import to avoid circulars
    from ..notifications.models import Notification
    cutoff = datetime.utcnow() - timedelta(days=30)

    q = Notification.query.filter(Notification.seen == True)
    if user_id:
        q = q.filter(Notification.user_id == user_id)

    # Prefer read_at/seen_at if available; fall back to created_at
    if hasattr(Notification, "read_at"):
        q = q.filter(Notification.read_at < cutoff)
    elif hasattr(Notification, "seen_at"):
        q = q.filter(Notification.seen_at < cutoff)
    elif hasattr(Notification, "updated_at"):
        q = q.filter(Notification.updated_at < cutoff)
    else:
        q = q.filter(Notification.created_at < cutoff)

    deleted = q.delete(synchronize_session=False)
    if deleted:
        db.session.commit()
    return deleted


@dashboard_bp.route("/notifications/<int:note_id>/delete", methods=["POST"])
@login_required
def delete_notification(note_id):
    from ..notifications.models import Notification
    n = Notification.query.get_or_404(note_id)

    if n.user_id != current_user.id:
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    if not getattr(n, "seen", False):
        # Only allow deletion of read notifications
        return jsonify({"ok": False, "error": "Only read notifications can be deleted"}), 400

    db.session.delete(n)
    db.session.commit()
    return jsonify({"ok": True, "deleted": True, "id": note_id})

@dashboard_bp.route("/notifications/delete_read", methods=["POST"])
@login_required
def delete_all_read_notifications():
    from ..notifications.models import Notification
    deleted = (Notification.query
               .filter_by(user_id=current_user.id, seen=True)
               .delete(synchronize_session=False))
    db.session.commit()
    return jsonify({"ok": True, "deleted": deleted})



@dashboard_bp.route("/logs/<int:log_id>")
@login_required
def log_detail(log_id):
    if current_user.role != "SUPER_ADMIN":
        return redirect(url_for("dashboard.index"))
    from ..security.models import AccessLog
    log = AccessLog.query.get_or_404(log_id)
    return render_template("dashboard/log_detail.html", log=log)


@dashboard_bp.route("/admin/users/<int:user_id>/set_name", methods=["POST"])
@login_required
def set_name(user_id):
    if current_user.role not in ("ADMIN","SUPER_ADMIN"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    u = User.query.get_or_404(user_id)
    if u.role == "SUPER_ADMIN":
        return jsonify({"ok": False, "error": "Cannot edit SUPER_ADMIN"}), 400
    new_name = request.form.get("name","").strip()
    u.name = new_name
    db.session.commit()
    return jsonify({"ok": True, "name": new_name})



@dashboard_bp.route("/tests_analytics")
@login_required
def tests_analytics():
    if current_user.role not in ("MANAGER","ADMIN","SUPER_ADMIN"):
        return redirect(url_for("dashboard.index"))
    from ..tests.models import Test, TestSubmission
    rows = (db.session.query(Test.id, Test.title, func.count(TestSubmission.id).label("attempts"), func.avg(TestSubmission.score).label("avg"))
            .outerjoin(TestSubmission, TestSubmission.test_id==Test.id)
            .group_by(Test.id).order_by(Test.title.asc()).all())
    # pass rate >= 60% (computed here)
    stats = []
    for tid, title, attempts, avg in rows:
        subs = TestSubmission.query.filter_by(test_id=tid).all()
        passed = 0
        for s in subs:
            total = len(s.test.questions)*5 or 1
            if (s.score or 0)/total*100 >= 60:
                passed += 1
        pass_rate = (passed/len(subs)*100) if subs else 0
        stats.append({"id": tid, "title": title, "attempts": attempts or 0, "avg": round(avg or 0,1), "pass_rate": round(pass_rate,1)})
    return render_template("dashboard/tests_analytics.html", stats=stats)

@dashboard_bp.route("/courses_analytics")
@login_required
def courses_analytics():
    if current_user.role not in ("MANAGER","ADMIN","SUPER_ADMIN"):
        return redirect(url_for("dashboard.index"))
    from ..academy.models import AcademyCourse, AcademyCourseItem, AcademyModuleStatus
    # total items per course
    totals = dict((cid, cnt) for cid, cnt in
                  db.session.query(AcademyCourseItem.course_id, func.count(AcademyCourseItem.id))
                  .group_by(AcademyCourseItem.course_id).all())
    # completed per course (for all users)
    dones = dict((cid, cnt) for cid, cnt in
                 db.session.query(AcademyCourseItem.course_id, func.count(AcademyModuleStatus.id))
                 .join(AcademyCourseItem, AcademyCourseItem.id==AcademyModuleStatus.course_item_id)
                 .group_by(AcademyCourseItem.course_id).all())
    courses = AcademyCourse.query.order_by(AcademyCourse.title.asc()).all()
    stats = []
    for c in courses:
        total = int(totals.get(c.id, 0) or 0)
        done = int(dones.get(c.id, 0) or 0)
        percent = round((done/(total or 1))*100, 1) if total else 0.0
        stats.append({"id": c.id, "title": c.title, "modules": total, "completions": done, "completion_rate": percent})
    return render_template("dashboard/courses_analytics.html", stats=stats)




@dashboard_bp.route("/super_admin/test_storage", methods=["POST"])
@login_required
def test_storage_connection():
    if current_user.role != "SUPER_ADMIN":
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    
    config = request.get_json()
    try:
        # Create a temporary filesystem object to test
        fs_instance = storage.get_fs_from_config(config)
        
        # [FIX] A more robust test: write and delete a temporary file.
        # This also ensures the prefix directory gets created if it doesn't exist.
        test_file = "connection_test.tmp"
        fs_instance.writetext(test_file, "success")
        if not fs_instance.exists(test_file):
            raise ConnectionError("File was not written successfully.")
        fs_instance.remove(test_file)
        
        return jsonify({"ok": True, "message": "Connection successful!"})
    except Exception as e:
        # Attempt to clean up in case the remove failed
        try:
            if 'fs_instance' in locals() and fs_instance.exists(test_file):
                fs_instance.remove(test_file)
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)}), 400

@dashboard_bp.route("/super_admin/storage_migration", methods=["POST"])
@login_required
def storage_migration():
    if current_user.role != "SUPER_ADMIN":
        return jsonify({"ok": False, "error": "Unauthorized"}), 403

    # The currently active filesystem is the source
    source_fs = storage.fs 
    
    # The new configuration is sent in the POST request body
    new_config = request.get_json()
    if not new_config:
        return jsonify({"ok": False, "error": "Missing new storage configuration."}), 400

    try:
        # Create the destination filesystem from the submitted config
        dest_fs = storage.get_fs_from_config(new_config)
        
        # Get a list of all files to migrate
        all_files = list(source_fs.walk.files())
        total_files = len(all_files)

        # Perform the copy
        copy_fs(source_fs, dest_fs, workers=4)

        # Verification step (optional but recommended)
        # Check if a few files from the source now exist in the destination
        verified_count = 0
        for f_path in all_files[:5]: # Check first 5 files
            if dest_fs.exists(f_path):
                verified_count += 1
        
        if total_files > 0 and verified_count == 0:
             raise RuntimeError("Post-migration verification failed. Files did not appear in the destination.")

        return jsonify({"ok": True, "message": f"Successfully migrated {total_files} files. You can now save your new settings."})
    except Exception as e:
        current_app.logger.error(f"Storage migration failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@dashboard_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
def delete_user(user_id):
    if current_user.role != "SUPER_ADMIN":
        return jsonify({"ok": False, "error": "Unauthorized"}), 403

    u = User.query.get_or_404(user_id)

    # Never allow deleting SUPER_ADMIN
    if u.role == "SUPER_ADMIN":
        return jsonify({"ok": False, "error": "Cannot delete SUPER_ADMIN"}), 400

    # Require that the user is already disabled
    if not u.disabled:
        return jsonify({"ok": False, "error": "User must be disabled before deletion."}), 400

    try:
        # Clean up some simple related data to reduce FK issues.
        # We *do not* try to delete content they created – if they have a lot of
        # dependent data, the delete may still fail, and we surface a clear error.

        from ..teams.models import TeamMember
        from ..notifications.models import Notification
        from ..security.models import UserSession
        from ..messaging.models import Friendship, ChatParticipant

        TeamMember.query.filter_by(user_id=u.id).delete(synchronize_session=False)
        Notification.query.filter_by(user_id=u.id).delete(synchronize_session=False)
        UserSession.query.filter_by(user_id=u.id).delete(synchronize_session=False)
        Friendship.query.filter(
            (Friendship.user_id == u.id) | (Friendship.friend_id == u.id)
        ).delete(synchronize_session=False)
        ChatParticipant.query.filter_by(user_id=u.id).delete(synchronize_session=False)

        db.session.delete(u)
        db.session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        current_app.logger.error(f"Failed to delete user {u.id}: {e}")
        db.session.rollback()
        # If there are still FK constraints (e.g. created tests, library items), 
        # we prevent deletion and ask the admin to keep the user disabled.
        return jsonify({
            "ok": False,
            "error": "Delete failed due to related data. Keep user disabled instead."
        }), 500


@dashboard_bp.route("/super_admin/system_metrics")
@login_required
def system_metrics():
    if current_user.role != "SUPER_ADMIN":
        return redirect(url_for("dashboard.index"))

    from sqlalchemy import func
    from app.utils.datetime_tools import convert_for_render

    now = datetime.utcnow()

    # --- Last 24h for line charts ---
    since_24h = now - timedelta(hours=24)
    recent = (
        SystemMetric.query
        .filter(SystemMetric.ts >= since_24h)
        .order_by(SystemMetric.ts.asc())
        .all()
    )

    cpu = [m.cpu_percent for m in recent]
    mem = [m.mem_percent for m in recent]
    disk = [m.disk_percent for m in recent]
    storage_gb = [
        (m.storage_total_bytes or 0) / (1024**3) for m in recent
    ]

    # Time labels in active timezone (per user if set)
    if recent:
        time_labels = convert_for_render(
            [m.ts for m in recent],
            fmt="%H:%M"
        )
    else:
        time_labels = []

    # --- Daily aggregates for last 7 days ---
    seven_days_ago = now - timedelta(days=7)
    dialect = db.engine.dialect.name

    if dialect == "sqlite":
        day_expr = func.date(SystemMetric.ts)  # 'YYYY-MM-DD' string
    elif dialect == "postgresql":
        day_expr = func.date_trunc("day", SystemMetric.ts)
    else:
        day_expr = func.date(SystemMetric.ts)

    daily_rows = (
        db.session.query(
            day_expr.label("day"),
            func.avg(SystemMetric.cpu_percent).label("cpu_avg"),
            func.max(SystemMetric.cpu_percent).label("cpu_max"),
            func.avg(SystemMetric.mem_percent).label("mem_avg"),
            func.max(SystemMetric.mem_percent).label("mem_max"),
            func.avg(SystemMetric.disk_percent).label("disk_avg"),
            func.max(SystemMetric.disk_percent).label("disk_max"),
            func.avg(SystemMetric.storage_total_bytes).label("storage_avg"),
            func.max(SystemMetric.storage_total_bytes).label("storage_max"),
        )
        .filter(SystemMetric.ts >= seven_days_ago)
        .group_by(day_expr)
        .order_by(day_expr.asc())
        .all()
    )

    daily_cpu_avg = [float(row.cpu_avg or 0) for row in daily_rows]
    daily_cpu_max = [float(row.cpu_max or 0) for row in daily_rows]
    daily_mem_avg = [float(row.mem_avg or 0) for row in daily_rows]
    daily_mem_max = [float(row.mem_max or 0) for row in daily_rows]
    daily_disk_avg = [float(row.disk_avg or 0) for row in daily_rows]
    daily_disk_max = [float(row.disk_max or 0) for row in daily_rows]

    daily_storage_avg_gb = [
        float((row.storage_avg or 0) / (1024**3)) for row in daily_rows
    ]
    daily_storage_max_gb = [
        float((row.storage_max or 0) / (1024**3)) for row in daily_rows
    ]

    # Daily labels (dates) in active timezone
    if daily_rows:
        daily_labels = convert_for_render(
            [row.day for row in daily_rows],
            fmt="%Y-%m-%d"
        )
    else:
        daily_labels = []

    return render_template(
        "dashboard/system_metrics.html",
        recent=recent,
        daily_rows=daily_rows,
        cpu=cpu,
        mem=mem,
        disk=disk,
        storage_gb=storage_gb,
        daily_cpu_avg=daily_cpu_avg,
        daily_cpu_max=daily_cpu_max,
        daily_mem_avg=daily_mem_avg,
        daily_mem_max=daily_mem_max,
        daily_disk_avg=daily_disk_avg,
        daily_disk_max=daily_disk_max,
        daily_storage_avg_gb=daily_storage_avg_gb,
        daily_storage_max_gb=daily_storage_max_gb,
        time_labels=time_labels,
        daily_labels=daily_labels,
    )






