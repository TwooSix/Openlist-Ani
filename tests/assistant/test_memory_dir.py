"""Tests for the directory-based memory system (MemoryDir)."""

from __future__ import annotations

import pytest

from openlist_ani.assistant.memory.frontmatter import format_frontmatter
from openlist_ani.assistant.memory.memory_dir import (
    ENTRYPOINT_NAME,
    MemoryDir,
    MemoryHeader,
)


@pytest.fixture
def memory_dir(tmp_path):
    """Create a MemoryDir rooted in a temp directory."""
    md = tmp_path / "memory"
    return MemoryDir(md)


# ------------------------------------------------------------------ #
# scan_memory_files
# ------------------------------------------------------------------ #


class TestScanMemoryFiles:
    """Tests for scan_memory_files()."""

    @pytest.mark.asyncio
    async def test_empty_dir(self, memory_dir: MemoryDir):
        headers = await memory_dir.scan_memory_files()
        assert headers == []

    @pytest.mark.asyncio
    async def test_scans_md_files_with_frontmatter(self, memory_dir: MemoryDir):
        # Write a memory file with frontmatter
        fm = format_frontmatter({
            "name": "User Preferences",
            "type": "user",
            "description": "Coding preferences",
        })
        (memory_dir.path / "user_prefs.md").write_text(
            fm + "- Likes dark mode\n", encoding="utf-8"
        )

        headers = await memory_dir.scan_memory_files()
        assert len(headers) == 1
        assert headers[0].filename == "user_prefs.md"
        assert headers[0].type == "user"
        assert headers[0].description == "Coding preferences"

    @pytest.mark.asyncio
    async def test_excludes_entrypoint(self, memory_dir: MemoryDir):
        (memory_dir.path / ENTRYPOINT_NAME).write_text(
            "# Index\n", encoding="utf-8"
        )
        (memory_dir.path / "topic.md").write_text("content\n", encoding="utf-8")

        headers = await memory_dir.scan_memory_files()
        assert len(headers) == 1
        assert headers[0].filename == "topic.md"

    @pytest.mark.asyncio
    async def test_excludes_non_md_files(self, memory_dir: MemoryDir):
        (memory_dir.path / "notes.txt").write_text("not a md\n", encoding="utf-8")
        (memory_dir.path / "data.json").write_text("{}\n", encoding="utf-8")
        (memory_dir.path / "real.md").write_text("content\n", encoding="utf-8")

        headers = await memory_dir.scan_memory_files()
        assert len(headers) == 1
        assert headers[0].filename == "real.md"

    @pytest.mark.asyncio
    async def test_sorted_by_mtime_descending(self, memory_dir: MemoryDir):
        import os
        import time

        # Create files with slightly different mtimes
        (memory_dir.path / "old.md").write_text("old\n", encoding="utf-8")
        # Set old mtime
        old_path = memory_dir.path / "old.md"
        os.utime(old_path, (time.time() - 100, time.time() - 100))

        (memory_dir.path / "new.md").write_text("new\n", encoding="utf-8")

        headers = await memory_dir.scan_memory_files()
        assert len(headers) == 2
        assert headers[0].filename == "new.md"  # Most recent first
        assert headers[1].filename == "old.md"

    @pytest.mark.asyncio
    async def test_handles_file_without_frontmatter(self, memory_dir: MemoryDir):
        (memory_dir.path / "bare.md").write_text(
            "Just some content\n", encoding="utf-8"
        )
        headers = await memory_dir.scan_memory_files()
        assert len(headers) == 1
        assert headers[0].type is None
        assert headers[0].description is None


# ------------------------------------------------------------------ #
# format_memory_manifest
# ------------------------------------------------------------------ #


class TestFormatMemoryManifest:
    def test_empty_headers(self, memory_dir: MemoryDir):
        result = memory_dir.format_memory_manifest([])
        assert result == "Memory files: (none)"

    def test_with_headers(self, memory_dir: MemoryDir):
        headers = [
            MemoryHeader(
                filename="prefs.md",
                file_path="memory/prefs.md",
                mtime_ms=1000.0,
                description="User preferences",
                type="user",
            ),
            MemoryHeader(
                filename="project.md",
                file_path="memory/project.md",
                mtime_ms=900.0,
                description=None,
                type="project",
            ),
        ]
        result = memory_dir.format_memory_manifest(headers)
        assert "Memory files (2):" in result
        assert "- prefs.md [user] \u2014 User preferences" in result
        assert "- project.md [project]" in result


# ------------------------------------------------------------------ #
# CRUD operations
# ------------------------------------------------------------------ #


class TestCRUD:
    def test_read_nonexistent(self, memory_dir: MemoryDir):
        assert memory_dir.read_memory("nonexistent.md") == ""

    @pytest.mark.asyncio
    async def test_write_and_read(self, memory_dir: MemoryDir):
        await memory_dir.write_memory(
            "test.md",
            "- Fact 1\n- Fact 2\n",
            frontmatter={"name": "Test", "type": "user", "description": "Test file"},
        )
        content = memory_dir.read_memory("test.md")
        assert "---" in content
        assert "name: Test" in content
        assert "- Fact 1" in content

    @pytest.mark.asyncio
    async def test_write_without_frontmatter(self, memory_dir: MemoryDir):
        await memory_dir.write_memory("plain.md", "Just content\n")
        content = memory_dir.read_memory("plain.md")
        assert content == "Just content\n"

    @pytest.mark.asyncio
    async def test_delete(self, memory_dir: MemoryDir):
        await memory_dir.write_memory("to_delete.md", "temp\n")
        assert memory_dir.read_memory("to_delete.md") != ""

        await memory_dir.delete_memory("to_delete.md")
        assert memory_dir.read_memory("to_delete.md") == ""

    @pytest.mark.asyncio
    async def test_delete_refuses_entrypoint(self, memory_dir: MemoryDir):
        # Create MEMORY.md
        (memory_dir.path / ENTRYPOINT_NAME).write_text("index\n", encoding="utf-8")

        await memory_dir.delete_memory(ENTRYPOINT_NAME)
        # Should still exist
        assert (memory_dir.path / ENTRYPOINT_NAME).is_file()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, memory_dir: MemoryDir):
        # Should not raise
        await memory_dir.delete_memory("ghost.md")

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, memory_dir: MemoryDir):
        with pytest.raises(ValueError, match="Path traversal"):
            memory_dir.read_memory("../../etc/passwd")


# ------------------------------------------------------------------ #
# Entrypoint (MEMORY.md index)
# ------------------------------------------------------------------ #


class TestEntrypoint:
    def test_load_empty(self, memory_dir: MemoryDir):
        result = memory_dir.load_entrypoint()
        assert result.content == ""
        assert result.line_count == 0
        assert not result.was_line_truncated
        assert not result.was_byte_truncated

    def test_load_normal(self, memory_dir: MemoryDir):
        (memory_dir.path / ENTRYPOINT_NAME).write_text(
            "- [Prefs](prefs.md) \u2014 preferences\n", encoding="utf-8"
        )
        result = memory_dir.load_entrypoint()
        assert "Prefs" in result.content
        # "content\n" splits to ["content", ""] -> 2 lines
        assert result.line_count == 2
        assert not result.was_line_truncated

    def test_load_truncates_lines(self, memory_dir: MemoryDir):
        # Create content exceeding MAX_ENTRYPOINT_LINES (200)
        lines = [f"- line {i}" for i in range(250)]
        (memory_dir.path / ENTRYPOINT_NAME).write_text(
            "\n".join(lines), encoding="utf-8"
        )
        result = memory_dir.load_entrypoint()
        assert result.was_line_truncated
        assert "WARNING" in result.content
        assert result.line_count == 200

    def test_load_truncates_bytes(self, memory_dir: MemoryDir):
        # Create content exceeding MAX_ENTRYPOINT_BYTES (25000)
        content = "x" * 30_000
        (memory_dir.path / ENTRYPOINT_NAME).write_text(content, encoding="utf-8")
        result = memory_dir.load_entrypoint()
        assert result.was_byte_truncated
        assert "WARNING" in result.content

    @pytest.mark.asyncio
    async def test_update_entrypoint(self, memory_dir: MemoryDir):
        await memory_dir.update_entrypoint("- [Topic](topic.md)\n")
        result = memory_dir.load_entrypoint()
        assert "Topic" in result.content


# ------------------------------------------------------------------ #
# Query helpers
# ------------------------------------------------------------------ #


class TestQueryHelpers:
    def test_is_memory_path_inside(self, memory_dir: MemoryDir):
        path = str(memory_dir.path / "test.md")
        assert memory_dir.is_memory_path(path) is True

    def test_is_memory_path_outside(self, memory_dir: MemoryDir):
        assert memory_dir.is_memory_path("/elsewhere/outside.md") is False

    @pytest.mark.asyncio
    async def test_list_filenames(self, memory_dir: MemoryDir):
        (memory_dir.path / ENTRYPOINT_NAME).write_text("index\n", encoding="utf-8")
        (memory_dir.path / "b.md").write_text("b\n", encoding="utf-8")
        (memory_dir.path / "a.md").write_text("a\n", encoding="utf-8")

        names = memory_dir.list_filenames()
        assert names == ["a.md", "b.md"]  # Sorted, excludes MEMORY.md


# ------------------------------------------------------------------ #
# Migration
# ------------------------------------------------------------------ #


class TestMigration:
    @pytest.mark.asyncio
    async def test_migrate_memory_md(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "MEMORY.md").write_text(
            "- Old memory fact\n", encoding="utf-8"
        )

        md = MemoryDir(data_dir / "memory")
        await md.migrate_from_flat_files(data_dir)

        result = md.load_entrypoint()
        assert "Old memory fact" in result.content

    @pytest.mark.asyncio
    async def test_migrate_user_md(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "USER.md").write_text(
            "- User likes dark mode\n", encoding="utf-8"
        )

        md = MemoryDir(data_dir / "memory")
        await md.migrate_from_flat_files(data_dir)

        content = md.read_memory("user_profile.md")
        assert "User likes dark mode" in content
        assert "type: user" in content

    @pytest.mark.asyncio
    async def test_migrate_idempotent(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "MEMORY.md").write_text("original\n", encoding="utf-8")

        md = MemoryDir(data_dir / "memory")
        await md.migrate_from_flat_files(data_dir)

        # Modify the migrated file
        await md.update_entrypoint("modified\n")

        # Re-run migration -- should NOT overwrite
        await md.migrate_from_flat_files(data_dir)
        result = md.load_entrypoint()
        assert "modified" in result.content

    @pytest.mark.asyncio
    async def test_migrate_skips_empty_user(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "USER.md").write_text("   \n", encoding="utf-8")

        md = MemoryDir(data_dir / "memory")
        await md.migrate_from_flat_files(data_dir)

        # Should not create user_profile.md for empty content
        assert not (md.path / "user_profile.md").exists()
