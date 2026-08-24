import html
import os
import smtplib
import ssl
from email.message import EmailMessage

import requests


def _mail_setting(*names, default=None):
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return default


def _mail_config():
    user = _mail_setting("CRM_USERNAME", "crm_username", "SMTP_USER")
    password = _mail_setting("CRM_PASSWORD", "crm_password", "SMTP_PASS")
    sender = _mail_setting("CRM_FROM", "crm_from", "SMTP_FROM", default=user)
    return {
        "host": _mail_setting("CRM_SMTP_HOST", "crm_smtp_host", "SMTP_HOST", default="smtp.office365.com"),
        "port": int(_mail_setting("CRM_SMTP_PORT", "crm_smtp_port", "SMTP_PORT", default="587")),
        "user": user,
        "password": password,
        "sender": sender,
    }


def email_ready():
    config = _mail_config()
    smtp_ready = all(config[key] for key in ("host", "user", "password", "sender"))
    graph_ready = bool(
        config["sender"] and os.getenv("AZURE_TENANT_ID")
        and os.getenv("AZURE_CLIENT_ID") and os.getenv("AZURE_CLIENT_SECRET")
    )
    transport = _mail_setting("CRM_MAIL_TRANSPORT", "crm_mail_transport", default="graph" if graph_ready else "smtp").lower()
    return graph_ready if transport == "graph" else smtp_ready


def _send_graph(subject, body, plain_text, recipient, sender):
    token_response = requests.post(
        f"https://login.microsoftonline.com/{os.environ['AZURE_TENANT_ID']}/oauth2/v2.0/token",
        data={
            "client_id": os.environ["AZURE_CLIENT_ID"],
            "client_secret": os.environ["AZURE_CLIENT_SECRET"],
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    token_response.raise_for_status()
    access_token = token_response.json().get("access_token")
    if not access_token:
        raise RuntimeError("Microsoft Graph did not issue an access token")
    response = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body},
                "toRecipients": [{"emailAddress": {"address": recipient}}],
            },
            "saveToSentItems": True,
        },
        timeout=30,
    )
    if response.status_code != 202:
        try:
            detail = (response.json().get("error") or {}).get("message")
        except ValueError:
            detail = None
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"Microsoft Graph rejected the message ({response.status_code}){suffix}")


def _send_smtp(subject, body, plain_text, recipient, config):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["sender"]
    message["To"] = recipient
    message.set_content(plain_text)
    message.add_alternative(body, subtype="html")
    with smtplib.SMTP(config["host"], config["port"], timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(config["user"], config["password"])
        smtp.send_message(message)


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
    config = _mail_config()
    plain_text = f"{subject}\n\nOverall result: {total}\n\nRegards,\nWellbeing Nutrition"
    graph_ready = bool(
        config["sender"] and os.getenv("AZURE_TENANT_ID")
        and os.getenv("AZURE_CLIENT_ID") and os.getenv("AZURE_CLIENT_SECRET")
    )
    transport = _mail_setting("CRM_MAIL_TRANSPORT", "crm_mail_transport", default="graph" if graph_ready else "smtp").lower()
    if transport == "graph":
        if not graph_ready:
            raise RuntimeError("Microsoft Graph mail is selected but the Azure application credentials are incomplete")
        try:
            _send_graph(subject, body, plain_text, recipient, config["sender"])
            return
        except Exception as exc:
            raise RuntimeError(
                "Email could not be sent through Microsoft 365. Confirm that the Azure app has the "
                "Application Mail.Send permission with admin consent. " + str(exc)
            ) from exc
    if transport != "smtp":
        raise RuntimeError("CRM_MAIL_TRANSPORT must be graph or smtp")
    _send_smtp(subject, body, plain_text, recipient, config)
