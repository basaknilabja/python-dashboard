from flask import (
    render_template, session, redirect, url_for,
    flash, request, send_file
)
import io
import os
from app.db import get_db_connection
from . import pms_bp
from app.decorators import login_required
from psycopg2.extras import RealDictCursor


# --------------------------------------------------
# PLANNED WORK VIEW
# --------------------------------------------------
@pms_bp.route("/planned", methods=["GET"])
@login_required
def pms_update():

    if session.get("user_id") != "Admin":
        flash("🚫 Access Denied: Admin only.", "danger")
        return redirect(url_for("auth.dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # 🔹 Planned work list
    cur.execute("""
        SELECT DISTINCT plannedworkname
        FROM public.pms_work
        WHERE plannedworkname IS NOT NULL
        ORDER BY plannedworkname
    """)
    planWork = cur.fetchall()

    selected_plan = request.args.get("planWork")
    pmsWork = []

    if selected_plan:
        cur.execute("""
            SELECT
                pmsworkid,
                planned,
                actual,
                boq,
                pilemarkno,
                delayDays,
                plannedboq,
                plannedworkname
            FROM public.pms_work
            WHERE plannedworkname = %s
            ORDER BY planned
        """, (selected_plan,))
        pmsWork = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "pms/pms_work_planned.html",
        planWork=planWork,
        pmsWork=pmsWork,
        selected_plan=selected_plan
    )


# --------------------------------------------------
# BOQ SUBMISSION FORM
# --------------------------------------------------
@pms_bp.route("/submission_form", methods=["GET", "POST"])
@login_required
def submission_form():

    if session.get("user_id") != "Admin":
        flash("🚫 Access Denied: Admin only.", "danger")
        return redirect(url_for("auth.dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Planned work list
    cur.execute("""
        SELECT DISTINCT plannedworkname
        FROM public.pms_work
        WHERE plannedworkname IS NOT NULL
        ORDER BY plannedworkname
    """)
    planned_work_list = cur.fetchall()

    # Technical employees
    cur.execute("""
        SELECT empname
        FROM public.emp_master
        WHERE department = 'TECHNICAL'
        ORDER BY empname
    """)
    emp_list = cur.fetchall()

    # ---------------- POST ----------------
    if request.method == "POST":

        pmsworkid       = request.form.get("pmsworkid")
        plannedworkname = request.form.get("plannedworkname")
        actual_date     = request.form.get("actual")
        contractor      = request.form.get("contractor")
        pilemarkno      = request.form.get('pilemarkno')
        submitBy        = request.form.get("submitBy")

        boq_raw = request.form.get("boq")
        if not boq_raw:
            flash("❌ BOQ is required", "danger")
            return redirect(url_for("pms.submission_form"))

        try:
            boq_qty = float(boq_raw)
        except ValueError:
            flash("❌ Invalid BOQ value", "danger")
            return redirect(url_for("pms.submission_form"))

        # Attachment
        file_obj = request.files.get("attachment")
        attachment = None
        attachment_name = None

        if file_obj and file_obj.filename:
            attachment = file_obj.read()
            attachment_name = file_obj.filename

        try:
            # 1️⃣ Insert submission
            cur.execute("""
                INSERT INTO public.pmsupdateform
                    (pmsworkid, plannedworkname, boq, actual,
                     attachment, attachment_name, pilemarkno, submitBy, contractor)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                pmsworkid,
                plannedworkname,
                boq_qty,
                actual_date,
                attachment,
                attachment_name,
                pilemarkno,
                submitBy,
                contractor
            ))

            # 2️⃣ Update running BOQ (PostgreSQL COALESCE)
            cur.execute("""
                UPDATE public.pms_work
                SET boq = COALESCE(boq, 0) + %s
                WHERE pmsworkid = %s
            """, (boq_qty, pmsworkid))

            # 3️⃣ Check BOQ completion
            cur.execute("""
                SELECT boq, plannedboq, planned
                FROM public.pms_work
                WHERE pmsworkid = %s
            """, (pmsworkid,))
            row = cur.fetchone()

            if row and row["boq"] == row["plannedboq"]:
                cur.execute("""
                    UPDATE public.pms_work
                    SET
                        actual = %s,
                        delayDays = (%s::date - planned::date)
                    WHERE pmsworkid = %s
                """, (actual_date, actual_date, pmsworkid))

            conn.commit()
            flash("✅ BOQ submitted successfully", "success")

        except Exception as e:
            conn.rollback()
            print("❌ BOQ ERROR:", e)
            flash("❌ Error while submitting BOQ", "danger")

        finally:
            cur.close()
            conn.close()

        return redirect(url_for("pms.submission_form"))

    # ---------------- HISTORY ----------------
    cur.execute("""
        SELECT
            id,
            pmsworkid,
            plannedworkname,
            boq,
            actual,
            submitBy,
            contractor
        FROM public.pmsupdateform
        ORDER BY actual DESC
    """)
    boqEntry = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "pms/boq_submission_form.html",
        planned_work_list=planned_work_list,
        emp_list=emp_list,
        boqEntry=boqEntry
    )


# --------------------------------------------------
# AJAX: Get PMS Work ID
# --------------------------------------------------
@pms_bp.route("/get_pms_work_ids")
@login_required
def get_pms_work_ids():

    plannedworkname = request.args.get("plannedworkname")

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT pmsworkid
        FROM public.pms_work
        WHERE plannedworkname = %s
          AND actual IS NULL
        ORDER BY planned
        LIMIT 1
    """, (plannedworkname,))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return {"data": rows}


# --------------------------------------------------
# BOQ SUBMISSION HISTORY
# --------------------------------------------------
@pms_bp.route("/boq_submision_history")
@login_required
def boq_submision_history():

    if session.get("user_id") != "Admin":
        flash("🚫 Access Denied", "danger")
        return redirect(url_for("auth.dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT
            id,
            pmsworkid,
            plannedworkname,
            boq,
            actual,
            submitBy,
            contractor
        FROM public.pmsupdateform
        ORDER BY actual DESC
    """)
    boqEntry = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "pms/boq_submission_history.html",
        boqEntry=boqEntry
    )


# --------------------------------------------------
# DOWNLOAD BOQ ATTACHMENT
# --------------------------------------------------
@pms_bp.route("/boq/attachment/<int:entry_id>")
@login_required
def download_boq_attachment(entry_id):

    if session.get("user_id") != "Admin":
        flash("🚫 Access Denied", "danger")
        return redirect(url_for("auth.dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT attachment, attachment_name, pmsworkid
        FROM public.pmsupdateform
        WHERE id = %s
    """, (entry_id,))
    row = cur.fetchone()

    cur.close()
    conn.close()

    if not row or not row["attachment"]:
        flash("❌ Attachment not found", "danger")
        return redirect(url_for("pms.boq_submision_history"))

    _, ext = os.path.splitext(row["attachment_name"] or "")
    if not ext:
        ext = ".bin"

    filename = f"{row['pmsworkid']}-{entry_id}{ext}"

    return send_file(
        io.BytesIO(row["attachment"]),
        as_attachment=True,
        download_name=filename
    )
