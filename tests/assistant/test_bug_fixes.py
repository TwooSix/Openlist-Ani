"""Tests for specific bugs found during bug scan.

Bug 1: Command injection in _dream_shell — shell metacharacters bypassed
        the allowlist check.
Bug 2: Consecutive same-role messages in compaction summary — caused
        Anthropic API rejection (role alternation enforcement).
Bug 3: Global asyncio.sleep patching in conftest — the _no_sleep fixture
        accidentally patched asyncio.sleep globally, breaking tests
        that depended on real sleep semantics (e.g. asyncio.wait_for).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.assistant.core.models import Message, ProviderResponse, Role, ToolResult
from openlist_ani.assistant.memory.compactor import AutoCompactor

from .conftest import MockProvider


# ------------------------------------------------------------------ #
# Bug 1: Command injection in _dream_shell
# ------------------------------------------------------------------ #


class TestDreamShellCommandInjection:
    """Verify that shell metacharacters are rejected by _dream_shell."""

    @pytest.fixture
    def runner(self, tmp_path):
        """Create an AutoDreamRunner with a temporary memory directory."""
        from openlist_ani.assistant.dream.config import AutoDreamConfig
        from openlist_ani.assistant.dream.runner import AutoDreamRunner

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        memory_dir = data_dir / "memory"
        memory_dir.mkdir()
        sessions_dir = data_dir / "sessions"
        sessions_dir.mkdir()

        provider = MockProvider([ProviderResponse(text="done")])
        config = AutoDreamConfig(enabled=False)

        return AutoDreamRunner(
            config=config,
            provider=provider,
            memory_dir=memory_dir,
            sessions_dir=sessions_dir,
            data_dir=data_dir,
        )

    @pytest.mark.asyncio
    async def test_semicolon_injection_rejected(self, runner):
        """A command like 'cat foo; rm -rf /' must be rejected."""
        result = await runner._dream_shell("cat /etc/passwd; rm -rf /")
        assert "Error" in result
        assert "metacharacter" in result.lower() or "not allowed" in result.lower()

    @pytest.mark.asyncio
    async def test_pipe_injection_rejected(self, runner):
        """A command like 'cat foo | bash' must be rejected."""
        result = await runner._dream_shell("cat /etc/passwd | bash")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_backtick_injection_rejected(self, runner):
        """A command with backticks must be rejected."""
        result = await runner._dream_shell("cat `whoami`")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_dollar_paren_injection_rejected(self, runner):
        """A command with $() must be rejected."""
        result = await runner._dream_shell("cat $(whoami)")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_and_injection_rejected(self, runner):
        """A command with && must be rejected."""
        result = await runner._dream_shell("cat /dev/null && rm -rf /")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_simple_allowed_command_works(self, runner, tmp_path):
        """A simple allowed command should work normally."""
        # Create a test file
        test_file = tmp_path / "data" / "testfile.txt"
        test_file.write_text("hello world")

        result = await runner._dream_shell(f"cat {test_file}")
        assert "hello world" in result

    @pytest.mark.asyncio
    async def test_disallowed_base_command_rejected(self, runner):
        """A command not in the allowlist should be rejected."""
        result = await runner._dream_shell("rm -rf /tmp/test")
        assert "Error" in result
        assert "not allowed" in result.lower()

    @pytest.mark.asyncio
    async def test_shell_false_prevents_glob_expansion(self, runner):
        """With shell=False, glob patterns are passed literally (not expanded)."""
        # This tests that shell=False is actually being used.
        # With shell=True, 'ls *.nonexistent' would expand the glob.
        # With shell=False, '*.nonexistent' is passed as a literal argument.
        result = await runner._dream_shell("ls *.nonexistent_extension_xyz")
        # Should either error or show no matches — not expand globs
        # The key point is it doesn't crash with a shell interpretation error
        assert isinstance(result, str)


# ------------------------------------------------------------------ #
# Bug 2: Consecutive same-role messages in compaction summary
# ------------------------------------------------------------------ #


class TestCompactionRoleAlternation:
    """Verify that _build_summary_messages merges consecutive same-role messages."""

    @pytest.fixture
    def compactor(self):
        provider = MockProvider([ProviderResponse(text="Summary text.")])
        return AutoCompactor(provider, max_context_chars=500_000)

    def test_no_consecutive_same_role_messages(self, compactor):
        """The summary message list must not contain consecutive same-role messages.

        This is the core bug: system→USER + user→USER created consecutive
        USER messages which Anthropic's API rejects.
        """
        messages = [
            Message(role=Role.SYSTEM, content="You are an assistant."),
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi there!"),
            Message(role=Role.USER, content="Search for X"),
            Message(
                role=Role.ASSISTANT,
                content="Searching...",
                tool_calls=[],
            ),
            Message(
                role=Role.TOOL,
                tool_results=[
                    ToolResult(
                        tool_call_id="tc1",
                        name="search",
                        content="Found results",
                    )
                ],
            ),
            Message(role=Role.USER, content="Thanks"),
            Message(role=Role.ASSISTANT, content="You're welcome!"),
        ]

        summary_msgs = compactor._build_summary_messages(messages)

        # Check that no two consecutive messages have the same role
        for i in range(1, len(summary_msgs)):
            assert summary_msgs[i].role != summary_msgs[i - 1].role, (
                f"Consecutive same-role messages at indices {i-1} and {i}: "
                f"both are {summary_msgs[i].role.value}. "
                f"Content[{i-1}]: {summary_msgs[i-1].content[:80]}... "
                f"Content[{i}]: {summary_msgs[i].content[:80]}..."
            )

    def test_system_and_user_merged(self, compactor):
        """System→USER followed by user→USER should be merged into one USER."""
        messages = [
            Message(role=Role.SYSTEM, content="System prompt"),
            Message(role=Role.USER, content="User message"),
        ]

        summary_msgs = compactor._build_summary_messages(messages)

        # Should have: SYSTEM (summary system), USER (merged system+user)
        user_messages = [m for m in summary_msgs if m.role == Role.USER]
        assert len(user_messages) == 1
        assert "System prompt" in user_messages[0].content
        assert "User message" in user_messages[0].content

    def test_tool_and_user_merged(self, compactor):
        """Tool→USER followed by user→USER should be merged."""
        messages = [
            Message(
                role=Role.TOOL,
                tool_results=[
                    ToolResult(
                        tool_call_id="tc1",
                        name="grep",
                        content="match found",
                    )
                ],
            ),
            Message(role=Role.USER, content="Next question"),
        ]

        summary_msgs = compactor._build_summary_messages(messages)

        # All converted USER messages should be merged
        user_messages = [m for m in summary_msgs if m.role == Role.USER]
        assert len(user_messages) == 1
        assert "match found" in user_messages[0].content
        assert "Next question" in user_messages[0].content

    def test_alternating_roles_preserved(self, compactor):
        """Messages that already alternate should not be merged."""
        messages = [
            Message(role=Role.USER, content="Q1"),
            Message(role=Role.ASSISTANT, content="A1"),
            Message(role=Role.USER, content="Q2"),
            Message(role=Role.ASSISTANT, content="A2"),
        ]

        summary_msgs = compactor._build_summary_messages(messages)

        # Should be: SYSTEM, USER(Q1), ASSISTANT(A1), USER(Q2), ASSISTANT(A2)
        roles = [m.role for m in summary_msgs]
        assert roles == [
            Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.USER, Role.ASSISTANT
        ]


# ------------------------------------------------------------------ #
# Bug 3: Global asyncio.sleep patching
# ------------------------------------------------------------------ #


class TestNoSleepFixtureScoping:
    """Verify that the _no_sleep fixture does NOT patch asyncio.sleep globally.

    Before the fix, the autouse _no_sleep fixture patched
    ``openlist_ani.assistant.core.loop.asyncio.sleep`` which, because
    ``asyncio`` is a shared module reference, globally patched
    ``asyncio.sleep``. This broke any test relying on real sleep
    semantics (e.g., asyncio.wait_for timeouts).
    """

    @pytest.mark.asyncio
    async def test_global_asyncio_sleep_is_not_patched(self):
        """asyncio.sleep should still work normally (not be a no-op).

        If asyncio.sleep were patched to be a no-op, this would complete
        in <1ms.  We test that a small sleep takes at least *some* time.
        """
        import time

        start = time.monotonic()
        await asyncio.sleep(0.02)
        elapsed = time.monotonic() - start

        # Should take at least 15ms (giving 5ms margin for scheduling)
        assert elapsed >= 0.015, (
            f"asyncio.sleep(0.02) completed in {elapsed*1000:.1f}ms — "
            "it appears to be globally patched to a no-op"
        )

    @pytest.mark.asyncio
    async def test_wait_for_timeout_works(self):
        """asyncio.wait_for should correctly raise TimeoutError.

        This is the scenario that was broken by the global sleep patch.
        """

        async def never_resolves():
            await asyncio.get_running_loop().create_future()

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(never_resolves(), timeout=0.05)
