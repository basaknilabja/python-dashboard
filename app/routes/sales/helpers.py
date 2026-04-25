# app/routes/sales/helpers.py
from flask import flash, render_template, request
from datetime import datetime, timedelta
from app.db import get_db_connection
from psycopg2.extras import RealDictCursor

def calculate_next_plan(status, actual_time=None, tat_days=None, site_visit_datetime=None):
    """Determine next plan datetime based on current status."""
    if actual_time is None:
        actual_time = datetime.now()

    # ✅ Fix: handle None or empty status safely
    if not status:
        print("⚠️ No status provided, skipping next plan calculation.")
        return None

    status = status.strip().lower()

    # Condition 1 — Switched off / Disconnected / Unanswered
    if status in ["switched off", "call disconnected", "unanswered"]:
        return actual_time + timedelta(hours=19)

    # Condition 2 — Duplicate / Incorrect no / Not interested / Budget mismatch / Location mismatch / Vendor
    elif status in ["duplicate", "incorrect no", "not interested", "budget mismatch", "location mismatch", "vendor"]:
        return None

    # Condition 3 — Ongoing / Ask to call back later
    elif status in ["ongoing communication", "ask to call back later"]:
        return actual_time + timedelta(days=tat_days or 1)

    # Condition 4 — Site visit scheduled
    elif status == "site visit scheduled":
        return site_visit_datetime

    # Condition 5 — Active after site visit
    elif status == "active":
        return actual_time + timedelta(days=1)

    # Default
    return actual_time + timedelta(hours=24)


def update_missing_plan1():
    """
    Find all presales records where plan1 is NULL
    and set plan1 = timestamp + 45 minutes.
    """
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    time_stamp1=datetime.now()

    cur.execute("SELECT leadsid FROM public.presales WHERE plan1 IS NULL")
    raw_rows = cur.fetchall()

    updated_count = 0
    for row in raw_rows:
        if row["leadsid"]:
            plan1_time = time_stamp1 + timedelta(minutes=45)
            cur.execute(
                "UPDATE public.presales SET timestamp=%s, plan1 = %s WHERE leadsid = %s",
                (time_stamp1,plan1_time, row["leadsid"]),
            )
            updated_count += 1

    if updated_count > 0:
        conn.commit()

    cur.close()
    conn.close()
    
    return updated_count

def presales_update_followup(empid, empname, leadsid, cur, conn):
    """
    Finds the actual pending lead for this presales person.
    Returns (leadsid, step_name)
    """
    # Auto-detect lead if not provided
    if not leadsid or leadsid == "TEMP":
        cur.execute("""
            SELECT unique_id, step_name
            FROM public.doer_pending
            WHERE doer_empid = %s AND tools_name = %s
              AND actual IS NULL
            ORDER BY planned ASC LIMIT 1
        """, (empid, "Pre Sales FMS"))
        row = cur.fetchone()
        if not row:
            return None, None
        leadsid = row["unique_id"]
        print(f"🟢 Auto-selected leadsid: {leadsid}")
    else:
        print(f"🟢 Using provided leadsid: {leadsid}")

    # Get current step
    cur.execute("""
        SELECT step_name
        FROM public.doer_pending
        WHERE unique_id = %s AND doer_empid = %s
          AND tools_name = %s AND actual IS NULL
        ORDER BY planned ASC LIMIT 1
    """, (leadsid, empid, "Pre Sales FMS"))
    step = cur.fetchone()
    step_name = step["step_name"] if step else "N/A"

    return leadsid, step_name


def presales_update_form_submission(leadsid,empid,cur):
    """Extracts and returns follow-up form data safely."""
    status = request.form.get("status")
    remarks = request.form.get("remarks")
    whatsapp_send = request.form.get("whatsapp_send")
    tat_days = request.form.get("tat", type=int)
    site_visit_datetime = request.form.get("site_visit_datetime")
    actual_time = datetime.now()

    # Update public.doer_pending table
    cur.execute("""
        UPDATE public.doer_pending
        SET actual = %s, status = %s, remarks = %s
        WHERE unique_id = %s AND doer_empid = %s AND actual IS NULL
        """, (actual_time, status, remarks, leadsid, empid))
    

    return status, remarks, whatsapp_send, tat_days, site_visit_datetime, actual_time

def ordinal_suffix(n: int) -> str:
    """Return an integer with its English ordinal suffix (1 → 1st, 2 → 2nd, 3 → 3rd, 4 → 4th, etc.)."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
