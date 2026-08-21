import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database import DB_PATH


def main():
    backup_dir = Path("/var/backups/wbn-crm")
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"crm-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(DB_PATH) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    for file in backup_dir.glob("crm-*.db"):
        modified = datetime.fromtimestamp(file.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            file.unlink()
    print(target)


if __name__ == "__main__":
    main()
