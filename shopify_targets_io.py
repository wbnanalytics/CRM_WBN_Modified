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


def performance_excel(report, product_field="all", product_query=""):
    book = Workbook()
    sheet = book.active
    sheet.title = "Sales Performance"
    period_label = "Week End — Year" if report["period"] == "weekly_year" else report["period"].title()
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
    headers.extend(["MRP", "Discount", "Selling Price", "Orders", "Average Order Value", "Total Target", "Attainment %"])
    sheet.append(headers)
    for cell in sheet[4]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    for employee in (item for item in report["rows"] if item["include_in_team"]):
        values = [employee["display_name"], employee["role"]]
        for index, sales in enumerate(employee["values"]):
            values.extend([sales, employee["targets"][index]])
        discount = abs(float(employee.get("discounts") or 0))
        values.extend([employee["total"] + discount, discount, employee["total"], employee["orders"], employee["average_order_value"], employee["total_target"], (employee["attainment"] or 0) * 100])
        sheet.append(values)
        fill = "EAF6EC" if employee["performance_band"] == "top" else ("FCEDEA" if employee["performance_band"] == "bottom" else None)
        if fill:
            for cell in sheet[sheet.max_row]:
                cell.fill = PatternFill("solid", fgColor=fill)
    if not report.get("selected_employee"):
        team_values = ["CRM Team", "Managed team"]
        for index, sales in enumerate(report["team"]["values"]):
            team_values.extend([sales, report["team"]["targets"][index]])
        team_discount = sum(abs(float(row.get("discounts") or 0)) for row in report["rows"] if row.get("include_in_team"))
        team_values.extend([report["team"]["total"] + team_discount, team_discount, report["team"]["total"], report["team"]["orders"], report["team"]["average_order_value"], report["team"]["target"], report["team"]["attainment"] * 100])
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
    product_sheet = book.create_sheet("Product by Employee")
    product_sheet.append(["Employee", "Role", "Product", "SKU", "Quantity Sold", "MRP", "Discount", "Selling Price", "Orders", "Average Order Value"])
    for cell in product_sheet[1]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(bold=True)
    query = (product_query or "").strip().lower()
    fields = {"employee": "display_name", "role": "role", "product": "product_title", "sku": "product_sku"}
    product_rows = report.get("product_rows", [])
    if query:
        if product_field in fields:
            product_rows = [item for item in product_rows if query in str(item.get(fields[product_field]) or "").lower()]
        else:
            product_rows = [item for item in product_rows if query in " ".join(str(item.get(key) or "").lower() for key in fields.values())]
    for item in product_rows:
        orders = item.get("orders", 0) or 0
        total = item.get("total", 0) or 0
        average_order_value = item.get("average_order_value")
        if average_order_value is None:
            average_order_value = total / orders if orders else 0
        discount = abs(float(item.get("discounts") or 0))
        product_sheet.append([
            item["display_name"], item["role"], item["product_title"], item["product_sku"],
            item.get("quantity", 0), total + discount, discount, total, orders, average_order_value,
        ])
    product_sheet.freeze_panes = "A2"
    product_sheet.auto_filter.ref = product_sheet.dimensions
    for row in product_sheet.iter_rows(min_row=2, min_col=6, max_col=10):
        row[0].number_format = row[1].number_format = row[2].number_format = row[4].number_format = '₹#,##0.00'
    for index, column in enumerate(product_sheet.columns, start=1):
        product_sheet.column_dimensions[get_column_letter(index)].width = min(max(len(str(cell.value or "")) for cell in column) + 3, 34)
    team_product_sheet = book.create_sheet("CRM Product Summary")
    team_product_sheet.append(["Rank", "Product", "SKU", "Quantity Sold", "MRP", "Discount", "Selling Price", "Orders", "Average Order Value"])
    for cell in team_product_sheet[1]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(bold=True)
    for rank, item in enumerate(report.get("team_product_rows", []), start=1):
        orders = item.get("orders", 0) or 0
        total = item.get("total", 0) or 0
        average_order_value = item.get("average_order_value")
        if average_order_value is None:
            average_order_value = total / orders if orders else 0
        discount = abs(float(item.get("discounts") or 0))
        team_product_sheet.append([
            rank, item["product_title"], item["product_sku"], item.get("quantity", 0),
            total + discount, discount, total, orders, average_order_value,
        ])
    team_product_sheet.freeze_panes = "A2"
    team_product_sheet.auto_filter.ref = team_product_sheet.dimensions
    for row in team_product_sheet.iter_rows(min_row=2, min_col=5, max_col=9):
        row[0].number_format = row[1].number_format = row[2].number_format = row[4].number_format = '₹#,##0.00'
    for index, column in enumerate(team_product_sheet.columns, start=1):
        team_product_sheet.column_dimensions[get_column_letter(index)].width = min(max(len(str(cell.value or "")) for cell in column) + 3, 42)
    output = BytesIO()
    book.save(output)
    output.seek(0)
    return output


def sales_underlying_excel(employee_rows, product_rows, start, end, employee_key="", order_rows=None,
                           payment_type="", financial_status="", search=""):
    book = Workbook()
    summary = book.active
    summary.title = "Employee Day Orders"
    summary.append(["WELLBEING NUTRITION — SHOPIFY UNDERLYING SALES DATA"])
    summary.merge_cells("A1:Q1")
    summary["A1"].fill = PatternFill("solid", fgColor=FOREST)
    summary["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    summary.append(["From", start, "To", end, "Employee filter", employee_key or "All employees"])
    summary.append([])
    summary.append(["Date", "Employee Key", "Employee", "Raw Shopify Employee", "Role", "Order ID", "Shopify Numeric ID", "Customer Name", "Customer Email", "Customer Phone", "Payment Type", "Payment Gateway", "Payment Status", "MRP", "Discount", "Selling Price", "Synced At"])
    for cell in summary[4]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(bold=True)
    for row in order_rows or []:
        selling = float(row.get("total_price") or 0)
        discount = float(row.get("total_discounts") or 0)
        summary.append([
            row.get("local_date"), row.get("employee_key"), row.get("display_name"),
            row.get("source_employee_name"), row.get("role"), row.get("order_name"),
            row.get("order_number"), row.get("customer_name"), row.get("customer_email"),
            row.get("customer_phone"), row.get("payment_type"), row.get("payment_gateways"),
            row.get("financial_status"), selling + discount, discount, selling, row.get("synced_at"),
        ])
    product = book.create_sheet("Product by Employee")
    product.append(["Date", "Employee Key", "Employee", "Raw Shopify Employee", "Role", "Product", "SKU", "Quantity", "MRP", "Discount", "Selling Price", "Orders", "Discount %", "Synced At"])
    for cell in product[1]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(bold=True)
    for row in product_rows:
        discount = abs(float(row.get("discounts") or 0))
        selling = float(row.get("total_sales") or 0)
        mrp = selling + discount
        product.append([row.get("day"), row.get("employee_key"), row.get("display_name"), row.get("source_employee_name"), row.get("role"), row.get("product_title"), row.get("product_sku"), int(row.get("quantity_sold") or 0), mrp, discount, selling, int(row.get("order_count") or 0), discount / mrp if mrp else 0, row.get("synced_at")])
    orders = book.create_sheet("Order Customer Payment")
    orders.append(["WELLBEING NUTRITION — FILTERED SHOPIFY ORDER DATA"])
    orders.merge_cells("A1:P1")
    orders["A1"].fill = PatternFill("solid", fgColor=FOREST)
    orders["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    orders.append(["From", start, "To", end, "Payment type", payment_type or "All", "Financial status", financial_status or "All", "Search", search or "None"])
    orders.append([])
    orders.append(["Date", "Created At", "Order ID", "Shopify Numeric ID", "Customer Name", "Customer Email", "Customer Phone", "Payment Type", "Payment Gateway", "Payment Status", "Selling Price", "Discount", "MRP", "Currency", "Source", "Synced At"])
    for cell in orders[4]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(bold=True)
    for row in order_rows or []:
        total = float(row.get("total_price") or 0)
        discount = float(row.get("total_discounts") or 0)
        orders.append([
            row.get("local_date"), row.get("created_at"), row.get("order_name"), row.get("order_number"),
            row.get("customer_name"), row.get("customer_email"), row.get("customer_phone"),
            row.get("payment_type"), row.get("payment_gateways"), row.get("financial_status"),
            total, discount, total + discount, row.get("currency_code"), row.get("source_name"), row.get("synced_at"),
        ])
    for sheet in (summary, product, orders):
        header_row = 4 if sheet in (summary, orders) else 1
        sheet.freeze_panes = f"A{header_row + 1}"
        sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(sheet.max_column)}{sheet.max_row}"
        for column in sheet.columns:
            sheet.column_dimensions[get_column_letter(column[0].column)].width = min(max(len(str(cell.value or "")) for cell in column) + 3, 34)
    for row in summary.iter_rows(min_row=5, min_col=14, max_col=16):
        for cell in row:
            cell.number_format = '₹#,##0.00'
    for row in product.iter_rows(min_row=2, min_col=9, max_col=11):
        for cell in row:
            cell.number_format = '₹#,##0.00'
    for cell in product["M"][1:]:
        cell.number_format = "0.0%"
    for row in orders.iter_rows(min_row=5, min_col=11, max_col=13):
        for cell in row:
            cell.number_format = '₹#,##0.00'
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
