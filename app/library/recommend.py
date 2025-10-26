from sqlalchemy import func, inspect
from .models import LibraryItem, LibraryView, LibraryRating, QuizAttempt, LibraryBias
from ..extensions import db

def compute_score(item_id):
    views = db.session.query(func.count(LibraryView.id)).filter(LibraryView.item_id == item_id).scalar() or 0
    avg_rating = db.session.query(func.avg(LibraryRating.overall)).filter(LibraryRating.item_id == item_id).scalar() or 0
    quiz = db.session.query(func.avg(QuizAttempt.score)).filter(QuizAttempt.item_id == item_id).scalar() or 0
    score = (views * 0.2) + ((avg_rating or 0) * 2) + (quiz * 1.0)
    return score

def get_recommendations(user):
    # Base: not archived
    items_q = LibraryItem.query.filter(LibraryItem.archived.is_(False))

    # Exclude unlisted if column exists; treat NULL as excluded too
    try:
        has_unlisted = any(c["name"] == "unlisted" for c in inspect(db.engine).get_columns("library_item"))
    except Exception:
        has_unlisted = False
    if has_unlisted:
        items_q = items_q.filter(LibraryItem.unlisted.is_(False))

    items = items_q.all()

    ranked = []
    from ..teams.models import TeamMember
    team_ids = [tm.team_id for tm in TeamMember.query.filter_by(user_id=user.id).all()]
    for i in items:
        auto_score = compute_score(i.id)
        bias = i.bias_weight or 0
        tbias = db.session.query(func.sum(LibraryBias.weight)).filter(LibraryBias.item_id == i.id, LibraryBias.team_id.in_(team_ids)).scalar() or 0
        ubias = db.session.query(func.sum(LibraryBias.weight)).filter(LibraryBias.item_id == i.id, LibraryBias.user_id == user.id).scalar() or 0
        final_score = auto_score + bias + tbias + ubias
        ranked.append((i, final_score))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked