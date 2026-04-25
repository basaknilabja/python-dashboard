from flask import Blueprint

inventory_bp = Blueprint(
    "inventory",
    __name__,
    url_prefix="/inventory"
    
)

# IMPORTANT: import routes AFTER blueprint creation
from . import inventory
