from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from . import teams_bp
from ..extensions import db
from .models import Team, TeamMember
from ..notifications.utils import create_notification
from ..utils.rbac import role_required
from ..models import User
from ..messaging.models import ChatRoom, ChatParticipant

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
    db.session.delete(tm); db.session.commit()
    create_notification(user_id,f"Removed from team {team_id}")
    flash("Removed","success")
    return redirect(url_for("teams.index"))

@teams_bp.route("/<int:team_id>/delete", methods=["POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def delete_team(team_id):
    team = Team.query.get_or_404(team_id)

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
    # permission check omitted for brevity

    # --- Aggregate logins by day/week/month ---
    from sqlalchemy import func, extract

    logins_by_day = db.session.query(
        func.date(UserSession.login_at),
        func.count(UserSession.id)
    ).filter_by(user_id=user.id).group_by(func.date(UserSession.login_at)).all()

    # time spent on library per week
    library_week = db.session.query(
        func.strftime('%W', LibrarySession.started_at),
        func.sum(LibrarySession.duration)
    ).filter_by(user_id=user.id).group_by(func.strftime('%W', LibrarySession.started_at)).all()

    # After querying your sessions table
    from sqlalchemy import func, extract

    weekly_labels = []
    weekly_logins = []
    weekly_time = []
    monthly_labels = []
    monthly_hours = []

    # Example: mock data until we compute durations
    for i in range(1,8):
        weekly_labels.append(f"Day {i}")
        weekly_logins.append(2)
        weekly_time.append(i*0.5)

    for w in range(1,5):
        monthly_labels.append(f"Week {w}")
        monthly_hours.append(w*3)

    return render_template("teams/member_activity.html",
                           user=user,
                           weekly_labels=weekly_labels,
                           weekly_logins=weekly_logins,
                           weekly_time=weekly_time,
                           monthly_labels=monthly_labels,
                           monthly_hours=monthly_hours)

