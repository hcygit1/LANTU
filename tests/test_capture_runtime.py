from pathlib import Path

import pytest

from lantu.tools.lens.capture_runtime import CaptureProxy, CaptureUnavailable


def test_capture_requires_mitmdump(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("lantu.tools.lens.capture_runtime.shutil.which", lambda _name: None)
    with pytest.raises(CaptureUnavailable, match=r"lantu\[capture\]"):
        CaptureProxy(tmp_path).start()


def test_capture_reads_session_failure_marker(tmp_path: Path) -> None:
    proxy = CaptureProxy(tmp_path, session_id="session_a")
    error = tmp_path / ".lantu" / "lens" / "capture" / "session_a.error"
    error.parent.mkdir(parents=True)
    error.write_text("disk full", encoding="utf-8")
    assert proxy.failure() == "disk full"
