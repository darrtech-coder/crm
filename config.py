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

    # [NEW] Storage Configuration
    FS_BACKEND = os.environ.get("FS_BACKEND", "local")

    STORAGE_PREFIX = os.environ.get("STORAGE_PREFIX", "crm") # [NEW] Add a prefix

    # S3 Compatible Config
    S3_KEY = os.environ.get("S3_KEY", "")
    S3_SECRET = os.environ.get("S3_SECRET", "")
    S3_BUCKET = os.environ.get("S3_BUCKET", "")
    S3_REGION = os.environ.get("S3_REGION", "")
    S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "") # For non-AWS S3-compatible services
    # Google Cloud Storage Config
    GCS_KEYFILE_PATH = os.environ.get("GCS_KEYFILE_PATH", "") # Path to the JSON keyfile
    GCS_BUCKET = os.environ.get("GCS_BUCKET", "")

class DevConfig(Config):
    DEBUG = True

class ProdConfig(Config):
    DEBUG = False
