import json

from ....logger import logger
from ..model import ParseResult, ResourceTitleParseResult
from ..prompts import build_batch_user_message, BATCH_SYSTEM_PROMPT
from ..utils import parse_json_array_from_markdown
from .client import LLMClient


async def parse_title_batch_via_llm(
    llm: LLMClient, titles: list[str]
) -> list[ParseResult]:
    messages = [
        {"role": "system", "content": BATCH_SYSTEM_PROMPT},
        {"role": "user", "content": build_batch_user_message(titles)},
    ]
    try:
        content = await llm.complete_chat(messages)
        return extract_batch_results(content, len(titles))
    except Exception as e:
        logger.error(f"Batch LLM parsing failed: {e}")
        return [ParseResult(success=False, error=str(e)) for _ in titles]


def extract_batch_results(content: str, expected_count: int) -> list[ParseResult]:
    json_str = parse_json_array_from_markdown(content)

    if not json_str:
        logger.error(
            f"Batch LLM failed to return valid JSON array. Output: {content[:500]}"
        )
        return [
            ParseResult(success=False, error="LLM returned no valid JSON array")
            for _ in range(expected_count)
        ]

    try:
        raw_items = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"Batch JSON decode error: {e}")
        return [
            ParseResult(success=False, error=f"JSON decode error: {e}")
            for _ in range(expected_count)
        ]

    if not isinstance(raw_items, list):
        logger.error(f"Batch LLM returned non-list JSON: {type(raw_items)}")
        return [
            ParseResult(success=False, error="LLM returned non-list JSON")
            for _ in range(expected_count)
        ]

    item_map: dict[int, dict] = {}
    for item in raw_items:
        if isinstance(item, dict) and "index" in item:
            item_map[item["index"]] = item

    return [
        _build_single_result(item_map.get(i + 1), i + 1) for i in range(expected_count)
    ]


def _build_single_result(item: dict | None, index: int) -> ParseResult:
    """Build a ParseResult from a single batch item."""
    if item and item.get("status") == "success":
        try:
            result = ResourceTitleParseResult.model_validate(item)
            return ParseResult(success=True, result=result)
        except Exception as e:
            logger.warning(f"Batch item {index} validation failed: {e}")
            return ParseResult(success=False, error=f"Validation failed: {e}")

    reason = item.get("reason", "unknown") if item else "missing from response"
    logger.debug(f"Batch item {index} failed: {reason}")
    return ParseResult(success=False, error=reason)
