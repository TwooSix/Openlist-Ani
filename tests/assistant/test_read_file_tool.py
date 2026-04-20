"""Tests for ReadFileTool — read-only file access with redaction."""

from __future__ import annotations

import pytest

from openlist_ani.assistant.tool.builtin import _file_security as fs
from openlist_ani.assistant.tool.builtin.read_file_tool import ReadFileTool


@pytest.fixture
def project(tmp_path, monkeypatch):
    for d in fs.WHITELIST_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_reads_file_with_line_numbers(project):
    f = project / "src" / "demo.py"
    f.write_text("a = 1\nb = 2\nc = 3\n")
    out = await ReadFileTool().execute(path="src/demo.py")
    assert "a = 1" in out
    assert "b = 2" in out
    assert "src/demo.py" in out
    # numbered output
    assert "\t" in out


@pytest.mark.asyncio
async def test_offset_and_limit(project):
    f = project / "src" / "long.py"
    f.write_text("\n".join(f"line{i}" for i in range(100)))
    out = await ReadFileTool().execute(path="src/long.py", offset=10, limit=5)
    assert "line10" in out
    assert "line14" in out
    assert "line9" not in out
    assert "line15" not in out


@pytest.mark.asyncio
async def test_redacts_secrets_in_content(project):
    f = project / "data" / "leak.txt"
    f.write_text("api_key = 'sk-abcdefghijklmnopqrst'\n")
    out = await ReadFileTool().execute(path="data/leak.txt")
    assert "sk-abcdefghijklmnopqrst" not in out
    assert "<REDACTED>" in out


@pytest.mark.asyncio
async def test_rejects_outside_whitelist(project):
    (project / "config.toml").write_text("token = 'leak'")
    out = await ReadFileTool().execute(path="config.toml")
    assert "Access denied" in out


@pytest.mark.asyncio
async def test_rejects_sensitive_filename(project):
    (project / "data" / ".env").write_text("OPENAI_API_KEY=xxx")
    out = await ReadFileTool().execute(path="data/.env")
    assert "Access denied" in out


@pytest.mark.asyncio
async def test_missing_file(project):
    out = await ReadFileTool().execute(path="src/nope.py")
    assert "not found" in out.lower()


@pytest.mark.asyncio
async def test_directory_rejected(project):
    out = await ReadFileTool().execute(path="src")
    assert "directory" in out.lower()


@pytest.mark.asyncio
async def test_binary_rejected(project):
    f = project / "data" / "blob.bin"
    f.write_bytes(b"\x00\x01\x02\x03binary")
    out = await ReadFileTool().execute(path="data/blob.bin")
    assert "binary" in out.lower()


def test_tool_metadata():
    t = ReadFileTool()
    assert t.name == "read_file"
    assert t.is_read_only() is True
    assert t.is_concurrency_safe() is True
    schema = t.parameters
    assert "path" in schema["required"]
