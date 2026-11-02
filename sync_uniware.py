import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from database import init_db, recent_sync_runs
from uniware_service import sync_uniware, sync_uniware_updates


IST = ZoneInfo("Asia/Kolkata")


def required_checkpoint(now):
    """Return the most recent 09:00/21:00 IST Uniware checkpoint."""
    today = now.date()
    if now.timetz().replace(tzinfo=None) >= time(21, 0):
        return datetime.combine(today, time(21, 0), tzinfo=IST)
    if now.timetz().replace(tzinfo=None) >= time(9, 0):
        return datetime.combine(today, time(9, 0), tzinfo=IST)
    return datetime.combine(today - timedelta(days=1), time(21, 0), tzinfo=IST)


def succeeded_since(checkpoint):
    for row in recent_sync_runs(100):
        if row.get("source") != "uniware" or row.get("status") != "success":
            continue
        completed = row.get("completed_at") or row.get("created_at")
        if not completed:
            continue
        parsed = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
        if parsed.astimezone(IST) >= checkpoint:
            return True
    return False


def main():
    load_dotenv()
    init_db()
    now = datetime.now(IST)
    checkpoint = required_checkpoint(now)
    if succeeded_since(checkpoint):
        sync_uniware_updates(now - timedelta(hours=2), now)
        return
    rolling_days = max(1, int(os.getenv("UNIWARE_ROLLING_DAYS", "7")))
    sync_uniware(now.date() - timedelta(days=rolling_days - 1), now.date())
    sync_uniware_updates(now - timedelta(hours=2), now)


if __name__ == "__main__":
    main()
