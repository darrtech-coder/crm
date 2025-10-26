import redis
from flask import Flask, render_template, redirect, url_for, request, current_app
from urllib.parse import urlparse
from datetime import datetime
from zoneinfo import ZoneInfo
from flask_login import current_user
from .extensions import flush_libview_worker

from sqlalchemy import inspect

from .extensions import db, login_manager, migrate, session, init_redis, mark_user_active, start_background_workers

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

    # Init core extensions that do not require settings from DB yet
    db.init_app(app)

    # --- [ROBUST CONFIGURATION LOGIC] ---
    # This block correctly determines the Redis URL and Mode whether running
    # locally, remotely, or during the initial setup on a remote server.
    with app.app_context():
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        
        # Start with the URL from the environment/config file. This is our best guess.
        initial_redis_url = app.config.get("REDIS_URL")

        if inspector.has_table("system_setting"):
            # --- Normal Operation: Read settings from the database ---
            try:
                from .utils.settings import get_setting
                app.config["TIMEZONE"] = get_setting("TIMEZONE", "UTC")
                app.config["REDIS_MODE"] = get_setting("REDIS_MODE", "local")
                # The DB setting for REDIS_URL overrides everything else.
                final_redis_url = get_setting("REDIS_URL", initial_redis_url)
                app.logger.info("✅ Loaded settings from database.")
            except Exception as e:
                app.logger.warning(f"⚠️ Failed to read system settings from DB, using fallbacks: {e}")
                app.config["TIMEZONE"] = "UTC"
                app.config["REDIS_MODE"] = "local" # Safe fallback
                final_redis_url = initial_redis_url
        else:
            # --- Setup Phase: No 'system_setting' table. Be smart about defaults. ---
            app.logger.warning("⚠️ No 'system_setting' table found — using smart defaults for setup phase.")
            app.config["TIMEZONE"] = "UTC"
            final_redis_url = initial_redis_url
            
            # THE CRITICAL FIX IS HERE:
            # If the provided Redis URL is not localhost, assume we are in a production/remote setup.
            if initial_redis_url and "localhost" not in initial_redis_url and "127.0.0.1" not in initial_redis_url:
                app.config["REDIS_MODE"] = "remote"
                app.logger.info("💡 Detected remote Redis URL during setup. Setting REDIS_MODE to 'remote'.")
            else:
                app.config["REDIS_MODE"] = "local"

        # Now, configure the session type and create the connection object based on the final decided mode
        if app.config["REDIS_MODE"] == "none":
            app.config["SESSION_TYPE"] = "filesystem"
            app.logger.info("🔧 Session backend configured to: filesystem.")
        else:
            app.config["SESSION_TYPE"] = "redis"
            # And critically, create the connection object for Flask-Session
            if final_redis_url:
                app.config["SESSION_REDIS"] = redis.from_url(final_redis_url)
                # Hide password in log for security
                safe_url = final_redis_url.split('@')[-1]
                app.logger.info(f"✅ Session backend configured to use Redis at: {safe_url}")
            else:
                app.logger.error("❌ Redis mode is enabled, but no REDIS_URL is available. Sessions may fail.")
                app.config["SESSION_TYPE"] = "filesystem" # Degrade gracefully


    # --- [END ROBUST CONFIGURATION LOGIC] ---


    # Finish extension initialization
    migrate.init_app(app, db)
    login_manager.init_app(app)
    # session.init_app will now find the correctly configured SESSION_REDIS object
    session.init_app(app) 
    init_redis(app)

    # Robust Jinja filter to render datetimes in the user's timezone (or global fallback)
    def tz(value, fmt="%Y-%m-%d %H:%M %Z"):
        """
        Render datetimes in the user's timezone (or global TIMEZONE).
        - Accepts datetime objects or strings.
        - Naive datetimes are treated as UTC (DB convention).
        - Strings are parsed as UTC if possible; if parsing fails, returned unchanged.
        """
        if value is None:
            return ""

        # Determine target timezone: per-user > global fallback
        tzname = (
            current_user.timezone
            if getattr(current_user, "is_authenticated", False) and getattr(current_user, "timezone", None)
            else app.config.get("TIMEZONE", "UTC")
        )

        # If a string, try to parse
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return ""
            iso = s
            # Normalize to ISO-8601 so fromisoformat can parse common inputs
            if iso.endswith("Z"):
                iso = iso[:-1] + "+00:00"
            if "T" not in iso and " " in iso:
                iso = iso.replace(" ", "T")

            dt = None
            try:
                dt = datetime.fromisoformat(iso)
            except Exception:
                # Try a few common patterns
                for f in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(s, f)
                        break
                    except Exception:
                        dt = None
            if dt is None:
                return s  # couldn't parse; leave as-is
        elif isinstance(value, datetime):
            dt = value
        else:
            # Some other type; show something sensible
            return str(value)

        # Treat naive as UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))

        # Convert and format
        try:
            return dt.astimezone(ZoneInfo(tzname)).strftime(fmt)
        except Exception:
            # Fallback if timezone invalid on host
            try:
                return dt.astimezone(ZoneInfo("UTC")).strftime(fmt)
            except Exception:
                try:
                    return dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    return str(value)

    # Register the filter ONCE, after app is created
    app.add_template_filter(tz, name="tz")

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
    app.register_blueprint(academy_bp)
    app.register_blueprint(badges_bp)

    # Track presence before each request
    @app.before_request
    def track_presence():
        if current_user.is_authenticated:
            mark_user_active(current_user.id)

    def _hyphen_alias_path(path: str) -> str:
        """Replace underscores with hyphens only in static segments (not in <variables>)."""
        parts = path.split("/")
        out = []
        for seg in parts:
            if not seg:
                out.append(seg)
                continue
            if seg.startswith("<") and seg.endswith(">"):
                out.append(seg)  # variable segment – keep as-is
            else:
                out.append(seg.replace("_", "-"))
        return "/".join(out)


    def register_hyphen_aliases(app: Flask):
        """
        Register hyphen-friendly aliases for routes that contain underscores.
        Keeps the original endpoints; adds a second rule pointing to the same view.
        """
        # Snapshot rules before we add any, so we don't iterate new ones
        rules_snapshot = list(app.url_map.iter_rules())

        # Build a queue of aliases to add (avoid modifying the map while iterating)
        to_add = []

        # Helper to check if a rule path already exists
        existing_paths = {r.rule for r in rules_snapshot}

        for rule in rules_snapshot:
            # Skip static endpoints
            if rule.endpoint == "static" or rule.endpoint.endswith(".static"):
                continue

            # Only consider rules with underscores in the static path
            if "_" not in rule.rule:
                continue

            hyphen_rule = _hyphen_alias_path(rule.rule)
            if hyphen_rule == rule.rule:
                continue
            if hyphen_rule in existing_paths:
                continue

            # Clone allowed methods, skip implicit ones
            methods = [m for m in (rule.methods or []) if m not in ("HEAD", "OPTIONS")]
            if not methods:
                continue

            # Resolve the view function
            view_func = app.view_functions.get(rule.endpoint)
            if not view_func:
                continue

            # Make a unique alias endpoint name
            base_alias = f"{rule.endpoint}__dash"
            alias_endpoint = base_alias
            i = 2
            while alias_endpoint in app.view_functions:
                alias_endpoint = f"{base_alias}{i}"
                i += 1

            to_add.append({
                "rule": hyphen_rule,
                "endpoint": alias_endpoint,
                "view_func": view_func,
                "methods": methods,
                "defaults": rule.defaults or {},
            })

        # Now add aliases
        for item in to_add:
            try:
                app.add_url_rule(
                    item["rule"],
                    endpoint=item["endpoint"],
                    view_func=item["view_func"],
                    methods=item["methods"],
                    defaults=item["defaults"],
                    provide_automatic_options=False,
                )
                existing_paths.add(item["rule"])
                app.logger.debug(f"Hyphen alias registered: {item['rule']} -> {item['endpoint']}")
            except Exception as e:
                # Non-fatal: skip this alias if anything conflicts
                app.logger.debug(f"Hyphen alias skipped for {item['rule']}: {e}")

    # Call it after all blueprints are registered
    register_hyphen_aliases(app)


    @app.before_request
    def canonical_hyphen_redirect():
        # Only safe to redirect GET/HEAD
        if request.method not in ("GET", "HEAD"):
            return

        rule = request.url_rule
        # If no rule matched (404) or no underscores in the rule pattern, skip
        if not rule or "_" not in rule.rule:
            return

        # Build canonical path based on the rule pattern and current args
        parts = []
        pattern_segments = [seg for seg in rule.rule.split("/") if seg != ""]
        values = request.view_args or {}

        for seg in pattern_segments:
            if seg.startswith("<") and seg.endswith(">"):
                # Variable segment like <int:id> or <path:filename>
                var = seg[1:-1]
                name = var.split(":", 1)[-1]  # after converter if present
                parts.append(str(values.get(name, "")))
            else:
                parts.append(seg.replace("_", "-"))

        canonical_path = "/" + "/".join(parts)
        # Respect trailing slash if rule requires it
        if rule.rule.endswith("/") and not canonical_path.endswith("/"):
            canonical_path += "/"

        # No-op if already canonical
        if canonical_path == request.path:
            return

        # Preserve the query string
        qs = request.query_string.decode("utf-8") if request.query_string else ""
        location = canonical_path + (("?" + qs) if qs else "")

        return redirect(location, code=301)




    # Root route
    @app.route("/")
    def index():
        # Import here to avoid circular imports at module load
        from .models import User
        if not User.query.first():
            return redirect(url_for("auth.setup"))
        return render_template("index.html")

    # Helpers available in all templates
    @app.context_processor
    def inject_helpers():
        def back_url(default_endpoint="dashboard.index", **kwargs):
            default = url_for(default_endpoint, **kwargs)
            ref = request.referrer or ""
            if not ref:
                return default
            try:
                ref_p = urlparse(ref)
                cur_p = urlparse(request.base_url)
                # Accept same-origin only; also avoid loops (same path)
                if (not ref_p.netloc or ref_p.netloc == cur_p.netloc) and ref_p.path != request.path:
                    return ref
            except Exception:
                pass
            return default
        return dict(tz_list=TZ_LIST, back_url=back_url)

    # Custom error pages
    @app.errorhandler(403)
    def err_403(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def err_404(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def err_500(e):
        return render_template("errors/500.html"), 500


    @app.context_processor
    def pagination_helpers():
        from flask import request, url_for
        def page_url(page, per_page=None, extra=None):
            args = request.args.to_dict(flat=True)
            if extra:
                args.update(extra)
            args["page"] = page
            if per_page is not None:
                args["per_page"] = per_page
            return url_for(request.endpoint, **(request.view_args or {}), **args)
        return dict(page_url=page_url)



    # Ensure role-based chatrooms exist (safe-guarded)
    with app.app_context():
        try:
            if inspect(db.engine).has_table("chat_room"):
                from .messaging.routes import (
                    ensure_manager_group, ensure_manager_admin_group, ensure_admin_group
                )
                ensure_manager_group()
                ensure_manager_admin_group()
                ensure_admin_group()
            else:
                app.logger.warning("⚠️ 'chat_room' table not found — run 'flask db upgrade' first!")
        except Exception as e:
            app.logger.warning(f"⚠️ Skipped auto-creating role-based chatrooms: {e}")

    # Start background workers if Redis is enabled
    if app.config.get("REDIS_MODE", "local") != "none":
        start_background_workers(app)

    return app