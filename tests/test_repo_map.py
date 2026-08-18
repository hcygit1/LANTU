from __future__ import annotations

from lantu.context.repo_map import build_repo_map


def test_repo_map_extracts_supported_symbols_deterministically(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text(
        "class Runner:\n    pass\n\ndef execute(task):\n    return task\n",
        encoding="utf-8",
    )
    (source / "worker.ts").write_text(
        "export interface Job {}\nexport function run(job: Job) {}\n",
        encoding="utf-8",
    )

    repo_map = build_repo_map(tmp_path, max_tokens=400)
    first = repo_map.snapshot
    second = repo_map.refresh()

    assert "src/main.py:1 class Runner" in first.text
    assert "src/main.py:4 function execute" in first.text
    assert "src/worker.ts:1 interface Job" in first.text
    assert "src/worker.ts:2 function run" in first.text
    assert second.text == first.text


def test_repo_map_respects_token_budget_and_reports_truncation(tmp_path) -> None:
    source = tmp_path / "many.py"
    source.write_text(
        "\n".join(f"def function_{index}(): pass" for index in range(100)),
        encoding="utf-8",
    )

    snapshot = build_repo_map(tmp_path, max_tokens=40).snapshot

    assert snapshot.estimated_tokens <= 40
    assert snapshot.truncated is True
    assert "map truncated" in snapshot.text


def test_repo_map_ignores_generated_and_internal_directories(tmp_path) -> None:
    (tmp_path / ".lantu").mkdir()
    (tmp_path / ".lantu" / "hidden.py").write_text(
        "def hidden(): pass", encoding="utf-8"
    )
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text(
        "function dependency() {}", encoding="utf-8"
    )
    (tmp_path / "app.py").write_text("def visible(): pass", encoding="utf-8")

    snapshot = build_repo_map(tmp_path).snapshot

    assert "visible" in snapshot.text
    assert "hidden" not in snapshot.text
    assert "dependency" not in snapshot.text
