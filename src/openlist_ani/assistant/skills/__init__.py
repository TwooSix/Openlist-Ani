"""Skills package — standalone domain-specific scripts.

Each skill subpackage contains:
- ``SKILL.md`` — documentation and CLI usage for the agent to read
- ``script/`` — executable Python scripts invoked via
  ``uv run python -m openlist_ani.assistant.skills.<skill>.script.<action>``

Skills are **not** imported or registered in the assistant code.
The LLM discovers them at runtime by searching for SKILL.md files.
"""
