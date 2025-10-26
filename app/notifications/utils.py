from ..extensions import db
from ..notifications.models import Notification
from ..models import User
from ..teams.models import TeamMember

def create_notification(user_id, message):
    n = Notification(user_id=user_id, message=message)
    db.session.add(n)
    db.session.commit()

def notify_role(role, message):
    users = User.query.filter_by(role=role).all()
    for u in users:
        create_notification(u.id, message)

def notify_roles(roles, message):
    users = User.query.filter(User.role.in_(roles)).all()
    for u in users:
        create_notification(u.id, message)

def notify_team(team_id, message):
    members = TeamMember.query.filter_by(team_id=team_id).all()
    for m in members:
        create_notification(m.user_id, message)

def notify_user(user, message):
    create_notification(user.id, message)