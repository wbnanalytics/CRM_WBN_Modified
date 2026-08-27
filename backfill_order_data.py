import argparse
import time
from datetime import date, timedelta

from dotenv import load_dotenv

from shopify_service import sync_shopify_order_history
from uniware_service import sync_uniware


def run_with_retries(label, operation, attempts=4):
    error = None
    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            print(f"{label}: complete {result}", flush=True)
            return
        except Exception as exc:
            error = exc
            print(f"{label}: attempt {attempt} failed: {exc}", flush=True)
            if attempt < attempts:
                time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"{label} failed after {attempts} attempts: {error}")


def main():
    parser = argparse.ArgumentParser(description="Backfill read-only Shopify order detail and Uniware outcomes.")
    parser.add_argument("start")
    parser.add_argument("end")
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start > end:
        raise SystemExit("start must not be after end")
    load_dotenv()
    cursor = start
    while cursor <= end:
        day = cursor.isoformat()
        run_with_retries(f"Shopify {day}", lambda day=day: sync_shopify_order_history(day, day))
        run_with_retries(f"Uniware {day}", lambda day=day: sync_uniware(day, day))
        cursor += timedelta(days=1)


if __name__ == "__main__":
    main()
