"""
Textual TUI frontend package.

Re-exports ``TextualFrontend`` so callers can do::

    from openlist_ani.assistant.frontend.textual_app import TextualFrontend
"""

from openlist_ani.assistant.frontend.textual_app.app import TextualFrontend

__all__ = ["TextualFrontend"]
