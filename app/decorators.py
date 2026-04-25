# app/decorators.py
from functools import wraps
from flask import session, redirect, url_for, flash, g

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'empid' not in session or 'user_id' not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("auth.login"))
        g.current_user = {
            "empid": session["empid"],
            "user_id": session["user_id"],
            "empname": session.get("empname", "")
        }
        return f(*args, **kwargs)
    return wrapper
