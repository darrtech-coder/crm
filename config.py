import os

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "changeme")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///app.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_TYPE = "redis"
    SESSION_PERMANENT = False
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Security/Lockout settings
    LOG_UNAUTHORIZED = True
    MAX_FAILED_LOGINS = 5
    LOCKOUT_MINUTES = 15

    # Registration control
    ALLOW_REGISTRATION = True

    PASSWORD_MIN_LENGTH = 8
    PASSWORD_REQUIRE_UPPER = False
    PASSWORD_REQUIRE_NUMBER = False
    PASSWORD_REQUIRE_SYMBOL = False
    LIBRARY_VIEW_DEBOUNCE_MINUTES = 5

class DevConfig(Config):
    DEBUG = True

class ProdConfig(Config):
    DEBUG = False
