"""
Skill catalog — discovers and manages SKILL.md-based skills.

Supports @include directives:
- Syntax: @path, @./relative/path, @~/home/path, or @/absolute/path
- Included files are resolved relative to the including SKILL.md
- Only text file extensions are allowed (prevents binary file inclusion)
- Circular references prevented via processed path tracking
- MAX_INCLUDE_DEPTH = 5
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from openlist_ani.assistant._constants import (
    CHARS_PER_TOKEN,
    DEFAULT_SKILL_LISTING_BUDGET,
    MAX_LISTING_DESC_CHARS,
    MAX_SKILL_OUTPUT_CHARS,
    MIN_DESC_LENGTH,
    SKILL_BUDGET_CONTEXT_PERCENT,
)

from .loader import load_and_run

from loguru import logger

MAX_INCLUDE_DEPTH = 5

TEXT_FILE_EXTENSIONS = frozenset({
    # Markdown and text
    ".md", ".txt", ".text",
    # Data formats
    ".json", ".yaml", ".yml", ".toml", ".xml", ".csv",
    # Web
    ".html", ".htm", ".css", ".scss",
    # JavaScript/TypeScript
    ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
    # Python
    ".py", ".pyi",
    # Other languages
    ".go", ".rs", ".java", ".kt", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".swift", ".rb", ".php", ".lua", ".r",
    # Shell
    ".sh", ".bash", ".zsh",
    # Config
    ".env", ".ini", ".cfg", ".conf", ".config",
    # Build/misc
    ".sql", ".graphql", ".proto", ".cmake", ".make",
    ".rst", ".adoc", ".org", ".tex",
})


def _resolve_include_path(raw_path: str, base_dir: Path) -> Path | None:
    """Resolve an @include path relative to a base directory.

    - @path or @./path → relative to base_dir
    - @~/path → expand ~ to home directory
    - @/path → absolute path

    Args:
        raw_path: The raw path from the @include directive.
        base_dir: Directory of the file containing the @include.

    Returns:
        Resolved absolute Path, or None if invalid.
    """
    if not raw_path:
        return None

    # Strip fragment identifiers (#heading, etc.)
    hash_idx = raw_path.find("#")
    if hash_idx != -1:
        raw_path = raw_path[:hash_idx]
    if not raw_path:
        return None

    # Expand ~ to home directory
    if raw_path.startswith("~/"):
        resolved = Path(os.path.expanduser(raw_path))
    elif raw_path.startswith("/"):
        resolved = Path(raw_path)
    elif raw_path.startswith("./"):
        resolved = base_dir / raw_path
    else:
        # Plain relative path
        resolved = base_dir / raw_path

    return resolved.resolve()


def _clean_line_for_includes(stripped: str) -> str:
    """Strip inline code spans from a line for @include matching."""
    if "`" in stripped:
        return re.sub(r"`[^`]+`", "", stripped)
    return stripped


# Regex for @path — matches at start of line or after whitespace
_INCLUDE_RE = re.compile(r"(?:^|\s)@((?:[^\s\\]|\\ )+)", re.MULTILINE)
_PATH_START_RE = re.compile(r"^[a-zA-Z0-9._~/-]")


def _resolve_matches_from_line(
    cleaned: str, base_dir: Path,
) -> list[Path]:
    """Extract and resolve all @include paths from a single cleaned line."""
    paths: list[Path] = []
    for match in _INCLUDE_RE.finditer(cleaned):
        raw = match.group(1)
        if not raw:
            continue
        raw = raw.replace("\\ ", " ")
        if not _PATH_START_RE.match(raw):
            continue
        resolved = _resolve_include_path(raw, base_dir)
        if resolved:
            paths.append(resolved)
    return paths


def _extract_include_paths(text: str, base_dir: Path) -> list[Path]:
    """Extract @include paths from text content.

    - Matches @path at word boundaries (not inside code blocks)
    - Resolves paths relative to the containing file's directory

    Args:
        text: The text content to scan for @include directives.
        base_dir: Directory of the file containing the text.

    Returns:
        List of resolved absolute paths.
    """
    paths: list[Path] = []
    in_code_block = False

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        cleaned = _clean_line_for_includes(stripped)
        paths.extend(_resolve_matches_from_line(cleaned, base_dir))

    return paths


def resolve_includes(
    content: str,
    base_dir: Path,
    processed_paths: set[str] | None = None,
    depth: int = 0,
) -> str:
    """Resolve @include directives in content, returning expanded text.

    - Recursively resolves @include paths
    - Prevents circular references via processed_paths set
    - Respects MAX_INCLUDE_DEPTH
    - Only includes text file extensions
    - Non-existent files are silently ignored

    Args:
        content: The text content with potential @include directives.
        base_dir: Directory for resolving relative paths.
        processed_paths: Set of already-processed file paths (for cycle detection).
        depth: Current recursion depth.

    Returns:
        Content with @include directives replaced by included file contents.
    """
    if depth >= MAX_INCLUDE_DEPTH:
        return content

    if processed_paths is None:
        processed_paths = set()

    include_paths = _extract_include_paths(content, base_dir)
    if not include_paths:
        return content

    # Collect included content
    included_parts: list[str] = []

    for path in include_paths:
        path_str = str(path)
        if path_str in processed_paths:
            logger.debug(f"Skipping circular @include: {path}")
            continue

        # Validate file extension
        ext = path.suffix.lower()
        if ext and ext not in TEXT_FILE_EXTENSIONS:
            logger.debug(f"Skipping non-text @include: {path}")
            continue

        if not path.exists() or not path.is_file():
            logger.debug(f"Skipping non-existent @include: {path}")
            continue

        processed_paths.add(path_str)

        try:
            file_content = path.read_text(encoding="utf-8")
            # Recursively resolve includes in the included file
            file_content = resolve_includes(
                file_content,
                path.parent,
                processed_paths,
                depth + 1,
            )
            included_parts.append(
                f"\n\n<!-- @include {path.name} -->\n{file_content}\n"
            )
        except Exception as e:
            logger.warning(f"Failed to read @include {path}: {e}")

    if not included_parts:
        return content

    # Append included content after the main content
    return content + "\n".join(included_parts)


def get_char_budget(context_window_tokens: int | None = None) -> int:
    """Calculate the character budget for skill listings.

    budget = contextWindowTokens × CHARS_PER_TOKEN × 1%

    Args:
        context_window_tokens: Context window size in tokens.
            If None, uses DEFAULT_SKILL_LISTING_BUDGET.

    Returns:
        Character budget for the skill listing.
    """
    if context_window_tokens:
        return int(
            context_window_tokens * CHARS_PER_TOKEN * SKILL_BUDGET_CONTEXT_PERCENT
        )
    return DEFAULT_SKILL_LISTING_BUDGET


def _truncate_description(text: str, max_chars: int = MAX_LISTING_DESC_CHARS) -> str:
    """Truncate a skill description to max_chars, appending '…' if truncated."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


@dataclass
class ActionParam:
    """A parameter for a skill action."""

    name: str
    type_hint: str  # e.g. "str", "int"
    default: str  # e.g. '""', "10"
    description: str  # Extracted from docstring


@dataclass
class SkillAction:
    """A callable action within a skill."""

    name: str  # Script filename (without .py)
    script_path: Path
    description: str = ""  # First line of run()'s docstring
    params: list[ActionParam] = field(default_factory=list)


@dataclass
class SkillEntry:
    """A discovered skill with metadata and actions."""

    name: str
    description: str
    when_to_use: str
    base_dir: Path
    actions: list[SkillAction] = field(default_factory=list)
    included_content: str = ""  # Content from @include directives


class SkillCatalog:
    """Discovers and manages skills from the filesystem.

    Scans skills_dir/*/SKILL.md for YAML frontmatter to build a catalog.
    Each skill directory may contain script/*.py files as actions.
    """

    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir
        self._skills: dict[str, SkillEntry] = {}

    def discover(self) -> None:
        """Scan the skills directory and build the catalog."""
        self._skills.clear()

        if not self._skills_dir.exists():
            logger.debug(f"Skills directory does not exist: {self._skills_dir}")
            return

        for skill_dir in sorted(self._skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            try:
                entry = self._parse_skill(skill_md, skill_dir)
                if entry:
                    self._skills[entry.name] = entry
                    logger.debug(f"Discovered skill: {entry.name} ({len(entry.actions)} actions)")
            except Exception as e:
                logger.warning(f"Failed to parse skill at {skill_dir}: {e}")

    def get_skill(self, name: str) -> SkillEntry | None:
        """Look up a skill by name."""
        return self._skills.get(name)

    def all_skills(self) -> list[SkillEntry]:
        """Return all discovered skills."""
        return list(self._skills.values())

    def build_catalog_prompt(
        self,
        context_window_tokens: int | None = None,
    ) -> str:
        """Generate a system prompt section listing available skills.

        1. Cap each description at MAX_LISTING_DESC_CHARS (250 chars)
        2. Try full descriptions first; if under budget, return as-is
        3. If over budget, calculate per-entry max description length
        4. If per-entry max < MIN_DESC_LENGTH, go names-only

        Args:
            context_window_tokens: Context window size in tokens.
                Used to calculate the character budget (1% of window).
                If None, uses DEFAULT_SKILL_LISTING_BUDGET (8000 chars).

        Returns:
            Formatted string describing all available skills for the model.
        """
        if not self._skills:
            return ""

        budget = get_char_budget(context_window_tokens)

        # Build full entries with descriptions capped at MAX_LISTING_DESC_CHARS
        entries: list[tuple[SkillEntry, str]] = []
        for skill in self._skills.values():
            entry_text = self._format_skill_entry(skill)
            entries.append((skill, entry_text))

        # Check if full listing fits within budget
        full_total = sum(len(text) for _, text in entries)
        if full_total <= budget:
            return "\n".join(text for _, text in entries)

        # Over budget — try truncating descriptions to fit
        # Calculate overhead per entry (name line + action names without descriptions)
        name_overhead = sum(
            len(f"- {skill.name}") for skill, _ in entries
        )
        newline_overhead = len(entries) - 1  # join produces N-1 newlines
        available_for_descs = budget - name_overhead - newline_overhead
        max_desc_len = max(0, available_for_descs // len(entries))

        if max_desc_len < MIN_DESC_LENGTH:
            # Extreme case: names-only listing
            logger.info(
                f"Skill listing budget exhausted ({full_total} > {budget}), "
                f"falling back to names-only"
            )
            return "\n".join(f"- {skill.name}" for skill, _ in entries)

        # Truncate descriptions to fit within budget
        truncated_count = 0
        truncated_entries: list[str] = []
        for skill, _ in entries:
            desc = self._get_skill_description(skill)
            if len(desc) > max_desc_len:
                desc = desc[: max_desc_len - 1] + "…"
                truncated_count += 1
            truncated_entries.append(f"- {skill.name}: {desc}")

        if truncated_count > 0:
            logger.info(
                f"Skill listing: truncated {truncated_count}/{len(entries)} "
                f"descriptions to fit budget ({full_total} → ~{budget} chars)"
            )

        return "\n".join(truncated_entries)

    @staticmethod
    def _get_skill_description(skill: SkillEntry) -> str:
        """Build a combined description string for a skill.

        Combines description and when_to_use, then caps at MAX_LISTING_DESC_CHARS.
        """
        desc = skill.description
        if skill.when_to_use:
            desc = f"{desc} - {skill.when_to_use}"
        return _truncate_description(desc)

    def _format_skill_entry(self, skill: SkillEntry) -> str:
        """Format a single skill entry with full description and actions.

        Each entry includes description (capped at MAX_LISTING_DESC_CHARS),
        action names, and parameter info.
        """
        lines: list[str] = []
        lines.append(f"## Skill: {skill.name}")
        desc = self._get_skill_description(skill)
        lines.append(f"Description: {desc}")
        lines.append("")

        if skill.actions:
            lines.append("Actions:")
            for action in skill.actions:
                self._format_action_lines(action, lines)
        else:
            lines.append("Actions: default")
        lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _format_action_lines(action: SkillAction, lines: list[str]) -> None:
        """Append formatted lines for a single action and its parameters."""
        desc_part = f" — {action.description}" if action.description else ""
        lines.append(f"  - **{action.name}**{desc_part}")
        for p in action.params:
            req = " (required)" if p.default == "_REQUIRED_" else f" (default: {p.default})"
            lines.append(
                f"    - `{p.name}`: "
                f"{p.description or p.type_hint}{req}"
            )

    async def run_action(
        self,
        skill_name: str,
        action: str = "default",
        params: dict | None = None,
    ) -> str:
        """Execute a skill action with automatic paging.

        If the output exceeds ``MAX_SKILL_OUTPUT_CHARS``, only one page
        is returned together with a hint telling the AI how to request
        the next page via the ``_offset`` parameter.

        Args:
            skill_name: Name of the skill.
            action: Action name (script filename without .py).
            params: Parameters to pass to the action's run() function.
                Special key ``_offset`` (int-string) controls paging.

        Returns:
            Action output (at most one page).

        Raises:
            ValueError: If skill or action not found.
        """
        skill = self._skills.get(skill_name)
        if skill is None:
            raise ValueError(f"Skill '{skill_name}' not found.")

        # Extract paging offset (not forwarded to the script)
        safe_params = dict(params) if params else {}
        offset = int(safe_params.pop("_offset", 0))

        # Find the action script
        skill_action = next(
            (a for a in skill.actions if a.name == action),
            None,
        )
        if skill_action is None:
            # Try default script
            default_script = skill.base_dir / "script" / "default.py"
            if default_script.exists() and action == "default":
                result = await load_and_run(default_script, safe_params)
            else:
                available = [a.name for a in skill.actions]
                raise ValueError(
                    f"Action '{action}' not found in skill '{skill_name}'. "
                    f"Available actions: {available}"
                )
        else:
            result = await load_and_run(skill_action.script_path, safe_params)

        # Paging
        total_len = len(result)
        if offset > 0:
            result = result[offset:]

        if len(result) > MAX_SKILL_OUTPUT_CHARS:
            result = result[:MAX_SKILL_OUTPUT_CHARS]
            next_offset = offset + MAX_SKILL_OUTPUT_CHARS
            result += (
                f"\n\n--- Page break (showing {offset + 1}~{next_offset} of "
                f"{total_len} chars) ---\n"
                f"More data available. To see the next page, call this action "
                f"again with the same parameters plus _offset={next_offset}"
            )
        elif offset > 0:
            result = (
                f"--- Continued from offset {offset} "
                f"({offset + 1}~{total_len} of {total_len} chars) ---\n\n"
                + result
            )

        return result

    def _parse_skill(self, skill_md: Path, skill_dir: Path) -> SkillEntry | None:
        """Parse a SKILL.md file with YAML frontmatter.

        Supports @include directives: the body content (after frontmatter) is
        scanned for @path references, which are resolved and appended to the
        skill's included_content.
        """
        content = skill_md.read_text(encoding="utf-8")

        # Extract YAML frontmatter between --- markers
        if not content.startswith("---"):
            return None

        parts = content.split("---", 2)
        if len(parts) < 3:
            return None

        frontmatter = yaml.safe_load(parts[1])
        if not isinstance(frontmatter, dict):
            return None

        name = frontmatter.get("name", skill_dir.name)
        description = frontmatter.get("description", "")
        when_to_use = frontmatter.get("when_to_use", "")

        # Resolve @include directives in the body content
        body_content = parts[2].strip()
        processed_paths: set[str] = {str(skill_md.resolve())}
        resolved_body = resolve_includes(
            body_content,
            skill_md.parent,
            processed_paths=processed_paths,
            depth=0,
        )
        # included_content = everything that was added by @include resolution
        included_content = ""
        if resolved_body != body_content:
            included_content = resolved_body

        # Discover actions from script/*.py and extract parameter info
        actions: list[SkillAction] = []
        script_dir = skill_dir / "script"
        if script_dir.exists():
            for py_file in sorted(script_dir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                action = self._parse_action(py_file)
                actions.append(action)

        return SkillEntry(
            name=name,
            description=description,
            when_to_use=when_to_use,
            base_dir=skill_dir,
            actions=actions,
            included_content=included_content,
        )

    @staticmethod
    def _parse_action(script_path: Path) -> SkillAction:
        """Parse a skill action script to extract parameter metadata.

        Loads the module, inspects run()'s signature and docstring
        to build ActionParam entries.
        """
        action_name = script_path.stem
        action = SkillAction(name=action_name, script_path=script_path)

        try:
            run_fn = _load_run_function(script_path, action_name)
            if run_fn is None:
                return action

            # Extract first line of docstring as action description
            doc = inspect.getdoc(run_fn) or ""
            if doc:
                action.description = doc.split("\n")[0].strip()

            # Extract parameters from signature + docstring
            param_docs = _parse_docstring_args(doc)
            action.params = _extract_action_params(run_fn, param_docs)

        except Exception as e:
            logger.debug(f"Could not introspect {script_path}: {e}")

        return action


def _load_run_function(script_path: Path, action_name: str):
    """Load a module from script_path and return its ``run`` function, or None."""
    spec = importlib.util.spec_from_file_location(
        f"_skill_introspect_{action_name}",
        script_path,
    )
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "run", None)


def _extract_action_params(
    run_fn, param_docs: dict[str, str],
) -> list[ActionParam]:
    """Extract ActionParam entries from a run() function's signature."""
    params: list[ActionParam] = []
    sig = inspect.signature(run_fn)
    empty = inspect.Parameter.empty
    for param_name, param in sig.parameters.items():
        if param_name == "kwargs" or param_name.startswith("_"):
            continue
        ann = param.annotation
        type_hint = "str" if ann == empty else getattr(ann, "__name__", str(ann))
        default = "_REQUIRED_" if param.default == empty else repr(param.default)
        params.append(
            ActionParam(
                name=param_name,
                type_hint=type_hint,
                default=default,
                description=param_docs.get(param_name, ""),
            )
        )
    return params


def _is_docstring_section_end(stripped: str) -> bool:
    """Check if a stripped line marks the end of the current docstring section."""
    if not stripped:
        return False
    if stripped.startswith("-") and stripped.endswith(":"):
        return True
    return ":" not in stripped and stripped.endswith(":")


def _parse_docstring_args(docstring: str) -> dict[str, str]:
    """Parse the Args section of a Google-style docstring.

    Returns a dict of {param_name: description}.
    """
    if not docstring:
        return {}

    result: dict[str, str] = {}
    in_args = False
    for line in docstring.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("args:"):
            in_args = True
            continue
        if not in_args:
            continue
        if _is_docstring_section_end(stripped):
            break
        # Parse "name: description" line
        parts = stripped.lstrip("- ").split(":", 1)
        if len(parts) == 2:
            result[parts[0].strip()] = parts[1].strip().rstrip(".")

    return result
