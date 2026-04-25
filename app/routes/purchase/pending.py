from . import purchase_bp
from flask import render_template,request,flash,redirect,url_for,abort,send_file,g,current_app
# from app.utils import login_required
from app.decorators import login_required
from app.db import get_db_connection
from datetime import datetime, timedelta
from io import BytesIO
from psycopg2.extras import RealDictCursor
# from app.routes.auth import *
# ================= Purchase CRUD =================

TOOLS_NAME = "Purchase FMS"

@purchase_bp.route("/purchase/list")
@login_required
def purchase_list():
    empid = g.current_user.get("empid") if hasattr(g, "current_user") else None
    empname = g.current_user.get("empname", "") if hasattr(g, "current_user") else ""

    if not empid:
        flash("Session expired. Please login again.", "danger")
        return redirect(url_for("auth.login"))
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM public.purchase ORDER BY indent_dt DESC")
    raw_rows = cursor.fetchall() or []
    cursor.close()
    conn.close()

    def format_timestamp(ts):
        if ts is None:
            return ""
        if isinstance(ts, datetime):
            return ts.strftime("%Y-%m-%dT%H:%M")
        try:
            ts_int = int(ts)
            if ts_int > 10**12:
                ts_int //= 1000
            return datetime.fromtimestamp(ts_int).strftime("%Y-%m-%dT%H:%M")
        except Exception:
            pass
        try:
            return datetime.fromisoformat(str(ts)).strftime("%Y-%m-%dT%H:%M")
        except Exception:
            return str(ts)

    purchases = []
    for row in raw_rows:
        if isinstance(row, dict):
            row["timestamp1"] = format_timestamp(row.get("timestamp1"))
            purchases.append(row)
    return render_template("purchase/purchase_list.html", purchases=purchases)

# Update public.purchase inline
@purchase_bp.route("/purchase/update/<int:purchase_id>", methods=["POST"])
@login_required
def purchase_update(purchase_id):
    indent_dt = request.form["indent_dt"]
    indent_location = request.form["indent_location"]
    job_reference = request.form["job_reference"]
    description = request.form["description"]
    prepared_by = request.form["prepared_by"]
    no_of_items = request.form["no_of_items"]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE public.purchase
        SET indent_dt=%s, indent_location=%s, job_reference=%s, description=%s, prepared_by=%s, no_of_items=%s
        WHERE id=%s
    """, (indent_dt, indent_location, job_reference, description, prepared_by, no_of_items, purchase_id))
    conn.commit()
    cursor.close()
    conn.close()

    flash("Purchase updated successfully!", "success")
    return redirect(url_for("purchase.purchase_list"))

# Delete purchase
@purchase_bp.route("/purchase/delete/<int:purchase_id>", methods=["POST"])
@login_required
def purchase_delete(purchase_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM public.purchase WHERE id=%s", (purchase_id,))
    conn.commit()
    cursor.close()
    conn.close()

    flash("Purchase deleted successfully!", "danger")
    return redirect(url_for("purchase.purchase_list"))

# --- serve indent photo bytes (if stored as blob) ---
@purchase_bp.route("/doer/attachment/<string:unique_id>/<int:which>")
@login_required
def doer_attachment(unique_id, which: int):
    col = "attachment1" if which == 1 else "attachment2"
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT {col} FROM doer_pending WHERE unique_id=%s", (unique_id,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if not row or not row[0]:
        abort(404)
        
    blob = row[0]

    # return Response(row[0], mimetype="image/jpeg")
    return send_file(BytesIO(blob),
                 mimetype="image/jpeg",
                 as_attachment=True,
                 download_name=f"{unique_id}.jpg")

# --- list pending in purchase ---
@purchase_bp.route("/purchase/pending")
@login_required
def purchase_pending():
    user_id = g.current_user["user_id"]
    empid = g.current_user["empid"]

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    purchases = []

    try:
        # ✅ FIXED query (corrected quote + flexible filters)
        cursor.execute("""
            SELECT 
                dp.unique_id, dp.step_name, dp.how_to_do, dp.planned, dp.actual,
                dp.`status`, dp.remarks, dp.doer, dp.attachment1, dp.attachment2, dp.tools_name
            FROM public.doer_pending dp
            JOIN public.emp_master e ON dp.doer_empid = e.empid
            WHERE e.user_id = %s
              AND dp.doer_empid = %s
              AND dp.actual IS NULL 
              AND (dp.`status` IS NULL OR dp.`status` = '')
              AND dp.planned IS NOT NULL
              AND dp.tools_name=%s
            ORDER BY dp.tools_name, dp.planned ASC
        """, (user_id, empid,"Purchase FMS"))

        purchases = cursor.fetchall()

        # ✅ Debug logging
        print(f"🔍 Found {len(purchases)} pending records for user_id={user_id}, empid={empid}")

        if not purchases:
            flash(f"No pending record found for user_id: {user_id} and empid: {empid}", "info")
        else:
            for p in purchases:
                dt = p.get("planned")
                if isinstance(dt, datetime):
                    p["planned_fmt"] = dt.strftime("%Y-%m-%d %H:%M")
                elif dt:
                    p["planned_fmt"] = str(dt)
                else:
                    p["planned_fmt"] = ""

    except Exception as e:
        current_app.logger.exception("Error fetching purchase pending list")
        flash(f"Error loading pending: {e}", "danger")
        purchases = []
    finally:
        cursor.close()
        conn.close()

    return render_template("purchase/purchase_pending.html", purchases=purchases)

# --- update apprv_status and apprv_through for a given indent_no ---


def _read_file(field_name):
    f = request.files.get(field_name)
    return f.read() if f and f.filename else None

def _insert_next_step(cur, *, indent_no, step_name, planned_dt, doer_name, doer_empid,
                      attachment1=None, attachment2=None):
    """Insert the next step row into public.doer_pending."""
    cur.execute(
        """
        INSERT INTO public.doer_pending
            (unique_id, step_name, planned, doer, doer_empid, tools_name, attachment1, attachment2)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (indent_no, step_name, planned_dt, doer_name, doer_empid, TOOLS_NAME, attachment1, attachment2)
    )

@purchase_bp.route("/purchase/pending/update/<string:indent_no>", methods=["POST"])
@login_required
def purchase_pending_update(indent_no):
    empid   = g.current_user["empid"]
    empname = g.current_user.get("empname", "")
    now     = datetime.now()
    status1="YES APPROVED"
    rem=""

    # Step to execute (must be provided by form OR looked up from doer_pending)
    step_name = (request.form.get("step_name") or "").strip()

    # Common enums
    allowed_status  = {"Yes", "No", "Hold", "Cancel","NA"}
    allowed_through = {"PO", "Local", "Transfer"}

    # Optional: you can choose next assignee via form for subsequent steps
    next_doer       = (request.form.get("next_doer") or empname).strip()
    next_doer_empid = (request.form.get("next_doer_empid") or empid).strip()

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Authorize: ensure this indent + step belongs to this doer (still pending)
        if not step_name:
            # If not supplied, grab the pending step assigned to this doer
            cur.execute(
                """
                SELECT step_name
                  FROM public.doer_pending
                 WHERE unique_id=%s AND doer_empid=%s AND tools_name=%s
                   AND actual IS NULL
                 ORDER BY planned ASC
                 LIMIT 1
                """, (indent_no, empid, TOOLS_NAME)
            )
            row = cur.fetchone()
            if not row:
                flash("No pending step assigned to you for this indent.", "warning")
                return redirect(url_for("purchase.purchase_pending"))
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
                """, (indent_no, empid, TOOLS_NAME, step_name)
            )
            if cur.fetchone() is None:
                flash("You are not authorized to update this step or it is already completed.", "danger")
                return redirect(url_for("purchase.purchase_pending"))

        # =========================
        # STEP 1: Indent approval
        # =========================
        if step_name == "Indent approval":
            # apprv_status  = (request.form.get("apprv_status")  or "").strip()
            status1  = (request.form.get("status1")  or "").strip()
            apprv_through = (request.form.get("apprv_through") or "").strip()
            rem = (request.form.get("rem") or "").strip()

            if status1 not in allowed_status or apprv_through not in allowed_through:
                flash(f"Select valid Approval Status {status1} and Through.", "danger")
                return redirect(url_for("purchase.purchase_pending"))

            apprv_actual = now
            # Next step planned (your spec says: chk_cal_plan = apprv_actual + 1 day)
            chk_cal_plan = (apprv_actual + timedelta(days=1))

            # Update public.purchase
            cur.execute(
                """
                UPDATE public.purchase
                   SET apprv_actual  = %s,
                       apprv_status  = %s,
                       apprv_remarks = %s,
                       apprv_through = %s,
                       chk_cal_plan  = %s
                 WHERE indent_no     = %s
                """,
                (apprv_actual, status1, rem, apprv_through, chk_cal_plan, indent_no)
            )

            # Update current doer_pending row
            cur.execute(
                """
                UPDATE public.doer_pending
                   SET actual        = %s,
                       `status`      = %s,
                       remarks       = %s
                WHERE unique_id      = %s
                   AND doer_empid    = %s
                   AND tools_name    = %s
                   AND step_name     = %s
                   AND actual IS NULL
                """,
                (apprv_actual, status1, rem, 
                 indent_no, empid, TOOLS_NAME, "Indent approval")
            )

            # Insert next step row: "Check calculation"
            att1 = _read_file("attachment1")  # optional
            att2 = _read_file("attachment2")  # optional
            _insert_next_step(
                cur,
                indent_no=indent_no,
                step_name="Check calculation",
                planned_dt=chk_cal_plan,
                # doer_name=next_doer,
                doer_name="ANKITA SHEEL",
                # doer_empid=next_doer_empid,
                doer_empid="TT0025",
                attachment1=att1,
                attachment2=att2
            )

            conn.commit()
            flash(f"Indent approval updated for {indent_no}. Next step enqueued.", "success")
            return redirect(url_for("purchase.purchase_pending"))

        # =========================
        # STEP 2: Check calculation
        # =========================
        elif step_name == "Check calculation":
            status1 = (request.form.get("status1") or "").strip()
            rem    = (request.form.get("rem")    or "").strip()
            resubmit   = _read_file("resubmit_cal")  # file
            if status1 not in allowed_status:
                flash(f"Select a valid Check Calculation status {status1}.", "danger")
                return redirect(url_for("purchase.purchase_pending"))

            chk_cal_actual = now
            # Your spec: also set vendor_plan datetime (choose a plan; here +1 day)
            vendor_plan = chk_cal_actual + timedelta(days=1)

            # Update purchase
            cur.execute(
                """
                UPDATE public.purchase
                   SET chk_cal_actual = %s,
                       chk_cal_status = %s,
                       chk_cal_rem    = %s,
                       resubmit_cal   = %s,
                       vendor_plan    = %s
                 WHERE indent_no      = %s
                """,
                (chk_cal_actual, status1, rem, resubmit, vendor_plan, indent_no)
            )

            # Update current doer_pending row
            cur.execute(
                """
                UPDATE public.doer_pending
                   SET actual  = %s,
                       status  = %s,
                       remarks = %s
                 WHERE unique_id  = %s
                   AND doer_empid = %s
                   AND tools_name = %s
                   AND step_name  = %s
                   AND actual IS NULL
                """,
                (chk_cal_actual, status1, rem, indent_no, empid, TOOLS_NAME, "Check calculation")
            )

            # Insert next step row: "Choose vendor and float quotation"
            att1 = _read_file("attachment1")  # optional
            att2 = _read_file("attachment2")  # optional
            _insert_next_step(
                cur,
                indent_no=indent_no,
                step_name="Choose vendor and float quotation",
                planned_dt=vendor_plan,
                # doer_name=next_doer,
                doer_name="ANAMIKA SARKAR",
                # doer_empid=next_doer_empid,
                doer_empid="TT0084",
                attachment1=att1,
                attachment2=att2
            )

            conn.commit()
            flash(f"Check calculation updated for {indent_no}. Next step enqueued.", "success")
            return redirect(url_for("purchase.purchase_pending"))

        # ================================================
        # STEP 3: Choose vendor and float quotation
        # ================================================
        elif step_name == "Choose vendor and float quotation":
            status1 = (request.form.get("status1") or "").strip()
            rem    = (request.form.get("rem")    or "").strip()
            qt1 = _read_file("qt1")
            qt2 = _read_file("qt2")
            qt3 = _read_file("qt3")
            comp_sht = _read_file("comp_sht")

            if status1 not in allowed_status:
                flash("Select a valid Vendor step status.", "danger")
                return redirect(url_for("purchase.purchase_pending"))

            vendor_actual   = now
            take_smpl_plan  = vendor_actual + timedelta(days=1)

            # Update purchase
            cur.execute(
                """
                UPDATE public.purchase
                   SET vendor_actual = %s,
                       vendor_status = %s,
                       vendor_rem    = %s,
                       qt1           = %s,
                       qt2           = %s,
                       qt3           = %s,
                       comp_sht      = %s,
                       take_smpl_plan= %s
                 WHERE indent_no     = %s
                """,
                (vendor_actual, status1, rem, qt1, qt2, qt3, comp_sht, take_smpl_plan, indent_no)
            )

            # Update current doer_pending row
            cur.execute(
                """
                UPDATE public.doer_pending
                   SET actual  = %s,
                       status  = %s,
                       remarks = %s
                 WHERE unique_id  = %s
                   AND doer_empid = %s
                   AND tools_name = %s
                   AND step_name  = %s
                   AND actual IS NULL
                """,
                (vendor_actual, status1, rem, indent_no, empid, TOOLS_NAME, "Choose vendor and float quotation")
            )

            # Insert next step row: "apprv_smpl_vndr"
            att1 = _read_file("attachment1")  # optional
            att2 = _read_file("attachment2")  # optional
            _insert_next_step(
                cur,
                indent_no=indent_no,
                step_name="Take sample from vendor",
                planned_dt=take_smpl_plan,
                # doer_name=next_doer,
                doer_name="ANAMIKA SARKAR",
                # doer_empid=next_doer_empid,
                doer_empid="TT0084",
                attachment1=att1,
                attachment2=att2
            )

            conn.commit()
            flash(f"Vendor/quotation step updated for {indent_no}. Next step enqueued.", "success")
            return redirect(url_for("purchase.purchase_pending"))

        # ================================================
        # STEP 4: TAKE SAMPLE FROM VENDOR
        # ================================================
        elif step_name == "Take sample from vendor":
            status1 = (request.form.get("status1") or "").strip()
            rem    = (request.form.get("rem")    or "").strip()
            
            if status1 not in allowed_status:
                flash("Verify the sample step status.", "danger")
                return redirect(url_for("purchase.purchase_pending"))

            take_smpl_actual   = now
            aprv_vndr_plan  = take_smpl_actual + timedelta(days=1)

            # Update purchase
            cur.execute(
                """
                UPDATE public.purchase
                   SET take_smpl_actual = %s,
                       take_smpl_status = %s,
                       aprv_smpl_plan= %s
                 WHERE indent_no     = %s
                """,
                (take_smpl_actual, status1, aprv_vndr_plan, indent_no)
            )

            # Update current doer_pending row
            cur.execute(
                """
                UPDATE public.doer_pending
                   SET actual  = %s,
                       status  = %s
                WHERE unique_id  = %s
                   AND doer_empid = %s
                   AND tools_name = %s
                   AND step_name  = %s
                   AND actual IS NULL
                """,
                (take_smpl_actual, status1, indent_no, empid, TOOLS_NAME, "Take sample from vendor")
            )

            # Insert next step row: "apprv_smpl_vndr"
            att1 = _read_file("attachment1")  # optional
            att2 = _read_file("attachment2")  # optional
            _insert_next_step(
                cur,
                indent_no=indent_no,
                step_name="Take approval of sample and vendor from CEO",
                planned_dt=aprv_vndr_plan,
                # doer_name=next_doer,
                doer_name="ANAMIKA SARKAR",
                # doer_empid=next_doer_empid,
                doer_empid="TT0084",
                attachment1=att1,
                attachment2=att2
            )

            conn.commit()
            flash(f"Take sample from vendor step updated for {indent_no}. Next step enqueued.", "success")
            return redirect(url_for("purchase.purchase_pending"))

        # ====================================================
        # STEP 5: Take approval of sample and vendor from CEO
        # ====================================================
        elif step_name == "Take approval of sample and vendor from CEO":
            status1 = (request.form.get("status1") or "").strip()
            rem    = (request.form.get("rem")    or "").strip()
            rem1    = (request.form.get("rem1")    or "").strip()
                        
            if status1 not in allowed_status:
                flash("Taking approval of sample and vendor from CEO step status.", "danger")
                return redirect(url_for("purchase.purchase_pending"))

            aprv_vndr_actual  = now
            nego_plan  = aprv_vndr_actual + timedelta(days=1)

            # Update purchase
            cur.execute(
                """
                UPDATE public.purchase
                   SET aprv_vndr_actual = %s,
                       aprv_vndr_status = %s,
                       aprv_sample = %s,
                       aprv_vendor = %s,
                       nego_plan= %s
                 WHERE indent_no  = %s
                """,
                (aprv_vndr_actual, status1, rem, rem1, nego_plan, indent_no)
            )

            # Update current doer_pending row
            cur.execute(
                """
                UPDATE public.doer_pending
                   SET actual  = %s,
                       status  = %s
                WHERE unique_id  = %s
                   AND doer_empid = %s
                   AND tools_name = %s
                   AND step_name  = %s
                   AND actual IS NULL
                """,
                (aprv_vndr_actual, status1, indent_no, empid, TOOLS_NAME, "Take approval of sample and vendor from CEO")
            )

            # Insert next step row: "Price negotiation"
            att1 = _read_file("attachment1")  # optional
            att2 = _read_file("attachment2")  # optional
            _insert_next_step(
                cur,
                indent_no=indent_no,
                step_name="Price negotiation",
                planned_dt=nego_plan,
                # doer_name=next_doer,
                doer_name="ANAMIKA SARKAR",
                # doer_empid=next_doer_empid,
                doer_empid="TT0084",
                attachment1=att1,
                attachment2=att2
            )

            conn.commit()
            flash(f"Take approval of sample and vendor from CEO step updated for {indent_no}. Next step enqueued.", "success")
            return redirect(url_for("purchase.purchase_pending"))

            
        # ====================================================
        # STEP 5: Price negotiation
        # ====================================================
        elif step_name == "Price negotiation":
            status1 = (request.form.get("status1") or "").strip()
            rem    = (request.form.get("rem")    or "").strip()
                                    
            if status1 not in allowed_status:
                flash("Price negotiation step status.", "danger")
                return redirect(url_for("purchase.purchase_pending"))

            nego_actual  = now
            po_plan  = nego_actual + timedelta(days=1)

            # Update purchase
            cur.execute(
                """
                UPDATE public.purchase
                   SET nego_actual = %s,
                       nego_status = %s,
                       nego_price = %s,
                       po_plan= %s
                 WHERE indent_no  = %s
                """,
                (nego_actual, status1, rem, po_plan, indent_no)
            )

            # Update current doer_pending row
            cur.execute(
                """
                UPDATE public.doer_pending
                   SET actual  = %s,
                       status  = %s
                WHERE unique_id  = %s
                   AND doer_empid = %s
                   AND tools_name = %s
                   AND step_name  = %s
                   AND actual IS NULL
                """,
                (nego_actual, status1, rem, indent_no, empid, TOOLS_NAME, "Price negotiation")
            )

            # Insert next step row: "Making PO"
            att1 = _read_file("attachment1")  # optional
            att2 = _read_file("attachment2")  # optional
            _insert_next_step(
                cur,
                indent_no=indent_no,
                step_name="Making purchase order",
                planned_dt=po_plan,
                # doer_name=next_doer,
                doer_name="ANKITA SHEEL",
                # doer_empid=next_doer_empid,
                doer_empid="TT0025",
                attachment1=att1,
                attachment2=att2
            )

            conn.commit()
            flash(f"Price negotiation step updated for {indent_no}. Next step enqueued.", "success")
            return redirect(url_for("purchase.purchase_pending"))
            
        # ====================================================
        # STEP 6: Making purchase order
        # ====================================================
        elif step_name == "Making purchase order":
            status1 = (request.form.get("status1") or "").strip()
            atch = _read_file("atch")
            # rem = (request.form.get("rem")  or "").strip()
                                    
            if status1 not in allowed_status:
                flash("Making purchase order step status.", "danger")
                return redirect(url_for("purchase.purchase_pending"))

            po_actual  = now
            fndaprv_plan  = po_actual + timedelta(days=1)

            # Update purchase
            cur.execute(
                """
                UPDATE public.public.purchase
                   SET po_actual = %s,
                       po_status = %s,
                       atch = %s,
                       fndaprv_plan= %s
                 WHERE indent_no  = %s
                """,
                (po_actual, status1, atch, fndaprv_plan, indent_no)
            )

            # Update current doer_pending row
            cur.execute(
                """
                UPDATE public.doer_pending
                   SET actual  = %s,
                       status  = %s
                WHERE unique_id  = %s
                   AND doer_empid = %s
                   AND tools_name = %s
                   AND step_name  = %s
                   AND actual IS NULL
                """,
                (po_actual, status1, indent_no, empid, TOOLS_NAME, "Making purchase order")
            )

            # Insert next step row: "Approval of fund and payment"
            att1 = _read_file("attachment1")  # optional
            att2 = _read_file("attachment2")  # optional
            _insert_next_step(
                cur,
                indent_no=indent_no,
                step_name="Approval of fund and payment",
                planned_dt=fndaprv_plan,
                # doer_name=next_doer,
                doer_name="RAJORSHI CHAKRABORTY",
                # doer_empid=next_doer_empid,
                doer_empid="TT0005",
                attachment1=att1,
                attachment2=att2
            )

            conn.commit()
            flash(f"Making purchase order step updated for {indent_no}. Next step enqueued.", "success")
            return redirect(url_for("purchase.purchase_pending"))

        # ====================================================
        # STEP 7: Approval of fund and payment
        # ====================================================
        elif step_name == "Approval of fund and payment":
            status1 = (request.form.get("status1") or "").strip()
            atch = _read_file("atch")
            # rem = (request.form.get("rem")  or "").strip()
                                    
            if status1 not in allowed_status:
                flash("Approval of fund and payment step status.", "danger")
                return redirect(url_for("purchase.purchase_pending"))

            fndaprv_actual  = now
            delivery_plan  = fndaprv_actual + timedelta(days=1)

            # Update purchase
            cur.execute(
                """
                UPDATE public.purchase
                   SET fndaprv_actual = %s,
                       fndaprv_status = %s,
                       atch = %s,
                       delivery_plan= %s
                 WHERE indent_no  = %s
                """,
                (fndaprv_actual, status1, atch, delivery_plan, indent_no)
            )

            # Update current doer_pending row
            cur.execute(
                """
                UPDATE public.doer_pending
                   SET actual  = %s,
                       status  = %s
                WHERE unique_id  = %s
                   AND doer_empid = %s
                   AND tools_name = %s
                   AND step_name  = %s
                   AND actual IS NULL
                """,
                (fndaprv_actual, status1, indent_no, empid, TOOLS_NAME, "Approval of fund and payment")
            )

            # Insert next step row: "Confirm delivery at site"
            att1 = _read_file("attachment1")  # optional
            att2 = _read_file("attachment2")  # optional
            _insert_next_step(
                cur,
                indent_no=indent_no,
                step_name="Confirm delivery at site",
                planned_dt=delivery_plan,
                # doer_name=next_doer,
                doer_name="RAJU SHANKHARI",
                # doer_empid=next_doer_empid,
                doer_empid="TT0085",
                attachment1=att1,
                attachment2=att2
            )

            conn.commit()
            flash(f"Approval of fund and payment step updated for {indent_no}. Next step enqueued.", "success")
            return redirect(url_for("purchase.purchase_pending"))
        
        # ====================================================
        # STEP 8: Confirm delivery at site
        # ====================================================
        elif step_name == "Confirm delivery at site":
            status1 = (request.form.get("status1") or "").strip()
            atch = _read_file("atch")
            atch1 = _read_file("atch1")
            atch2 = _read_file("atch2")
            atch3 = _read_file("atch3")
            # rem = (request.form.get("rem")  or "").strip()
                                    
            if status1 not in allowed_status:
                flash("Confirm delivery at site step status.", "danger")
                return redirect(url_for("purchase.purchase_pending"))

            delivery_actual  = now
            qlty_qly_plan  = delivery_actual + timedelta(days=1)

            # Update purchase
            cur.execute(
                """
                UPDATE public.purchase
                   SET delivery_actual = %s,
                       delivery_status = %s,
                       delivery_pht1 = %s,
                       delivery_pht2 = %s,
                       delivery_pht3 = %s,
                       delivery_pht4 = %s,
                       delivery_plan= %s
                 WHERE indent_no  = %s
                """,
                (delivery_actual, status1, atch, atch1, atch2, atch3, delivery_plan, indent_no)
            )

            # Update current doer_pending row
            cur.execute(
                """
                UPDATE public.doer_pending
                   SET actual  = %s,
                       status  = %s
                WHERE unique_id  = %s
                   AND doer_empid = %s
                   AND tools_name = %s
                   AND step_name  = %s
                   AND actual IS NULL
                """,
                (delivery_actual, status1, indent_no, empid, TOOLS_NAME, "Confirm delivery at site")
            )

            # Insert next step row: "Check quality and quantity"
            att1 = _read_file("attachment1")  # optional
            att2 = _read_file("attachment2")  # optional
            _insert_next_step(
                cur,
                indent_no=indent_no,
                step_name="Confirm delivery at site",
                planned_dt=qlty_qly_plan,
                # doer_name=next_doer,
                doer_name="RAJU SHANKHARI",
                # doer_empid=next_doer_empid,
                doer_empid="TT0085",
                attachment1=att1,
                attachment2=att2
            )

            conn.commit()
            flash(f"Confirm delivery at site step updated for {indent_no}. Next step enqueued.", "success")
            return redirect(url_for("purchase.purchase_pending"))

        else:
            flash(f"Unsupported step: {step_name}", "warning")
            return redirect(url_for("purchase.purchase_pending"))

    except Exception as e:
        conn.rollback()
        current_app.logger.exception("Pending update failed for %s", indent_no)
        flash(f"Failed to update: {e}", "danger")
        return redirect(url_for("purchase.purchase_pending"))
    finally:
        cur.close()
        conn.close()