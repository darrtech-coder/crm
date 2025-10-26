from flask import render_template, redirect, url_for, jsonify, request, flash
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

from ..activity.models import LibrarySession



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
    if current_user.role!="SUPER_ADMIN": return redirect(url_for("dashboard.index"))
    return render_template("dashboard/super_admin.html", user=current_user)

@dashboard_bp.route("/admin")
@login_required
def admin():
    if current_user.role not in ("ADMIN","SUPER_ADMIN"): return redirect(url_for("dashboard.index"))
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    pagination = User.query.order_by(User.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return render_template("dashboard/admin.html", user=current_user, users=pagination.items, pagination=pagination, per_page=per_page)


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
    if current_user.role!="SUPER_ADMIN":
        return redirect(url_for("dashboard.index"))

    if request.method=="POST":
        set_setting("LOG_UNAUTHORIZED", request.form.get("log_unauth")=="on")
        set_setting("MAX_FAILED_LOGINS", request.form.get("max_failed","5"))
        set_setting("LOCKOUT_MINUTES", request.form.get("lockout","15"))
        set_setting("ALLOW_REGISTRATION", request.form.get("allow_reg")=="on")

        # ✅ New password rules
        set_setting("PASSWORD_MIN_LENGTH", request.form.get("pw_length","8"))
        set_setting("PASSWORD_REQUIRE_UPPER", "pw_upper" in request.form)
        set_setting("PASSWORD_REQUIRE_NUMBER", "pw_number" in request.form)
        set_setting("PASSWORD_REQUIRE_SYMBOL", "pw_symbol" in request.form)

        # New
        set_setting("TIMEZONE", request.form.get("timezone","UTC").strip() or "UTC")
        set_setting("REDIS_MODE", request.form.get("redis_mode","local"))
        set_setting("REDIS_URL", request.form.get("redis_url","").strip())

        flash("Settings updated","success")

    vals = {
        "log_unauth": get_setting("LOG_UNAUTHORIZED","True"),
        "max_failed": get_setting("MAX_FAILED_LOGINS","5"),
        "lockout": get_setting("LOCKOUT_MINUTES","15"),
        "allow_reg": get_setting("ALLOW_REGISTRATION","True"),
        # ✅ include password rules in template
        "pw_length": int(get_setting("PASSWORD_MIN_LENGTH","8")),
        "pw_upper": get_setting("PASSWORD_REQUIRE_UPPER","True")=="True",
        "pw_number": get_setting("PASSWORD_REQUIRE_NUMBER","True")=="True",
        "pw_symbol": get_setting("PASSWORD_REQUIRE_SYMBOL","True")=="True",
        # New
        "timezone": get_setting("TIMEZONE","UTC"),
        "redis_mode": get_setting("REDIS_MODE","local"),
        "redis_url": get_setting("REDIS_URL","")
    }
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