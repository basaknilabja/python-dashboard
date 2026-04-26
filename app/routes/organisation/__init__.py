from flask import Blueprint

org_bp = Blueprint('organisation', __name__, url_prefix='/organisation')

from . organisation_profile import *
