from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill


FOREST = "123D31"
LIME = "DDF47B"


def _finish(workbook):
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def cs_dashboard_excel(report):
    workbook = Workbook()
    summary = workbook.active
    summary.title = "CS Overview"
    summary.append(["WBN CRM — CS Dashboard"])
    summary.append(["Period", report["start"], report["end"]])
    summary.append([])
    summary.append(["Channel", "Source", "Total", "Resolved", "Open / Queue", "Escalated / On Hold", "Spam", "Genuine", "Untagged", "Resolution %", "FRT incl. spam (sec)", "FRT excl. spam (sec)", "ART incl. spam (sec)", "ART excl. spam (sec)", "CSAT %", "CSAT responses", "Tickets with 10–12 replies*"])
    for item in report["metrics"]:
        summary.append([item["label"], item["source"], item["total"], item["resolved"], item["open"], item["escalated"], item["spam"], item["genuine"], item["untagged"], item["resolution_rate"], item["frt_with_spam"], item["frt_without_spam"], item["art_with_spam"], item["art_without_spam"], item["csat_rate"], item["csat_received"], item["reply_10_12"]])
    summary.append([])
    summary.append(["*Zoho reply count is estimated from ticket thread activity until thread-level history is available."])
    for channel in report["metrics"]:
        sheet = workbook.create_sheet(f"{channel['label']} Agents")
        sheet.append(["Agent", "Total", "Resolved", "Open / Queue", "Escalated / On Hold", "Spam", "Genuine", "Resolution %", "FRT incl. spam", "FRT excl. spam", "ART incl. spam", "ART excl. spam", "CSAT %", "CSAT responses", "Tickets with 10–12 replies*"])
        for item in channel["agents"]:
            sheet.append([item["name"], item["total"], item["resolved"], item["open"], item["escalated"], item["spam"], item["genuine"], item["resolution_rate"], item["frt_with_spam"], item["frt_without_spam"], item["art_with_spam"], item["art_without_spam"], item["csat_rate"], item["csat_received"], item["reply_10_12"]])
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor=FOREST)
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(vertical="center")
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(max(len(str(cell.value or "")) for cell in column) + 3, 42)
    return _finish(workbook)


def cs_detail_excel(rows, title="CS underlying data"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Underlying Data"
    columns = ["source", "channel", "record_id", "date", "created_time", "agent", "status",
               "classification", "category", "disposition", "message_type", "first_response_seconds",
               "average_response_seconds", "resolution_seconds", "reply_count_estimate"]
    sheet.append([title])
    sheet.append([column.replace("_", " ").title() for column in columns])
    for row in rows:
        sheet.append([row.get(column) for column in columns])
    sheet.freeze_panes = "A3"
    sheet.auto_filter.ref = f"A2:O{max(sheet.max_row, 2)}"
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=FOREST)
        cell.font = Font(color="FFFFFF", bold=True)
    for cell in sheet[2]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(color=FOREST, bold=True)
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = min(max(len(str(cell.value or "")) for cell in column) + 3, 34)
    return _finish(workbook)


def attendance_dashboard_excel(report):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Attendance"
    sheet.append(["Employee", "Role", "Present", "Planned Leave", "Absent / Unplanned", "Week Off / Holidays", "Attendance %"])
    for item in report["rows"]:
        sheet.append([item["employee_name"], item["role"], item["present"], item["planned"], item["unplanned"], item["off"], item["attendance_rate"] / 100])
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=FOREST)
        cell.font = Font(color="FFFFFF", bold=True)
    for cell in sheet["G"][1:]:
        cell.number_format = "0.0%"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = min(max(len(str(cell.value or "")) for cell in column) + 3, 36)
    return _finish(workbook)
