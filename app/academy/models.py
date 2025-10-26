from datetime import datetime
from ..extensions import db
from ..library.models import LibraryItem
from ..tests.models import Test

class AcademyCourse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    thumbnail = db.Column(db.String(255))
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    published = db.Column(db.Boolean, default=True)
    items = db.relationship("AcademyCourseItem", backref="course",
                            cascade="all, delete-orphan",
                            order_by="AcademyCourseItem.position")

class AcademyCourseItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("academy_course.id"))
    position = db.Column(db.Integer, default=1)   # 1-based order
    type = db.Column(db.String(20))               # "library" or "test"
    library_item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"), nullable=True)
    test_id = db.Column(db.Integer, db.ForeignKey("test.id"), nullable=True)
    # gating
    pass_percent = db.Column(db.Integer, default=0)      # 0 = no pass needed
    require_review = db.Column(db.Boolean, default=False)

    library_item = db.relationship("LibraryItem")
    test = db.relationship("Test")

class AcademyModuleStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    course_item_id = db.Column(db.Integer, db.ForeignKey("academy_course_item.id"))
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id","course_item_id", name="uq_user_course_item"),
    )