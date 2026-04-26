"""Tests for CLI frontend UX improvements."""

from __future__ import annotations

import tempfile
from pathlib import Path

from openlist_ani.assistant.core.cancellation import CancellationToken
from openlist_ani.assistant.core.context import ContextBuilder
from openlist_ani.assistant.core.loop import AgenticLoop
from openlist_ani.assistant.core.models import ProviderResponse
from openlist_ani.assistant.frontend.textual_app.app import TextualFrontend
from openlist_ani.assistant.memory.manager import MemoryManager
from openlist_ani.assistant.tool.registry import ToolRegistry
from .conftest import MockProvider


def _make_app() -> TextualFrontend:
    """Helper to create a TextualFrontend for testing."""
    provider = MockProvider([ProviderResponse(text="hello")])
    registry = ToolRegistry()
    tmp = Path(tempfile.mkdtemp())
    memory = MemoryManager(data_dir=tmp / "data", project_root=tmp / "proj")
    (tmp / "proj").mkdir(exist_ok=True)
    context = ContextBuilder(memory)
    loop = AgenticLoop(provider, registry, context, memory)
    return TextualFrontend(loop)


class TestAutoRefocus:
    """Input box should always retain focus."""

    def test_has_on_descendant_blur(self):
        """App should have an on_descendant_blur handler."""
        app = _make_app()
        assert hasattr(app, "on_descendant_blur")

    def test_has_refocus_input(self):
        """App should have a _refocus_input method."""
        app = _make_app()
        assert hasattr(app, "_refocus_input")


class TestEscCancelTurn:
    """ESC should cancel the running agent turn."""

    def test_escape_in_bindings(self):
        """App BINDINGS should include an escape binding."""
        app = _make_app()
        binding_keys = [b[0] if isinstance(b, tuple) else b.key for b in app.BINDINGS]
        assert "escape" in binding_keys

    def test_cancel_turn_action_exists(self):
        """App should have an action_cancel_turn method."""
        app = _make_app()
        assert hasattr(app, "action_cancel_turn")
        assert callable(app.action_cancel_turn)

    def test_cancel_token_attribute_exists(self):
        """App should have a _cancel_token attribute."""
        app = _make_app()
        assert hasattr(app, "_cancel_token")

    def test_cancel_turn_sets_token(self):
        """action_cancel_turn should call cancel() on the token.

        Uses cooperative cancellation: token is cancelled immediately,
        task force-cancel is deferred to a fallback timer.
        """
        from unittest.mock import MagicMock, patch

        app = _make_app()
        mock_token = MagicMock(spec=CancellationToken)
        app._cancel_token = mock_token
        mock_task = MagicMock()
        mock_task.done.return_value = False
        app._processing_task = mock_task

        with patch.object(app, "set_timer"):
            app.action_cancel_turn()

        mock_token.cancel.assert_called_once()


class TestQuitDuringExecution:
    """The /quit command should always exit, even during execution."""

    def test_quit_check_before_processing_guard(self):
        """Verify /quit is checked before the _processing_task guard.

        We inspect the source of on_input_submitted to verify ordering.
        """
        import inspect

        app = _make_app()
        source = inspect.getsource(app.on_input_submitted)
        quit_pos = source.find("/quit")
        processing_pos = source.find("_processing_task")
        assert quit_pos != -1, "/quit check not found in on_input_submitted"
        assert processing_pos != -1, "_processing_task check not found"
        assert (
            quit_pos < processing_pos
        ), "/quit check should come BEFORE _processing_task check"
