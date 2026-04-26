"""Tests for openlist_ani.core.parser.utils module."""

import pytest

from openlist_ani.core.parser.utils import (
    parse_json_array_from_markdown,
    parse_json_from_markdown,
)


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


class TestParseJsonArrayFromMarkdown:
    """Test parse_json_array_from_markdown utility function."""

    # --- Array in markdown code block ---

    def test_array_in_markdown_code_block(self):
        text = '```json\n["Frieren", "Oshi no Ko"]\n```'
        result = parse_json_array_from_markdown(text)
        assert result == '["Frieren", "Oshi no Ko"]'

    def test_array_in_markdown_code_block_with_objects(self):
        text = '```json\n[{"name": "Frieren"}, {"name": "Oshi"}]\n```'
        result = parse_json_array_from_markdown(text)
        assert result == '[{"name": "Frieren"}, {"name": "Oshi"}]'

    def test_array_in_markdown_code_block_with_whitespace(self):
        text = '```json\n  \n["a", "b"]\n  \n```'
        result = parse_json_array_from_markdown(text)
        assert result == '["a", "b"]'

    def test_markdown_code_block_with_object_not_array_falls_through(self):
        """When code block contains a JSON object (not array), fall through
        to bare text search for a bracket-delimited array."""
        text = '```json\n{"key": "value"}\n```\nSome [1, 2, 3] extra'
        result = parse_json_array_from_markdown(text)
        assert result == "[1, 2, 3]"

    def test_markdown_code_block_with_object_only_returns_none(self):
        """Code block has an object and there's no array in bare text either."""
        text = '```json\n{"key": "value"}\n```'
        result = parse_json_array_from_markdown(text)
        # Falls through to bare text: finds '[' inside the code block text
        # but the function searches the full text, so it finds brackets.
        # Actually: text.index("[") would fail since there's no [ outside
        # of the code block — but wait, there's no [ at all in this text
        # except possibly none. Let's verify:
        # The full text is: ```json\n{"key": "value"}\n```
        # No '[' at all → ValueError → returns None
        assert result is None

    # --- Bare JSON array (no code block) ---

    def test_bare_json_array(self):
        text = '["query1", "query2", "query3"]'
        result = parse_json_array_from_markdown(text)
        assert result == '["query1", "query2", "query3"]'

    def test_bare_array_surrounded_by_text(self):
        text = 'Here are the queries: ["Frieren", "葬送のフリーレン"] end.'
        result = parse_json_array_from_markdown(text)
        assert result == '["Frieren", "葬送のフリーレン"]'

    def test_bare_array_with_nested_arrays(self):
        text = "[[1, 2], [3, 4]]"
        result = parse_json_array_from_markdown(text)
        assert result == "[[1, 2], [3, 4]]"

    # --- Empty array ---

    def test_empty_array_in_code_block(self):
        text = "```json\n[]\n```"
        result = parse_json_array_from_markdown(text)
        assert result == "[]"

    def test_empty_array_bare(self):
        text = "The result is: []"
        result = parse_json_array_from_markdown(text)
        assert result == "[]"

    # --- Invalid JSON ---

    def test_invalid_json_content_still_extracts_brackets(self):
        """The function only extracts the string; it doesn't validate JSON."""
        text = "[not valid json at all]"
        result = parse_json_array_from_markdown(text)
        assert result == "[not valid json at all]"

    def test_only_opening_bracket_returns_none(self):
        text = "just a [ without close"
        result = parse_json_array_from_markdown(text)
        # text.index("[") succeeds, text.rindex("]") raises ValueError → None
        assert result is None

    def test_only_closing_bracket_returns_none(self):
        text = "just a ] without open"
        result = parse_json_array_from_markdown(text)
        assert result is None

    # --- None/empty input ---

    def test_empty_string_returns_none(self):
        assert parse_json_array_from_markdown("") is None

    def test_no_brackets_returns_none(self):
        assert parse_json_array_from_markdown("No array here") is None

    # --- Mixed content with arrays ---

    def test_markdown_code_block_takes_priority_over_bare_array(self):
        """Code block array should be preferred over bare text array."""
        text = '[1, 2]\n```json\n["preferred"]\n```'
        result = parse_json_array_from_markdown(text)
        assert result == '["preferred"]'

    def test_multiple_arrays_returns_outermost_brackets(self):
        """Falls through to bare text, picks first '[' to last ']'."""
        text = "[1, 2] middle [3, 4]"
        result = parse_json_array_from_markdown(text)
        assert result == "[1, 2] middle [3, 4]"

    def test_multiline_array_in_code_block(self):
        text = '```json\n[\n  "Frieren",\n  "葬送のフリーレン"\n]\n```'
        result = parse_json_array_from_markdown(text)
        assert '"Frieren"' in result
        assert '"葬送のフリーレン"' in result
        assert result.startswith("[")

    def test_unicode_content_in_array(self):
        text = '["葬送のフリーレン", "Frieren: Beyond Journey\'s End"]'
        result = parse_json_array_from_markdown(text)
        assert "葬送のフリーレン" in result
        assert "Frieren" in result
