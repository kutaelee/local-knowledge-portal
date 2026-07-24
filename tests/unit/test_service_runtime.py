from types import SimpleNamespace

import pytest
from lkp_indexer.service_runtime import assert_mount_guards


def test_mount_guard_requires_files_and_nonempty_directories(tmp_path):
    marker = tmp_path / "runtime.marker"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    settings = SimpleNamespace(
        mount_guard_path_list=[marker],
        mount_guard_nonempty_dir_list=[sessions],
    )

    with pytest.raises(RuntimeError, match="mount guard failed"):
        assert_mount_guards(settings)

    marker.write_text("ready", encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty_or_unreadable"):
        assert_mount_guards(settings)

    (sessions / "session.jsonl").write_text("{}", encoding="utf-8")
    assert_mount_guards(settings)
