from flask import render_template, request, jsonify, g, make_response
from datetime import datetime
from app.db import get_db_connection
from . import sales_bp
from app.decorators import login_required
from fpdf import FPDF
import io
from psycopg2.extras import RealDictCursor


# ========================= MAIN SALES REPORTS ========================= #
@sales_bp.route("/reports")
@login_required
def sales_reports():
    try:
        user_id = g.current_user["user_id"]
        empname = g.current_user["empname"]

        print(f"🟢 sales_reports | user_id={user_id} | empname={empname}")

        conn = get_db_connection()
        cur1 = conn.cursor(cursor_factory=RealDictCursor)
        cur2 = conn.cursor(cursor_factory=RealDictCursor)
        cur3 = conn.cursor(cursor_factory=RealDictCursor)

        # -------------------- ADMIN VIEW -------------------- #
        if user_id.lower() == "admin":
            print("🔸 Admin mode: fetching all doer reports")

            # 1️⃣ STATUS SUMMARY (all doers)
            status_query = """
                SELECT 
                    a.doer,
                    a.status,
                    COUNT(a.status) AS total_count,
                    COALESCE((
                        SELECT COUNT(*)
                        FROM public.doer_pending AS b
                        WHERE b.tools_name = a.tools_name
                          AND b.doer = a.doer
                          AND b.status = a.status
                          AND b.actual >= CURDATE()
                          AND b.actual < CURDATE() + INTERVAL 1 DAY
                    ), 0) AS today_count
                FROM public.doer_pending AS a
                WHERE a.tools_name = 'Pre Sales FMS'
                GROUP BY a.doer, a.status
                ORDER BY a.doer, a.status;
            """

            # 2️⃣ TODAY SUMMARY
            summary_query = """
                SELECT 
                    doer,
                    (SELECT COUNT(*) FROM public.doer_pending 
                     WHERE doer = d.doer 
                       AND tools_name = 'Pre Sales FMS' 
                       AND actual >= CURDATE() AND actual < CURDATE() + INTERVAL 1 DAY) AS todays_attempts,
                    (SELECT COUNT(*) FROM public.doer_pending 
                     WHERE doer = d.doer 
                       AND tools_name = 'Pre Sales FMS' 
                       AND planned >= CURDATE() AND planned < CURDATE() + INTERVAL 1 DAY) AS todays_planned
                FROM public.doer_pending AS d
                WHERE d.tools_name = 'Pre Sales FMS'
                GROUP BY doer;
            """

            # 3️⃣ CONSOLIDATED CONVERSION (Month-wise, Doer-wise)
            conversion_query = """
                SELECT 
                    DATE_FORMAT(planned, '%b-%y') AS month_year,
                    doer,
                    COUNT(DISTINCT unique_id) AS total_enquiries,
                    COUNT(DISTINCT CASE WHEN status = 'Active' THEN unique_id END) AS total_active,
                    ROUND((COUNT(DISTINCT CASE WHEN status = 'Active' THEN unique_id END) /
                           COUNT(DISTINCT unique_id)) * 100, 1) AS conversion_ratio
                FROM public.doer_pending
                WHERE tools_name = 'Pre Sales FMS'
                GROUP BY month_year, doer
                ORDER BY month_year, doer;
            """

            # Execute all queries
            cur1.execute(status_query)
            all_rows = cur1.fetchall()

            reports = {}
            for r in all_rows:
                reports.setdefault(r["doer"], []).append(r)

            # Calculate % per doer
            for doer, rows in reports.items():
                total = sum(r["total_count"] for r in rows)
                for r in rows:
                    r["percentage"] = round((r["total_count"] / total) * 100, 1) if total else 0

            cur2.execute(summary_query)
            summaries = {row["doer"]: row for row in cur2.fetchall()}

            cur3.execute(conversion_query)
            conv_data = cur3.fetchall()

            conversions = {}
            for r in conv_data:
                month = r["month_year"]
                conversions.setdefault(month, []).append(r)

            generated_on = datetime.now().strftime("%d-%b-%Y %I:%M %p")

            print(f"✅ Render admin view | doers={len(reports)} | months={len(conversions)}")

            return render_template(
                "sales/sales_reports.html",
                reports=reports,
                summaries=summaries,
                conversions=conversions,
                generated_on=generated_on
            )

        # -------------------- USER VIEW -------------------- #
        else:
            print(f"🔹 User mode: fetching report for {empname}")

            user_status_query = """
                SELECT 
                    status,
                    COUNT(status) AS total_count,
                    COALESCE((
                        SELECT COUNT(*) FROM public.doer_pending b
                        WHERE b.doer = a.doer
                          AND b.status = a.status
                          AND b.actual >= CURDATE()
                          AND b.actual < CURDATE() + INTERVAL 1 DAY
                    ), 0) AS today_count
                FROM public.doer_pending a
                WHERE a.tools_name = 'Pre Sales FMS' AND a.doer = %s
                GROUP BY status ORDER BY status;
            """

            user_summary_query = """
                SELECT 
                    doer,
                    (SELECT COUNT(*) FROM public.doer_pending 
                     WHERE doer = %s 
                       AND tools_name = 'Pre Sales FMS' 
                       AND actual >= CURDATE() AND actual < CURDATE() + INTERVAL 1 DAY) AS todays_attempts,
                    (SELECT COUNT(*) FROM public.doer_pending 
                     WHERE doer = %s 
                       AND tools_name = 'Pre Sales FMS' 
                       AND planned >= CURDATE() AND planned < CURDATE() + INTERVAL 1 DAY) AS todays_planned
                FROM public.doer_pending
                WHERE doer = %s
                LIMIT 1;
            """

            cur1.execute(user_status_query, (empname,))
            user_rows = cur1.fetchall()

            reports = {empname: user_rows}
            total = sum(r["total_count"] for r in user_rows)
            for r in user_rows:
                r["percentage"] = round((r["total_count"] / total) * 100, 1) if total else 0

            cur2.execute(user_summary_query, (empname, empname, empname))
            summaries = {empname: cur2.fetchone()}

            generated_on = datetime.now().strftime("%d-%b-%Y %I:%M %p")

            return render_template(
                "sales/sales_report_user.html",
                reports=reports,
                summaries=summaries,
                generated_on=generated_on
            )

    except Exception as e:
        print(f"❌ Error in sales_reports: {e}")
        return "Internal Server Error", 500

    finally:
        try:
            cur1.close(); cur2.close(); cur3.close(); conn.close()
        except Exception:
            pass


# ========================= STATUS DETAILS (AJAX) ========================= #
@sales_bp.route("/sales/status_details", methods=["POST"])
@login_required
def status_details():
    """Fetch enquiry list when clicking a status cell or summary box"""
    try:
        data = request.get_json()
        status = data.get("status")
        doer = data.get("doer")

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        user_id = g.current_user["user_id"]
        empname = g.current_user["empname"]

        base_query = """
            SELECT 
                dp.unique_id AS lead_id,
                ps.name AS customer_name,
                dp.doer,
                dp.status,
                dp.planned,
                dp.actual,
                dp.last_status,
                dp.last_remarks
            FROM public.doer_pending dp
            LEFT JOIN public.presales ps ON ps.leadsid = dp.unique_id
            WHERE dp.tools_name = 'Pre Sales FMS'
        """

        params = []

        # ✅ 1️⃣ Consolidated admin view
        if doer == "All" and user_id.lower() == "admin":
            if status.lower() == "active":
                base_query += " AND dp.status = 'Active'"
            elif status.lower() == "pending":
                base_query += " AND dp.actual IS NULL"
            elif status.lower() == "attempts":
                base_query += " AND dp.actual IS NOT NULL"
            base_query += " ORDER BY dp.planned DESC"
            cur.execute(base_query)

        # ✅ 2️⃣ Admin individual doer or user block
        else:
            base_query += " AND dp.doer = %s"
            params.append(doer if user_id.lower() == "admin" else empname)

            if status.lower() == "active":
                base_query += " AND dp.status = 'Active'"
            elif status.lower() == "pending":
                base_query += " AND dp.actual IS NULL"
            elif status.lower() == "attempts":
                base_query += " AND dp.actual IS NOT NULL"
            else:
                base_query += " AND dp.status = %s"
                params.append(status)

            base_query += " ORDER BY dp.planned DESC"
            cur.execute(base_query, tuple(params))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        # ✅ Format dates
        for r in rows:
            if r["planned"]:
                r["planned"] = r["planned"].strftime("%d-%m-%y %H:%M")
            if r["actual"]:
                r["actual"] = r["actual"].strftime("%d-%m-%y %H:%M")

        return jsonify(rows)

    except Exception as e:
        print(f"❌ Error in status_details: {e}")
        return jsonify([]), 500

@sales_bp.route("/reports_analysis")
@login_required
def reports_analysis():
    """
    Enquiry Source Report with optional date filters.
    User selects start_date & end_date.
    PDF auto-downloads based on the selected range.
    """
    try:
        # ------------------- GET DATE RANGE FROM USER ------------------- #
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        # If no date range → show the date selector page
        if not start_date or not end_date:
            return render_template("sales/reports_analysis.html")

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # ------------------- FILTERED QUERY ------------------- #
        query = """
            SELECT 
                ANY_VALUE(project) AS `Project Name`,
                lead_date AS `Lead Date`,
                source AS `Source Name`,
                COUNT(*) AS `Total`
            FROM public.presales
            WHERE lead_date BETWEEN %s AND %s
            GROUP BY lead_date, source
            ORDER BY lead_date, source;
        """

        cur.execute(query, (start_date, end_date))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            return f"No records found between {start_date} and {end_date}", 404

        from fpdf import FPDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)

        period_text = f"{start_date} to {end_date}"

        # ----------- HEADER ------------ #
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, "Team Taurus", ln=True, align="C")

        pdf.set_font("Arial", "B", 14)
        pdf.cell(0, 10, "Enquiry Source Report", ln=True, align="C")

        pdf.set_font("Arial", "", 12)
        pdf.cell(0, 10, f"Period: {period_text}", ln=True, align="C")
        pdf.ln(10)

        # ------------- COLUMN WIDTHS ---------------- #
        w_project = 40
        w_date = 35
        w_source = 55
        w_total = 25

        total_width = w_project + w_date + w_source + w_total
        table_x = (pdf.w - total_width) / 2

        # ------------ TABLE HEADER -------------- #
        pdf.set_font("Arial", "B", 12)
        pdf.set_fill_color(50, 50, 50)
        pdf.set_text_color(255, 255, 255)

        pdf.set_x(table_x)
        pdf.cell(w_project, 10, "Project", 1, 0, "C", True)
        pdf.cell(w_date, 10, "Lead Date", 1, 0, "C", True)
        pdf.cell(w_source, 10, "Source", 1, 0, "C", True)
        pdf.cell(w_total, 10, "Total", 1, 1, "C", True)

        # ---------- TABLE BODY ------------- #
        pdf.set_font("Arial", "", 11)
        pdf.set_text_color(0, 0, 0)
        fill = False

        pdf.set_fill_color(235, 235, 235)

        for row in rows:
            lead_date = row["Lead Date"].strftime("%d-%m-%Y")

            pdf.set_x(table_x)
            pdf.cell(w_project, 10, str(row["Project Name"]), 1, 0, "C", fill)
            pdf.cell(w_date, 10, lead_date, 1, 0, "C", fill)
            pdf.cell(w_source, 10, str(row["Source Name"]), 1, 0, "C", fill)
            pdf.cell(w_total, 10, str(row["Total"]), 1, 1, "C", fill)
            fill = not fill

        pdf.ln(8)
        pdf.set_font("Arial", "I", 10)
        pdf.cell(0, 10, "Auto-generated by Team Taurus PMS", ln=True, align="R")

        pdf_bytes = pdf.output(dest="S").encode("latin1")

        from flask import make_response
        response = make_response(pdf_bytes)
        response.headers["Content-Type"] = "application/pdf"
        response.headers[
            "Content-Disposition"
        ] = f"attachment; filename=Enquiry_Source_Report_{start_date}_to_{end_date}.pdf"

        return response

    except Exception as e:
        print(f"❌ Error in reports_analysis: {e}")
        return "Internal Server Error", 500

from flask import render_template, request, make_response
from datetime import datetime
from fpdf import FPDF

# (place this inside your sales blueprint file where other routes exist)

@sales_bp.route("/reports_analysis_calling_status")
@login_required
def reports_analysis_calling_status():
    """
    Calling Status report (PDF download).
    Includes last_interaction in PDF.
    """
    try:
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        # If no date range provided, show form
        if not start_date or not end_date:
            return render_template("sales/calling_status_report.html")

        # Validate date range
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d").date()
            ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            return "Invalid date format. Use YYYY-MM-DD.", 400

        if sd > ed:
            return "Start date cannot be greater than end date.", 400

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # NOW ADDED last_interaction
        query = """
            SELECT
                COALESCE(presales_person, '') AS presales_person,
                COALESCE(last_status, '') AS last_status,
                COALESCE(last_remarks, '') AS last_remarks,
                COALESCE(name, '') AS customer_name,
                lead_date,
                last_interaction
            FROM public.presales
            WHERE lead_date IS NOT NULL
              AND DATE(lead_date) BETWEEN %s AND %s
            ORDER BY presales_person, DATE(lead_date) DESC;
        """
        cur.execute(query, (start_date, end_date))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            return f"No records found between {start_date} and {end_date}", 404

        # ---------------------------------------------------
        # PDF GENERATION
        # ---------------------------------------------------
        pdf = FPDF(orientation="P", unit="mm", format="A4")
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=12)

        generated_on = datetime.now().strftime("%d-%b-%Y %I:%M %p")
        period = f"{sd.strftime('%d-%m-%Y')} to {ed.strftime('%d-%m-%Y')}"

        # ----- TITLE -----
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 8, "Team Taurus", ln=True, align="C")
        pdf.ln(2)

        pdf.set_font("Arial", "B", 14)
        pdf.cell(0, 8, "Calling Status Report", ln=True, align="C")
        pdf.ln(2)

        pdf.set_font("Arial", "", 11)
        pdf.cell(0, 6, f"Period: {period}    |    Generated: {generated_on}", ln=True, align="C")
        pdf.ln(6)

        # ---------------------------------------------------
        # COLUMN WIDTHS (Adjusted for last_interaction)
        # ---------------------------------------------------
        w_doer = 30
        w_status = 30
        w_remarks = 55
        w_name = 35
        w_lead = 20
        w_lastint = 22   # <--- NEW COLUMN

        table_width = w_doer + w_status + w_remarks + w_name + w_lead + w_lastint

        page_width = pdf.w - pdf.l_margin - pdf.r_margin
        table_x = (page_width - table_width) / 2 + pdf.l_margin

        # ---------------------------------------------------
        # TABLE HEADER
        # ---------------------------------------------------
        def print_header():
            pdf.set_x(table_x)
            pdf.set_font("Arial", "B", 9)
            pdf.set_fill_color(60, 60, 60)
            pdf.set_text_color(255, 255, 255)

            pdf.cell(w_doer, 8, "Doer", 1, 0, "C", True)
            pdf.cell(w_status, 8, "Status", 1, 0, "C", True)
            pdf.cell(w_remarks, 8, "Remarks", 1, 0, "C", True)
            pdf.cell(w_name, 8, "Customer", 1, 0, "C", True)
            pdf.cell(w_lead, 8, "Lead Dt", 1, 0, "C", True)
            pdf.cell(w_lastint, 8, "Last Int.", 1, 1, "C", True)

        print_header()

        # ---------------------------------------------------
        # TABLE BODY
        # ---------------------------------------------------
        pdf.set_font("Arial", "", 9)
        pdf.set_text_color(0, 0, 0)
        fill = False
        pdf.set_fill_color(245, 245, 245)

        for r in rows:

            # Format dates
            lead_dt = ""
            if r.get("lead_date"):
                try:
                    lead_dt = datetime.strptime(str(r["lead_date"])[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
                except:
                    lead_dt = str(r["lead_date"])

            last_int = ""
            if r.get("last_interaction"):
                try:
                    last_int = datetime.strptime(str(r["last_interaction"])[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
                except:
                    last_int = str(r["last_interaction"])

            remarks_text = (r.get("last_remarks") or "").replace("\n", " ").strip()

            pdf.set_x(table_x)
            pdf.cell(w_doer, 7, (r["presales_person"] or "")[:25], 1, 0, "C", fill)
            pdf.cell(w_status, 7, (r["last_status"] or "")[:25], 1, 0, "C", fill)
            pdf.cell(w_remarks, 7, remarks_text[:70], 1, 0, "L", fill)
            pdf.cell(w_name, 7, (r["customer_name"] or "")[:25], 1, 0, "C", fill)
            pdf.cell(w_lead, 7, lead_dt, 1, 0, "C", fill)
            pdf.cell(w_lastint, 7, last_int, 1, 1, "C", fill)

            fill = not fill

            # PAGE BREAK HANDLING WITH HEADER REPEAT
            if pdf.get_y() > pdf.h - pdf.b_margin - 20:
                pdf.add_page()
                print_header()
                pdf.set_font("Arial", "", 9)
                pdf.set_text_color(0, 0, 0)

        # FOOTER
        pdf.ln(5)
        pdf.set_font("Arial", "I", 9)
        pdf.cell(0, 6, "Auto-generated by Team Taurus", ln=True, align="R")

        # DOWNLOAD PDF
        pdf_bytes = pdf.output(dest="S").encode("latin1")
        response = make_response(pdf_bytes)
        response.headers["Content-Type"] = "application/pdf"
        filename = f"Calling_Status_{start_date}_to_{end_date}.pdf"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"

        return response

    except Exception as e:
        print(f"❌ Error in reports_analysis_calling_status: {e}")
        return "Internal Server Error", 500

@sales_bp.route("/reports_analysis_active_status")
@login_required
def reports_analysis_active_status():
    """
    Doer-wise Active Status report:
    Shows active leads (status='Active') with:
      - Doer Name  (from public.doer_pending)
      - Source     (from public.presales)
      - Customer   (from public.presales)
      - Status
      - Sales Person (from public.presales)
      - Last Interaction Date

    With:
      - Doer-wise detail
      - Subtotals
      - Final summary grouped by doer, source, sales_person
    """

    try:
        from fpdf import FPDF
        from flask import make_response

        # ------------------ READ DATE RANGE ------------------
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        # If user did NOT enter dates, show date selection page
        if not start_date or not end_date:
            return render_template("sales/active_status_report.html")

        # Validate format
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d").date()
            ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            return "Invalid date format. Use YYYY-MM-DD", 400

        if sd > ed:
            return "Start Date must be <= End Date", 400

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # ------------------ MAIN DETAILED QUERY ------------------
        detailed_sql = """
            SELECT
                dp.doer AS doer_name,
                ps.source AS source_name,
                ps.name AS customer_name,
                dp.status AS status,
                ps.sales_person AS sales_person,
                dp.last_interaction
            FROM public.doer_pending dp
            LEFT JOIN public.presales ps ON ps.leadsid = dp.unique_id
            WHERE dp.status = 'Active'
              AND dp.last_interaction IS NOT NULL
              AND DATE(dp.last_interaction) BETWEEN %s AND %s
            ORDER BY dp.doer, dp.last_interaction DESC;
        """

        cur.execute(detailed_sql, (start_date, end_date))
        detailed_rows = cur.fetchall()

        # ------------------ SUMMARY QUERY ------------------
        summary_sql = """
            SELECT
                dp.doer AS doer_name,
                ps.source AS source_name,
                ps.sales_person AS sales_person,
                COUNT(*) AS total_active
            FROM public.doer_pending dp
            LEFT JOIN public.presales ps ON ps.leadsid = dp.unique_id
            WHERE dp.status = 'Active'
              AND dp.last_interaction IS NOT NULL
              AND DATE(dp.last_interaction) BETWEEN %s AND %s
            GROUP BY dp.doer, ps.source, ps.sales_person
            ORDER BY dp.doer, ps.source, ps.sales_person;
        """

        cur.execute(summary_sql, (start_date, end_date))
        summary_rows = cur.fetchall()

        cur.close()
        conn.close()

        if not detailed_rows:
            return "No Active status data found in given range", 404

        # =============================================================
        #                    PDF GENERATION
        # =============================================================
        pdf = FPDF("P", "mm", "A4")
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=12)

        generated_on = datetime.now().strftime("%d-%b-%Y %I:%M %p")
        period = f"{sd.strftime('%d-%m-%Y')} to {ed.strftime('%d-%m-%Y')}"

        # ------------------ TITLE ------------------
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, "Team Taurus", ln=True, align="C")
        pdf.set_font("Arial", "B", 14)
        pdf.cell(0, 8, "Doer-wise Active Status Report", ln=True, align="C")
        pdf.set_font("Arial", "", 11)
        pdf.cell(0, 7, f"Period: {period} | Generated on: {generated_on}", ln=True, align="C")
        pdf.ln(6)

        # ------------------ COLUMN WIDTHS ------------------
        w_doer = 30
        w_source = 35
        w_customer = 45
        w_status = 25
        w_sales = 35
        w_date = 22

        table_width = w_doer + w_source + w_customer + w_status + w_sales + w_date
        page_width = pdf.w - pdf.l_margin - pdf.r_margin
        table_x = (page_width - table_width) / 2 + pdf.l_margin

        # ------------------ HEADER FUNCTION ------------------
        def draw_header():
            pdf.set_font("Arial", "B", 10)
            pdf.set_fill_color(60, 60, 60)
            pdf.set_text_color(255, 255, 255)
            pdf.set_x(table_x)
            pdf.cell(w_doer, 8, "Doer", 1, 0, "C", True)
            pdf.cell(w_source, 8, "Source", 1, 0, "C", True)
            pdf.cell(w_customer, 8, "Customer", 1, 0, "C", True)
            pdf.cell(w_status, 8, "Status", 1, 0, "C", True)
            pdf.cell(w_sales, 8, "Sales Person", 1, 0, "C", True)
            pdf.cell(w_date, 8, "Last Interaction", 1, 1, "C", True)

        draw_header()

        # ------------------ PRINT ROWS ------------------
        pdf.set_font("Arial", "", 9)
        pdf.set_text_color(0, 0, 0)
        fill = False

        current_doer = None
        count_subtotal = 0

        for r in detailed_rows:

            # If new doer → add subtotal
            if current_doer and r["doer_name"] != current_doer:
                pdf.set_x(table_x)
                pdf.set_font("Arial", "B", 10)
                pdf.cell(table_width, 7, f"Subtotal for {current_doer}: {count_subtotal}", 1, 1, "R")
                pdf.ln(3)
                draw_header()
                pdf.set_font("Arial", "", 9)
                count_subtotal = 0

            current_doer = r["doer_name"]
            count_subtotal += 1

            # Format date
            last_int = ""
            if r["last_interaction"]:
                try:
                    last_int = datetime.strptime(
                        str(r["last_interaction"])[:10], "%Y-%m-%d"
                    ).strftime("%d-%m-%Y")
                except:
                    last_int = str(r["last_interaction"])

            # Row
            pdf.set_x(table_x)
            pdf.cell(w_doer, 7, (r["doer_name"] or "")[:20], 1, 0, "C", fill)
            pdf.cell(w_source, 7, (r["source_name"] or "")[:20], 1, 0, "C", fill)
            pdf.cell(w_customer, 7, (r["customer_name"] or "")[:30], 1, 0, "L", fill)
            pdf.cell(w_status, 7, (r["status"] or "")[:20], 1, 0, "C", fill)
            pdf.cell(w_sales, 7, (r["sales_person"] or "")[:25], 1, 0, "C", fill)
            pdf.cell(w_date, 7, last_int, 1, 1, "C", fill)
            fill = not fill

            # Page overflow handling
            if pdf.get_y() > pdf.h - 20:
                pdf.add_page()
                draw_header()

        # Final subtotal
        pdf.set_font("Arial", "B", 10)
        pdf.set_x(table_x)
        pdf.cell(table_width, 7, f"Subtotal for {current_doer}: {count_subtotal}", 1, 1, "R")
        pdf.ln(8)

        # =============================================================
        #                FINAL SUMMARY TABLE
        # =============================================================
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, "Summary (Grouped)", ln=True, align="C")
        pdf.ln(3)

        # Summary widths
        w_d = 35
        w_s = 40
        w_sp = 40
        w_cnt = 30

        summary_width = w_d + w_s + w_sp + w_cnt
        sum_x = (page_width - summary_width) / 2 + pdf.l_margin

        # summary header
        pdf.set_font("Arial", "B", 10)
        pdf.set_x(sum_x)
        pdf.set_fill_color(70, 70, 70)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(w_d, 8, "Doer", 1, 0, "C", True)
        pdf.cell(w_s, 8, "Source", 1, 0, "C", True)
        pdf.cell(w_sp, 8, "Sales Person", 1, 0, "C", True)
        pdf.cell(w_cnt, 8, "Active Count", 1, 1, "C", True)

        pdf.set_font("Arial", "", 9)
        pdf.set_text_color(0, 0, 0)
        fill = False

        for r in summary_rows:
            pdf.set_x(sum_x)
            pdf.cell(w_d, 7, (r["doer_name"] or "")[:20], 1, 0, "C", fill)
            pdf.cell(w_s, 7, (r["source_name"] or "")[:25], 1, 0, "C", fill)
            pdf.cell(w_sp, 7, (r["sales_person"] or "")[:25], 1, 0, "C", fill)
            pdf.cell(w_cnt, 7, str(r["total_active"]), 1, 1, "C", fill)
            fill = not fill

        # Footer
        pdf.ln(6)
        pdf.set_font("Arial", "I", 9)
        pdf.cell(0, 6, "Auto-generated by Team Taurus", ln=True, align="R")

        # ------------------ DOWNLOAD RESPONSE ------------------
        pdf_bytes = pdf.output(dest="S").encode("latin1")
        resp = make_response(pdf_bytes)
        resp.headers["Content-Type"] = "application/pdf"
        resp.headers[
            "Content-Disposition"
        ] = f"attachment; filename=Active_Status_{start_date}_to_{end_date}.pdf"

        return resp

    except Exception as e:
        print("❌ Error in reports_analysis_active_status:", e)
        return "Internal Server Error", 500
