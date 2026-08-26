import fcntl
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from database import claim_sync_request, finish_sync_request, init_db
from report_services import sync_bik, sync_zoho
from shopify_service import sync_shopify
from sync_reports import with_retries


def main():
    load_dotenv()
    init_db()
    data_dir = Path(os.getenv("CRM_DATA_DIR", Path(__file__).resolve().parent / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "manual-sync.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        request = claim_sync_request()
        if not request:
            return
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        day = today.isoformat()
        results = {}
        errors = []
        operations = (
            ("zoho", lambda: sync_zoho(day, day)),
            ("bik_customer_tickets", lambda: with_retries(sync_bik, "customer_tickets", day, day)),
            ("bik_conversations", lambda: with_retries(sync_bik, "all_conversations", day, day)),
            ("shopify", lambda: sync_shopify(
                today - timedelta(days=44), today,
                order_detail_start=today - timedelta(days=6),
            )),
        )
        for name, operation in operations:
            try:
                results[name] = operation()
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        if errors:
            finish_sync_request(request["id"], "failed", results, "; ".join(errors)[:2000])
        else:
            finish_sync_request(request["id"], "success", results)


if __name__ == "__main__":
    main()
