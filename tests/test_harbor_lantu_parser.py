from pathlib import Path

from evals.harbor.parser import parse_metrics


def test_parse_metrics_prefers_final_result(tmp_path: Path) -> None:
    output = tmp_path / "stream.jsonl"
    output.write_text(
        '{"type":"usage","input_tokens":10,"output_tokens":2}\n'
        '{"type":"result","duration_ms":1234,"usage":{"input_tokens":42,"output_tokens":7}}\n',
        encoding="utf-8",
    )

    assert parse_metrics(output).input_tokens == 42
    assert parse_metrics(output).output_tokens == 7
    assert parse_metrics(output).duration_ms == 1234


def test_parse_metrics_ignores_invalid_lines_and_falls_back_to_usage(tmp_path: Path) -> None:
    output = tmp_path / "stream.jsonl"
    output.write_text(
        "banner\nnot-json\n"
        '{"type":"usage","input_tokens":3,"output_tokens":4}\n',
        encoding="utf-8",
    )

    metrics = parse_metrics(output)
    assert (metrics.input_tokens, metrics.output_tokens, metrics.duration_ms) == (3, 4, 0)


def test_parse_metrics_missing_file_is_empty(tmp_path: Path) -> None:
    metrics = parse_metrics(tmp_path / "missing.jsonl")
    assert metrics.input_tokens == metrics.output_tokens == metrics.duration_ms == 0
