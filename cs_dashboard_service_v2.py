import json
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from database import db_connection

IST = ZoneInfo("Asia/Kolkata")
SPAM_SUBCATEGORIES = {"spam / invalid / irrelevant", "brand/marketing related", "other"}
RESOLVED = {"CLOSED", "BULK_CLOSED", "AUTO_CLOSED", "BULK_AUTO_CLOSED"}
SPAM = {"BULK_CLOSED_AS_SPAM", "CLOSED_AS_SPAM", "CLOSE AS SPAM"}
ESCALATED = {"WAITING_ON_CUSTOMER", "BULK_WAITING_ON_CUSTOMER", "ESCALATED", "BULK_ESCALATED"}
ALIASES = {"shubham": "Shubham Kadam", "aditya": "Aditya Shinde", "tapasya": "Tapasya Dhekane", "khushbu": "Khushbu Nishad"}
SYSTEM_AGENTS = {"unassigned", "bot", "wellbeing nutrition", "wellbeing nutrition_main"}


def tagged_general(raw):
    """Treat an exact General classification/tag/disposition as spam."""
    keys = ("category", "subCategory", "disposition", "Disposition", "Disposition Category",
            "Type", "tag", "tags", "Ticket label", "cf_disposition_category", "cf_tyoe")
    for container in (raw, raw.get("customFields") or {}, raw.get("cf") or {}):
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            values = value if isinstance(value, list) else [value]
            if any(str(item or "").strip().lower() == "general" for item in values):
                return True
    return False


def average(values):
    values = [float(value) for value in values if value is not None and float(value) >= 0]
    return round(sum(values) / len(values)) if values else None


def agent_name(value):
    value = " ".join(str(value or "").split()).strip() or "Unassigned"
    return ALIASES.get(value.lower(), value)


def zoho_agent(raw):
    assignee = raw.get("assignee") or {}
    if isinstance(assignee, dict):
        name = " ".join(filter(None, (assignee.get("firstName"), assignee.get("lastName")))).strip()
        return agent_name(name or assignee.get("email"))
    return agent_name(assignee)


def working_hours(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        local = parsed.astimezone(IST)
        return local.weekday() < 5 and 10 <= local.hour < 19
    except (TypeError, ValueError):
        return False


def bucket():
    return {"total": 0, "resolved": 0, "open": 0, "escalated": 0, "spam": 0,
            "genuine": 0, "untagged": 0, "working_hours": 0, "outside_hours": 0,
            "agent_replies": 0, "frt_all": [], "frt_clean": [], "art_all": [],
            "art_clean": [], "aht_all": [], "aht_clean": [], "csat_received": 0,
            "csat_positive": 0, "csat_negative": 0, "bot_handled": 0,
            "bot_resolved": 0, "human_handover": 0, "instagram_dm": 0,
            "instagram_comment": 0, "facebook_dm": 0, "facebook_comment": 0}


def add_common(target, resolved, spam, escalated):
    target["total"] += 1
    target["resolved" if resolved else "open"] += 1
    target["spam"] += int(spam)
    target["escalated"] += int(escalated)


def finish(values):
    denominator = values["total"] - values["spam"]
    result = {key: value for key, value in values.items() if not isinstance(value, list)}
    result.update({
        "resolution_rate": round(values["resolved"] / denominator * 100, 1) if denominator else 0,
        "frt_with_spam": average(values["frt_all"]), "frt_without_spam": average(values["frt_clean"]),
        "art_with_spam": average(values["art_all"]), "art_without_spam": average(values["art_clean"]),
        "aht_with_spam": average(values["aht_all"]), "aht_without_spam": average(values["aht_clean"]),
        "csat_rate": round(values["csat_positive"] / values["csat_received"] * 100, 1) if values["csat_received"] else None,
    })
    return result


def cs_dashboard(start_date, end_date, selected_agent=None):
    labels = {"email": ("Email", "Zoho Desk"), "inbound": ("Inbound", "Zoho Desk"),
              "chats": ("Chats", "BIK"), "orm": ("ORM", "BIK")}
    overall = {key: bucket() for key in labels}
    agents = {key: defaultdict(bucket) for key in labels}
    with db_connection() as db:
        zoho = db.execute("""SELECT channel,status,status_type,closed_time,created_time,raw_json
            FROM zoho_tickets WHERE created_local_date BETWEEN ? AND ?""", (start_date, end_date)).fetchall()
        bik = db.execute("""SELECT channel,ticket_stage,agent_name,closure_seconds,first_response_seconds,
            human_response_seconds,agent_messages,csat_response,csat_sentiment,raw_json FROM bik_tickets
            WHERE local_date BETWEEN ? AND ?""", (start_date, end_date)).fetchall()

    for row in zoho:
        channel = (row["channel"] or "").lower()
        key = "email" if channel == "email" else ("inbound" if channel in {"phone", "inbound"} else None)
        if not key:
            continue
        raw = json.loads(row["raw_json"] or "{}")
        status = (row["status_type"] or row["status"] or "").lower()
        is_resolved = status in {"closed", "resolved"} or bool(row["closed_time"])
        is_escalated = status in {"escalated", "on hold", "onhold"}
        subcategory = str(raw.get("subCategory") or "").strip()
        is_spam = bool(raw.get("isSpam")) or subcategory.lower() in SPAM_SUBCATEGORIES or tagged_general(raw)
        untagged = not is_spam and (not subcategory or subcategory == "-")
        in_hours = working_hours(row["created_time"])
        replies = int(raw.get("_agentReplyCount") or 0)
        frt = raw.get("_firstResponseSeconds")
        art = raw.get("_averageResponseSeconds")
        happiness = raw.get("customerHappiness")
        if isinstance(happiness, dict):
            happiness = happiness.get("rating") or happiness.get("value") or happiness.get("status")
        happiness_text = str(happiness or "").strip().lower()
        has_csat = bool(happiness_text and happiness_text not in {"none", "null", "unrated"})
        positive_csat = happiness_text in {"positive", "happy", "satisfied", "good", "excellent", "5", "4"}
        targets = (overall[key], agents[key][zoho_agent(raw)])
        for target in targets:
            add_common(target, is_resolved, is_spam, is_escalated)
            target["working_hours" if in_hours else "outside_hours"] += 1
            target["agent_replies"] += replies
            target["genuine"] += int(not is_spam and not untagged)
            target["untagged"] += int(untagged)
            if has_csat:
                target["csat_received"] += 1
                target["csat_positive" if positive_csat else "csat_negative"] += 1
            for value, all_name, clean_name in ((frt, "frt_all", "frt_clean"), (art, "art_all", "art_clean")):
                if value is not None and float(value) >= 0:
                    target[all_name].append(value)
                    if not is_spam:
                        target[clean_name].append(value)

    for row in bik:
        channel = (row["channel"] or "").lower()
        key = "chats" if channel in {"whatsapp", "chat", "chats", "livechat"} else (
            "orm" if channel in {"instagram", "messenger", "facebook", "fb", "ig"} else None)
        if not key:
            continue
        raw = json.loads(row["raw_json"] or "{}")
        stage = (row["ticket_stage"] or "").upper()
        is_spam = stage in SPAM or tagged_general(raw)
        is_resolved, is_escalated = stage in RESOLVED or is_spam, stage in ESCALATED
        name = agent_name(row["agent_name"])
        targets = (overall[key], agents[key][name])
        for target in targets:
            add_common(target, is_resolved, is_spam, is_escalated)
            target["genuine"] += int(not is_spam)
            target["agent_replies"] += int(row["agent_messages"] or 0)
        if key == "chats":
            system = name.lower() in SYSTEM_AGENTS
            overall[key]["bot_handled"] += int(system)
            overall[key]["bot_resolved"] += int(system and is_resolved)
            overall[key]["human_handover"] += int(not system)
        else:
            message_type = str(raw.get("Type of Message") or "").lower()
            comment = "comment" in message_type
            if channel in {"instagram", "ig"}:
                overall[key]["instagram_comment" if comment else "instagram_dm"] += 1
            else:
                overall[key]["facebook_comment" if comment else "facebook_dm"] += 1
        for value, all_name, clean_name in ((row["first_response_seconds"], "frt_all", "frt_clean"),
                                                  (row["human_response_seconds"], "art_all", "art_clean"),
                                                  (row["closure_seconds"], "aht_all", "aht_clean")):
            if value is not None and value >= 0:
                for target in targets:
                    target[all_name].append(value)
                    if not is_spam:
                        target[clean_name].append(value)
        sentiment = str(row["csat_sentiment"] or "").strip().lower()
        if str(row["csat_response"] or "").strip() or sentiment:
            positive = sentiment in {"positive", "happy", "satisfied", "good", "excellent"}
            for target in targets:
                target["csat_received"] += 1
                target["csat_positive" if positive else "csat_negative"] += 1

    available_agents = sorted({name for channel_agents in agents.values() for name in channel_agents})
    selected_agent = selected_agent if selected_agent in available_agents else None
    channels = {}
    for key, (label, source) in labels.items():
        values = agents[key].get(selected_agent, bucket()) if selected_agent else overall[key]
        channels[key] = {"key": key, "label": label, "source": source, **finish(values)}
        channels[key]["agents"] = sorted(
            ({"name": name, **finish(agent_values)} for name, agent_values in agents[key].items()
             if not selected_agent or name == selected_agent),
            key=lambda item: (-item["total"], item["name"]))
    metrics = list(channels.values())
    return {"start": start_date, "end": end_date, "channels": channels, "metrics": metrics,
            "available_agents": available_agents, "selected_agent": selected_agent,
            "agents": [], "total": sum(x["total"] for x in metrics),
            "resolved": sum(x["resolved"] for x in metrics), "spam": sum(x["spam"] for x in metrics)}


def cs_detail_rows(start_date, end_date, channel_key="all", metric="total", agent_filter=None):
    """Return the safe, auditable source rows behind a dashboard count."""
    rows = []
    with db_connection() as db:
        zoho = db.execute("""SELECT id,ticket_number,department_name,channel,status,status_type,
            created_time,created_local_date,closed_time,raw_json FROM zoho_tickets
            WHERE created_local_date BETWEEN ? AND ?""", (start_date, end_date)).fetchall()
        bik = db.execute("""SELECT ticket_id,local_date,channel,ticket_stage,agent_name,created_time,
            closed_time,closure_seconds,first_response_seconds,human_response_seconds,agent_messages,raw_json
            FROM bik_tickets WHERE local_date BETWEEN ? AND ?""", (start_date, end_date)).fetchall()
    for row in zoho:
        source_channel = (row["channel"] or "").lower()
        key = "email" if source_channel == "email" else ("inbound" if source_channel in {"phone", "inbound"} else None)
        if not key or channel_key not in {"all", key}:
            continue
        raw = json.loads(row["raw_json"] or "{}")
        status = (row["status_type"] or row["status"] or "").lower()
        resolved = status in {"closed", "resolved"} or bool(row["closed_time"])
        escalated = status in {"escalated", "on hold", "onhold"}
        subcategory = str(raw.get("subCategory") or "").strip()
        spam = bool(raw.get("isSpam")) or subcategory.lower() in SPAM_SUBCATEGORIES or tagged_general(raw)
        untagged = not spam and (not subcategory or subcategory == "-")
        replies = int(raw.get("_agentReplyCount") or 0)
        flags = {"total": True, "resolved": resolved, "open": not resolved, "escalated": escalated,
                 "spam": spam, "genuine": not spam and not untagged, "untagged": untagged,
                 "working_hours": working_hours(row["created_time"]),
                 "outside_hours": not working_hours(row["created_time"]), "agent_replies": replies > 0}
        if not flags.get(metric, False) or (agent_filter and zoho_agent(raw) != agent_filter):
            continue
        rows.append({"source": "Zoho Desk", "channel": key.title(), "record_id": row["ticket_number"] or row["id"],
                     "date": row["created_local_date"], "created_time": row["created_time"],
                     "agent": zoho_agent(raw), "status": row["status"], "classification": "Spam" if spam else ("Untagged" if untagged else "Genuine"),
                     "category": raw.get("category") or (raw.get("customFields") or {}).get("Type") or "",
                     "disposition": subcategory or (raw.get("customFields") or {}).get("Disposition Category") or "",
                     "message_type": "", "first_response_seconds": raw.get("_firstResponseSeconds"),
                     "average_response_seconds": raw.get("_averageResponseSeconds"),
                     "resolution_seconds": None, "agent_reply_count": replies})
    for row in bik:
        source_channel = (row["channel"] or "").lower()
        key = "chats" if source_channel in {"whatsapp", "chat", "chats", "livechat"} else (
            "orm" if source_channel in {"instagram", "messenger", "facebook", "fb", "ig"} else None)
        if not key or channel_key not in {"all", key}:
            continue
        raw = json.loads(row["raw_json"] or "{}")
        stage = (row["ticket_stage"] or "").upper()
        spam = stage in SPAM or tagged_general(raw)
        resolved, escalated = stage in RESOLVED or spam, stage in ESCALATED
        name = agent_name(row["agent_name"])
        message_type = str(raw.get("Type of Message") or "")
        system = name.lower() in SYSTEM_AGENTS
        comment = "comment" in message_type.lower()
        flags = {"total": True, "resolved": resolved, "open": not resolved, "escalated": escalated,
                 "spam": spam, "genuine": not spam, "bot_handled": system,
                 "bot_resolved": system and resolved, "human_handover": not system,
                 "instagram_dm": key == "orm" and source_channel in {"instagram", "ig"} and not comment,
                 "instagram_comment": key == "orm" and source_channel in {"instagram", "ig"} and comment,
                 "facebook_dm": key == "orm" and source_channel not in {"instagram", "ig"} and not comment,
                 "facebook_comment": key == "orm" and source_channel not in {"instagram", "ig"} and comment}
        if not flags.get(metric, False) or (agent_filter and name != agent_filter):
            continue
        rows.append({"source": "BIK", "channel": key.title(), "record_id": row["ticket_id"] or "",
                     "date": row["local_date"], "created_time": row["created_time"], "agent": name,
                     "status": row["ticket_stage"], "classification": "Spam" if spam else "Genuine",
                     "category": row["channel"], "disposition": raw.get("Ticket label") or "",
                     "message_type": message_type, "first_response_seconds": row["first_response_seconds"],
                     "average_response_seconds": row["human_response_seconds"],
                     "resolution_seconds": row["closure_seconds"], "agent_reply_count": int(row["agent_messages"] or 0)})
    return sorted(rows, key=lambda item: (item["date"] or "", str(item["record_id"])), reverse=True)
