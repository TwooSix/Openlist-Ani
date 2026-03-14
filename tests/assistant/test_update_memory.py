"""Tests for the three memory tools and first-time user onboarding flow."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openlist_ani.assistant.memory import AssistantMemoryManager
from openlist_ani.assistant.tools.update_memory import UpdateMemoryTool
from openlist_ani.assistant.tools.update_soul import UpdateSoulTool
from openlist_ani.assistant.tools.update_user_profile import UpdateUserProfileTool


@pytest.fixture
def temp_memory_dir(tmp_path) -> Path:
    mem_dir = tmp_path / "assistant"
    mem_dir.mkdir()
    (mem_dir / "sessions").mkdir()
    return mem_dir


def _create_manager(temp_memory_dir: Path) -> AssistantMemoryManager:
    return AssistantMemoryManager(
        client=None,
        model="gpt-4o",
        base_dir=temp_memory_dir,
    )


# ------------------------------------------------------------------ #
#  UpdateUserProfileTool
# ------------------------------------------------------------------ #


class TestUpdateUserProfileTool:
    async def test_saves_observation(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)
        manager._ensure_dirs()

        tool = UpdateUserProfileTool()
        tool.set_memory_manager(manager)

        result = await tool.execute(content="用户名字叫小明")
        assert "✅" in result

        user_content = (temp_memory_dir / "USER.md").read_text(encoding="utf-8")
        assert "小明" in user_content

    async def test_saves_bangumi_preferences(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)
        manager._ensure_dirs()

        tool = UpdateUserProfileTool()
        tool.set_memory_manager(manager)

        prefs = "- 偏好类型: 科幻(0.9), 战斗(0.8)\n- 评分倾向: 偏高"
        result = await tool.execute(content=prefs, section="bangumi_preferences")

        assert "✅" in result
        user_content = (temp_memory_dir / "USER.md").read_text(encoding="utf-8")
        assert "科幻" in user_content

    async def test_empty_content_rejected(self):
        tool = UpdateUserProfileTool()
        tool.set_memory_manager(MagicMock())

        assert "No content" in await tool.execute(content="")
        assert "No content" in await tool.execute(content="   ")

    async def test_no_manager(self):
        tool = UpdateUserProfileTool()
        result = await tool.execute(content="test")
        assert "unavailable" in result.lower()

    def test_tool_name(self):
        tool = UpdateUserProfileTool()
        assert tool.name == "update_user_profile"
        defn = tool.get_definition()
        assert "content" in defn["function"]["parameters"]["properties"]
        assert "section" in defn["function"]["parameters"]["properties"]


# ------------------------------------------------------------------ #
#  UpdateMemoryTool
# ------------------------------------------------------------------ #


class TestUpdateMemoryTool:
    async def test_saves_fact(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)
        manager._ensure_dirs()

        tool = UpdateMemoryTool()
        tool.set_memory_manager(manager)

        result = await tool.execute(
            fact="用户的 qBittorrent 运行在 8080 端口",
            category="project_state",
        )
        assert "✅" in result

        memory_content = (temp_memory_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "qBittorrent" in memory_content
        assert "project_state" in memory_content

    async def test_default_category_is_general(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)
        manager._ensure_dirs()

        tool = UpdateMemoryTool()
        tool.set_memory_manager(manager)

        await tool.execute(fact="some general fact")
        memory_content = (temp_memory_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "general" in memory_content

    async def test_empty_fact_rejected(self):
        tool = UpdateMemoryTool()
        tool.set_memory_manager(MagicMock())
        assert "No fact" in await tool.execute(fact="")

    async def test_no_manager(self):
        tool = UpdateMemoryTool()
        result = await tool.execute(fact="test")
        assert "unavailable" in result.lower()

    def test_tool_name(self):
        tool = UpdateMemoryTool()
        assert tool.name == "update_memory"
        defn = tool.get_definition()
        assert "fact" in defn["function"]["parameters"]["properties"]
        assert "category" in defn["function"]["parameters"]["properties"]


# ------------------------------------------------------------------ #
#  UpdateSoulTool
# ------------------------------------------------------------------ #


class TestUpdateSoulTool:
    async def test_saves_customization(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)
        manager._ensure_dirs()

        tool = UpdateSoulTool()
        tool.set_memory_manager(manager)

        result = await tool.execute(instruction="回复尽量简洁")
        assert "✅" in result

        soul_content = (temp_memory_dir / "SOUL.md").read_text(encoding="utf-8")
        assert "回复尽量简洁" in soul_content
        assert "User Customizations" in soul_content

    async def test_duplicate_not_repeated(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)
        manager._ensure_dirs()

        tool = UpdateSoulTool()
        tool.set_memory_manager(manager)

        await tool.execute(instruction="不要用emoji")
        await tool.execute(instruction="不要用emoji")

        soul_content = (temp_memory_dir / "SOUL.md").read_text(encoding="utf-8")
        assert soul_content.count("不要用emoji") == 1

    async def test_preserves_core_soul(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)
        manager._ensure_dirs()

        tool = UpdateSoulTool()
        tool.set_memory_manager(manager)

        await tool.execute(instruction="用日语回复")

        soul_content = (temp_memory_dir / "SOUL.md").read_text(encoding="utf-8")
        # Core personality should still be present
        assert "oAni" in soul_content
        # Customization should be appended
        assert "用日语回复" in soul_content

    async def test_empty_instruction_rejected(self):
        tool = UpdateSoulTool()
        tool.set_memory_manager(MagicMock())
        assert "No instruction" in await tool.execute(instruction="")

    async def test_no_manager(self):
        tool = UpdateSoulTool()
        result = await tool.execute(instruction="test")
        assert "unavailable" in result.lower()

    def test_tool_name(self):
        tool = UpdateSoulTool()
        assert tool.name == "update_soul"


# ------------------------------------------------------------------ #
#  First-time onboarding flow
# ------------------------------------------------------------------ #


class TestFirstTimeOnboarding:
    async def test_default_user_triggers_first_time_prompt(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)
        messages = await manager.build_system_messages("你好")

        first_time_msgs = [
            m for m in messages if "首次用户初始化" in m.get("content", "")
        ]
        assert len(first_time_msgs) == 1
        content = first_time_msgs[0]["content"]
        assert "update_user_profile" in content
        assert "bangumi_preferences" in content

    async def test_after_observation_no_first_time_prompt(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)

        await manager.add_user_observation("用户名字叫小明")

        messages = await manager.build_system_messages("帮我搜个动漫")
        assert not any("首次用户初始化" in m.get("content", "") for m in messages)

        profile_msgs = [
            m
            for m in messages
            if m["role"] == "system" and "profile" in m["content"].lower()
        ]
        assert len(profile_msgs) == 1
        assert "小明" in profile_msgs[0]["content"]

    async def test_multiple_observations_accumulate(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)

        await manager.add_user_observation("用户名字叫小明")
        await manager.add_user_observation("用户喜欢科幻类动漫")

        user_content = (temp_memory_dir / "USER.md").read_text(encoding="utf-8")
        assert "小明" in user_content
        assert "科幻" in user_content

    async def test_duplicate_observation_not_repeated(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)

        await manager.add_user_observation("用户名字叫小明")
        await manager.add_user_observation("用户名字叫小明")

        user_content = (temp_memory_dir / "USER.md").read_text(encoding="utf-8")
        assert user_content.count("小明") == 1


# ------------------------------------------------------------------ #
#  End-to-end onboarding simulation
# ------------------------------------------------------------------ #


class TestEndToEndOnboardingSimulation:
    async def test_full_onboarding_flow(self, temp_memory_dir):
        """Simulate: new user → greet → ask name → user responds →
        save profile + Bangumi preferences → next turn uses profile.
        """
        manager = _create_manager(temp_memory_dir)
        profile_tool = UpdateUserProfileTool()
        profile_tool.set_memory_manager(manager)

        # Turn 1: User's first message
        msgs = await manager.build_system_messages("你好")
        assert any("首次用户初始化" in m.get("content", "") for m in msgs)

        await manager.append_turn("你好", "你好！我是 oAni 🎌 我该怎么称呼你？")

        # Turn 2: User provides name and agrees to show collection
        msgs = await manager.build_system_messages("我叫小明，帮我看看收藏吧")
        assert any("首次用户初始化" in m.get("content", "") for m in msgs)

        # LLM saves name
        result = await profile_tool.execute(content="用户名字叫小明")
        assert "✅" in result

        # LLM fetches collection and saves preferences
        prefs_result = await profile_tool.execute(
            content="- 偏好类型: 科幻, 机战\n- 评分倾向: 7-9分居多\n- 收藏数量: 42部",
            section="bangumi_preferences",
        )
        assert "✅" in prefs_result

        await manager.append_turn(
            "我叫小明，帮我看看收藏吧",
            "好的小明！你的收藏里有42部...",
        )

        # Turn 3: Profile should be active, no first-time prompt
        msgs = await manager.build_system_messages("谢谢")
        assert not any("首次用户初始化" in m.get("content", "") for m in msgs)

        profile_msgs = [
            m
            for m in msgs
            if m["role"] == "system" and "profile" in m["content"].lower()
        ]
        assert len(profile_msgs) == 1
        profile_content = profile_msgs[0]["content"]
        assert "小明" in profile_content
        assert "科幻" in profile_content
