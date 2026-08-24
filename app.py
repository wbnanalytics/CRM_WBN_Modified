import os
import secrets
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import msal
from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from database import (
    PRIMARY_ADMIN_EMAIL, bik_conversation_metrics, bik_metrics, get_portal_permissions, get_portal_user, init_db,
    latest_sync_request, list_portal_users, queue_sync_request, recent_sync_runs,
    save_portal_user, set_portal_permissions, zoho_metrics,
)
from report_services import ZOHO_DEPARTMENTS
import scorecard_service as scorecards
from scorecard_export import appraisal_excel, scorecard_excel
from scorecard_mailer import email_ready as scorecard_email_ready, send_record_email
from shopify_service import sales_report as shopify_sales_report, seed_default_employees, shopify_configured


load_dotenv()
for env_key, secret_file in {
    "AZURE_CLIENT_ID": "azure-client-id",
    "AZURE_CLIENT_SECRET": "azure-client-secret",
    "AZURE_TENANT_ID": "azure-tenant-id",
}.items():
    secret_path = Path(__file__).resolve().parent / ".local-secrets" / secret_file
    if not os.getenv(env_key) and secret_path.exists():
        os.environ[env_key] = secret_path.read_text(encoding="utf-8").strip()

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

PORTAL_PERMISSIONS = (
    ("dashboard", "Dashboard", "Combined CS Metric dashboard"),
    ("zoho.customer_experience", "Zoho · Customer Experience", "Customer Experience tickets"),
    ("zoho.customer_escalation", "Zoho · Customer Escalation", "Escalation queue tickets"),
    ("bik.customer_tickets", "BIK · Customer Tickets", "Ticket performance and service metrics"),
    ("bik.all_conversations", "BIK · All Conversations", "Conversation and message metrics"),
    ("shopify.view", "Shopify · Sales Reports", "Daily, weekly and monthly employee sales"),
    ("scorecard.view", "Scorecard · Dashboard", "Scorecard overview and performance trends"),
    ("scorecard.agents", "Scorecard · Agents", "Create and maintain the agent directory"),
    ("scorecard.scorecards", "Scorecard · Scorecards", "Create, score and export monthly scorecards"),
    ("scorecard.templates", "Scorecard · KPI Templates", "Configure department KPI grids and zero-score rules"),
    ("scorecard.appraisals", "Scorecard · Appraisals", "Create and export annual appraisals"),
    ("scorecard.email", "Scorecard · Send Email", "Email finalized scorecards and appraisals"),
)
VALID_PERMISSION_KEYS = {item[0] for item in PORTAL_PERMISSIONS}


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
    "shopify": {
        "name": "Shopify Sales",
        "description": "Employee-attributed daily, weekly and monthly sales",
        "reports": ["Daily Sales", "Weekly Sales", "Monthly Sales"],
        "required_keys": ["SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET", "SHOPIFY_STORE"],
    },
}


def connector_status(source):
    if source == "shopify":
        return "configured" if shopify_configured() else "needs_setup"
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
    if session.get("user") and request.endpoint not in public:
        access = get_portal_user(session["user"].get("email"))
        if not access or not access["active"]:
            session.clear()
            flash("Your account does not have access to this portal.", "error")
            return redirect(url_for("index"))
        session["user"]["role"] = access["role"]
        session["user"]["permissions"] = get_portal_permissions(access["email"])


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user", {}).get("role") != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def is_super_admin():
    return session.get("user", {}).get("email") == PRIMARY_ADMIN_EMAIL


def can_access(permission_key):
    user = session.get("user", {})
    permissions = user.get("permissions", [])
    return user.get("role") == "admin" or permission_key in permissions or (
        permission_key.startswith("scorecard.") and "scorecard" in permissions
    )


def can_access_scorecard():
    return any(can_access(key) for key, _, _ in PORTAL_PERMISSIONS if key.startswith("scorecard."))


def permission_required(permission_key):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not can_access(permission_key):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def csrf_token():
    if not session.get("csrf_token"):
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def require_csrf():
    if not secrets.compare_digest(request.form.get("csrf_token", ""), session.get("csrf_token", "")):
        abort(400)


def require_json_csrf():
    supplied = request.headers.get("X-CSRF-Token", "")
    if not secrets.compare_digest(supplied, session.get("csrf_token", "")):
        abort(400)


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
    return {
        "current_year": datetime.now(timezone.utc).year,
        "connector_status": connector_status,
        "is_admin": session.get("user", {}).get("role") == "admin",
        "is_super_admin": is_super_admin(),
        "can_access": can_access,
        "can_access_scorecard": can_access_scorecard,
        "csrf_token": csrf_token,
    }


@app.template_filter("inr")
def indian_currency(value):
    number = int(round(float(value or 0)))
    sign = "-" if number < 0 else ""
    digits = str(abs(number))
    if len(digits) > 3:
        tail = digits[-3:]
        head = digits[:-3]
        groups = []
        while head:
            groups.append(head[-2:])
            head = head[:-2]
        digits = ",".join(reversed(groups)) + "," + tail
    return f"{sign}₹{digits}"


@app.route("/")
def index():
    if session.get("preview_user") or session.get("user"):
        if can_access("dashboard"):
            return redirect(url_for("dashboard"))
        if can_access("zoho.customer_experience") or can_access("zoho.customer_escalation"):
            return redirect(url_for("zoho"))
        if can_access("bik.customer_tickets") or can_access("bik.all_conversations"):
            return redirect(url_for("bik"))
        if can_access("shopify.view"):
            return redirect(url_for("shopify"))
        if can_access_scorecard():
            return redirect(url_for("scorecard"))
        if session.get("user", {}).get("role") == "admin":
            return redirect(url_for("manage_users"))
        session.clear()
        flash("No modules have been assigned to your account.", "error")
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
    session.clear()
    session["user"] = {"name": "Varun", "email": PRIMARY_ADMIN_EMAIL, "role": "admin", "permissions": []}
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
@permission_required("dashboard")
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
    if not can_access(f"zoho.{department_key}"):
        allowed = next((key.split(".", 1)[1] for key in session.get("user", {}).get("permissions", []) if key.startswith("zoho.")), None)
        if session.get("user", {}).get("role") != "admin" and not allowed:
            abort(403)
        if allowed:
            department_key = allowed
    department = ZOHO_DEPARTMENTS.get(department_key, ZOHO_DEPARTMENTS["customer_experience"])
    metrics = zoho_metrics(start.isoformat(), end.isoformat(), department["id"])
    return render_template("source.html", active_page="zoho", page_title="Zoho Desk", source_key="zoho", source=REPORT_SOURCES["zoho"], metrics=metrics, departments=ZOHO_DEPARTMENTS, department_key=department_key, department=department, start=start, end=end, preset=preset, date_presets=DATE_PRESETS)


@app.route("/bik")
def bik():
    if not (can_access("bik.customer_tickets") or can_access("bik.all_conversations")):
        abort(403)
    start, end, preset = selected_range()
    allowed_reports = [key for key in ("customer_tickets", "all_conversations") if can_access(f"bik.{key}")]
    report_key = request.args.get("report", allowed_reports[0])
    if report_key not in allowed_reports:
        report_key = allowed_reports[0]
    metrics = bik_metrics(start.isoformat(), end.isoformat()) if report_key == "customer_tickets" else bik_conversation_metrics(start.isoformat(), end.isoformat())
    return render_template("source.html", active_page="bik", page_title="BIK", source_key="bik", source=REPORT_SOURCES["bik"], metrics=metrics, bik_report=report_key, allowed_bik_reports=allowed_reports, start=start, end=end, preset=preset, date_presets=DATE_PRESETS)


@app.route("/shopify")
@permission_required("shopify.view")
def shopify():
    seed_default_employees()
    period = request.args.get("period", "daily")
    if period not in {"daily", "weekly", "monthly"}:
        period = "daily"
    today = datetime.now(IST).date()
    try:
        if period == "monthly":
            year = int(request.args.get("year", today.year))
            anchor = date(year, 1, 1)
        else:
            month = request.args.get("month", today.strftime("%Y-%m"))
            anchor = date.fromisoformat(f"{month}-01")
    except (TypeError, ValueError):
        anchor = date(today.year, today.month, 1)
    report = shopify_sales_report(period, anchor)
    return render_template(
        "shopify.html", active_page="shopify", page_title="Shopify Sales",
        source=REPORT_SOURCES["shopify"], report=report, period=period,
        selected_month=anchor.strftime("%Y-%m"), selected_year=anchor.year,
        available_years=range(today.year, max(today.year - 3, 2024) - 1, -1),
    )


@app.route("/api/sync/all", methods=["POST"])
@permission_required("dashboard")
def request_full_sync():
    require_json_csrf()
    sync_request, created = queue_sync_request(session["user"]["email"])
    if created:
        if os.getenv("APP_ENV") == "production":
            trigger = Path(os.getenv("CRM_DATA_DIR", Path(__file__).resolve().parent / "data")) / "manual-sync.trigger"
            trigger.parent.mkdir(parents=True, exist_ok=True)
            trigger.touch()
        else:
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve().parent / "sync_all.py")],
                cwd=Path(__file__).resolve().parent,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
    return jsonify({
        "id": sync_request["id"], "status": sync_request["status"],
        "message": "Data refresh queued" if created else "A data refresh is already in progress",
    }), 202


@app.route("/api/sync/status")
@permission_required("dashboard")
def full_sync_status():
    return jsonify(latest_sync_request() or {"status": "idle"})


@app.route("/scorecard")
def scorecard():
    if not can_access_scorecard():
        abort(403)
    return render_template(
        "scorecard.html", active_page="scorecard", page_title="Scorecard",
        agents=scorecards.list_agents(), templates=scorecards.list_templates(),
        scorecard_rows=scorecards.list_scorecards(), appraisals=scorecards.list_appraisals(),
        dashboard=scorecards.dashboard_data(), email_configured=scorecard_email_ready(),
    )


def scorecard_access_required(permission):
    return permission_required(f"scorecard.{permission}")


@app.route("/api/scorecard/agents", methods=["GET", "POST"])
@scorecard_access_required("agents")
def scorecard_agents_api():
    if request.method == "GET":
        return jsonify(scorecards.list_agents())
    require_json_csrf()
    try:
        return jsonify({"id": scorecards.save_agent(request.get_json() or {}, session["user"]["email"])}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/scorecard/agents/<int:agent_id>", methods=["PUT", "DELETE"])
@scorecard_access_required("agents")
def scorecard_agent_api(agent_id):
    require_json_csrf()
    try:
        if request.method == "DELETE":
            scorecards.delete_agent(agent_id)
        else:
            scorecards.save_agent(request.get_json() or {}, session["user"]["email"], agent_id)
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/scorecard/agents/<int:agent_id>/toggle", methods=["POST"])
@scorecard_access_required("agents")
def scorecard_agent_toggle_api(agent_id):
    require_json_csrf()
    scorecards.toggle_agent(agent_id)
    return jsonify({"success": True})


@app.route("/api/scorecard/templates", methods=["GET", "POST"])
@scorecard_access_required("templates")
def scorecard_templates_api():
    if request.method == "GET":
        return jsonify(scorecards.list_templates())
    require_json_csrf()
    body = request.get_json() or {}
    try:
        template_id = scorecards.save_template(
            body.get("department"), body.get("name"), body.get("zero_score_conditions") or [],
            body.get("metrics") or [], session["user"]["email"],
        )
        return jsonify({"id": template_id}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/scorecard/templates/<path:department>", methods=["GET", "PUT"])
@scorecard_access_required("templates")
def scorecard_template_api(department):
    if request.method == "GET":
        template = scorecards.get_template(department)
        return (jsonify(template), 200) if template else (jsonify({"error": "Template not found"}), 404)
    require_json_csrf()
    body = request.get_json() or {}
    try:
        scorecards.save_template(
            department, body.get("name"), body.get("zero_score_conditions") or [],
            body.get("metrics") or [], session["user"]["email"],
        )
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/scorecard/scorecards", methods=["GET", "POST"])
@scorecard_access_required("scorecards")
def scorecard_records_api():
    if request.method == "GET":
        return jsonify(scorecards.list_scorecards())
    require_json_csrf()
    body = request.get_json() or {}
    try:
        record_id = scorecards.create_scorecard(
            (body.get("emp_id") or "").strip().upper(), body.get("evaluation_date"), session["user"]["email"],
        )
        return jsonify({"id": record_id}), 201
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/scorecard/scorecards/<int:record_id>", methods=["GET", "PUT", "DELETE"])
@scorecard_access_required("scorecards")
def scorecard_record_api(record_id):
    if request.method == "GET":
        record = scorecards.get_scorecard(record_id)
        return (jsonify(record), 200) if record else (jsonify({"error": "Scorecard not found"}), 404)
    require_json_csrf()
    try:
        if request.method == "DELETE":
            scorecards.delete_scorecard(record_id)
        else:
            scorecards.update_scorecard(record_id, request.get_json() or {})
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/scorecard/scorecards/<int:record_id>/excel")
@scorecard_access_required("scorecards")
def scorecard_record_excel(record_id):
    record = scorecards.get_scorecard(record_id)
    if not record:
        abort(404)
    return send_file(
        scorecard_excel(record), as_attachment=True,
        download_name=f"scorecard-{record['emp_id']}-{record['evaluation_date']}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/scorecard/appraisals", methods=["GET", "POST"])
@scorecard_access_required("appraisals")
def scorecard_appraisals_api():
    if request.method == "GET":
        return jsonify(scorecards.list_appraisals())
    require_json_csrf()
    body = request.get_json() or {}
    try:
        record_id = scorecards.create_appraisal(
            (body.get("emp_id") or "").strip().upper(), body.get("review_date"),
            body.get("period_label"), session["user"]["email"],
        )
        return jsonify({"id": record_id}), 201
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/scorecard/appraisals/<int:record_id>", methods=["GET", "PUT", "DELETE"])
@scorecard_access_required("appraisals")
def scorecard_appraisal_api(record_id):
    if request.method == "GET":
        record = scorecards.get_appraisal(record_id)
        return (jsonify(record), 200) if record else (jsonify({"error": "Appraisal not found"}), 404)
    require_json_csrf()
    try:
        if request.method == "DELETE":
            scorecards.delete_appraisal(record_id)
        else:
            scorecards.update_appraisal(record_id, request.get_json() or {})
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/scorecard/appraisals/<int:record_id>/excel")
@scorecard_access_required("appraisals")
def scorecard_appraisal_excel(record_id):
    record = scorecards.get_appraisal(record_id)
    if not record:
        abort(404)
    return send_file(
        appraisal_excel(record), as_attachment=True,
        download_name=f"appraisal-{record['emp_id']}-{record['review_date']}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/scorecard/<record_type>/<int:record_id>/email", methods=["POST"])
@scorecard_access_required("email")
def scorecard_email_api(record_type, record_id):
    if record_type not in {"scorecard", "appraisal"}:
        abort(404)
    require_json_csrf()
    record = scorecards.get_scorecard(record_id) if record_type == "scorecard" else scorecards.get_appraisal(record_id)
    if not record:
        abort(404)
    if record["status"] == "draft":
        return jsonify({"error": "Finalize the record before sending it"}), 400
    recipient = ((request.get_json() or {}).get("recipient") or record["employee_email"]).strip().lower()
    try:
        send_record_email(record_type, record, recipient)
        scorecards.log_email(record_type, record_id, recipient, session["user"]["email"], "sent")
        return jsonify({"success": True, "message": f"Email sent to {recipient}"})
    except Exception as exc:
        scorecards.log_email(record_type, record_id, recipient, session["user"]["email"], "failed", str(exc))
        return jsonify({"error": str(exc)}), 503


@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def manage_users():
    if request.method == "POST":
        require_csrf()
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "user")
        if not email.endswith("@wellbeingnutrition.com"):
            flash("Use a wellbeingnutrition.com email address.", "error")
        elif role == "admin" and not is_super_admin():
            flash("Only the Super Admin can create another Admin.", "error")
        else:
            save_portal_user(email, request.form.get("display_name"), role, True, session["user"]["email"])
            set_portal_permissions(email, request.form.getlist("permissions") if role == "user" else [], session["user"]["email"])
            flash(f"Access saved for {email}.", "success")
        return redirect(url_for("manage_users"))
    return render_template(
        "users.html", active_page="users", page_title="User Management",
        users=[dict(user, permissions=get_portal_permissions(user["email"])) for user in list_portal_users()],
        primary_admin=PRIMARY_ADMIN_EMAIL, permission_options=PORTAL_PERMISSIONS,
    )


@app.route("/admin/users/update", methods=["POST"])
@admin_required
def update_user():
    require_csrf()
    email = request.form.get("email", "").strip().lower()
    existing = get_portal_user(email)
    if not existing:
        abort(404)
    if email == PRIMARY_ADMIN_EMAIL:
        flash("The primary admin cannot be removed or demoted.", "error")
    elif existing["role"] == "admin" and not is_super_admin():
        abort(403)
    elif request.form.get("role", existing["role"]) == "admin" and not is_super_admin():
        flash("Only the Super Admin can grant Admin access.", "error")
    else:
        role = request.form.get("role", existing["role"])
        save_portal_user(
            email, existing.get("display_name"), role,
            request.form.get("active") == "1", session["user"]["email"],
        )
        set_portal_permissions(email, request.form.getlist("permissions") if role == "user" else [], session["user"]["email"])
        flash(f"Access updated for {email}.", "success")
    return redirect(url_for("manage_users"))


@app.route("/api/status")
@admin_required
def api_status():
    return jsonify({
        "app": "ready",
        "zoho": connector_status("zoho"),
        "bik": connector_status("bik"),
        "shopify": connector_status("shopify"),
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "recentSyncs": recent_sync_runs(5),
    })


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


@app.route("/api/metrics")
@permission_required("dashboard")
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
    access = get_portal_user(email)
    if not email.endswith("@wellbeingnutrition.com") or not access or not access["active"]:
        session.clear()
        flash("Your Microsoft account is not yet authorized for this portal.", "error")
        return redirect(url_for("index"))
    session.clear()
    permissions = get_portal_permissions(email)
    session["user"] = {"name": claims.get("name") or access.get("display_name") or email, "email": email, "role": access["role"], "permissions": permissions}
    if access["role"] == "admin" or "dashboard" in permissions:
        return redirect(url_for("dashboard"))
    if any(key.startswith("zoho.") for key in permissions):
        return redirect(url_for("zoho"))
    if any(key.startswith("bik.") for key in permissions):
        return redirect(url_for("bik"))
    if "shopify.view" in permissions:
        return redirect(url_for("shopify"))
    return redirect(url_for("scorecard"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
