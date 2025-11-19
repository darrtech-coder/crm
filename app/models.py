from .extensions import db
from flask_login import UserMixin
from datetime import datetime

ROLES = ("SUPER_ADMIN", "ADMIN", "MANAGER", "AGENT")

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    name = db.Column(db.String(100))
    role = db.Column(db.String(20), default="AGENT")
    theme = db.Column(db.String(20), default="light")  # light/dark preference
    timezone = db.Column(db.String(64), default="UTC")   # NEW: per-user timezone

    approved = db.Column(db.Boolean, default=False)
    disabled = db.Column(db.Boolean, default=False)

    dob = db.Column(db.Date)
    gender = db.Column(db.String(20))

    last_login_ip = db.Column(db.String(100))
    last_login_ua = db.Column(db.String(255))

    failed_logins = db.Column(db.Integer, default=0)
    lockout_until = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def is_admin(self):
        return self.role in ("ADMIN", "SUPER_ADMIN")

    def can_login(self):
        if not (self.approved and not self.disabled):
            return False
        if self.lockout_until and self.lockout_until > datetime.utcnow():
            return False
        return True

    def unlock(self):
        self.failed_logins = 0
        self.lockout_until = None

class MessageRead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("message.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    read_at = db.Column(db.DateTime, default=datetime.utcnow)



