import json
import os
import csv
import io
import re
import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from database import (
    clear_bik_report_date, finish_sync_run, save_bik_conversations, save_bik_tickets,
    start_sync_run, upsert_zoho_tickets,
)


IST = ZoneInfo("Asia/Kolkata")
ZOHO_DEPARTMENTS = {
    "customer_experience": {
        "id": "218679000000483199",
        "name": "Customer Experience",
        "report_id": "218679000000483210",
    },
    "customer_escalation": {
        "id": "218679000000815290",
        "name": "Customer Escalation",
        "report_id": "218679000000815301",
    },
}

BIK_REPORTS = {
    "all_conversations": {
        "name": "All Conversation Report",
        "api_name": "All Conversations Report",
        "type": "Overall",
    },
    "customer_tickets": {
        "name": "Customer Ticket Report",
        "api_name": "Customer ticket report (with forms)",
        "type": "Helpdesk",
    },
}


class IntegrationError(RuntimeError):
    pass


def _parse_date(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _zoho_access_token():
    response = requests.post(
        os.environ["ZOHO_ACCOUNTS_URL"].rstrip("/") + "/oauth/v2/token",
        data={
            "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
            "client_id": os.environ["ZOHO_CLIENT_ID"],
            "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _local_date(iso_timestamp):
    parsed = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return parsed.astimezone(IST).date().isoformat()


def sync_zoho(start_date, end_date):
    start_date = _parse_date(start_date)
    end_date = _parse_date(end_date)
    if end_date < start_date:
        raise ValueError("End date must be on or after start date")
    run_id = start_sync_run("zoho", "tickets_by_channel", start_date.isoformat(), end_date.isoformat())
    received = 0
    selected = []
    try:
        token = _zoho_access_token()
        headers = {
            "Authorization": f"Zoho-oauthtoken {token}",
            "orgId": os.environ["ZOHO_ORG_ID"],
        }
        endpoint = os.environ["ZOHO_DESK_URL"].rstrip("/") + "/api/v1/tickets"
        target_ids = {item["id"] for item in ZOHO_DEPARTMENTS.values()}
        offset = 0
        page_size = 100
        reached_start = False
        while offset < 20000 and not reached_start:
            response = requests.get(
                endpoint,
                headers=headers,
                params={
                    "from": offset,
                    "limit": page_size,
                    "include": "departments,assignee,team",
                    "sortBy": "-createdTime",
                },
                timeout=45,
            )
            response.raise_for_status()
            rows = response.json().get("data", [])
            if not rows:
                break
            received += len(rows)
            for ticket in rows:
                created_local = _local_date(ticket["createdTime"])
                if created_local < start_date.isoformat():
                    reached_start = True
                    continue
                if created_local <= end_date.isoformat() and str(ticket.get("departmentId")) in target_ids:
                    ticket["createdLocalDate"] = created_local
                    selected.append(ticket)
            if len(rows) < page_size:
                break
            offset += page_size
        saved = upsert_zoho_tickets(selected)
        finish_sync_run(run_id, "success", received, saved)
        return {"run_id": run_id, "rows_received": received, "rows_saved": saved}
    except Exception as exc:
        finish_sync_run(run_id, "failed", received, 0, str(exc)[:1000])
        raise


def completed_day(days_ago=1):
    today = datetime.now(IST).date()
    selected = today - timedelta(days=days_ago)
    return selected, selected


def generate_bik_report(report_key, start_date, end_date, recipient_email, cursor=None):
    if report_key not in BIK_REPORTS:
        raise ValueError("Unknown BIK report")
    if not recipient_email:
        raise ValueError("A BIK report recipient email is required")
    config = BIK_REPORTS[report_key]
    start_date = _parse_date(start_date)
    end_date = _parse_date(end_date)
    start_utc = datetime.combine(start_date, time.min, tzinfo=IST).astimezone(timezone.utc)
    end_utc = min(datetime.combine(end_date, time.max, tzinfo=IST).astimezone(timezone.utc), datetime.now(timezone.utc) - timedelta(seconds=5))
    payload = {
        "reportName": config["api_name"],
        "dateRange": {
            "startDate": start_utc.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "endDate": end_utc.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        },
        "reportType": config["type"],
        "emails": [recipient_email],
    }
    if cursor:
        payload["cursor"] = cursor
    response = requests.post(
        "https://bikapi.bikayi.app/integrations/bikPlatformFunctions-generateReport",
        auth=(os.environ["BIK_KEY"], os.environ["BIK_SECRET"]),
        json=payload,
        timeout=45,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("status") not in (200, 201) or data.get("errors"):
        messages = [error.get("message", "Unknown BIK error") for error in data.get("errors", [])]
        raise IntegrationError("; ".join(messages) or "BIK rejected the report request")
    return data


def parse_duration_seconds(value):
    if not value:
        return 0
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    return sum(int(number) * units[unit] for number, unit in re.findall(r"(\d+)\s*([dhms])", str(value).lower()))


def _poll_bik_report(report_history_id, attempts=60):
    endpoint = "https://bikapi.bikayi.app/integrations/bikPlatformFunctions-getReportById"
    for _ in range(attempts):
        response = requests.post(
            endpoint,
            auth=(os.environ["BIK_KEY"], os.environ["BIK_SECRET"]),
            json={"reportHistoryId": report_history_id},
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        status = data.get("reportStatus")
        if status == "COMPLETED":
            return data
        if status == "FAILED":
            raise IntegrationError(f"BIK report {report_history_id} failed")
        time_module.sleep(10)
    raise IntegrationError(f"BIK report {report_history_id} did not complete in time")


def _download_csv(url):
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    text = response.content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def sync_bik(report_key, start_date, end_date, recipient_email=None):
    recipient_email = recipient_email or os.getenv("BIK_REPORT_EMAIL")
    start_date = _parse_date(start_date)
    end_date = _parse_date(end_date)
    run_id = start_sync_run("bik", report_key, start_date.isoformat(), end_date.isoformat())
    total = 0
    external_ids = []
    cursor = None
    try:
        clear_bik_report_date(report_key, start_date.isoformat())
        for _ in range(10):
            generated = generate_bik_report(report_key, start_date, end_date, recipient_email, cursor)
            history_id = (generated.get("data") or {}).get("reportHistoryId")
            if not history_id:
                raise IntegrationError("BIK did not return a report history ID")
            external_ids.append(str(history_id))
            completed = _poll_bik_report(history_id)
            rows = _download_csv(completed["downloadLink"])
            if report_key == "customer_tickets":
                saved = save_bik_tickets(rows, start_date.isoformat(), parse_duration_seconds, total)
            else:
                saved = save_bik_conversations(rows, start_date.isoformat(), total)
            total += saved
            cursor = completed.get("cursor")
            if not cursor:
                break
        finish_sync_run(run_id, "success", total, total, external_id=",".join(external_ids))
        return {"run_id": run_id, "rows_saved": total, "external_ids": external_ids}
    except Exception as exc:
        finish_sync_run(run_id, "failed", total, total, str(exc)[:1000], ",".join(external_ids))
        raise
