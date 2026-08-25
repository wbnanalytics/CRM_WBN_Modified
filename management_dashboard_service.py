"""Small, auditable management summaries built from the detailed reports.

These helpers do not fetch or reinterpret source data. They aggregate the same
rows used by the operational Shopify and CS reports so dashboard totals always
reconcile to their underlying report.
"""

from collections import defaultdict


def _business_group(role, name=""):
    role_text = (role or "").casefold()
    name_text = (name or "").casefold()
    if "nutrition" in role_text:
        return "Nutritionists"
    if "admin" in role_text or "replacement" in role_text:
        return "CRM Admin / Replacement"
    if "sales" in role_text:
        return "Sales Team"
    if any(word in role_text for word in ("crm - cs", "limechat", "calls", "emails", "orm", "quality", "coach")):
        return "Customer Success"
    if "crm admin" in name_text:
        return "CRM Admin / Replacement"
    return "Other / Unassigned"


def sales_management(report):
    groups = defaultdict(lambda: {"sales": 0.0, "orders": 0, "people": 0, "discounts": 0.0, "gross_sales": 0.0})
    all_sales = all_orders = all_discounts = all_gross_sales = 0
    for row in report.get("rows", []):
        group = _business_group(row.get("role"), row.get("display_name"))
        sales = float(row.get("total") or 0)
        orders = int(row.get("orders") or 0)
        discounts = abs(float(row.get("discounts") or 0))
        gross_sales = float(row.get("gross_sales") or 0)
        groups[group]["sales"] += sales
        groups[group]["orders"] += orders
        groups[group]["people"] += 1
        groups[group]["discounts"] += discounts
        groups[group]["gross_sales"] += gross_sales
        all_sales += sales
        all_orders += orders
        all_discounts += discounts
        all_gross_sales += gross_sales

    order = ("Sales Team", "Nutritionists", "Customer Success", "CRM Admin / Replacement", "Other / Unassigned")
    split = []
    for name in order:
        values = groups[name]
        values.update({
            "name": name,
            "aov": values["sales"] / values["orders"] if values["orders"] else 0,
            "share": values["sales"] / all_sales * 100 if all_sales else 0,
        })
        split.append(dict(values))

    products = []
    for row in report.get("team_product_rows", [])[:12]:
        products.append({
            "product": row.get("product_title") or "Unspecified product",
            "sku": row.get("product_sku") or "—",
            "quantity": int(row.get("quantity") or 0),
            "sales": float(row.get("total") or 0),
            "orders": int(row.get("orders") or 0),
        })
    return {
        "sales": all_sales,
        "orders": all_orders,
        "aov": all_sales / all_orders if all_orders else 0,
        "split": split,
        "products": products,
        "discount_available": all_gross_sales > 0,
        "discounts": all_discounts,
        "gross_sales": all_gross_sales,
        "discount_rate": all_discounts / all_gross_sales * 100 if all_gross_sales else 0,
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
