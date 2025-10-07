from flask import Blueprint


dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard", template_folder="../templates/dashboard")

from . import routes

