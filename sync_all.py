import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from database import claim_sync_request, finish_sync_request, init_db
from shopify_service import sync_shopify, sync_shopify_customer_history


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
            results = {}
            errors = []
            order_detail_start = min(today.replace(day=1), today - timedelta(days=31))
            try:
                results["shopify"] = {
                    "sales": sync_shopify(
                        today - timedelta(days=44), today,
                        order_detail_start=order_detail_start,
                    ),
                    "customers": sync_shopify_customer_history(order_detail_start, today),
                }
            except Exception as exc:
                errors.append(f"shopify: {exc}")

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
