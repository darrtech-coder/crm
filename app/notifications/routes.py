from flask import jsonify
from flask_login import login_required, current_user
from . import notifications_bp
from .models import Notification
from ..extensions import db

@notifications_bp.route("/poll")
@login_required
def poll():
    notes = Notification.query.filter_by(user_id=current_user.id, seen=False).all()
    data=[{"id":n.id,"message":n.message,"time":n.created_at.isoformat()} for n in notes]
    for n in notes: n.seen=True
    db.session.commit()
    return jsonify(data)


