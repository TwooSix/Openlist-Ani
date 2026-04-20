"""File-access security helpers shared by ``read_file`` and ``grep`` tools.

This module is the **only** trusted enforcement point for the file-read /
search tools.  The LLM-facing tools call these helpers to:

1. Resolve a user-supplied path to an absolute, real path that lies inside
   one of the project whitelist directories.
2. Reject paths whose basename matches a sensitive-name blacklist (token,
   secret, credential, .env, .pem, etc.) — even if the path is inside the
   whitelist.
3. Redact common secret patterns from any text before it is handed to the
   LLM.

The security model is *defense-in-depth*: the SOUL prompt also instructs
the model to refuse, but we never trust the model — code rejects first.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from loguru import logger

# ── Whitelist ────────────────────────────────────────────────────────

# Project sub-directories that the file tools may access.  All other paths
# (including the project root itself, /etc, /home, …) are forbidden.
_WHITELIST_DIRS: tuple[str, ...] = (
    "src",
    "skills",
    "data",
    "logs",
    "memory",
)


def _project_root() -> Path:
    """Return the project root used to anchor the whitelist.

    Anchored to ``Path.cwd()`` (matching how the assistant resolves
    ``data_dir`` / ``skills_dir``).  Resolved to an absolute real path.
    """
    return Path.cwd().resolve()


def whitelist_roots() -> list[Path]:
    """Return the absolute paths of all whitelisted directories.

    Non-existent directories are still returned (callers may want to know
    what *would* be accessible if they were created); the caller's
    ``resolve_safe_path`` will reject reads that target non-existent
    files anyway.
    """
    root = _project_root()
    return [(root / name).resolve() for name in _WHITELIST_DIRS]


# ── Sensitive-name blacklist ─────────────────────────────────────────

# Files whose *name* (case-insensitive) suggests they hold credentials.
# Matched against the basename only, after path resolution.  A hit aborts
# the read regardless of whitelist status.
_SENSITIVE_NAME_RE = re.compile(
    r"(?i)("
    r"^\.env(\..*)?$"               # .env, .env.local, .env.production
    r"|^.*secrets?(\..*)?$"         # secret(s).*
    r"|^.*tokens?(\..*)?$"          # token(s).*
    r"|^.*credentials?(\..*)?$"     # credential(s).*
    r"|^.*api[_-]?keys?(\..*)?$"    # api_key(s).*
    r"|^.*passwords?(\..*)?$"       # password(s).*
    r"|^.*private[_-]?keys?(\..*)?$"  # private_key(s).*
    r"|^cookies?\.txt$"             # cookies.txt
    r"|.*\.pem$"
    r"|.*\.key$"
    r"|.*\.pfx$"
    r"|.*\.p12$"
    r"|^id_rsa(\..*)?$"
    r"|^id_ed25519(\..*)?$"
    r"|^id_ecdsa(\..*)?$"
    r"|^id_dsa(\..*)?$"
    r")"
)


class FileAccessDenied(PermissionError):
    """Raised when a path is rejected by the security layer.

    Subclassing :class:`PermissionError` lets the calling tool format a
    consistent error message back to the LLM without leaking internals.
    """


# ── Path resolution ──────────────────────────────────────────────────

def resolve_safe_path(user_path: str) -> Path:
    """Resolve ``user_path`` to an absolute real path inside the whitelist.

    Args:
        user_path: Path supplied by the LLM.  May be relative (resolved
            against the project root) or absolute.

    Returns:
        Absolute :class:`Path` with ``resolve()`` already applied.

    Raises:
        FileAccessDenied: If the path lies outside every whitelist root,
            or if its basename matches the sensitive-name blacklist, or
            if path traversal escapes the project root.
    """
    if not user_path or not isinstance(user_path, str):
        raise FileAccessDenied("Path is required.")

    raw = Path(user_path)
    candidate = raw if raw.is_absolute() else (_project_root() / raw)

    # Resolve symlinks / .. segments.  ``strict=False`` lets callers see a
    # clearer error from the tool layer if the file is simply missing.
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as e:
        raise FileAccessDenied(f"Cannot resolve path: {e}") from e

    roots = whitelist_roots()
    if not any(_is_within(resolved, root) for root in roots):
        whitelist_str = ", ".join(_WHITELIST_DIRS)
        raise FileAccessDenied(
            f"Access denied: path is outside the whitelist. "
            f"Allowed sub-directories: {whitelist_str}."
        )

    if _SENSITIVE_NAME_RE.match(resolved.name):
        raise FileAccessDenied(
            f"Access denied: '{resolved.name}' looks like a credential / "
            f"secret file.  Refusing to read."
        )

    return resolved


def _is_within(path: Path, root: Path) -> bool:
    """Return True iff ``path`` equals ``root`` or is inside its tree."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# ── Secret redaction ─────────────────────────────────────────────────

# Each pattern matches a *whole* token-bearing fragment; the entire match
# is replaced with ``<REDACTED>``.  Order matters only for performance.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # `api_key=...`, `token : ...`, `password = "..."` …
    # Char classes use only the lowercase form because (?i) makes the
    # whole pattern case-insensitive — including [A-Z] would be a
    # duplicate of [a-z].
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?key|secret[_-]?key|token|"
        r"password|passwd|pwd|secret|bearer|authorization|auth[_-]?token)"
        r"\s*[:=]\s*['\"]?[a-z0-9+/=._-]{8,}['\"]?"
    ),
    # HTTP `Authorization: Bearer xxx` headers.
    re.compile(r"(?i)Authorization:\s*[a-z]+\s+[a-z0-9+/=._-]{8,}"),
    # PEM blocks (multi-line; DOTALL).
    re.compile(
        r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----[\s\S]+?"
        r"-----END [A-Z0-9 ]+PRIVATE KEY-----"
    ),
    # AWS access key id.
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # GitHub PATs (ghp_, gho_, ghu_, ghs_, ghr_).
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    # Slack tokens.
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # Telegram bot token: digits:alnum block.
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_\-]{30,}\b"),
    # Generic long base64-ish secret prefixed with `sk-` (OpenAI-style).
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
)

_REDACTED = "<REDACTED>"


def redact_secrets(text: str) -> tuple[str, int]:
    """Replace common secret patterns in ``text`` with ``<REDACTED>``.

    Args:
        text: Raw text to scan.

    Returns:
        Tuple ``(scrubbed_text, hit_count)``.  A non-zero ``hit_count``
        signals that at least one pattern matched; callers may want to
        log a warning so operators notice if a secret is sitting in a
        whitelisted file.
    """
    if not text:
        return text, 0

    hits = 0
    scrubbed = text
    for pat in _SECRET_PATTERNS:
        scrubbed, n = pat.subn(_REDACTED, scrubbed)
        hits += n

    if hits:
        logger.warning(
            f"redact_secrets: redacted {hits} secret-like fragment(s) "
            f"from tool output"
        )

    return scrubbed, hits


# ── Helpers used by tool layer ───────────────────────────────────────

def short_path(path: Path) -> str:
    """Render ``path`` relative to the project root if possible."""
    root = _project_root()
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def is_likely_binary(sample: bytes) -> bool:
    """Heuristic binary-content check used by ``read_file``.

    Treats a file as binary if its first KiB contains a NUL byte or has
    a high ratio of non-printable, non-UTF-8 bytes.
    """
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    text_chars = bytes(range(32, 127)) + b"\n\r\t\b\f"
    nontext = sum(1 for b in sample if b not in text_chars)
    return nontext / len(sample) > 0.30


# Make whitelist constants importable for tests / docs without poking at
# the underscore-prefixed module attribute.
WHITELIST_DIRS = _WHITELIST_DIRS
SENSITIVE_NAME_PATTERN = _SENSITIVE_NAME_RE
__all__ = [
    "FileAccessDenied",
    "WHITELIST_DIRS",
    "SENSITIVE_NAME_PATTERN",
    "is_likely_binary",
    "redact_secrets",
    "resolve_safe_path",
    "short_path",
    "whitelist_roots",
]


# Reference ``os`` so static linters don't warn after future trimming.
_ = os.sep
