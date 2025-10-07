from datetime import datetime
from ..extensions import db
from ..models import User
from ..library.models import LibraryItem


class Test(db.Model):
    """Top-level test or assessment."""
    id = db.Column(db.Integer, primary_key=True)
    time_limit = db.Column(db.Integer, nullable=True)
    title = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(20), default="draft")  # draft, public, private, archived, scheduled
    publish_at = db.Column(db.DateTime, nullable=True)
    description = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    linked_item_id = db.Column(
        db.Integer, db.ForeignKey("library_item.id"), nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    creator = db.relationship(User, backref="tests_created")
    linked_item = db.relationship(LibraryItem, backref="linked_tests")
    questions = db.relationship(
        "TestQuestion",
        backref="test",
        cascade="all, delete-orphan",
        lazy=True,
    )
    submissions = db.relationship(
        "TestSubmission",
        backref="test",
        cascade="all, delete-orphan",
        lazy=True,
    )

    requirements = db.relationship(
        "TestCourseRequirement", backref="test",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Test {self.title}>"


class TestQuestion(db.Model):
    """A single question belonging to a Test."""
    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("test.id"), nullable=False)
    type = db.Column(db.String(20))  # "mcq", "short_text", "audio"
    question = db.Column(db.String(500), nullable=False)

    # Choices for multiple-choice questions
    options = db.relationship(
        "TestOption",
        backref="question",
        cascade="all, delete-orphan",
        lazy=True,
    )
    media_path = db.Column(db.String(255), nullable=True)   # <‑ add this line

    def __repr__(self):
        return f"<Question {self.id} ({self.type})>"


class TestOption(db.Model):
    """Options for a multiple-choice question."""
    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey("test_question.id"))
    text = db.Column(db.String(255))
    is_correct = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f"<Option {self.text[:30]}{' ✅' if self.is_correct else ''}>"


class TestSubmission(db.Model):
    """A user’s submission for a given test."""
    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("test.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    time_spent = db.Column(db.Integer)  # seconds
    score = db.Column(db.Float)

    user = db.relationship(User, backref="test_submissions")
    answers = db.relationship(
        "TestAnswer",
        backref="submission",
        cascade="all, delete-orphan",
        lazy=True,
    )

    def __repr__(self):
        return f"<Submission test={self.test_id} user={self.user_id}>"


class TestAnswer(db.Model):
    """Individual answers given in a submission."""
    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("test_submission.id"))
    question_id = db.Column(db.Integer, db.ForeignKey("test_question.id"))
    selected_option = db.Column(
        db.Integer, db.ForeignKey("test_option.id"), nullable=True
    )
    answer_text = db.Column(db.Text, nullable=True)
    answer_audio = db.Column(db.String(255), nullable=True)
    is_correct = db.Column(db.Boolean, default=None)
    score = db.Column(db.Float, default=0.0)   # <‑‑ new

    question = db.relationship("TestQuestion")
    option = db.relationship("TestOption")

    def __repr__(self):
        return f"<Answer Q{self.question_id} sub={self.submission_id}>"


from ..library.models import LibraryItem

class TestCourseRequirement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("test.id"))
    course_id = db.Column(db.Integer, db.ForeignKey("library_item.id"))
    requirement_type = db.Column(db.String(20))  # suggested, recommended, required
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


    course = db.relationship(LibraryItem, backref="course_requirements")

class TestPrerequisite(db.Model):
    """Defines prerequisite tests required before taking another test."""
    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("test.id"))
    prereq_test_id = db.Column(db.Integer, db.ForeignKey("test.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # relationships for clarity
    parent_test = db.relationship("Test", foreign_keys=[test_id], backref="prerequisites")
    prereq_test = db.relationship("Test", foreign_keys=[prereq_test_id])

    def __repr__(self):
        return f"<Prereq {self.prereq_test.title} → {self.parent_test.title}>"