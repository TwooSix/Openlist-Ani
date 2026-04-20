"""Tests for GrepTool — ripgrep-backed search restricted to whitelist."""

from __future__ import annotations

import shutil

import pytest

from openlist_ani.assistant.tool.builtin import _file_security as fs
from openlist_ani.assistant.tool.builtin.grep_tool import GrepTool

# All these tests require the real ``rg`` binary on PATH.
pytestmark = pytest.mark.skipif(
    shutil.which("rg") is None,
    reason="ripgrep (rg) binary not available on PATH",
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    for d in fs.WHITELIST_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("def foo():\n    return 1\n")
    (tmp_path / "src" / "b.py").write_text("def bar():\n    return 2\n")
    (tmp_path / "skills" / "x.md").write_text("# Heading\nhello world\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_files_with_matches(project):
    out = await GrepTool().execute(pattern=r"def\s+\w+")
    assert "a.py" in out
    assert "b.py" in out


@pytest.mark.asyncio
async def test_content_mode(project):
    out = await GrepTool().execute(
        pattern=r"return", output_mode="content"
    )
    assert "return 1" in out or "return 2" in out


@pytest.mark.asyncio
async def test_count_mode(project):
    out = await GrepTool().execute(
        pattern=r"return", output_mode="count"
    )
    assert ":1" in out  # rg --count emits "<file>:N"


@pytest.mark.asyncio
async def test_glob_filter(project):
    out = await GrepTool().execute(
        pattern=r"hello", glob="**/*.md"
    )
    assert "x.md" in out
    assert "a.py" not in out


@pytest.mark.asyncio
async def test_no_matches(project):
    out = await GrepTool().execute(pattern=r"zzzz_no_such_thing_zzzz")
    assert "no matches" in out.lower()


@pytest.mark.asyncio
async def test_redacts_match_content(project):
    leak = project / "data" / "scratch.log"
    leak.parent.mkdir(parents=True, exist_ok=True)
    leak.write_text("token=ghp_abcdefghijklmnopqrstuvwxyz0123456789\n")
    out = await GrepTool().execute(
        pattern=r"token", output_mode="content"
    )
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in out
    assert "<REDACTED>" in out


@pytest.mark.asyncio
async def test_path_outside_whitelist_rejected(project):
    out = await GrepTool().execute(pattern=r"x", path="/etc")
    assert "Access denied" in out or "outside the whitelist" in out


def test_tool_metadata():
    t = GrepTool()
    assert t.name == "grep"
    assert t.is_read_only() is True
    assert t.is_concurrency_safe() is True
    assert "pattern" in t.parameters["required"]
