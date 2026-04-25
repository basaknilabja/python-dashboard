from flask import Blueprint, render_template, request, redirect, url_for, session
from app.db import get_db_connection

org_bp = Blueprint("organisation", __name__)

@org_bp.route("/setup-company", methods=["GET", "POST"])
def setup_company():
    conn = get_db_connection()
    cur = conn.cursor()

    # Check if company already exists
    cur.execute("SELECT company_name FROM public.organisation_profile LIMIT 1")
    existing = cur.fetchone()

    if existing:
        session["company_name"] = existing[0]
        cur.close()
        conn.close()
        return redirect(url_for("auth.dashboard"))

    if request.method == "POST":
        company_name = request.form["company_name"].strip()

        cur.execute(
            "INSERT INTO public.organisation_profile (company_name) VALUES (%s)",
            (company_name,)
        )
        conn.commit()

        session["company_name"] = company_name

        cur.close()
        conn.close()

        return redirect(url_for("auth.dashboard"))

    cur.close()
    conn.close()
    return render_template("organisation/setup_company.html")