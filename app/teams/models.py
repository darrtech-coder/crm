from ..extensions import db
from ..models import User

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    storage_limit_mb = db.Column(db.Integer, default=100)

    # NEW relationship (Team has one chat room if created)
    chatrooms = db.relationship("ChatRoom", backref="team", lazy="dynamic")

class TeamMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    role = db.Column(db.String(20))  # MANAGER or AGENT

    # relationship backrefs
    user = db.relationship("User", backref="team_memberships")
    team = db.relationship("Team", backref="members")
