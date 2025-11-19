from datetime import datetime
from ..extensions import db
from ..models import User


class LibraryItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    keywords = db.Column(db.String(255))
    filename = db.Column(db.String(255))
    mime = db.Column(db.String(100))
    size = db.Column(db.Integer)
    text_content = db.Column(db.Text)
    creator_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    min_role = db.Column(db.String(20), default="AGENT")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    allow_comments = db.Column(db.Boolean, default=True)
    bias_weight = db.Column(db.Float, default=0.0)

    category_id = db.Column(db.Integer, db.ForeignKey("library_category.id"), nullable=True)
    category = db.relationship("LibraryCategory", backref="items")

    creator = db.relationship(User, backref="library_items")
    thumbnail = db.Column(db.String(255), nullable=True)
    archived = db.Column(db.Boolean, default=False)  # soft delete/archive flag

    # 🔥 NEW
    manager_only = db.Column(db.Boolean, default=False)
    restricted_access = db.relationship("LibraryAccess", backref="item", cascade="all, delete-orphan")

    # Backref to prerequisites where the item is the “parent”
    # NEW: expose prerequisites where this item is the child that requires others
    library_prerequisites = db.relationship(
        "LibraryPrerequisite",
        foreign_keys="LibraryPrerequisite.item_id",
        cascade="all, delete-orphan",
        backref="item"
    )

    unlisted = db.Column(db.Boolean, default=False)  # NEW


class LibraryView(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow)


class LibraryRating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    easy = db.Column(db.Integer)
    complete = db.Column(db.Integer)
    overall = db.Column(db.Integer)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    item = db.relationship(LibraryItem, backref="ratings")
    user = db.relationship(User)


class LibraryAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    filename = db.Column(db.String(255))
    mime = db.Column(db.String(100))
    size = db.Column(db.Integer)

    item = db.relationship(LibraryItem, backref="attachments")


class FAQ(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    question = db.Column(db.String(255))
    answer = db.Column(db.Text)

    item = db.relationship(LibraryItem, backref="faqs")


class QuizQuestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    question = db.Column(db.String(255))

    # Proper one-to-many relationship
    options = db.relationship("QuizOption", backref="question", cascade="all, delete-orphan")

    item = db.relationship("LibraryItem", backref="quiz_questions")


class QuizOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey("quiz_question.id"))
    text = db.Column(db.String(255))
    is_correct = db.Column(db.Boolean, default=False)
    # ⚠️ No need to define another relationship here, backref already handles it.


class QuizAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    score = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")
    item = db.relationship("LibraryItem", backref="quiz_attempts")


class LibraryBias(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    weight = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BiasLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    weight = db.Column(db.Float)
    applied_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    applied_at = db.Column(db.DateTime, default=datetime.utcnow)


class LibraryCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)


class TrendingItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    item = db.relationship("LibraryItem", backref="trending_entries")

class LibraryAccess(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    user = db.relationship("User")
    team = db.relationship("Team")

class LibraryProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    position = db.Column(db.Integer, default=0)    # seconds
    duration = db.Column(db.Integer, default=0)    # seconds
    percent = db.Column(db.Float, default=0.0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id','item_id', name='uq_progress_user_item'),)


class LibraryPrerequisite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"), nullable=False)
    prereq_item_id = db.Column(db.Integer, db.ForeignKey("library_item.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # helpful relationship: the referenced prerequisite item
    prereq_item = db.relationship("LibraryItem", foreign_keys=[prereq_item_id])


    __table_args__ = (
        db.UniqueConstraint("item_id", "prereq_item_id", name="uq_lib_prereq"),
    )


