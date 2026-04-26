"""
SkillTool — invokes skills from the catalog.

The model calls this tool to execute a named skill action.

Architecture (two-layer separation):
  1. Discovery: build_catalog_prompt() injects minimal name+description
     into the system prompt (<available_skills> section).
  2. On-demand: When the model calls this tool for the first time per
     skill, the full SKILL.md body content is prepended to the result
     so the model sees detailed usage guides at execution time — not in
     every system prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openlist_ani.assistant.tool.base import BaseTool

from loguru import logger

if TYPE_CHECKING:
    from openlist_ani.assistant.skill.catalog import SkillCatalog


class SkillTool(BaseTool):
    """Tool that delegates to the SkillCatalog for execution."""

    def __init__(self, catalog: SkillCatalog) -> None:
        self._catalog = catalog
        self._seen_skills: set[str] = set()

    @property
    def name(self) -> str:
        return "skill_tool"

    @property
    def description(self) -> str:
        return (
            "Execute a skill from the available skill catalog. "
            "Use this to invoke specialized capabilities defined as skills."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to invoke.",
                },
                "action": {
                    "type": "string",
                    "description": "Action within the skill to execute (default: 'default').",
                    "default": "default",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters to pass to the skill action.",
                    "default": {},
                },
            },
            "required": ["skill_name"],
        }

    def is_concurrency_safe(self, tool_input: dict | None = None) -> bool:
        """Per-input concurrency: read-only skill actions are safe."""
        if tool_input is None:
            return False
        # Search, query, and calendar actions are read-only;
        # subscribe, update_collection, etc. are not.
        action = str(tool_input.get("action", "default"))
        _READ_ONLY_ACTIONS = {
            "search",
            "calendar",
            "query",
            "subgroups",
            "releases",
            "default",
        }
        return action in _READ_ONLY_ACTIONS

    def prompt(
        self,
        tools: list[BaseTool] | None = None,
    ) -> str:
        """Contribute generic skill_tool usage instructions to the system prompt.

        This prompt is skill-agnostic: it describes *how* to call the tool,
        not *what* each skill does.  Skill-specific descriptions live in
        <available_skills> (via build_catalog_prompt) and full guides are
        injected on-demand by execute() on first invocation per skill.
        """
        skills = self._catalog.all_skills()
        if not skills:
            return ""

        lines = [
            "# Skill Tool Usage Guide",
            "",
            "You have a `skill_tool` that invokes skills from the catalog. "
            "Available skills are listed in the <available_skills> section. "
            "When the user's request matches any available skill, call "
            "`skill_tool` immediately — do not ask for confirmation.",
            "",
            "## Calling Convention",
            "",
            "```",
            'skill_tool(skill_name="<name>", action="<action>", '
            'params={"key": "value"})',
            "```",
            "",
            "## Rules",
            "",
            "- Your FIRST response to a user request MUST be a tool call, "
            "not text. Do NOT say 'let me search' — just call the tool.",
            "- NEVER say you cannot do something if there is a matching "
            "skill. Call the tool.",
            "- If a search returns results and the user's next request "
            "relates to one of them, use IDs from the results — "
            "do NOT ask the user to provide them.",
            "- When a skill result says 'More data available' with a "
            "`_offset` value, call the same action again with "
            "`_offset=<value>` to see the next page.",
        ]
        return "\n".join(lines)

    def _get_skill_body_once(self, skill_name: str) -> str:
        """Return SKILL.md body content the first time a skill is called.

        On the first invocation of each skill, the full SKILL.md body
        (after YAML frontmatter, with @include directives resolved) is
        returned so it can be prepended to the action result.  Subsequent
        calls for the same skill return an empty string to avoid wasting
        tokens on repeated guides.

        Args:
            skill_name: Name of the skill.

        Returns:
            Body content on first call, empty string on subsequent calls.
        """
        if skill_name in self._seen_skills:
            return ""
        self._seen_skills.add(skill_name)
        return self._catalog.get_skill_content(skill_name) or ""

    async def execute(self, **kwargs: object) -> str:
        """Execute a skill action, injecting SKILL.md guide on first call.

        For skills with actions (script/*.py), runs the requested action
        and prepends the SKILL.md body on first invocation.

        For guide-only skills (no script/ directory), returns only the
        SKILL.md body content — these are pure knowledge-injection skills
        that instruct the agent how to orchestrate other skills.

        Args:
            **kwargs: Must include ``skill_name``; optionally ``action``
                and ``params``.

        Returns:
            Action result, optionally prefixed with the skill guide.
        """
        skill_name = str(kwargs.get("skill_name", ""))
        action = str(kwargs.get("action", "default"))
        params = kwargs.get("params", {})
        if not isinstance(params, dict):
            params = {}

        logger.info(f"Skill call: {skill_name}/{action} params={params}")

        try:
            skill = self._catalog.get_skill(skill_name)
            if skill is None:
                logger.warning(f"Skill not found: '{skill_name}'")
                return f"Skill error: Skill '{skill_name}' not found."

            body = self._get_skill_body_once(skill_name)

            # Guide-only skill (no script actions) — return body as knowledge
            if not skill.actions:
                if body:
                    logger.debug(f"Skill '{skill_name}' is guide-only, returning body")
                    return (
                        f"<skill-guide>\n{body}\n</skill-guide>\n\n"
                        "This is a guide-only skill. Follow the instructions "
                        "above and call the referenced API skills to proceed."
                    )
                return f"Skill '{skill_name}' has no actions and no guide content."

            # Skill with actions — execute the requested action
            result = await self._catalog.run_action(skill_name, action, params)
            logger.debug(
                f"Skill '{skill_name}/{action}' completed ({len(result)} chars)"
            )
            if body:
                return f"<skill-guide>\n{body}\n</skill-guide>\n\n{result}"
            return result
        except (ValueError, FileNotFoundError, AttributeError) as e:
            logger.warning(f"Skill '{skill_name}/{action}' error: {e}")
            return f"Skill error: {e}"
        except Exception as e:
            logger.opt(exception=True).error(
                f"Skill '{skill_name}/{action}' unexpected error: {e}"
            )
            return f"Unexpected error running skill '{skill_name}': {e}"
