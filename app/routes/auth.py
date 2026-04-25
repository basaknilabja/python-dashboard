# app/routes/auth.py

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
    current_app,
    g,
    Response
)

from app.db import get_db_connection
from psycopg2.extras import RealDictCursor

from datetime import timedelta
import time
from functools import wraps

# --------------------------------------------------
# Blueprint
# --------------------------------------------------
auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# --------------------------------------------------
# Login Required Decorator
# --------------------------------------------------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "empid" not in session or "user_id" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("auth.login"))

        g.current_user = {
            "empid": session["empid"],
            "user_id": session["user_id"],
            "empname": session.get("empname", "")
        }
        return f(*args, **kwargs)
    return wrapper

# --------------------------------------------------
# AJAX: Check Password
# --------------------------------------------------
@auth_bp.route("/check_password", methods=["POST"])
def check_password():
    user_id = request.form.get("user_id")
    password = request.form.get("password")

    if not user_id or not password:
        return jsonify(status="error", message="Provide User ID and Password"), 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute(
        """
        SELECT empid, password
        FROM public.emp_master
        WHERE user_id = %s
        """,
        (user_id,)
    )

    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if user and user["password"] == password:
        return jsonify(
            status="success",
            message="Password matched. Press Sign In."
        )

    return jsonify(status="error", message="Invalid User ID or Password")

# --------------------------------------------------
# Login
# --------------------------------------------------
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user_id = (request.form.get("user_id") or "").strip()
        password = (request.form.get("password") or "").strip()

        if not user_id or not password:
            flash("Please enter both User ID and Password.", "warning")
            return redirect(url_for("auth.login"))

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            """
            SELECT empid, empname, user_id, password
            FROM public.emp_master
            WHERE user_id = %s
            LIMIT 1
            """,
            (user_id,)
        )

        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user and user["password"] == password:
            session.clear()

            session["empid"] = user["empid"]
            session["user_id"] = user["user_id"]
            session["empname"] = user["empname"]

            session.permanent = True
            current_app.permanent_session_lifetime = timedelta(minutes=60)

            now_ts = int(time.time())
            session["login_ts"] = now_ts
            session["last_seen_ts"] = now_ts

            flash(f"Welcome, {user['empname']}!", "success")
            return redirect(url_for("auth.dashboard"))

        flash("Invalid User ID or Password.", "danger")
        return redirect(url_for("auth.login"))

    return render_template("user/login.html")

# --------------------------------------------------
# Register (Admin Only)
# --------------------------------------------------
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if not session.get("is_admin_verified"):
        if request.method == "POST":
            admin_user = request.form.get("admin_user", "").strip()
            admin_pass = request.form.get("admin_pass", "").strip()

            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            cursor.execute(
                """
                SELECT password
                FROM public.emp_master
                WHERE LOWER(user_id) = 'admin'
                """
            )
            admin = cursor.fetchone()

            cursor.close()
            conn.close()

            if admin and admin_user.lower() == "admin" and admin["password"] == admin_pass:
                session["is_admin_verified"] = True
                flash("Admin verified. You may register a new user.", "success")
                return redirect(url_for("auth.register"))

            flash("Invalid admin credentials.", "danger")

        return render_template("user/register.html")

    if request.method == "POST":
        empid = request.form.get("empid")
        empname = request.form.get("empname")
        user_id = request.form.get("user_id")
        password = request.form.get("password")

        if not (empid and empname and user_id and password):
            flash("All fields are required.", "danger")
            return render_template("user/register.html")

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            """
            SELECT empid
            FROM public.emp_master
            WHERE empid = %s OR user_id = %s
            """,
            (empid, user_id)
        )

        if cursor.fetchone():
            flash("Employee already exists.", "danger")
            cursor.close()
            conn.close()
            return render_template("user/register.html")

        cursor.execute(
            """
            INSERT INTO public.emp_master (empid, empname, user_id, password)
            VALUES (%s, %s, %s, %s)
            """,
            (empid, empname, user_id, password)
        )

        conn.commit()
        cursor.close()
        conn.close()

        session.pop("is_admin_verified", None)
        flash("Registration successful. Please login.", "success")
        return redirect(url_for("auth.login"))

    return render_template("user/register.html")

# --------------------------------------------------
# User Photo  ✅ FIXED & REQUIRED
# --------------------------------------------------
# --------------------------------------------------
# User Photo (optional – safe fallback)
# --------------------------------------------------
@auth_bp.route("/photo/<empid>")
def user_photo(empid):
    """
    Returns employee photo if column exists,
    otherwise returns 404 safely.
    """

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT photo FROM public.emp_master WHERE empid = %s",
            (empid,)
        )
        row = cursor.fetchone()
    except Exception:
        row = None
    finally:
        cursor.close()
        conn.close()

    # If photo exists, return it
    if row and row[0]:
        return current_app.response_class(
            row[0],
            mimetype="image/jpeg"
        )

    # Otherwise return empty image (no crash)
    return ("", 404)

# --------------------------------------------------
# Dashboard
# --------------------------------------------------
@auth_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")

# --------------------------------------------------
# Users List
# --------------------------------------------------
@auth_bp.route("/users")
@login_required
def users_list():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute(
        """
        SELECT empid, empname, user_id
        FROM public.emp_master
        ORDER BY empname
        """
    )
    users = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("user/users.html", users=users)

# --------------------------------------------------
# Logout
# --------------------------------------------------
@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
