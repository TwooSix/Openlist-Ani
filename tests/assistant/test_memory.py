"""Tests for memory manager -- four-file persistent memory + CLAUDE.md."""

import pytest
from pathlib import Path

from openlist_ani.assistant.memory.manager import MemoryManager, MAX_MEMORY_LINES


@pytest.fixture
def data_dir(tmp_path: Path):
    """Create a temporary data directory."""
    d = tmp_path / "data" / "assistant"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def project_dir(tmp_path: Path):
    """Create a temporary project directory with CLAUDE.md files."""
    project = tmp_path / "myproject"
    project.mkdir()

    # Project-level CLAUDE.md
    (project / "CLAUDE.md").write_text(
        "Always use Python 3.11+.\nPrefer pathlib over os.path.\n"
    )

    # .openlist-ani/CLAUDE.md
    dot_dir = project / ".openlist-ani"
    dot_dir.mkdir()
    (dot_dir / "CLAUDE.md").write_text("Use pytest for testing.\n")

    # CLAUDE.local.md
    (project / "CLAUDE.local.md").write_text("My private local notes.\n")

    return project


class TestMemoryManagerInit:
    def test_creates_data_dir(self, tmp_path: Path):
        data = tmp_path / "new_data"
        MemoryManager(data_dir=data)
        assert data.exists()
        assert (data / "sessions").exists()

    def test_creates_default_soul(self, data_dir: Path):
        MemoryManager(data_dir=data_dir)
        soul_file = data_dir / "SOUL.md"
        assert soul_file.exists()
        content = soul_file.read_text()
        assert "Openlist-Ani Assistant" in content
        assert "anime tracking" in content
        assert "# How to Use Tools" in content

    def test_does_not_overwrite_existing_soul(self, data_dir: Path):
        soul_file = data_dir / "SOUL.md"
        soul_file.write_text("Custom soul content.\n")
        MemoryManager(data_dir=data_dir)
        assert soul_file.read_text() == "Custom soul content.\n"


class TestSoul:
    def test_load_soul(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        soul = mm.load_soul()
        assert "Openlist-Ani Assistant" in soul

    def test_load_custom_soul(self, data_dir: Path):
        soul_file = data_dir / "SOUL.md"
        soul_file.write_text("I am a custom assistant.\n")
        mm = MemoryManager(data_dir=data_dir)
        soul = mm.load_soul()
        assert soul == "I am a custom assistant.\n"


class TestMemory:
    def test_load_memory_empty(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        assert mm.load_memory() == ""

    @pytest.mark.asyncio
    async def test_append_memory(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        await mm.append_memory("User prefers dark mode")
        content = mm.load_memory()
        assert "User prefers dark mode" in content

    @pytest.mark.asyncio
    async def test_append_memory_timestamp(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        await mm.append_memory("Some fact")
        content = mm.load_memory()
        # Should contain timestamp in format [YYYY-MM-DD HH:MM]
        assert "[" in content and "]" in content

    def test_load_memory_truncation(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        memory_file = data_dir / "MEMORY.md"
        # Write more lines than MAX_MEMORY_LINES
        lines = [f"- Line {i}" for i in range(MAX_MEMORY_LINES + 50)]
        memory_file.write_text("\n".join(lines))
        content = mm.load_memory()
        assert "WARNING" in content
        assert f"{MAX_MEMORY_LINES + 50} lines" in content


class TestUser:
    def test_load_user_empty(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        assert mm.load_user() == ""

    @pytest.mark.asyncio
    async def test_append_user_fact(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        await mm.append_user_fact("User is a data scientist")
        content = mm.load_user()
        assert "data scientist" in content

    @pytest.mark.asyncio
    async def test_append_user_fact_timestamp(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        await mm.append_user_fact("User likes Python")
        content = mm.load_user()
        assert "[" in content and "]" in content


class TestSession:
    @pytest.mark.asyncio
    async def test_start_new_session(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        await mm.start_new_session()
        session_files = list((data_dir / "sessions").glob("SESSION_*.md"))
        assert len(session_files) == 1
        content = session_files[0].read_text()
        assert content.startswith("# Session")

    @pytest.mark.asyncio
    async def test_append_turn(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        await mm.start_new_session()
        await mm.append_turn(
            user_msg="Hello!",
            assistant_msg="Hi there!",
            tool_context="",
        )
        history = await mm.load_session_history()
        assert "Hello!" in history
        assert "Hi there!" in history

    @pytest.mark.asyncio
    async def test_append_turn_with_tools(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        await mm.start_new_session()
        await mm.append_turn(
            user_msg="Search for files",
            assistant_msg="Found 3 files.",
            tool_context="grep, glob",
        )
        history = await mm.load_session_history()
        assert "grep, glob" in history

    @pytest.mark.asyncio
    async def test_load_session_history_empty(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        history = await mm.load_session_history()
        assert history == ""


class TestClaudeMd:
    def test_load_claude_md_files(self, data_dir: Path, project_dir: Path):
        mm = MemoryManager(data_dir=data_dir, project_root=project_dir)
        files = mm.load_claude_md_files()
        assert len(files) == 3
        types = [f["type"] for f in files]
        assert "Project" in types
        assert "Local" in types

    def test_load_claude_md_project(self, data_dir: Path, project_dir: Path):
        mm = MemoryManager(data_dir=data_dir, project_root=project_dir)
        files = mm.load_claude_md_files()
        project_files = [f for f in files if f["type"] == "Project"]
        assert len(project_files) == 2
        contents = " ".join(f["content"] for f in project_files)
        assert "Python 3.11" in contents
        assert "pytest" in contents

    def test_load_claude_md_local(self, data_dir: Path, project_dir: Path):
        mm = MemoryManager(data_dir=data_dir, project_root=project_dir)
        files = mm.load_claude_md_files()
        local_files = [f for f in files if f["type"] == "Local"]
        assert len(local_files) == 1
        assert "private local" in local_files[0]["content"]

    def test_load_claude_md_no_files(self, data_dir: Path, tmp_path: Path):
        empty_project = tmp_path / "empty_project"
        empty_project.mkdir()
        mm = MemoryManager(data_dir=data_dir, project_root=empty_project)
        files = mm.load_claude_md_files()
        assert len(files) == 0

    def test_build_claude_md_prompt(self, data_dir: Path, project_dir: Path):
        mm = MemoryManager(data_dir=data_dir, project_root=project_dir)
        prompt = mm.build_claude_md_prompt()
        assert "OVERRIDE" in prompt
        assert "Python 3.11" in prompt
        assert "pytest" in prompt
        assert "private local" in prompt

    def test_build_claude_md_prompt_empty(self, data_dir: Path, tmp_path: Path):
        empty_project = tmp_path / "empty_project"
        empty_project.mkdir()
        mm = MemoryManager(data_dir=data_dir, project_root=empty_project)
        prompt = mm.build_claude_md_prompt()
        assert prompt == ""


class TestClearReset:
    @pytest.mark.asyncio
    async def test_clear_session(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        await mm.start_new_session()
        await mm.append_turn("Hi", "Hello", "")
        await mm.clear_session()
        # Session files should be gone
        session_files = list((data_dir / "sessions").glob("SESSION_*.md"))
        assert len(session_files) == 0

    @pytest.mark.asyncio
    async def test_clear_all(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        await mm.append_memory("A fact")
        await mm.append_user_fact("User info")
        await mm.start_new_session()
        await mm.append_turn("Hi", "Hello", "")

        await mm.clear_all()

        assert mm.load_memory() == ""
        assert mm.load_user() == ""
        history = await mm.load_session_history()
        assert history == ""

    @pytest.mark.asyncio
    async def test_clear_all_keeps_soul(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        await mm.clear_all()
        soul = mm.load_soul()
        assert "Openlist-Ani Assistant" in soul


class TestUtilities:
    def test_estimate_tokens(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        assert mm.estimate_tokens("hello world!") == 3  # 12 chars / 4
        assert mm.estimate_tokens("") == 0

    def test_data_dir_property(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        assert mm.data_dir == data_dir

    def test_project_root_property(self, data_dir: Path, project_dir: Path):
        mm = MemoryManager(data_dir=data_dir, project_root=project_dir)
        assert mm.project_root == project_dir
