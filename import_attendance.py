import argparse
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from attendance_service import employee_key, import_attendance_snapshot
from database import init_db


def parse_date_header(value):
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    cleaned = re.sub(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s*", "", cleaned)
    for fmt in ("%d %B, %Y", "%d %b, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    return None


def clean_value(value):
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_attendance_workbook(path, through):
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook["Attendance Planner"]
    rows = list(sheet.iter_rows(values_only=True))
    header = rows[0]
    date_columns = [
        (index, parsed) for index, value in enumerate(header)
        if (parsed := parse_date_header(value)) and parsed <= through
    ]
    if not date_columns:
        raise ValueError("No attendance dates were found in the Attendance Planner sheet")
    employees, records = [], []
    for row_index, row in enumerate(rows[1:], start=1):
        name = clean_value(row[1] if len(row) > 1 else None)
        employment_status = clean_value(row[3] if len(row) > 3 else None)
        if not name or employment_status not in {"Active", "Inactive"}:
            continue
        key = employee_key(name)
        employees.append({
            "employee_key": key,
            "source_employee_id": clean_value(row[0] if len(row) > 0 else None),
            "employee_name": name,
            "role": clean_value(row[2] if len(row) > 2 else None) or "Unassigned",
            "employment_status": employment_status,
            "pl_balance": clean_value(row[4] if len(row) > 4 else None),
            "sl_balance": clean_value(row[5] if len(row) > 5 else None),
            "sort_order": row_index,
        })
        for column, attendance_date in date_columns:
            status = clean_value(row[column] if column < len(row) else None)
            if status:
                records.append({
                    "employee_key": key,
                    "attendance_date": attendance_date.isoformat(),
                    "status_code": status,
                })
    return employees, records, date_columns[0][1], date_columns[-1][1]


def main():
    parser = argparse.ArgumentParser(description="Import the CRM Attendance Planner workbook")
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--through", help="Last date to import in YYYY-MM-DD (defaults to today IST)")
    parser.add_argument("--imported-by", default="attendance-workbook")
    args = parser.parse_args()
    through = date.fromisoformat(args.through) if args.through else datetime.now(ZoneInfo("Asia/Kolkata")).date()
    employees, records, range_start, range_end = read_attendance_workbook(args.workbook, through)
    init_db()
    result = import_attendance_snapshot(
        args.workbook.name, employees, records, range_start.isoformat(), range_end.isoformat(), args.imported_by,
    )
    print(result)


if __name__ == "__main__":
    main()
