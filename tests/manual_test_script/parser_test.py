"""
Manual parser validation script:
feed RSS resource titles through the full LLM parsing + TMDB mapping pipeline,
then validate whether parser outputs match expected values.

Requires a valid config.toml with LLM and TMDB API keys.

Usage:
    uv run python tests/manual_parser_parse_validation.py
    uv run python tests/manual_parser_parse_validation.py --titles "..." "..."

Note:
    This file is intentionally named without `test_` prefix so pytest will not
    auto-collect it. Run it manually when needed.
"""

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

import openlist_ani.core.parser.parser as parser_module
from openlist_ani.config import config
from openlist_ani.core.parser.llm.client import OpenAILLMClient
from openlist_ani.core.parser.model import ParseResult
from openlist_ani.core.parser.parser import parse_metadata
from openlist_ani.core.website.model import (
    AnimeResourceInfo,
    LanguageType,
    VideoQuality,
)


@dataclass
class PerfStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_api_calls: int = 0
    tmdb_api_calls: int = 0
    tmdb_search_calls: int = 0
    tmdb_details_calls: int = 0
    tmdb_season_episodes_calls: int = 0

    def record_llm(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            self.total_tokens += getattr(usage, "total_tokens", 0) or 0
        self.llm_api_calls += 1

    def record_tmdb(self, api_name: str) -> None:
        self.tmdb_api_calls += 1
        if api_name == "search_tv_show":
            self.tmdb_search_calls += 1
        elif api_name == "get_tv_show_details":
            self.tmdb_details_calls += 1
        elif api_name == "get_season_episodes":
            self.tmdb_season_episodes_calls += 1


_stats: PerfStats | None = None
_OriginalOpenAILLMClient = parser_module.OpenAILLMClient
_OriginalGetTMDBClient = parser_module.get_tmdb_client


class TrackedOpenAILLMClient(OpenAILLMClient):
    async def chat_completion(
        self, messages: list[dict[str, str]], model: str | None = None
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "messages": messages,
        }
        response = await self._client.chat.completions.create(**kwargs)
        if _stats is not None:
            _stats.record_llm(response)
        return response.choices[0].message.content or ""


class TrackedTMDBClientProxy:
    """Proxy TMDB client that counts API invocations while delegating behavior."""

    def __init__(self, wrapped: Any):
        self._wrapped = wrapped

    async def search_tv_show(self, query: str) -> list[dict[str, Any]]:
        if _stats is not None:
            _stats.record_tmdb("search_tv_show")
        return await self._wrapped.search_tv_show(query)

    async def get_tv_show_details(self, tmdb_id: int) -> dict[str, Any]:
        if _stats is not None:
            _stats.record_tmdb("get_tv_show_details")
        return await self._wrapped.get_tv_show_details(tmdb_id)

    async def get_season_episodes(
        self, tmdb_id: int, season_number: int
    ) -> list[dict[str, Any]]:
        if _stats is not None:
            _stats.record_tmdb("get_season_episodes")
        return await self._wrapped.get_season_episodes(tmdb_id, season_number)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def _get_tracked_tmdb_client() -> TrackedTMDBClientProxy:
    return TrackedTMDBClientProxy(_OriginalGetTMDBClient())


def print_perf_stats(stats: PerfStats, elapsed: float) -> None:
    total_api_calls = stats.llm_api_calls + stats.tmdb_api_calls
    print(f"\n{'═' * 72}")
    print("  PERFORMANCE METRICS")
    print(f"{'═' * 72}")
    print("  1) Token 消耗数量")
    print(f"     - Prompt tokens:     {stats.prompt_tokens:,}")
    print(f"     - Completion tokens: {stats.completion_tokens:,}")
    print(f"     - Total tokens:      {stats.total_tokens:,}")
    print("  2) 网络/API 请求次数")
    print(f"     - LLM API calls:     {stats.llm_api_calls}")
    print(f"     - TMDB API calls:    {stats.tmdb_api_calls}")
    print(f"       · search_tv_show:      {stats.tmdb_search_calls}")
    print(f"       · get_tv_show_details: {stats.tmdb_details_calls}")
    print(f"       · get_season_episodes: {stats.tmdb_season_episodes_calls}")
    print(f"     - Total API calls:   {total_api_calls}")
    print("  3) 总耗时")
    print(f"     - Elapsed:           {elapsed:.2f}s")


@dataclass
class ParserValidationCase:
    title: str
    expect_name: Optional[str] = None
    expect_season: Optional[int] = None
    expect_episode: Optional[int] = None
    expect_quality: Optional[VideoQuality] = None
    expect_languages: Optional[list[LanguageType]] = None
    expect_version: Optional[int] = None
    description: str = ""


BUILT_IN_VALIDATION_CASES: list[ParserValidationCase] = [
    ParserValidationCase(
        title="【喵萌奶茶屋】★01月新番★[金牌得主 / Medalist][15][1080p][简日双语][招募翻译]",
        expect_name="金牌得主",
        expect_season=2,
        expect_episode=2,
        expect_quality=VideoQuality.k1080p,
        expect_languages=[LanguageType.kChs, LanguageType.kJp],
        expect_version=1,
        description="Medalist [15] → TMDB S02E02（分季后重计数）",
    ),
    ParserValidationCase(
        title="[ANi] Solo Leveling S02 /  我独自升级 第二季 －起于暗影－ - 14 [1080P][Baha][WEB-DL][AAC AVC][CHT][MP4]",
        expect_name="我独自升级",
        expect_season=1,
        expect_episode=14,
        expect_quality=VideoQuality.k1080p,
        expect_languages=[LanguageType.kCht],
        expect_version=1,
        description="Solo Leveling S02E14（累加编号）→ TMDB S01E14",
    ),
    ParserValidationCase(
        title="[桜都字幕组] 我推的孩子 第三季 /  Oshi no Ko 3rd Season [01][1080p][简体内嵌]",
        expect_name="【我推的孩子】",
        expect_season=1,
        expect_episode=25,
        expect_quality=VideoQuality.k1080p,
        expect_languages=[LanguageType.kChs],
        expect_version=1,
        description="Oshi no Ko S3E01 → TMDB S01E25",
    ),
    ParserValidationCase(
        title="[ANi] Chained Soldier S02 /  魔都精兵的奴隶 第二季 - 07 [1080P][Baha][WEB-DL][AAC AVC][CHT][MP4]",
        expect_name="魔都精兵的奴隶",
        expect_season=2,
        expect_episode=7,
        expect_quality=VideoQuality.k1080p,
        expect_languages=[LanguageType.kCht],
        expect_version=1,
        description="Chained Soldier S02-07 → TMDB S02E08",
    ),
    ParserValidationCase(
        title="[ANi]  安逸领主的愉快领地防卫～用生产系魔术将无名村改造成最强要塞都市～ - 08 [1080P][Baha][WEB-DL][AAC AVC][CHT][MP4]",
        expect_name="安逸领主的愉快领地防卫～以生产系魔术将无名小村打造成最强要塞都市～",
        expect_season=1,
        expect_episode=8,
        expect_quality=VideoQuality.k1080p,
        expect_languages=[LanguageType.kCht],
        expect_version=1,
        description="标题归一化后名称 + S01E08",
    ),
    ParserValidationCase(
        title="[GJ.Y] 香格里拉・开拓异境～粪作猎手挑战神作～ / Shangri-La Frontier - 14.5 (CR 1920x1080 AVC AAC MKV)",
        expect_name="香格里拉边境",
        expect_season=0,
        expect_quality=VideoQuality.k1080p,
        expect_version=1,
    ),
    ParserValidationCase(
        title="[绿茶字幕组] 蘑菇魔女 / Champignon no Majo [05v2][WebRip][1080p][繁日内嵌]",
        expect_name="蘑菇魔女",
        expect_season=1,
        expect_episode=5,
        expect_quality=VideoQuality.k1080p,
        expect_languages=[LanguageType.kCht, LanguageType.kJp],
        expect_version=2,
        description="v2版本, 验证版本号解析",
    ),
]


def make_entry(title: str) -> AnimeResourceInfo:
    return AnimeResourceInfo(title=title, download_url="magnet:?xt=test")


def check_result(tc: ParserValidationCase, pr: ParseResult) -> tuple[bool, list[str]]:
    """Check ParseResult against expected values.

    Note: fansub is intentionally excluded from strict assertions.
    """
    issues: list[str] = []
    if not pr.success or not pr.result:
        issues.append(f"解析失败: {pr.error}")
        return False, issues

    r = pr.result
    if tc.expect_name is not None:
        if tc.expect_name != r.anime_name:
            issues.append(f"名称不匹配: 期望 '{tc.expect_name}', 实际 '{r.anime_name}'")

    if tc.expect_season is not None and r.season != tc.expect_season:
        issues.append(f"季数不匹配: 期望 S{tc.expect_season:02d}, 实际 S{r.season:02d}")

    if tc.expect_episode is not None and r.episode != tc.expect_episode:
        issues.append(
            f"集数不匹配: 期望 E{tc.expect_episode:02d}, 实际 E{r.episode:02d}"
        )

    if tc.expect_quality is not None and r.quality != tc.expect_quality:
        issues.append(f"清晰度不匹配: 期望 {tc.expect_quality}, 实际 {r.quality}")

    if tc.expect_languages is not None:
        actual_languages = r.languages or []
        if sorted(actual_languages) != sorted(tc.expect_languages):
            issues.append(
                f"语言不匹配: 期望 {tc.expect_languages}, 实际 {actual_languages}"
            )

    if tc.expect_version is not None and r.version != tc.expect_version:
        issues.append(f"版本不匹配: 期望 v{tc.expect_version}, 实际 v{r.version}")

    return len(issues) == 0, issues


def format_result(pr: ParseResult) -> str:
    if not pr.success or not pr.result:
        return f"❌ FAILED — {pr.error}"
    r = pr.result
    s = f"S{r.season:02d}" if r.season is not None else "S??"
    e = f"E{r.episode:02d}" if r.episode is not None else "E??"
    return f"{r.anime_name} {s}{e}  (tmdb_id={r.tmdb_id})"


async def run_parser_validation(
    cases: list[ParserValidationCase],
) -> tuple[bool, float]:
    print("═" * 72)
    print("  Manual Parser Parse Validation")
    print("═" * 72)
    print(f"  Model:    {config.llm.openai_model}")
    print(f"  Base URL: {config.llm.openai_base_url}")
    print(f"  Cases:    {len(cases)}")
    print()

    entries = [make_entry(tc.title) for tc in cases]

    print("  ⏳ Running...")
    t0 = time.monotonic()
    results = await parse_metadata(entries, batch_size=len(entries))
    elapsed = time.monotonic() - t0
    print(f"  ✓ Completed in {elapsed:.1f}s")

    print(f"\n{'─' * 72}")
    print("  VALIDATION RESULTS")
    print(f"{'─' * 72}")

    passed = 0
    failed = 0

    for i, (tc, pr) in enumerate(zip(cases, results)):
        idx = f"[{i + 1:2d}]"
        result_str = format_result(pr)
        ok, issues = check_result(tc, pr)

        if ok:
            passed += 1
            status = "✅ PASS"
        else:
            failed += 1
            status = "❌ FAIL"

        print(f"\n  {idx} {status}")
        if tc.description:
            print(f"       描述: {tc.description}")
        print(f"       输入: {tc.title}")
        print(f"       输出: {result_str}")
        if tc.expect_name is not None:
            print(f"       期望名称: {tc.expect_name}")
        if tc.expect_season is not None or tc.expect_episode is not None:
            parts = []
            if tc.expect_season is not None:
                parts.append(f"S{tc.expect_season:02d}")
            if tc.expect_episode is not None:
                parts.append(f"E{tc.expect_episode:02d}")
            print(f"       期望: {''.join(parts)}")
        if tc.expect_quality is not None:
            print(f"       期望清晰度: {tc.expect_quality}")
        if tc.expect_languages is not None:
            print(f"       期望语言: {tc.expect_languages}")
        if tc.expect_version is not None:
            print(f"       期望版本: v{tc.expect_version}")
        if issues:
            for issue in issues:
                print(f"       ⚠ {issue}")

    print(f"\n{'═' * 72}")
    total = passed + failed
    print(
        f"  SUMMARY: {passed}/{total} passed, {failed}/{total} failed  ({elapsed:.1f}s)"
    )
    print(f"{'═' * 72}")

    return failed == 0, elapsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manual parser parse-result validation (LLM + TMDB)"
    )
    parser.add_argument(
        "--titles",
        nargs="+",
        help="Custom RSS titles to parse (no expected-value assertions)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show debug logs from parser (TMDB mapping, cour detection, etc.)",
    )
    return parser.parse_args()


async def main() -> None:
    global _stats
    args = parse_args()

    if args.verbose:
        from openlist_ani.logger import logger as _logger

        _logger.enable("openlist_ani")
        import sys as _sys

        _logger.remove()
        _logger.add(
            _sys.stderr,
            level="DEBUG",
            filter="openlist_ani.core.parser",
            format="<level>{level: <8}</level> | {message}",
        )

    parser_module.OpenAILLMClient = TrackedOpenAILLMClient
    parser_module.get_tmdb_client = _get_tracked_tmdb_client
    try:
        _stats = PerfStats()

        if args.titles:
            validation_cases = [
                ParserValidationCase(title=t, description="(custom input)")
                for t in args.titles
            ]
        else:
            validation_cases = BUILT_IN_VALIDATION_CASES

        all_passed, elapsed = await run_parser_validation(validation_cases)

        if _stats is not None:
            print_perf_stats(_stats, elapsed)

        sys.exit(0 if all_passed else 1)
    finally:
        parser_module.OpenAILLMClient = _OriginalOpenAILLMClient
        parser_module.get_tmdb_client = _OriginalGetTMDBClient


if __name__ == "__main__":
    asyncio.run(main())
