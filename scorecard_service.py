import json
import re
from datetime import date, datetime

from database import (
    PRIMARY_ADMIN_EMAIL, db_connection, get_portal_user, save_portal_user, set_portal_permissions,
    utc_now,
)


DEFAULT_BEHAVIOURS = (
    ("WBN Values", "Ownership", "Takes responsibility and closes commitments without follow-up.", 20),
    ("WBN Values", "Collaboration", "Works constructively across teams and supports shared outcomes.", 20),
    ("Capability", "Communication", "Communicates clearly, promptly and with the right context.", 20),
    ("Capability", "Problem solving", "Uses data and sound judgement to resolve issues.", 20),
    ("Growth", "Learning agility", "Seeks feedback and applies learning to improve performance.", 20),
)


def _json(value, fallback=None):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return [] if fallback is None else fallback


def _as_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def list_agents(active_only=False):
    where = "WHERE active=1" if active_only else ""
    with db_connection() as db:
        rows = db.execute(
            f"SELECT * FROM crm_agents {where} ORDER BY active DESC, name COLLATE NOCASE"
        ).fetchall()
    return [dict(row) for row in rows]


def ensure_agent_portal_access(agent, actor="scorecard-sync"):
    """Keep active Scorecard agents as read-only dashboard and Shopify users."""
    email = (agent.get("email") or "").strip().lower()
    if not agent.get("active") or not email.endswith("@wellbeingnutrition.com"):
        return False
    if email == PRIMARY_ADMIN_EMAIL:
        return False
    existing = get_portal_user(email)
    # Never downgrade an admin or overwrite access that an admin has already
    # configured. Automatic enrollment is only for agents missing from the
    # portal directory.
    if existing:
        return False
    save_portal_user(email, agent.get("name") or email.split("@", 1)[0], "user", True, actor)
    set_portal_permissions(email, ["dashboard", "shopify.view"], actor)
    return True


def sync_agents_to_portal_users(actor="scorecard-sync"):
    return sum(1 for agent in list_agents(active_only=True) if ensure_agent_portal_access(agent, actor))


def get_agent(agent_id=None, emp_id=None):
    field, value = ("id", agent_id) if agent_id is not None else ("emp_id", emp_id)
    with db_connection() as db:
        row = db.execute(f"SELECT * FROM crm_agents WHERE {field}=?", (value,)).fetchone()
    return dict(row) if row else None


def save_agent(payload, actor, agent_id=None):
    emp_id = (payload.get("emp_id") or "").strip().upper()
    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    department = (payload.get("department") or "").strip()
    start_date = payload.get("start_date") or date.today().isoformat()
    if not emp_id or not name or not department:
        raise ValueError("Employee ID, name and department are required")
    if not email.endswith("@wellbeingnutrition.com"):
        raise ValueError("Use a wellbeingnutrition.com email address")
    now = utc_now()
    with db_connection() as db:
        if agent_id:
            db.execute(
                """UPDATE crm_agents SET emp_id=?, name=?, email=?, department=?, start_date=?,
                   active=?, updated_at=? WHERE id=?""",
                (emp_id, name, email, department, start_date, int(bool(payload.get("active", True))), now, agent_id),
            )
            saved_id = agent_id
        else:
            cursor = db.execute(
                """INSERT INTO crm_agents
                   (emp_id, name, email, department, start_date, active, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (emp_id, name, email, department, start_date, int(bool(payload.get("active", True))), actor, now, now),
            )
            saved_id = cursor.lastrowid
    ensure_agent_portal_access(get_agent(agent_id=saved_id), actor)
    return saved_id


def toggle_agent(agent_id):
    with db_connection() as db:
        db.execute("UPDATE crm_agents SET active=1-active, updated_at=? WHERE id=?", (utc_now(), agent_id))


def delete_agent(agent_id):
    with db_connection() as db:
        used = db.execute("SELECT 1 FROM crm_scorecards WHERE agent_id=? LIMIT 1", (agent_id,)).fetchone()
        used = used or db.execute("SELECT 1 FROM crm_appraisals WHERE agent_id=? LIMIT 1", (agent_id,)).fetchone()
        if used:
            raise ValueError("This agent has scorecard history. Deactivate the agent instead.")
        db.execute("DELETE FROM crm_agents WHERE id=?", (agent_id,))


def list_templates():
    with db_connection() as db:
        templates = db.execute(
            """SELECT t.*, COUNT(m.id) metric_count, COALESCE(SUM(m.max_points),0) total_points
               FROM crm_kpi_templates t LEFT JOIN crm_kpi_metrics m ON m.template_id=t.id
               GROUP BY t.id ORDER BY t.department"""
        ).fetchall()
    return [dict(row, zero_score_conditions=_json(row["zero_score_conditions"])) for row in templates]


def get_template(department):
    with db_connection() as db:
        row = db.execute("SELECT * FROM crm_kpi_templates WHERE department=?", (department,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["zero_score_conditions"] = _json(result["zero_score_conditions"])
        result["metrics"] = [dict(metric, scoring_rules=_json(metric["scoring_rules"])) for metric in db.execute(
            "SELECT * FROM crm_kpi_metrics WHERE template_id=? ORDER BY sort_order, id", (row["id"],)
        ).fetchall()]
    return result


def save_template(department, name, zero_conditions, metrics, actor):
    department = (department or "").strip()
    if not department:
        raise ValueError("Department is required")
    cleaned = []
    for index, metric in enumerate(metrics):
        kpi = (metric.get("kpi") or "").strip()
        if not kpi:
            continue
        max_points = float(metric.get("max_points") or 0)
        if max_points < 0:
            raise ValueError("Maximum points cannot be negative")
        cleaned.append((
            (metric.get("section") or "BUSINESS PARAMETERS/KPI").strip(), kpi,
            (metric.get("grid_scoring") or "").strip(), (metric.get("kra") or "").strip(),
            float(metric.get("weightage") or 0), max_points,
            json.dumps(metric.get("scoring_rules") or [], ensure_ascii=False), index,
        ))
    if not cleaned:
        raise ValueError("Add at least one KPI")
    now = utc_now()
    with db_connection() as db:
        current = db.execute("SELECT id FROM crm_kpi_templates WHERE department=?", (department,)).fetchone()
        if current:
            template_id = current["id"]
            db.execute(
                """UPDATE crm_kpi_templates SET name=?, zero_score_conditions=?, active=1, updated_at=?
                   WHERE id=?""", (name or f"{department} Scorecard", json.dumps(zero_conditions), now, template_id),
            )
            db.execute("DELETE FROM crm_kpi_metrics WHERE template_id=?", (template_id,))
        else:
            cursor = db.execute(
                """INSERT INTO crm_kpi_templates
                   (department, name, zero_score_conditions, active, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, 1, ?, ?, ?)""",
                (department, name or f"{department} Scorecard", json.dumps(zero_conditions), actor, now, now),
            )
            template_id = cursor.lastrowid
        db.executemany(
            """INSERT INTO crm_kpi_metrics
               (template_id, section, kpi, grid_scoring, kra, weightage, max_points, scoring_rules, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(template_id, *metric) for metric in cleaned],
        )
    return template_id


def create_scorecard(emp_id, evaluation_date, actor):
    agent = get_agent(emp_id=emp_id)
    if not agent or not agent["active"]:
        raise ValueError("Select an active agent")
    template = get_template(agent["department"])
    if not template or not template["metrics"]:
        raise ValueError(f"Create a KPI template for {agent['department']} first")
    now = utc_now()
    with db_connection() as db:
        try:
            cursor = db.execute(
                """INSERT INTO crm_scorecards
                   (agent_id, template_id, evaluation_date, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (agent["id"], template["id"], evaluation_date, actor, now, now),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise ValueError("A scorecard already exists for this agent and date") from exc
            raise
        scorecard_id = cursor.lastrowid
        db.executemany(
            """INSERT INTO crm_scorecard_rows
               (scorecard_id, section, kpi, grid_scoring, kra, weightage, max_points,
                scoring_rules, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(scorecard_id, metric["section"], metric["kpi"], metric["grid_scoring"], metric["kra"],
              metric["weightage"], metric["max_points"], json.dumps(metric["scoring_rules"]), index)
             for index, metric in enumerate(template["metrics"])],
        )
    return scorecard_id


def list_scorecards():
    with db_connection() as db:
        rows = db.execute(
            """SELECT s.*, a.emp_id, a.name employee_name, a.email employee_email, a.department
               FROM crm_scorecards s JOIN crm_agents a ON a.id=s.agent_id
               ORDER BY s.evaluation_date DESC, s.id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_scorecard(scorecard_id):
    with db_connection() as db:
        row = db.execute(
            """SELECT s.*, a.emp_id, a.name employee_name, a.email employee_email, a.department,
                      t.name template_name, t.zero_score_conditions
               FROM crm_scorecards s JOIN crm_agents a ON a.id=s.agent_id
               JOIN crm_kpi_templates t ON t.id=s.template_id WHERE s.id=?""", (scorecard_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["zero_score_conditions"] = _json(result["zero_score_conditions"])
        result["rows"] = [dict(item, scoring_rules=_json(item["scoring_rules"])) for item in db.execute(
            "SELECT * FROM crm_scorecard_rows WHERE scorecard_id=? ORDER BY sort_order, id", (scorecard_id,)
        ).fetchall()]
        result["email_log"] = [dict(item) for item in db.execute(
            """SELECT * FROM crm_scorecard_email_log
               WHERE record_type='scorecard' AND record_id=? ORDER BY id DESC""", (scorecard_id,)
        ).fetchall()]
    return result


def _calculate_points(achieved, target, rules, max_points):
    if achieved is None:
        return None, 0.0
    ratio = achieved / target if target not in (None, 0) else achieved
    for rule in rules:
        threshold = rule.get("threshold")
        operator = rule.get("operator")
        matched = False
        if operator == ">=": matched = ratio >= float(threshold)
        elif operator == ">": matched = ratio > float(threshold)
        elif operator == "<=": matched = ratio <= float(threshold)
        elif operator == "<": matched = ratio < float(threshold)
        elif operator == "==": matched = ratio == float(threshold)
        elif operator == "between" and isinstance(threshold, list) and len(threshold) == 2:
            matched = float(threshold[0]) <= ratio <= float(threshold[1])
        if matched:
            return ratio, min(float(rule.get("points") or 0), max_points)
    if not rules:
        normalized = ratio if target else achieved / 100 if achieved > 1 else achieved
        return ratio, max(0, min(normalized, 1)) * max_points
    return ratio, 0.0


def update_scorecard(scorecard_id, payload):
    current = get_scorecard(scorecard_id)
    if not current:
        raise ValueError("Scorecard not found")
    submitted = {int(row["id"]): row for row in payload.get("rows", [])}
    zero = bool(payload.get("zero_score_triggered"))
    updates, total_points, max_points = [], 0.0, 0.0
    for row in current["rows"]:
        entry = submitted.get(row["id"], {})
        achieved, target = _as_float(entry.get("achieved")), _as_float(entry.get("target"))
        ratio, points = _calculate_points(achieved, target, row["scoring_rules"], row["max_points"])
        if zero:
            points = 0.0
        max_points += row["max_points"]
        total_points += points
        updates.append((achieved, target, ratio, round(points, 2), (entry.get("manager_remarks") or "").strip(), row["id"]))
    pct = round(total_points / max_points * 100, 2) if max_points else 0
    now = utc_now()
    with db_connection() as db:
        db.executemany(
            """UPDATE crm_scorecard_rows SET achieved=?, target=?, score_ratio=?, final_points=?,
               manager_remarks=? WHERE id=?""", updates,
        )
        db.execute(
            """UPDATE crm_scorecards SET evaluation_date=?, positive_feedback=?, area_of_development=?,
               zero_score_triggered=?, total_points=?, max_points=?, score_pct=?, status=?,
               finalized_at=CASE WHEN ?='finalized' THEN COALESCE(finalized_at, ?) ELSE finalized_at END,
               updated_at=? WHERE id=?""",
            (payload.get("evaluation_date") or current["evaluation_date"], (payload.get("positive") or "").strip(),
             (payload.get("development") or "").strip(), int(zero), round(total_points, 2), round(max_points, 2), pct,
             payload.get("status") if payload.get("status") in {"draft", "finalized"} else current["status"],
             payload.get("status"), now, now, scorecard_id),
        )


def delete_scorecard(scorecard_id):
    with db_connection() as db:
        db.execute("DELETE FROM crm_scorecards WHERE id=?", (scorecard_id,))


def create_appraisal(emp_id, review_date, period_label, actor):
    agent = get_agent(emp_id=emp_id)
    if not agent or not agent["active"]:
        raise ValueError("Select an active agent")
    now = utc_now()
    label = (period_label or f"Annual Review {review_date[:4]}").strip()
    with db_connection() as db:
        cursor = db.execute(
            """INSERT INTO crm_appraisals
               (agent_id, period_label, review_date, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""", (agent["id"], label, review_date, actor, now, now),
        )
        appraisal_id = cursor.lastrowid
        db.executemany(
            """INSERT INTO crm_appraisal_rows
               (appraisal_id, section, criteria, guidance, weightage, sort_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(appraisal_id, section, criteria, guidance, weight, index)
             for index, (section, criteria, guidance, weight) in enumerate(DEFAULT_BEHAVIOURS)],
        )
    return appraisal_id


def list_appraisals():
    with db_connection() as db:
        rows = db.execute(
            """SELECT p.*, a.emp_id, a.name employee_name, a.email employee_email, a.department
               FROM crm_appraisals p JOIN crm_agents a ON a.id=p.agent_id
               ORDER BY p.review_date DESC, p.id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_appraisal(appraisal_id):
    with db_connection() as db:
        row = db.execute(
            """SELECT p.*, a.emp_id, a.name employee_name, a.email employee_email, a.department
               FROM crm_appraisals p JOIN crm_agents a ON a.id=p.agent_id WHERE p.id=?""", (appraisal_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["rows"] = [dict(item) for item in db.execute(
            "SELECT * FROM crm_appraisal_rows WHERE appraisal_id=? ORDER BY sort_order, id", (appraisal_id,)
        ).fetchall()]
        year = int(result["review_date"][:4])
        suggestion = db.execute(
            """SELECT AVG(score_pct)/20 suggested FROM crm_scorecards
               WHERE agent_id=? AND evaluation_date BETWEEN ? AND ? AND status<>'draft'""",
            (result["agent_id"], f"{year-1}-01-01", result["review_date"]),
        ).fetchone()["suggested"]
        result["kpi_suggestion"] = round(suggestion, 2) if suggestion is not None else None
        result["email_log"] = [dict(item) for item in db.execute(
            """SELECT * FROM crm_scorecard_email_log
               WHERE record_type='appraisal' AND record_id=? ORDER BY id DESC""", (appraisal_id,)
        ).fetchall()]
    return result


def update_appraisal(appraisal_id, payload):
    current = get_appraisal(appraisal_id)
    if not current:
        raise ValueError("Appraisal not found")
    submitted = {int(row["id"]): row for row in payload.get("rows", [])}
    weighted, weights = 0.0, 0.0
    updates = []
    for row in current["rows"]:
        entry = submitted.get(row["id"], {})
        rating = _as_float(entry.get("rating"))
        if rating is not None and not 1 <= rating <= 5:
            raise ValueError("Behaviour ratings must be between 1 and 5")
        if rating is not None:
            weighted += rating * row["weightage"]
            weights += row["weightage"]
        updates.append((rating, (entry.get("comments") or "").strip(), row["id"]))
    behaviour = round(weighted / weights, 2) if weights else None
    kpi = _as_float(payload.get("kpi_rating_5"))
    if kpi is not None and not 0 <= kpi <= 5:
        raise ValueError("KPI rating must be between 0 and 5")
    total = round((kpi * .6 + behaviour * .4) * 2, 2) if kpi is not None and behaviour is not None else None
    now = utc_now()
    with db_connection() as db:
        db.executemany("UPDATE crm_appraisal_rows SET rating=?, comments=? WHERE id=?", updates)
        db.execute(
            """UPDATE crm_appraisals SET period_label=?, review_date=?, kpi_rating_5=?,
               behaviour_rating_5=?, total_rating_10=?, strengths=?, improvements=?,
               learning_requirements=?, overall_band=?, increment_pct=?, promotion_recommendation=?,
               status=?, updated_at=? WHERE id=?""",
            ((payload.get("period_label") or current["period_label"]).strip(), payload.get("review_date") or current["review_date"],
             kpi, behaviour, total, (payload.get("strengths") or "").strip(),
             (payload.get("improvements") or "").strip(), (payload.get("learning") or "").strip(),
             (payload.get("overall_band") or "").strip(), _as_float(payload.get("increment_pct")),
             (payload.get("promotion") or "").strip(),
             payload.get("status") if payload.get("status") in {"draft", "finalized"} else current["status"], now, appraisal_id),
        )


def delete_appraisal(appraisal_id):
    with db_connection() as db:
        db.execute("DELETE FROM crm_appraisals WHERE id=?", (appraisal_id,))


def dashboard_data(department=None, year=None, agent_emp_id=None):
    agents = list_agents()
    cards = list_scorecards()
    if department:
        cards = [card for card in cards if card["department"] == department]
    if year:
        cards = [card for card in cards if card["evaluation_date"].startswith(str(year))]
    if agent_emp_id:
        cards = [card for card in cards if card["emp_id"] == agent_emp_id]
    completed = [card for card in cards if card["status"] != "draft"]
    by_agent = {}
    for card in completed:
        by_agent.setdefault(card["employee_name"], []).append(card["score_pct"])
    averages = [(name, round(sum(values) / len(values), 1)) for name, values in by_agent.items()]
    averages.sort(key=lambda item: item[1], reverse=True)
    needs_attention = min(averages, key=lambda item: (item[1], item[0].casefold())) if averages else None
    tiers = {"On track": 0, "Needs focus": 0, "At risk": 0, "No data": 0}
    for agent in agents:
        values = [card["score_pct"] for card in completed if card["agent_id"] == agent["id"]]
        if not values: tiers["No data"] += 1
        elif sum(values) / len(values) >= 85: tiers["On track"] += 1
        elif sum(values) / len(values) >= 65: tiers["Needs focus"] += 1
        else: tiers["At risk"] += 1
    return {
        "total_agents": len([agent for agent in agents if agent["active"]]),
        "total_scorecards": len(cards), "top_performer": averages[0] if averages else None,
        "needs_attention": needs_attention, "tiers": tiers,
        "recent": cards[:8], "departments": sorted({agent["department"] for agent in agents}),
        "years": sorted({card["evaluation_date"][:4] for card in list_scorecards()}, reverse=True),
        "agents": agents,
    }


def log_email(record_type, record_id, recipient, sent_by, status, error_message=None):
    now = utc_now()
    table = "crm_scorecards" if record_type == "scorecard" else "crm_appraisals"
    with db_connection() as db:
        db.execute(
            """INSERT INTO crm_scorecard_email_log
               (record_type, record_id, recipient, sent_by, status, error_message, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""", (record_type, record_id, recipient, sent_by, status, error_message, now),
        )
        if status == "sent":
            db.execute(
                f"UPDATE {table} SET status='emailed', emailed_at=?, emailed_by=?, updated_at=? WHERE id=?",
                (now, sent_by, now, record_id),
            )
