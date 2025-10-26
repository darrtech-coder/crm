from datetime import datetime
from ..extensions import db
from ..models import User

class Presentation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    creator_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    creator = db.relationship(User, backref="presentations")

    # 🔒 access controls
    restricted_to_managers = db.Column(db.Boolean, default=False)
    access_rules = db.relationship("PresentationAccess", backref="presentation", cascade="all,delete-orphan")

class Slide(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    presentation_id = db.Column(db.Integer, db.ForeignKey("presentation.id"))
    position = db.Column(db.Integer)
    client_content = db.Column(db.Text)
    agent_notes = db.Column(db.Text)

    pres = db.relationship(Presentation, backref="slides")

class MediaFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship(User)

# NEW: Restriction model
class PresentationAccess(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    presentation_id = db.Column(db.Integer, db.ForeignKey("presentation.id"))
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)