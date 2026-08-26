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
        })
    return {
        "width": chart_width, "height": 350, "left": left, "right": right,
        "top": top, "bottom": bottom, "bar_width": bar_width, "points": points,
        "line_points": " ".join(f'{row["x"]:.2f},{row["line_y"]:.2f}' for row in points),
        "sales_ticks": [{"y": bottom - plot_height * i / 5, "label": _compact_number(sales_step * i)} for i in range(6)],
        "rate_ticks": [{"y": bottom - plot_height * i / 5, "label": f"{rate_max * i / 5:.0f}%"} for i in range(6)],
    }


def _business_group(role, name=""):
    role_text = (role or "").casefold()
    name_text = (name or "").casefold()
    if "nutrition" in role_text:
        return "WBN – Nutritionists"
    if "admin" in role_text or "replacement" in role_text:
        return "CRM Admin / Replacement"
    if role_text in {"cs", "customer success"} or "sales" in role_text or any(word in role_text for word in ("crm - cs", "limechat", "calls", "emails", "orm", "quality", "coach")):
        return "Team CRM"
    if "crm admin" in name_text:
        return "CRM Admin / Replacement"
    return "Other / Unassigned"


def sales_management(report):
    groups = defaultdict(lambda: {"sales": 0.0, "orders": 0, "people": 0, "discounts": 0.0, "gross_sales": 0.0, "members": []})
    all_sales = all_orders = all_discounts = all_gross_sales = 0
    for row in report.get("rows", []):
        group = _business_group(row.get("role"), row.get("display_name"))
        sales = float(row.get("total") or 0)
        orders = int(row.get("orders") or 0)
        discounts = abs(float(row.get("discounts") or 0))
        gross_sales = sales + discounts
        groups[group]["sales"] += sales
        groups[group]["orders"] += orders
        groups[group]["people"] += 1
        groups[group]["discounts"] += discounts
        groups[group]["gross_sales"] += gross_sales
        groups[group]["members"].append({
            "name": row.get("display_name") or "Unassigned",
            "role": row.get("role") or "Unassigned",
            "sales": sales,
            "discounts": discounts,
            "mrp": gross_sales,
            "discount_rate": discounts / gross_sales * 100 if gross_sales else 0,
            "orders": orders,
            "aov": sales / orders if orders else 0,
        })
        all_sales += sales
        all_orders += orders
        all_discounts += discounts
        all_gross_sales += gross_sales

    order = ("Team CRM", "WBN – Nutritionists", "CRM Admin / Replacement", "Other / Unassigned")
    split = []
    for name in order:
        values = groups[name]
        values["members"].sort(key=lambda row: (-row["sales"], row["name"].casefold()))
        values.update({
            "name": name,
            "aov": values["sales"] / values["orders"] if values["orders"] else 0,
            "share": values["sales"] / all_sales * 100 if all_sales else 0,
            "mrp": values["sales"] + values["discounts"],
            "discount_rate": values["discounts"] / (values["sales"] + values["discounts"]) * 100 if values["sales"] + values["discounts"] else 0,
        })
        split.append(dict(values))

    product_totals = defaultdict(lambda: {"sales": 0.0, "discounts": 0.0, "orders": 0, "quantity": 0})
    product_agents = []
    for row in report.get("product_rows", []):
        key = (row.get("product_title") or "Unspecified product", row.get("product_sku") or "—")
        slot = product_totals[key]
        slot["sales"] += float(row.get("total") or 0)
        slot["discounts"] += abs(float(row.get("discounts") or 0))
        slot["orders"] += int(row.get("orders") or 0)
        slot["quantity"] += int(row.get("quantity") or 0)
        product_agents.append({
            "employee": row.get("display_name") or "Unassigned", "role": row.get("role") or "Unassigned",
            "product": key[0], "sku": key[1], "sales": float(row.get("total") or 0),
            "discounts": abs(float(row.get("discounts") or 0)),
            "mrp": float(row.get("total") or 0) + abs(float(row.get("discounts") or 0)),
            "orders": int(row.get("orders") or 0), "quantity": int(row.get("quantity") or 0),
        })
    products = [{
        "product": key[0], "sku": key[1], "sales": values["sales"],
        "discounts": values["discounts"], "mrp": values["sales"] + values["discounts"],
        "orders": values["orders"], "quantity": values["quantity"],
    } for key, values in product_totals.items()]
    products.sort(key=lambda row: (-row["discounts"], -row["sales"], row["product"]))
    product_agents.sort(key=lambda row: (-row["discounts"], -row["sales"], row["employee"], row["product"]))

    trend = []
    for index, column in enumerate(report.get("columns", [])):
        sales_value = sum(float(row.get("values", [0])[index] or 0) for row in report.get("rows", []) if row.get("include_in_team"))
        discount_value = sum(abs(float(row.get("discount_values", [0])[index] or 0)) for row in report.get("rows", []) if row.get("include_in_team"))
        mrp_value = sales_value + discount_value
        trend.append({
            "label": column["label"], "sales": sales_value, "discounts": discount_value,
            "mrp": mrp_value, "discount_rate": discount_value / mrp_value * 100 if mrp_value else 0,
        })
    trend_max = max((max(row["sales"], row["discounts"]) for row in trend), default=0) or 1
    for row in trend:
        row["sales_pct"] = row["sales"] / trend_max * 100
        row["discount_pct"] = row["discounts"] / trend_max * 100
    return {
        "sales": all_sales,
        "orders": all_orders,
        "aov": all_sales / all_orders if all_orders else 0,
        "split": split,
        "products": products,
        "product_agents": product_agents,
        "trend": trend,
        "chart": _sales_discount_chart(trend),
        "discount_available": all_gross_sales > 0,
        "discounts": all_discounts,
        "gross_sales": all_sales + all_discounts,
        "mrp": all_sales + all_discounts,
        "discount_rate": all_discounts / (all_sales + all_discounts) * 100 if all_sales + all_discounts else 0,
        "unassigned_sales": groups["Other / Unassigned"]["sales"],
    }


def cs_management(report):
    targets = {
        "email": {"frt": 4 * 3600, "art": 4 * 3600, "resolution": 48 * 3600},
        "inbound": {},
        "chats": {"frt": 30, "art": 2 * 3600, "resolution": 48 * 3600},
        "orm": {"frt": 30, "art": 4 * 3600, "resolution": 48 * 3600},
    }
    channels = []
    for item in report.get("metrics", []):
        clean_total = max(0, item.get("total", 0) - item.get("spam", 0))
        channel_targets = targets[item["key"]]
        frt = item.get("frt_without_spam")
        art = item.get("art_without_spam")
        channels.append({
            **item,
            "clean_total": clean_total,
            "open_rate": item.get("open", 0) / clean_total * 100 if clean_total else 0,
            "frt_target": channel_targets.get("frt"),
            "art_target": channel_targets.get("art"),
            "frt_met": frt is not None and channel_targets.get("frt") is not None and frt <= channel_targets["frt"],
            "art_met": art is not None and channel_targets.get("art") is not None and art <= channel_targets["art"],
            "source_note": "Zoho ticket cohort" if item["source"] == "Zoho Desk" else "BIK ticket export",
        })
    attention = sorted(channels, key=lambda row: (-row["open"], row["label"]))
    return {
        "channels": channels,
        "attention": attention,
        "total": report.get("total", 0),
        "resolved": report.get("resolved", 0),
        "spam": report.get("spam", 0),
        "open": sum(item.get("open", 0) for item in channels),
        "clean_total": sum(item.get("clean_total", 0) for item in channels),
    }
