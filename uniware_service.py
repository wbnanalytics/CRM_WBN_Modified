import os
import re
import time as time_module
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests

from database import (
    finish_sync_run,
    replace_uniware_sale_order,
    shopify_orders_for_reconciliation,
    start_sync_run,
    update_uniware_shopify_links,
)


IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
RETURN_STATUSES = {"RETURN_REQUESTED", "COURIER_RETURN", "RETURNED", "REPLACED"}


class UniwareError(RuntimeError):
    pass


def _env(*names):
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return ""


def uniware_configured():
    return all((
        _env("UNIWARE_CLIENT_ID", "uniware-client-id"),
        _env("UNIWARE_USERNAME", "uniware-username"),
        _env("UNIWARE_PASSWORD", "uniware-password"),
        _env("UNIWARE_BASE_URL", "uniware-base-url"),
    ))


def _base_url():
    return _env("UNIWARE_BASE_URL", "uniware-base-url").rstrip("/")


def _access_token():
    response = requests.post(
        f"{_base_url()}/oauth/token",
        data={
            "grant_type": "password",
            "client_id": _env("UNIWARE_CLIENT_ID", "uniware-client-id"),
            "username": _env("UNIWARE_USERNAME", "uniware-username"),
            "password": _env("UNIWARE_PASSWORD", "uniware-password"),
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("access_token"):
        raise UniwareError(payload.get("error_description") or "Uniware did not issue an access token")
    return payload["access_token"]


def _headers(token):
    return {"Authorization": f"bearer {token}", "Content-Type": "application/json"}


def _post(token, endpoint, payload, timeout=60):
    response = requests.post(
        f"{_base_url()}{endpoint}", headers=_headers(token), json=payload, timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("successful"):
        errors = data.get("errors") or []
        message = "; ".join(
            str(item.get("description") or item.get("message") or item) for item in errors
        )
        raise UniwareError(message or data.get("message") or "Uniware request failed")
    return data


def _utc_iso(value):
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _date_text(value):
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            parsed = datetime.fromtimestamp(float(value) / 1000, tz=UTC)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(IST).date().isoformat()
    except (TypeError, ValueError, OSError):
        return str(value)[:10] or None


def _timestamp_text(value):
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(float(value) / 1000, tz=UTC).isoformat()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except (TypeError, ValueError, OSError):
        return str(value)


def _normal_order_code(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _search_orders(token, start, end):
    from_dt = datetime.combine(start, time.min, tzinfo=IST)
    to_dt = datetime.combine(end + timedelta(days=1), time.min, tzinfo=IST) - timedelta(milliseconds=1)
    rows = []
    offset = 0
    while True:
        data = _post(token, "/services/rest/v1/oms/saleOrder/search", {
            "fromDate": _utc_iso(from_dt),
            "toDate": _utc_iso(to_dt),
            "dateType": "CREATED",
            "searchOptions": {
                "displayLength": 200,
                "displayStart": offset,
                "getCount": True,
                "sortDirection": "DESCENDING",
            },
        })
        page = data.get("elements") or []
        rows.extend(page)
        total = int(data.get("totalRecords") or 0)
        if not page or len(rows) >= total:
            return rows
        offset += len(page)
        if offset > 250000:
            raise UniwareError("Uniware pagination exceeded the safety limit")


def _get_order(token, code):
    last_error = None
    for attempt in range(4):
        try:
            data = _post(
                token, "/services/rest/v1/oms/saleorder/get",
                {"code": code, "paymentDetailRequired": True}, timeout=90,
            )
            return data.get("saleOrderDTO") or {}
        except (requests.RequestException, UniwareError) as exc:
            last_error = exc
            if attempt == 3:
                break
            time_module.sleep(0.5 * (2 ** attempt))
    raise UniwareError(f"Uniware order {code} failed after retries: {last_error}")


def _fulfilment_bucket(items, parent_status):
    statuses = [str(item.get("statusCode") or "").upper() for item in items]
    if any(status in RETURN_STATUSES or status.startswith("RETURN") for status in statuses):
        return "Returned"
    if statuses and all(status == "CANCELLED" for status in statuses):
        return "Cancelled"
    active = [status for status in statuses if status != "CANCELLED"]
    if active and all(status == "DELIVERED" for status in active):
        return "Delivered"
    if not statuses and str(parent_status or "").upper() == "CANCELLED":
        return "Cancelled"
    return "Pending"


def _customer_fields(dto):
    billing = dto.get("billingAddress") or {}
    addresses = dto.get("addresses") or []
    fallback = addresses[0] if addresses else {}
    return (
        billing.get("name") or fallback.get("name") or dto.get("customerCode") or "",
        dto.get("notificationEmail") or billing.get("email") or fallback.get("email") or "",
        dto.get("notificationMobile") or billing.get("phone") or fallback.get("phone") or "",
    )


def _transform_order(dto):
    raw_items = dto.get("saleOrderItems") or []
    statuses = Counter(str(item.get("statusCode") or "UNKNOWN").upper() for item in raw_items)
    customer_name, customer_email, customer_phone = _customer_fields(dto)
    display_code = str(dto.get("displayOrderCode") or dto.get("code") or "").strip()
    order = {
        "sale_order_code": str(dto.get("code") or display_code),
        "display_order_code": display_code,
        "display_order_date": _date_text(dto.get("displayOrderDateTime") or dto.get("created")),
        "channel": dto.get("channel"),
        "source": dto.get("source"),
        "order_status": str(dto.get("status") or "").upper(),
        "fulfilment_bucket": _fulfilment_bucket(raw_items, dto.get("status")),
        "customer_name": customer_name,
        "customer_email": customer_email,
        "customer_phone": customer_phone,
        "cod": bool(dto.get("cod")),
        "item_count": len(raw_items),
        "item_status_summary": dict(statuses),
    }
    items = []
    for index, item in enumerate(raw_items, start=1):
        selling = float(item.get("sellingPrice") or 0)
        discount = abs(float(item.get("discount") or 0))
        mrp = float(item.get("maxRetailPrice") or 0) or selling + discount
        items.append({
            "sale_order_item_code": str(item.get("code") or f"{order['sale_order_code']}:{index}"),
            "item_sku": item.get("itemSku"),
            "seller_sku_code": item.get("sellerSkuCode"),
            "item_name": item.get("itemName"),
            "status_code": str(item.get("statusCode") or "UNKNOWN").upper(),
            "shipping_package_status": item.get("shippingPackageStatus"),
            "reverse_pickup_code": item.get("reversePickupCode"),
            "quantity": int(item.get("totalQuantity") or 1),
            "selling_price": selling,
            "discount": discount,
            "mrp": mrp,
            "updated_at": _timestamp_text(item.get("updated")),
        })
    return order, items


def _link_orders(start, end, uniware_orders):
    shopify = shopify_orders_for_reconciliation(start.isoformat(), end.isoformat())
    by_code = {}
    for row in shopify:
        for value in (row.get("order_name"), row.get("order_number")):
            key = _normal_order_code(value)
            if key:
                by_code.setdefault(key, []).append(row)
    links = []
    for order in uniware_orders:
        candidates = by_code.get(_normal_order_code(order["display_order_code"])) or []
        if len(candidates) != 1:
            continue
        linked = candidates[0]
        links.append({
            "sale_order_code": order["sale_order_code"],
            "shopify_order_id": linked["order_id"],
            "shopify_order_name": linked["order_name"],
            "employee_key": linked.get("employee_key"),
            "source_employee_name": linked.get("source_employee_name"),
        })
    return shopify, links


def sync_uniware(start_date, end_date, workers=None):
    start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    end = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
    if start > end:
        raise ValueError("Start date must not be after end date")
    if (end - start).days > 91:
        raise ValueError("Uniware permits a maximum 92-day Sale Orders range")
    if not uniware_configured():
        raise UniwareError("Uniware credentials are not configured")
    run_id = start_sync_run("uniware", "sale_orders", start.isoformat(), end.isoformat())
    received = saved_orders = saved_items = 0
    try:
        token = _access_token()
        search_rows = _search_orders(token, start, end)
        received = len(search_rows)
        order_codes = [str(row.get("code") or "") for row in search_rows if row.get("code")]
        transformed = []
        # A lower default avoids exhausting ephemeral sockets while fetching
        # thousands of item-level orders. Individual calls retry above.
        maximum = workers or int(_env("UNIWARE_SYNC_WORKERS") or 6)
        with ThreadPoolExecutor(max_workers=max(1, min(maximum, 20))) as executor:
            pending = {executor.submit(_get_order, token, code): code for code in order_codes}
            for future in as_completed(pending):
                transformed.append(_transform_order(future.result()))
        transformed.sort(key=lambda pair: pair[0]["sale_order_code"])
        plain_orders = []
        for order, items in transformed:
            order_saved, items_saved = replace_uniware_sale_order(order, items)
            saved_orders += order_saved
            saved_items += items_saved
            plain_orders.append(order)
        shopify_rows, links = _link_orders(start, end, plain_orders)
        update_uniware_shopify_links(links)
        finish_sync_run(run_id, "success", received, saved_orders + saved_items)
        return {
            "run_id": run_id,
            "rows_received": received,
            "orders_saved": saved_orders,
            "items_saved": saved_items,
            "shopify_orders": len(shopify_rows),
            "matched_orders": len(links),
            "shopify_unmatched": max(len(shopify_rows) - len(links), 0),
            "uniware_unmatched": max(saved_orders - len(links), 0),
        }
    except Exception as exc:
        finish_sync_run(run_id, "failed", received, saved_orders + saved_items, str(exc)[:1000])
        raise
