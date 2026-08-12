import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .db import SessionLocal
from .models import BackupRun


def record_backup(backup_path: str, manifest_path: Path) -> str:
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    with SessionLocal() as session:
        row = session.scalar(
            select(BackupRun).where(BackupRun.backup_path == backup_path)
        )
        if row is None:
            row = BackupRun(
                backup_path=backup_path,
                status="succeeded",
                manifest=payload,
                finished_at=datetime.now(timezone.utc),
            )
            session.add(row)
        session.commit()
        return str(row.id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    print(record_backup(args.path, args.manifest))


if __name__ == "__main__":
    main()
