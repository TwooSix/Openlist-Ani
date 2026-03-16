"""
Run skill tool — execute domain skills in-process.

Dynamically imports skill modules under
``openlist_ani.assistant.skills.<skill>.script.<action>``
and calls their ``run()`` coroutine directly.
"""

import importlib
import re
from typing import Any

from ...logger import logger
from .base import BaseTool

_MAX_OUTPUT_LEN = 4000

_SKILLS_PACKAGE = "openlist_ani.assistant.skills"

# Only allow alphanumeric, underscore, and dot — no path traversal.
_MODULE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")


def _validate_module(skill_module: str) -> str | None:
    """Validate a skill module path for safety.

    Args:
        skill_module: Relative module path, e.g. ``"bangumi.script.calendar"``.

    Returns:
        Error message if invalid, None if OK.
    """
    if not skill_module:
        return "skill_module must not be empty."

    if ".." in skill_module:
        return "skill_module must not contain '..' (path traversal)."

    if not _MODULE_NAME_RE.match(skill_module):
        return (
            "skill_module contains invalid characters. "
            "Only letters, digits, underscores, and dots are allowed."
        )

    # Must have at least <skill>.script.<action>
    parts = skill_module.split(".")
    if len(parts) < 3 or parts[-2] != "script":
        return (
            "skill_module must follow the pattern "
            "'<skill>.script.<action>', e.g. 'bangumi.script.calendar'."
        )

    return None


class RunSkillTool(BaseTool):
    """Tool for executing domain skill scripts in-process."""

    @property
    def name(self) -> str:
        return "run_skill"

    @property
    def description(self) -> str:
        return (
            "Execute a domain skill in-process. "
            "skill_module is the relative path under "
            f"'{_SKILLS_PACKAGE}', e.g. 'bangumi.script.calendar'. "
            "arguments is a JSON object of keyword arguments "
            "passed to the skill's run() function."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_module": {
                    "type": "string",
                    "description": (
                        "Relative module path under skills package, "
                        "e.g. 'bangumi.script.calendar', "
                        "'oani.script.download', "
                        "'mikan.script.search'."
                    ),
                },
                "arguments": {
                    "type": "object",
                    "description": (
                        "Keyword arguments for the skill's run() "
                        'function, e.g. {"weekday": 1}.'
                    ),
                },
            },
            "required": ["skill_module"],
        }

    async def execute(
        self,
        skill_module: str,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        """Execute a skill module's ``run()`` coroutine.

        Args:
            skill_module: Relative module path, e.g. ``"bangumi.script.calendar"``.
            arguments: Keyword arguments passed to ``run()``.

        Returns:
            Result string from the skill, or error message.
        """
        error = _validate_module(skill_module)
        if error:
            return f"Error: {error}"

        full_module = f"{_SKILLS_PACKAGE}.{skill_module}"
        logger.info(f"RunSkillTool: Importing {full_module}")

        try:
            module = importlib.import_module(full_module)
        except ModuleNotFoundError:
            return f"Error: Skill module '{skill_module}' not found."
        except Exception as exc:
            return f"Error importing skill module: {exc}"

        run_fn = getattr(module, "run", None)
        if run_fn is None or not callable(run_fn):
            return f"Error: Module '{skill_module}' has no callable 'run' function."

        args = arguments or {}
        logger.info(f"RunSkillTool: Calling {full_module}.run({args})")

        try:
            result = await run_fn(**args)
        except TypeError as exc:
            return f"Error: Invalid arguments for '{skill_module}': {exc}"
        except Exception as exc:
            logger.exception(f"RunSkillTool: Error in {full_module}.run()")
            return f"Error executing skill '{skill_module}': {exc}"

        result = str(result)

        if len(result) > _MAX_OUTPUT_LEN:
            result = (
                result[:_MAX_OUTPUT_LEN]
                + f"\n... (truncated, {len(result)} chars total)"
            )

        return result
