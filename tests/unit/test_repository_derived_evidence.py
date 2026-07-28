from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from lkp_indexer.repository_analysis.discovery import discover
from lkp_indexer.repository_derived_evidence import (
    prepare_tar_members,
    prepare_text_chunks,
)


def test_discovery_accepts_cp949_text_without_changing_raw_hash(tmp_path: Path) -> None:
    source = tmp_path / "legacy.properties"
    source.write_bytes("설정=값\n".encode("cp949"))

    result = discover(tmp_path, allowed_roots=[tmp_path])

    assert [item.relative_path for item in result.files] == ["legacy.properties"]
    assert result.skipped == []
    assert result.files[0].line_count == 1


def test_text_chunks_are_content_addressed_and_reconstruct_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "bundle.js"
    content = "const 값 = '검증';" * 1_000
    source.write_text(content, encoding="utf-8")
    output = tmp_path / "derived"

    destination, manifest = prepare_text_chunks(
        source,
        output,
        source_label="web/app.js",
        chunk_chars=4_096,
    )
    repeated_destination, repeated_manifest = prepare_text_chunks(
        source,
        output,
        source_label="web/app.js",
        chunk_chars=4_096,
    )

    assert repeated_destination == destination
    assert repeated_manifest == manifest
    assert len(manifest["chunks"]) > 1
    assert (
        "".join(
            (destination / item["file"]).read_text(encoding="utf-8")
            for item in manifest["chunks"]
        )
        == content
    )
    assert json.loads(
        (destination / "evidence-manifest.json").read_text(encoding="utf-8")
    )["source_sha256"] == manifest["source_sha256"]


def test_tar_members_are_extracted_without_path_traversal(tmp_path: Path) -> None:
    source = tmp_path / "legacy.properties"
    with tarfile.open(source, "w") as archive:
        payload = b"<root/>"
        member = tarfile.TarInfo("config/runtime.xml")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    destination, manifest = prepare_tar_members(
        source,
        tmp_path / "derived",
        source_label="legacy.properties",
    )

    assert (destination / "config" / "runtime.xml").read_bytes() == b"<root/>"
    assert manifest["members"][0]["file"] == "config/runtime.xml"


def test_tar_members_reject_path_traversal(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.properties"
    with tarfile.open(source, "w") as archive:
        payload = b"unsafe"
        member = tarfile.TarInfo("../outside.xml")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    with pytest.raises(ValueError, match="unsafe archive member"):
        prepare_tar_members(
            source,
            tmp_path / "derived",
            source_label="unsafe.properties",
        )

    assert not (tmp_path / "outside.xml").exists()
