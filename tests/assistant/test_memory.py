"""Tests for memory manager -- directory-based memory + CLAUDE.md."""

import pytest
from pathlib import Path

from openlist_ani.assistant.memory.manager import MemoryManager


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

    def test_creates_memory_dir(self, tmp_path: Path):
        data = tmp_path / "new_data"
        MemoryManager(data_dir=data)
        assert (data / "memory").exists()

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

    def test_load_memory_with_content(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        # Write MEMORY.md in memory/ dir
        (data_dir / "memory" / "MEMORY.md").write_text(
            "- [Prefs](prefs.md) \u2014 preferences\n"
        )
        content = mm.load_memory()
        assert "Prefs" in content

    def test_build_memory_prompt_empty(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        prompt = mm.build_memory_prompt()
        assert "# Memory" in prompt
        assert "No memories stored yet" in prompt

    def test_build_memory_prompt_with_content(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        (data_dir / "memory" / "MEMORY.md").write_text(
            "- [User Profile](user_profile.md) \u2014 user info\n"
        )
        prompt = mm.build_memory_prompt()
        assert "# Memory" in prompt
        assert "User Profile" in prompt

    def test_memory_dir_property(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        assert mm.memory_dir is not None
        assert mm.memory_dir.path == data_dir / "memory"


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


class TestMigration:
    @pytest.mark.asyncio
    async def test_migrate_if_needed(self, data_dir: Path):
        # Create old-style MEMORY.md at data root
        (data_dir / "MEMORY.md").write_text("- Old fact\n")
        (data_dir / "USER.md").write_text("- User is a developer\n")

        mm = MemoryManager(data_dir=data_dir)
        await mm.migrate_if_needed()

        # Check MEMORY.md migrated
        assert mm.load_memory() != ""
        assert "Old fact" in mm.load_memory()

        # Check USER.md migrated
        content = mm.memory_dir.read_memory("user_profile.md")
        assert "developer" in content


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


class TestMemoryPromptUsesTool:
    """Verify that the memory system prompt references the memory tool."""

    def test_prompt_references_memory_tool(self, data_dir: Path):
        mm = MemoryManager(data_dir=data_dir)
        prompt = mm.build_memory_prompt()
        # Should reference the memory tool, not raw file writes
        assert "memory(action=" in prompt or 'action="write"' in prompt
        # Should NOT contain old "Write (or update) a topic file" instruction
        assert "Write (or update) a topic file" not in prompt
