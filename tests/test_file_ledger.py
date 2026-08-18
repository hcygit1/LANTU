from __future__ import annotations

from pathlib import Path

from lantu.memory.file_ledger import FileLedger


def test_file_ledger_builds_hash_and_metadata(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    entry = FileLedger.build_entry(
        path,
        "alpha\nbeta\n",
        operation="read",
        offset=1,
        limit=1,
        mtime_ns=42,
    )

    assert entry.path == str(path.resolve())
    assert len(entry.content_hash) == 64
    assert entry.size == len("alpha\nbeta\n".encode("utf-8"))
    assert entry.line_count == 2
    assert entry.offset == 1
    assert entry.limit == 1
    assert entry.mtime_ns == 42


def test_file_ledger_keeps_latest_entry_and_replays_payload(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    ledger = FileLedger()
    first = FileLedger.build_entry(path, "old", operation="read")
    latest = FileLedger.build_entry(path, "new", operation="edit")

    ledger.apply(first)
    ledger.apply_payload(latest.to_payload())

    assert ledger.get(path) == latest
    assert ledger.as_payloads() == [latest.to_payload()]


def test_file_ledger_maps_observation_to_visible_ranges(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    ledger = FileLedger()
    entry = FileLedger.build_entry(
        path,
        "\n".join(f"line-{i}" for i in range(1, 201)),
        operation="read",
        offset=0,
        limit=200,
        tool_call_id="read-1",
    )

    ledger.apply(entry)
    observation = ledger.observation("read-1")

    assert observation is not None
    assert (observation.range_start, observation.range_end) == (1, 200)
    assert not ledger.is_visible(path, entry.content_hash, 1, 200)

    ledger.mark_visible(path, entry.content_hash, 1, 80)
    assert ledger.is_visible(path, entry.content_hash, 1, 80)
    assert not ledger.is_visible(path, entry.content_hash, 81, 100)


def test_file_ledger_invalidates_visible_ranges_for_new_file_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sample.py"
    ledger = FileLedger()
    old = FileLedger.build_entry(path, "old\n", operation="read")
    ledger.apply(old)
    ledger.mark_visible(path, old.content_hash, 1, 1)

    assert ledger.is_visible(path, old.content_hash, 1, 1)
    ledger.invalidate(path)
    assert not ledger.is_visible(path, old.content_hash, 1, 1)
