import json
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from database import db_connection


IST = ZoneInfo("Asia/Kolkata")
SPAM_SUBCATEGORIES = {
    "spam / invalid / irrelevant", "brand/marketing related", "other",
}
RESOLVED_BIK = {"CLOSED", "BULK_CLOSED", "AUTO_CLOSED", "BULK_AUTO_CLOSED"}
SPAM_BIK = {"BULK_CLOSED_AS_SPAM", "CLOSED_AS_SPAM", "CLOSE AS SPAM"}


def _average(values):
    values = [float(value) for value in values if value is not None and float(value) > 0]
    return round(sum(values) / len(values)) if values else None


def _agent_name(raw, fallback="Unassigned"):
    assignee = raw.get("assignee") or {}
    if isinstance(assignee, dict):
        name = " ".join(filter(None, (assignee.get("firstName"), assignee.get("lastName")))).strip()
        return name or assignee.get("email") or fallback
    return str(assignee or fallback)


def _working_hours(value):
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        local = parsed.astimezone(IST)
        return local.weekday() < 5 and 10 <= local.hour < 19
    except ValueError:
        return False


def _base_metric(key, label):
    return {
        "key": key, "label": label, "total": 0, "resolved": 0, "open": 0,
        "spam": 0, "non_spam": 0, "working_hours": 0, "outside_hours": 0,
        "frt_with_spam": None, "frt_without_spam": None, "reply_10_12": 0,
    }


def cs_dashboard(start_date, end_date):
    metrics = {
        "email": _base_metric("email", "Email"),
        "inbound": _base_metric("inbound", "Inbound"),
        "chats": _base_metric("chats", "Chats"),
        "orm": _base_metric("orm", "ORM"),
    }
    agents = defaultdict(lambda: {
        "email": 0, "inbound": 0, "chats": 0, "orm": 0, "resolved": 0,
        "frt_values": [], "non_spam_frt_values": [],
    })
    with db_connection() as db:
        zoho_rows = db.execute(
            """SELECT channel, status, status_type, closed_time, created_time, raw_json
               FROM zoho_tickets WHERE created_local_date BETWEEN ? AND ?""",
            (start_date, end_date),
        ).fetchall()
        bik_rows = db.execute(
            """SELECT channel, ticket_stage, agent_name, first_response_seconds, raw_json
               FROM bik_tickets WHERE local_date BETWEEN ? AND ?""",
            (start_date, end_date),
        ).fetchall()

    for row in zoho_rows:
        channel = (row["channel"] or "").strip().lower()
        key = "email" if channel == "email" else ("inbound" if channel in {"phone", "inbound"} else None)
        if not key:
            continue
        raw = json.loads(row["raw_json"] or "{}")
        metric = metrics[key]
        agent = _agent_name(raw)
        status = (row["status_type"] or row["status"] or "").lower()
        resolved = status in {"closed", "resolved"} or bool(row["closed_time"])
        is_spam = bool(raw.get("isSpam")) or str(raw.get("subCategory") or "").strip().lower() in SPAM_SUBCATEGORIES
        metric["total"] += 1
        metric["resolved" if resolved else "open"] += 1
        metric["spam" if is_spam else "non_spam"] += 1
        metric["working_hours" if _working_hours(row["created_time"]) else "outside_hours"] += 1
        thread_count = int(raw.get("threadCount") or 0)
        if 10 <= max(thread_count - 1, 0) <= 12:
            metric["reply_10_12"] += 1
        agents[agent][key] += 1
        if resolved:
            agents[agent]["resolved"] += 1

    frt = defaultdict(list)
    frt_non_spam = defaultdict(list)
    for row in bik_rows:
        channel = (row["channel"] or "").strip().lower()
        if channel in {"whatsapp", "chat", "chats"}:
            key = "chats"
        elif channel in {"instagram", "messenger", "facebook", "fb", "ig"}:
            key = "orm"
        else:
            continue
        stage = (row["ticket_stage"] or "").upper()
        is_spam = stage in SPAM_BIK
        resolved = stage in RESOLVED_BIK or is_spam
        metric = metrics[key]
        metric["total"] += 1
        metric["resolved" if resolved else "open"] += 1
        metric["spam" if is_spam else "non_spam"] += 1
        value = row["first_response_seconds"]
        if value and value > 0:
            frt[key].append(value)
            if not is_spam:
                frt_non_spam[key].append(value)
        agent = row["agent_name"] or "Unassigned"
        agents[agent][key] += 1
        if resolved:
            agents[agent]["resolved"] += 1
        if value and value > 0:
            agents[agent]["frt_values"].append(value)
            if not is_spam:
                agents[agent]["non_spam_frt_values"].append(value)

    for key, metric in metrics.items():
        metric["resolution_rate"] = round(metric["resolved"] / metric["total"] * 100, 1) if metric["total"] else 0
        metric["frt_with_spam"] = _average(frt[key])
        metric["frt_without_spam"] = _average(frt_non_spam[key])

    agent_rows = []
    for name, values in agents.items():
        total = values["email"] + values["inbound"] + values["chats"] + values["orm"]
        agent_rows.append({
            "name": name, **{key: values[key] for key in ("email", "inbound", "chats", "orm")},
            "total": total, "resolved": values["resolved"],
            "resolution_rate": round(values["resolved"] / total * 100, 1) if total else 0,
            "frt_with_spam": _average(values["frt_values"]),
            "frt_without_spam": _average(values["non_spam_frt_values"]),
        })
    agent_rows.sort(key=lambda item: (-item["total"], item["name"]))
    return {
        "start": start_date, "end": end_date,
        "metrics": list(metrics.values()), "agents": agent_rows,
        "total": sum(item["total"] for item in metrics.values()),
        "resolved": sum(item["resolved"] for item in metrics.values()),
        "spam": sum(item["spam"] for item in metrics.values()),
    }
