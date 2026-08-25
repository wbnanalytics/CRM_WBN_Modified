import argparse
import html
import os
from collections import defaultdict
from datetime import date, timedelta

from dotenv import load_dotenv

from attendance_service import attendance_period
from database import list_shopify_employees, shopify_sales_between
from scorecard_mailer import send_html_email
from shopify_service import sales_report


IST_TEST_RECIPIENT = "varun.c@wellbeingnutrition.com"


def _inr(value):
    digits = str(int(round(float(value or 0))))
    if len(digits) <= 3:
        grouped = digits
    else:
        grouped = digits[-3:]
        head = digits[:-3]
        while head:
            grouped = head[-2:] + "," + grouped
            head = head[:-2]
    return f"₹{grouped}"


def _period_rankings(start, end):
    configs = {
        item["employee_key"]: item for item in list_shopify_employees()
        if item["active"] and item["include_in_team"]
    }
    totals = defaultdict(lambda: {"sales": 0.0, "orders": 0})
    for item in shopify_sales_between(start.isoformat(), end.isoformat()):
        if item["employee_key"] not in configs:
            continue
        totals[item["employee_key"]]["sales"] += float(item["total_sales"] or 0)
        totals[item["employee_key"]]["orders"] += int(item["order_count"] or 0)
    rows = []
    for key, config in configs.items():
        rows.append({
            "employee_key": key, "name": config["display_name"], "role": config["role"],
            "sales": totals[key]["sales"], "orders": totals[key]["orders"],
        })
    return sorted(rows, key=lambda row: (-row["sales"], row["name"].casefold()))


def _last_completed_week(today):
    days_since_friday = (today.weekday() - 4) % 7
    end = today - timedelta(days=days_since_friday or 7)
    return end - timedelta(days=6), end


def _ranking_rows(rows, limit=3):
    colors = ("#d9ef78", "#dcebe3", "#f2e8ce")
    result = []
    for index, row in enumerate(rows[:limit], 1):
        result.append(
            f'<tr><td style="padding:13px 8px;border-bottom:1px solid #e8ece5">'
            f'<span style="display:inline-block;width:30px;height:30px;line-height:30px;text-align:center;'
            f'border-radius:50%;background:{colors[index-1]};color:#123f33;font-weight:800">{index}</span></td>'
            f'<td style="padding:13px 8px;border-bottom:1px solid #e8ece5"><strong style="color:#14251f">{html.escape(row["name"])}</strong>'
            f'<br><span style="color:#77867f;font-size:12px">{html.escape(row.get("role") or "CRM Team")}</span></td>'
            f'<td style="padding:13px 8px;border-bottom:1px solid #e8ece5;text-align:right"><strong style="color:#123f33">{_inr(row["sales"])}</strong>'
            f'<br><span style="color:#77867f;font-size:12px">{row.get("orders", 0):,} orders</span></td></tr>'
        )
    return "".join(result)


def _name_chips(names):
    return "".join(
        f'<span style="display:inline-block;margin:4px;padding:8px 11px;border-radius:999px;background:#edf5e9;'
        f'border:1px solid #d7e5d6;color:#234d3e;font-weight:700;font-size:13px">✓ {html.escape(name)}</span>'
        for name in names
    )


def _signature():
    return """<div style="margin-top:28px;padding-top:20px;border-top:1px solid #e4e9e2">
      <p style="margin:0 0 12px;color:#263a31">Thanks &amp; Regards,</p>
      <table role="presentation" cellspacing="0" cellpadding="0"><tr>
        <td style="width:76px;vertical-align:middle"><img src="cid:wbn-logo" width="72" height="72" alt="Wellbeing Nutrition" style="display:block;width:72px;height:72px;object-fit:contain;border:0"></td>
        <td style="padding-left:14px"><strong style="font-size:16px;color:#123f33">Team CRM</strong><br>
          <span style="font-size:14px;color:#53675e">Wellbeing Nutrition</span>
        </td></tr></table></div>"""


def _email_shell(title, eyebrow, intro, content, period_label):
    return f"""<!doctype html><html><body style="margin:0;background:#f4f5ef;font-family:Arial,Helvetica,sans-serif;color:#263a31">
    <div style="display:none;max-height:0;overflow:hidden">A Team CRM celebration from Wellbeing Nutrition.</div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f5ef"><tr><td align="center" style="padding:28px 12px">
      <table role="presentation" width="680" cellspacing="0" cellpadding="0" style="max-width:680px;width:100%;background:#fff;border:1px solid #dfe6dc;border-radius:20px;overflow:hidden;box-shadow:0 12px 34px rgba(18,63,51,.10)">
        <tr><td style="background:#123f33;padding:32px 34px;color:#fff;position:relative">
          <div style="font-size:22px;letter-spacing:8px;color:#d9ef78;margin-bottom:12px">✦  ✧  ✦  ✧  ✦</div>
          <div style="font-size:11px;font-weight:800;letter-spacing:2px;color:#d9ef78;text-transform:uppercase">{html.escape(eyebrow)}</div>
          <h1 style="font-size:30px;line-height:1.18;margin:10px 0 8px;color:#fff">{html.escape(title)}</h1>
          <div style="display:inline-block;background:#d9ef78;color:#123f33;padding:7px 11px;border-radius:999px;font-size:12px;font-weight:800">{html.escape(period_label)}</div>
        </td></tr>
        <tr><td style="padding:30px 34px"><p style="font-size:16px;line-height:1.65;margin:0 0 22px;color:#40564c">{intro}</p>
          {content}{_signature()}
        </td></tr>
        <tr><td style="background:#edf3e9;padding:13px 34px;color:#6a7b73;font-size:11px;text-align:center">Internal recognition · Team CRM · Wellbeing Nutrition</td></tr>
      </table>
    </td></tr></table></body></html>"""


def ranking_email(kind, start, end, test=False):
    labels = {
        "daily": start.strftime("%d %b %Y"),
        "weekly": f"{start:%d %b} – {end:%d %b %Y}",
        "monthly": start.strftime("%B %Y"),
        "yearly": str(start.year),
    }
    titles = {
        "daily": "Yesterday’s Top 3 Sales Performers",
        "weekly": "Top 3 Performers of the Week",
        "monthly": "Top 3 Performers of the Month",
        "yearly": "Top 3 Performers of the Year",
    }
    subjects = {
        "daily": f"Congratulations! 🎉 Yesterday’s Top 3 Sales Performers | {labels['daily']}",
        "weekly": f"Congratulations! 🎉 Weekly Sales Champions | Week Ending {end:%d %b %Y}",
        "monthly": f"Congratulations! 🎉 Monthly Sales Champions | {labels['monthly']}",
        "yearly": f"Congratulations! 🎉 Annual Sales Champions | {labels['yearly']}",
    }
    rows = [row for row in _period_rankings(start, end) if row["sales"] > 0]
    content = f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0">{_ranking_rows(rows)}</table>'
    prefix = "[TEST – APPROVAL REQUIRED] " if test else ""
    return {
        "key": kind,
        "subject": prefix + subjects[kind],
        "body": _email_shell(
            titles[kind], "SALES RECOGNITION",
            "Congratulations to our outstanding performers. Your consistency, ownership, and customer focus made this achievement possible.",
            content, labels[kind],
        ),
        "rows": rows[:3],
    }


def build_samples(today=None):
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    week_start, week_end = _last_completed_week(today)
    month_start = date(today.year, today.month, 1)
    prior_month_end = month_start - timedelta(days=1)
    prior_month_start = date(prior_month_end.year, prior_month_end.month, 1)
    prior_year = today.year - 1
    periods = {
        "daily": (yesterday, yesterday),
        "weekly": (week_start, week_end),
        "monthly": (prior_month_start, prior_month_end),
        "yearly": (date(prior_year, 1, 1), date(prior_year, 12, 31)),
    }
    samples = []
    labels = {
        "daily": yesterday.strftime("%d %b %Y"),
        "weekly": f"{week_start:%d %b} – {week_end:%d %b %Y}",
        "monthly": prior_month_start.strftime("%B %Y"),
        "yearly": str(prior_year),
    }
    titles = {
        "daily": "Yesterday’s Top 3 Sales Performers",
        "weekly": "Top 3 Performers of the Week",
        "monthly": "Top 3 Performers of the Month",
        "yearly": "Top 3 Performers of the Year",
    }
    subjects = {
        "daily": f"Congratulations! 🎉 Yesterday’s Top 3 Sales Performers | {labels['daily']}",
        "weekly": f"Congratulations! 🎉 Weekly Sales Champions | Week Ending {week_end:%d %b %Y}",
        "monthly": f"Congratulations! 🎉 Monthly Sales Champions | {labels['monthly']}",
        "yearly": f"Congratulations! 🎉 Annual Sales Champions | {labels['yearly']}",
    }
    for key in ("daily", "weekly", "monthly", "yearly"):
        rows = _period_rankings(*periods[key])
        content = f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0">{_ranking_rows(rows)}</table>'
        body = _email_shell(
            titles[key], "SALES RECOGNITION",
            "Congratulations to our outstanding performers. Your consistency, ownership, and customer focus made this achievement possible.",
            content, labels[key],
        )
        samples.append({"key": key, "subject": "[TEST – APPROVAL REQUIRED] " + subjects[key], "body": body})

    attendance = attendance_period(prior_month_start, prior_month_end)
    no_leave = [row["employee_name"] for row in attendance["rows"] if row["present"] > 0 and row["planned"] == 0 and row["unplanned"] == 0]
    no_leave_content = (
        '<div style="padding:18px;border-radius:14px;background:#f6f9f2;border:1px solid #e1eadc">'
        + (_name_chips(no_leave) if no_leave else '<span style="color:#6c7b74">No qualifying employees in the selected completed month.</span>') + '</div>'
    )
    samples.append({
        "key": "perfect_attendance",
        "subject": f"[TEST – APPROVAL REQUIRED] Congratulations! 🎉 Perfect Attendance Recognition | {labels['monthly']}",
        "body": _email_shell("Perfect Attendance Recognition", "ATTENDANCE MILESTONE",
            "A special appreciation for the team members who maintained a complete attendance record without taking leave during the month.",
            no_leave_content, labels["monthly"]),
    })

    daily_report = sales_report("daily", yesterday)
    monthly_report = sales_report("monthly", prior_month_start)
    achieved = []
    seen = set()
    for period_name, report in (("Daily", daily_report), ("Monthly", monthly_report)):
        for row in report["rows"]:
            if row.get("include_in_team") and row.get("attainment") is not None and row["attainment"] >= 1:
                token = (row["display_name"], period_name)
                if token not in seen:
                    achieved.append({"name": row["display_name"], "period": period_name, "attainment": row["attainment"]})
                    seen.add(token)
    achieved_content = "".join(
        f'<div style="margin:8px 0;padding:14px 16px;border-left:4px solid #b8db42;background:#f5f8f0;border-radius:10px">'
        f'<strong style="color:#123f33">{html.escape(row["name"])}</strong><span style="float:right;color:#287248;font-weight:800">{row["attainment"]*100:.1f}%</span>'
        f'<br><span style="font-size:12px;color:#6f8077">{row["period"]} target achieved</span></div>' for row in achieved
    ) or '<p style="padding:16px;background:#f6f9f2;border-radius:12px">No target achievers in the completed sample periods.</p>'
    samples.append({
        "key": "target_achievers",
        "subject": "[TEST – APPROVAL REQUIRED] Congratulations! 🎉 Target Achievers | Daily & Monthly Recognition",
        "body": _email_shell("Target Achievers", "MILESTONE CELEBRATION",
            "Congratulations to the colleagues who met or exceeded their assigned sales target. Your focus and execution set a strong benchmark for the team.",
            achieved_content, f"Daily {yesterday:%d %b} · Monthly {labels['monthly']}"),
    })
    return samples


def send_test_samples(recipient):
    if recipient.casefold() != IST_TEST_RECIPIENT:
        raise ValueError("Approval samples are hard-locked to varun.c@wellbeingnutrition.com")
    samples = build_samples()
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "wbn-logo.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.getcwd(), "static", "wbn-logo.png")
    for sample in samples:
        plain = html.unescape(sample["subject"]).replace("[TEST – APPROVAL REQUIRED] ", "")
        send_html_email(
            sample["subject"], sample["body"], plain, [recipient], cc=[],
            inline_images={"wbn-logo": logo_path},
        )
        print(f'SENT {sample["key"]} -> {recipient}')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send approval-only CRM recognition email samples")
    parser.add_argument("--send-tests", metavar="EMAIL")
    args = parser.parse_args()
    load_dotenv(".env")
    if not args.send_tests:
        parser.error("Use --send-tests varun.c@wellbeingnutrition.com")
    send_test_samples(args.send_tests)
