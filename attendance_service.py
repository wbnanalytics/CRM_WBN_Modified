import re
from calendar import monthrange
from datetime import date, timedelta

from database import db_connection, utc_now


STATUS_LABELS = {
    "P": "Present",
    "A": "Absent",
    "PL": "Planned Leave",
    "UPL": "Unplanned Leave",
    "SL": "Sick Leave",
    "MAT": "Maternity Leave",
    "PAT": "Paternity Leave",
    "CO": "Comp Off",
    "FL": "Floating Leave",
    "PH": "Public Holiday",
    "RH": "Restricted Holiday",
    "BL": "Bereavement Leave",
    "WO": "Week Off",
    "WFH": "Work From Home",
    "Offsite": "Offsite",
    "HD-PL": "Half-day Planned Leave",
    "UL": "Unplanned Leave",
    "NA": "Not Applicable",
}

PRESENT_CODES = {"P", "WFH", "Offsite"}
PLANNED_CODES = {"PL", "MAT", "PAT", "CO", "FL", "HD-PL"}
UNPLANNED_CODES = {"A", "UPL", "SL", "BL", "UL"}
NON_WORKING_CODES = {"WO", "PH", "RH", "NA"}
PL_DEDUCTION_CODES = {"PL", "UPL", "MAT", "PAT", "CO", "FL", "PH", "BL"}


def leave_deduction(code):
    if code == "SL":
        return (0.0, 1.0)
    if code == "HD-PL":
        return (0.5, 0.0)
    if code in PL_DEDUCTION_CODES:
        return (1.0, 0.0)
    return (0.0, 0.0)


def employee_key(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def import_attendance_snapshot(source_name, employees, records, range_start, range_end, imported_by="system"):
    now = utc_now()
    with db_connection() as db:
        db.executemany(
            """INSERT INTO attendance_employees
               (employee_key, source_employee_id, employee_name, role, employment_status,
                pl_balance, sl_balance, sort_order, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(employee_key) DO UPDATE SET
                 source_employee_id=excluded.source_employee_id,
                 employee_name=excluded.employee_name,
                 role=excluded.role,
                 employment_status=excluded.employment_status,
                 pl_balance=excluded.pl_balance,
                 sl_balance=excluded.sl_balance,
                 sort_order=excluded.sort_order,
                 updated_at=excluded.updated_at""",
            [
                (
                    item["employee_key"], item.get("source_employee_id"), item["employee_name"],
                    item["role"], item["employment_status"], item.get("pl_balance"),
                    item.get("sl_balance"), item["sort_order"], now,
                )
                for item in employees
            ],
        )
        before = db.total_changes
        db.executemany(
            """INSERT INTO attendance_records
               (employee_key, attendance_date, status_code, source, updated_by, updated_at)
               VALUES (?, ?, ?, 'workbook', ?, ?)
               ON CONFLICT(employee_key, attendance_date) DO NOTHING""",
            [(item["employee_key"], item["attendance_date"], item["status_code"], imported_by, now) for item in records],
        )
        saved = db.total_changes - before
        db.execute(
            """INSERT INTO attendance_imports
               (source_name, range_start, range_end, employees_imported, records_imported,
                imported_by, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source_name, range_start, range_end, len(employees), saved, imported_by, now),
        )
    return {"employees": len(employees), "records": saved, "range_start": range_start, "range_end": range_end}


def latest_attendance_import():
    with db_connection() as db:
        row = db.execute("SELECT * FROM attendance_imports ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def set_attendance(employee, attendance_date, status_code, updated_by):
    if status_code not in STATUS_LABELS and status_code != "":
        raise ValueError("Unknown attendance status")
    date.fromisoformat(attendance_date)
    now = utc_now()
    with db_connection() as db:
        person = db.execute(
            "SELECT pl_balance,sl_balance FROM attendance_employees WHERE employee_key=?", (employee,),
        ).fetchone()
        if not person:
            raise ValueError("Employee not found")
        previous = db.execute(
            "SELECT status_code FROM attendance_records WHERE employee_key=? AND attendance_date=?",
            (employee, attendance_date),
        ).fetchone()
        old_pl, old_sl = leave_deduction(previous["status_code"] if previous else "")
        new_pl, new_sl = leave_deduction(status_code)
        pl_balance = float(person["pl_balance"] or 0) + old_pl - new_pl
        sl_balance = float(person["sl_balance"] or 0) + old_sl - new_sl
        if pl_balance < 0 or sl_balance < 0:
            raise ValueError("Insufficient PL or SL balance for this attendance selection")
        db.execute("UPDATE attendance_employees SET pl_balance=?,sl_balance=?,updated_at=? WHERE employee_key=?",
                   (pl_balance, sl_balance, now, employee))
        db.execute(
            """INSERT INTO attendance_records
               (employee_key, attendance_date, status_code, source, updated_by, updated_at)
               VALUES (?, ?, ?, 'portal', ?, ?)
               ON CONFLICT(employee_key, attendance_date) DO UPDATE SET
                 status_code=excluded.status_code, source='portal',
                 updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
            (employee, attendance_date, status_code, updated_by, now),
        )
    return {"employee_key": employee, "attendance_date": attendance_date, "status_code": status_code,
            "pl_balance": pl_balance, "sl_balance": sl_balance, "updated_at": now}


def set_leave_balance(employee, pl_balance, sl_balance, updated_by):
    def clean(value, label):
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} balance must be a number") from exc
        if number < 0 or number > 365:
            raise ValueError(f"{label} balance must be between 0 and 365")
        return str(int(number)) if number.is_integer() else str(round(number, 2))
    pl, sl = clean(pl_balance, "PL"), clean(sl_balance, "SL")
    now = utc_now()
    with db_connection() as db:
        updated = db.execute(
            """UPDATE attendance_employees SET pl_balance=?, sl_balance=?, updated_at=?
               WHERE employee_key=?""", (pl, sl, now, employee),
        ).rowcount
        if not updated:
            raise ValueError("Employee not found")
    return {"employee_key": employee, "pl_balance": pl, "sl_balance": sl,
            "updated_by": updated_by, "updated_at": now}


def attendance_month(anchor, today=None):
    today = today or date.today()
    start = date(anchor.year, anchor.month, 1)
    end = date(anchor.year, anchor.month, monthrange(anchor.year, anchor.month)[1])
    dates = [start + timedelta(days=index) for index in range((end - start).days + 1)]
    latest_import = latest_attendance_import()
    default_from = date.fromisoformat(latest_import["range_end"]) if latest_import else today
    with db_connection() as db:
        employee_rows = db.execute(
            """SELECT employee_key, source_employee_id, employee_name, role, employment_status,
                      pl_balance, sl_balance, sort_order
               FROM attendance_employees ORDER BY sort_order, employee_name"""
        ).fetchall()
        record_rows = db.execute(
            """SELECT employee_key, attendance_date, status_code, source, updated_by, updated_at
               FROM attendance_records WHERE attendance_date BETWEEN ? AND ?""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        updated = db.execute(
            """SELECT MAX(updated_at) AS updated_at FROM (
                 SELECT updated_at FROM attendance_records
                 UNION ALL SELECT updated_at FROM attendance_employees
               )"""
        ).fetchone()["updated_at"]
    records = {(row["employee_key"], row["attendance_date"]): dict(row) for row in record_rows}
    employees = []
    daily = {day.isoformat(): {"rostered": 0, "present": 0, "planned": 0, "unplanned": 0, "off": 0} for day in dates}
    totals = {"present": 0, "planned": 0, "unplanned": 0, "off": 0}
    for employee in employee_rows:
        item = dict(employee)
        item["active"] = item["employment_status"] == "Active"
        item["days"] = []
        for day in dates:
            key = (item["employee_key"], day.isoformat())
            saved = records.get(key)
            code = saved["status_code"] if saved else None
            virtual = False
            if not code and item["active"] and default_from <= day <= today:
                code, virtual = "P", True
            item["days"].append({
                "date": day.isoformat(), "code": code or "", "label": STATUS_LABELS.get(code, code or "Not marked"),
                "virtual": virtual,
            })
            if not item["active"] or day > today:
                continue
            daily_item = daily[day.isoformat()]
            daily_item["rostered"] += 1
            if code in PRESENT_CODES:
                daily_item["present"] += 1
                totals["present"] += 1
            elif code in PLANNED_CODES:
                daily_item["planned"] += 1
                totals["planned"] += 1
            elif code in UNPLANNED_CODES:
                daily_item["unplanned"] += 1
                totals["unplanned"] += 1
            elif code in NON_WORKING_CODES:
                daily_item["off"] += 1
                totals["off"] += 1
        employees.append(item)
    attended_base = totals["present"] + totals["planned"] + totals["unplanned"]
    totals["attendance_rate"] = round(totals["present"] / attended_base * 100, 1) if attended_base else 0
    latest_update = max(filter(None, [updated, latest_import.get("imported_at") if latest_import else None]), default=None)
    return {
        "start": start, "end": end, "dates": dates, "employees": employees, "daily": daily,
        "totals": totals, "latest_import": latest_import, "last_updated": latest_update,
        "status_labels": STATUS_LABELS,
    }


def attendance_period(start, end, today=None):
    today = today or date.today()
    effective_end = min(end, today)
    dates = [] if start > effective_end else [start + timedelta(days=index) for index in range((effective_end - start).days + 1)]
    latest_import = latest_attendance_import()
    default_from = date.fromisoformat(latest_import["range_end"]) if latest_import else today
    with db_connection() as db:
        employee_rows = db.execute(
            """SELECT employee_key, employee_name, role, employment_status
               FROM attendance_employees ORDER BY sort_order, employee_name"""
        ).fetchall()
        record_rows = db.execute(
            """SELECT employee_key, attendance_date, status_code
               FROM attendance_records WHERE attendance_date BETWEEN ? AND ?""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        updated = db.execute("SELECT MAX(updated_at) AS updated_at FROM attendance_records").fetchone()["updated_at"]
    records = {(row["employee_key"], row["attendance_date"]): row["status_code"] for row in record_rows}
    rows = []
    totals = {"present": 0, "planned": 0, "unplanned": 0, "off": 0}
    for employee_row in employee_rows:
        employee = dict(employee_row)
        if employee["employment_status"] != "Active":
            continue
        counts = {"present": 0, "planned": 0, "unplanned": 0, "off": 0}
        for day in dates:
            code = records.get((employee["employee_key"], day.isoformat()))
            if not code and default_from <= day <= today:
                code = "P"
            if code in PRESENT_CODES:
                counts["present"] += 1
            elif code in PLANNED_CODES:
                counts["planned"] += 1
            elif code in UNPLANNED_CODES:
                counts["unplanned"] += 1
            elif code in NON_WORKING_CODES:
                counts["off"] += 1
        base = counts["present"] + counts["planned"] + counts["unplanned"]
        rows.append({**employee, **counts, "attendance_rate": round(counts["present"] / base * 100, 1) if base else 0})
        for key in totals:
            totals[key] += counts[key]
    base = totals["present"] + totals["planned"] + totals["unplanned"]
    totals["attendance_rate"] = round(totals["present"] / base * 100, 1) if base else 0
    return {"start": start, "end": end, "rows": rows, "totals": totals, "last_updated": updated, "employee_count": len(rows)}
