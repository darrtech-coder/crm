from flask import Blueprint

coaching_bp = Blueprint(
    "coaching",
    __name__,
    url_prefix="/coaching",
    template_folder="../templates/coaching"
)

from . import routes
