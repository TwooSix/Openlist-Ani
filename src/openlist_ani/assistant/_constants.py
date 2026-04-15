"""
Constants for the assistant module.
"""

# Lazily evaluated paths — use via config, not at module import time
DEFAULT_DATA_DIR_NAME = "data/assistant"
DEFAULT_SKILLS_DIR_NAME = "skills"

MAX_TOOL_ROUNDS = 15
MAX_TOOL_USE_CONCURRENCY = 10  # Max concurrent tool executions
MAX_SKILL_OUTPUT_CHARS = 16_000
DEFAULT_MAX_CONTEXT_CHARS = 512_000  # ~128K tokens

# Tool result truncation
MAX_TOOL_RESULT_CHARS = 50_000  # Per-result cap before truncation
MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200_000  # Aggregate cap per message

# Autocompact thresholds
AUTOCOMPACT_RESERVED_OUTPUT_TOKENS = 20_000  # Reserved for model output
AUTOCOMPACT_BUFFER_TOKENS = 13_000  # Safety buffer
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3  # Circuit breaker

# Post-compact restoration limits
POST_COMPACT_MAX_FILES_TO_RESTORE = 5
POST_COMPACT_TOKEN_BUDGET = 50_000
POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000

# Skill listing budget
SKILL_BUDGET_CONTEXT_PERCENT = 0.01  # 1% of context window
CHARS_PER_TOKEN = 4
DEFAULT_SKILL_LISTING_BUDGET = 8_000  # Fallback: 1% of 200k × 4
MAX_LISTING_DESC_CHARS = 250  # Per-entry description cap
MIN_DESC_LENGTH = 20  # Below this, go names-only

# Error recovery
MAX_API_RETRIES = 3  # Max retries on transient API errors
API_RETRY_BACKOFF_BASE = 1.0  # Seconds base for exponential backoff
INTERRUPTED_TEXT = "(interrupted)"  # Standard text for cancellation events

# Max output tokens recovery
MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3  # Max continue-message injections
MAX_OUTPUT_TOKENS_CONTINUE_MESSAGE = (
    "Output token limit hit. Resume directly — no apology, no recap of what "
    "you were doing. Pick up mid-thought if that is where the cut happened. "
    "Break remaining work into smaller pieces."
)

# Dynamic max_tokens
# Capped default for slot-reservation optimization (uses 8k cap + 64k escalation)
DEFAULT_MAX_OUTPUT_TOKENS = 16_384  # Default for unknown models
CAPPED_DEFAULT_MAX_TOKENS = 8_000  # Conservative cap
ESCALATED_MAX_TOKENS = 64_000  # Escalation target on max_tokens hit

# Model-specific max output tokens
MODEL_MAX_OUTPUT_TOKENS: dict[str, tuple[int, int]] = {
    # (default, upper_limit)
    "claude-opus-4-6": (64_000, 128_000),
    "claude-sonnet-4-6": (32_000, 128_000),
    "claude-opus-4-5": (32_000, 64_000),
    "claude-sonnet-4": (32_000, 64_000),
    "claude-haiku-4": (32_000, 64_000),
    "claude-sonnet-4-20250514": (32_000, 64_000),
    "gpt-4o": (16_384, 16_384),
    "gpt-4o-mini": (16_384, 16_384),
    "gpt-4-turbo": (4_096, 4_096),
    "o1": (100_000, 100_000),
    "o3": (100_000, 100_000),
    "deepseek-chat": (8_192, 8_192),
    "deepseek-reasoner": (8_192, 8_192),
}

# Provider defaults
DEFAULT_TEMPERATURE = 1.0
PROVIDER_TIMEOUT_SECONDS = 600  # 10 minutes
PROVIDER_MAX_RETRIES = 3  # SDK-level retry count

# Preserved tail compaction
PRESERVED_TAIL_MIN_MESSAGES = 2  # Minimum messages to preserve in tail

# Memory directory constants
MAX_ENTRYPOINT_LINES = 200  # Max lines in MEMORY.md index
MAX_ENTRYPOINT_BYTES = 25_000  # Max bytes in MEMORY.md index (~25KB)
MAX_MEMORY_FILES = 200  # Max individual memory topic files
FRONTMATTER_MAX_LINES = 30  # Max lines for YAML frontmatter block

# Auto-dream constants
DREAM_MAX_ROUNDS = 20  # Max LLM rounds for dream consolidation loop
DREAM_SCAN_INTERVAL_SECONDS = 600  # 10 minutes between session scans
DREAM_LOCK_STALE_SECONDS = 3_600  # 1 hour — consider lock holder stale
