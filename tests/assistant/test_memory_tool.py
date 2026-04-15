"""Tests for the MemoryTool."""

from __future__ import annotations

import pytest

from openlist_ani.assistant.memory.frontmatter import format_frontmatter
from openlist_ani.assistant.memory.memory_dir import MemoryDir
from openlist_ani.assistant.tool.builtin.memory_tool import MemoryTool


@pytest.fixture
def memory_dir(tmp_path):
    """Create a MemoryDir rooted in a temp directory."""
    return MemoryDir(tmp_path / "memory")


@pytest.fixture
def tool(memory_dir: MemoryDir) -> MemoryTool:
    """Create a MemoryTool backed by the temp MemoryDir."""
    return MemoryTool(memory_dir)


class TestListAction:
    @pytest.mark.asyncio
    async def test_list_empty(self, tool: MemoryTool):
        result = await tool.execute(action="list")
        assert "none" in result.lower()

    @pytest.mark.asyncio
    async def test_list_with_files(
        self, tool: MemoryTool, memory_dir: MemoryDir,
    ):
        fm = format_frontmatter({
            "name": "User Preferences",
            "type": "user",
            "description": "Coding preferences",
        })
        (memory_dir.path / "user_prefs.md").write_text(
            fm + "- Likes dark mode\n", encoding="utf-8",
        )

        result = await tool.execute(action="list")
        assert "user_prefs.md" in result
        assert "user" in result
        assert "Coding preferences" in result


class TestReadAction:
    @pytest.mark.asyncio
    async def test_read_existing(
        self, tool: MemoryTool, memory_dir: MemoryDir,
    ):
        (memory_dir.path / "notes.md").write_text(
            "- Important note\n", encoding="utf-8",
        )
        result = await tool.execute(action="read", filename="notes.md")
        assert "Important note" in result

    @pytest.mark.asyncio
    async def test_read_nonexistent(self, tool: MemoryTool):
        result = await tool.execute(action="read", filename="ghost.md")
        assert (
            "not found" in result.lower()
            or "does not exist" in result.lower()
        )

    @pytest.mark.asyncio
    async def test_read_missing_filename(self, tool: MemoryTool):
        result = await tool.execute(action="read")
        assert "required" in result.lower() or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_read_path_traversal(self, tool: MemoryTool):
        result = await tool.execute(
            action="read", filename="../../etc/passwd",
        )
        assert "error" in result.lower()


class TestWriteAction:
    @pytest.mark.asyncio
    async def test_write_with_frontmatter(
        self, tool: MemoryTool, memory_dir: MemoryDir,
    ):
        result = await tool.execute(
            action="write",
            filename="user_prefs.md",
            content="- Likes dark mode\n",
            name="User Preferences",
            type="user",
            description="Coding preferences",
        )
        assert "wrote" in result.lower() or "saved" in result.lower()

        # Verify file was written correctly
        content = memory_dir.read_memory("user_prefs.md")
        assert "name: User Preferences" in content
        assert "type: user" in content
        assert "- Likes dark mode" in content

    @pytest.mark.asyncio
    async def test_write_without_frontmatter(
        self, tool: MemoryTool, memory_dir: MemoryDir,
    ):
        result = await tool.execute(
            action="write",
            filename="plain.md",
            content="Just plain content\n",
        )
        assert "wrote" in result.lower() or "saved" in result.lower()

        content = memory_dir.read_memory("plain.md")
        assert content == "Just plain content\n"

    @pytest.mark.asyncio
    async def test_write_missing_filename(self, tool: MemoryTool):
        result = await tool.execute(action="write", content="hello")
        assert "required" in result.lower() or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_write_missing_content(self, tool: MemoryTool):
        result = await tool.execute(action="write", filename="test.md")
        assert "required" in result.lower() or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_write_reminds_update_index(self, tool: MemoryTool):
        result = await tool.execute(
            action="write",
            filename="test.md",
            content="content",
        )
        assert "MEMORY.md" in result or "update_index" in result

    @pytest.mark.asyncio
    async def test_write_path_traversal(self, tool: MemoryTool):
        result = await tool.execute(
            action="write",
            filename="../../etc/evil.md",
            content="bad",
        )
        assert "error" in result.lower()


class TestDeleteAction:
    @pytest.mark.asyncio
    async def test_delete_existing(
        self, tool: MemoryTool, memory_dir: MemoryDir,
    ):
        (memory_dir.path / "to_delete.md").write_text(
            "temp\n", encoding="utf-8",
        )
        result = await tool.execute(
            action="delete", filename="to_delete.md",
        )
        assert "deleted" in result.lower()
        assert memory_dir.read_memory("to_delete.md") == ""

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, tool: MemoryTool):
        result = await tool.execute(
            action="delete", filename="ghost.md",
        )
        assert (
            "not found" in result.lower()
            or "does not exist" in result.lower()
        )

    @pytest.mark.asyncio
    async def test_delete_memory_md_protected(
        self, tool: MemoryTool, memory_dir: MemoryDir,
    ):
        (memory_dir.path / "MEMORY.md").write_text(
            "index\n", encoding="utf-8",
        )
        result = await tool.execute(
            action="delete", filename="MEMORY.md",
        )
        assert "cannot" in result.lower() or "protected" in result.lower()
        assert (memory_dir.path / "MEMORY.md").is_file()

    @pytest.mark.asyncio
    async def test_delete_missing_filename(self, tool: MemoryTool):
        result = await tool.execute(action="delete")
        assert "required" in result.lower() or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_reminds_update_index(
        self, tool: MemoryTool, memory_dir: MemoryDir,
    ):
        (memory_dir.path / "tmp.md").write_text(
            "x\n", encoding="utf-8",
        )
        result = await tool.execute(
            action="delete", filename="tmp.md",
        )
        assert "MEMORY.md" in result or "update_index" in result


class TestUpdateAction:
    @pytest.mark.asyncio
    async def test_update_replaces_text(
        self, tool: MemoryTool, memory_dir: MemoryDir,
    ):
        (memory_dir.path / "prefs.md").write_text(
            "- Likes dark mode\n- Uses vim\n", encoding="utf-8",
        )
        result = await tool.execute(
            action="update",
            filename="prefs.md",
            old_str="dark mode",
            new_str="light mode",
        )
        assert "updated" in result.lower()

        content = memory_dir.read_memory("prefs.md")
        assert "light mode" in content
        assert "dark mode" not in content

    @pytest.mark.asyncio
    async def test_update_nonexistent_file(self, tool: MemoryTool):
        result = await tool.execute(
            action="update",
            filename="ghost.md",
            old_str="x",
            new_str="y",
        )
        assert (
            "not found" in result.lower()
            or "does not exist" in result.lower()
        )

    @pytest.mark.asyncio
    async def test_update_old_str_not_found(
        self, tool: MemoryTool, memory_dir: MemoryDir,
    ):
        (memory_dir.path / "test.md").write_text(
            "hello world\n", encoding="utf-8",
        )
        result = await tool.execute(
            action="update",
            filename="test.md",
            old_str="xyz not here",
            new_str="replacement",
        )
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_update_old_str_not_unique(
        self, tool: MemoryTool, memory_dir: MemoryDir,
    ):
        (memory_dir.path / "dup.md").write_text(
            "- item\n- item\n", encoding="utf-8",
        )
        result = await tool.execute(
            action="update",
            filename="dup.md",
            old_str="item",
            new_str="thing",
        )
        assert "unique" in result.lower() or "multiple" in result.lower()

    @pytest.mark.asyncio
    async def test_update_same_old_new(
        self, tool: MemoryTool, memory_dir: MemoryDir,
    ):
        (memory_dir.path / "same.md").write_text(
            "content\n", encoding="utf-8",
        )
        result = await tool.execute(
            action="update",
            filename="same.md",
            old_str="content",
            new_str="content",
        )
        assert "differ" in result.lower() or "same" in result.lower()

    @pytest.mark.asyncio
    async def test_update_missing_params(self, tool: MemoryTool):
        result = await tool.execute(
            action="update", filename="test.md",
        )
        assert "required" in result.lower() or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_update_path_traversal(self, tool: MemoryTool):
        result = await tool.execute(
            action="update",
            filename="../../etc/passwd",
            old_str="x",
            new_str="y",
        )
        assert "error" in result.lower()


class TestUpdateIndexAction:
    @pytest.mark.asyncio
    async def test_update_index(
        self, tool: MemoryTool, memory_dir: MemoryDir,
    ):
        result = await tool.execute(
            action="update_index",
            content="- [Prefs](prefs.md) — preferences\n",
        )
        assert "updated" in result.lower()

        ep = memory_dir.load_entrypoint()
        assert "Prefs" in ep.content

    @pytest.mark.asyncio
    async def test_update_index_missing_content(self, tool: MemoryTool):
        result = await tool.execute(action="update_index")
        assert "required" in result.lower() or "error" in result.lower()


class TestToolMetadata:
    def test_name(self, tool: MemoryTool):
        assert tool.name == "memory"

    def test_description_nonempty(self, tool: MemoryTool):
        assert len(tool.description) > 10

    def test_parameters_has_action(self, tool: MemoryTool):
        props = tool.parameters["properties"]
        assert "action" in props
        assert "enum" in props["action"]

    def test_prompt_nonempty(self, tool: MemoryTool):
        prompt = tool.prompt()
        assert "memory" in prompt.lower()
        assert "read" in prompt
        assert "write" in prompt
        assert "update" in prompt
        assert "delete" in prompt
        assert "list" in prompt
        assert "update_index" in prompt

    def test_concurrency_safe_read(self, tool: MemoryTool):
        assert tool.is_concurrency_safe({"action": "read"}) is True

    def test_concurrency_safe_list(self, tool: MemoryTool):
        assert tool.is_concurrency_safe({"action": "list"}) is True

    def test_concurrency_unsafe_write(self, tool: MemoryTool):
        assert tool.is_concurrency_safe({"action": "write"}) is False

    def test_concurrency_unsafe_update(self, tool: MemoryTool):
        assert tool.is_concurrency_safe({"action": "update"}) is False

    def test_concurrency_unsafe_delete(self, tool: MemoryTool):
        assert tool.is_concurrency_safe({"action": "delete"}) is False

    def test_concurrency_unsafe_update_index(self, tool: MemoryTool):
        assert (
            tool.is_concurrency_safe({"action": "update_index"})
            is False
        )

    def test_concurrency_none_input(self, tool: MemoryTool):
        assert tool.is_concurrency_safe(None) is False

    def test_is_read_only_read(self, tool: MemoryTool):
        assert tool.is_read_only({"action": "read"}) is True

    def test_is_read_only_write(self, tool: MemoryTool):
        assert tool.is_read_only({"action": "write"}) is False

    def test_user_facing_name(self, tool: MemoryTool):
        assert (
            tool.user_facing_name(
                {"action": "read", "filename": "x.md"},
            )
            == "memory.read(x.md)"
        )
        assert (
            tool.user_facing_name({"action": "list"})
            == "memory.list"
        )
        assert tool.user_facing_name() == "memory"

    def test_activity_description(self, tool: MemoryTool):
        assert "Reading" in (
            tool.get_activity_description(
                {"action": "read", "filename": "x.md"},
            )
            or ""
        )
        assert "Listing" in (
            tool.get_activity_description({"action": "list"}) or ""
        )
        assert tool.get_activity_description(None) is None


class TestUnknownAction:
    @pytest.mark.asyncio
    async def test_unknown_action(self, tool: MemoryTool):
        result = await tool.execute(action="explode")
        assert "unknown" in result.lower() or "error" in result.lower()
