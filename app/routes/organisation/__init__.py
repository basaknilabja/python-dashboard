from flask import Blueprint

org_bp = Blueprint('organisation_profile', __name__, url_prefix='/organisation')

from . organisation_profile import *
