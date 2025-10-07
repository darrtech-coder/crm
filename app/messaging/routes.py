from flask import render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user
from . import messaging_bp
from ..extensions import db
from .models import Message, ChatRoom, ChatParticipant
from ..notifications.utils import create_notification
from flask import current_app
import time 

@messaging_bp.route("/")
@login_required
def index():
    return redirect(url_for("messaging.rooms"))

from ..teams.models import TeamMember
from ..models import User

@messaging_bp.route("/rooms")
@login_required
def rooms():
    # Rooms for this user
    parts = ChatParticipant.query.filter_by(user_id=current_user.id).all()
    room_ids = [p.room_id for p in parts]
    my_rooms = ChatRoom.query.filter(ChatRoom.id.in_(room_ids)).all()

    # teammates = all users in same teams as me
    if current_user.role in ("ADMIN", "SUPER_ADMIN"):
        teammates = User.query.all()
    else:
        team_ids = [tm.team_id for tm in TeamMember.query.filter_by(user_id=current_user.id).all()]
        teammates = User.query.join(TeamMember, TeamMember.user_id==User.id).filter(TeamMember.team_id.in_(team_ids)).all()

    return render_template("messaging/rooms.html", rooms=my_rooms, teammates=teammates)


@messaging_bp.route("/room/<int:room_id>")
@login_required
def room(room_id):
    messages = Message.query.filter_by(room_id=room_id).order_by(Message.created_at.asc()).all()
    return render_template("messaging/room.html", messages=messages, room_id=room_id)

@messaging_bp.route("/room/<int:room_id>/send", methods=["POST"])
@login_required
def send_message(room_id):
    room = ChatRoom.query.get_or_404(room_id)

    # prevent agents sending to channels
    if room.type == "channel" and current_user.role == "AGENT":
        return jsonify({"status": "error", "message": "Agents cannot post in channels."}), 403

    content = request.form.get("content")
    msg = Message(room_id=room.id, sender_id=current_user.id, content=content)
    db.session.add(msg)
    db.session.commit()

    # Notify participants except sender
    participants = ChatParticipant.query.filter_by(room_id=room.id).all()
    for p in participants:
        if p.user_id != current_user.id:
            create_notification(p.user_id, f"New message in room {room.name}")

    return jsonify({"status": "ok", "message": content})


@messaging_bp.route("/create_room", methods=["POST"])
@login_required
def create_room():
    room_type = request.form["type"]  # direct, team, manager_group, channel, notice
    name = request.form.get("name")

    room = ChatRoom(name=name, type=room_type, created_by=current_user.id)
    team_id = request.form.get("team_id")

    if room_type == "team" and team_id:
        room.team_id = int(team_id)

    db.session.add(room)
    db.session.commit()

    # if it's a team room, auto add all members
    if room.team_id:
        from ..teams.models import TeamMember
        members = TeamMember.query.filter_by(team_id=room.team_id).all()
        for m in members:
            db.session.add(ChatParticipant(user_id=m.user_id, room_id=room.id))
    else:
        db.session.add(ChatParticipant(user_id=current_user.id, room_id=room.id))

    db.session.commit()
    return redirect(url_for("messaging.rooms"))

@messaging_bp.route("/team_notice/<int:team_id>", methods=["POST"])
@login_required
def send_team_notice(team_id):
    if current_user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        abort(403)

    from ..teams.models import Team
    team = Team.query.get_or_404(team_id)

    room = ChatRoom.query.filter_by(team_id=team.id, type="notice").first()
    if not room:
        room = ChatRoom(
            name=f"{team.name} Notice Board",
            type="notice",
            team_id=team.id,
            created_by=current_user.id
        )
        db.session.add(room)
        db.session.commit()

    msg = Message(
        room_id=room.id,
        sender_id=current_user.id,
        content=request.form["content"],
        requires_read_receipt=True
    )
    db.session.add(msg)
    db.session.commit()

    return redirect(url_for("messaging.room", room_id=room.id))

@messaging_bp.route("/room/<int:room_id>/read/<int:msg_id>", methods=["POST"])
@login_required
def mark_read(room_id, msg_id):
    existing = MessageReceipt.query.filter_by(message_id=msg_id, user_id=current_user.id).first()
    if not existing:
        db.session.add(MessageReceipt(message_id=msg_id, user_id=current_user.id))
        db.session.commit()
    return jsonify(ok=True)

def ensure_team_room(team):
    room = ChatRoom.query.filter_by(team_id=team.id, type="team").first()
    if not room:
        room = ChatRoom(name=f"{team.name} Chat", team_id=team.id, type="team", created_by=None)
        db.session.add(room)
        db.session.commit()

        from ..teams.models import TeamMember
        members = TeamMember.query.filter_by(team_id=team.id).all()
        for m in members:
            db.session.add(ChatParticipant(user_id=m.user_id, room_id=room.id))
        db.session.commit()
    return room


@messaging_bp.route("/direct/<int:user_id>")
@login_required
def create_direct(user_id):
    # Direct chat between two users if exists, re-use it
    existing = (ChatRoom.query
                .filter_by(type="direct")
                .join(ChatParticipant)
                .filter(ChatParticipant.user_id.in_([current_user.id, user_id]))
                .first())
    if existing:
        return redirect(url_for("messaging.room", room_id=existing.id))

    # Create new direct room
    other = User.query.get_or_404(user_id)
    room = ChatRoom(name=f"DM: {current_user.username} & {other.username}", type="direct")
    db.session.add(room); db.session.commit()

    db.session.add(ChatParticipant(user_id=current_user.id, room_id=room.id))
    db.session.add(ChatParticipant(user_id=other.id, room_id=room.id))
    db.session.commit()

    return redirect(url_for("messaging.room", room_id=room.id))

from datetime import datetime

@messaging_bp.route("/presence")
@login_required
def presence():
    from ..teams.models import TeamMember
    from ..models import User

    team_ids = [tm.team_id for tm in TeamMember.query.filter_by(user_id=current_user.id).all()]
    users = User.query.join(TeamMember, TeamMember.user_id == User.id)\
                      .filter(TeamMember.team_id.in_(team_ids)).all()

    result = []
    r = current_app.redis
    now = int(time.time())

    for u in users:
        status = "offline"
        last_seen = None
        if r and r.exists(f"user:{u.id}:online"):
            status = "online"
        else:
            ts = r.get(f"user:{u.id}:last_seen")
            if ts:
                delta = now - int(ts)
                if delta < 3600:
                    last_seen = f"{delta//60} min ago"
                else:
                    last_seen = f"{delta//3600} hr ago"

        result.append({
            "id": u.id,
            "username": u.username,
            "status": status,
            "last_seen": last_seen
        })

    return jsonify(result)

@messaging_bp.route("/room/<int:room_id>/messages")
@login_required
def room_messages(room_id):
    messages = Message.query.filter_by(room_id=room_id).order_by(Message.created_at.asc()).all()
    return jsonify([{
        "id": m.id,
        "sender": m.sender.username,
        "content": m.content,
        "time": m.created_at.strftime("%H:%M")
    } for m in messages])


@messaging_bp.route("/add_friend", methods=["POST"])
@login_required
def add_friend():
    identifier = request.form.get("identifier")  # email or username
    friend = User.query.filter(
        (User.email == identifier) | (User.username == identifier)
    ).first()

    if not friend:
        flash("User not found", "danger")
        return redirect(url_for("messaging.rooms"))

    if friend.id == current_user.id:
        flash("Cannot add yourself", "warning")
        return redirect(url_for("messaging.rooms"))

    exists = Friendship.query.filter_by(user_id=current_user.id, friend_id=friend.id).first()
    if exists:
        flash("Already friends", "info")
    else:
        f1 = Friendship(user_id=current_user.id, friend_id=friend.id)
        f2 = Friendship(user_id=friend.id, friend_id=current_user.id)
        db.session.add_all([f1, f2])
        db.session.commit()
        flash(f"{friend.username} added as friend!", "success")

    return redirect(url_for("messaging.rooms"))

def ensure_role_room(room_type, name):
    """Ensure a global role-based chatroom exists, return it."""
    room = ChatRoom.query.filter_by(type=room_type).first()
    if not room:
        room = ChatRoom(name=name, type=room_type, created_by=None)
        db.session.add(room)
        db.session.commit()
    return room

def add_user_to_room_if_not(user_id, room):
    exists = ChatParticipant.query.filter_by(user_id=user_id, room_id=room.id).first()
    if not exists:
        db.session.add(ChatParticipant(user_id=user_id, room_id=room.id))
        db.session.commit()


def ensure_manager_group():
    return ensure_role_room("managers", "Managers Group")

def ensure_manager_admin_group():
    return ensure_role_room("managers_admins", "Managers + Admins Chat")

def ensure_admin_group():
    return ensure_role_room("admins", "Admins Group")