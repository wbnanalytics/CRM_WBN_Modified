import argparse
import time
from datetime import date, timedelta

from dotenv import load_dotenv

from database import init_db
from report_services import sync_bik, sync_zoho


def with_retries(operation, *args, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            return operation(*args)
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(5 * attempt)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Import Zoho and BIK reports into the CRM database")
    parser.add_argument("--date", help="IST report date in YYYY-MM-DD; defaults to yesterday")
    parser.add_argument("--start", help="First IST report date in YYYY-MM-DD")
    parser.add_argument("--end", help="Last IST report date in YYYY-MM-DD")
    parser.add_argument("--today", action="store_true", help="Import the current IST day up to now")
    parser.add_argument("--source", choices=("all", "zoho", "bik"), default="all")
    args = parser.parse_args()
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end must be supplied together")
    if args.start:
        range_start, range_end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    else:
        report_date = date.today() if args.today else (date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1))
        range_start = range_end = report_date
    if range_start > range_end:
        parser.error("--start must be on or before --end")
    start_day, end_day = range_start.isoformat(), range_end.isoformat()
    init_db()
    if args.source in ("all", "zoho"):
        print("Zoho:", sync_zoho(start_day, end_day))
    if args.source in ("all", "bik"):
        print("BIK customer tickets:", with_retries(sync_bik, "customer_tickets", start_day, end_day))
        print("BIK conversations:", with_retries(sync_bik, "all_conversations", start_day, end_day))


if __name__ == "__main__":
    main()
