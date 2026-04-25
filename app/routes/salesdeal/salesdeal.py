# app/routes/sales/sales_fms.py

from flask import (
    Blueprint, render_template, request, jsonify, g, redirect, url_for, flash,send_file
)
from datetime import datetime, timedelta
from app.db import get_db_connection
from app.decorators import login_required
from psycopg2.extras import RealDictCursor

# salesdeal_bp = Blueprint("sales_fms", __name__)
from . import salesdeal_bp



@salesdeal_bp.route("/fms/update", methods=["GET", "POST"])
@login_required
def salesdeal_fms_update():

    empid = g.current_user.get("empid")
    empname = g.current_user.get("empname")

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # ========================== GET ==========================
    if request.method == "GET":
        cur.execute("""
            SELECT id, unique_id, step_name, last_status, planned, project
            FROM public.doer_pending
            WHERE actual IS NULL
              AND doer_empid = %s
              AND doer = %s
              AND tools_name = 'Sales FMS'
            ORDER BY planned
        """, (empid, empname))

        pending_list = cur.fetchall()
        cur.close(); conn.close()

        return render_template(
            "salesdeal/salesdeal_fms_update.html",
            pending_list=pending_list
        )

    # ========================== POST ==========================
    form = request.form
    dp_id = form.get("doer_pending_id")
    leadsid = form.get("unique_id")
    project = form.get("project")
    status = (form.get("status") or "").lower()
    remarks = form.get("remarks")
    tat = form.get("tat", type=int)

    now = datetime.now()

    try:
        # --------- Close current doer_pending ---------
        cur.execute("""
            UPDATE public.doer_pending
            SET actual=%s,
                status=%s,
                remarks=%s,
                last_interaction=%s
            WHERE id=%s
        """, (now, status, remarks, now.date(), dp_id))

        # ===================== BOOKING =====================
        if status == "booking":

            car_parking = (form.get("car_parking") or "").strip()
            if car_parking not in ("Covered", "Open", "No"):
                car_parking = "No"

            flat_no = form.get("flat_no")

            # Insert into public.sd_customer
            cur.execute("""
                INSERT INTO public.sd_customer
                (leadsid, project, customername, phoneno, wtsp_no, booking,
                 email, flat_no, rate, flat_price, car_parking,
                 initial_amt)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                leadsid,
                project,
                form.get("customername"),
                form.get("phoneno"),
                form.get("wtsp_no"),
                form.get("booking"),
                form.get("email"),
                flat_no,
                form.get("rate"),
                form.get("flat_price"),
                car_parking,
                form.get("initial_amt")
            ))

            # Mark flat as BOOKED
            cur.execute("""
                UPDATE public.sales_inventory
                SET status='BOOKED'
                WHERE flat_no=%s AND project=%s
            """, (flat_no, project))

            # Insert next workflow
            cur.execute("""
                INSERT INTO public.doer_pending
                (unique_id, step_name, planned, tools_name,
                 doer, doer_empid, last_status, last_interaction, project)
                VALUES (%s,'Customer Onboarding',%s,'Customer FMS',
                        %s,%s,%s,%s,%s)
            """, (
                leadsid,
                now,
                empname,
                empid,
                status,
                now.date(),
                project
            ))

        # ===================== FOLLOW UPS =====================
        elif status in ("revisit", "negotiation", "indecisive", "ongoing call") and tat:
            planned_dt = now + timedelta(days=tat)

            cur.execute("""
                INSERT INTO public.doer_pending
                (unique_id, step_name, planned, tools_name,
                 doer, doer_empid, last_status, last_interaction, project)
                VALUES (%s,%s,%s,'Sales FMS',%s,%s,%s,%s,%s)
            """, (
                leadsid,
                status.title(),
                planned_dt,
                empname,
                empid,
                status,
                now.date(),
                project
            ))

        conn.commit()
        cur.close(); conn.close()

        return jsonify(success=True, message="Sales update saved successfully")

    except Exception as e:
        conn.rollback()
        cur.close(); conn.close()
        print("❌ ERROR:", e)
        return jsonify(success=False, message=str(e)), 500

@salesdeal_bp.route("/flats/<project>")
@login_required
def get_available_flats(project):
    print("🔥 get_available_flats called | project =", project)

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT flat_no
        FROM public.sales_inventory
        WHERE (status IS NULL OR status = '')
          AND project = %s
        ORDER BY flat_no
    """, (project,))

    flats = cur.fetchall()

    print("🔥 Flats fetched:", flats)

    cur.close()
    conn.close()

    return jsonify(flats)
