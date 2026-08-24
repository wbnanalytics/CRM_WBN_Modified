import argparse
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime

from dotenv import load_dotenv

from database import init_db
from shopify_service import sync_shopify


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Import employee-attributed Shopify sales")
    parser.add_argument("--start", help="First IST date in YYYY-MM-DD")
    parser.add_argument("--end", help="Last IST date in YYYY-MM-DD")
    parser.add_argument("--rolling-days", type=int, default=45)
    args = parser.parse_args()
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end must be supplied together")
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    if args.start:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    else:
        end = today
        start = today - timedelta(days=max(args.rolling_days - 1, 0))
    init_db()
    print("Shopify employee sales:", sync_shopify(start, end))


if __name__ == "__main__":
    main()
