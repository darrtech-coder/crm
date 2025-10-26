from flask import Blueprint

library_bp = Blueprint(
    "library",
    __name__,
    url_prefix="/library",
    template_folder="../templates/library"
)

from . import routes
