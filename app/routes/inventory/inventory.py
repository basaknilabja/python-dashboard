from flask import render_template, request, redirect, url_for, flash
from datetime import datetime
from psycopg2.extras import RealDictCursor

from app.db import get_db_connection
from . import inventory_bp
from app.decorators import login_required


# =====================================================
# 🔹 View Live Inventory
# =====================================================

@inventory_bp.route("/view_inventory")
@login_required
def view_inventory():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    prod_category = request.args.get("prod_category", "").strip()
    q = request.args.get("q", "").strip()

    # Categories
    cur.execute("""
        SELECT DISTINCT prod_category
        FROM public.sdims
        ORDER BY prod_category
    """)
    categories = [r["prod_category"] for r in cur.fetchall()]

    # Search list
    cur.execute("""
        SELECT part_code, item_name
        FROM public.sdims
        ORDER BY item_name
    """)
    search_list = cur.fetchall()

    # Vendors
    cur.execute("""
        SELECT vendor_name
        FROM public.vendor
        ORDER BY vendor_name
    """)
    vendors = cur.fetchall()

    # PO Numbers
    cur.execute("""
        SELECT po_no
        FROM public.po
        ORDER BY po_date
    """)
    po_no = cur.fetchall()

    # Part items
    cur.execute("""
        SELECT part_code, item_name, actual_qty
        FROM public.sdims
        ORDER BY item_name
    """)
    part_items = cur.fetchall()

    # Contractors
    cur.execute("""
        SELECT contractor_name
        FROM public.contractor
        ORDER BY contractor_id
    """)
    contractors = cur.fetchall()

    # Site Engineers
    cur.execute("""
        SELECT empname
        FROM public.emp_master
        WHERE department = %s
        ORDER BY empname
    """, ("TECHNICAL",))
    site_engineers = cur.fetchall()

    # Inventory list
    query = """
        SELECT prod_category, sub_category, part_code,
               item_name, units, actual_qty
        FROM public.sdims
        WHERE 1=1
    """
    params = []

    if prod_category:
        query += " AND prod_category = %s"
        params.append(prod_category)

    if q:
        query += " AND (part_code ILIKE %s OR item_name ILIKE %s)"
        params.extend([f"%{q}%", f"%{q}%"])

    query += " ORDER BY prod_category, part_code"

    cur.execute(query, params)
    inventory = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "inventory/inventory.html",
        inventory=inventory,
        categories=categories,
        search_list=search_list,
        selected_category=prod_category,
        search_text=q,
        vendors=vendors,
        po_no=po_no,
        part_items=part_items,
        contractors=contractors,
        site_engineers=site_engineers
    )


# =====================================================
# 🔹 Receive Stock
# =====================================================

@inventory_bp.route("/receive", methods=["POST"])
@login_required
def receive_stock():
    conn = get_db_connection()
    cur = conn.cursor()

    part_code = request.form["part_code"]
    qty_receive = float(request.form["qty_receive"])
    rate = float(request.form["rate"])
    amount = float(request.form["amount"])
    gst = request.form.get("gst")
    job = request.form["job"]
    pms_id = request.form.get("pmsid")
    vendor = request.form["vendor_name"]
    po_no = request.form.get("po_no")
    invoice_no = request.form.get("invoice_no")
    challan_no = request.form.get("challan_no")
    delivery_cost = request.form.get("delivery_cost")
    delivery_mode = request.form.get("delivery_mode")
    remarks = request.form["remarks"]

    # Fetch item name
    cur.execute(
        "SELECT item_name FROM public.sdims WHERE part_code=%s",
        (part_code,)
    )
    row = cur.fetchone()
    item_name = row[0] if row else None

    # Insert ledger
    cur.execute("""
        INSERT INTO public.sdims_stockledger
        (part_code, item_name, trans_date, status,
         qty_receive, rate, amount, gst,
         vendor_name, po_number,
         job, pms_unique_id,
         invoice_no, challan_no,
         delivery_cost, delivery_mode, remarks)
        VALUES (
            %s, %s, %s, 'R',
            %s, %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s, %s, %s
        )
    """, (
        part_code, item_name, datetime.now(),
        qty_receive, rate, amount, gst,
        vendor, po_no,
        job, pms_id,
        invoice_no, challan_no,
        delivery_cost, delivery_mode, remarks
    ))

    # Update stock
    cur.execute("""
        UPDATE public.sdims
        SET actual_qty = actual_qty + %s
        WHERE part_code = %s
    """, (qty_receive, part_code))

    conn.commit()
    cur.close()
    conn.close()

    flash("✅ Stock received successfully", "success")
    return redirect(url_for("inventory.view_inventory"))


# =====================================================
# 🔹 Issue Stock
# =====================================================

@inventory_bp.route("/issue", methods=["POST"])
@login_required
def issue_stock():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:
        part_code = request.form["part_code"]
        qty = float(request.form["qty_issue"])
        contractor = request.form.get("contractor_name")
        issued_to = request.form.get("issued_to")
        site_engineer = request.form.get("site_engineer")
        pms_id = request.form.get("pmsid")

        # Check stock
        cur.execute(
            "SELECT actual_qty FROM public.sdims WHERE part_code = %s",
            (part_code,)
        )
        item = cur.fetchone()

        if not item:
            flash("❌ Item not found", "danger")
            return redirect(url_for("inventory.view_inventory"))

        current_stock = float(item["actual_qty"])

        if current_stock < qty:
            flash("❌ Insufficient stock", "danger")
            return redirect(url_for("inventory.view_inventory"))

        # Insert ledger
        cur.execute("""
            INSERT INTO public.sdims_stockledger
            (part_code, trans_date, status, qty_issue,
             contractor_name, issued_to, site_engineer, pms_unique_id)
            VALUES (%s, %s, 'I', %s, %s, %s, %s, %s)
        """, (
            part_code,
            datetime.now(),
            qty,
            contractor,
            issued_to,
            site_engineer,
            pms_id
        ))

        # Update stock
        cur.execute("""
            UPDATE public.sdims
            SET actual_qty = actual_qty - %s
            WHERE part_code = %s
        """, (qty, part_code))

        conn.commit()
        flash("✅ Stock issued successfully", "success")

    except Exception as e:
        conn.rollback()
        flash(f"❌ Error issuing stock: {str(e)}", "danger")

    finally:
        cur.close()
        conn.close()

    return redirect(url_for("inventory.view_inventory"))

# =====================================================
# 🔹 Stock Ledger
# =====================================================

@inventory_bp.route("/stock_ledger")
@login_required
def stock_ledger():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Stockledger
    cur.execute("""
        SELECT part_code, item_name, trans_date, status, qty, units, qty_issue, qty_receive, 
                job, vendor_name, vendor_code, contractor_name, contractor_code, po_number, 
                invoice_no, challan_no, delivery_cost, delivery_mode, person_issue, pms_unique_id, 
                live_stock, unit_cost, amount, remarks, rate FROM public.sdims_stockledger
        ORDER BY vendor_name
    """)
    ledger = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "inventory/stock_ledger.html",
        ledger=ledger)