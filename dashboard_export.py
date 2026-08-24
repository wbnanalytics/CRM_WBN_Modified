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
    summary.append(["Channel", "Total", "Resolved", "Open", "Spam", "Non-spam", "FRT incl. spam (sec)", "FRT excl. spam (sec)", "Tickets with 10–12 replies*"])
    for item in report["metrics"]:
        summary.append([item["label"], item["total"], item["resolved"], item["open"], item["spam"], item["non_spam"], item["frt_with_spam"], item["frt_without_spam"], item["reply_10_12"]])
    summary.append([])
    summary.append(["*Zoho reply count is estimated from ticket thread activity until thread-level history is available."])
    agents = workbook.create_sheet("Agent Performance")
    agents.append(["Agent", "Email", "Inbound", "Chats", "ORM", "Total", "Resolved", "Resolution %", "FRT incl. spam", "FRT excl. spam"])
    for item in report["agents"]:
        agents.append([item["name"], item["email"], item["inbound"], item["chats"], item["orm"], item["total"], item["resolved"], item["resolution_rate"], item["frt_with_spam"], item["frt_without_spam"]])
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
