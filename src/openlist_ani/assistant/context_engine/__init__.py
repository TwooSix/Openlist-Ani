"""Context Engine — modular context assembly, pruning, and compaction.

Inspired by the OpenClaw framework's layered design:

- **Pruner**: Read-time tool-result trimming (soft trim + hard clear).
- **Compaction**: LLM-driven summarisation of old session turns.
- **MemoryFlush**: Pre-compaction ping that prompts LLM to persist durable memories.
- **SkillCatalog**: Skill discovery + compact prompt generation.
- **PromptBuilder**: Unified system-message assembly pipeline.
- **Settings**: Centralised configuration dataclasses.
"""

from .compaction import SessionCompactor
from .memory_flush import MemoryFlushGuard
from .prompt_builder import ContextPromptBuilder
from .pruner import ParsedTurn, prune_turn_messages
from .settings import CompactionSettings, PruningSettings, SkillCatalogSettings
from .skill_catalog import SkillCatalog

__all__ = [
    "CompactionSettings",
    "ContextPromptBuilder",
    "MemoryFlushGuard",
    "ParsedTurn",
    "PruningSettings",
    "SessionCompactor",
    "SkillCatalog",
    "SkillCatalogSettings",
    "prune_turn_messages",
]
