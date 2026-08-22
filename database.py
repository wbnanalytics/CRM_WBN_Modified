import json
import hashlib
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("CRM_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "crm.db"
PRIMARY_ADMIN_EMAIL = "varun.c@wellbeingnutrition.com"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def db_connection():
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    with db_connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                report_key TEXT NOT NULL,
                range_start TEXT NOT NULL,
                range_end TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                rows_received INTEGER NOT NULL DEFAULT 0,
                rows_saved INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                external_id TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS zoho_tickets (
                id TEXT PRIMARY KEY,
                ticket_number TEXT,
                department_id TEXT NOT NULL,
                department_name TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'Unknown',
                status TEXT,
                status_type TEXT,
                priority TEXT,
                created_time TEXT NOT NULL,
                created_local_date TEXT NOT NULL,
                closed_time TEXT,
                customer_response_time TEXT,
                assignee_id TEXT,
                team_id TEXT,
                subject TEXT,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bik_report_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_key TEXT NOT NULL,
                report_name TEXT NOT NULL,
                range_start TEXT NOT NULL,
                range_end TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                external_id TEXT,
                status TEXT NOT NULL,
                download_url TEXT,
                row_count INTEGER NOT NULL DEFAULT 0,
                response_json TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS bik_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_run_id INTEGER NOT NULL,
                report_key TEXT NOT NULL,
                source_row_number INTEGER NOT NULL,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (report_run_id) REFERENCES bik_report_runs(id) ON DELETE CASCADE,
                UNIQUE (report_run_id, source_row_number)
            );

            CREATE TABLE IF NOT EXISTS bik_tickets (
                record_hash TEXT PRIMARY KEY,
                ticket_id TEXT,
                local_date TEXT NOT NULL,
                channel TEXT,
                ticket_stage TEXT,
                agent_name TEXT,
                created_time TEXT,
                assigned_time TEXT,
                closed_time TEXT,
                closure_seconds INTEGER,
                first_response_seconds INTEGER,
                human_response_seconds INTEGER,
                csat_response TEXT,
                csat_sentiment TEXT,
                priority TEXT,
                reassignments INTEGER,
                reopens INTEGER,
                agent_messages INTEGER,
                customer_messages INTEGER,
                ticket_source TEXT,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bik_conversations (
                record_hash TEXT PRIMARY KEY,
                ticket_id TEXT,
                local_date TEXT NOT NULL,
                channel TEXT,
                sent_at TEXT,
                sender TEXT,
                agent_id TEXT,
                agent_name TEXT,
                ticket_stage TEXT,
                ticket_created_time TEXT,
                ticket_closed_time TEXT,
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS portal_users (
                email TEXT PRIMARY KEY,
                display_name TEXT,
                role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                active INTEGER NOT NULL DEFAULT 1,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS portal_permissions (
                email TEXT NOT NULL,
                permission_key TEXT NOT NULL,
                granted_by TEXT NOT NULL,
                granted_at TEXT NOT NULL,
                PRIMARY KEY (email, permission_key),
                FOREIGN KEY (email) REFERENCES portal_users(email) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_zoho_tickets_department_date
                ON zoho_tickets(department_id, created_local_date);
            CREATE INDEX IF NOT EXISTS idx_zoho_tickets_date_channel
                ON zoho_tickets(created_local_date, channel);
            CREATE INDEX IF NOT EXISTS idx_sync_runs_source_created
                ON sync_runs(source, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_bik_report_runs_key_created
                ON bik_report_runs(report_key, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_bik_tickets_date_channel
                ON bik_tickets(local_date, channel);
            CREATE INDEX IF NOT EXISTS idx_bik_conversations_date_channel
                ON bik_conversations(local_date, channel);
            """
        )
        now = utc_now()
        db.execute(
            """INSERT INTO portal_users
               (email, display_name, role, active, created_by, created_at, updated_at)
               VALUES (?, 'Varun', 'admin', 1, 'system', ?, ?)
               ON CONFLICT(email) DO UPDATE SET role='admin', active=1, updated_at=excluded.updated_at""",
            (PRIMARY_ADMIN_EMAIL, now, now),
        )
        hash_version = db.execute(
            "SELECT value FROM schema_meta WHERE key='bik_conversation_hash_version'"
        ).fetchone()
        if not hash_version:
            rows = db.execute(
                """SELECT ticket_id, local_date, channel, sent_at, sender, agent_id, agent_name,
                          ticket_stage, ticket_created_time, ticket_closed_time, synced_at
                   FROM bik_conversations"""
            ).fetchall()
            if rows:
                db.execute("""CREATE TABLE bik_conversations_v2 (
                    record_hash TEXT PRIMARY KEY, ticket_id TEXT, local_date TEXT NOT NULL,
                    channel TEXT, sent_at TEXT, sender TEXT, agent_id TEXT, agent_name TEXT,
                    ticket_stage TEXT, ticket_created_time TEXT, ticket_closed_time TEXT,
                    synced_at TEXT NOT NULL)""")
                values = []
                for row in rows:
                    stable = {key: row[key] for key in (
                        "ticket_id", "local_date", "channel", "sent_at", "sender",
                        "agent_id", "agent_name", "ticket_created_time",
                    )}
                    values.append((_row_hash(stable), *tuple(row)))
                db.executemany(
                    "INSERT OR REPLACE INTO bik_conversations_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                db.execute("DROP TABLE bik_conversations")
                db.execute("ALTER TABLE bik_conversations_v2 RENAME TO bik_conversations")
                db.execute("CREATE INDEX idx_bik_conversations_date_channel ON bik_conversations(local_date, channel)")
            db.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('bik_conversation_hash_version', '2')"
            )
        db.execute("PRAGMA optimize")


def get_portal_user(email):
    normalized = (email or "").strip().lower()
    if not normalized:
        return None
    with db_connection() as db:
        row = db.execute(
            "SELECT email, display_name, role, active, created_by, created_at, updated_at FROM portal_users WHERE email=?",
            (normalized,),
        ).fetchone()
    return dict(row) if row else None


def list_portal_users():
    with db_connection() as db:
        rows = db.execute(
            """SELECT email, display_name, role, active, created_by, created_at, updated_at
               FROM portal_users ORDER BY CASE WHEN email=? THEN 0 ELSE 1 END, role, email""",
            (PRIMARY_ADMIN_EMAIL,),
        ).fetchall()
    return [dict(row) for row in rows]


def save_portal_user(email, display_name, role, active, created_by):
    normalized = (email or "").strip().lower()
    if normalized == PRIMARY_ADMIN_EMAIL:
        role, active = "admin", True
    if role not in {"admin", "user"}:
        raise ValueError("Invalid portal role")
    now = utc_now()
    with db_connection() as db:
        db.execute(
            """INSERT INTO portal_users
               (email, display_name, role, active, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET
                 display_name=excluded.display_name, role=excluded.role,
                 active=excluded.active, updated_at=excluded.updated_at""",
            (normalized, (display_name or "").strip(), role, int(bool(active)), created_by, now, now),
        )
    return get_portal_user(normalized)


def get_portal_permissions(email):
    with db_connection() as db:
        rows = db.execute(
            "SELECT permission_key FROM portal_permissions WHERE email=? ORDER BY permission_key",
            ((email or "").strip().lower(),),
        ).fetchall()
    return [row["permission_key"] for row in rows]


def set_portal_permissions(email, permission_keys, granted_by):
    normalized = (email or "").strip().lower()
    now = utc_now()
    keys = sorted(set(permission_keys))
    with db_connection() as db:
        db.execute("DELETE FROM portal_permissions WHERE email=?", (normalized,))
        db.executemany(
            "INSERT INTO portal_permissions (email, permission_key, granted_by, granted_at) VALUES (?, ?, ?, ?)",
            [(normalized, key, granted_by, now) for key in keys],
        )
    return keys


def start_sync_run(source, report_key, start_date, end_date):
    with db_connection() as db:
        cursor = db.execute(
            """INSERT INTO sync_runs
               (source, report_key, range_start, range_end, status, created_at)
               VALUES (?, ?, ?, ?, 'running', ?)""",
            (source, report_key, start_date, end_date, utc_now()),
        )
        return cursor.lastrowid


def finish_sync_run(run_id, status, rows_received=0, rows_saved=0, error=None, external_id=None):
    with db_connection() as db:
        db.execute(
            """UPDATE sync_runs
               SET status=?, rows_received=?, rows_saved=?, error_message=?,
                   external_id=?, completed_at=?
               WHERE id=?""",
            (status, rows_received, rows_saved, error, external_id, utc_now(), run_id),
        )


def upsert_zoho_tickets(tickets):
    if not tickets:
        return 0
    now = utc_now()
    values = []
    for ticket in tickets:
        department = ticket.get("department") or {}
        values.append(
            (
                str(ticket["id"]), ticket.get("ticketNumber"),
                str(ticket.get("departmentId") or department.get("id") or ""),
                department.get("name") or "Unknown",
                ticket.get("channel") or ticket.get("channelCode") or "Unknown",
                ticket.get("status"), ticket.get("statusType"), ticket.get("priority"),
                ticket.get("createdTime"), ticket["createdLocalDate"], ticket.get("closedTime"),
                ticket.get("customerResponseTime"), ticket.get("assigneeId"), ticket.get("teamId"),
                ticket.get("subject"), json.dumps(ticket, ensure_ascii=False), now,
            )
        )
    with db_connection() as db:
        db.executemany(
            """INSERT INTO zoho_tickets (
                id, ticket_number, department_id, department_name, channel, status,
                status_type, priority, created_time, created_local_date, closed_time,
                customer_response_time, assignee_id, team_id, subject, raw_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                ticket_number=excluded.ticket_number,
                department_id=excluded.department_id,
                department_name=excluded.department_name,
                channel=excluded.channel,
                status=excluded.status,
                status_type=excluded.status_type,
                priority=excluded.priority,
                created_time=excluded.created_time,
                created_local_date=excluded.created_local_date,
                closed_time=excluded.closed_time,
                customer_response_time=excluded.customer_response_time,
                assignee_id=excluded.assignee_id,
                team_id=excluded.team_id,
                subject=excluded.subject,
                raw_json=excluded.raw_json,
                synced_at=excluded.synced_at""",
            values,
        )
    return len(values)


def zoho_metrics(start_date, end_date, department_id=None):
    where = "created_local_date BETWEEN ? AND ?"
    params = [start_date, end_date]
    if department_id:
        where += " AND department_id = ?"
        params.append(str(department_id))
    with db_connection() as db:
        summary = db.execute(
            f"""SELECT COUNT(*) AS total,
                       SUM(CASE WHEN lower(COALESCE(status_type,'')) IN ('closed','resolved')
                                     OR closed_time IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
                       SUM(CASE WHEN lower(COALESCE(status_type,'')) NOT IN ('closed','resolved')
                                     AND closed_time IS NULL THEN 1 ELSE 0 END) AS open
                FROM zoho_tickets WHERE {where}""",
            params,
        ).fetchone()
        channels = db.execute(
            f"""SELECT channel AS label, COUNT(*) AS value
                FROM zoho_tickets WHERE {where}
                GROUP BY channel ORDER BY value DESC, label""",
            params,
        ).fetchall()
        statuses = db.execute(
            f"""SELECT COALESCE(status, status_type, 'Unknown') AS label, COUNT(*) AS value
                FROM zoho_tickets WHERE {where}
                GROUP BY COALESCE(status, status_type, 'Unknown') ORDER BY value DESC, label""",
            params,
        ).fetchall()
        trend = db.execute(
            f"""SELECT created_local_date AS date, COUNT(*) AS tickets,
                       SUM(CASE WHEN lower(COALESCE(status_type,'')) IN ('closed','resolved')
                                     OR closed_time IS NOT NULL THEN 1 ELSE 0 END) AS resolved
                FROM zoho_tickets WHERE {where}
                GROUP BY created_local_date ORDER BY created_local_date""",
            params,
        ).fetchall()
        recent = db.execute(
            f"""SELECT ticket_number, subject, department_name, channel, status,
                       status_type, priority, created_time, closed_time
                FROM zoho_tickets WHERE {where}
                ORDER BY created_time DESC LIMIT 50""",
            params,
        ).fetchall()
    total = summary["total"] or 0
    resolved = summary["resolved"] or 0
    return {
        "total": total,
        "resolved": resolved,
        "open": summary["open"] or 0,
        "resolution_rate": round((resolved / total * 100), 1) if total else 0,
        "channels": [dict(row) for row in channels],
        "statuses": [dict(row) for row in statuses],
        "trend": [dict(row) for row in trend],
        "recent": [dict(row) for row in recent],
    }


def recent_sync_runs(limit=10):
    with db_connection() as db:
        rows = db.execute(
            """SELECT id, source, report_key, range_start, range_end, status,
                      rows_received, rows_saved, error_message, created_at, completed_at
               FROM sync_runs ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _row_hash(row):
    payload = json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_int(value):
    try:
        return int(float(value)) if str(value).strip() else 0
    except (TypeError, ValueError):
        return 0


def clear_bik_report_range(report_key, start_date, end_date):
    table = "bik_tickets" if report_key == "customer_tickets" else "bik_conversations"
    with db_connection() as db:
        db.execute(f"DELETE FROM {table} WHERE local_date BETWEEN ? AND ?", (start_date, end_date))


def _bik_local_date(value, fallback):
    if not value:
        return fallback
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", str(value).strip(), flags=re.IGNORECASE)
    for fmt in ("%d %b %Y, %H:%M:%S", "%d %b %Y, %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return fallback


def save_bik_tickets(rows, local_date, duration_parser, row_offset=0):
    now = utc_now()
    values = []
    for index, row in enumerate(rows, start=row_offset):
        row_date = _bik_local_date(row.get("Ticket created timestamp"), local_date)
        ticket_id = str(row.get("Ticket Id") or "").strip()
        record_key = {"ticket_id": ticket_id} if ticket_id else {"row": row}
        values.append((
            _row_hash(record_key), ticket_id or None, row_date, row.get("Channel"),
            row.get("Ticket stage"), row.get("Agent name"), row.get("Ticket created timestamp"),
            row.get("Ticket assigned timestamp"), row.get("Ticket close timestamp"),
            duration_parser(row.get("Ticket closure time")), duration_parser(row.get("First response time")),
            duration_parser(row.get("Time to First Human Response")), row.get("CSAT response"),
            row.get("CSAT sentiment"), row.get("Ticket priority"),
            _safe_int(row.get("Number of Reassignments")), _safe_int(row.get("Number of Reopens")),
            _safe_int(row.get("Number of Agent Messages")), _safe_int(row.get("Number of Customer Messages")),
            row.get("Ticket Source"), json.dumps(row, ensure_ascii=False), now,
        ))
    with db_connection() as db:
        db.executemany(
            """INSERT INTO bik_tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(record_hash) DO UPDATE SET
                 ticket_stage=excluded.ticket_stage, closed_time=excluded.closed_time,
                 closure_seconds=excluded.closure_seconds,
                 first_response_seconds=excluded.first_response_seconds,
                 human_response_seconds=excluded.human_response_seconds,
                 csat_response=excluded.csat_response, csat_sentiment=excluded.csat_sentiment,
                 raw_json=excluded.raw_json, synced_at=excluded.synced_at""",
            values,
        )
    return len(values)


def save_bik_conversations(rows, local_date, row_offset=0):
    now = utc_now()
    values = []
    for row in rows:
        row_date = _bik_local_date(row.get("Sent At") or row.get("Ticket Creation Timestamp"), local_date)
        stable = {
            "ticket_id": row.get("Ticket ID"), "local_date": row_date,
            "channel": row.get("Channel"), "sent_at": row.get("Sent At"),
            "sender": row.get("Sender"), "agent_id": row.get("Agent ID"),
            "agent_name": row.get("Agent Name"),
            "ticket_created_time": row.get("Ticket Creation Timestamp"),
        }
        values.append((
            _row_hash(stable), row.get("Ticket ID"), row_date, row.get("Channel"), row.get("Sent At"),
            row.get("Sender"), row.get("Agent ID"), row.get("Agent Name"), row.get("Ticket Stage"),
            row.get("Ticket Creation Timestamp"), row.get("Ticket Closed Timestamp"), now,
        ))
    with db_connection() as db:
        db.executemany(
            """INSERT INTO bik_conversations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(record_hash) DO UPDATE SET
                 ticket_stage=excluded.ticket_stage,
                 ticket_closed_time=excluded.ticket_closed_time,
                 synced_at=excluded.synced_at""",
            values,
        )
    return len(values)


def bik_metrics(start_date, end_date):
    params = (start_date, end_date)
    with db_connection() as db:
        summary = db.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN upper(COALESCE(ticket_stage,'')) IN
                        ('CLOSED','BULK_CLOSED','AUTO_CLOSED','CLOSE','CLOSE AS SPAM') THEN 1 ELSE 0 END) AS resolved,
                      AVG(CASE WHEN first_response_seconds > 0 THEN first_response_seconds END) AS avg_frt,
                      SUM(CASE WHEN lower(COALESCE(csat_sentiment,''))='positive' THEN 1 ELSE 0 END) AS positive_csat,
                      SUM(CASE WHEN COALESCE(csat_sentiment,'')<>'' THEN 1 ELSE 0 END) AS csat_responses
               FROM bik_tickets WHERE local_date BETWEEN ? AND ?""",
            params,
        ).fetchone()
        channels = db.execute(
            """SELECT COALESCE(channel,'Unknown') AS label, COUNT(*) AS value
               FROM bik_tickets WHERE local_date BETWEEN ? AND ?
               GROUP BY channel ORDER BY value DESC""",
            params,
        ).fetchall()
        stages = db.execute(
            """SELECT COALESCE(ticket_stage,'Unknown') AS label, COUNT(*) AS value
               FROM bik_tickets WHERE local_date BETWEEN ? AND ?
               GROUP BY ticket_stage ORDER BY value DESC""",
            params,
        ).fetchall()
        message_summary = db.execute(
            """SELECT COUNT(*) AS messages, COUNT(DISTINCT ticket_id) AS conversations
               FROM bik_conversations WHERE local_date BETWEEN ? AND ?""",
            params,
        ).fetchone()
        recent = db.execute(
            """SELECT ticket_id, channel, ticket_stage, agent_name, created_time,
                      closed_time, first_response_seconds, csat_sentiment
               FROM bik_tickets WHERE local_date BETWEEN ? AND ?
               ORDER BY created_time DESC LIMIT 50""",
            params,
        ).fetchall()
    total = summary["total"] or 0
    resolved = summary["resolved"] or 0
    csat_count = summary["csat_responses"] or 0
    return {
        "total": total, "resolved": resolved, "open": max(total - resolved, 0),
        "resolution_rate": round(resolved / total * 100, 1) if total else 0,
        "avg_frt_seconds": round(summary["avg_frt"] or 0),
        "csat_score": round((summary["positive_csat"] or 0) / csat_count * 100, 1) if csat_count else 0,
        "csat_responses": csat_count,
        "messages": message_summary["messages"] or 0,
        "conversations": message_summary["conversations"] or 0,
        "channels": [dict(row) for row in channels],
        "statuses": [dict(row) for row in stages],
        "recent": [dict(row) for row in recent],
    }


def bik_conversation_metrics(start_date, end_date):
    params = (start_date, end_date)
    with db_connection() as db:
        summary = db.execute(
            """SELECT COUNT(*) messages, COUNT(DISTINCT ticket_id) conversations,
                      COUNT(DISTINCT CASE WHEN COALESCE(agent_id,'')<>'' THEN agent_id END) agents
               FROM bik_conversations WHERE local_date BETWEEN ? AND ?""",
            params,
        ).fetchone()
        channels = db.execute(
            """SELECT COALESCE(channel,'Unknown') label, COUNT(*) value
               FROM bik_conversations WHERE local_date BETWEEN ? AND ?
               GROUP BY channel ORDER BY value DESC""",
            params,
        ).fetchall()
        stages = db.execute(
            """SELECT COALESCE(ticket_stage,'Unknown') label, COUNT(*) value
               FROM bik_conversations WHERE local_date BETWEEN ? AND ?
               GROUP BY ticket_stage ORDER BY value DESC""",
            params,
        ).fetchall()
    return {
        "total": summary["messages"] or 0, "messages": summary["messages"] or 0,
        "conversations": summary["conversations"] or 0, "agents": summary["agents"] or 0,
        "resolved": 0, "open": 0, "resolution_rate": 0, "csat_score": 0,
        "csat_responses": 0, "channels": [dict(row) for row in channels],
        "statuses": [dict(row) for row in stages], "recent": [],
    }
