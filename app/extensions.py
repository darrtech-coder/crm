from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask import current_app
from flask_session import Session
import redis
import threading
from datetime import datetime # 


from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
session = Session()

def init_redis(app):
    mode = app.config.get("REDIS_MODE", "local")
    url  = app.config.get("REDIS_URL")
    if mode == "none" or not url:
        app.redis = None
        return None
    try:
        app.redis = redis.from_url(url)
    except Exception as e:
        app.logger.warning(f"Redis init failed: {e}")
        app.redis = None
    return app.redis

from sqlalchemy.exc import OperationalError
import time

def mark_user_active(user_id):
    now = int(time.time())
    r = getattr(current_app, "redis", None)
    if r:
        r.set(f"user:{user_id}:online", now, ex=60)   # online marker
        r.set(f"user:{user_id}:last_seen", now)       # persist last seen permanently


def safe_commit(max_retries=3, backoff=0.1):
    """Commit with short retries; rollback on lock errors (helps SQLite)."""
    for attempt in range(max_retries):
        try:
            db.session.commit()
            return True
        except OperationalError:
            db.session.rollback()
            time.sleep(backoff * (attempt + 1))
    return False


# Apply SQLite pragmas on each new connection to reduce locking
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    try:
        from sqlite3 import Connection as SQLite3Connection
        if isinstance(dbapi_connection, SQLite3Connection):
            cursor = dbapi_connection.cursor()
            # WAL allows concurrent readers; busy_timeout makes writes wait instead of failing fast
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA busy_timeout=5000;")  # ms
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.close()
    except Exception:
        # Non‑SQLite engines or other environments will skip silently
        pass

# --- [NEW] Wrap thread startup in a function ---
def start_background_workers(app):
    """Initializes and starts background threads within the app context."""

    def flush_libview_worker():
        # ... (keep existing worker code, just indented)
        from app.library.models import LibraryView
        with app.app_context():
            r = app.redis
            if not r: return # Exit thread if redis is disabled
            while True:
                try:
                    item = r.blpop("queue:libview", timeout=5)
                    if not item: continue
                    _, raw = item
                    data = json.loads(raw)
                    db.session.add(LibraryView(
                        user_id=data["user_id"],
                        item_id=data["item_id"],
                        viewed_at=datetime.utcfromtimestamp(data["ts"])
                    ))
                    if not safe_commit(max_retries=3, backoff=0.2):
                        r.rpush("queue:libview", raw)
                        time.sleep(0.5)
                except Exception as e:
                    db.session.rollback()
                    app.logger.warning(f"libview flush error: {e}")
                    time.sleep(0.5)


def flush_libview_worker():
    # drains queue:libview and writes to LibraryView with retries
    from app.library.models import LibraryView
    with app.app_context():
        r = app.redis
        while True:
            try:
                item = r.blpop("queue:libview", timeout=5)
                if not item:
                    continue
                _, raw = item
                data = json.loads(raw)
                db.session.add(LibraryView(
                    user_id=data["user_id"],
                    item_id=data["item_id"],
                    viewed_at=datetime.utcfromtimestamp(data["ts"])
                ))
                if not safe_commit(max_retries=3, backoff=0.2):
                    # push back and sleep if DB busy
                    r.rpush("queue:libview", raw)
                    time.sleep(0.5)
            except Exception as e:
                db.session.rollback()
                app.logger.warning(f"libview flush error: {e}")
                time.sleep(0.5)

    def flush_libprog_worker():
        # periodically scan progress hashes and upsert to DB
        from app.library.models import LibraryProgress
        with app.app_context():
            r = app.redis
            while True:
                try:
                    # scan keys to avoid blocking (small set in dev)
                    for key in r.scan_iter(match="libprog:*"):
                        try:
                            parts = key.decode().split(":")
                            _, _, user_id, item_id = parts  # libprog:{user}:{item}
                            h = r.hgetall(key)
                            if not h:
                                continue
                            pos = int(h.get(b"position", b"0"))
                            dur = int(h.get(b"duration", b"0"))
                            # upsert
                            rec = (LibraryProgress.query
                                    .filter_by(user_id=int(user_id), item_id=int(item_id))
                                    .first())
                            if not rec:
                                rec = LibraryProgress(user_id=int(user_id), item_id=int(item_id),
                                                      position=pos, duration=dur)
                                db.session.add(rec)
                            else:
                                rec.position = pos
                                if dur:  rec.duration = dur
                            if not safe_commit(max_retries=3, backoff=0.2):
                                db.session.rollback()
                                # leave the hash for next cycle
                        except Exception as e:
                            db.session.rollback()
                            app.logger.debug(f"libprog flush error on {key}: {e}")
                    time.sleep(5)  # flush interval
                except Exception as e:
                    db.session.rollback()
                    app.logger.warning(f"libprog worker loop error: {e}")
                    time.sleep(1)

    # Check if threads are already running to avoid duplicates during hot-reloads
    if not any(t.name == "flush_libview" for t in threading.enumerate()):
        t1 = threading.Thread(target=flush_libview_worker, name="flush_libview", daemon=True)
        t1.start()
        app.logger.info("✅ Started flush_libview background worker.")

    if not any(t.name == "flush_libprog" for t in threading.enumerate()):
        t2 = threading.Thread(target=flush_libprog_worker, name="flush_libprog", daemon=True)
        t2.start()
        app.logger.info("✅ Started flush_libprog background worker.")
