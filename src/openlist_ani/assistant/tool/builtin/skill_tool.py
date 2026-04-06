"""
SkillTool — invokes skills from the catalog.

The model calls this tool to execute a named skill action.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openlist_ani.assistant.tool.base import BaseTool

if TYPE_CHECKING:
    from openlist_ani.assistant.skill.catalog import SkillCatalog


class SkillTool(BaseTool):
    """Tool that delegates to the SkillCatalog for execution."""

    def __init__(self, catalog: SkillCatalog) -> None:
        self._catalog = catalog

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
        _READ_ONLY_ACTIONS = {"search", "calendar", "query", "subgroups", "episodes", "default"}
        return action in _READ_ONLY_ACTIONS

    async def prompt(
        self,
        tools: list[BaseTool] | None = None,
    ) -> str:
        """Contribute skill usage instructions to the system prompt.

        Injects detailed calling conventions, intent-to-skill mapping,
        and behavioral rules so the model knows exactly when and how
        to call skills.
        """
        skills = self._catalog.all_skills()
        if not skills:
            return ""

        lines = [
            "# Skill Tool Usage Guide",
            "",
            "You have a `skill_tool` that invokes skills from the catalog. "
            "ALWAYS call this tool directly when the user's intent matches a skill. "
            "NEVER ask the user for confirmation or clarification if you can "
            "determine the intent.",
            "",
            "## Calling Convention",
            "",
            "```",
            "skill_tool(skill_name=\"mikan\", action=\"search\", "
            "params={\"keyword\": \"hell's paradise\"})",
            "```",
            "",
            "## Intent Mapping (Common User Requests -> Skill Calls)",
            "",
            "| User intent | skill_name | action | key params |",
            "|---|---|---|---|",
            "| search anime by name | mikan | search | keyword |",
            "| list fansub groups for anime | mikan | subgroups | bangumi_id |",
            "| list episodes for a fansub group | mikan | episodes | bangumi_id, group_id |",
            "| find / download a specific episode | mikan | subgroups → episodes | bangumi_id |",
            "| subscribe to anime releases | mikan | subscribe | bangumi_id |",
            "| unsubscribe from anime | mikan | unsubscribe | bangumi_id |",
            "| get anime details / rating | bangumi | subject_detail | subject_id |",
            "| weekly airing schedule | bangumi | calendar | (none) |",
            "| user watch list / collection | bangumi | user_collections | type |",
            "| download a torrent | oani | create_download | url |",
            "| check download status | oani | list_downloads | (none) |",
            "| query downloaded library | oani | query_library | sql |",
            "",
            "## Episode Identification & Download Workflow",
            "",
            "When the user asks about or wants to download a specific episode:",
            "1. Call mikan/subgroups to list all fansub groups for the bangumi.",
            "2. Pick a suitable group (prefer groups with more episodes, "
            "or one the user specifies).",
            "3. Call mikan/episodes with the chosen group_id to get episode "
            "releases with full magnet links.",
            "4. Carefully read EACH title to identify which entry matches "
            "the requested episode number. Pay close attention — titles "
            "may contain similar numbers for resolution (1080), season, "
            "or batch ranges. The episode number is typically after "
            "a dash '- XX' or in brackets '[XX]'.",
            "5. ONLY use the magnet link from the entry whose title "
            "clearly contains the exact requested episode number.",
            "6. Call oani/create_download with the correct magnet link.",
            "",
            "CRITICAL RULES for episode selection:",
            "- NEVER guess or pick the nearest episode. If you cannot "
            "confidently identify the exact episode in any title, "
            "tell the user it's not available in the current list.",
            "- NEVER confuse episode numbers with resolution (1080p), "
            "season numbers, or version numbers (v2).",
            "- If the user corrects you ('I said 24, not 25'), re-read "
            "the episodes results carefully and find the correct entry.",
            "",
            "## Rules",
            "",
            "- Your FIRST response to a user request MUST be a tool call, not "
            "text. Do NOT say 'let me search' or 'I will look that up' -- "
            "just call the tool.",
            "- If the user mentions an anime name, FIRST search for it "
            "(mikan/search), THEN use the bangumi_id from results for "
            "follow-up actions (subgroups, subscribe).",
            "- NEVER say you cannot do something if there is a matching skill. "
            "Call the tool.",
            "- If a search returns results and the user's next request relates "
            "to one of them, use the bangumi_id from the search results -- "
            "do NOT ask the user to provide it.",
            "- When uncertain between skills, prefer mikan for "
            "download/subscription tasks and bangumi for "
            "information/collection tasks.",
            "- When subgroups results include magnet links, you can use them "
            "directly with oani/create_download to download specific episodes.",
            "- When a skill result says 'More data available' with a "
            "`_offset` value, call the same skill again with the same "
            "params plus `_offset=<value>` to see the next page. Use "
            "this when you cannot find the target data in the current page.",
        ]
        return "\n".join(lines)

    async def execute(self, **kwargs: object) -> str:
        skill_name = str(kwargs.get("skill_name", ""))
        action = str(kwargs.get("action", "default"))
        params = kwargs.get("params", {})
        if not isinstance(params, dict):
            params = {}

        try:
            return await self._catalog.run_action(skill_name, action, params)
        except (ValueError, FileNotFoundError, AttributeError) as e:
            return f"Skill error: {e}"
        except Exception as e:
            return f"Unexpected error running skill '{skill_name}': {e}"
