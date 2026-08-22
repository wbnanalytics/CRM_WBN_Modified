import html
import os
import smtplib
import ssl
from email.message import EmailMessage


def email_ready():
    return all(os.getenv(key) for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM"))


def _score_rows(record):
    rows = []
    for item in record.get("rows", []):
        label = item.get("kpi") or item.get("criteria") or ""
        value = item.get("final_points") if "final_points" in item else item.get("rating")
        maximum = item.get("max_points") if "max_points" in item else 5
        rows.append(
            f"<tr><td>{html.escape(str(label))}</td><td style='text-align:right'>{html.escape(str(value if value is not None else '—'))}</td>"
            f"<td style='text-align:right'>{html.escape(str(maximum))}</td></tr>"
        )
    return "".join(rows)


def send_record_email(record_type, record, recipient):
    if not email_ready():
        raise RuntimeError("Email is not configured. Add the approved Microsoft 365 SMTP sender credentials first.")
    if not recipient.lower().endswith("@wellbeingnutrition.com"):
        raise ValueError("Scorecards can only be emailed to a wellbeingnutrition.com address")
    is_scorecard = record_type == "scorecard"
    subject = (
        f"Performance Scorecard — {record['employee_name']} — {record['evaluation_date']}"
        if is_scorecard else f"Performance Appraisal — {record['employee_name']} — {record['period_label']}"
    )
    total = (
        f"{record.get('total_points', 0):g} / {record.get('max_points', 0):g} ({record.get('score_pct', 0):g}%)"
        if is_scorecard else f"{record.get('total_rating_10') if record.get('total_rating_10') is not None else '—'} / 10"
    )
    body = f"""<!doctype html><html><body style="font-family:Arial,sans-serif;color:#17251f">
      <div style="max-width:720px;margin:auto;border:1px solid #e4e7df;border-radius:16px;overflow:hidden">
        <div style="background:#173f32;color:white;padding:24px"><h2 style="margin:0">Wellbeing Nutrition</h2>
        <p style="margin:8px 0 0">{'Performance Scorecard' if is_scorecard else 'Performance Appraisal'}</p></div>
        <div style="padding:24px"><p>Hello {html.escape(record['employee_name'])},</p>
        <p>Your {'scorecard' if is_scorecard else 'appraisal'} has been finalized. Please find the summary below.</p>
        <p><strong>Department:</strong> {html.escape(record['department'])}<br>
        <strong>{'Evaluation date' if is_scorecard else 'Review period'}:</strong> {html.escape(record['evaluation_date'] if is_scorecard else record['period_label'])}<br>
        <strong>Overall result:</strong> {html.escape(total)}</p>
        <table style="width:100%;border-collapse:collapse" cellpadding="8"><thead><tr style="background:#f5f4ed"><th style="text-align:left">Criteria</th><th style="text-align:right">Result</th><th style="text-align:right">Maximum</th></tr></thead>
        <tbody>{_score_rows(record)}</tbody></table>
        <p style="margin-top:22px">Regards,<br>Wellbeing Nutrition</p></div></div></body></html>"""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = recipient
    message.set_content(f"{subject}\n\nOverall result: {total}\n\nRegards,\nWellbeing Nutrition")
    message.add_alternative(body, subtype="html")
    host, port = os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        smtp.send_message(message)
