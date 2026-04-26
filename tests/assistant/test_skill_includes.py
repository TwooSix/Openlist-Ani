"""Tests for skill @include system."""

from pathlib import Path

from openlist_ani.assistant.skill.catalog import (
    MAX_INCLUDE_DEPTH,
    TEXT_FILE_EXTENSIONS,
    SkillCatalog,
    _extract_include_paths,
    _resolve_include_path,
    resolve_includes,
)


class TestResolveIncludePath:
    """Tests for _resolve_include_path()."""

    def test_relative_path(self, tmp_path: Path):
        result = _resolve_include_path("utils.py", tmp_path)
        assert result == (tmp_path / "utils.py").resolve()

    def test_dotslash_path(self, tmp_path: Path):
        result = _resolve_include_path("./lib/helper.py", tmp_path)
        assert result == (tmp_path / "lib" / "helper.py").resolve()

    def test_absolute_path(self, tmp_path: Path):
        result = _resolve_include_path("/etc/config.txt", tmp_path)
        assert result == Path("/etc/config.txt").resolve()

    def test_home_path(self, tmp_path: Path):
        import os

        result = _resolve_include_path("~/test.md", tmp_path)
        assert result is not None
        assert str(result).startswith(os.path.expanduser("~"))

    def test_strip_fragment(self, tmp_path: Path):
        result = _resolve_include_path("file.md#section", tmp_path)
        assert result == (tmp_path / "file.md").resolve()

    def test_empty_path(self, tmp_path: Path):
        assert _resolve_include_path("", tmp_path) is None

    def test_fragment_only(self, tmp_path: Path):
        assert _resolve_include_path("#section", tmp_path) is None


class TestExtractIncludePaths:
    """Tests for _extract_include_paths()."""

    def test_basic_include(self, tmp_path: Path):
        content = "Some text @./config.md more text"
        paths = _extract_include_paths(content, tmp_path)
        assert len(paths) == 1
        assert paths[0] == (tmp_path / "config.md").resolve()

    def test_multiple_includes(self, tmp_path: Path):
        content = """
First line @./file1.md
Second line @./file2.py
"""
        paths = _extract_include_paths(content, tmp_path)
        assert len(paths) == 2

    def test_no_includes(self, tmp_path: Path):
        content = "No includes here, just normal text."
        paths = _extract_include_paths(content, tmp_path)
        assert len(paths) == 0

    def test_skip_code_blocks(self, tmp_path: Path):
        content = """Normal text
```
@./should_be_skipped.md
```
After code block @./should_be_included.md
"""
        paths = _extract_include_paths(content, tmp_path)
        # Only the one outside the code block
        assert len(paths) == 1
        assert paths[0].name == "should_be_included.md"

    def test_skip_inline_code(self, tmp_path: Path):
        content = "Check `@./not_included.md` but @./included.md is fine"
        paths = _extract_include_paths(content, tmp_path)
        assert len(paths) == 1
        assert paths[0].name == "included.md"

    def test_start_of_line(self, tmp_path: Path):
        content = "@./at_start.md"
        paths = _extract_include_paths(content, tmp_path)
        assert len(paths) == 1
        assert paths[0].name == "at_start.md"


class TestResolveIncludes:
    """Tests for resolve_includes()."""

    def test_basic_include(self, tmp_path: Path):
        # Create included file
        included = tmp_path / "utils.md"
        included.write_text("# Utils\nHelper content", encoding="utf-8")

        content = "Main content @./utils.md"
        result = resolve_includes(content, tmp_path)

        assert "Main content" in result
        assert "Helper content" in result

    def test_nonexistent_include_ignored(self, tmp_path: Path):
        content = "Main content @./nonexistent.md"
        result = resolve_includes(content, tmp_path)
        # Should return content unchanged (nonexistent file silently ignored)
        assert result == content

    def test_circular_reference_prevention(self, tmp_path: Path):
        # Create two files that include each other
        file_a = tmp_path / "a.md"
        file_b = tmp_path / "b.md"
        file_a.write_text("Content A @./b.md", encoding="utf-8")
        file_b.write_text("Content B @./a.md", encoding="utf-8")

        # Should not infinite loop
        result = resolve_includes(
            "Root @./a.md",
            tmp_path,
            processed_paths={str(tmp_path / "root.md")},
        )
        assert "Content A" in result
        # b.md should also be included (one level deep)
        assert "Content B" in result

    def test_max_depth_limit(self, tmp_path: Path):
        # Create a chain of includes deeper than MAX_INCLUDE_DEPTH
        for i in range(MAX_INCLUDE_DEPTH + 2):
            path = tmp_path / f"level{i}.md"
            if i < MAX_INCLUDE_DEPTH + 1:
                path.write_text(f"Level {i} @./level{i + 1}.md", encoding="utf-8")
            else:
                path.write_text(f"Level {i} (leaf)", encoding="utf-8")

        result = resolve_includes("Root @./level0.md", tmp_path)
        assert "Root" in result
        assert "Level 0" in result
        # Should not reach the deepest level due to depth limit
        assert f"Level {MAX_INCLUDE_DEPTH + 1}" not in result

    def test_binary_file_rejected(self, tmp_path: Path):
        # Create a binary-looking file
        img = tmp_path / "image.png"
        img.write_bytes(b"\x89PNG fake")

        content = "Content @./image.png"
        result = resolve_includes(content, tmp_path)
        # Should not include binary file
        assert "PNG" not in result

    def test_text_extensions_allowed(self, tmp_path: Path):
        # Create a .py file (text extension)
        py_file = tmp_path / "helper.py"
        py_file.write_text("def hello(): pass", encoding="utf-8")

        content = "Content @./helper.py"
        result = resolve_includes(content, tmp_path)
        assert "def hello" in result

    def test_no_includes_returns_original(self, tmp_path: Path):
        content = "No @-style includes here."
        result = resolve_includes(content, tmp_path)
        assert result == content


class TestTextFileExtensions:
    """Tests for the TEXT_FILE_EXTENSIONS whitelist."""

    def test_common_extensions_included(self):
        assert ".md" in TEXT_FILE_EXTENSIONS
        assert ".py" in TEXT_FILE_EXTENSIONS
        assert ".js" in TEXT_FILE_EXTENSIONS
        assert ".ts" in TEXT_FILE_EXTENSIONS
        assert ".json" in TEXT_FILE_EXTENSIONS
        assert ".yaml" in TEXT_FILE_EXTENSIONS

    def test_binary_extensions_excluded(self):
        assert ".png" not in TEXT_FILE_EXTENSIONS
        assert ".jpg" not in TEXT_FILE_EXTENSIONS
        assert ".pdf" not in TEXT_FILE_EXTENSIONS
        assert ".exe" not in TEXT_FILE_EXTENSIONS
        assert ".zip" not in TEXT_FILE_EXTENSIONS


class TestSkillCatalogWithIncludes:
    """Tests for SkillCatalog integration with @include."""

    def test_skill_with_include(self, tmp_path: Path):
        """SKILL.md with @include should resolve included content."""
        skill_dir = tmp_path / "skills" / "hello"
        skill_dir.mkdir(parents=True)
        script_dir = skill_dir / "script"
        script_dir.mkdir()

        # Create an included file
        docs = skill_dir / "docs.md"
        docs.write_text("# Detailed Documentation\nThis is included.", encoding="utf-8")

        # Create SKILL.md with @include
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: hello\n"
            "description: A hello skill\n"
            "when_to_use: When greeting\n"
            "---\n"
            "Main body content.\n"
            "@./docs.md\n",
            encoding="utf-8",
        )

        # Create script
        (script_dir / "default.py").write_text(
            'async def run(**kwargs):\n    return "Hello!"\n',
            encoding="utf-8",
        )

        catalog = SkillCatalog(tmp_path / "skills")
        catalog.discover()

        skill = catalog.get_skill("hello")
        assert skill is not None
        assert skill.included_content != ""
        assert "Detailed Documentation" in skill.included_content

    def test_skill_without_include(self, tmp_path: Path):
        """SKILL.md without @include should have empty included_content."""
        skill_dir = tmp_path / "skills" / "basic"
        skill_dir.mkdir(parents=True)
        script_dir = skill_dir / "script"
        script_dir.mkdir()

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: basic\n"
            "description: A basic skill\n"
            "when_to_use: Always\n"
            "---\n"
            "No includes here.\n",
            encoding="utf-8",
        )

        (script_dir / "default.py").write_text(
            'async def run(**kwargs):\n    return "Basic"\n',
            encoding="utf-8",
        )

        catalog = SkillCatalog(tmp_path / "skills")
        catalog.discover()

        skill = catalog.get_skill("basic")
        assert skill is not None
        assert skill.included_content == ""
