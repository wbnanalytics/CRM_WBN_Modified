import os
import re
from collections import defaultdict
from calendar import monthrange
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from database import (
    apply_shopify_order_attribution,
    finish_sync_run, latest_shopify_sync, list_shopify_employees,
    replace_shopify_order_details, replace_shopify_product_sales, replace_shopify_sales,
    seed_shopify_employees, shopify_orders_between, shopify_product_sales_between, shopify_sales_between,
    shopify_sales_summary, shopify_targets_between, start_sync_run,
)


IST = ZoneInfo("Asia/Kolkata")
API_VERSION = "2026-07"

ALIASES = {
    "eeshikapoojari": ("eeshikapujari", "Eeshika Pujari"),
    "shahnawazalam": ("shahnawazansari", "Shahnawaz Ansari"),
}

DEFAULT_EMPLOYEES = (
    ("Babita Bagdi", "CRM - Sales", 170625),
    ("Kajol Rajput", "CRM - Sales", 170625),
    ("Akash Varma", "CRM - Sales", 170625),
    ("Eeshika Pujari", "CRM - Sales", 170625),
    ("Rachana Rathod", "CRM - Sales", 170625),
    ("Shubh Pajwani", "CRM - Sales", 170625),
    ("Akshay Shah", "CRM - Sales", 170625),
    ("Shahnawaz Ansari", "CRM - Sales", 170625),
    ("Ajay Cynthala", "CRM - Sales", 170625),
    ("Yusuf Shaikh", "CRM - Sales", 170625),
    ("Muskan Padvekar", "CRM - CS - Emails", 85313),
    ("Akil Shaikh", "CRM - CS - Emails", 85313),
    ("Sampada Nayak", "CRM - CS - ORM", 85313),
    ("Spriha Shukla", "CRM - CS - Calls", 85313),
    ("Salman Shaikh", "CRM - CS - Calls", 85313),
    ("Tapasya Dhekane", "CRM - CS - ORM", 85313),
    ("Aditya Shinde", "CRM - CS - Limechat", 85313),
    ("Shubham Kadam", "CRM - CS - Calls", 85313),
    ("Purvi Gawade", "CRM - CS - Calls", 85313),
    ("Ahanti Vichare", "CRM - Quality Assurance", 0),
    ("Atul Aherwar", "CRM - Team Coach", 0),
    ("Adina Rakhange", "Nutritionist", 0),
    ("Shezana Bhojani", "Nutritionist", 0),
    ("Yash Sawant", "Nutritionist", 0),
    ("Shareena Dsilva", "Nutritionist", 0),
)


class ShopifyError(RuntimeError):
    pass


def _env(*keys):
    return next((os.getenv(key) for key in keys if os.getenv(key)), None)


def shopify_configured():
    return all(
        (
            _env("SHOPIFY_CLIENT_ID", "shopify-client-id"),
            _env("SHOPIFY_CLIENT_SECRET", "shopify-client-secret"),
            _env("SHOPIFY_STORE", "shopify-store"),
        )
    )


def _employee_identity(name):
    source = " ".join(str(name or "").split()).strip()
    key = re.sub(r"[^a-z0-9]+", "", source.casefold())
    if key in ALIASES:
        return ALIASES[key]
    display = " ".join(part.capitalize() for part in source.split())
    return key, display


def seed_default_employees():
    rows = []
    for name, role, target in DEFAULT_EMPLOYEES:
        key, display = _employee_identity(name)
        rows.append({
            "employee_key": key, "source_name": name, "display_name": display,
            "role": role, "weekly_target": target, "include_in_team": True,
        })
    seed_shopify_employees(rows)


def _store_handle():
    value = (_env("SHOPIFY_STORE", "shopify-store") or "").strip()
    value = value.removeprefix("https://").rstrip("/")
    return value.removesuffix(".myshopify.com")


def _access_token():
    response = requests.post(
        f"https://{_store_handle()}.myshopify.com/admin/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": _env("SHOPIFY_CLIENT_ID", "shopify-client-id"),
            "client_secret": _env("SHOPIFY_CLIENT_SECRET", "shopify-client-secret"),
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("access_token"):
        raise ShopifyError(payload.get("error_description") or "Shopify did not issue an access token")
    return payload["access_token"]


def _shopifyql(token, query):
    graphql = """query Report($query: String!) {
      shopifyqlQuery(query: $query) {
        tableData { columns { name dataType displayName } rows }
        parseErrors
      }
    }"""
    response = requests.post(
        f"https://{_store_handle()}.myshopify.com/admin/api/{API_VERSION}/graphql.json",
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"query": graphql, "variables": {"query": query}},
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise ShopifyError("; ".join(item.get("message", "Shopify query failed") for item in payload["errors"]))
    result = (payload.get("data") or {}).get("shopifyqlQuery") or {}
    if result.get("parseErrors"):
        raise ShopifyError("; ".join(str(item) for item in result["parseErrors"]))
    return (result.get("tableData") or {}).get("rows") or []


def _shopifyql_all(token, query, page_size=1000):
    rows = []
    offset = 0
    while True:
        page = _shopifyql(token, f"{query}\nLIMIT {page_size} OFFSET {offset}")
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size
        if offset >= 25000:
            raise ShopifyError("Shopify returned more than 25,000 grouped rows; reduce the sync date range")


def _admin_graphql(token, query, variables=None):
    response = requests.post(
        f"https://{_store_handle()}.myshopify.com/admin/api/{API_VERSION}/graphql.json",
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}}, timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise ShopifyError("; ".join(item.get("message", "Shopify query failed") for item in payload["errors"]))
    return payload.get("data") or {}


def _payment_type(gateways, financial_status, tags):
    text = " ".join([*(gateways or []), *(tags or [])]).casefold().replace("_", " ")
    if "cash on delivery" in text or re.search(r"\bcod\b", text):
        return "COD"
    if financial_status in {"PAID", "AUTHORIZED", "PARTIALLY_PAID", "PARTIALLY_REFUNDED", "REFUNDED"}:
        return "Prepaid"
    return "Other / Pending"


def _shopify_orders(token, start, end):
    query = """query Orders($search: String!, $after: String) {
      orders(first: 250, query: $search, after: $after, sortKey: CREATED_AT) {
        nodes {
          id legacyResourceId name createdAt email phone displayFinancialStatus
          paymentGatewayNames sourceName tags
          currentTotalPriceSet { shopMoney { amount currencyCode } }
          currentTotalDiscountsSet { shopMoney { amount currencyCode } }
          customer {
            displayName
            defaultEmailAddress { emailAddress }
            defaultPhoneNumber { phoneNumber }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }"""
    search = f"created_at:>={start.isoformat()} created_at:<{(end + timedelta(days=1)).isoformat()}"
    after = None
    rows = []
    while True:
        connection = _admin_graphql(token, query, {"search": search, "after": after})["orders"]
        for order in connection["nodes"]:
            created = datetime.fromisoformat(order["createdAt"].replace("Z", "+00:00"))
            local_date = created.astimezone(IST).date()
            if local_date < start or local_date > end:
                continue
            customer = order.get("customer") or {}
            email_address = (customer.get("defaultEmailAddress") or {}).get("emailAddress")
            phone_number = (customer.get("defaultPhoneNumber") or {}).get("phoneNumber")
            total_money = ((order.get("currentTotalPriceSet") or {}).get("shopMoney") or {})
            discount_money = ((order.get("currentTotalDiscountsSet") or {}).get("shopMoney") or {})
            gateways = order.get("paymentGatewayNames") or []
            rows.append({
                "order_id": order["id"], "order_name": order["name"],
                "order_number": str(order.get("legacyResourceId") or ""),
                "created_at": order["createdAt"], "local_date": local_date.isoformat(),
                "customer_name": customer.get("displayName") or "",
                "customer_email": order.get("email") or email_address or "",
                "customer_phone": order.get("phone") or phone_number or "",
                "payment_type": _payment_type(gateways, order.get("displayFinancialStatus"), order.get("tags")),
                "payment_gateways": ", ".join(gateways),
                "financial_status": order.get("displayFinancialStatus") or "",
                "total_price": float(total_money.get("amount") or 0),
                "total_discounts": float(discount_money.get("amount") or 0),
                "currency_code": total_money.get("currencyCode") or discount_money.get("currencyCode") or "INR",
                "source_name": order.get("sourceName") or "",
            })
        page_info = connection["pageInfo"]
        if not page_info["hasNextPage"]:
            return rows
        after = page_info["endCursor"]


def _shopify_order_attribution(token, start, end):
    query = f"""FROM sales
SHOW total_sales, gross_sales, discounts, orders
GROUP BY staff_member_name, order_id, order_name
SINCE {start.isoformat()} UNTIL {end.isoformat()}
ORDER BY staff_member_name ASC, order_id ASC"""
    rows = []
    for row in _shopifyql_all(token, query):
        source_name = str(row.get("staff_member_name") or "").strip()
        if not source_name:
            continue
        employee_key, _display_name = _employee_identity(source_name)
        if employee_key:
            rows.append({
                "employee_key": employee_key,
                "source_employee_name": source_name,
                "order_number": str(row.get("order_id") or ""),
                "order_name": str(row.get("order_name") or ""),
            })
    return rows


def sync_shopify_order_attribution(start_date, end_date):
    start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    end = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
    if start > end:
        raise ValueError("Start date must not be after end date")
    token = _access_token()
    rows = _shopify_order_attribution(token, start, end)
    return apply_shopify_order_attribution(rows)


def _month_chunks(start, end):
    cursor = start
    while cursor <= end:
        chunk_end = min(end, date(cursor.year, cursor.month, monthrange(cursor.year, cursor.month)[1]))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def sync_shopify(start_date, end_date, order_detail_start=None):
    start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    end = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
    order_detail_start = (
        date.fromisoformat(order_detail_start) if isinstance(order_detail_start, str) else order_detail_start
    )
    if start > end:
        raise ValueError("Start date must not be after end date")
    if not shopify_configured():
        raise ShopifyError("Shopify credentials are not configured")
    seed_default_employees()
    run_id = start_sync_run("shopify", "employee_sales", start.isoformat(), end.isoformat())
    received = saved = 0
    try:
        token = _access_token()
        for chunk_start, chunk_end in _month_chunks(start, end):
            query = f"""FROM sales
SHOW gross_sales, discounts, total_sales, orders
TIMESERIES day
GROUP BY staff_member_name
SINCE {chunk_start.isoformat()} UNTIL {chunk_end.isoformat()}
ORDER BY day ASC, staff_member_name ASC"""
            source_rows = _shopifyql_all(token, query)
            received += len(source_rows)
            combined = defaultdict(lambda: {"total_sales": 0.0, "gross_sales": 0.0, "discounts": 0.0, "order_count": 0, "source_names": set()})
            discovered = {}
            for row in source_rows:
                source_name = row.get("staff_member_name")
                if not source_name:
                    continue
                employee_key, display_name = _employee_identity(source_name)
                day = str(row.get("day") or "")[:10]
                if not employee_key or not day:
                    continue
                item = combined[(day, employee_key)]
                item["total_sales"] += float(row.get("total_sales") or 0)
                item["gross_sales"] += float(row.get("gross_sales") or 0)
                item["discounts"] += float(row.get("discounts") or 0)
                item["order_count"] += int(float(row.get("orders") or 0))
                item["source_names"].add(str(source_name))
                discovered[employee_key] = {
                    "employee_key": employee_key, "source_name": str(source_name),
                    "display_name": display_name, "role": "Unassigned",
                    "weekly_target": 0, "include_in_team": False,
                }
            if discovered:
                seed_shopify_employees(discovered.values())
            rows = [
                {
                    "day": day, "employee_key": employee_key,
                    "source_employee_name": " / ".join(sorted(item["source_names"])),
                    "total_sales": item["total_sales"], "gross_sales": item["gross_sales"],
                    "discounts": item["discounts"], "order_count": item["order_count"],
                }
                for (day, employee_key), item in combined.items()
            ]
            previous = shopify_sales_summary(chunk_start.isoformat(), chunk_end.isoformat())
            if previous["row_count"] and not rows:
                raise ShopifyError(
                    f"Shopify returned an empty snapshot for {chunk_start} to {chunk_end}; existing data was preserved"
                )
            saved += replace_shopify_sales(chunk_start.isoformat(), chunk_end.isoformat(), rows)
            product_query = f"""FROM sales
SHOW gross_sales, discounts, total_sales, orders, net_items_sold
TIMESERIES day
GROUP BY staff_member_name, product_title, product_variant_sku
SINCE {chunk_start.isoformat()} UNTIL {chunk_end.isoformat()}
ORDER BY day ASC, staff_member_name ASC, product_title ASC, product_variant_sku ASC"""
            product_source_rows = _shopifyql_all(token, product_query)
            received += len(product_source_rows)
            product_combined = defaultdict(lambda: {"total_sales": 0.0, "gross_sales": 0.0, "discounts": 0.0, "order_count": 0, "quantity_sold": 0, "source_names": set()})
            for row in product_source_rows:
                source_name = row.get("staff_member_name")
                day = str(row.get("day") or "")[:10]
                title = str(row.get("product_title") or "Unspecified product").strip()
                sku = str(row.get("product_variant_sku") or "").strip()
                employee_key, _display_name = _employee_identity(source_name)
                if not source_name or not employee_key or not day:
                    continue
                item = product_combined[(day, employee_key, title, sku)]
                item["total_sales"] += float(row.get("total_sales") or 0)
                item["gross_sales"] += float(row.get("gross_sales") or 0)
                item["discounts"] += float(row.get("discounts") or 0)
                item["order_count"] += int(float(row.get("orders") or 0))
                item["quantity_sold"] += int(float(row.get("net_items_sold") or 0))
                item["source_names"].add(str(source_name))
            product_rows = [{
                "day": day, "employee_key": employee_key,
                "source_employee_name": " / ".join(sorted(item["source_names"])),
                "product_title": title, "product_sku": sku,
                "total_sales": item["total_sales"], "gross_sales": item["gross_sales"],
                "discounts": item["discounts"], "order_count": item["order_count"],
                "quantity_sold": item["quantity_sold"],
            } for (day, employee_key, title, sku), item in product_combined.items()]
            replace_shopify_product_sales(chunk_start.isoformat(), chunk_end.isoformat(), product_rows)
            order_start = max(chunk_start, order_detail_start) if order_detail_start else chunk_start
            if order_start > chunk_end:
                continue
            order_rows = _shopify_orders(token, order_start, chunk_end)
            attribution_rows = _shopify_order_attribution(token, order_start, chunk_end)
            attribution_by_number = {row["order_number"]: row for row in attribution_rows if row["order_number"]}
            attribution_by_name = {row["order_name"]: row for row in attribution_rows if row["order_name"]}
            for order in order_rows:
                attribution = attribution_by_number.get(order["order_number"]) or attribution_by_name.get(order["order_name"])
                if attribution:
                    order["employee_key"] = attribution["employee_key"]
                    order["source_employee_name"] = attribution["source_employee_name"]
            received += len(order_rows)
            previous_orders = shopify_orders_between(order_start.isoformat(), chunk_end.isoformat())
            if previous_orders and not order_rows:
                raise ShopifyError(
                    f"Shopify returned an empty order snapshot for {order_start} to {chunk_end}; existing order data was preserved"
                )
            saved += replace_shopify_order_details(order_start.isoformat(), chunk_end.isoformat(), order_rows)
        finish_sync_run(run_id, "success", received, saved)
        return {"run_id": run_id, "rows_received": received, "rows_saved": saved}
    except Exception as exc:
        finish_sync_run(run_id, "failed", received, saved, str(exc)[:1000])
        raise


def _period_columns(period, anchor):
    if period == "daily":
        start = end = anchor
        columns = [{"key": anchor.isoformat(), "label": anchor.strftime("%d %b %Y"), "start": anchor, "end": anchor}]
        default_target_divisor = 5.5
    elif period == "weekly":
        week_end = anchor + timedelta(days=(4 - anchor.weekday()) % 7)
        start, end = week_end - timedelta(days=6), week_end
        columns = []
        cursor = start
        while cursor <= end:
            columns.append({
                "key": cursor.isoformat(), "label": cursor.strftime("%a %d %b").upper(),
                "start": cursor, "end": cursor,
            })
            cursor += timedelta(days=1)
        default_target_divisor = 7
    elif period == "weekly_month":
        month_start = date(anchor.year, anchor.month, 1)
        month_end = date(anchor.year, anchor.month, monthrange(anchor.year, anchor.month)[1])
        first_friday = month_start + timedelta(days=(4 - month_start.weekday()) % 7)
        last_friday = month_end - timedelta(days=(month_end.weekday() - 4) % 7)
        columns = []
        week_end = first_friday
        while week_end <= last_friday:
            columns.append({
                "key": week_end.isoformat(), "label": f"WE {week_end.strftime('%d %b').upper()}",
                "start": week_end - timedelta(days=6), "end": week_end,
            })
            week_end += timedelta(days=7)
        start, end = columns[0]["start"], columns[-1]["end"]
        default_target_divisor = 1
    elif period == "weekly_year":
        year_start, year_end = date(anchor.year, 1, 1), date(anchor.year, 12, 31)
        first_friday = year_start + timedelta(days=(4 - year_start.weekday()) % 7)
        last_friday = year_end - timedelta(days=(year_end.weekday() - 4) % 7)
        columns = []
        week_end = first_friday
        while week_end <= last_friday:
            columns.append({
                "key": week_end.isoformat(), "label": f"WE {week_end.strftime('%d %b').upper()}",
                "start": week_end - timedelta(days=6), "end": week_end,
            })
            week_end += timedelta(days=7)
        start, end = columns[0]["start"], columns[-1]["end"]
        default_target_divisor = 1
    elif period == "dashboard":
        month_start = date(anchor.year, anchor.month, 1)
        month_end = date(anchor.year, anchor.month, monthrange(anchor.year, anchor.month)[1])
        columns = []
        cursor = month_start
        while cursor <= month_end:
            week_end = min(cursor + timedelta(days=6 - cursor.weekday()), month_end)
            columns.append({
                "key": cursor.isoformat(),
                "label": f"{cursor.strftime('%d %b')} – {week_end.strftime('%d %b')}",
                "start": cursor, "end": week_end,
            })
            cursor = week_end + timedelta(days=1)
        start, end = month_start, month_end
        default_target_divisor = 1
    elif period == "monthly":
        start = date(anchor.year, anchor.month, 1)
        end = date(anchor.year, anchor.month, monthrange(anchor.year, anchor.month)[1])
        columns = []
        cursor = start
        while cursor <= end:
            columns.append({
                "key": cursor.isoformat(), "label": cursor.strftime("%d %b"),
                "start": cursor, "end": cursor,
            })
            cursor += timedelta(days=1)
        default_target_divisor = 0.25
    else:
        period = "yearly"
        start, end = date(anchor.year, 1, 1), date(anchor.year, 12, 31)
        columns = []
        for month in range(1, 13):
            month_start = date(anchor.year, month, 1)
            month_end = date(anchor.year, month, monthrange(anchor.year, month)[1])
            columns.append({
                "key": month_start.isoformat(), "label": month_start.strftime("%b"),
                "start": month_start, "end": month_end,
            })
        default_target_divisor = 0.25
    return period, start, end, columns, default_target_divisor


def sales_report(period="daily", anchor=None, employee_key=""):
    anchor = anchor or datetime.now(IST).date()
    period, start, end, columns, target_divisor = _period_columns(period, anchor)
    sales = shopify_sales_between(start.isoformat(), end.isoformat())
    product_sales = shopify_product_sales_between(start.isoformat(), end.isoformat())
    target_period = "weekly" if period in {"dashboard", "weekly", "weekly_month", "weekly_year"} else ("monthly" if period == "yearly" else period)
    target_start = date(anchor.year, 1 if period in {"yearly", "weekly_year"} else anchor.month, 1)
    target_end = end if period in {"yearly", "weekly_year"} else target_start
    override_rows = shopify_targets_between(target_period, target_start.isoformat(), target_end.isoformat())
    overrides = {
        (item["employee_key"], item["period_start"]): float(item["target_amount"])
        for item in override_rows
    }
    configs = {item["employee_key"]: item for item in list_shopify_employees() if item["active"]}
    for item in sales:
        configs.setdefault(item["employee_key"], {
            "employee_key": item["employee_key"], "display_name": item["display_name"],
            "role": item["role"], "weekly_target": item["weekly_target"],
            "include_in_team": item["include_in_team"], "active": 1,
        })
    available_employees = sorted(
        (
            {"employee_key": item["employee_key"], "display_name": item["display_name"], "role": item["role"]}
            for item in configs.values() if item.get("include_in_team")
        ),
        key=lambda item: item["display_name"].lower(),
    )
    selected_employee = next((item for item in available_employees if item["employee_key"] == employee_key), None)
    if selected_employee:
        configs = {employee_key: configs[employee_key]}
    by_employee_day = defaultdict(lambda: {"sales": 0.0, "gross_sales": 0.0, "discounts": 0.0, "orders": 0})
    for item in sales:
        slot = by_employee_day[(item["employee_key"], item["day"])]
        slot["sales"] += float(item["total_sales"])
        slot["gross_sales"] += float(item.get("gross_sales") or 0)
        slot["discounts"] += float(item.get("discounts") or 0)
        slot["orders"] += int(item["order_count"])
    rows = []
    for employee in configs.values():
        values = []
        gross_values = []
        discount_values = []
        order_values = []
        for column in columns:
            cursor = column["start"]
            amount = gross_amount = discounts = orders = 0
            while cursor <= column["end"]:
                item = by_employee_day[(employee["employee_key"], cursor.isoformat())]
                amount += item["sales"]
                gross_amount += item["gross_sales"]
                discounts += item["discounts"]
                orders += item["orders"]
                cursor += timedelta(days=1)
            values.append(amount)
            gross_values.append(gross_amount)
            discount_values.append(discounts)
            order_values.append(orders)
        weekly_target = float(employee.get("weekly_target") or 0)
        period_target = weekly_target / target_divisor if target_divisor else weekly_target * 4
        if period == "weekly":
            weekly_total_target = overrides.get(
                (employee["employee_key"], date(end.year, end.month, 1).isoformat()),
                weekly_target,
            )
            targets = [weekly_total_target / len(columns) for _ in columns]
        elif period in {"weekly_month", "weekly_year"}:
            targets = [overrides.get(
                (employee["employee_key"], date(column["end"].year, column["end"].month, 1).isoformat()),
                period_target,
            ) for column in columns]
        elif period == "monthly":
            selected_target = overrides.get((employee["employee_key"], target_start.isoformat()), period_target)
            targets = [selected_target / len(columns) for _ in columns]
        else:
            targets = [overrides.get(
                (employee["employee_key"], (column["start"] if period == "yearly" else target_start).isoformat()),
                period_target,
            ) for column in columns]
        total_target = sum(targets)
        total = sum(values)
        rows.append({
            **employee, "values": values, "gross_values": gross_values,
            "discount_values": discount_values, "order_values": order_values,
            "period_target": period_target, "targets": targets, "total_target": total_target,
            "total": total, "orders": sum(order_values),
            "gross_sales": sum(gross_values), "discounts": sum(discount_values),
            "average_order_value": total / sum(order_values) if sum(order_values) else 0,
            "attainment": total / total_target if total_target else None,
        })
    role_order = {"Sales": 0, "CRM - CS": 1, "Team Coach": 2, "Unassigned": 9}
    rows.sort(key=lambda item: (
        0 if item.get("include_in_team") else 1,
        next((rank for role, rank in role_order.items() if item["role"].startswith(role)), 8),
        item["display_name"],
    ))
    team_rows = [row for row in rows if row.get("include_in_team")]
    ranked = sorted(
        (row for row in team_rows if row["attainment"] is not None),
        key=lambda item: (-item["attainment"], item["display_name"]),
    )
    top_keys = {row["employee_key"] for row in ranked[:3]}
    bottom_keys = {row["employee_key"] for row in ranked[-3:]} - top_keys
    for row in rows:
        row["performance_band"] = "top" if row["employee_key"] in top_keys else ("bottom" if row["employee_key"] in bottom_keys else "")
    team_values = [sum(row["values"][i] for row in team_rows) for i in range(len(columns))]
    team_discount_values = [sum(abs(float(row["discount_values"][i] or 0)) for row in team_rows) for i in range(len(columns))]
    team_orders = [sum(row["order_values"][i] for row in team_rows) for i in range(len(columns))]
    team_targets = [sum(row["targets"][i] for row in team_rows) for i in range(len(columns))]
    team_target = sum(row["total_target"] for row in team_rows)
    today = datetime.now(IST).date()
    growth_source = [
        (column["label"], value)
        for column, value in zip(columns, team_values)
        if column["start"] <= today
    ]
    growth_max = max((value for _, value in growth_source), default=0) or 1
    growth_points = []
    for index, (label, value) in enumerate(growth_source):
        x = 4 if len(growth_source) == 1 else 4 + (index * 92 / (len(growth_source) - 1))
        y = 92 - (value / growth_max * 76)
        previous_value = growth_source[index - 1][1] if index else None
        point_change = (
            (value - previous_value) / previous_value
            if previous_value
            else None
        )
        growth_points.append({
            "label": label,
            "value": value,
            "x": round(x, 2),
            "y": round(y, 2),
            "change": point_change,
        })
    growth_current = growth_points[-1]["value"] if growth_points else 0
    growth_previous = growth_points[-2]["value"] if len(growth_points) > 1 else 0
    growth_change = (
        (growth_current - growth_previous) / growth_previous
        if growth_previous else None
    )
    product_summary = defaultdict(lambda: {"sales": 0.0, "gross_sales": 0.0, "discounts": 0.0, "orders": 0, "quantity": 0})
    for item in product_sales:
        if selected_employee and item["employee_key"] != employee_key:
            continue
        slot = product_summary[(item["employee_key"], item["display_name"], item["role"], item["product_title"], item["product_sku"])]
        slot["sales"] += float(item["total_sales"])
        slot["gross_sales"] += float(item.get("gross_sales") or 0)
        slot["discounts"] += abs(float(item.get("discounts") or 0))
        slot["orders"] += int(item["order_count"])
        slot["quantity"] += int(item.get("quantity_sold") or 0)
    product_rows = [{
        "employee_key": key[0], "display_name": key[1], "role": key[2],
        "product_title": key[3], "product_sku": key[4], "total": values["sales"],
        "gross_sales": values["gross_sales"], "discounts": values["discounts"],
        "mrp": values["sales"] + values["discounts"],
        "orders": values["orders"],
        "quantity": values["quantity"],
        "average_order_value": values["sales"] / values["orders"] if values["orders"] else 0,
    } for key, values in product_summary.items()]
    product_rows.sort(key=lambda item: (item["display_name"], -item["total"], item["product_title"]))
    team_product_summary = defaultdict(lambda: {"sales": 0.0, "gross_sales": 0.0, "discounts": 0.0, "orders": 0, "quantity": 0})
    included_keys = {
        item["employee_key"] for item in configs.values()
        if item.get("include_in_team") and "nutrition" not in (item.get("role") or "").casefold()
    }
    for item in product_sales:
        if item["employee_key"] not in included_keys:
            continue
        key = (item["product_title"], item["product_sku"])
        slot = team_product_summary[key]
        slot["sales"] += float(item["total_sales"])
        slot["gross_sales"] += float(item.get("gross_sales") or 0)
        slot["discounts"] += abs(float(item.get("discounts") or 0))
        slot["orders"] += int(item["order_count"])
        slot["quantity"] += int(item.get("quantity_sold") or 0)
    team_product_rows = [{
        "product_title": key[0], "product_sku": key[1],
        "total": values["sales"], "gross_sales": values["gross_sales"],
        "discounts": values["discounts"], "mrp": values["sales"] + values["discounts"],
        "orders": values["orders"], "quantity": values["quantity"],
    } for key, values in team_product_summary.items()]
    team_product_rows.sort(key=lambda item: (-item["quantity"], -item["total"], item["product_title"]))
    return {
        "period": period, "anchor": anchor, "start": start, "end": end,
        "columns": columns, "rows": rows,
        "product_rows": product_rows,
        "team_product_rows": team_product_rows,
        "available_employees": available_employees,
        "selected_employee": selected_employee,
        "growth": {
            "points": growth_points,
            "polyline": " ".join(f'{point["x"]},{point["y"]}' for point in growth_points),
            "current": growth_current,
            "previous": growth_previous,
            "change": growth_change,
        },
        "team": {
            "values": team_values, "discount_values": team_discount_values,
            "targets": team_targets, "orders_by_period": team_orders,
            "total": sum(team_values), "orders": sum(team_orders),
            "discounts": sum(team_discount_values), "mrp": sum(team_values) + sum(team_discount_values),
            "average_order_value": sum(team_values) / sum(team_orders) if sum(team_orders) else 0,
            "target": team_target,
            "attainment": sum(team_values) / team_target if team_target else 0,
        },
        "last_sync": latest_shopify_sync(),
    }
