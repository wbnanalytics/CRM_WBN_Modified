#!/usr/bin/env python3
"""Import the read-only Forecast Scorecard snapshot into WBN CRM.

The import is idempotent: agents, department templates, and scorecards are
upserted using stable employee/date keys. A timestamped database backup is
created before every run.
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def number(value):
    if value in (None, "", "—"):
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except ValueError:
        return None


def rules_from_grid(text):
    rules = []
    for line in re.split(r"\n|;", text or ""):
        points_match = re.search(r"(?:=|:|—|-)\s*([\d.]+)\s*(?:pts?|points?)?\s*$", line, re.I)
        if not points_match:
            continue
        condition = line[: points_match.start()].strip()
        points = float(points_match.group(1))
        factor = 0.01 if "%" in condition else 1
        match = re.search(r"([\d.]+)%?\s*[-–]\s*([\d.]+)%?", condition)
        if match:
            rules.append({"operator": "between", "threshold": [float(match.group(1)) * factor, float(match.group(2)) * factor], "points": points})
            continue
        match = re.search(r"([\d.]+)%?\s+and\s+(?:above|over)", condition, re.I)
        if match:
            rules.append({"operator": ">=", "threshold": float(match.group(1)) * factor, "points": points})
            continue
        match = re.search(r"(?:≥|>=)\s*([\d.]+)%?", condition)
        if match:
            rules.append({"operator": ">=", "threshold": float(match.group(1)) * factor, "points": points})
            continue
        match = re.search(r"(?:≤|<=)\s*([\d.]+)%?", condition)
        if match:
            rules.append({"operator": "<=", "threshold": float(match.group(1)) * factor, "points": points})
            continue
        match = re.search(r"(?:less\s+than|<)\s*([\d.]+)%?", condition, re.I)
        if match:
            rules.append({"operator": "<", "threshold": float(match.group(1)) * factor, "points": points})
            continue
        match = re.search(r"(?:>|more\s+than)\s*([\d.]+)%?", condition, re.I)
        if match:
            rules.append({"operator": ">", "threshold": float(match.group(1)) * factor, "points": points})
            continue
        match = re.search(r"^([\d.]+)%?", condition)
        if match:
            rules.append({"operator": "==", "threshold": float(match.group(1)) * factor, "points": points})
    return rules


def source_id(item):
    match = re.search(r"/(\d+)/excel$", item["summary"].get("excel", ""))
    return match.group(1) if match else "unknown"


def form_value(form, input_type, default=""):
    for item in form or []:
        if item.get("type") == input_type:
            return item.get("value") or default
    return default


def period_start(label):
    return datetime.strptime(label, "%b %Y").date().replace(day=1).isoformat()


def run(snapshot_path, data_dir):
    os.environ["CRM_DATA_DIR"] = str(data_dir)
    project_root = str(Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from database import DB_PATH, db_connection, init_db, utc_now

    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    init_db()
    if DB_PATH.exists():
        backup_dir = DB_PATH.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        shutil.copy2(DB_PATH, backup_dir / f"crm-before-forecast-{stamp}.db")

    actor = "forecast-scorecard-migration"
    now = utc_now()
    stats = {"agents": 0, "templates": 0, "scorecards": 0, "scorecard_details": 0, "appraisals": 0}

    with db_connection() as db:
        for agent in snapshot["agents"]:
            start_date = form_value(agent.get("form"), "date", "2026-01-01")
            active = int(agent.get("status") == "Active")
            existing = db.execute("SELECT id FROM crm_agents WHERE emp_id=?", (agent["emp_id"],)).fetchone()
            if existing:
                db.execute(
                    """UPDATE crm_agents SET name=?, email=?, department=?, start_date=?, active=?, updated_at=? WHERE id=?""",
                    (agent["name"], agent["email"].lower(), agent["department"], start_date, active, now, existing["id"]),
                )
            else:
                db.execute(
                    """INSERT INTO crm_agents
                       (emp_id,name,email,department,start_date,active,created_by,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (agent["emp_id"], agent["name"], agent["email"].lower(), agent["department"], start_date, active, actor, now, now),
                )
            stats["agents"] += 1

        template_ids = {}
        template_metrics = {}
        for template in snapshot["templates"]:
            department = template["department"]
            zero_conditions = [line.strip() for line in template.get("zero_conditions", "").splitlines() if line.strip()]
            current = db.execute("SELECT id FROM crm_kpi_templates WHERE department=?", (department,)).fetchone()
            if current:
                template_id = current["id"]
                db.execute(
                    """UPDATE crm_kpi_templates SET name=?,zero_score_conditions=?,active=1,updated_at=? WHERE id=?""",
                    (template["name"], json.dumps(zero_conditions, ensure_ascii=False), now, template_id),
                )
                db.execute("DELETE FROM crm_kpi_metrics WHERE template_id=?", (template_id,))
            else:
                template_id = db.execute(
                    """INSERT INTO crm_kpi_templates
                       (department,name,zero_score_conditions,active,created_by,created_at,updated_at)
                       VALUES (?,?,?,1,?,?,?)""",
                    (department, template["name"], json.dumps(zero_conditions, ensure_ascii=False), actor, now, now),
                ).lastrowid
            metrics = []
            for order, row in enumerate(template["rows"]):
                section, kpi, grid, kra, weightage, max_points = row
                rules = rules_from_grid(grid)
                db.execute(
                    """INSERT INTO crm_kpi_metrics
                       (template_id,section,kpi,grid_scoring,kra,weightage,max_points,scoring_rules,sort_order)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (template_id, section, kpi, grid, kra, float(weightage), float(max_points), json.dumps(rules, ensure_ascii=False), order),
                )
                metrics.append({"section": section, "kpi": kpi, "grid": grid, "kra": kra, "weightage": float(weightage), "max_points": float(max_points), "rules": rules})
            template_ids[department] = template_id
            template_metrics[department] = metrics
            stats["templates"] += 1

        agents_by_name = {row["name"]: row for row in db.execute("SELECT id,name FROM crm_agents").fetchall()}
        for item in snapshot["scorecards"]:
            cells = item["summary"]["cells"]
            employee_name, department, period = cells[0], cells[1], cells[2]
            total_text, max_text = [part.strip() for part in cells[3].split("/", 1)]
            total_points, max_points = float(total_text), float(max_text)
            detail = item.get("detail")
            controls = detail.get("controls", []) if detail else []
            evaluation_date = controls[0]["value"] if controls else period_start(period)
            positive = controls[1]["value"] if len(controls) > 1 else ""
            development = controls[2]["value"] if len(controls) > 2 else ""
            zero = int(bool(controls[3].get("checked"))) if len(controls) > 3 else 0
            agent = agents_by_name.get(employee_name)
            if not agent:
                raise RuntimeError(f"Missing agent for scorecard: {employee_name}")
            template_id = template_ids[department]
            score_pct = round(total_points / max_points * 100, 2) if max_points else 0
            external = source_id(item)
            existing = db.execute(
                "SELECT id FROM crm_scorecards WHERE agent_id=? AND evaluation_date=?",
                (agent["id"], evaluation_date),
            ).fetchone()
            values = (template_id, positive, development, zero, total_points, max_points, score_pct, actor + ":" + external, now, now)
            if existing:
                scorecard_id = existing["id"]
                db.execute(
                    """UPDATE crm_scorecards SET template_id=?,positive_feedback=?,area_of_development=?,
                       zero_score_triggered=?,total_points=?,max_points=?,score_pct=?,status='finalized',
                       created_by=?,updated_at=?,finalized_at=COALESCE(finalized_at,?) WHERE id=?""",
                    (*values, scorecard_id),
                )
                db.execute("DELETE FROM crm_scorecard_rows WHERE scorecard_id=?", (scorecard_id,))
            else:
                scorecard_id = db.execute(
                    """INSERT INTO crm_scorecards
                       (agent_id,template_id,evaluation_date,positive_feedback,area_of_development,
                        zero_score_triggered,total_points,max_points,score_pct,status,created_by,created_at,updated_at,finalized_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'finalized',?,?,?,?)""",
                    (agent["id"], template_id, evaluation_date, positive, development, zero, total_points, max_points, score_pct, actor + ":" + external, now, now, now),
                ).lastrowid

            metrics = template_metrics[department]
            metric_lookup = {metric["kpi"]: metric for metric in metrics}
            detail_rows = [row for row in (detail.get("rows", []) if detail else []) if len(row) >= 8]
            control_index = 4
            for order, metric in enumerate(metrics):
                row = next((candidate for candidate in detail_rows if candidate[1] == metric["kpi"]), None)
                achieved = target = ratio = None
                remarks = ""
                final_points = 0.0
                grid = metric["grid"]
                if row and control_index < len(controls):
                    grid = row[2]
                    achieved = number(controls[control_index].get("value")); control_index += 1
                    if row[4] != "—" and control_index < len(controls):
                        target = number(controls[control_index].get("value")); control_index += 1
                    if control_index < len(controls):
                        remarks = controls[control_index].get("value", ""); control_index += 1
                    ratio_value = number(row[5])
                    ratio = ratio_value / 100 if row[5].endswith("%") and ratio_value is not None else ratio_value
                    final_points = number(row[6]) or 0.0
                db.execute(
                    """INSERT INTO crm_scorecard_rows
                       (scorecard_id,section,kpi,grid_scoring,kra,weightage,max_points,scoring_rules,
                        achieved,target,score_ratio,final_points,manager_remarks,sort_order)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (scorecard_id, metric["section"], metric["kpi"], grid, metric["kra"], metric["weightage"], metric["max_points"],
                     json.dumps(rules_from_grid(grid), ensure_ascii=False), achieved, target, ratio, final_points, remarks, order),
                )
            stats["scorecards"] += 1
            stats["scorecard_details"] += int(bool(detail))

        for item in snapshot["appraisals"]:
            cells = item["summary"]["cells"]
            employee_name = cells[0]
            detail = item["detail"]
            controls = detail["controls"]
            period_label, review_date = controls[0]["value"], controls[1]["value"]
            kpi_rating = number(controls[2]["value"])
            agent = agents_by_name.get(employee_name)
            if not agent:
                raise RuntimeError(f"Missing agent for appraisal: {employee_name}")
            existing = db.execute(
                "SELECT id FROM crm_appraisals WHERE agent_id=? AND period_label=? AND review_date=?",
                (agent["id"], period_label, review_date),
            ).fetchone()
            if existing:
                appraisal_id = existing["id"]
                db.execute(
                    """UPDATE crm_appraisals SET kpi_rating_5=?,status='draft',created_by=?,updated_at=? WHERE id=?""",
                    (kpi_rating, actor + ":" + source_id(item), now, appraisal_id),
                )
                db.execute("DELETE FROM crm_appraisal_rows WHERE appraisal_id=?", (appraisal_id,))
            else:
                appraisal_id = db.execute(
                    """INSERT INTO crm_appraisals
                       (agent_id,period_label,review_date,kpi_rating_5,status,created_by,created_at,updated_at)
                       VALUES (?,?,?,?,'draft',?,?,?)""",
                    (agent["id"], period_label, review_date, kpi_rating, actor + ":" + source_id(item), now, now),
                ).lastrowid
            appraisal_rows = [row for row in detail["rows"] if len(row) >= 6]
            for order, row in enumerate(appraisal_rows):
                db.execute(
                    """INSERT INTO crm_appraisal_rows
                       (appraisal_id,section,criteria,guidance,weightage,rating,comments,sort_order)
                       VALUES (?,?,?,?,?,NULL,'',?)""",
                    (appraisal_id, row[0], row[1], row[2], float(row[3]), order),
                )
            stats["appraisals"] += 1

    with db_connection() as db:
        actual = {
            "agents": db.execute("SELECT COUNT(*) n FROM crm_agents").fetchone()["n"],
            "templates": db.execute("SELECT COUNT(*) n FROM crm_kpi_templates").fetchone()["n"],
            "scorecards": db.execute("SELECT COUNT(*) n FROM crm_scorecards").fetchone()["n"],
            "appraisals": db.execute("SELECT COUNT(*) n FROM crm_appraisals").fetchone()["n"],
            "score_total": db.execute("SELECT ROUND(SUM(total_points),2) n FROM crm_scorecards").fetchone()["n"],
        }
    return {"imported": stats, "database": actual, "database_path": str(DB_PATH)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot")
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, Path(args.data_dir)), indent=2))
