from flask import Blueprint

messaging_bp = Blueprint(
    "messaging",
    __name__,
    url_prefix="/messaging",
    template_folder="../templates/messaging"
)

from . import routes
