from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask import current_app
from flask_session import Session
import redis, os
import threading
from datetime import datetime # 
import psutil


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



import psutil

def start_resource_monitor(app, interval_seconds=60):
    """Background thread to periodically collect CPU, RAM, disk, storage usage,
    and send alerts when thresholds are exceeded."""

    from app.system_monitor.models import SystemMetric

    def worker():
        from app.utils.settings import get_setting, set_setting
        from app.notifications.utils import notify_roles

        with app.app_context():
            root_path = os.path.abspath(os.sep)
            while True:
                try:
                    # Sample system resources
                    cpu = psutil.cpu_percent(interval=None)

                    vm = psutil.virtual_memory()
                    mem_used_mb = vm.used / (1024 * 1024)
                    mem_percent = vm.percent

                    du = psutil.disk_usage(root_path)
                    disk_used_gb = du.used / (1024 * 1024 * 1024)
                    disk_percent = du.percent

                    # Current storage usage from our running total
                    total_bytes = int(get_setting("STORAGE_TOTAL_BYTES", "0") or 0)

                    # Insert metric row
                    m = SystemMetric(
                        cpu_percent=cpu,
                        mem_used_mb=mem_used_mb,
                        mem_percent=mem_percent,
                        disk_used_gb=disk_used_gb,
                        disk_percent=disk_percent,
                        storage_total_bytes=total_bytes,
                    )
                    db.session.add(m)
                    safe_commit()

                    # Threshold alerts
                    def get_threshold(key, default_cfg):
                        return int(
                            get_setting(key, str(app.config.get(default_cfg, 0))) or 0
                        )

                    cpu_thresh = get_threshold("CPU_ALERT_PERCENT", "CPU_ALERT_PERCENT")
                    mem_thresh = get_threshold("MEM_ALERT_PERCENT", "MEM_ALERT_PERCENT")
                    disk_thresh = get_threshold("DISK_ALERT_PERCENT", "DISK_ALERT_PERCENT")

                    def maybe_alert(name, value, threshold):
                        if not threshold or value < threshold:
                            return
                        key_last = f"{name}_ALERT_LAST_SENT"
                        last_iso = get_setting(key_last, "")
                        now = datetime.utcnow()
                        if last_iso:
                            try:
                                last_dt = datetime.fromisoformat(last_iso)
                                # Only alert at most once per hour per metric
                                if now - last_dt < timedelta(hours=1):
                                    return
                            except Exception:
                                pass
                        notify_roles(
                            ("ADMIN", "SUPER_ADMIN"),
                            f"⚠️ {name} high: {value:.1f}% (threshold {threshold}%)",
                        )
                        set_setting(key_last, now.isoformat())

                    maybe_alert("CPU", cpu, cpu_thresh)
                    maybe_alert("MEMORY", mem_percent, mem_thresh)
                    maybe_alert("DISK", disk_percent, disk_thresh)

                except Exception as e:
                    db.session.rollback()
                    app.logger.warning(f"resource monitor error: {e}")
                finally:
                    time.sleep(interval_seconds)

    # Avoid duplicate threads on reload
    if not any(t.name == "resource_monitor" for t in threading.enumerate()):
        t = threading.Thread(target=worker, name="resource_monitor", daemon=True)
        t.start()
        app.logger.info("✅ Started resource_monitor background worker.")
