from ..extensions import db
from ..security.models import SystemSetting

def get_setting(key, default=None):
    s = SystemSetting.query.filter_by(key=key).first()
    return s.value if s else default

def set_setting(key, value):
    s = SystemSetting.query.filter_by(key=key).first()
    if s:
        s.value = str(value)
    else:
        s = SystemSetting(key=key, value=str(value))
        db.session.add(s)
    db.session.commit()
