import argparse
from datetime import date, timedelta

from dotenv import load_dotenv

from database import init_db
from report_services import sync_bik, sync_zoho


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Import Zoho and BIK reports into the CRM database")
    parser.add_argument("--date", help="IST report date in YYYY-MM-DD; defaults to yesterday")
    parser.add_argument("--source", choices=("all", "zoho", "bik"), default="all")
    args = parser.parse_args()
    report_date = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    day = report_date.isoformat()
    init_db()
    if args.source in ("all", "zoho"):
        print("Zoho:", sync_zoho(day, day))
    if args.source in ("all", "bik"):
        print("BIK customer tickets:", sync_bik("customer_tickets", day, day))
        print("BIK conversations:", sync_bik("all_conversations", day, day))


if __name__ == "__main__":
    main()
