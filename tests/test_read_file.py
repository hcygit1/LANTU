from __future__ import annotations

import pytest

from lantu.tools.read_file import Params, READ_MAX_LINE_LENGTH, ReadFile


@pytest.mark.asyncio
async def test_read_file_truncates_long_lines_and_recommends_grep(tmp_path) -> None:
    path = tmp_path / "minified.js"
    path.write_text("x" * (READ_MAX_LINE_LENGTH + 100), encoding="utf-8")

    result = await ReadFile().execute(Params(file_path=str(path)))

    assert not result.is_error
    assert result.output.startswith(f"1\t{'x' * READ_MAX_LINE_LENGTH}")
    assert "line truncated to 2000 chars" in result.output
    assert "use Grep to search this line" in result.output
    assert "x" * (READ_MAX_LINE_LENGTH + 1) not in result.output


@pytest.mark.asyncio
async def test_read_file_keeps_normal_lines_unchanged(tmp_path) -> None:
    path = tmp_path / "normal.py"
    path.write_text("first\nsecond", encoding="utf-8")

    result = await ReadFile().execute(Params(file_path=str(path)))

    assert result.output == "1\tfirst\n2\tsecond"
