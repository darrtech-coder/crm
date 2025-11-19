from datetime import datetime
from ..extensions import db
from ..models import User

from ..library.models import LibraryItem
from ..tests.models import Test
from ..academy.models import AcademyCourse


class CoachingPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    assigned_to = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)
    due_date = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    pass_percent = db.Column(db.Integer, default=60)

    creator = db.relationship(User, foreign_keys=[created_by])
    assignee = db.relationship(User, foreign_keys=[assigned_to])

    coaching_plan_items = db.relationship("CoachingPlanItem", backref="plan", cascade="all, delete-orphan")
    coaching_plan_tests = db.relationship("CoachingPlanTest", backref="plan", cascade="all, delete-orphan")
    coaching_plan_courses = db.relationship("CoachingPlanCourse", backref="plan", cascade="all, delete-orphan")

class CoachingAcknowledge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("coaching_plan.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    acknowledged_at = db.Column(db.DateTime, default=datetime.utcnow)

    plan = db.relationship(CoachingPlan, backref="acknowledgments")
    user = db.relationship(User)

class CoachingPlanItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("coaching_plan.id"))
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    item = db.relationship("LibraryItem")

class CoachingPlanTest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("coaching_plan.id"))
    test_id = db.Column(db.Integer, db.ForeignKey("test.id"))
    test = db.relationship("Test")

class CoachingPlanCourse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("coaching_plan.id"))
    course_id = db.Column(db.Integer, db.ForeignKey("academy_course.id"))
    course = db.relationship("AcademyCourse")