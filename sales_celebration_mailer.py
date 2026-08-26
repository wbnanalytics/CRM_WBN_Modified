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


def _full_report_rows(rows):
    result = []
    total_sales = 0.0
    total_orders = 0
    for index, row in enumerate(rows, 1):
        total_sales += float(row.get("sales") or 0)
        total_orders += int(row.get("orders") or 0)
        shade = "#f7f9f5" if index % 2 == 0 else "#ffffff"
        result.append(
            f'<tr style="background:{shade}"><td style="padding:10px 8px;border-bottom:1px solid #e8ece5;color:#6f8077">{index}</td>'
            f'<td style="padding:10px 8px;border-bottom:1px solid #e8ece5"><strong style="color:#14251f">{html.escape(row["name"])}</strong>'
            f'<br><span style="color:#77867f;font-size:11px">{html.escape(row.get("role") or "CRM Team")}</span></td>'
            f'<td style="padding:10px 8px;border-bottom:1px solid #e8ece5;text-align:right;color:#52675d">{int(row.get("orders") or 0):,}</td>'
            f'<td style="padding:10px 8px;border-bottom:1px solid #e8ece5;text-align:right"><strong style="color:#123f33">{_inr(row.get("sales") or 0)}</strong></td></tr>'
        )
    result.append(
        f'<tr style="background:#edf5e9"><td style="padding:12px 8px" colspan="2"><strong style="color:#123f33">Team total</strong></td>'
        f'<td style="padding:12px 8px;text-align:right"><strong style="color:#123f33">{total_orders:,}</strong></td>'
        f'<td style="padding:12px 8px;text-align:right"><strong style="color:#123f33">{_inr(total_sales)}</strong></td></tr>'
    )
    return "".join(result)


def _full_report_section(title, period_label, rows, eyebrow="FULL TEAM REPORT"):
    return f'''<div style="margin-top:28px">
      <div style="margin-bottom:12px"><div style="font-size:11px;font-weight:800;letter-spacing:1.4px;color:#6e7f75;text-transform:uppercase">{html.escape(eyebrow)}</div>
      <h2 style="margin:5px 0 2px;font-size:21px;color:#123f33">{html.escape(title)}</h2>
      <div style="font-size:12px;color:#75847d">{html.escape(period_label)}</div></div>
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e1e8de;border-radius:12px;overflow:hidden">
        <tr style="background:#123f33;color:#fff"><th style="padding:10px 8px;text-align:left;font-size:11px">#</th><th style="padding:10px 8px;text-align:left;font-size:11px">EMPLOYEE</th><th style="padding:10px 8px;text-align:right;font-size:11px">ORDERS</th><th style="padding:10px 8px;text-align:right;font-size:11px">SALES</th></tr>
        {_full_report_rows(rows)}
      </table>
    </div>'''


def _celebration_media(content_id="celebration-animation"):
    return ""


def _celebration_animation_path():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "email-celebration.gif")
    return path if os.path.exists(path) else None


def _perfect_attendance_animation_path():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "email-perfect-attendance.gif")
    return path if os.path.exists(path) else None


def _target_achiever_animation_path():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "email-target-achiever.gif")
    return path if os.path.exists(path) else None


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


def _email_shell(title, eyebrow, intro, content, period_label, animation_cid="celebration-animation"):
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
        <tr><td style="padding:30px 34px">{_celebration_media(animation_cid)}<p style="font-size:16px;line-height:1.65;margin:0 0 22px;color:#40564c">{intro}</p>
          {content}{_signature()}
        </td></tr>
        <tr><td style="background:#edf3e9;padding:13px 34px;color:#6a7b73;font-size:11px;text-align:center">Internal recognition · Team CRM · Wellbeing Nutrition</td></tr>
      </table>
    </td></tr></table></body></html>"""


def ranking_email(kind, start, end, test=False):
    if kind != "daily":
        raise ValueError("Weekly, monthly, and yearly ranking emails are discontinued")
    label = start.strftime("%d %b %Y")
    title = "Yesterday’s Top 3 Sales Performers"
    subject = f"Congratulations! 🎉 Yesterday’s Top 3 Sales Performers | {label}"
    all_rows = _period_rankings(start, end)
    rows = [row for row in all_rows if row["sales"] > 0]
    content = f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0">{_ranking_rows(rows)}</table>'
    inline_media = {}
    if kind == "daily":
        month_start = date(end.year, end.month, 1)
        month_rows = _period_rankings(month_start, end)
        crm_daily = [row for row in all_rows if "nutrition" not in (row.get("role") or "").casefold()]
        nutrition_daily = [row for row in all_rows if "nutrition" in (row.get("role") or "").casefold()]
        crm_month = [row for row in month_rows if "nutrition" not in (row.get("role") or "").casefold()]
        nutrition_month = [row for row in month_rows if "nutrition" in (row.get("role") or "").casefold()]
        content = (
            content
            + _full_report_section("Team CRM · Yesterday’s sales", label, crm_daily, "TEAM CRM · SALES & CUSTOMER SUCCESS")
            + _full_report_section("WBN – Nutritionists · Yesterday’s sales", label, nutrition_daily, "WBN – NUTRITIONISTS")
            + _full_report_section(
                "Team CRM · Month-to-date sales",
                f"{month_start:%d %b} – {end:%d %b %Y}",
                crm_month,
                "TEAM CRM · SALES & CUSTOMER SUCCESS",
            )
            + _full_report_section(
                "WBN – Nutritionists · Month-to-date sales",
                f"{month_start:%d %b} – {end:%d %b %Y}",
                nutrition_month,
                "WBN – NUTRITIONISTS",
            )
        )
    prefix = "[TEST – APPROVAL REQUIRED] " if test else ""
    return {
        "key": kind,
        "subject": prefix + subject,
        "body": _email_shell(
            title, "SALES RECOGNITION",
            "Congratulations to our outstanding performers. Your consistency, ownership, and customer focus made this achievement possible.",
            content, label,
        ),
        "rows": rows[:3],
        "inline_media": inline_media,
    }


def build_samples(today=None):
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    month_start = date(today.year, today.month, 1)
    prior_month_end = month_start - timedelta(days=1)
    prior_month_start = date(prior_month_end.year, prior_month_end.month, 1)
    samples = []
    monthly_label = prior_month_start.strftime("%B %Y")
    daily_sample = ranking_email("daily", yesterday, yesterday, test=True)
    samples.append(daily_sample)

    attendance = attendance_period(prior_month_start, prior_month_end)
    no_leave = [row["employee_name"] for row in attendance["rows"] if row["present"] > 0 and row["planned"] == 0 and row["unplanned"] == 0]
    no_leave_content = (
        '<div style="padding:18px;border-radius:14px;background:#f6f9f2;border:1px solid #e1eadc">'
        + (_name_chips(no_leave) if no_leave else '<span style="color:#6c7b74">No qualifying employees in the selected completed month.</span>') + '</div>'
    )
    samples.append({
        "key": "perfect_attendance",
        "subject": f"[TEST – APPROVAL REQUIRED] Congratulations! 🎉 Perfect Attendance Recognition | {monthly_label}",
        "body": _email_shell("Perfect Attendance Recognition", "ATTENDANCE MILESTONE",
            "A special appreciation for the team members who maintained a complete attendance record without taking leave during the month.",
            no_leave_content, monthly_label, animation_cid="perfect-attendance-animation"),
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
            achieved_content, f"Daily {yesterday:%d %b} · Monthly {monthly_label}", animation_cid="target-achiever-animation"),
    })
    return samples


def send_test_samples(recipient, sample_key="all"):
    if recipient.casefold() != IST_TEST_RECIPIENT:
        raise ValueError("Approval samples are hard-locked to varun.c@wellbeingnutrition.com")
    samples = build_samples()
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "wbn-logo.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.getcwd(), "static", "wbn-logo.png")
    selected = samples if sample_key == "all" else [sample for sample in samples if sample["key"] == sample_key]
    if not selected:
        raise ValueError(f"Unknown sample: {sample_key}")
    for sample in selected:
        plain = html.unescape(sample["subject"]).replace("[TEST – APPROVAL REQUIRED] ", "")
        send_html_email(
            sample["subject"], sample["body"], plain, [recipient], cc=[],
            inline_images={"wbn-logo": logo_path},
            require_graph=True,
        )
        print(f'SENT {sample["key"]} -> {recipient}')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send approval-only CRM recognition email samples")
    parser.add_argument("--send-tests", metavar="EMAIL")
    parser.add_argument("--sample", choices=("daily", "perfect_attendance", "target_achievers", "all"), default="all")
    args = parser.parse_args()
    load_dotenv(".env")
    if not args.send_tests:
        parser.error("Use --send-tests varun.c@wellbeingnutrition.com")
    send_test_samples(args.send_tests, args.sample)
