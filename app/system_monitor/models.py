# app/system_monitor/models.py
from datetime import datetime
from ..extensions import db

class SystemMetric(db.Model):
    __tablename__ = "system_metric"

    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    cpu_percent = db.Column(db.Float)
    mem_used_mb = db.Column(db.Float)
    mem_percent = db.Column(db.Float)

    disk_used_gb = db.Column(db.Float)
    disk_percent = db.Column(db.Float)

    # Optional, if you added storage tracking later
    storage_total_bytes = db.Column(db.BigInteger)