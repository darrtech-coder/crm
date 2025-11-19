from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from . import teams_bp
from ..extensions import db
from .models import Team, TeamMember
from ..notifications.utils import create_notification
from ..utils.rbac import role_required
from ..models import User
from ..messaging.models import ChatRoom, ChatParticipant

# -------------------- [START] New Imports --------------------
from sqlalchemy import func, extract
from datetime import date, timedelta
from ..security.models import UserSession
from ..activity.models import LibrarySession
from ..tests.models import TestSubmission
from ..library.models import LibraryItem
# -------------------- [END] New Imports --------------------

# [FIX] Define the helper function at the module level
def _date_to_str(val):
    """Helper to handle different date/datetime types from different DBs."""
    if isinstance(val, (dt_class, date)):
        return val.strftime("%Y-%m-%d")
    if val is None:
        return ""
    return str(val).split(' ')[0]

@teams_bp.route("/")
@login_required
def index():
    if current_user.role in ("ADMIN", "SUPER_ADMIN"):
        teams = Team.query.all()
    elif current_user.role == "MANAGER":
        my_team_ids = [tm.team_id for tm in TeamMember.query.filter_by(user_id=current_user.id, role="MANAGER")]
        teams = Team.query.filter(Team.id.in_(my_team_ids)).all()
    else:  # Agent
        tm = TeamMember.query.filter_by(user_id=current_user.id).first()
        teams = [tm.team] if tm else []

    # provide all users for dropdown (admins/managers only)
    users = []
    if current_user.role in ("ADMIN","SUPER_ADMIN","MANAGER"):
        users = User.query.order_by(User.username).all()

    return render_template("teams/index.html", teams=teams, users=users)

@teams_bp.route("/create", methods=["GET","POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def create():
    if request.method=="POST":
        name=request.form["name"]
        team=Team(name=name)
        db.session.add(team); db.session.commit()
        ensure_team_chatroom(team)   # 👈 NEW: auto create chatroom for that team
        flash("Team created","success")
        return redirect(url_for("teams.index"))
    return render_template("teams/create.html")

@teams_bp.route("/<int:team_id>/add_member", methods=["POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN","MANAGER")
def add_member(team_id):
    team = Team.query.get_or_404(team_id)   # 👈 Add this

    user_id = int(request.form["user_id"])
    role = request.form.get("role", "AGENT")

    existing = TeamMember.query.filter_by(team_id=team.id, user_id=user_id).first()
    if existing:
        flash("User already in team", "info")
        return redirect(url_for("teams.index"))

    tm = TeamMember(team_id=team.id, user_id=user_id, role=role)
    db.session.add(tm)
    db.session.commit()

    # Ensure team has chatroom
    room = ChatRoom.query.filter_by(team_id=team.id, type="team").first()
    if room:
        db.session.add(ChatParticipant(user_id=user_id, room_id=room.id))
        db.session.commit()

    create_notification(user_id, f"Added to team {team.name}")
    flash("Member added", "success")
    return redirect(url_for("teams.index"))

@teams_bp.route("/<int:team_id>/remove_member/<int:user_id>", methods=["POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN","MANAGER")
def remove_member(team_id,user_id):
    tm=TeamMember.query.filter_by(team_id=team_id,user_id=user_id).first()
    if not tm:
        flash("Not found","warning"); return redirect(url_for("teams.index"))
    # Remove from chatroom participants if exists
    room = ChatRoom.query.filter_by(team_id=team_id, type="team").first()
    if room:
        ChatParticipant.query.filter_by(room_id=room.id, user_id=user_id).delete()
    db.session.delete(tm); db.session.commit()
    create_notification(user_id,f"Removed from team {team_id}")
    flash("Removed","success")
    return redirect(url_for("teams.index"))

@teams_bp.route("/<int:team_id>/delete", methods=["POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def delete_team(team_id):
    team = Team.query.get_or_404(team_id)

    # Delete team chatrooms + participants
    rooms = ChatRoom.query.filter_by(team_id=team.id).all()
    for r in rooms:
        ChatParticipant.query.filter_by(room_id=r.id).delete()
        db.session.delete(r)

    # Remove members first
    TeamMember.query.filter_by(team_id=team.id).delete()
    db.session.delete(team)
    db.session.commit()

    flash("Team deleted", "success")
    return redirect(url_for("teams.index"))

@teams_bp.route("/<int:team_id>")
@login_required
def detail(team_id):
    team = Team.query.get_or_404(team_id)
    members = TeamMember.query.filter_by(team_id=team.id).all()

    room = ensure_team_chatroom(team)

    # permission check: managers can only see their own teams
    if current_user.role == "MANAGER":
        my_team_ids = [tm.team_id for tm in TeamMember.query.filter_by(user_id=current_user.id, role="MANAGER")]
        if team.id not in my_team_ids:
            flash("Not authorized to view this team", "danger")
            return redirect(url_for("teams.index"))

    # 👇 Ensure the team has a chatroom, and pass it into render template
    # from .routes import ensure_team_chatroom
    room = ensure_team_chatroom(team)

    return render_template("teams/detail.html", team=team, members=members, room=room)

def ensure_team_chatroom(team):
    """Make sure a default team chatroom exists for this team."""
    room = ChatRoom.query.filter_by(team_id=team.id, type="team").first()
    if not room:
        room = ChatRoom(name=f"{team.name} Chat", team_id=team.id, type="team")
        db.session.add(room)
        db.session.commit()

        # add all current members as participants
        members = TeamMember.query.filter_by(team_id=team.id).all()
        for m in members:
            db.session.add(ChatParticipant(user_id=m.user_id, room_id=room.id))
        db.session.commit()
    return room



@teams_bp.route("/member/<int:user_id>")
@login_required
def member_activity(user_id):
    user = User.query.get_or_404(user_id)
    
    # Helper to handle different date/datetime types from different DBs
    def _date_to_str(val):
        if isinstance(val, (dt_class, date)): return val.strftime("%Y-%m-%d")
        return str(val).split(' ')[0]

    today = date.today()
    seven_days_ago = today - timedelta(days=6)
    four_weeks_ago = today - timedelta(weeks=4)

    # --- Weekly Data (Works across DBs) ---
    date_range = [seven_days_ago + timedelta(days=i) for i in range(7)]
    weekly_data = {d.strftime("%Y-%m-%d"): {"logins": 0, "time": 0.0} for d in date_range}

    logins = db.session.query(
        func.date(UserSession.login_at), func.count(UserSession.id)
    ).filter(
        UserSession.user_id == user_id,
        func.date(UserSession.login_at) >= seven_days_ago
    ).group_by(func.date(UserSession.login_at)).all()
    for login_date, count in logins:
        date_str = _date_to_str(login_date)
        if date_str in weekly_data:
            weekly_data[date_str]["logins"] = int(count or 0)

    study_sessions = db.session.query(
        func.date(LibrarySession.started_at), func.sum(LibrarySession.duration)
    ).filter(
        LibrarySession.user_id == user_id,
        func.date(LibrarySession.started_at) >= seven_days_ago
    ).group_by(func.date(LibrarySession.started_at)).all()
    for study_date, total_seconds in study_sessions:
        date_str = _date_to_str(study_date)
        if date_str in weekly_data:
            weekly_data[date_str]["time"] = round((float(total_seconds or 0) / 3600.0), 2)
            
    weekly_labels = list(weekly_data.keys())
    weekly_logins = [d["logins"] for d in weekly_data.values()]
    weekly_time = [d["time"] for d in weekly_data.values()]

    # --- [FIX] Monthly Data (Dialect-Specific) ---
    dialect_name = db.engine.dialect.name
    
    if dialect_name == 'postgresql':
        # Use PostgreSQL's to_char function
        week_func = func.to_char(LibrarySession.started_at, 'WW')
    else: # Default to SQLite's strftime
        week_func = func.strftime('%W', LibrarySession.started_at)
    
    monthly_sessions_query = db.session.query(
        week_func,
        func.sum(LibrarySession.duration)
    ).filter(
        LibrarySession.user_id == user_id,
        func.date(LibrarySession.started_at) >= four_weeks_ago
    ).group_by(week_func).all()
    
    monthly_data = {}
    for week_num, total_seconds in monthly_sessions_query:
        # Both to_char and strftime return strings, so this is safe
        week_key = str(week_num).strip() 
        monthly_data[week_key] = round((float(total_seconds or 0) / 3600.0), 2)

    # Ensure all recent weeks are present, even if with 0 hours
    for i in range(4):
        wn_str = (today - timedelta(weeks=i)).strftime('%W')
        if wn_str not in monthly_data:
            monthly_data[wn_str] = 0.0

    sorted_weeks = sorted(monthly_data.keys())
    monthly_labels = [f"Week {wn}" for wn in sorted_weeks]
    monthly_hours = [monthly_data[wn] for wn in sorted_weeks]

    # --- Activity Log Queries (Unchanged) ---
    library_history = LibrarySession.query.join(LibraryItem, LibrarySession.item_id == LibraryItem.id)\
        .filter(LibrarySession.user_id == user_id).order_by(LibrarySession.started_at.desc()).limit(20).all()
    
    test_history = TestSubmission.query.filter_by(user_id=user_id).join(TestSubmission.test)\
        .order_by(TestSubmission.submitted_at.desc()).limit(20).all()

    activity_summary = db.session.query(
        LibraryItem.id, LibraryItem.title,
        func.sum(LibrarySession.duration).label('total_duration'),
        func.count(LibrarySession.id).label('view_count')
    ).join(LibrarySession, LibraryItem.id == LibrarySession.item_id)\
     .filter(LibrarySession.user_id == user_id)\
     .group_by(LibraryItem.id, LibraryItem.title)\
     .order_by(func.sum(LibrarySession.duration).desc()).all()

    return render_template("teams/member_activity.html",
                           user=user,
                           weekly_labels=weekly_labels, weekly_logins=weekly_logins, weekly_time=weekly_time,
                           monthly_labels=monthly_labels, monthly_hours=monthly_hours,
                           library_history=library_history, test_history=test_history,
                           activity_summary=activity_summary)

