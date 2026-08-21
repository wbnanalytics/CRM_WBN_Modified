import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from dotenv import load_dotenv

from database import init_db
from report_services import sync_bik


def weekly_ranges(start, end):
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=6), end)
        yield current.isoformat(), chunk_end.isoformat()
        current = chunk_end + timedelta(days=1)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Backfill BIK conversations in API-safe 7-day windows")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if start > end:
        parser.error("--start must be on or before --end")
    init_db()
    ranges = list(weekly_ranges(start, end))
    failures = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as pool:
        futures = {pool.submit(sync_bik, "all_conversations", first, last): (first, last) for first, last in ranges}
        for future in as_completed(futures):
            first, last = futures[future]
            try:
                result = future.result()
                print(f"{first}..{last}: {result['rows_saved']} rows", flush=True)
            except Exception as exc:
                failures.append((first, last, str(exc)))
                print(f"{first}..{last}: FAILED: {exc}", flush=True)
    if failures:
        raise SystemExit(f"{len(failures)} of {len(ranges)} ranges failed")
    print(f"Completed {len(ranges)} weekly ranges", flush=True)


if __name__ == "__main__":
    main()
