from flask import render_template, request, redirect, url_for, flash, current_app,g,session,jsonify
from app.db import get_db_connection
from datetime import datetime, date, time as dtime, timedelta
from psycopg2.extras import RealDictCursor

from . import sales_bp
# from app.routes.sales import sales_bp
from app.decorators import login_required
from .helpers import (
    calculate_next_plan, 
    update_missing_plan1, 
    presales_update_followup, 
    presales_update_form_submission,
    ordinal_suffix 
)
from app.utils import login_required
# from flask_login import login_required

from app.routes.sales.helpers import (
    ordinal_suffix,
    calculate_next_plan
)

@sales_bp.route("/presales", methods=["GET", "POST"])
@login_required
def sales_presales():
    """Admin-only — record-wise allotment of enquiries to presales executives and insertion into public.doer_pending."""
    # from app.routes.sales.helpers import update_missing_plan1
    update_missing_plan1()

    user_id = session.get("user_id")
    if user_id != "Admin":
        flash("🚫 Access Denied: Only Admin can allot enquiries.", "danger")
        return redirect(url_for("auth.dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # ✅ Fetch all presales employees
    cur.execute("SELECT empname FROM public.emp_master WHERE department = 'SALES' ORDER BY empname")
    employees = [row["empname"] for row in cur.fetchall()]

    # ✅ Fetch pending enquiries (not yet actioned)
    cur.execute("""
        SELECT leadsid, project, name, contactno, emailid, plan1, timestamp, presales_person
        FROM public.presales
        WHERE plan1 IS NOT NULL AND actual1 IS NULL
        ORDER BY timestamp DESC
    """)
    enquiries = cur.fetchall()

    # ✅ When Admin clicks “Allot”
    if request.method == "POST":
        leadsid = request.form.get("leadsid")
        empname = request.form.get("empname")

        if not empname:
            flash("⚠ Please select an employee to allot.", "warning")
        else:
            # --- Step 1️⃣ Update presales table ---
            cur.execute(
                "UPDATE public.presales SET presales_person = %s WHERE leadsid = %s",
                (empname, leadsid),
            )

            # --- Step 2️⃣ Insert new record into public.doer_pending ---
            cur.execute(
                "SELECT plan1, presales_person  FROM public.presales WHERE leadsid = %s", (leadsid,)
            )
            lead = cur.fetchone()

            if lead:
                tools_name = "Pre Sales FMS"
                plan1=lead["plan1"]
                empnm=lead["presales_person"]
                step_name = "1st Followup"
                timestamp = datetime.now()
                
                # Avoid duplicate insertion
                cur.execute("SELECT COUNT(*) AS cnt FROM public.doer_pending WHERE unique_id = %s", (leadsid,))
                exists = cur.fetchone()["cnt"]

                if exists == 0:
                    cur.execute("""
                        INSERT INTO public.doer_pending (unique_id, step_name, how_to_do, planned, doer,tools_name)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (leadsid, step_name, "Followup the customer by phone", plan1, empnm, tools_name))

                    # --- Step 3️⃣ Update doer_empid by joining emp_master ---
                    cur.execute("""
                        UPDATE public.doer_pending d
                        JOIN emp_master e ON d.doer = e.empname
                        SET d.doer_empid = e.empid
                        WHERE d.unique_id = %s
                    """, (leadsid,))

                    conn.commit()
                    flash(f"✅ Lead {leadsid} allotted to {empname} and added to Doer Pending.", "success")
                else:
                    flash(f"ℹ Lead {leadsid} already exists in Doer Pending.", "info")

            else:
                flash("⚠ Lead not found in presales table.", "danger")

        cur.close()
        conn.close()
        return redirect(url_for("sales.sales_presales"))

    cur.close()
    conn.close()
    return render_template("sales/enquiries_allotment.html", employees=employees, enquiries=enquiries)

# ================= PRESALES UPDATE ROUTE =================
@sales_bp.route("/presales/update", defaults={"leadsid": None}, methods=["GET", "POST"])
@sales_bp.route("/presales/update/<leadsid>", methods=["GET", "POST"])
@login_required
def presales_update(leadsid):
    empid = g.current_user.get("empid")
    empname = g.current_user.get("empname", "")

    if not empid:
        flash("Session expired. Please login again.", "danger")
        return redirect(url_for("auth.login"))

    print(f"\n🟢 presales_update called | empid={empid} | leadsid={leadsid}")

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # ==============================================================
    # Load pending list for this Pre-Sales employee
    # ==============================================================
    cur.execute("""
        SELECT unique_id, step_name, planned, last_status, last_remarks,
               last_interaction, tools_name, project
        FROM public.doer_pending
        WHERE doer_empid=%s AND tools_name='Pre Sales FMS' AND actual IS NULL
        ORDER BY planned ASC
    """, (empid,))
    pending_list = cur.fetchall()

    # ==============================================================
    # Load sales team list
    # ==============================================================
    cur.execute("SELECT empname FROM public.emp_master WHERE department='Sales' ORDER BY empname ASC")
    sales_people = [r["empname"] for r in cur.fetchall()]

    # ==============================================================
    # GET → render page
    # ==============================================================
    if request.method == "GET":
        cur.close()
        conn.close()
        return render_template("sales/presales_update.html",
                               pending_list=pending_list,
                               sales_people=sales_people)

    # ==============================================================
    # POST → SAVE FOLLOW-UP
    # ==============================================================
    form = request.form

    leadsid = leadsid or form.get("leadsid")
    if not leadsid:
        cur.close(); conn.close()
        return jsonify(success=False, message="No lead selected"), 400

    status = form.get("status")
    remarks = form.get("remarks")
    whatsapp_send = form.get("whatsapp_send")
    tat_days = form.get("tat", type=int)
    assigned_sales = form.get("sales_person")
    # project = form.get("project")

    site_visit_dt_str = form.get("site_visit_datetime")
    site_visit_dt = None
    if site_visit_dt_str:
        try:
            site_visit_dt = datetime.strptime(site_visit_dt_str, "%Y-%m-%dT%H:%M")
            print(f'Site visit datetime{site_visit_dt}')
        except:
            print("⚠ Invalid site visit datetime")
    
    actual_time = datetime.now()
    actual_date = actual_time.date()

    # ==============================================================
    # Fetch current active step
    # ==============================================================
    cur.execute("""
        SELECT step_name
        FROM public.doer_pending
        WHERE unique_id=%s AND doer_empid=%s
              AND tools_name='Pre Sales FMS' AND actual IS NULL
        ORDER BY planned ASC LIMIT 1
    """, (leadsid, empid))
    row = cur.fetchone()

    if not row:
        cur.close(); conn.close()
        return jsonify(success=False, message="No active follow-up found"), 404

    step_name = row["step_name"]
    followup_num = int(''.join(filter(str.isdigit, step_name))) or 1

    print(f"🟣 Updating Lead {leadsid} | Step {followup_num} | Status={status}")

    # ==============================================================
    # UPDATE PRESALES TABLE
    # ==============================================================
    fields = [
        f"actual{followup_num}=%s",
        f"status{followup_num}=%s",
        f"remarks{followup_num}=%s",
        f"whatsapp_send{followup_num}=%s",
        f"tat{followup_num}=%s",
        "last_status=%s",
        "last_remarks=%s",
        "last_interaction=%s"
        ]

    params = [
        actual_time,
        status,
        remarks,
        whatsapp_send,
        tat_days,
        status,
        remarks,
        actual_date
       ]

    status_lower = (status or "").lower()

    if status_lower in ("active", "site visit scheduled"):
        fields.append("site_visit=%s")
        params.append(site_visit_dt)

    if status_lower == "active":
        fields.append("sales_person=%s")
        params.append(assigned_sales)

    update_sql = f"""
        UPDATE public.presales
        SET {", ".join(fields)}
        WHERE leadsid=%s
    """
    params.append(leadsid)

    cur.execute(update_sql, tuple(params))
    
    print("PRESALES ROWS UPDATED:", cur.rowcount)

    # ==============================================================
    # UPDATE public.doer_pending (mark current as done)
    # ==============================================================
    cur.execute("""
        UPDATE public.doer_pending
        SET actual=%s, status=%s, remarks=%s, last_interaction=%s, last_status=%s, last_remarks=%s
        WHERE unique_id=%s AND step_name=%s AND doer_empid=%s AND tools_name=%s
    """, (actual_time, status, remarks, actual_date, status, remarks,
          leadsid, step_name, empid,"Pre Sales FMS"))

    # ==============================================================
    # DETERMINE NEXT PLAN
    # ==============================================================
    status_lower = (status or "").strip().lower()

    next_plan = None

    if status_lower == "site visit scheduled" and site_visit_dt:
        next_plan = site_visit_dt
        print("📅 Next plan = site visit date")

    elif status_lower == "active":
        next_plan = None
        print("🟠 Active → Sales assigned, no next plan for presales fms but next plan for sales Fms")

    elif status_lower not in [
        "duplicate", "incorrect no", "not interested",
        "budget mismatch", "location mismatch", "vendor"
    ]:
        next_plan = calculate_next_plan(status_lower, actual_time, tat_days, site_visit_dt)

    # ==============================================================
    # INSERT NEXT FOLLOW-UP (ONLY IF next_plan exists)
    # ==============================================================
    if next_plan:
        next_step_num = followup_num + 1
        next_step_name = f"{ordinal_suffix(next_step_num)} Followup"

        cur.execute(f"UPDATE public.presales SET plan{next_step_num}=%s WHERE leadsid=%s",
                    (next_plan, leadsid))

        cur.execute("""
            INSERT INTO public.doer_pending
            (unique_id, step_name, planned, last_status, last_remarks,
             last_interaction, tools_name, doer, doer_empid, project)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (leadsid, next_step_name, next_plan, status, remarks,
              actual_date, "Pre Sales FMS", empname, empid, "SINGHADUAR"))

    # ==============================================================
    # ALWAYS — Update public.SALES table when status == Active
    # ==============================================================
    if status_lower == "active":
        cur.execute("""
            SELECT project, name, contactno, emailid, sales_person, site_visit
            FROM public.presales
            WHERE leadsid=%s
        """, (leadsid,))
        p = cur.fetchone()

        if p:
            # Prefer the freshly selected site_visit from DB, fall back to form value
            # (or the other way round, depending on your logic)
            sitevisit_final = site_visit_dt or p["site_visit"]

            # 🔹 If it's already a full datetime -> just set time to 19:00
            if isinstance(sitevisit_final, datetime):
                sitevisit_final = sitevisit_final.replace(
                    hour=19, minute=0, second=0, microsecond=0
                )

            # 🔹 If it's only a date object -> combine with time 19:00
            elif isinstance(sitevisit_final, date):
                sitevisit_final = datetime.combine(sitevisit_final, dtime(19, 0))

            cur.execute("""
                INSERT INTO public.sales (leadsid, project, customer_name, mobile_no, email_id,
                                   sales_person, sitevisit_planned)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    project=VALUES(project),
                    customer_name=VALUES(customer_name),
                    mobile_no=VALUES(mobile_no),
                    email_id=VALUES(email_id),
                    sales_person=VALUES(sales_person),
                    sitevisit_planned=VALUES(sitevisit_planned)
            """, (
                leadsid,
                p["project"], p["name"], p["contactno"],
                p["emailid"], assigned_sales,
                sitevisit_final
            ))

            cur.execute("""
            INSERT INTO public.doer_pending
            (unique_id, step_name, planned, last_status, last_remarks,
             last_interaction, tools_name, doer, doer_empid, project)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (leadsid, 'site visit', sitevisit_final, status, remarks,
              actual_date, "Sales FMS", empname, empid, "SINGHADUAR"))

            print(f"🟢 SALES sitevisit_planned saved: {sitevisit_final}")

    # ==============================================================
    # FINAL COMMIT
    # ==============================================================
    conn.commit()

    print("🔥 Update Completed Successfully")

    # Return updated pending list
    cur.execute("""
        SELECT unique_id, step_name, planned, last_status, last_remarks,
               last_interaction, tools_name, project
        FROM public.doer_pending
        WHERE doer_empid=%s AND tools_name='Pre Sales FMS' AND actual IS NULL
        ORDER BY planned ASC
    """, (empid,))
    updated_pending = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify(success=True, pending=updated_pending)


@sales_bp.route('/presales_followup/<leadsid>', methods=['GET', 'POST'])
@login_required
def presales_followup(leadsid):
    empid   = g.current_user["empid"]
    empname = g.current_user.get("empname", "")
    now     = datetime.now()
    status1="YES APPROVED"
    rem=""
    tools_name="Pre Sales FMS"

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute("""SELECT * FROM public.doer_pending WHERE doer_empid=%s AND unique_id=%s AND tools_name=%s""",
                    (empid,leadsid,tools_name)
                    )
        if not cur.fetchall():
            flash("No pending followup")
            current_app.logger.exception("No pending followup" )
            return redirect(url_for("presales_followup"))
        else:
            cur.execute(
                """
                SELECT step_name
                  FROM public.doer_pending
                 WHERE unique_id=%s AND doer_empid=%s AND tools_name=%s
                   AND actual IS NULL
                 ORDER BY planned ASC
                 LIMIT 1
                """, (leadsid, empid, tools_name))
            
        if not step_name:
            
            cur.execute(
                """
                SELECT step_name
                  FROM public.doer_pending
                 WHERE unique_id=%s AND doer_empid=%s AND tools_name=%s
                   AND actual IS NULL
                 ORDER BY planned ASC
                 LIMIT 1
                """, (leadsid, empid, "Pre Sales FMS")
            )
            row = cur.fetchone()
            if not row:
                flash("No pending step assigned to you for this indent.", "warning")
                return redirect(url_for("sales.sales_presales"))
            step_name = row["step_name"]
        else:
            # Verify this exact step row is assigned to this doer and still pending
            cur.execute(
                """
                SELECT 1
                  FROM public.doer_pending
                 WHERE unique_id=%s AND doer_empid=%s AND tools_name=%s
                   AND step_name=%s AND actual IS NULL
                 LIMIT 1
                """, (leadsid, empid, "Pre Sales FMS", step_name)
            )
            if cur.fetchone() is None:
                flash("You are not authorized to update this step or it is already completed.", "danger")
                return redirect(url_for("purchase.purchase_pending"))

            # =========================
            # STEP 1: 1st Followup
            # =========================

            if request.method == 'POST':
                status = request.form.get('status1')
                remarks = request.form.get('remarks1')
                whatsapp_send = request.form.get('whatsapp_send1')
                tat_days = request.form.get('tat1', type=int)
                site_visit_datetime = request.form.get('site_visit_datetime')
                actual_time = datetime.now()

                cur.execute("""
                    UPDATE public.presales
                    SET actual1=%s, status1=%s, remarks1=%s, whatsapp_send1=%s, tat1=%s
                    WHERE leadsid=%s
                """, (actual_time, status, remarks, whatsapp_send, tat_days, leadsid))
    finally:
        
        flash("Follow-up updated successfully!", "success")
        return redirect(url_for('sales.presales_followup'))

        cur.execute("SELECT * FROM presales WHERE leadsid = %s", (leadsid,))
        lead = cur.fetchone()
        cur.close(); conn.close()

    return render_template('sales/presales_followup.html', lead=lead)

@sales_bp.route("/customer_walkIn", methods=["GET", "POST"])
@login_required
def customer_walkIn():
    """Sales Admin-only filled WALK-IN form whenever a new customer walk in site— """
    # from app.routes.sales.helpers import update_missing_plan1
    
    user_id = session.get("user_id")
    if user_id != "sd Admin":
        flash("🚫 Access Denied: Only sd Admin can allot enquiries.", "danger")
        return redirect(url_for("auth.dashboard"))

    if request.method=="GET":
        # cur.close(), conn.close()
        return render_template('/sales/walkin.html')

    
    if request.method=="POST":
        custname = request.form.get('custname')   
        mobileno = request.form.get('mobileno') 
        whtsappno = request.form.get('whtsappno')
        emailid = request.form.get('emailid')
        location = request.form.get('location')
        pincode = request.form.get('pincode')
        source= request.form.get('source')
        budget = request.form.get('budget')
        bhk = request.form.get('bhk')

        actual_time = datetime.now()
        lead_date = actual_time.date()
        leadid = f"({custname}_{lead_date.strftime('%Y%m%d')})"

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        cur.execute("""INSERT INTO public.enqlead (leadid, project , custname, mobileno, whtsappno, emailid, location, pincode, source, budget, bhk, lead_date)
            values (%s, 'SINGHADUAR', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """,
            (leadid, 'SINGHADUAR', custname, mobileno, whtsappno, emailid, location, pincode, source, budget, bhk, lead_date))
        
        cur.close(); conn.close()

        flash("✅ Walk-in customer added successfully!", "success")
        return redirect(url_for("sales.customer_walkIn"))
        

        

