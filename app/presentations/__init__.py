from flask import Blueprint

presentations_bp = Blueprint(
    "presentations",
    __name__,
    url_prefix="/presentations",
    template_folder="../templates/presentations"
)

from . import routes
