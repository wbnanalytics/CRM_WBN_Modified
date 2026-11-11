import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from database import claim_sync_request, finish_sync_request, init_db
from report_services import sync_bik, sync_zoho
from shopify_service import sync_shopify, sync_shopify_customer_history
from sync_reports import with_retries
from uniware_service import sync_uniware, sync_uniware_updates


def acquire_file_lock(lock_file):
    try:
        import fcntl
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except ImportError:
        try:
            import msvcrt
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except (OSError, IOError):
            return False
    except (BlockingIOError, OSError):
        return False


def release_file_lock(lock_file):
    try:
        import fcntl
        fcntl.flock(lock_file, fcntl.LOCK_UN)
    except ImportError:
        try:
            import msvcrt
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        except (OSError, IOError):
            pass
    except (BlockingIOError, OSError):
        pass


def main():
    load_dotenv()
    init_db()
    data_dir = Path(os.getenv("CRM_DATA_DIR", Path(__file__).resolve().parent / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "manual-sync.lock").open("a+") as lock:
        if not acquire_file_lock(lock):
            return
        request = None
        try:
            request = claim_sync_request()
            if not request:
                return
            today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
            day = today.isoformat()
            results = {}
            errors = []
            bik_email = request.get("requested_by") or os.getenv("BIK_REPORT_EMAIL") or os.getenv("crm_username")
            operations = (
                ("zoho", lambda: sync_zoho(day, day)),
                ("bik_customer_tickets", lambda: with_retries(sync_bik, "customer_tickets", day, day, bik_email)),
                ("bik_conversations", lambda: with_retries(sync_bik, "all_conversations", day, day, bik_email)),
                ("shopify", lambda: {
                    "sales": sync_shopify(
                        today - timedelta(days=44), today,
                        order_detail_start=today - timedelta(days=6),
                    ),
                    "customers": sync_shopify_customer_history(today - timedelta(days=6), today),
                }),
            )
            # These sources are independent. Running them together makes the
            # button wait for the slowest source instead of the sum of all four.
            with ThreadPoolExecutor(max_workers=len(operations)) as executor:
                pending = {executor.submit(operation): name for name, operation in operations}
                for future in as_completed(pending):
                    name = pending[future]
                    try:
                        results[name] = future.result()
                    except Exception as exc:
                        errors.append(f"{name}: {exc}")
            # Uniware reconciliation runs after Shopify so newly fetched parent
            # orders can be linked before fulfilment totals are published.
            try:
                results["uniware"] = {
                    "created": sync_uniware(today - timedelta(days=6), today),
                    "updated": sync_uniware_updates(
                        datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(hours=2),
                        datetime.now(ZoneInfo("Asia/Kolkata")),
                    ),
                }
            except Exception as exc:
                errors.append(f"uniware: {exc}")
            if errors:
                finish_sync_request(request["id"], "failed", results, "; ".join(errors)[:2000])
            else:
                finish_sync_request(request["id"], "success", results)
        except Exception as exc:
            if request:
                finish_sync_request(request["id"], "failed", locals().get("results", {}), str(exc)[:2000])
            raise
        finally:
            release_file_lock(lock)
            
if __name__ == "__main__":
    main()
