from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


FOREST = "123F33"
LIME = "D9EF78"


def _header(sheet, row):
    for cell in sheet[row]:
        cell.fill = PatternFill("solid", fgColor=LIME)
        cell.font = Font(color=FOREST, bold=True)
        cell.alignment = Alignment(vertical="center")


def _finish(sheet, header_row):
    sheet.freeze_panes = f"A{header_row + 1}"
    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(sheet.max_column)}{sheet.max_row}"
    for column in sheet.columns:
        width = max(len(str(cell.value or "")) for cell in column) + 3
        sheet.column_dimensions[get_column_letter(column[0].column)].width = min(max(width, 12), 42)


def loyalty_workbook(report):
    book = Workbook()
    customers = book.active
    customers.title = "Customers"
    customers.append(["WELLBEING NUTRITION — CUSTOMER LOYALTY INTELLIGENCE"])
    customers.merge_cells("A1:I1" if report["scope"] == "overall" else "A1:J1")
    customers["A1"].fill = PatternFill("solid", fgColor=FOREST)
    customers["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    customers.append([
        "Scope", report["scope_label"], "Period", report["period"] or "Year / month",
        "From", report["start_date"], "To", report["end_date"],
        "Tier filter", report["tier"] or "All",
    ])
    customers.append([])
    customer_headers = [
        "Customer", "Email", "Phone", "Tier", "Orders", "First order",
        "Last order",
    ]
    if report["scope"] != "overall":
        customer_headers.append("Agents")
    customer_headers.extend(["Identity matched by", "Scope"])
    customers.append(customer_headers)
    _header(customers, 4)
    for row in report["customers"]:
        values = [
            row["customer_name"], row["customer_email"], row["customer_phone"], row["tier"],
            row["order_count"], row["first_order_month"], row["last_order_month"],
        ]
        if report["scope"] != "overall":
            values.append(", ".join(row["agents"]))
        values.extend([row["matched_by"], report["scope_label"]])
        customers.append(values)
    _finish(customers, 4)

    summary = book.create_sheet("Fulfilment Summary")
    summary.append(["WELLBEING NUTRITION — UNIWARE ORDER STATUS"])
    summary.merge_cells("A1:C1")
    summary["A1"].fill = PatternFill("solid", fgColor=FOREST)
    summary["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    summary.append(["Scope", report["scope_label"], "From", report["start_date"], "To", report["end_date"]])
    summary.append([])
    summary.append(["Status", "Orders", "% of matched parent orders"])
    _header(summary, 4)
    total = report["fulfilment"]["total"]
    summary.append(["Total", total, 1 if total else 0])
    for bucket in report["fulfilment_buckets"]:
        count = report["fulfilment"]["summary"][bucket]
        summary.append([bucket, count, count / total if total else 0])
    for cell in summary["C"][4:]:
        cell.number_format = "0.0%"
    _finish(summary, 4)

    fulfilment = book.create_sheet("Fulfilment Orders")
    fulfilment_headers = [
        "Display Order", "Uniware Order", "Sub-order Count", "Order Date", "Bucket", "Uniware Status",
        "Channel",
    ]
    if report["scope"] != "overall":
        fulfilment_headers.extend(["Employee", "Role"])
    fulfilment_headers.extend(["Customer", "Email", "Phone", "COD", "Item Count", "Item Statuses", "Shopify Order"])
    fulfilment.append(fulfilment_headers)
    _header(fulfilment, 1)
    for row in report["fulfilment"]["orders"]:
        statuses = ", ".join(f"{key}: {value}" for key, value in row["item_status_summary"].items())
        values = [
            row["display_order_code"], row["sale_order_code"], row["sub_order_count"], row["display_order_date"],
            row["fulfilment_bucket"], row["order_status"], row["channel"],
        ]
        if report["scope"] != "overall":
            values.extend([row["display_name"], row["role"]])
        values.extend([
            row["customer_name"], row["customer_email"], row["customer_phone"],
            "Yes" if row["cod"] else "No", row["item_count"], statuses, row["linked_shopify_order_name"],
        ])
        fulfilment.append(values)
    _finish(fulfilment, 1)

    items = book.create_sheet("Uniware Items")
    items.append([
        "Display Order", "Uniware Order", "Sale Order Item Code", "SKU", "Seller SKU",
        "Product", "Item Status", "Package Status", "Reverse Pickup", "Quantity",
        "Selling Price", "Discount", "MRP", "Updated At",
    ])
    _header(items, 1)
    for row in report["fulfilment"]["items"]:
        items.append([
            row["display_order_code"], row["sale_order_code"], row.get("source_item_code") or row["sale_order_item_code"],
            row["item_sku"], row["seller_sku_code"], row["item_name"], row["status_code"],
            row["shipping_package_status"], row["reverse_pickup_code"], row["quantity"],
            row["selling_price"], row["discount"], row["mrp"], row["updated_at"],
        ])
    _finish(items, 1)
    for row in items.iter_rows(min_row=2, min_col=11, max_col=13):
        for cell in row:
            cell.number_format = "₹#,##0.00"

    reconciliation = book.create_sheet("Reconciliation")
    reconciliation.append(["Control", "Count", "Meaning"])
    _header(reconciliation, 1)
    checks = report["fulfilment"]["reconciliation"]
    reconciliation.append(["Shopify orders", checks["shopify_orders"], "Orders in the selected Shopify scope/date range"])
    reconciliation.append(["Shopify orders in Uniware coverage", checks["shopify_orders_in_coverage"], "Shopify orders inside the synced Uniware coverage window"])
    reconciliation.append(["Matched orders", checks["matched_orders"], "Exact normalized Shopify ↔ Uniware order-code matches"])
    reconciliation.append(["Matched Uniware sub-orders", checks["matched_sub_orders"], "Collapsed into unique Shopify parent orders before status totals"])
    reconciliation.append(["Shopify missing in Uniware", checks["shopify_missing_uniware"], "Shopify orders without a matched Uniware record in local data"])
    reconciliation.append(["Uniware rows", checks["uniware_rows"], "All Uniware parent orders synced for the range"])
    reconciliation.append([])
    reconciliation.append(["Rule", "Definition", ""])
    _header(reconciliation, 9)
    reconciliation.append(["Returned", "Any item is returned, courier-returned, return-requested or replaced", ""])
    reconciliation.append(["Cancelled", "Every item is cancelled", ""])
    reconciliation.append(["Delivered", "Every non-cancelled item is delivered and at least one is delivered", ""])
    reconciliation.append(["Pending", "Any remaining processing or mixed incomplete state", ""])
    _finish(reconciliation, 1)

    output = BytesIO()
    book.save(output)
    output.seek(0)
    return output


def loyalty_comparison_workbook(comparison):
    """Export the exact Customer Loyalty Comparison page values."""
    book = Workbook()
    overview = book.active
    overview.title = "Scope Comparison"
    base = comparison["overall"]
    overview.append(["WELLBEING NUTRITION — CUSTOMER LOYALTY COMPARISON"])
    overview.merge_cells("A1:H1")
    overview["A1"].fill = PatternFill("solid", fgColor=FOREST)
    overview["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    overview.append(["From", base["start_date"], "To", base["end_date"], "Period", base["period"] or "Year / month"])
    overview.append([])
    overview.append(["Scope", "Unique Customers", "Shopify Orders", "Loyal", "Regular", "Repeat", "One-time", "Matched Uniware Orders"])
    labels = {"overall": "Wellbeing Overall", "crm": "Team CRM", "nutritionists": "Team Nutritionists"}
    for key in ("overall", "crm", "nutritionists"):
        row = comparison[key]
        overview.append([
            labels[key], row["customer_count"], row["order_count"], row["tier_counts"]["Loyal"],
            row["tier_counts"]["Regular"], row["tier_counts"]["Repeat"], row["tier_counts"]["One-time"],
            row["fulfilment"]["total"],
        ])
    _header(overview, 4)
    _finish(overview, 4)

    fulfilment = book.create_sheet("Fulfilment Comparison")
    fulfilment.append(["Scope", "Delivered", "Cancelled", "Returned", "Pending", "Total Matched Orders"])
    _header(fulfilment, 1)
    for key in ("overall", "crm", "nutritionists"):
        row = comparison[key]["fulfilment"]
        fulfilment.append([
            labels[key], row["summary"]["Delivered"], row["summary"]["Cancelled"],
            row["summary"]["Returned"], row["summary"]["Pending"], row["total"],
        ])
    _finish(fulfilment, 1)
    output = BytesIO()
    book.save(output)
    output.seek(0)
    return output
