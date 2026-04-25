from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from app.db import get_db_connection
from psycopg2.extras import RealDictCursor

tasks_bp = Blueprint("tasks", __name__, url_prefix="/tasks")

@tasks_bp.route("/")
def view_tasks():
    if "user" not in session:
        return redirect(url_for("auth.login"))

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM tasks")
    tasks = cursor.fetchall()
    conn.close()

    return render_template("tasks.html", tasks=tasks)

@tasks_bp.route("/add", methods=["POST"])
def add_task():
    if "user" not in session:
        return redirect(url_for("auth.login"))

    task = request.form["task"]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (task) VALUES (%s)", (task,))
    conn.commit()
    conn.close()

    flash("Task added successfully", "success")
    return redirect(url_for("tasks.view_tasks"))
