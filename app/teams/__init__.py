from flask import Blueprint

teams_bp = Blueprint(
    "teams",
    __name__,
    url_prefix="/teams",
    template_folder="../templates/teams"
)

from . import routes
