"""Tests for openlist_ani.core.parser.utils module."""

import pytest

from openlist_ani.core.parser.utils import parse_json_from_markdown


class TestParseJsonFromMarkdown:
    """Test parse_json_from_markdown utility function."""

    # --- Normal cases ---

    def test_json_in_markdown_code_block(self):
        text = '```json\n{"anime_name": "Test", "season": 1}\n```'
        result = parse_json_from_markdown(text)
        assert result == '{"anime_name": "Test", "season": 1}'

    def test_json_in_markdown_code_block_with_extra_whitespace(self):
        text = '```json\n  \n{"key": "value"}\n  \n```'
        result = parse_json_from_markdown(text)
        assert result == '{"key": "value"}'

    def test_json_in_markdown_multiline(self):
        text = '```json\n{\n  "anime_name": "Frieren",\n  "season": 1,\n  "episode": 5\n}\n```'
        result = parse_json_from_markdown(text)
        assert '"anime_name": "Frieren"' in result
        assert '"season": 1' in result

    def test_plain_json_in_text(self):
        text = 'Here is the result: {"anime_name": "Test", "season": 1}'
        result = parse_json_from_markdown(text)
        assert result == '{"anime_name": "Test", "season": 1}'

    def test_json_surrounded_by_text(self):
        text = 'Some text before {"key": "value"} some text after'
        result = parse_json_from_markdown(text)
        assert result == '{"key": "value"}'

    def test_nested_json_braces(self):
        text = '{"outer": {"inner": "value"}, "list": [1, 2]}'
        result = parse_json_from_markdown(text)
        assert result == '{"outer": {"inner": "value"}, "list": [1, 2]}'

    def test_markdown_code_block_takes_priority_over_bare_json(self):
        """If both code block and bare JSON exist, code block should be preferred."""
        text = '{"ignored": true}\n```json\n{"preferred": true}\n```'
        result = parse_json_from_markdown(text)
        assert result == '{"preferred": true}'

    # --- Edge / boundary cases ---

    def test_empty_string_returns_none(self):
        assert parse_json_from_markdown("") is None

    def test_no_json_returns_none(self):
        assert parse_json_from_markdown("No JSON content here") is None

    def test_only_opening_brace_returns_none(self):
        """Only '{' without '}' → ValueError from rindex → None."""
        assert parse_json_from_markdown("just a { without close") is None

    def test_only_closing_brace_returns_none(self):
        """Only '}' without '{' → ValueError from index → None."""
        assert parse_json_from_markdown("just a } without open") is None

    def test_empty_json_object(self):
        result = parse_json_from_markdown("{}")
        assert result == "{}"

    def test_unicode_content(self):
        text = '{"anime_name": "葬送のフリーレン", "fansub": "喵萌奶茶屋"}'
        result = parse_json_from_markdown(text)
        assert "葬送のフリーレン" in result
        assert "喵萌奶茶屋" in result

    # --- Potential crash / robustness ---

    def test_very_large_input_does_not_crash(self):
        """Ensure no crash or excessive time on large inputs."""
        large_text = "a" * 100_000 + '{"key": "value"}' + "b" * 100_000
        result = parse_json_from_markdown(large_text)
        assert result is not None
        assert '"key": "value"' in result

    def test_none_input_raises_type_error(self):
        """Passing None should raise TypeError, not segfault."""
        with pytest.raises(TypeError):
            parse_json_from_markdown(None)  # type: ignore[arg-type]

    def test_multiple_json_objects_returns_outermost(self):
        """If there are multiple JSON objects, picks from first '{' to last '}'."""
        text = '{"first": 1} middle {"second": 2}'
        result = parse_json_from_markdown(text)
        # Should span from first '{' to last '}'
        assert result == '{"first": 1} middle {"second": 2}'

    def test_markdown_block_empty_json(self):
        text = "```json\n{}\n```"
        result = parse_json_from_markdown(text)
        assert result == "{}"
