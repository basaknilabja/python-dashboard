from flask import Blueprint

pms_bp = Blueprint('pms', __name__, url_prefix='/pms')

from . import pms_update
