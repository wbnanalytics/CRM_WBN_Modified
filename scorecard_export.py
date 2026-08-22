from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


FOREST = "173F32"
LIME = "D9ED81"


def _workbook(title):
    book = Workbook()
    sheet = book.active
    sheet.title = title[:31]
    sheet.freeze_panes = "A6"
    return book, sheet


def _finish(book, sheet):
    for index, column in enumerate(sheet.columns, start=1):
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = min(max(len(str(cell.value or "")) for cell in column) + 3, 55)
    output = BytesIO()
    book.save(output)
    output.seek(0)
    return output


def scorecard_excel(record):
    book, sheet = _workbook("Scorecard")
    sheet.append(["WELLBEING NUTRITION — PERFORMANCE SCORECARD"])
    sheet.merge_cells("A1:H1")
    sheet["A1"].fill = PatternFill("solid", fgColor=FOREST)
    sheet["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    sheet["A1"].alignment = Alignment(horizontal="center")
    sheet.append(["Employee", record["employee_name"], "Employee ID", record["emp_id"]])
    sheet.append(["Department", record["department"], "Evaluation date", record["evaluation_date"]])
    sheet.append(["Total", record["total_points"], "Maximum", record["max_points"], "Score %", record["score_pct"]])
    sheet.append([])
    sheet.append(["Section", "KPI / Objective", "KRA", "Achieved", "Target", "Score %", "Points", "Manager remarks"])
    for cell in sheet[6]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(bold=True)
    for row in record["rows"]:
        ratio = round((row["score_ratio"] or 0) * 100, 2) if row["score_ratio"] is not None else None
        sheet.append([row["section"], row["kpi"], row["kra"], row["achieved"], row["target"], ratio, row["final_points"], row["manager_remarks"]])
    sheet.append([])
    sheet.append(["Positive feedback", record.get("positive_feedback") or ""])
    sheet.append(["Area of development", record.get("area_of_development") or ""])
    return _finish(book, sheet)


def appraisal_excel(record):
    book, sheet = _workbook("Appraisal")
    sheet.append(["WELLBEING NUTRITION — PERFORMANCE APPRAISAL"])
    sheet.merge_cells("A1:F1")
    sheet["A1"].fill = PatternFill("solid", fgColor=FOREST)
    sheet["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    sheet["A1"].alignment = Alignment(horizontal="center")
    sheet.append(["Employee", record["employee_name"], "Employee ID", record["emp_id"]])
    sheet.append(["Department", record["department"], "Review period", record["period_label"]])
    sheet.append(["KPI /5", record["kpi_rating_5"], "Behaviour /5", record["behaviour_rating_5"], "Total /10", record["total_rating_10"]])
    sheet.append([])
    sheet.append(["Section", "Criteria", "Guidance", "Weight", "Rating", "Comments"])
    for cell in sheet[6]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(bold=True)
    for row in record["rows"]:
        sheet.append([row["section"], row["criteria"], row["guidance"], row["weightage"], row["rating"], row["comments"]])
    sheet.append([])
    sheet.append(["Strengths", record.get("strengths") or ""])
    sheet.append(["Areas of improvement", record.get("improvements") or ""])
    sheet.append(["Learning requirements", record.get("learning_requirements") or ""])
    sheet.append(["Overall band", record.get("overall_band") or ""])
    sheet.append(["Increment %", record.get("increment_pct")])
    sheet.append(["Promotion recommendation", record.get("promotion_recommendation") or ""])
    return _finish(book, sheet)
