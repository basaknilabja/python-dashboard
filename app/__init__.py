from flask import Flask, session, redirect, url_for, flash, request
import time
from datetime import timedelta

# from flask import Flask
# from app.routes.purchase import purchase_bp
# from app.routes.sales import sales_bp
# from app.routes.salesdeal import salesdeal_bp

# from app.routes.auth import auth_bp

IDLE_TIMEOUT_SEC = 59 * 60       # 59 minutes idle
ABS_TIMEOUT_SEC  = 59 * 60       # 59 minutes absolute

def create_app():
    app = Flask(__name__)
    app.secret_key = "S5&0-3#06FJ3*$.1T2&82"  # TODO: replace with a strong random value

    # Cookie lifetime (browser-side). We’ll also enforce our own timers.
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=59)
    # We’ll manage idle explicitly; this flag doesn’t matter for our custom checks:
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True

    # Register Blueprints
    from app.routes.auth import auth_bp
    from app.routes.purchase import purchase_bp
    from app.routes.sales import sales_bp
    from app.routes.salesdeal import salesdeal_bp
    from app.routes.pms import pms_bp
    from app.routes.inventory import inventory_bp
    from app.routes.organisation import org_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(purchase_bp)   # ✅ make sure this line exists
    app.register_blueprint(sales_bp)
    app.register_blueprint(salesdeal_bp)
    app.register_blueprint(pms_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(org_bp)

    # Home route → redirects to login page
    @app.route("/")
    def home():
        return redirect(url_for("auth.login"))

    @app.before_request
    def enforce_timeouts():
        """Enforce idle + absolute session timeouts."""
        # Endpoints that must remain accessible without session
        allow = {"auth.login", "auth.register", "static"}
        if request.endpoint in allow:
            return

        # If not logged in, nothing to check
        if "empid" not in session or "user_id" not in session:
            return

        now = int(time.time())

        # --- Absolute timeout ---
        login_ts = session.get("login_ts")
        if isinstance(login_ts, int) and now - login_ts > ABS_TIMEOUT_SEC:
            session.clear()
            flash("⏰ Session expired. Please log in again.", "warning")
            return redirect(url_for("auth.login"))

        # --- Idle timeout ---
        last_seen_ts = session.get("last_seen_ts")
        if isinstance(last_seen_ts, int) and now - last_seen_ts > IDLE_TIMEOUT_SEC:
            session.clear()
            flash("⏳ Session timed out due to inactivity. Please log in again.", "warning")
            return redirect(url_for("auth.login"))

        # Update idle timer on each authenticated request
        session["last_seen_ts"] = now

    return app