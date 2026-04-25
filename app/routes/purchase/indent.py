# app/routes/purchase/indent.py
from flask import render_template, request, redirect, url_for, flash,current_app,g
from app.db import get_db_connection
# from app.utils import login_required
from app.decorators import login_required
from . import purchase_bp   # ✅ this imports purchase_bp from __init__.py
from datetime import datetime, timedelta
from psycopg2.extras import RealDictCursor
import psycopg2

@purchase_bp.route("/", methods=["GET", "POST"])
@login_required
def purchase():
    user_id = g.current_user["user_id"]
    empid= g.current_user["empid"]
    # Precompute values used when rendering the form
    # get current time
    now = datetime.now()

    conn = get_db_connection()
    try:
        c = conn.cursor(cursor_factory=RealDictCursor)

        # Generate Indent No: mm-yyyy-count+1
        c.execute("SELECT COUNT(*) as cnt FROM public.purchase")
        count = c.fetchone()["cnt"]
    finally:
        try:
            c.close()
            conn.close()
        except Exception:
            pass
        conn.close()

    today = datetime.today()
    indent_no_generated  = today.strftime("%b-%Y-") + str(count + 1)

    # Convert datetime to timestamp (seconds since epoch)
    timestamp1 = int(now.timestamp())

    # Approval planned date = +1 day
    # apprv_plan_dt = now + timedelta(days=1)
    
    apprv_plan_dt = (now + timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
    apprv_by='RAHUL DE'
        
    if request.method == "POST":
        pms_id = request.form["pms_id"]
        timestamp1 = request.form["timestamp1"]  # hidden field ensures it is saved
        indent_no = request.form["indent_no"]    # hidden field ensures it is saved
        indent_dt = request.form["indent_dt"]
        indent_location = request.form["indent_location"]
        job_reference = request.form["job_reference"]
        description = request.form["description"]
        prepared_by = request.form["prepared_by"]
        no_of_items = request.form["no_of_items"]
        indent_photo = request.files.get("indent_photo")
        calc_sheet = request.files.get("calc_sheet")

        indnt_pht = indent_photo.read() if indent_photo else None
        calc_sht = calc_sheet.read() if calc_sheet else None
        tools_name="Purchase FMS"
        step_name="Indent approval"

        conn=get_db_connection()
        cursor=conn.cursor(cursor_factory=RealDictCursor)

        try:
            # 1) insert into public.purchase
            cursor.execute(
                """INSERT INTO public.purchase 
                (pms_id, timestamp1, indent_no, indent_dt, indent_location, job_reference, description, prepared_by, no_of_items, indent_photo, calc_sheet, apprv_plan_dt, apprv_by,tools_name, step_name,prepared_empid) 
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (pms_id, timestamp1, indent_no, indent_dt, indent_location, job_reference, description, prepared_by, no_of_items, indnt_pht, calc_sht, apprv_plan_dt, apprv_by, tools_name, step_name, empid)
            )
            conn.commit()


            cursor.execute(
                """SELECT DISTINCT pms_id FROM public.pms_sd """
            )
            pms_id=cursor.fetchall()
            

            # 2) Now check if we should insert into public.doer_pending for this indent_no.
                #    Condition: apprv_plan_dt IS NOT NULL AND apprv_actual IS NULL
                #    (We just set apprv_plan_dt, so it should be true — but check DB to be safe.)

            cursor.execute("select * from public.purchase where indent_no=%s and apprv_plan_dt is not null and apprv_actual is null",
                       (indent_no,)
            )
            row=cursor.fetchone()

            if not row:
                # nothing to add
                current_app.logger.info("No pending approval row found for %s",(indent_no))
            else:
                unique_id=row["indent_no"]
                planned=row["apprv_plan_dt"]
                # step_name="Indent approval"
                how_to_do="Check the inventory and analyse indent"
                doer="RAHUL DE"
                # tools_name="Purchase FMS"

                # check if already exists in doer_pending
                cursor.execute("select 1 from public.doer_pending where unique_id=%s and tools_name=%s and step_name=%s",(indent_no, tools_name, step_name)
                )
                exists=cursor.fetchone()

                if exists:
                    current_app.logger.info("doer_pending already has entry where unique_id=%s",(unique_id))
                    flash(f"Purchase indent no {unique_id} already quued for doer","info")
                else:
                    # insert into public.doer_pending
                    # use a non-dictionary cursor for insert or reuse the same cursor
                    cursor.execute("""insert into public.doer_pending (unique_id, planned, step_name, how_to_do, doer, attachment1, attachment2, tools_name,doer_empid) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""", 
                                (unique_id, planned, step_name, how_to_do, doer, indnt_pht, calc_sht, tools_name,"TT0008" )
                    )
                    conn.commit()
                    flash("Inserted into public.doer_pending","info")
                    
                # all done
                return redirect(url_for("purchase.purchase"))
            
        except psycopg2.Error as e:
            conn.rollback()
            current_app.logger.exception(
                "DB error while creating purchase / doer_pending for %s", indent_no
            )
            flash(f"DB error: {e.pgerror}", "danger")
            return redirect(url_for("purchase.purchase"))

        
        finally:
            cursor.close()
            conn.close()

    # GET request — render the form with generated defaults
    # timestamp1 for html datetime-local input expects format like "YYYY-MM-DDTHH:MM"

    formatted_timestamp1 = datetime.fromtimestamp(timestamp1).strftime("%Y-%m-%dT%H:%M")

    return render_template(
        "purchase/purchase.html",
        indent_no=indent_no_generated,
        today=today.strftime("%Y-%m-%d"),
        timestamp1=formatted_timestamp1,
        pms_id=pms_id
    )