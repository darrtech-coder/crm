from datetime import datetime
from ..extensions import db

class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    # Core lead info
    name = db.Column(db.String(120), nullable=False)             # REQUIRED
    phone = db.Column(db.String(50), nullable=False)             # REQUIRED
    email = db.Column(db.String(120), nullable=True)
    contact_info = db.Column(db.String(255))                     # optional legacy field
    
    # Relationships with explicit FK names (avoids Alembic SQLite issues)
    created_by = db.Column(
        db.Integer,
        db.ForeignKey("user.id", name="fk_lead_created_by"),     # Agent who submitted
        nullable=False
    )
    assigned_to = db.Column(
        db.Integer,
        db.ForeignKey("user.id", name="fk_lead_assigned_to"),    # Optional: manager assignment
        nullable=True
    )
    reviewed_by = db.Column(
        db.Integer,
        db.ForeignKey("user.id", name="fk_lead_reviewed_by"),    # Reviewer (manager/admin)
        nullable=True
    )
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    
    # Review status
    review_status = db.Column(db.String(20), default="pending")
    # Values: "good", "bad", "pending", "need_callback"
    
    # Workflow status
    status = db.Column(db.String(20), default="open")
    # Values: "open", "closed", "pending", "in_progress", etc.
    
    # Misc notes
    notes = db.Column(db.Text)