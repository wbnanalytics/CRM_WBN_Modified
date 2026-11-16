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


def sales_trend_excel(trend, selection, scope="overall", overall_trend=None):
    """Export the exact filtered values used by the sales/discount chart."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sales & Discount Trend"
    sheet.append(["WBN CRM — Sales & Discount Trend"])
    scope_label = {"overall": "Wellbeing Overall", "crm": "Team CRM", "nutritionists": "Team Nutritionists"}.get(scope, "Wellbeing Overall")
    sheet.append(["Selected period", selection["start"], selection["end"], "Scope", scope_label])
    sheet.append([])
    if scope == "overall" or not overall_trend:
        headers = ["Date / Period", "Selling Price", "MRP", "Discount (CRM Admin Excluded)", "Discount %"]
        sheet.append(headers)
        for row in trend:
            sheet.append([row["label"], row["sales"], row["mrp"], row["discounts"], (row["discount_rate"] or 0) / 100])
    else:
        headers = ["Date / Period", f"{scope_label} Selling Price", f"{scope_label} MRP", f"{scope_label} Discount", f"{scope_label} Discount %", "Wellbeing Selling Price", "Wellbeing MRP", "Wellbeing Discount", "Wellbeing Discount %"]
        sheet.append(headers)
        for index, row in enumerate(trend):
            overall = overall_trend[index]
            sheet.append([
                row["label"], row["sales"], row["mrp"], row["discounts"], (row["discount_rate"] or 0) / 100,
                overall["sales"], overall["mrp"], overall["discounts"], (overall["discount_rate"] or 0) / 100,
            ])

    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    sheet["A1"].fill = PatternFill("solid", fgColor=FOREST)
    sheet["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    sheet["A1"].alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 26
    for cell in sheet[4]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(color=FOREST, bold=True)
        cell.alignment = Alignment(vertical="center")
    for row_number in range(5, sheet.max_row + 1):
        currency_columns = (2, 3, 4) if len(headers) == 5 else (2, 3, 4, 6, 7, 8)
        for column in currency_columns:
            sheet.cell(row_number, column).number_format = '₹#,##0.00'
        for column in ((5,) if len(headers) == 5 else (5, 9)):
            sheet.cell(row_number, column).number_format = "0.0%"
    sheet.freeze_panes = "A5"
    sheet.auto_filter.ref = f"A4:{sheet.cell(4, len(headers)).column_letter}{max(sheet.max_row, 4)}"
    for column in range(1, len(headers) + 1):
        sheet.column_dimensions[sheet.cell(4, column).column_letter].width = 20 if column == 1 else 22
    return _finish(workbook)


def product_report_excel(report, selection, scope="overall"):
    """Export the exact CRM-Admin-excluded product report shown in the portal."""
    workbook = Workbook()
    scope_label = {
        "overall": "Wellbeing Overall",
        "crm": "Team CRM",
        "nutritionists": "Team Nutritionists",
    }.get(scope, "Wellbeing Overall")

    product_sales = workbook.active
    product_sales.title = "Product Sales"
    product_sales.append(["WBN CRM — PRODUCT REPORT"])
    product_sales.append(["Scope", scope_label, "From", selection["start"], "To", selection["end"]])
    product_sales.append(["CRM Admin", "Excluded from every Product Report metric"])
    product_sales.append([])
    product_sales.append(["Rank", "Product", "SKU", "Quantity Sold", "Selling Price", "MRP", "Eligible Discount", "Discount %", "Orders"])
    for rank, row in enumerate(report.get("products_by_quantity", []), 1):
        product_sales.append([
            rank, row["product"], row["sku"], row["quantity"], row["sales"], row["mrp"],
            row["discounts"], row["discounts"] / row["mrp"] if row["mrp"] else 0, row["orders"],
        ])

    discount = workbook.create_sheet("Discount Highest First")
    discount.append(["Rank", "Product", "SKU", "Quantity Sold", "Selling Price", "MRP", "Eligible Discount", "Discount %", "Orders"])
    for rank, row in enumerate(report.get("products", []), 1):
        discount.append([
            rank, row["product"], row["sku"], row["quantity"], row["sales"], row["mrp"],
            row["discounts"], row["discounts"] / row["mrp"] if row["mrp"] else 0, row["orders"],
        ])

    agents = workbook.create_sheet("Product by Agent")
    agents.append(["Agent", "Role", "Product", "SKU", "Quantity Sold", "Selling Price", "MRP", "Eligible Discount", "Discount %", "Orders"])
    for row in report.get("product_agents", []):
        agents.append([
            row["employee"], row["role"], row["product"], row["sku"], row["quantity"],
            row["sales"], row["mrp"], row["discounts"],
            row["discounts"] / row["mrp"] if row["mrp"] else 0, row["orders"],
        ])

    for sheet in workbook.worksheets:
        header_row = 5 if sheet is product_sales else 1
        for cell in sheet[header_row]:
            cell.fill = PatternFill("solid", fgColor=LIME)
            cell.font = Font(color=FOREST, bold=True)
        sheet.freeze_panes = f"A{header_row + 1}"
        sheet.auto_filter.ref = f"A{header_row}:{sheet.cell(sheet.max_row, sheet.max_column).coordinate}"
        currency_columns = (5, 6, 7) if sheet is not agents else (6, 7, 8)
        percentage_column = 8 if sheet is not agents else 9
        for row in sheet.iter_rows(min_row=header_row + 1):
            for column in currency_columns:
                sheet.cell(row[0].row, column).number_format = "₹#,##0.00"
            sheet.cell(row[0].row, percentage_column).number_format = "0.0%"
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(
                max(len(str(cell.value or "")) for cell in column) + 3, 48
            )
    product_sales.merge_cells("A1:I1")
    product_sales["A1"].fill = PatternFill("solid", fgColor=FOREST)
    product_sales["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    return _finish(workbook)


def sales_comparison_excel(comparison, selection):
    """Export the three Sales Comparison graphs and their exact filtered values."""
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Scope Comparison"
    summary.append(["WBN CRM — SALES COMPARISON"])
    summary.append(["From", selection["start"], "To", selection["end"], "View", selection["period"]])
    summary.append([])
    summary.append(["Scope", "Selling Price", "Eligible Discount", "MRP", "Discount %", "Orders"])
    labels = {"overall": "Wellbeing Overall", "crm": "Team CRM", "nutritionists": "Team Nutritionists"}
    for key in ("overall", "crm", "nutritionists"):
        row = comparison[key]
        summary.append([labels[key], row["sales"], row["discounts"], row["mrp"], row["discount_rate"] / 100, row["orders"]])

    trend = workbook.create_sheet("Period Trend")
    trend.append([
        "Date / Period",
        "Wellbeing Sales", "Wellbeing Discount", "Wellbeing MRP", "Wellbeing Discount %",
        "Team CRM Sales", "Team CRM Discount", "Team CRM MRP", "Team CRM Discount %",
        "Nutritionists Sales", "Nutritionists Discount", "Nutritionists MRP", "Nutritionists Discount %",
    ])
    row_count = max(len(comparison[key]["trend"]) for key in comparison)
    for index in range(row_count):
        scope_rows = {key: comparison[key]["trend"][index] for key in comparison}
        trend.append([
            scope_rows["overall"]["label"],
            scope_rows["overall"]["sales"], scope_rows["overall"]["discounts"], scope_rows["overall"]["mrp"], scope_rows["overall"]["discount_rate"] / 100,
            scope_rows["crm"]["sales"], scope_rows["crm"]["discounts"], scope_rows["crm"]["mrp"], scope_rows["crm"]["discount_rate"] / 100,
            scope_rows["nutritionists"]["sales"], scope_rows["nutritionists"]["discounts"], scope_rows["nutritionists"]["mrp"], scope_rows["nutritionists"]["discount_rate"] / 100,
        ])

    for sheet, header_row, currency_columns, percent_columns in (
        (summary, 4, (2, 3, 4), (5,)),
        (trend, 1, (2, 3, 4, 6, 7, 8, 10, 11, 12), (5, 9, 13)),
    ):
        for cell in sheet[header_row]:
            cell.fill = PatternFill("solid", fgColor=LIME)
            cell.font = Font(color=FOREST, bold=True)
        for row_number in range(header_row + 1, sheet.max_row + 1):
            for column in currency_columns:
                sheet.cell(row_number, column).number_format = "₹#,##0.00"
            for column in percent_columns:
                sheet.cell(row_number, column).number_format = "0.0%"
        sheet.freeze_panes = f"A{header_row + 1}"
        sheet.auto_filter.ref = f"A{header_row}:{sheet.cell(sheet.max_row, sheet.max_column).coordinate}"
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(max(len(str(cell.value or "")) for cell in column) + 3, 28)
    summary.merge_cells("A1:F1")
    summary["A1"].fill = PatternFill("solid", fgColor=FOREST)
    summary["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    return _finish(workbook)


def product_comparison_excel(comparison, comparison_products, selection):
    """Export the Product Comparison page using the same three filtered scopes."""
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Scope Comparison"
    summary.append(["WBN CRM — PRODUCT COMPARISON"])
    summary.append(["From", selection["start"], "To", selection["end"], "View", selection["period"]])
    summary.append(["CRM Admin", "Excluded from every Product Report metric"])
    summary.append([])
    summary.append(["Scope", "Product Selling Price", "Quantity Sold", "Eligible Product Discount", "Product MRP", "Discount %", "Orders"])
    labels = {"overall": "Wellbeing Overall", "crm": "Team CRM", "nutritionists": "Team Nutritionists"}
    for key in ("overall", "crm", "nutritionists"):
        row = comparison[key]
        summary.append([
            labels[key], row["product_sales"], row["product_quantity"], row["product_discounts"],
            row["product_mrp"], row["product_discount_rate"] / 100, row["orders"],
        ])

    products = workbook.create_sheet("Top Product Comparison")
    products.append([
        "Rank", "Product", "SKU",
        "Wellbeing Qty", "Wellbeing Sales", "Team CRM Qty", "Team CRM Sales",
        "Nutritionists Qty", "Nutritionists Sales",
    ])
    for rank, row in enumerate(comparison_products, 1):
        products.append([
            rank, row["product"], row["sku"],
            row["overall"].get("quantity", 0), row["overall"].get("sales", 0),
            row["crm"].get("quantity", 0), row["crm"].get("sales", 0),
            row["nutritionists"].get("quantity", 0), row["nutritionists"].get("sales", 0),
        ])

    for sheet, header_row, currency_columns, percent_columns in (
        (summary, 5, (2, 4, 5), (6,)),
        (products, 1, (5, 7, 9), ()),
    ):
        for cell in sheet[header_row]:
            cell.fill = PatternFill("solid", fgColor=LIME)
            cell.font = Font(color=FOREST, bold=True)
        for row_number in range(header_row + 1, sheet.max_row + 1):
            for column in currency_columns:
                sheet.cell(row_number, column).number_format = "₹#,##0.00"
            for column in percent_columns:
                sheet.cell(row_number, column).number_format = "0.0%"
        sheet.freeze_panes = f"A{header_row + 1}"
        sheet.auto_filter.ref = f"A{header_row}:{sheet.cell(sheet.max_row, sheet.max_column).coordinate}"
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(max(len(str(cell.value or "")) for cell in column) + 3, 48)
    summary.merge_cells("A1:G1")
    summary["A1"].fill = PatternFill("solid", fgColor=FOREST)
    summary["A1"].font = Font(color="FFFFFF", bold=True, size=14)
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
