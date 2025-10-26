from datetime import datetime
from ..extensions import db
# -------------------- [START] Add import --------------------
# from ..library.models import LibraryItem
# -------------------- [END] Add import --------------------

class LibrarySession(db.Model):
    __tablename__ = "library_session"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    duration = db.Column(db.Integer, default=0)
    # -------------------- [START] Add relationship --------------------
    item = db.relationship("LibraryItem")
    # -------------------- [END] Add relationship --------------------