# app/routes/purchase/__init__.py
from flask import Blueprint

purchase_bp = Blueprint('purchase', __name__, url_prefix='/purchase') 


# Import all purchase-related route files here
from . import pending, indent, reports

from io import BytesIO
import time
from functools import wraps
from flask import Flask
from app.routes.auth import auth_bp
from app.routes.purchase import purchase_bp
# from app.routes.sales import sales_bp


