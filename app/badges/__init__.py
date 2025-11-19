from flask import Blueprint

badges_bp = Blueprint(
    "badges", 
    __name__, 
    url_prefix="/badges", 
    template_folder="../templates/badges"
)

from . import routes