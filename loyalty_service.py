import re
from collections import Counter, defaultdict
from calendar import monthrange
from datetime import date, timedelta

from database import (
    shopify_customer_sales_daily_between,
    shopify_customer_sales_daily_coverage,
    shopify_orders_between,
)


SCOPES = {
    "overall": "Wellbeing Overall",
    "crm": "Team CRM",
    "nutritionists": "Team Nutritionists",
}
TIERS = ("Loyal", "Regular", "Repeat", "One-time")
FULFILMENT_BUCKETS = ("Delivered", "Cancelled", "Returned", "Pending")
OVERALL_CUSTOMER_KEY = "shopify-overall"
DATE_PRESETS = (
    ("today", "Today"),
    ("yesterday", "Yesterday"),
    ("last_7", "Last 7 days"),
    ("last_30", "Last 30 days"),
    ("last_60", "Last 60 days"),
    ("last_90", "Last 90 days"),
)


def _email(value):
    return str(value or "").strip().casefold()


def _phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else digits


def _name(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _scope_matches(scope, role, name=""):
    normalized = str(role or "").strip().casefold()
    normalized_name = str(name or "").strip().casefold()
    if scope == "overall":
        return True
    if scope == "nutritionists":
        return "nutrition" in normalized
    return (
        normalized == "sales" or normalized.startswith("crm -")
        or "admin" in normalized or "replacement" in normalized
        or "crm admin" in normalized_name
    )


def _tier(order_count):
    if order_count >= 7:
        return "Loyal"
    if order_count >= 5:
        return "Regular"
    if order_count >= 2:
        return "Repeat"
    return "One-time"


class _Groups:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[right] = left


def _preferred(values):
    cleaned = [str(value).strip() for value in values if str(value or "").strip()]
    if not cleaned:
        return ""
    counts = Counter(cleaned)
    return max(counts, key=lambda item: (counts[item], len(item), item))


def _customer_rows(source_rows, order_rows):
    phone_by_email = defaultdict(set)
    phone_by_name = defaultdict(set)
    for order in order_rows:
        phone = _phone(order.get("customer_phone"))
        if not phone:
            continue
        if _email(order.get("customer_email")):
            phone_by_email[_email(order.get("customer_email"))].add(phone)
        if _name(order.get("customer_name")):
            phone_by_name[_name(order.get("customer_name"))].add(phone)

    records = []
    for row in source_rows:
        email_key = _email(row.get("customer_email"))
        name_key = _name(row.get("customer_name"))
        phones = set(phone_by_email.get(email_key, set())) | set(phone_by_name.get(name_key, set()))
        records.append({**row, "email_key": email_key, "name_key": name_key, "phones": phones})

    groups = _Groups(len(records))
    seen = {"customer_id": {}, "email": {}, "phone": {}, "name": {}}
    for index, row in enumerate(records):
        identifiers = {
            "customer_id": str(row.get("customer_id") or "").strip(),
            "email": row["email_key"],
            "name": row["name_key"],
        }
        for kind, value in identifiers.items():
            if not value:
                continue
            if value in seen[kind]:
                groups.union(index, seen[kind][value])
            else:
                seen[kind][value] = index
        for value in row["phones"]:
            if value in seen["phone"]:
                groups.union(index, seen["phone"][value])
            else:
                seen["phone"][value] = index

    members = defaultdict(list)
    for index, row in enumerate(records):
        members[groups.find(index)].append(row)
    customers = []
    for customer_number, rows in enumerate(members.values(), start=1):
        count = sum(int(row.get("order_count") or 0) for row in rows)
        names = [row.get("customer_name") for row in rows]
        emails = [row.get("customer_email") for row in rows]
        phones = sorted({phone for row in rows for phone in row["phones"]})
        agents = sorted({row.get("display_name") for row in rows if row.get("display_name") and row.get("display_name") != "Unassigned"})
        basis = []
        for label, values in (
            ("Shopify customer ID", {str(row.get("customer_id") or "") for row in rows if row.get("customer_id")}),
            ("email", {_email(value) for value in emails if _email(value)}),
            ("phone", set(phones)),
            ("name", {_name(value) for value in names if _name(value)}),
        ):
            if values:
                basis.append(label)
        customers.append({
            "customer_key": f"customer-{customer_number}",
            "customer_name": _preferred(names) or "Unnamed customer",
            "customer_email": _preferred(emails),
            "customer_phone": phones[0] if phones else "",
            "order_count": count,
            "tier": _tier(count),
            "first_order_month": min(row["day"] for row in rows),
            "last_order_month": max(row["day"] for row in rows),
            "agents": agents,
            "matched_by": ", ".join(basis),
        })
    return sorted(customers, key=lambda row: (-row["order_count"], row["customer_name"].casefold()))


def _filter_customers(customers, tier, search, scope="overall"):
    needle = str(search or "").strip().casefold()
    result = []
    for customer in customers:
        if tier and customer["tier"] != tier:
            continue
        fields = [customer["customer_name"], customer["customer_email"], customer["customer_phone"], customer["tier"]]
        if scope != "overall":
            fields.append(" ".join(customer["agents"]))
        haystack = " ".join(fields).casefold()
        if needle and needle not in haystack:
            continue
        result.append(customer)
    return result


def loyalty_report(scope="overall", year="", month="", period="", tier="", search="", status="", start_date="", end_date=""):
    scope = scope if scope in SCOPES else "overall"
    tier = tier if tier in TIERS else ""
    status = status if status in FULFILMENT_BUCKETS else ""
    coverage = shopify_customer_sales_daily_coverage()
    today = date.today()
    valid_periods = {key for key, _label in DATE_PRESETS}
    period = period if period in valid_periods else ""
    coverage_start = str(coverage.get("start_date") or today.isoformat())[:4]
    coverage_end = str(coverage.get("end_date") or today.isoformat())[:4]
    selected_year = int(year) if str(year).isdigit() and 2020 <= int(year) <= today.year else int(coverage_end)
    selected_month = int(month) if str(month).isdigit() and 1 <= int(month) <= 12 else 0
    if start_date and end_date:
        start_date, end_date = str(start_date), str(end_date)
    elif period:
        days = {"today": 1, "yesterday": 1, "last_7": 7, "last_30": 30, "last_60": 60, "last_90": 90}[period]
        end = today - timedelta(days=1) if period == "yesterday" else today
        start = end - timedelta(days=days - 1)
        start_date, end_date = start.isoformat(), end.isoformat()
    elif selected_month:
        start_date = date(selected_year, selected_month, 1).isoformat()
        end_date = date(selected_year, selected_month, monthrange(selected_year, selected_month)[1]).isoformat()
    else:
        start_date = date(selected_year, 1, 1).isoformat()
        end_date = date(selected_year, 12, 31).isoformat()
    years = list(range(int(coverage_end), int(coverage_start) - 1, -1))
    if selected_year not in years:
        years.append(selected_year)
        years.sort(reverse=True)

    all_source = shopify_customer_sales_daily_between(start_date, end_date)
    source = (
        [row for row in all_source if row.get("employee_key") == OVERALL_CUSTOMER_KEY]
        if scope == "overall"
        else [
            row for row in all_source
            if row.get("employee_key") != OVERALL_CUSTOMER_KEY
            and _scope_matches(scope, row.get("role"), row.get("display_name"))
        ]
    )
    orders = [
        row for row in shopify_orders_between(start_date, end_date)
        if _scope_matches(scope, row.get("role"), row.get("display_name"))
    ]
    all_customers = _customer_rows(source, orders)
    tier_counts = Counter(row["tier"] for row in all_customers)
    return {
        "scope": scope,
        "scope_label": SCOPES[scope],
        "scopes": SCOPES,
        "year": selected_year,
        "month": selected_month,
        "period": period,
        "periods": DATE_PRESETS,
        "years": years,
        "months": tuple((number, date(2000, number, 1).strftime("%B")) for number in range(1, 13)),
        "start_date": start_date,
        "end_date": end_date,
        "tier": tier,
        "search": search,
        "status": status,
        "tiers": TIERS,
        "tier_counts": {name: tier_counts.get(name, 0) for name in TIERS},
        "customer_count": len(all_customers),
        "order_count": sum(row["order_count"] for row in all_customers),
        "customers": _filter_customers(all_customers, tier, search, scope),
        "coverage": coverage,
    }
