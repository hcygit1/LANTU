from __future__ import annotations

import pytest

from lantu.tools.grep import GREP_CONTEXT_CHARS, Grep, Params


@pytest.mark.asyncio
async def test_grep_keeps_context_around_match_in_long_line(tmp_path) -> None:
    path = tmp_path / "minified.js"
    line = "a" * 5_000 + "NEEDLE" + "b" * 5_000
    path.write_text(line, encoding="utf-8")

    result = await Grep().execute(Params(pattern="NEEDLE", path=str(tmp_path)))

    assert not result.is_error
    assert "NEEDLE" in result.output
    assert len(result.output) < 2 * GREP_CONTEXT_CHARS + 100
    assert result.output.startswith("minified.js:1:...")
    assert result.output.endswith("...")


@pytest.mark.asyncio
async def test_grep_keeps_short_matching_lines_unchanged(tmp_path) -> None:
    path = tmp_path / "sample.py"
    path.write_text("before NEEDLE after", encoding="utf-8")

    result = await Grep().execute(Params(pattern="NEEDLE", path=str(tmp_path)))

    assert result.output == "sample.py:1:before NEEDLE after"
