from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask import current_app
from flask_session import Session
import redis

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
session = Session()

def init_redis(app):
    app.redis = redis.from_url(app.config["REDIS_URL"])
    return app.redis

import time

def mark_user_active(user_id):
    now = int(time.time())
    r = getattr(current_app, "redis", None)
    if r:
        r.set(f"user:{user_id}:online", now, ex=60)   # online marker
        r.set(f"user:{user_id}:last_seen", now)       # persist last seen permanently