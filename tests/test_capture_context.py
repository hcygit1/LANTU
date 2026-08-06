from lantu.client import _model_call_headers, model_call_context


def test_model_call_context_adds_and_clears_capture_headers(monkeypatch) -> None:
    monkeypatch.delenv("LANTU_CAPTURE_ENABLED", raising=False)
    assert _model_call_headers() == {}
    monkeypatch.setenv("LANTU_CAPTURE_ENABLED", "1")
    with model_call_context("call_1", "session_a"):
        assert _model_call_headers() == {
            "X-LANTU-Model-Call-ID": "call_1",
            "X-LANTU-Session-ID": "session_a",
        }
    assert _model_call_headers() == {}
