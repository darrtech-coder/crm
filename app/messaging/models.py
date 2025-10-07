from datetime import datetime
from ..extensions import db
from ..models import User

class ChatRoom(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)   # NEW
    type = db.Column(db.String(20), default="direct")                          # NEW
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True) # NEW
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ChatParticipant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    room_id = db.Column(db.Integer, db.ForeignKey("chat_room.id"))


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("chat_room.id"))
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    content = db.Column(db.Text, nullable=False)
    requires_read_receipt = db.Column(db.Boolean, default=False)   # NEW
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sender = db.relationship(User, backref="messages_sent")


# NEW table for read receipts
class MessageReceipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("message.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    read_at = db.Column(db.DateTime, default=datetime.utcnow)

    message = db.relationship(Message, backref="receipts")

class Friendship(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    friend_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
