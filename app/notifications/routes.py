from flask import jsonify, request, redirect, url_for, flash
from flask_login import login_required, current_user
from . import notifications_bp
from .models import Notification
from ..extensions import db, safe_commit

@notifications_bp.route("/poll")
@login_required
def poll():
    notes = Notification.query.filter_by(user_id=current_user.id, seen=False).all()
    data=[{"id":n.id,"message":n.message,"time":n.created_at.isoformat()} for n in notes]
    # for n in notes: n.seen=True
    # db.session.commit()
    return jsonify(data)


@notifications_bp.route("/mark_seen", methods=["POST"])
@login_required
def mark_seen():
    ids = (request.get_json(silent=True) or {}).get("ids", [])
    if not ids:
        return jsonify({"ok": True, "updated": 0})
    updated = (Notification.query
        .filter(Notification.user_id == current_user.id,
        Notification.id.in_(ids))
        .update({Notification.seen: True}, synchronize_session=False))
    safe_commit()
    return jsonify({"ok": True, "updated": updated})



# --- [NEW] Route to toggle a single notification's status ---
@notifications_bp.route("/<int:note_id>/toggle_seen", methods=["POST"])
@login_required
def toggle_seen(note_id):
    note = Notification.query.get_or_404(note_id)
    if note.user_id != current_user.id:
        flash("Unauthorized", "danger")
        return redirect(url_for('dashboard.unread_notifications'))

    note.seen = not note.seen
    db.session.commit()
    
    # Check if request came from AJAX or a form post
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True, "seen": note.seen})
    
    flash("Notification status updated.", "success")
    return redirect(url_for('dashboard.unread_notifications'))



