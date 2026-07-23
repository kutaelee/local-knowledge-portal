import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from lkp.db import SessionLocal
from lkp.models import BackupRun
from sqlalchemy import select


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    canonical = str(args.path.resolve())
    with SessionLocal() as session:
        row = session.scalar(
            select(BackupRun).where(BackupRun.backup_path == canonical)
        )
        if row is None:
            row = BackupRun(
                backup_path=canonical,
                status="succeeded",
                manifest=payload,
                finished_at=datetime.now(timezone.utc),
            )
            session.add(row)
        session.commit()
        print(row.id)


if __name__ == "__main__":
    main()
