from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


FOREST = "173F32"
LIME = "D9ED81"
HEADERS = ("Employee Key", "Employee Name", "Role", "Target Month", "Daily Target", "Weekly Target", "Monthly Target")


def target_template(employees, month_start, current_targets):
    book = Workbook()
    sheet = book.active
    sheet.title = "Sales Targets"
    sheet.append(["WELLBEING NUTRITION — SHOPIFY SALES TARGETS"])
    sheet.merge_cells("A1:G1")
    sheet["A1"].fill = PatternFill("solid", fgColor=FOREST)
    sheet["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    sheet["A1"].alignment = Alignment(horizontal="center")
    sheet.append(["Instructions", "Enter non-negative amounts only. Do not change Employee Key or Target Month."])
    sheet.merge_cells("B2:G2")
    sheet.append([])
    sheet.append(list(HEADERS))
    for cell in sheet[4]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(bold=True)
    for employee in employees:
        key = employee["employee_key"]
        sheet.append([
            key, employee["display_name"], employee["role"], month_start,
            current_targets.get((key, "daily"), employee["weekly_target"] / 5.5),
            current_targets.get((key, "weekly"), employee["weekly_target"]),
            current_targets.get((key, "monthly"), employee["weekly_target"] * 4),
        ])
    sheet.freeze_panes = "A5"
    sheet.auto_filter.ref = f"A4:G{sheet.max_row}"
    for index, width in enumerate((22, 24, 28, 16, 18, 18, 18), start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=5, min_col=5, max_col=7):
        for cell in row:
            cell.number_format = '₹#,##0.00'
    for cell in sheet["D"][4:]:
        cell.number_format = "mmm yyyy"
    output = BytesIO()
    book.save(output)
    output.seek(0)
    return output


def performance_excel(report):
    book = Workbook()
    sheet = book.active
    sheet.title = "Sales Performance"
    period_label = report["period"].title()
    sheet.append(["WELLBEING NUTRITION — SHOPIFY SALES PERFORMANCE"])
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=5 + len(report["columns"]) * 2)
    sheet["A1"].fill = PatternFill("solid", fgColor=FOREST)
    sheet["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    sheet["A1"].alignment = Alignment(horizontal="center")
    sheet.append(["Reporting view", period_label, "From", report["start"], "To", report["end"]])
    sheet.append([])
    headers = ["Employee", "Role"]
    for column in report["columns"]:
        headers.extend([f"{column['label']} Sales", f"{column['label']} Target"])
    headers.extend(["Total Sales", "Orders", "Average Order Value", "Total Target", "Attainment %"])
    sheet.append(headers)
    for cell in sheet[4]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    for employee in (item for item in report["rows"] if item["include_in_team"]):
        values = [employee["display_name"], employee["role"]]
        for index, sales in enumerate(employee["values"]):
            values.extend([sales, employee["targets"][index]])
        values.extend([employee["total"], employee["orders"], employee["average_order_value"], employee["total_target"], (employee["attainment"] or 0) * 100])
        sheet.append(values)
        fill = "EAF6EC" if employee["performance_band"] == "top" else ("FCEDEA" if employee["performance_band"] == "bottom" else None)
        if fill:
            for cell in sheet[sheet.max_row]:
                cell.fill = PatternFill("solid", fgColor=fill)
    team_values = ["CRM Team", "Managed team"]
    for index, sales in enumerate(report["team"]["values"]):
        team_values.extend([sales, report["team"]["targets"][index]])
    team_values.extend([report["team"]["total"], report["team"]["orders"], report["team"]["average_order_value"], report["team"]["target"], report["team"]["attainment"] * 100])
    sheet.append(team_values)
    for cell in sheet[sheet.max_row]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E6EFE2")
    sheet.freeze_panes = "C5"
    sheet.auto_filter.ref = f"A4:{get_column_letter(sheet.max_column)}{sheet.max_row}"
    for row in sheet.iter_rows(min_row=5, min_col=3, max_col=sheet.max_column - 1):
        for cell in row:
            cell.number_format = '₹#,##0.00'
    for cell in (sheet.cell(row=row, column=sheet.max_column) for row in range(5, sheet.max_row + 1)):
        cell.number_format = '0.0%'
        cell.value = (cell.value or 0) / 100
    for index, column in enumerate(sheet.columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = min(max(len(str(cell.value or "")) for cell in column) + 3, 28)
    output = BytesIO()
    book.save(output)
    output.seek(0)
    return output


def read_target_upload(file_storage, expected_month):
    try:
        book = load_workbook(file_storage.stream, read_only=True, data_only=True)
        sheet = book.active
    except Exception as exc:
        raise ValueError("Upload a valid .xlsx target template") from exc
    headers = tuple(str(sheet.cell(4, column).value or "").strip() for column in range(1, 8))
    if headers != HEADERS:
        raise ValueError("Use the downloaded target template without changing its columns")
    rows = []
    for number, values in enumerate(sheet.iter_rows(min_row=5, values_only=True), start=5):
        employee_key, _name, _role, month_value, daily, weekly, monthly = values[:7]
        if not employee_key:
            continue
        month_text = month_value.strftime("%Y-%m-%d") if hasattr(month_value, "strftime") else str(month_value or "")[:10]
        if month_text != expected_month:
            raise ValueError(f"Row {number}: Target Month must be {expected_month}")
        for period, amount in (("daily", daily), ("weekly", weekly), ("monthly", monthly)):
            try:
                numeric = float(amount)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Row {number}: {period.title()} Target must be a number") from exc
            if numeric < 0:
                raise ValueError(f"Row {number}: targets cannot be negative")
            rows.append({
                "employee_key": str(employee_key).strip(), "period_type": period,
                "period_start": expected_month, "target_amount": numeric,
            })
    if not rows:
        raise ValueError("The target template contains no employee rows")
    return rows
