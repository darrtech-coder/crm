from flask import Blueprint

tests_bp = Blueprint(
    "tests",
    __name__,
    url_prefix="/tests",
    template_folder="../templates/tests"
)

from . import routes  # registers the routes