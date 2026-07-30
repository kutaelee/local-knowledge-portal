"""Share one generation model load across nightly curation and project articles."""

from __future__ import annotations

import json

from lkp.db import SessionLocal
from lkp.settings import get_settings

from .generation import build_generation_provider
from .knowledge_curator import run_once as run_curation
from .project_article import refresh_all_project_articles
from .service_runtime import assert_mount_guards, service_pid


def run() -> tuple[dict, int]:
    settings = get_settings()
    assert_mount_guards(settings)
    provider = build_generation_provider(settings)
    if provider is None:
        return {"state": "disabled", "curation": {}, "project_articles": []}, 0
    result: dict = {}
    failures = 0
    try:
        if settings.knowledge_curation_enabled:
            try:
                with SessionLocal() as session:
                    result["curation"] = run_curation(
                        session,
                        settings,
                        provider=provider,
                        respect_next_attempt=False,
                    )
                    session.commit()
            except Exception as exc:
                failures += 1
                result["curation"] = {
                    "state": "failed",
                    "error_type": type(exc).__name__,
                }
        else:
            result["curation"] = {"state": "disabled"}

        if settings.project_article_enabled:
            try:
                with SessionLocal() as session:
                    articles = refresh_all_project_articles(
                        session,
                        provider=provider,
                        settings=settings,
                    )
                result["project_articles"] = articles
                failures += sum(item.get("status") == "failed" for item in articles)
            except Exception as exc:
                failures += 1
                result["project_articles"] = [
                    {"status": "failed", "error_type": type(exc).__name__}
                ]
        else:
            result["project_articles"] = []
        metrics = getattr(provider, "performance_metrics", None)
        if metrics is not None:
            result["model_performance"] = metrics()
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            close()
    result["state"] = "succeeded" if failures == 0 else "completed_with_errors"
    result["failed_stages"] = failures
    return result, failures


def main() -> int:
    with service_pid():
        result, failures = run()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
