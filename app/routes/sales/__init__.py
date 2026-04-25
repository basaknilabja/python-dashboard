from flask import Blueprint

sales_bp = Blueprint('sales', __name__, url_prefix='/sales')

from . import presales, reports
