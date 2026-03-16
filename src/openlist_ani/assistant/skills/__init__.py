"""Skills package — standalone domain-specific scripts.

Each skill subpackage contains:
- ``SKILL.md`` — documentation and usage for the agent to read
- ``script/`` — Python modules exposing an ``async run()`` function,
  executed in-process via the ``run_skill`` tool

Skills are **not** imported or registered in the assistant code.
The LLM discovers them at runtime by searching for SKILL.md files.
"""
