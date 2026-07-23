import argparse
import json
from pathlib import Path

from watchfiles import watch


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that a bind-mounted path delivers watchfiles events."
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--polling", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=15000)
    args = parser.parse_args()

    for changes in watch(
        args.path,
        force_polling=args.polling,
        rust_timeout=args.timeout_ms,
        yield_on_timeout=True,
    ):
        if not changes:
            print(json.dumps({"status": "timeout", "path": str(args.path)}))
            return 2
        print(
            json.dumps(
                {
                    "status": "event",
                    "mode": "polling" if args.polling else "native",
                    "changes": [
                        {"change": change.name, "path": path}
                        for change, path in sorted(changes, key=lambda item: item[1])
                    ],
                }
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
