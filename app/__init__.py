from flask import Flask, render_template, redirect, url_for
from .extensions import db, login_manager, migrate, session, init_redis, mark_user_active
from .auth import auth_bp
from .dashboard import dashboard_bp
from .messaging import messaging_bp
from .notifications import notifications_bp
from .teams import teams_bp
from .leads import leads_bp
from .library import library_bp
from .presentations import presentations_bp
from .profile import profile_bp
from .coaching import coaching_bp
from flask_login import current_user
import logging
from .tests import tests_bp



def create_app(config_class="config.DevConfig"):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Init extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    session.init_app(app)
    init_redis(app)

    login_manager.login_view = "auth.login"

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(messaging_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(teams_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(library_bp)
    app.register_blueprint(presentations_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(coaching_bp)
    app.register_blueprint(tests_bp)

    # Track presence before each request
    @app.before_request
    def track_presence():
        if current_user.is_authenticated:
            mark_user_active(current_user.id)

    # Root route
    @app.route("/")
    def index():
        from .models import User
        if not User.query.first():
            return redirect(url_for("auth.setup"))
        return render_template("index.html")

    # 🔥 Ensure the special role-based chatrooms exist on startup (safe-guarded)
    with app.app_context():
        from .messaging.routes import (
            ensure_manager_group,
            ensure_manager_admin_group,
            ensure_admin_group,
        )
        try:
            # Only attempt if ChatRoom table exists
            if db.engine.has_table("chat_room"):
                ensure_manager_group()
                ensure_manager_admin_group()
                ensure_admin_group()
            else:
                app.logger.warning("⚠️ 'chat_room' table not found — run 'flask db upgrade' first!")
        except Exception as e:
            app.logger.warning(f"⚠️ Skipped auto-creating role-based chatrooms: {e}")

    return app