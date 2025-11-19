from flask import Blueprint, current_app, send_from_directory, abort
from .utils.storage import storage
import os

storage_bp = Blueprint('storage', __name__)

@storage_bp.route('/uploads/<path:path>')
def serve_file(path):
    """
    Endpoint to serve files from the local 'uploads' directory.
    This is only used when FS_BACKEND is 'local'.
    """
    # [FIX] Accessing cdn_url triggers the lazy loader, ensuring fs is initialized.
    if storage.cdn_url:
        return "File serving is handled by a remote CDN.", 404
        
    # The OSFS instance is now rooted at instance_path/uploads
    uploads_dir = os.path.join(current_app.instance_path, 'uploads')
    
    if not storage.fs.exists(path):
        return abort(404)

    return send_from_directory(uploads_dir, path)