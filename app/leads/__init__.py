from flask import Blueprint

leads_bp = Blueprint(
    "leads",
    __name__,
    url_prefix="/leads",
    template_folder="../templates/leads"
)

from . import routes
