from dotenv import load_dotenv
import redis, os
from flask import Flask, render_template, redirect, url_for, request, current_app
from urllib.parse import urlparse
from datetime import datetime
from zoneinfo import ZoneInfo
from flask_login import current_user
from sqlalchemy import inspect

# Load environment variables from .env file first
load_dotenv()

from .extensions import db, login_manager, migrate, session, init_redis, mark_user_active, start_background_workers
from .utils.storage import storage
from .storage_bp import storage_bp

# Blueprints
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
from .tests import tests_bp
from .academy import academy_bp
from .badges import badges_bp

# Build a reusable timezone list once at import time
try:
    from zoneinfo import available_timezones
    TZ_LIST = sorted(available_timezones())
except Exception:
    TZ_LIST = [
        "UTC","Europe/London","Europe/Berlin","Europe/Paris",
        "America/New_York","America/Chicago","America/Denver","America/Los_Angeles",
        "America/Sao_Paulo","Africa/Johannesburg",
        "Asia/Dubai","Asia/Kolkata","Asia/Singapore","Asia/Tokyo","Australia/Sydney"
    ]

def create_app(config_class="config.DevConfig"):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)

    # --- Robust Redis Configuration ---
    with app.app_context():
        try:
            inspector = inspect(db.engine)
            initial_redis_url = app.config.get("REDIS_URL")

            if inspector.has_table("system_setting"):
                from .utils.settings import get_setting
                app.config["TIMEZONE"] = get_setting("TIMEZONE", "UTC")
                app.config["REDIS_MODE"] = get_setting("REDIS_MODE", "local")
                final_redis_url = get_setting("REDIS_URL", initial_redis_url)
                app.logger.info("✅ Loaded settings from database.")
            else:
                app.logger.warning("⚠️ No 'system_setting' table found — using smart defaults for setup phase.")
                app.config["TIMEZONE"] = "UTC"
                final_redis_url = initial_redis_url
                if initial_redis_url and "localhost" not in initial_redis_url and "127.0.0.1" not in initial_redis_url:
                    app.config["REDIS_MODE"] = "remote"
                    app.logger.info("💡 Detected remote Redis URL during setup. Setting REDIS_MODE to 'remote'.")
                else:
                    app.config["REDIS_MODE"] = "local"
        except Exception as e:
            app.logger.error(f"❌ DB connection failed during initial config: {e}. Falling back to safe defaults.")
            app.config["TIMEZONE"] = "UTC"
            app.config["REDIS_MODE"] = "local"
            final_redis_url = app.config.get("REDIS_URL")

        if app.config["REDIS_MODE"] == "none":
            app.config["SESSION_TYPE"] = "filesystem"
            app.logger.info("🔧 Session backend configured to: filesystem.")
        else:
            app.config["SESSION_TYPE"] = "redis"
            if final_redis_url:
                try:
                    app.config["SESSION_REDIS"] = redis.from_url(final_redis_url)
                    safe_url = final_redis_url.split('@')[-1]
                    app.logger.info(f"✅ Session backend configured to use Redis at: {safe_url}")
                except Exception as e:
                    app.logger.error(f"❌ Redis connection failed: {e}. Degrading to filesystem sessions.")
                    app.config["SESSION_TYPE"] = "filesystem"
            else:
                app.logger.error("❌ Redis mode is enabled, but no REDIS_URL is available. Sessions may fail.")
                app.config["SESSION_TYPE"] = "filesystem"

    # --- Initialize Extensions ---
    migrate.init_app(app, db)
    login_manager.init_app(app)
    session.init_app(app) 
    init_redis(app)
    storage.init_app(app)

    # --- Jinja Filters ---
    def tz(value, fmt="%Y-%m-%d %H:%M %Z"):
        if not isinstance(value, datetime): return value
        tzname = (current_user.timezone if getattr(current_user, "is_authenticated", False) and current_user.timezone else app.config.get("TIMEZONE", "UTC"))
        if value.tzinfo is None: value = value.replace(tzinfo=ZoneInfo("UTC"))
        try:
            return value.astimezone(ZoneInfo(tzname)).strftime(fmt)
        except Exception:
            return value.strftime("%Y-%m-%d %H:%M")
    app.add_template_filter(tz, name="tz")

    login_manager.login_view = "auth.login"

    # --- Register Blueprints ---
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
    app.register_blueprint(academy_bp)
    app.register_blueprint(badges_bp)
    app.register_blueprint(storage_bp)

    # --- Request Hooks ---
    @app.before_request
    def track_presence():
        if current_user.is_authenticated:
            mark_user_active(current_user.id)

    # --- Root Route ---
    @app.route("/")
    def index():
        from .models import User
        try:
            if not User.query.first():
                return redirect(url_for("auth.setup"))
        except Exception:
            return redirect(url_for("auth.setup"))
        return render_template("index.html")

    # --- Context Processors ---
    @app.context_processor
    def inject_helpers():
        from .utils.storage import storage # Import here to avoid circulars

        def back_url(default_endpoint="dashboard.index", **kwargs):
            default = url_for(default_endpoint, **kwargs)
            ref = request.referrer or ""
            if not ref: return default
            try:
                ref_p, cur_p = urlparse(ref), urlparse(request.base_url)
                if (not ref_p.netloc or ref_p.netloc == cur_p.netloc) and ref_p.path != request.path:
                    return ref
            except Exception: pass
            return default
        return dict(tz_list=TZ_LIST, back_url=back_url, storage=storage)

    # --- Error Handlers ---
    @app.errorhandler(403)
    def err_403(e): return render_template("errors/403.html"), 403
    @app.errorhandler(404)
    def err_404(e): return render_template("errors/404.html"), 404
    @app.errorhandler(500)
    def err_500(e): return render_template("errors/500.html"), 500

    # --- Background Workers ---
    if app.config.get("REDIS_MODE", "local") != "none":
        start_background_workers(app)

    return app