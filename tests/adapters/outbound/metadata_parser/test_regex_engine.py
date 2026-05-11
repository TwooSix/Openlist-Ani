import json
from pathlib import Path

import pytest

from openlist_ani.adapters.outbound.metadata_parser.regex import (
    RegexTitleExtractEngine,
)

FIXTURE_PATH = (
    Path(__file__).parents[3]
    / "fixtures"
    / "metadata_parser"
    / "regex_manual_cases.jsonl"
)


def _load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["title"])
async def test_regex_engine_parses_manual_release_title_cases(case: dict):
    engine = RegexTitleExtractEngine()

    result = (await engine.parse_titles([case["title"]]))[0]

    assert result.release_title == case["title"]
    if not case["expect_success"]:
        assert result.success is False
        assert case["error_contains"] in (result.error or "")
        return

    assert result.success is True
    assert result.result is not None
    parsed = result.result.model_dump()
    assert parsed == {
        **case["expected"],
        "tmdb_id": None,
    }
