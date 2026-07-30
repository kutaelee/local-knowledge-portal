"""Refresh documents written during nightly generation before feed publication."""

from __future__ import annotations

import json

from .embedding_reindex import run, wait_for_ingest_quiescence
from .service_runtime import service_pid


def refresh() -> tuple[dict, int]:
    ingest = wait_for_ingest_quiescence()
    result = {"ingest": ingest}
    if ingest["state"] != "quiescent":
        result["state"] = "ingest_timeout"
        return result, 1
    result["document_reindex"] = run()
    result["state"] = "succeeded"
    return result, 0


def main() -> int:
    with service_pid():
        result, exit_code = refresh()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
