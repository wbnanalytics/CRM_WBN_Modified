from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


FOREST = "123D31"
LIME = "DDF47B"


def workbook_from_tables(title, filters, tables):
    """Create a consistent filtered workbook from named row dictionaries."""
    book = Workbook()
    book.remove(book.active)
    for sheet_name, rows, columns in tables:
        sheet = book.create_sheet(sheet_name[:31])
        sheet.append([title])
        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(len(columns), 1))
        sheet["A1"].fill = PatternFill("solid", fgColor=FOREST)
        sheet["A1"].font = Font(color="FFFFFF", bold=True, size=14)
        sheet.append([f"{key}: {value}" for key, value in filters.items()])
        sheet.append([])
        sheet.append([label for _key, label in columns])
        for cell in sheet[4]:
            cell.fill = PatternFill("solid", fgColor=LIME)
            cell.font = Font(color=FOREST, bold=True)
        for row in rows:
            sheet.append([row.get(key) for key, _label in columns])
        sheet.freeze_panes = "A5"
        sheet.auto_filter.ref = f"A4:{get_column_letter(len(columns))}{max(sheet.max_row, 4)}"
        for index, column in enumerate(sheet.columns, 1):
            sheet.column_dimensions[get_column_letter(index)].width = min(max(len(str(cell.value or "")) for cell in column) + 3, 42)
    stream = BytesIO()
    book.save(stream)
    stream.seek(0)
    return stream


def source_metrics_workbook(source_name, metrics, start, end, active_filter):
    summary = [{
        "total": metrics.get("total", 0), "resolved": metrics.get("resolved", 0),
        "open": metrics.get("open", 0), "resolution_rate": metrics.get("resolution_rate", 0),
        "messages": metrics.get("messages", 0), "conversations": metrics.get("conversations", 0),
        "agents": metrics.get("agents", 0), "csat_score": metrics.get("csat_score", 0),
        "csat_responses": metrics.get("csat_responses", 0),
    }]
    recent = metrics.get("recent") or []
    recent_keys = sorted({key for row in recent for key in row.keys()})
    return workbook_from_tables(
        f"WBN CRM — {source_name}", {"From": start, "To": end, "Filter": active_filter},
        [
            ("Summary", summary, [(key, key.replace("_", " ").title()) for key in summary[0]]),
            ("Channels", metrics.get("channels") or [], [("label", "Channel"), ("value", "Count")]),
            ("Statuses", metrics.get("statuses") or [], [("label", "Status"), ("value", "Count")]),
            ("Recent Records", recent, [(key, key.replace("_", " ").title()) for key in recent_keys] or [("record", "Record")]),
        ],
    )


def attendance_planner_workbook(report):
    columns = [
        ("source_employee_id", "Employee ID"), ("employee_name", "Employee"), ("role", "Role"),
        ("employment_status", "Employment Status"), ("pl_balance", "PL Balance"), ("sl_balance", "SL Balance"),
    ] + [(day.isoformat(), day.strftime("%d %b %Y")) for day in report["dates"]]
    rows = []
    for employee in report["employees"]:
        row = {key: employee.get(key) for key, _label in columns[:6]}
        row.update({item["date"]: item.get("code") or "" for item in employee["days"]})
        rows.append(row)
    return workbook_from_tables(
        "WBN CRM — Attendance Planner", {"Month": report["start"].strftime("%B %Y")},
        [("Attendance", rows, columns)],
    )
