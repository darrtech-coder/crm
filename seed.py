import click
from faker import Faker
from datetime import datetime
from app import create_app, db
from app.models import User
from app.teams.models import Team, TeamMember
from app.library.models import LibraryItem, LibraryCategory
from app.messaging.models import ChatRoom, ChatParticipant
from werkzeug.security import generate_password_hash

fake = Faker()

app = create_app()

@app.cli.command("seed")
def seed():
    """Wipe DB, run migrations, and seed with dummy data."""
    from flask_migrate import upgrade, downgrade
    click.echo("➡️ Dropping database...")
    db.drop_all()
    db.session.commit()

    click.echo("➡️ Recreating tables...")
    db.create_all()

    # ✅ Seed Users
    admin = User(
        email="admin@example.com",
        username="admin",
        password=generate_password_hash("password"),
        role="SUPER_ADMIN",
        approved=True
    )
    manager = User(
        email="manager@example.com",
        username="manager",
        password=generate_password_hash("password"),
        role="MANAGER",
        approved=True
    )
    agent1 = User(
        email="agent1@example.com",
        username="agent1",
        password=generate_password_hash("password"),
        role="AGENT",
        approved=True
    )
    agent2 = User(
        email="agent2@example.com",
        username="agent2",
        password=generate_password_hash("password"),
        role="AGENT",
        approved=True
    )

    db.session.add_all([admin, manager, agent1, agent2])
    db.session.commit()

    click.echo("✅ Users seeded")

    # ✅ Seed Team
    team = Team(name="Alpha Team")
    db.session.add(team)
    db.session.commit()

    db.session.add_all([
        TeamMember(team_id=team.id, user_id=manager.id, role="MANAGER"),
        TeamMember(team_id=team.id, user_id=agent1.id, role="AGENT"),
        TeamMember(team_id=team.id, user_id=agent2.id, role="AGENT")
    ])
    db.session.commit()
    click.echo("✅ Team seeded")

    # ✅ Ensure team chatroom
    room = ChatRoom(name="Alpha Team Chat", team_id=team.id, type="team", created_by=manager.id)
    db.session.add(room)
    db.session.commit()
    for u in [manager, agent1, agent2]:
        db.session.add(ChatParticipant(user_id=u.id, room_id=room.id))
    db.session.commit()

    click.echo("✅ Team Chatroom seeded")

    # ✅ Seed Library Categories & Items
    cat1 = LibraryCategory(name="Sales")
    cat2 = LibraryCategory(name="Product")
    db.session.add_all([cat1, cat2])
    db.session.commit()

    item1 = LibraryItem(
        title="Sales Playbook",
        description="How to sell effectively",
        creator_id=admin.id,
        category_id=cat1.id,
        created_at=datetime.utcnow()
    )
    item2 = LibraryItem(
        title="Product Overview",
        description="Deep dive into product features",
        creator_id=admin.id,
        category_id=cat2.id,
        created_at=datetime.utcnow()
    )
    db.session.add_all([item1, item2])
    db.session.commit()

    click.echo("✅ Library items seeded")
    click.echo("🎉 Seed complete — login as admin@example.com / password")

