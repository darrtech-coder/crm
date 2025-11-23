from flask import current_app, url_for
from fs.base import FS
from fs_s3fs import S3FS
from fs_gcsfs import GCSFS
from fs.osfs import OSFS
from fs.subfs import SubFS
from werkzeug.utils import secure_filename
import uuid
import os
from io import BytesIO

class StorageManager:
    def __init__(self, app=None):
        self._fs = None
        self._cdn_url = ''
        self._prefix = ''
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        self.app = app
        app.extensions['storage_manager'] = self


    def _record_storage_delta(self, delta_bytes: int):
        """Adjust global storage usage counter by delta_bytes (can be negative).
           Also sends alerts if we cross configured storage thresholds.
        """
        try:
            from ..utils.settings import get_setting, set_setting
            from ..notifications.utils import notify_roles

            current = int(get_setting("STORAGE_TOTAL_BYTES", "0") or 0)
            new_total = max(0, current + int(delta_bytes))
            set_setting("STORAGE_TOTAL_BYTES", new_total)

            # Check against storage limit, if configured
            limit_bytes = int(get_setting("STORAGE_LIMIT_BYTES", "0") or 0)
            if limit_bytes > 0 and new_total > 0:
                usage_ratio = new_total / limit_bytes

                # Determine alert level (simple 80/90/100% thresholds)
                last_level = get_setting("STORAGE_LIMIT_ALERT_LEVEL", "none")
                level = "none"
                if usage_ratio >= 1.0:
                    level = "full"
                elif usage_ratio >= 0.9:
                    level = "90"
                elif usage_ratio >= 0.8:
                    level = "80"

                if level != "none" and level != last_level:
                    gb_used = new_total / (1024**3)
                    gb_limit = limit_bytes / (1024**3)
                    notify_roles(
                        ("ADMIN", "SUPER_ADMIN"),
                        f"⚠️ Storage usage {gb_used:.1f}GB / {gb_limit:.1f}GB ({usage_ratio:.0%})."
                    )
                    set_setting("STORAGE_LIMIT_ALERT_LEVEL", level)
        except Exception as e:
            current_app.logger.warning(f"Failed updating storage total: {e}")

    @property
    def fs(self):
        if self._fs is None:
            with self.app.app_context():
                from ..utils.settings import get_setting
                backend = get_setting("FS_BACKEND", "local")
                
                self._prefix = get_setting("STORAGE_PREFIX", "").strip('/')

                base_fs = None
                
                if backend == "s3":
                    try:
                        base_fs = S3FS(
                            bucket_name=get_setting("S3_BUCKET"),
                            aws_access_key_id=get_setting("S3_KEY"),
                            aws_secret_access_key=get_setting("S3_SECRET"),
                            region=get_setting("S3_REGION"),
                            endpoint_url=get_setting("S3_ENDPOINT_URL") or None,
                        )
                        self._cdn_url = get_setting("S3_CDN_URL", "").rstrip('/')
                        current_app.logger.info("✅ Lazily initialized S3-compatible storage.")
                    except Exception as e:
                        current_app.logger.error(f"❌ S3 storage lazy init failed: {e}. Falling back to local.")
                        self._init_local_fallback()
                elif backend == "gcs":
                    try:
                        base_fs = GCSFS(
                            bucket_name=get_setting("GCS_BUCKET"),
                            key_file=get_setting("GCS_KEYFILE_PATH"),
                        )
                        self._cdn_url = get_setting("GCS_CDN_URL", "").rstrip('/')
                        current_app.logger.info("✅ Lazily initialized Google Cloud Storage.")
                    except Exception as e:
                        current_app.logger.error(f"❌ GCS storage lazy init failed: {e}. Falling back to local.")
                        self._init_local_fallback()
                else:
                    self._init_local_fallback()

                if base_fs and self._prefix:
                    base_fs.makedirs(self._prefix, recreate=True)
                    self._fs = SubFS(base_fs, self._prefix)
                elif base_fs:
                    self._fs = base_fs
        return self._fs

    @property
    def cdn_url(self):
        if self._fs is None:
            _ = self.fs
        return self._cdn_url

    def _init_local_fallback(self):
        root_path = os.path.join(current_app.instance_path, 'uploads')
        os.makedirs(root_path, exist_ok=True)
        self._fs = OSFS(root_path)
        self._cdn_url = ''
        self._prefix = ''
        current_app.logger.info("✅ Lazily initialized local file storage.")

    def get_fs_from_config(self, config):
        backend = config.get("fs_backend")
        prefix = config.get("storage_prefix", "").strip('/')
        base_fs = None

        if backend == "s3":
            base_fs = S3FS(bucket_name=config.get("s3_bucket"), aws_access_key_id=config.get("s3_key"), aws_secret_access_key=config.get("s3_secret"), region=config.get("s3_region"), endpoint_url=config.get("s3_endpoint_url") or None)
        elif backend == "gcs":
            if not config.get("gcs_keyfile_path"): raise ValueError("GCS keyfile path is missing for test.")
            base_fs = GCSFS(bucket_name=config.get("gcs_bucket"), key_file=config.get("gcs_keyfile_path"))
        else:
            root_path = os.path.join(current_app.instance_path, 'uploads')
            return OSFS(root_path, create=True)
        
        if base_fs and prefix:
            base_fs.makedirs(prefix, recreate=True)
            return SubFS(base_fs, prefix)
        return base_fs

    def save(self, file_storage, subfolder, filename=None):
        if not filename:
            ext = os.path.splitext(file_storage.filename)[1]
            secure_base = secure_filename(os.path.splitext(file_storage.filename)[0])
            filename = f"{secure_base}_{uuid.uuid4().hex[:8]}{ext}"
        
        full_path = f"{subfolder}/{filename}"
        self.fs.makedirs(subfolder, recreate=True)

        # Measure size BEFORE writing
        size_bytes = 0
        try:
            pos = file_storage.tell()
            file_storage.seek(0, os.SEEK_END)
            size_bytes = file_storage.tell()
            file_storage.seek(0, os.SEEK_SET)
        except Exception:
            data = file_storage.read()
            size_bytes = len(data)
            file_storage = BytesIO(data)


        file_storage.seek(0)
        self.fs.writebytes(full_path, file_storage.read())

        # Record storage delta
        if size_bytes:
            self._record_storage_delta(size_bytes)

        return full_path

    def get_url(self, path):
        """
        [REVISED] Intelligently generates a public URL for a file path.
        - If the path is already a full URL, it returns it as is.
        - Otherwise, it constructs the correct URL based on the current storage config.
        """
        if not path:
            return ""

        # 1. If path is already a full URL, return it directly.
        if path.startswith(('http://', 'https://')):
            return path
        
        # 2. If not a full URL, generate one based on the current configuration.
        _ = self.fs # Ensure fs is initialized to get cdn_url and prefix

        if self.cdn_url:
            # For remote storage, construct the full URL
            full_path = f"{self._prefix}/{path.lstrip('/')}" if self._prefix else path.lstrip('/')
            return f"{self.cdn_url}/crm/{full_path.lstrip('/')}"
        else:
            # For local storage, generate a URL to our serving endpoint
            return url_for('storage.serve_file', path=path)

    def delete(self, path):
        if not self.fs.exists(path):
            return
        size_bytes = 0
        try:
            info = self.fs.getinfo(path, namespaces=["details"])
            # fs.info.size works across OSFS, S3FS, GCSFS
            size_bytes = getattr(info, "size", None) or info.raw.get("size", 0)
        except Exception:
            size_bytes = 0

        self.fs.remove(path)

        if size_bytes:
            self._record_storage_delta(-size_bytes)

storage = StorageManager()