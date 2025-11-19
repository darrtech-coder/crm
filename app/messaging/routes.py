from flask import render_template, request, jsonify, redirect, url_for, abort, flash
from flask_login import login_required, current_user
from . import messaging_bp
from ..extensions import db
# --- [FIX] Add MessageReaction to the import ---
from .models import Message, ChatRoom, ChatParticipant, MessageReceipt, Friendship, MessageReaction
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

    if current_user.role in ("ADMIN", "SUPER_ADMIN"):
        all_contacts = User.query.filter(User.id != current_user.id).all()
    else:
        team_ids = [tm.team_id for tm in TeamMember.query.filter_by(user_id=current_user.id).all()]
        teammate_ids = {tm.user_id for tm in TeamMember.query.filter(TeamMember.team_id.in_(team_ids)).all()}
        
        friend_ids = {f.friend_id for f in Friendship.query.filter_by(user_id=current_user.id).all()}
        
        all_contact_ids = teammate_ids.union(friend_ids)
        if current_user.id in all_contact_ids:
            all_contact_ids.remove(current_user.id)
            
        all_contacts = User.query.filter(User.id.in_(list(all_contact_ids))).all()

    all_contacts.sort(key=lambda u: u.username)

    return render_template("messaging/rooms.html", rooms=my_rooms, teammates=all_contacts)


@messaging_bp.route("/room/<int:room_id>")
@login_required
def room(room_id):
    # --- [FIX] Fetch actual participants of THIS room, not all teammates. ---
    room = ChatRoom.query.get_or_404(room_id)

    # First, confirm the current user is actually in this room.
    is_participant = ChatParticipant.query.filter_by(room_id=room_id, user_id=current_user.id).first()
    if not is_participant and current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        flash("You are not a member of this chat room.", "danger")
        return redirect(url_for("messaging.rooms"))

    # Now, get all participants for the sidebar.
    participant_links = ChatParticipant.query.filter_by(room_id=room_id).all()
    participant_ids = [p.user_id for p in participant_links]
    
    # Fetch the User objects for these participants.
    # The template uses the variable 'teammates', so we'll pass the list with that name.
    participants = User.query.filter(User.id.in_(participant_ids)).order_by(User.username).all()
        
    return render_template("messaging/room.html", room_id=room_id, teammates=participants)

@messaging_bp.route("/room/<int:room_id>/send", methods=["POST"])
@login_required
def send_message(room_id):
    room = ChatRoom.query.get_or_404(room_id)

    if room.type == "channel" and current_user.role == "AGENT":
        return jsonify({"status": "error", "message": "Agents cannot post in channels."}), 403

    content = request.form.get("content")
    if not content or not content.strip():
        return jsonify({"status": "error", "message": "Message cannot be empty."}), 400

    msg = Message(room_id=room.id, sender_id=current_user.id, content=content)
    db.session.add(msg)
    db.session.commit()

    participants = ChatParticipant.query.filter_by(room_id=room.id).all()
    for p in participants:
        if p.user_id != current_user.id:
            create_notification(p.user_id, f"New message in room {room.name}")

    return jsonify({"status": "ok", "message": content})


@messaging_bp.route("/create_room", methods=["POST"])
@login_required
def create_room():
    room_type = request.form["type"]
    name = request.form.get("name")

    room = ChatRoom(name=name, type=room_type, created_by=current_user.id)
    team_id = request.form.get("team_id")

    if room_type == "team" and team_id:
        room.team_id = int(team_id)

    db.session.add(room)
    db.session.commit()

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
        room = ChatRoom(name=f"{team.name} Notice Board", type="notice", team_id=team.id, created_by=current_user.id)
        db.session.add(room)
        db.session.commit()

    msg = Message(room_id=room.id, sender_id=current_user.id, content=request.form["content"], requires_read_receipt=True)
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
    from sqlalchemy import and_

    # Find a room that has exactly these two participants
    # This is a more robust way to find a 1-on-1 chat
    room_id_query = db.session.query(ChatParticipant.room_id)\
        .filter(ChatParticipant.user_id.in_([current_user.id, user_id]))\
        .group_by(ChatParticipant.room_id)\
        .having(db.func.count(ChatParticipant.user_id) == 2)
    
    room = ChatRoom.query.filter(
        ChatRoom.type == 'direct',
        ChatRoom.id.in_(room_id_query)
    ).first()

    if room:
        return redirect(url_for("messaging.room", room_id=room.id))

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
        elif r:
            ts = r.get(f"user:{u.id}:last_seen")
            if ts:
                try:
                    delta = now - int(ts)
                    if delta < 3600: last_seen = f"{max(1, delta//60)}m ago"
                    else: last_seen = f"{delta//3600}h ago"
                except (ValueError, TypeError):
                    pass

        result.append({ "id": u.id, "username": u.username, "status": status, "last_seen": last_seen })

    return jsonify(result)

# Replace the existing room_messages function with this one
@messaging_bp.route("/room/<int:room_id>/messages")
@login_required
def room_messages(room_id):
    try:
        messages = Message.query.filter_by(room_id=room_id).order_by(Message.created_at.asc()).all()
        
        processed_messages = []
        for m in messages:
            # --- START: Defensive block for a single message ---
            try:
                # Prevent crash if a message's sender was deleted
                if not m.sender:
                    continue

                # --- Defensively get sender's badges ---
                sender_badges = []
                user_badge_links = m.sender.badges.limit(3).all()
                for ub in user_badge_links:
                    # Check if the related Badge object exists
                    if ub.badge:
                        sender_badges.append({
                            "name": ub.badge.name,
                            "image_file": ub.badge.image_file
                        })
                
                # --- Defensively aggregate reactions ---
                reactions = {}
                for r in m.reactions:
                    # Check if the user who reacted still exists
                    if not r.user:
                        continue
                    
                    if r.emoji not in reactions:
                        reactions[r.emoji] = {"count": 0, "users": [], "usernames": []}
                    
                    reactions[r.emoji]["count"] += 1
                    reactions[r.emoji]["users"].append(r.user.id)
                    reactions[r.emoji]["usernames"].append(r.user.username)
                    
                processed_messages.append({
                    "id": m.id,
                    "sender": m.sender.username,
                    "content": m.content,
                    "time": m.created_at.strftime("%H:%M"),
                    "badges": sender_badges,
                    "reactions": reactions
                })
            except Exception as e:
                # If processing a single message fails, log it and skip it
                current_app.logger.error(f"Failed to process message_id {m.id}: {e}")
                continue
            # --- END: Defensive block for a single message ---
            
        return jsonify(processed_messages)

    except Exception as e:
        # Catch-all for any other unexpected errors in this route
        current_app.logger.error(f"Error in room_messages for room {room_id}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500


@messaging_bp.route("/add_friend", methods=["POST"])
@login_required
def add_friend():
    identifier = request.form.get("identifier")
    friend = User.query.filter((User.email == identifier) | (User.username == identifier)).first()

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

def ensure_manager_group(): return ensure_role_room("managers", "Managers Group")
def ensure_manager_admin_group(): return ensure_role_room("managers_admins", "Managers + Admins Chat")
def ensure_admin_group(): return ensure_role_room("admins", "Admins Group")


@messaging_bp.route("/react/<int:message_id>", methods=["POST"])
@login_required
def react_to_message(message_id):
    emoji = request.form.get("emoji")
    if not emoji:
        return jsonify({"ok": False, "error": "Emoji is required."}), 400

    existing_reaction = MessageReaction.query.filter_by(message_id=message_id, user_id=current_user.id, emoji=emoji).first()

    if existing_reaction:
        db.session.delete(existing_reaction)
        action = "removed"
    else:
        new_reaction = MessageReaction(message_id=message_id, user_id=current_user.id, emoji=emoji)
        db.session.add(new_reaction)
        action = "added"
    
    db.session.commit()
    return jsonify({"ok": True, "action": action})