from app.routes.auth import *

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'empid' not in session or 'user_id' not in session:
            flash("Your session has expired. Please log in again.", "warning")
            return redirect(url_for('auth.login'))
        # stash current user in g for this request
        g.current_user = {
            "empid":   session['empid'],
            "user_id": session['user_id'],
            "empname": session.get('empname')
        }
        return f(*args, **kwargs)
    return wrapper