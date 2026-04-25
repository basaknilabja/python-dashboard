from flask import Blueprint

salesdeal_bp = Blueprint('salesdeal', __name__, url_prefix='/salesdeal')

from . import salesdeal

