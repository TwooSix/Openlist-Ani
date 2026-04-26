"""Tests for skill catalog discovery and execution."""

import pytest
from pathlib import Path

from openlist_ani.assistant._constants import (
    DEFAULT_SKILL_LISTING_BUDGET,
    MAX_LISTING_DESC_CHARS,
)
from openlist_ani.assistant.skill.catalog import (
    SkillCatalog,
    _truncate_description,
    get_char_budget,
)


@pytest.fixture
def skill_dir(tmp_path: Path):
    """Create a temporary skill directory structure."""
    skill_base = tmp_path / "skills" / "hello"
    skill_base.mkdir(parents=True)

    # Write SKILL.md with YAML frontmatter
    skill_md = skill_base / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: hello\n"
        "description: A hello world skill\n"
        "when_to_use: When user asks for a greeting\n"
        "---\n"
        "\n"
        "# Hello Skill\n"
        "This skill says hello.\n"
    )

    # Create script directory with an action
    script_dir = skill_base / "script"
    script_dir.mkdir()
    (script_dir / "greet.py").write_text(
        "async def run(name='World', **kwargs):\n    return f'Hello, {name}!'\n"
    )
    (script_dir / "default.py").write_text(
        "def run(**kwargs):\n    return 'Hello from default!'\n"
    )

    return tmp_path / "skills"


@pytest.fixture
def empty_skill_dir(tmp_path: Path):
    """Create an empty skill directory."""
    d = tmp_path / "empty_skills"
    d.mkdir()
    return d


class TestSkillCatalog:
    def test_discover_skills(self, skill_dir: Path):
        from openlist_ani.assistant.skill.catalog import SkillCatalog

        catalog = SkillCatalog(skill_dir)
        catalog.discover()

        skills = catalog.all_skills()
        assert len(skills) == 1
        assert skills[0].name == "hello"
        assert skills[0].description == "A hello world skill"
        assert len(skills[0].actions) == 2

    def test_discover_empty_dir(self, empty_skill_dir: Path):
        from openlist_ani.assistant.skill.catalog import SkillCatalog

        catalog = SkillCatalog(empty_skill_dir)
        catalog.discover()
        assert catalog.all_skills() == []

    def test_discover_nonexistent_dir(self, tmp_path: Path):
        from openlist_ani.assistant.skill.catalog import SkillCatalog

        catalog = SkillCatalog(tmp_path / "nonexistent")
        catalog.discover()
        assert catalog.all_skills() == []

    def test_get_skill(self, skill_dir: Path):
        from openlist_ani.assistant.skill.catalog import SkillCatalog

        catalog = SkillCatalog(skill_dir)
        catalog.discover()

        assert catalog.get_skill("hello") is not None
        assert catalog.get_skill("nonexistent") is None

    def test_build_catalog_prompt(self, skill_dir: Path):
        from openlist_ani.assistant.skill.catalog import SkillCatalog

        catalog = SkillCatalog(skill_dir)
        catalog.discover()

        prompt = catalog.build_catalog_prompt()
        assert "hello" in prompt
        assert "A hello world skill" in prompt
        assert "greet" in prompt

    def test_build_catalog_prompt_empty(self, empty_skill_dir: Path):
        from openlist_ani.assistant.skill.catalog import SkillCatalog

        catalog = SkillCatalog(empty_skill_dir)
        catalog.discover()
        assert catalog.build_catalog_prompt() == ""

    @pytest.mark.asyncio
    async def test_run_action(self, skill_dir: Path):
        from openlist_ani.assistant.skill.catalog import SkillCatalog

        catalog = SkillCatalog(skill_dir)
        catalog.discover()

        result = await catalog.run_action("hello", "greet", {"name": "Alice"})
        assert result == "Hello, Alice!"

    @pytest.mark.asyncio
    async def test_run_default_action(self, skill_dir: Path):
        from openlist_ani.assistant.skill.catalog import SkillCatalog

        catalog = SkillCatalog(skill_dir)
        catalog.discover()

        result = await catalog.run_action("hello", "default")
        assert result == "Hello from default!"

    @pytest.mark.asyncio
    async def test_run_action_unknown_skill(self, skill_dir: Path):
        from openlist_ani.assistant.skill.catalog import SkillCatalog

        catalog = SkillCatalog(skill_dir)
        catalog.discover()

        with pytest.raises(ValueError, match="not found"):
            await catalog.run_action("unknown_skill", "default")

    @pytest.mark.asyncio
    async def test_run_action_unknown_action(self, skill_dir: Path):
        from openlist_ani.assistant.skill.catalog import SkillCatalog

        catalog = SkillCatalog(skill_dir)
        catalog.discover()

        with pytest.raises(ValueError, match="not found"):
            await catalog.run_action("hello", "nonexistent_action")


class TestGetCharBudget:
    def test_default_budget(self):
        """Without context window, returns DEFAULT_SKILL_LISTING_BUDGET."""
        assert get_char_budget() == DEFAULT_SKILL_LISTING_BUDGET
        assert get_char_budget(None) == DEFAULT_SKILL_LISTING_BUDGET

    def test_budget_with_context_window(self):
        """Budget = contextWindowTokens × 4 × 1%."""
        # 128k tokens → 128_000 × 4 × 0.01 = 5120
        assert get_char_budget(128_000) == 5120

    def test_budget_with_200k_window(self):
        """200k tokens → 200_000 × 4 × 0.01 = 8000."""
        assert get_char_budget(200_000) == 8000


class TestTruncateDescription:
    def test_short_description_unchanged(self):
        """Description under limit is returned as-is."""
        assert _truncate_description("Short desc") == "Short desc"

    def test_exact_limit_unchanged(self):
        """Description exactly at limit is returned as-is."""
        text = "x" * MAX_LISTING_DESC_CHARS
        assert _truncate_description(text) == text

    def test_over_limit_truncated_with_ellipsis(self):
        """Description over limit is truncated with ellipsis."""
        text = "x" * (MAX_LISTING_DESC_CHARS + 100)
        result = _truncate_description(text)
        assert len(result) == MAX_LISTING_DESC_CHARS
        assert result.endswith("…")

    def test_custom_limit(self):
        """Custom max_chars works."""
        result = _truncate_description("Hello World!", max_chars=8)
        assert len(result) == 8
        assert result.endswith("…")


class TestBuildCatalogPromptBudget:
    """Tests for skill listing budget enforcement in build_catalog_prompt()."""

    @pytest.fixture
    def catalog_with_many_skills(self, tmp_path: Path):
        """Create a catalog with many skills to exceed default budget."""
        skills_dir = tmp_path / "skills"
        for i in range(50):
            skill_dir = skills_dir / f"skill_{i:03d}"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\n"
                f"name: skill_{i:03d}\n"
                f"description: {'A very long description that goes on and on. ' * 10}\n"
                f"when_to_use: {'Use when doing task number {i} which is very important. ' * 5}\n"
                f"---\n"
            )
        catalog = SkillCatalog(skills_dir)
        catalog.discover()
        return catalog

    @pytest.fixture
    def catalog_with_few_skills(self, tmp_path: Path):
        """Create a catalog with a few small skills."""
        skills_dir = tmp_path / "skills"
        for i in range(3):
            skill_dir = skills_dir / f"skill_{i}"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: skill_{i}\ndescription: Short desc {i}\n---\n"
            )
        catalog = SkillCatalog(skills_dir)
        catalog.discover()
        return catalog

    def test_under_budget_no_truncation(self, catalog_with_few_skills):
        """Small catalog within budget returns full entries."""
        prompt = catalog_with_few_skills.build_catalog_prompt()
        assert prompt != ""
        # Should contain full format with ## headers
        assert "## Skill:" in prompt
        assert "Description:" in prompt

    def test_over_budget_descriptions_truncated(self, catalog_with_many_skills):
        """Large catalog exceeding budget gets descriptions truncated."""
        # Use a small context window to force truncation
        prompt = catalog_with_many_skills.build_catalog_prompt(
            context_window_tokens=10_000  # 10k tokens → budget = 400 chars
        )
        assert prompt != ""
        # Should be in truncated format (- name: desc)
        assert "## Skill:" not in prompt
        # All 50 skills should still be listed
        for i in range(50):
            assert f"skill_{i:03d}" in prompt

    def test_extreme_budget_names_only(self, catalog_with_many_skills):
        """Extremely small budget falls back to names-only."""
        # 1000 tokens → budget = 40 chars, way too small for 50 skills with descs
        prompt = catalog_with_many_skills.build_catalog_prompt(
            context_window_tokens=1_000
        )
        assert prompt != ""
        # Should be names-only: "- skill_xxx" format
        lines = prompt.strip().split("\n")
        for line in lines:
            assert line.startswith("- skill_")
            # Names-only means no ": description" part
            assert ":" not in line

    def test_description_capped_at_max_listing_desc_chars(self, tmp_path: Path):
        """Individual descriptions are capped at MAX_LISTING_DESC_CHARS."""
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "verbose"
        skill_dir.mkdir(parents=True)
        very_long = "x" * 1000
        (skill_dir / "SKILL.md").write_text(
            f"---\n"
            f"name: verbose\n"
            f"description: {very_long}\n"
            f"when_to_use: {very_long}\n"
            f"---\n"
        )
        catalog = SkillCatalog(skills_dir)
        catalog.discover()

        # Even with a huge budget, per-description cap applies
        prompt = catalog.build_catalog_prompt(context_window_tokens=1_000_000)
        # The combined description+when_to_use (2000+ chars) should be capped
        # at MAX_LISTING_DESC_CHARS in the Description: line
        desc_line = [
            line for line in prompt.split("\n") if line.startswith("Description:")
        ][0]
        desc_content = desc_line[len("Description: ") :]
        assert len(desc_content) <= MAX_LISTING_DESC_CHARS
        assert desc_content.endswith("…")

    def test_default_budget_used_when_no_tokens(self, catalog_with_many_skills):
        """Without context_window_tokens, DEFAULT_SKILL_LISTING_BUDGET is used."""
        prompt_default = catalog_with_many_skills.build_catalog_prompt()
        prompt_explicit = catalog_with_many_skills.build_catalog_prompt(
            context_window_tokens=None
        )
        assert prompt_default == prompt_explicit

    def test_empty_catalog_returns_empty(self, tmp_path: Path):
        """Empty catalog returns empty string regardless of budget."""
        catalog = SkillCatalog(tmp_path / "empty")
        catalog.discover()
        assert catalog.build_catalog_prompt(context_window_tokens=128_000) == ""

    def test_when_to_use_combined_in_description(self, tmp_path: Path):
        """Description and when_to_use are combined."""
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "combined"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: combined\n"
            "description: A test skill\n"
            "when_to_use: When testing stuff\n"
            "---\n"
        )
        catalog = SkillCatalog(skills_dir)
        catalog.discover()

        prompt = catalog.build_catalog_prompt()
        # Description should combine both fields
        assert "A test skill - When testing stuff" in prompt
