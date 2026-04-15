"""
Claude Code-inspired colour palette and Textual CSS for the TUI frontend.

Background is transparent — uses the terminal's own colours.
"""

from __future__ import annotations

# ── Colour constants ──

# Accent — blue-purple
ACCENT_PRIMARY = "#7c6cff"
ACCENT_SECONDARY = "#a78bfa"
ACCENT_CYAN = "#56b6c2"
# Suggestion — Claude-style light blue-purple for focused items
SUGGESTION = "#b1b9f9"

# Text
TEXT_PRIMARY = "#e0e0e0"
TEXT_DIM = "#555555"
TEXT_DIM_LIGHT = "#888888"

# Status
STATUS_SUCCESS = "#4ade80"
STATUS_ERROR = "#ef4444"
STATUS_WARNING = "#f59e0b"

# Borders
BORDER_COLOR = "#444444"

# ── Textual CSS ──

APP_CSS = """
Screen {
    background: $background;
}

#chat-view {
    height: 1fr;
    overflow-y: auto;
    padding: 1 2;
    scrollbar-size: 1 1;
    scrollbar-color: #444444;
    scrollbar-color-hover: #7c6cff;
    scrollbar-color-active: #a78bfa;
}

#input-wrapper {
    dock: bottom;
    height: auto;
    max-height: 12;
    padding: 0 0 1 0;
}

#top-rule {
    height: 1;
    padding: 0 0;
    color: #444444;
}

#bottom-rule {
    height: 1;
    padding: 0 0;
    color: #444444;
}

#input-row {
    height: auto;
    max-height: 8;
    padding: 0 0;
}

#input-row #prompt-char {
    width: 2;
    height: 1;
    padding: 0;
    color: #56b6c2;
    content-align: left middle;
}

#input-row #command-tag {
    width: auto;
    height: 1;
    padding: 0 1 0 0;
    color: #7c6cff;
}

#input-row #user-input {
    height: auto;
    max-height: 6;
    min-height: 1;
    background: $background;
    border: none;
    padding: 0;
}

#input-row #user-input:focus {
    border: none;
}

.welcome-banner {
    margin-bottom: 1;
}

.msg-block {
    margin: 0;
    padding: 0;
}

.msg-block--user {
    margin-top: 1;
}

.msg-block--assistant {
    margin-top: 0;
}

.msg-block--tool-start {
    margin: 0;
}

.msg-block--tool-end {
    margin: 0;
}

.msg-block--error {
    margin: 0;
}

.msg-block--injected {
    margin: 0;
}

.turn-footer {
    margin-top: 0;
    margin-bottom: 1;
    color: #555555;
}

.thinking-spinner {
    height: 1;
    margin: 0;
    padding: 0;
}

.cmd-result {
    margin: 0;
    padding: 0;
}

#status-bar {
    height: 1;
    dock: bottom;
    padding: 0 2;
    color: #555555;
}

#completion-overlay {
    height: auto;
    max-height: 10;
    padding: 0 0;
    color: #888888;
}

/* ── Session picker modal ── */

SessionPickerScreen {
    align: center middle;
}

#session-picker-container {
    width: 80%;
    max-width: 100;
    height: auto;
    max-height: 80%;
    padding: 1 2;
    border: round #444444;
    background: $surface;
}

#session-picker-header {
    height: 1;
    padding: 0 0 1 0;
    color: #b1b9f9;
}

#session-picker-list {
    height: auto;
    max-height: 60%;
    min-height: 5;
    scrollbar-size: 1 1;
    scrollbar-color: #444444;
    scrollbar-color-hover: #b1b9f9;
}

#session-picker-footer {
    height: 1;
    padding: 1 0 0 0;
    color: #555555;
}
"""
