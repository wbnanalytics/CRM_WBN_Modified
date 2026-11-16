"""Small, auditable management summaries built from the detailed reports.

These helpers do not fetch or reinterpret source data. They aggregate the same
rows used by the operational Shopify and CS reports so dashboard totals always
reconcile to their underlying report.
"""

import math
from collections import defaultdict


def _compact_number(value):
    value = float(value or 0)
    if value >= 10_000_000:
        return f"{value / 10_000_000:.1f}Cr"
    if value >= 100_000:
        return f"{value / 100_000:.1f}L"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return f"{value:.0f}"


def _sales_discount_chart(trend):
    """Build responsive SVG coordinates for bars plus a percentage line."""
    count = len(trend)
    chart_width = max(760, count * 48 + 140)
    left, right, top, bottom = 70, chart_width - 70, 28, 286
    plot_width, plot_height = right - left, bottom - top
    sales_peak = max((row["sales"] for row in trend), default=0)
    sales_step = max(1, math.ceil((sales_peak / 5) / 100_000) * 100_000)
    sales_max = sales_step * 5
    rate_peak = max((row["discount_rate"] for row in trend), default=0)
    rate_max = max(5, math.ceil(rate_peak / 5) * 5)
    slot = plot_width / max(count, 1)
    bar_width = min(26, max(10, slot * 0.52))
    points = []
    for index, row in enumerate(trend):
        x = left + slot * (index + 0.5)
        bar_height = row["sales"] / sales_max * plot_height if sales_max else 0
        line_y = bottom - (row["discount_rate"] / rate_max * plot_height if rate_max else 0)
        points.append({
            **row, "x": x, "bar_x": x - bar_width / 2, "bar_y": bottom - bar_height,
            "bar_height": bar_height, "line_y": line_y,
            "hit_x": left + slot * index, "sales_label": _compact_number(row["sales"]),
        })
    best_sales = max(trend, key=lambda row: row["sales"], default=None)
    highest_discount = max(trend, key=lambda row: row["discount_rate"], default=None)
    active_days = [row for row in trend if row["sales"] or row["discounts"]]
    return {
        "width": chart_width, "height": 350, "left": left, "right": right,
        "top": top, "bottom": bottom, "bar_width": bar_width, "slot": slot, "points": points,
        "line_points": " ".join(f'{row["x"]:.2f},{row["line_y"]:.2f}' for row in points),
        "sales_ticks": [{"y": bottom - plot_height * i / 5, "label": _compact_number(sales_step * i)} for i in range(6)],
        "rate_ticks": [{"y": bottom - plot_height * i / 5, "label": f"{rate_max * i / 5:.0f}%"} for i in range(6)],
        "best_sales": best_sales,
        "highest_discount": highest_discount,
        "average_sales": sum(row["sales"] for row in active_days) / len(active_days) if active_days else 0,
    }


def _two_line_chart(overall_trend, scoped_trend, value_key):
    """Build a same-axis comparison chart for Wellbeing versus one team."""
    count = len(overall_trend)
    chart_width = max(760, count * 48 + 140)
    left, right, top, bottom = 70, chart_width - 70, 28, 236
    plot_width, plot_height = right - left, bottom - top
    peak = max(
        [float(row.get(value_key) or 0) for row in overall_trend]
        + [float(row.get(value_key) or 0) for row in scoped_trend]
        + [0]
    )
    step = max(1, math.ceil((peak / 5) / 100_000) * 100_000)
    axis_max = step * 5
    slot = plot_width / max(count, 1)
    points = []
    for index, overall in enumerate(overall_trend):
        scoped = scoped_trend[index] if index < len(scoped_trend) else {}
        x = left + slot * (index + 0.5)
        overall_value = float(overall.get(value_key) or 0)
        scoped_value = float(scoped.get(value_key) or 0)
        points.append({
            "label": overall["label"], "x": x,
            "overall": overall_value, "scoped": scoped_value,
            "overall_y": bottom - overall_value / axis_max * plot_height if axis_max else bottom,
            "scoped_y": bottom - scoped_value / axis_max * plot_height if axis_max else bottom,
            "overall_label": _compact_number(overall_value), "scoped_label": _compact_number(scoped_value),
        })
    return {
        "width": chart_width, "height": 292, "left": left, "right": right, "top": top, "bottom": bottom,
        "points": points,
        "overall_line": " ".join(f'{row["x"]:.2f},{row["overall_y"]:.2f}' for row in points),
        "scoped_line": " ".join(f'{row["x"]:.2f},{row["scoped_y"]:.2f}' for row in points),
        "ticks": [{"y": bottom - plot_height * i / 5, "label": _compact_number(step * i)} for i in range(6)],
    }


def _business_group(role, name=""):
    role_text = (role or "").casefold()
    name_text = (name or "").casefold()
    if "nutrition" in role_text:
        return "Team Nutritionists"
    if "admin" in role_text or "replacement" in role_text:
        return "CRM Admin / Replacement"
    if role_text in {"cs", "customer success"} or "sales" in role_text or any(word in role_text for word in ("crm - cs", "limechat", "calls", "emails", "orm", "quality", "coach")):
        return "Team CRM"
    if "crm admin" in name_text:
        return "CRM Admin / Replacement"
    return "Other / Unassigned"


def matches_sales_scope(row, scope):
    group = _business_group(row.get("role"), row.get("display_name"))
    if scope == "crm":
        return group in {"Team CRM", "CRM Admin / Replacement"}
    if scope == "nutritionists":
        return group == "Team Nutritionists"
    return True


def is_crm_admin(row):
    return _business_group(row.get("role"), row.get("display_name")) == "CRM Admin / Replacement"


def product_management(report, scope="overall"):
    """Product reporting excludes CRM Admin from every metric and row."""
    filtered_report = {
        **report,
        "rows": [row for row in report.get("rows", []) if not is_crm_admin(row)],
        "product_rows": [row for row in report.get("product_rows", []) if not is_crm_admin(row)],
    }
    result = sales_management(filtered_report, scope)
    result["products_by_quantity"] = sorted(
        result["products"],
        key=lambda row: (-row["quantity"], -row["sales"], row["product"]),
    )
    result["product_quantity"] = sum(row["quantity"] for row in result["products"])
    result["product_sales"] = sum(row["sales"] for row in result["products"])
    result["product_discounts"] = sum(row["discounts"] for row in result["products"])
    result["product_mrp"] = result["product_sales"] + result["product_discounts"]
    result["product_discount_rate"] = (
        result["product_discounts"] / result["product_mrp"] * 100
        if result["product_mrp"] else 0
    )
    return result


def sales_management(report, scope="overall"):
    if scope not in {"overall", "crm", "nutritionists"}:
        scope = "overall"
    scoped_rows = [row for row in report.get("rows", []) if matches_sales_scope(row, scope)]
    scoped_product_rows = [row for row in report.get("product_rows", []) if matches_sales_scope(row, scope)]
    groups = defaultdict(lambda: {"sales": 0.0, "orders": 0, "people": 0, "discounts": 0.0, "gross_sales": 0.0, "members": []})
    all_sales = all_orders = all_discount_signed = eligible_gross_sales = eligible_sales = 0
    for row in scoped_rows:
        group = _business_group(row.get("role"), row.get("display_name"))
        sales = float(row.get("total") or 0)
        orders = int(row.get("orders") or 0)
        discount_signed = 0.0 if group == "CRM Admin / Replacement" else float(row.get("discounts") or 0)
        discounts = abs(discount_signed)
        gross_sales = float(row.get("gross_sales") or 0)
        groups[group]["sales"] += sales
        groups[group]["orders"] += orders
        groups[group]["people"] += 1
        groups[group]["discounts"] += discount_signed
        groups[group]["gross_sales"] += gross_sales
        groups[group]["members"].append({
            "employee_key": row.get("employee_key") or "",
            "name": row.get("display_name") or "Unassigned",
            "role": row.get("role") or "Unassigned",
            "sales": sales,
            "discounts": discounts,
            "mrp": sales + discounts,
            "gross_sales": gross_sales,
            "discount_rate": discounts / gross_sales * 100 if gross_sales else 0,
            "orders": orders,
            "aov": sales / orders if orders else 0,
        })
        all_sales += sales
        all_orders += orders
        all_discount_signed += discount_signed
        if group != "CRM Admin / Replacement":
            eligible_sales += sales
            eligible_gross_sales += gross_sales

    order = {
        "crm": ("Team CRM", "CRM Admin / Replacement"),
        "nutritionists": ("Team Nutritionists",),
    }.get(scope, ("Team CRM", "Team Nutritionists", "CRM Admin / Replacement", "Other / Unassigned"))
    split = []
    for name in order:
        values = groups[name]
        values["discounts"] = abs(values["discounts"])
        values["members"].sort(key=lambda row: (-row["sales"], row["name"].casefold()))
        values.update({
            "name": name,
            "aov": values["sales"] / values["orders"] if values["orders"] else 0,
            "share": values["sales"] / all_sales * 100 if all_sales else 0,
            "mrp": values["sales"] + values["discounts"],
            "discount_rate": values["discounts"] / values["gross_sales"] * 100 if values["gross_sales"] else 0,
        })
        split.append(dict(values))

    product_totals = defaultdict(lambda: {"sales": 0.0, "discounts": 0.0, "orders": 0, "quantity": 0})
    product_agents = []
    for row in scoped_product_rows:
        key = (row.get("product_title") or "Unspecified product", row.get("product_sku") or "—")
        slot = product_totals[key]
        row_discount_signed = 0.0 if _business_group(row.get("role"), row.get("display_name")) == "CRM Admin / Replacement" else float(row.get("discounts") or 0)
        row_discount = abs(row_discount_signed)
        slot["sales"] += float(row.get("total") or 0)
        slot["discounts"] += row_discount_signed
        slot["orders"] += int(row.get("orders") or 0)
        slot["quantity"] += int(row.get("quantity") or 0)
        product_agents.append({
            "employee": row.get("display_name") or "Unassigned", "role": row.get("role") or "Unassigned",
            "product": key[0], "sku": key[1], "sales": float(row.get("total") or 0),
            "discounts": row_discount,
            "mrp": float(row.get("total") or 0) + row_discount,
            "orders": int(row.get("orders") or 0), "quantity": int(row.get("quantity") or 0),
        })
    products = [{
        "product": key[0], "sku": key[1], "sales": values["sales"],
        "discounts": abs(values["discounts"]), "mrp": values["sales"] + abs(values["discounts"]),
        "orders": values["orders"], "quantity": values["quantity"],
    } for key, values in product_totals.items()]
    products.sort(key=lambda row: (-row["discounts"], -row["sales"], row["product"]))
    product_agents.sort(key=lambda row: (-row["discounts"], -row["sales"], row["employee"], row["product"]))

    trend = []
    for index, column in enumerate(report.get("columns", [])):
        # Overall Sales includes every displayed business group. Keep the
        # trend and its Excel export on the exact same complete row set.
        sales_value = sum(float(row.get("values", [0])[index] or 0) for row in scoped_rows)
        eligible_sales_value = sum(
            float(row.get("values", [0])[index] or 0) for row in scoped_rows
            if _business_group(row.get("role"), row.get("display_name")) != "CRM Admin / Replacement"
        )
        discount_value = abs(sum(
            float(row.get("discount_values", [0])[index] or 0) for row in scoped_rows
            if _business_group(row.get("role"), row.get("display_name")) != "CRM Admin / Replacement"
        ))
        eligible_gross_value = sum(
            float(row.get("gross_values", [0])[index] or 0) for row in scoped_rows
            if _business_group(row.get("role"), row.get("display_name")) != "CRM Admin / Replacement"
        )
        # Selling price always represents the complete selected scope. Only
        # CRM Admin's discount is excluded from the eligible discount amount.
        mrp_value = sales_value + discount_value
        trend.append({
            "label": column["label"], "sales": sales_value, "discounts": discount_value,
            "mrp": mrp_value, "eligible_sales": eligible_sales_value,
            "discount_rate": discount_value / eligible_gross_value * 100 if eligible_gross_value else 0,
        })
    trend_max = max((max(row["sales"], row["discounts"]) for row in trend), default=0) or 1
    for row in trend:
        row["sales_pct"] = row["sales"] / trend_max * 100
        row["discount_pct"] = row["discounts"] / trend_max * 100
    overall_trend = []
    for index, column in enumerate(report.get("columns", [])):
        overall_sales = sum(float(row.get("values", [0])[index] or 0) for row in report.get("rows", []))
        overall_eligible_sales = sum(
            float(row.get("values", [0])[index] or 0) for row in report.get("rows", [])
            if _business_group(row.get("role"), row.get("display_name")) != "CRM Admin / Replacement"
        )
        overall_discount = abs(sum(
            float(row.get("discount_values", [0])[index] or 0) for row in report.get("rows", [])
            if _business_group(row.get("role"), row.get("display_name")) != "CRM Admin / Replacement"
        ))
        overall_eligible_gross = sum(
            float(row.get("gross_values", [0])[index] or 0) for row in report.get("rows", [])
            if _business_group(row.get("role"), row.get("display_name")) != "CRM Admin / Replacement"
        )
        overall_mrp = overall_sales + overall_discount
        overall_trend.append({
            "label": column["label"], "sales": overall_sales, "discounts": overall_discount,
            "mrp": overall_mrp, "eligible_sales": overall_eligible_sales,
            "discount_rate": overall_discount / overall_eligible_gross * 100 if overall_eligible_gross else 0,
        })
    comparison = None if scope == "overall" else {
        "sales": _two_line_chart(overall_trend, trend, "sales"),
        "discounts": _two_line_chart(overall_trend, trend, "discounts"),
        "overall_trend": overall_trend,
        "overall_sales": sum(row["sales"] for row in overall_trend),
        "overall_discounts": sum(row["discounts"] for row in overall_trend),
        "scoped_sales": all_sales,
        "scoped_discounts": abs(all_discount_signed),
    }
    return {
        "scope": scope,
        "sales": all_sales,
        "eligible_sales": eligible_sales,
        "orders": all_orders,
        "aov": all_sales / all_orders if all_orders else 0,
        "split": split,
        "products": products,
        "product_agents": product_agents,
        "trend": trend,
        "chart": _sales_discount_chart(trend),
        "comparison": comparison,
        "discount_available": eligible_sales + abs(all_discount_signed) > 0,
        "discounts": abs(all_discount_signed),
        "gross_sales": eligible_gross_sales,
        "mrp": all_sales + abs(all_discount_signed),
        "total_adjusted_mrp": all_sales + abs(all_discount_signed),
        "discount_rate": abs(all_discount_signed) / eligible_gross_sales * 100 if eligible_gross_sales else 0,
        "unassigned_sales": groups["Other / Unassigned"]["sales"],
    }
