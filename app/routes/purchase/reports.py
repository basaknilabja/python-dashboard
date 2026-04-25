from flask import render_template
# from app.utils import login_required
from app.decorators import login_required
from . import purchase_bp   # ✅ this imports purchase_bp from __init__.py
from datetime import datetime, timedelta


# Report (dummy page for now)
@purchase_bp.route("/report")
@login_required
def purchase_report():
    return render_template("purchase/purchase_report.html")


# Settings (dummy page for now)
@purchase_bp.route("/settings")
@login_required
def purchase_settings():
    return render_template("purchase/purchase_settings.html")