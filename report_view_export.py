from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


FOREST = "123F33"
LIME = "D9EF78"


def _workbook(title, headers, rows, currency_columns=(), percent_columns=(), total_columns=(), footer_row=None):
    book = Workbook()
    sheet = book.active
    sheet.title = "Report"
    sheet.append([title])
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    sheet["A1"].fill = PatternFill("solid", fgColor=FOREST)
    sheet["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    sheet.append(headers)
    for cell in sheet[2]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(color=FOREST, bold=True)
        cell.alignment = Alignment(vertical="center")
    for values in rows:
        sheet.append(values)
    if footer_row:
        sheet.append(footer_row)
        for cell in sheet[sheet.max_row]:
            cell.fill = PatternFill("solid", fgColor="EAF3E5")
            cell.font = Font(color=FOREST, bold=True)
    elif total_columns and rows:
        total_row = sheet.max_row + 1
        sheet.cell(total_row, 1, "Filtered total")
        for column in total_columns:
            sheet.cell(total_row, column, f"=SUM({get_column_letter(column)}3:{get_column_letter(column)}{total_row - 1})")
        for cell in sheet[total_row]:
            cell.fill = PatternFill("solid", fgColor="EAF3E5")
            cell.font = Font(color=FOREST, bold=True)
    for row_number in range(3, sheet.max_row + 1):
        for column in currency_columns:
            sheet.cell(row_number, column).number_format = "₹#,##0.00"
        for column in percent_columns:
            sheet.cell(row_number, column).number_format = "0.0%"
    sheet.freeze_panes = "A3"
    sheet.auto_filter.ref = f"A2:{get_column_letter(sheet.max_column)}{max(sheet.max_row, 2)}"
    for column in sheet.columns:
        sheet.column_dimensions[get_column_letter(column[0].column)].width = min(max(len(str(cell.value or "")) for cell in column) + 3, 46)
    output = BytesIO()
    book.save(output)
    output.seek(0)
    return output


def visible_table_excel(title, headers, rows, totals=None):
    """Create one workbook/sheet from the exact rows visible in a portal table."""
    safe_headers = [str(value or "")[:200] for value in headers]
    safe_rows = []
    for row in rows:
        values = []
        for value in row[:50]:
            value = str(value or "")[:4000]
            if value[:1] in ("=", "+", "-", "@"):
                value = "'" + value
            values.append(value)
        safe_rows.append(values)
    safe_totals = []
    for value in (totals or [])[:50]:
        value = str(value or "")[:4000]
        if value[:1] in ("=", "+", "-", "@"):
            value = "'" + value
        safe_totals.append(value)
    return _workbook(
        str(title or "Filtered report")[:120], safe_headers, safe_rows,
        footer_row=safe_totals or None,
    )


def product_view_excel(report, scope_label, view):
    if view == "agents":
        rows = [[r["employee"], r["role"], r["product"], r["sku"], r["quantity"], r["sales"], r["mrp"], r["discounts"], r["discounts"] / r["mrp"] if r["mrp"] else 0, r["orders"]] for r in report["product_agents"]]
        return _workbook(f"{scope_label} — Product by Agent", ["Agent", "Role", "Product", "SKU", "Quantity", "Selling Price", "MRP", "Eligible Discount", "Discount %", "Orders"], rows, (6, 7, 8), (9,), (5, 6, 7, 8, 10))
    source = report["products"] if view == "discount" else report["products_by_quantity"]
    rows = [[i, r["product"], r["sku"], r["quantity"], r["sales"], r["mrp"], r["discounts"], r["discounts"] / r["mrp"] if r["mrp"] else 0, r["orders"]] for i, r in enumerate(source, 1)]
    title = "Product Discount — Highest First" if view == "discount" else "Product Sales — Quantity Highest First"
    return _workbook(f"{scope_label} — {title}", ["Rank", "Product", "SKU", "Quantity", "Selling Price", "MRP", "Eligible Discount", "Discount %", "Orders"], rows, (5, 6, 7), (8,), (4, 5, 6, 7, 9))


def comparison_view_excel(comparison, view, report_type, products=None):
    labels = {"overall": "Wellbeing Overall", "crm": "Team CRM", "nutritionists": "Team Nutritionists"}
    if report_type == "sales":
        if view == "discount":
            rows = [[labels[k], comparison[k]["discounts"], comparison[k]["mrp"], comparison[k]["discount_rate"] / 100] for k in labels]
            return _workbook("Sales Comparison — Eligible Discount", ["Scope", "Eligible Discount", "MRP", "Discount %"], rows, (2, 3), (4,), (2, 3))
        rows = [[labels[k], comparison[k]["sales"], comparison[k]["orders"]] for k in labels]
        return _workbook("Sales Comparison — Selling Price", ["Scope", "Selling Price", "Orders"], rows, (2,), (), (2, 3))
    if report_type == "product" and view == "products":
        rows = [[i, r["product"], r["sku"], r["overall"].get("quantity", 0), r["crm"].get("quantity", 0), r["nutritionists"].get("quantity", 0)] for i, r in enumerate(products or [], 1)]
        return _workbook("Product Quantity Comparison", ["Rank", "Product", "SKU", "Wellbeing Overall", "Team CRM", "Team Nutritionists"], rows, (), (), (4, 5, 6))
    field = {"sales": "product_sales", "quantity": "product_quantity", "discount": "product_discounts"}.get(view, "product_sales")
    rows = [[labels[k], comparison[k][field]] for k in labels]
    return _workbook(f"Product Comparison — {view.title()}", ["Scope", view.replace("_", " ").title()], rows, (2,) if view != "quantity" else (), (), (2,))


def loyalty_comparison_view_excel(comparison, view):
    labels = {"overall": "Wellbeing Overall", "crm": "Team CRM", "nutritionists": "Team Nutritionists"}
    if view == "fulfilment":
        rows = [[labels[k], comparison[k]["fulfilment"]["reconciliation"]["shopify_orders"]] + [comparison[k]["fulfilment"]["summary"][b] for b in comparison[k]["fulfilment_buckets"]] + [comparison[k]["fulfilment"]["reconciliation"]["matched_orders"], comparison[k]["fulfilment"]["reconciliation"]["shopify_missing_uniware"]] for k in labels]
        return _workbook("Order Report Comparison", ["Scope", "Total Shopify Orders", "Delivered", "Cancelled", "Returned", "Pending", "Reconciled Orders", "Awaiting Uniware Sync / Match"], rows, (), (), (2, 3, 4, 5, 6, 7, 8))
    if view == "tiers":
        rows = [[labels[k]] + [comparison[k]["tier_counts"][tier] for tier in comparison[k]["tiers"]] + [comparison[k]["customer_count"]] for k in labels]
        return _workbook("Customer Loyalty Tier Comparison", ["Scope", "Loyal", "Regular", "Repeat", "One-time", "Total Customers"], rows, (), (), (2, 3, 4, 5, 6))
    field = "order_count" if view == "orders" else "customer_count"
    label = "Shopify Orders" if view == "orders" else "Unique Customers"
    return _workbook(f"Customer Loyalty Comparison — {label}", ["Scope", label], [[labels[k], comparison[k][field]] for k in labels], (), (), (2,))


def underlying_view_excel(detail, order_rows, product_rows):
    if detail == "product":
        rows = []
        for row in product_rows:
            discount = abs(float(row.get("discounts") or 0)); selling = float(row.get("total_sales") or 0)
            rows.append([row.get("day"), row.get("display_name"), row.get("role"), row.get("product_title"), row.get("product_sku"), int(row.get("quantity_sold") or 0), selling + discount, discount, selling, int(row.get("order_count") or 0)])
        return _workbook("Product / Agent Underlying Data", ["Date", "Employee", "Role", "Product", "SKU", "Quantity", "MRP", "Discount", "Selling Price", "Orders"], rows, (7, 8, 9), (), (6, 7, 8, 9, 10))
    rows = []
    for row in order_rows:
        selling = float(row.get("total_price") or 0); discount = float(row.get("total_discounts") or 0)
        rows.append([row.get("local_date"), row.get("display_name"), row.get("role"), row.get("order_name"), row.get("customer_name"), row.get("customer_email"), row.get("customer_phone"), row.get("payment_type"), row.get("payment_gateways"), row.get("financial_status"), selling + discount, discount, selling])
    return _workbook("Employee / Day / Order Underlying Data", ["Date", "Employee", "Role", "Order ID", "Customer", "Email", "Phone", "Payment", "Gateway", "Status", "MRP", "Discount", "Selling Price"], rows, (11, 12, 13), (), (11, 12, 13))


def loyalty_view_excel(report, view):
    if view == "fulfilment":
        rows = [[r["display_order_code"], r["sale_order_code"], r["display_order_date"], r["fulfilment_bucket"], r["order_status"], r["customer_name"], r["customer_email"], r["customer_phone"], "Yes" if r["cod"] else "No", r["item_count"]] for r in report["fulfilment"]["orders"]]
        return _workbook(f"{report['scope_label']} — Uniware Order Detail", ["Display Order", "Uniware Order", "Date", "Status", "Raw Status", "Customer", "Email", "Phone", "COD", "Items"], rows, (), (), (10,))
    rows = [[r["customer_name"], r["customer_email"], r["customer_phone"], r["tier"], r["order_count"], r["first_order_month"], r["last_order_month"], ", ".join(r.get("agents") or []), r["matched_by"]] for r in report["customers"]]
    return _workbook(f"{report['scope_label']} — Customer Loyalty Directory", ["Customer", "Email", "Phone", "Tier", "Orders", "First Order", "Last Order", "Agents", "Identity Evidence"], rows, (), (), (5,))
