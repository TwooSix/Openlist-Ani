"""Tests for the Bangumi latest_episode builtin skill action."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from openlist_ani.assistant.skill.catalog import SkillCatalog


def test_bangumi_catalog_exposes_latest_episode_action() -> None:
    """The builtin Bangumi skill advertises latest_episode to the assistant."""
    skills_dir = Path(__file__).parents[2] / "src/openlist_ani/builtin_skills/skills"
    catalog = SkillCatalog(skills_dir)
    catalog.discover()

    bangumi = catalog.get_skill("bangumi")
    assert bangumi is not None
    assert "latest_episode" in {action.name for action in bangumi.actions}


@pytest.mark.asyncio
async def test_latest_episode_reports_latest_aired_main_episode(monkeypatch) -> None:
    """latest_episode selects the newest episode whose airdate is not future."""
    module = importlib.import_module(
        "openlist_ani.builtin_skills.skills.bangumi.script.latest_episode",
    )

    class FakeBangumiClient:
        def __init__(self, access_token: str = "") -> None:
            self.access_token = access_token

        async def fetch_subject(self, subject_id: int):
            assert subject_id == 377130
            return SimpleNamespace(
                id=subject_id,
                display_name="尖帽子的魔法工房",
            )

        async def fetch_subject_episodes(self, subject_id: int, episode_type: int = 0):
            assert subject_id == 377130
            assert episode_type == 0
            return [
                {
                    "id": 1656038,
                    "type": 0,
                    "name": "誰が為の魔法",
                    "name_cn": "魔法为谁而放",
                    "sort": 7,
                    "ep": 7,
                    "airdate": "2026-05-18",
                },
                {
                    "id": 1656040,
                    "type": 0,
                    "name": "黒に沈む悪夢",
                    "name_cn": "",
                    "sort": 9,
                    "ep": 9,
                    "airdate": "2026-06-01",
                },
                {
                    "id": 1656039,
                    "type": 0,
                    "name": "魔警団の疑念",
                    "name_cn": "",
                    "sort": 8,
                    "ep": 8,
                    "airdate": "2026-05-25",
                },
            ]

        async def close(self) -> None:
            return None

    monkeypatch.setattr(module, "BangumiClient", FakeBangumiClient)

    result = await module.run(subject_id="377130", as_of_date="2026-05-26")

    assert "# Latest aired episode for 尖帽子的魔法工房" in result
    assert "As of: 2026-05-26" in result
    assert "Episode: ep.8" in result
    assert "Episode ID: 1656039" in result
    assert "Airdate: 2026-05-25" in result
    assert "Title: 魔警団の疑念" in result


@pytest.mark.asyncio
async def test_latest_episode_reports_no_aired_episode(monkeypatch) -> None:
    """latest_episode explains when all known main episodes are in the future."""
    module = importlib.import_module(
        "openlist_ani.builtin_skills.skills.bangumi.script.latest_episode",
    )

    class FakeBangumiClient:
        def __init__(self, access_token: str = "") -> None:
            pass

        async def fetch_subject(self, subject_id: int):
            return SimpleNamespace(id=subject_id, display_name="未开播动画")

        async def fetch_subject_episodes(self, subject_id: int, episode_type: int = 0):
            return [
                {
                    "id": 1,
                    "type": 0,
                    "name": "第1话",
                    "sort": 1,
                    "ep": 1,
                    "airdate": "2026-06-01",
                },
            ]

        async def close(self) -> None:
            return None

    monkeypatch.setattr(module, "BangumiClient", FakeBangumiClient)

    result = await module.run(subject_id="123", as_of_date="2026-05-26")

    assert (
        "No aired main-story episodes found for 未开播动画 as of 2026-05-26." in result
    )
    assert "Known main-story episodes: 1" in result
