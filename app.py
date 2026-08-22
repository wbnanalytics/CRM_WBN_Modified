import os
import secrets
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo

import requests
import msal
from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from database import (
    PRIMARY_ADMIN_EMAIL, bik_conversation_metrics, bik_metrics, get_portal_permissions, get_portal_user, init_db,
    list_portal_users, recent_sync_runs, save_portal_user, set_portal_permissions, zoho_metrics,
)
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

PORTAL_PERMISSIONS = (
    ("dashboard", "Dashboard", "Combined CS Metric dashboard"),
    ("zoho.customer_experience", "Zoho · Customer Experience", "Customer Experience tickets"),
    ("zoho.customer_escalation", "Zoho · Customer Escalation", "Escalation queue tickets"),
    ("bik.customer_tickets", "BIK · Customer Tickets", "Ticket performance and service metrics"),
    ("bik.all_conversations", "BIK · All Conversations", "Conversation and message metrics"),
    ("scorecard", "Scorecard", "Performance scorecards when launched"),
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
    return user.get("role") == "admin" or permission_key in user.get("permissions", [])


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
        "csrf_token": csrf_token,
    }


@app.route("/")
def index():
    if session.get("preview_user") or session.get("user"):
        if can_access("dashboard"):
            return redirect(url_for("dashboard"))
        if can_access("zoho.customer_experience") or can_access("zoho.customer_escalation"):
            return redirect(url_for("zoho"))
        if can_access("bik.customer_tickets") or can_access("bik.all_conversations"):
            return redirect(url_for("bik"))
        if can_access("scorecard"):
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


@app.route("/scorecard")
@permission_required("scorecard")
def scorecard():
    return render_template("coming_soon.html", active_page="scorecard", page_title="Scorecard")


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
    return redirect(url_for("scorecard"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
