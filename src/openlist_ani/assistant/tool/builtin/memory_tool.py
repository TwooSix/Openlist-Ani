"""
MemoryTool — LLM-facing tool for the directory-based memory system.

Exposes MemoryDir CRUD operations through a single tool with an
``action`` parameter.  Actions: read, write, update, delete, list,
update_index.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from openlist_ani.assistant.memory.memory_dir import ENTRYPOINT_NAME
from openlist_ani.assistant.tool.base import BaseTool

if TYPE_CHECKING:
    from openlist_ani.assistant.memory.memory_dir import MemoryDir

_READ_ONLY_ACTIONS = frozenset({"read", "list"})
_ALL_ACTIONS = frozenset({
    "read", "write", "update", "delete", "list", "update_index",
})


class MemoryTool(BaseTool):
    """Tool that lets the LLM manage persistent memory files.

    Delegates to :class:`MemoryDir` for all file I/O.  Each action
    validates its own required parameters and returns a human-readable
    result string.
    """

    def __init__(self, memory_dir: MemoryDir) -> None:
        self._memory_dir = memory_dir

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return (
            "Manage persistent memory files. Actions: read, write, "
            "update, delete, list, update_index."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": sorted(_ALL_ACTIONS),
                    "description": (
                        "The memory operation to perform."
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Memory filename (e.g. 'user_prefs.md'). "
                        "Required for read/write/update/delete."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "File body content (for write) or new "
                        "MEMORY.md content (for update_index)."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Frontmatter 'name' field (for write)."
                    ),
                },
                "type": {
                    "type": "string",
                    "enum": [
                        "user", "project", "feedback", "reference",
                    ],
                    "description": "Memory type (for write).",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Frontmatter 'description' field (for write)."
                    ),
                },
                "old_str": {
                    "type": "string",
                    "description": (
                        "Text to find (for update). "
                        "Must be unique in the file."
                    ),
                },
                "new_str": {
                    "type": "string",
                    "description": (
                        "Replacement text (for update)."
                    ),
                },
            },
            "required": ["action"],
        }

    def prompt(
        self,
        tools: list[BaseTool] | None = None,
    ) -> str:
        """Generate memory-tool-specific system prompt contribution."""
        return "\n".join([
            "# Memory Tool",
            "",
            "Use the `memory` tool to manage your persistent "
            "memory files.",
            "",
            "## Available Actions",
            "",
            "| Action | Purpose | Required Params |",
            "|--------|---------|-----------------|",
            "| `read` | Read a memory file | `filename` |",
            "| `write` | Create or overwrite a memory file "
            "| `filename`, `content` |",
            "| `update` | Replace text in a memory file "
            "| `filename`, `old_str`, `new_str` |",
            "| `delete` | Delete a memory file | `filename` |",
            "| `list` | List all memory files | (none) |",
            "| `update_index` | Overwrite MEMORY.md index "
            "| `content` |",
            "",
            "## Write Action Optional Params",
            "",
            "When using `write`, you can set frontmatter fields: "
            "`name`, `type` (user/project/feedback/reference), "
            "`description`.",
            "",
            "## Two-Step Save Process",
            "",
            "1. Write the memory file: "
            '`memory(action="write", filename="topic.md", '
            'content="...", name="...", type="...", '
            'description="...")`',
            "2. Update the MEMORY.md index: "
            '`memory(action="update_index", content="- '
            "[Topic](topic.md) "
            "— one-line description\\n...\")`",
        ])

    def is_concurrency_safe(
        self, tool_input: dict | None = None,
    ) -> bool:
        """Read and list are safe; all writes are not."""
        if tool_input is None:
            return False
        action = str(tool_input.get("action", ""))
        return action in _READ_ONLY_ACTIONS

    def is_read_only(
        self, tool_input: dict | None = None,
    ) -> bool:
        """True for read/list actions only."""
        if tool_input is None:
            return False
        action = str(tool_input.get("action", ""))
        return action in _READ_ONLY_ACTIONS

    def user_facing_name(
        self, tool_input: dict | None = None,
    ) -> str:
        """Display name for UI/logging."""
        if tool_input:
            action = tool_input.get("action", "")
            filename = tool_input.get("filename", "")
            if filename:
                return f"memory.{action}({filename})"
            return f"memory.{action}"
        return "memory"

    def get_activity_description(
        self, tool_input: dict | None = None,
    ) -> str | None:
        """Human-readable activity for spinner display."""
        if not tool_input:
            return None
        action = tool_input.get("action", "")
        filename = tool_input.get("filename", "")
        match action:
            case "read":
                return f"Reading memory file {filename}"
            case "write":
                return f"Writing memory file {filename}"
            case "update":
                return f"Updating memory file {filename}"
            case "delete":
                return f"Deleting memory file {filename}"
            case "list":
                return "Listing memory files"
            case "update_index":
                return "Updating MEMORY.md index"
            case _:
                return None

    async def execute(self, **kwargs: object) -> str:
        """Dispatch to the appropriate action handler.

        Args:
            **kwargs: Tool-specific arguments including ``action``
                and action-dependent parameters.

        Returns:
            Human-readable result string.
        """
        action = str(kwargs.get("action", ""))
        if action not in _ALL_ACTIONS:
            return (
                f"Error: Unknown action '{action}'. "
                f"Valid actions: {', '.join(sorted(_ALL_ACTIONS))}"
            )

        match action:
            case "list":
                return await self._action_list()
            case "read":
                return await self._action_read(kwargs)
            case "write":
                return await self._action_write(kwargs)
            case "delete":
                return await self._action_delete(kwargs)
            case "update":
                return await self._action_update(kwargs)
            case "update_index":
                return await self._action_update_index(kwargs)

    # ------------------------------------------------------------------ #
    # Action handlers
    # ------------------------------------------------------------------ #

    async def _action_list(self) -> str:
        """List all memory files with metadata."""
        headers = await self._memory_dir.scan_memory_files()
        return self._memory_dir.format_memory_manifest(headers)

    async def _action_read(self, kwargs: dict) -> str:
        """Read a single memory file.

        Args:
            kwargs: Must contain ``filename``.

        Returns:
            File content or an error message.
        """
        filename = str(kwargs.get("filename", ""))
        if not filename:
            return (
                "Error: 'filename' is required for the read action."
            )

        try:
            content = await asyncio.to_thread(
                self._memory_dir.read_memory, filename,
            )
        except ValueError as exc:
            logger.debug(f"Path traversal blocked for {filename!r}")
            return f"Error: {exc}"

        if not content:
            return (
                f"Error: Memory file '{filename}' does not exist."
            )
        return content

    async def _action_write(self, kwargs: dict) -> str:
        """Write or overwrite a memory file.

        Args:
            kwargs: Must contain ``filename`` and ``content``.
                Optional: ``name``, ``type``, ``description`` for
                frontmatter.

        Returns:
            Success message or an error message.
        """
        filename = str(kwargs.get("filename", ""))
        if not filename:
            return (
                "Error: 'filename' is required for the write action."
            )

        content = str(kwargs.get("content", ""))
        if not content:
            return (
                "Error: 'content' is required for the write action."
            )

        # Build optional frontmatter from flat params
        fm: dict[str, str] | None = None
        fm_name = kwargs.get("name")
        fm_type = kwargs.get("type")
        fm_desc = kwargs.get("description")
        if fm_name or fm_type or fm_desc:
            fm = {}
            if fm_name:
                fm["name"] = str(fm_name)
            if fm_type:
                fm["type"] = str(fm_type)
            if fm_desc:
                fm["description"] = str(fm_desc)

        try:
            await self._memory_dir.write_memory(
                filename, content, frontmatter=fm,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        logger.info(f"MemoryTool wrote: {filename}")
        return (
            f"Saved memory file '{filename}'. "
            "Remember to update MEMORY.md index with "
            'memory(action="update_index", content="...").'
        )

    async def _action_delete(self, kwargs: dict) -> str:
        """Delete a memory file.

        Args:
            kwargs: Must contain ``filename``.

        Returns:
            Success message or an error message.
        """
        filename = str(kwargs.get("filename", ""))
        if not filename:
            return (
                "Error: 'filename' is required for the delete action."
            )

        if filename == ENTRYPOINT_NAME:
            return (
                "Error: Cannot delete MEMORY.md — it is the protected "
                "index file. Use update_index to modify its content."
            )

        # Validate path (raises ValueError on traversal) and check existence.
        try:
            content = self._memory_dir.read_memory(filename)
        except ValueError as exc:
            return f"Error: {exc}"

        # read_memory returns "" for both non-existent and empty files.
        # Check the file list to distinguish.
        if not content and filename not in self._memory_dir.list_filenames():
            return (
                f"Error: Memory file '{filename}' does not exist."
            )

        try:
            await self._memory_dir.delete_memory(filename)
        except ValueError as exc:
            return f"Error: {exc}"

        logger.info(f"MemoryTool deleted: {filename}")
        return (
            f"Deleted memory file '{filename}'. "
            "Remember to update MEMORY.md index with "
            'memory(action="update_index", content="...").'
        )

    async def _action_update(self, kwargs: dict) -> str:
        """Perform str_replace on a memory file.

        Args:
            kwargs: Must contain ``filename``, ``old_str``, and
                ``new_str``.

        Returns:
            Success message or an error message.
        """
        filename = str(kwargs.get("filename", ""))
        if not filename:
            return (
                "Error: 'filename' is required for the update "
                "action."
            )

        old_str = kwargs.get("old_str")
        new_str = kwargs.get("new_str")
        if old_str is None or new_str is None:
            return (
                "Error: 'old_str' and 'new_str' are both required "
                "for the update action."
            )
        old_str = str(old_str)
        new_str = str(new_str)

        if old_str == new_str:
            return "Error: 'old_str' and 'new_str' must differ."

        # Read current content
        try:
            content = self._memory_dir.read_memory(filename)
        except ValueError as exc:
            return f"Error: {exc}"

        if not content:
            return (
                f"Error: Memory file '{filename}' does not exist."
            )

        # Validate unique match
        count = content.count(old_str)
        if count == 0:
            return (
                f"Error: '{old_str}' was not found in "
                f"'{filename}'. Make sure the text matches "
                "exactly."
            )
        if count > 1:
            return (
                f"Error: '{old_str}' appears {count} times in "
                f"'{filename}'. It must be unique (appear exactly "
                "once). Provide more surrounding context to make "
                "the match unique."
            )

        # Perform replacement
        new_content = content.replace(old_str, new_str, 1)

        try:
            await self._memory_dir.write_memory(
                filename, new_content,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        logger.info(f"MemoryTool updated: {filename}")
        return f"Updated memory file '{filename}'."

    async def _action_update_index(self, kwargs: dict) -> str:
        """Overwrite the MEMORY.md index file.

        Args:
            kwargs: Must contain ``content``.

        Returns:
            Success message or an error message.
        """
        content = kwargs.get("content")
        if content is None or str(content).strip() == "":
            return (
                "Error: 'content' is required for the "
                "update_index action."
            )

        await self._memory_dir.update_entrypoint(str(content))
        logger.info("MemoryTool updated MEMORY.md index")
        return "Updated MEMORY.md index."
