import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
import msal
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from database import bik_metrics, init_db, recent_sync_runs, zoho_metrics
from report_services import ZOHO_DEPARTMENTS


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "local-preview-only-change-me")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=os.getenv("APP_ENV") == "production")
init_db()

IST = ZoneInfo("Asia/Kolkata")
DATE_PRESETS = (
    ("today", "Today"), ("yesterday", "Yesterday"), ("last_7", "Last 7 days"),
    ("last_30", "Last 30 days"), ("last_60", "Last 60 days"),
    ("last_90", "Last 90 days"), ("this_year", "This year"),
    ("last_year", "Last year"), ("custom", "Custom range"),
)


REPORT_SOURCES = {
    "zoho": {
        "name": "Zoho Desk",
        "description": "Customer Experience and Escalation ticket metrics",
        "reports": ["Customer Experience", "Escalation Queue"],
        "required_keys": [
            "ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN",
            "ZOHO_ORG_ID", "ZOHO_ACCOUNTS_URL", "ZOHO_DESK_URL",
        ],
    },
    "bik": {
        "name": "BIK",
        "description": "Conversation and customer ticket performance",
        "reports": ["All Conversation Report", "Customer Ticket Report"],
        "required_keys": ["BIK_KEY", "BIK_SECRET", "BIK_APP_ID"],
    },
}


def connector_status(source):
    config = REPORT_SOURCES[source]
    configured = all(os.getenv(key) for key in config["required_keys"])
    return "configured" if configured else "needs_setup"


def selected_range():
    today = datetime.now(IST).date()
    preset = request.args.get("preset", "last_30")
    if preset == "today":
        start = end = today
    elif preset == "yesterday":
        start = end = today - timedelta(days=1)
    elif preset in {"last_7", "last_30", "last_60", "last_90"}:
        days = int(preset.split("_")[1])
        end, start = today, today - timedelta(days=days - 1)
    elif preset == "this_year":
        start, end = date(today.year, 1, 1), today
    elif preset == "last_year":
        start, end = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    elif preset == "custom":
        try:
            start = date.fromisoformat(request.args["start"])
            end = date.fromisoformat(request.args["end"])
        except (KeyError, ValueError):
            preset, end, start = "last_30", today, today - timedelta(days=29)
    else:
        preset, end, start = "last_30", today, today - timedelta(days=29)
    if start > end:
        start, end = end, start
    return start, end, preset


def microsoft_ready():
    return all(os.getenv(key) for key in ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID", "AZURE_REDIRECT_URI"))


def msal_app():
    return msal.ConfidentialClientApplication(os.environ["AZURE_CLIENT_ID"], authority=f"https://login.microsoftonline.com/{os.environ['AZURE_TENANT_ID']}", client_credential=os.environ["AZURE_CLIENT_SECRET"])


@app.before_request
def require_production_login():
    public = {"index", "microsoft_login", "microsoft_callback", "health", "static"}
    if os.getenv("APP_ENV") == "production" and request.endpoint not in public and not session.get("user"):
        return redirect(url_for("index"))


def get_zoho_access_token():
    response = requests.post(
        f"{os.getenv('ZOHO_ACCOUNTS_URL')}/oauth/v2/token",
        data={
            "refresh_token": os.getenv("ZOHO_REFRESH_TOKEN"),
            "client_id": os.getenv("ZOHO_CLIENT_ID"),
            "client_secret": os.getenv("ZOHO_CLIENT_SECRET"),
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def get_zoho_tickets(limit=100):
    response = requests.get(
        f"{os.getenv('ZOHO_DESK_URL')}/api/v1/tickets",
        headers={
            "Authorization": f"Zoho-oauthtoken {get_zoho_access_token()}",
            "orgId": os.getenv("ZOHO_ORG_ID"),
        },
        params={"limit": limit},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def test_bik_auth():
    response = requests.post(
        "https://bikapi.bikayi.app/integrations/bikPlatformFunctions-authPartnerViaKey",
        auth=(os.getenv("BIK_KEY"), os.getenv("BIK_SECRET")),
        json={"appId": os.getenv("BIK_APP_ID")},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


@app.context_processor
def inject_globals():
    return {"current_year": datetime.now(timezone.utc).year, "connector_status": connector_status}


@app.route("/")
def index():
    if session.get("preview_user") or session.get("user"):
        return redirect(url_for("dashboard"))
    return render_template("login.html", microsoft_ready=microsoft_ready())


@app.route("/auth/microsoft")
def microsoft_login():
    if not microsoft_ready():
        return redirect(url_for("index"))
    session["auth_state"] = os.urandom(24).hex()
    url = msal_app().get_authorization_request_url([], state=session["auth_state"], redirect_uri=os.environ["AZURE_REDIRECT_URI"], prompt="select_account")
    return redirect(url)


@app.route("/preview-login", methods=["POST"])
def preview_login():
    session["preview_user"] = "Customer Success Team"
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
def dashboard():
    start, end, preset = selected_range()
    zoho_data = zoho_metrics(start.isoformat(), end.isoformat())
    bik_data = bik_metrics(start.isoformat(), end.isoformat())
    total = zoho_data["total"] + bik_data["total"]
    resolved = zoho_data["resolved"] + bik_data["resolved"]
    return render_template("dashboard.html", active_page="dashboard", page_title="CS Metric Dashboard", sources=REPORT_SOURCES, zoho_metrics=zoho_data, bik_metrics=bik_data, total=total, resolved=resolved, resolution_rate=round(resolved / total * 100, 1) if total else 0, start=start, end=end, preset=preset, date_presets=DATE_PRESETS, sync_runs=recent_sync_runs(5))


@app.route("/zoho")
def zoho():
    start, end, preset = selected_range()
    department_key = request.args.get("department", "customer_experience")
    department = ZOHO_DEPARTMENTS.get(department_key, ZOHO_DEPARTMENTS["customer_experience"])
    metrics = zoho_metrics(start.isoformat(), end.isoformat(), department["id"])
    return render_template("source.html", active_page="zoho", page_title="Zoho Desk", source_key="zoho", source=REPORT_SOURCES["zoho"], metrics=metrics, departments=ZOHO_DEPARTMENTS, department_key=department_key, department=department, start=start, end=end, preset=preset, date_presets=DATE_PRESETS)


@app.route("/bik")
def bik():
    start, end, preset = selected_range()
    metrics = bik_metrics(start.isoformat(), end.isoformat())
    return render_template("source.html", active_page="bik", page_title="BIK", source_key="bik", source=REPORT_SOURCES["bik"], metrics=metrics, start=start, end=end, preset=preset, date_presets=DATE_PRESETS)


@app.route("/scorecard")
def scorecard():
    return render_template("coming_soon.html", active_page="scorecard", page_title="Scorecard")


@app.route("/api/status")
def api_status():
    return jsonify({
        "app": "ready",
        "zoho": connector_status("zoho"),
        "bik": connector_status("bik"),
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "recentSyncs": recent_sync_runs(5),
    })


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


@app.route("/api/metrics")
def api_metrics():
    start, end, preset = selected_range()
    department_key = request.args.get("department")
    department_id = ZOHO_DEPARTMENTS.get(department_key, {}).get("id") if department_key else None
    return jsonify({"range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset}, "zoho": zoho_metrics(start.isoformat(), end.isoformat(), department_id), "bik": bik_metrics(start.isoformat(), end.isoformat())})


@app.route("/auth/microsoft/callback")
def microsoft_callback():
    if request.args.get("error") or request.args.get("state") != session.get("auth_state"):
        return redirect(url_for("index"))
    if not microsoft_ready() or not request.args.get("code"):
        return redirect(url_for("index"))
    result = msal_app().acquire_token_by_authorization_code(request.args["code"], scopes=[], redirect_uri=os.environ["AZURE_REDIRECT_URI"])
    claims = result.get("id_token_claims") or {}
    email = (claims.get("preferred_username") or claims.get("email") or "").lower()
    if not email.endswith("@wellbeingnutrition.com"):
        session.clear()
        return redirect(url_for("index"))
    session.clear()
    session["user"] = {"name": claims.get("name") or email, "email": email}
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
