import argparse
import html
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from attendance_service import attendance_period
from database import db_connection, list_portal_users, list_shopify_employees, utc_now
from sales_celebration_mailer import (
    _celebration_animation_path, _email_shell, _inr, _name_chips,
    _perfect_attendance_animation_path, _period_rankings, ranking_email,
    _target_achiever_animation_path,
)
from scorecard_mailer import send_html_email
from scorecard_service import list_agents
from shopify_service import sales_report


IST = ZoneInfo("Asia/Kolkata")
ALWAYS_TO = [
    "soumik@wellbeingnutrition.com",
    "khushbu.nishad@wellbeingnutrition.com",
    "ashutosh@wellbeingnutrition.com",
]
FIXED_CC = [
    "varun.c@wellbeingnutrition.com",
    "alcantro@wellbeingnutrition.com",
    "ravindra@wellbeingnutrition.com",
    "adina@wellbeingnutrition.com",
    "shezana.bhojani@wellbeingnutrition.com",
    "yash.sawant@wellbeingnutrition.com",
    "shareena.dsilva@wellbeingnutrition.com",
]


def _normalize(value):
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def _email_directory():
    eligible_names = {
        _normalize(employee["display_name"])
        for employee in list_shopify_employees()
        if employee.get("active") and employee.get("include_in_team")
    }
    directory = {
        _normalize(user["display_name"]): user["email"].strip().lower()
        for user in list_portal_users()
        if user.get("active")
        if _normalize(user.get("display_name")) in eligible_names
        if (user.get("email") or "").lower().endswith("@wellbeingnutrition.com")
    }
    directory.update({
        _normalize(agent["name"]): agent["email"].strip().lower()
        for agent in list_agents(active_only=True)
        if _normalize(agent["name"]) in eligible_names
        if (agent.get("email") or "").lower().endswith("@wellbeingnutrition.com")
    })
    return directory


def _logo_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "wbn-logo.png")


def _init_log():
    with db_connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS crm_recognition_email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            email_type TEXT NOT NULL,
            period_label TEXT NOT NULL,
            recipients TEXT NOT NULL,
            sent_at TEXT NOT NULL
        )""")


def _already_sent(event_key):
    with db_connection() as db:
        return bool(db.execute(
            "SELECT 1 FROM crm_recognition_email_log WHERE event_key=?", (event_key,)
        ).fetchone())


def _record_sent(event_key, email_type, period_label, recipients):
    with db_connection() as db:
        db.execute(
            """INSERT OR IGNORE INTO crm_recognition_email_log
               (event_key, email_type, period_label, recipients, sent_at)
               VALUES (?, ?, ?, ?, ?)""",
            (event_key, email_type, period_label, ",".join(recipients), utc_now()),
        )


def _send_once(event_key, email_type, period_label, subject, body, _event_recipients, inline_media=None):
    # Recognition messages always go to the CRM leadership group.  Every active
    # member of the Shopify/CRM roster is copied dynamically, so newly-added
    # team members are included without changing this scheduler.
    team_cc = list(_email_directory().values())
    recipients = list(dict.fromkeys(address.lower() for address in ALWAYS_TO if address))
    if not recipients or _already_sent(event_key):
        return False
    recipient_set = set(recipients)
    cc = list(dict.fromkeys(
        address.lower()
        for address in [*FIXED_CC, *team_cc]
        if address and address.lower() not in recipient_set
    ))
    send_html_email(
        subject, body, html.unescape(subject), recipients, cc=cc,
        inline_images={
            "wbn-logo": _logo_path(),
            ("perfect-attendance-animation" if email_type == "perfect_attendance" else ("target-achiever-animation" if email_type == "daily_target" else "celebration-animation")):
                (_perfect_attendance_animation_path() if email_type == "perfect_attendance" else (_target_achiever_animation_path() if email_type == "daily_target" else _celebration_animation_path())),
            **(inline_media or {}),
        },
        require_graph=True,
    )
    _record_sent(event_key, email_type, period_label, recipients)
    print(f"SENT {event_key} -> {','.join(recipients)}")
    return True


def _emails_for_rows(rows):
    directory = _email_directory()
    return [directory.get(_normalize(row["name"])) for row in rows]


def send_ranking(kind, start, end):
    message = ranking_email(kind, start, end)
    recipients = _emails_for_rows(message["rows"])
    event_key = f"top3:{kind}:{start.isoformat()}:{end.isoformat()}"
    return _send_once(
        event_key, f"top3_{kind}", f"{start.isoformat()}:{end.isoformat()}",
        message["subject"], message["body"], recipients,
        inline_media=message.get("inline_media"),
    )


def _is_working_day(day):
    return day.weekday() < 5


def _first_working_day(year, month):
    day = date(year, month, 1)
    while not _is_working_day(day):
        day += timedelta(days=1)
    return day


def send_perfect_attendance(today):
    month_start = date(today.year, today.month, 1)
    prior_end = month_start - timedelta(days=1)
    prior_start = date(prior_end.year, prior_end.month, 1)
    report = attendance_period(prior_start, prior_end, today=prior_end)
    qualifying = [
        row for row in report["rows"]
        if row["present"] > 0 and row["planned"] == 0 and row["unplanned"] == 0
    ]
    names = [row["employee_name"] for row in qualifying]
    recipients = _emails_for_rows([{"name": name} for name in names])
    if not recipients:
        return False
    label = prior_start.strftime("%B %Y")
    content = '<div style="padding:18px;border-radius:14px;background:#f6f9f2;border:1px solid #e1eadc">' + _name_chips(names) + '</div>'
    body = _email_shell(
        "Perfect Attendance Recognition", "ATTENDANCE MILESTONE",
        "A special appreciation for the team members who maintained a complete attendance record without taking leave during the month.",
        content, label, animation_cid="perfect-attendance-animation",
    )
    return _send_once(
        f"perfect-attendance:{prior_start:%Y-%m}", "perfect_attendance", label,
        f"Congratulations! 🎉 Perfect Attendance Recognition | {label}", body, recipients,
    )


def send_target_achievers(today):
    report = sales_report("daily", today)
    directory = _email_directory()
    sent = 0
    for row in report["rows"]:
        if not row.get("include_in_team") or row.get("attainment") is None or row["attainment"] < 1:
            continue
        recipient = directory.get(_normalize(row["display_name"]))
        if not recipient:
            continue
        content = f"""<div style="padding:20px;border-radius:14px;background:#f5f8f0;border-left:5px solid #b8db42">
          <strong style="font-size:20px;color:#123f33">{html.escape(row['display_name'])}</strong>
          <p style="margin:8px 0 0;color:#52675d">Sales <b>{_inr(row['total'])}</b> · Target <b>{_inr(row['total_target'])}</b> · Attainment <b>{row['attainment']*100:.1f}%</b></p>
        </div>"""
        body = _email_shell(
            "Daily Sales Target Achieved", "MILESTONE CELEBRATION",
            "Congratulations! You have reached your assigned sales target for today. Your focus and execution set a strong benchmark for the team.",
            content, today.strftime("%d %b %Y"), animation_cid="target-achiever-animation",
        )
        if _send_once(
            f"target:daily:{today.isoformat()}:{row['employee_key']}", "daily_target",
            today.isoformat(), f"Congratulations! 🎉 Daily Target Achieved | {row['display_name']} | {today:%d %b %Y}",
            body, [recipient],
        ):
            sent += 1
    return sent


def run(mode, now=None):
    _init_log()
    now = now or datetime.now(IST)
    today = now.date()
    if mode == "daily":
        if _is_working_day(today):
            yesterday = today - timedelta(days=1)
            send_ranking("daily", yesterday, yesterday)
        if today == _first_working_day(today.year, today.month):
            send_perfect_attendance(today)
    elif mode in {"weekly", "closing"}:
        # Retain compatibility with any old VM timer until deployment removes it,
        # but never send the discontinued weekly/monthly/yearly ranking emails.
        print(f"DISABLED recognition mode: {mode}")
    elif mode == "targets":
        send_target_achievers(today)


if __name__ == "__main__":
    load_dotenv(".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("daily", "weekly", "closing", "targets"))
    args = parser.parse_args()
    run(args.mode)
