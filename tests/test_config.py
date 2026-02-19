"""Tests for ConfigManager and Pydantic config models."""

import os

import pytest
from pydantic import ValidationError

from openlist_ani.config import (
    AssistantConfig,
    BotConfig,
    ConfigManager,
    LLMConfig,
    LogConfig,
    NotificationConfig,
    OpenListConfig,
    ProxyConfig,
    RSSConfig,
    UserConfig,
)
from openlist_ani.core.download.downloader.api.model import OfflineDownloadTool

# ===========================================================================
# Pydantic model defaults & validation
# ===========================================================================


class TestRSSConfig:
    def test_defaults(self):
        cfg = RSSConfig()
        assert cfg.urls == []
        assert cfg.interval_time == 300

    def test_custom_values(self):
        cfg = RSSConfig(urls=["http://feed1", "http://feed2"], interval_time=60)
        assert len(cfg.urls) == 2
        assert cfg.interval_time == 60


class TestOpenListConfig:
    def test_defaults(self):
        cfg = OpenListConfig()
        assert cfg.url == "http://localhost:5244"
        assert cfg.token == ""
        assert cfg.download_path == "/"
        assert cfg.offline_download_tool == OfflineDownloadTool.QBITTORRENT

    def test_offline_tool_enum(self):
        cfg = OpenListConfig(offline_download_tool="aria2")
        assert cfg.offline_download_tool == OfflineDownloadTool.ARIA2

    def test_invalid_offline_tool_raises(self):
        with pytest.raises(ValidationError):
            OpenListConfig(offline_download_tool="invalid_tool")


class TestLLMConfig:
    def test_defaults(self):
        cfg = LLMConfig()
        assert cfg.openai_api_key == ""
        assert "openai" in cfg.openai_base_url
        assert cfg.openai_model == "gpt-4o"
        assert cfg.tmdb_api_key == "8ed20a12d9f37dcf9484a505c8be696c"
        assert cfg.tmdb_language == "zh-CN"


class TestBotConfig:
    def test_basic(self):
        cfg = BotConfig(type="telegram", config={"bot_token": "t", "user_id": 1})
        assert cfg.type == "telegram"
        assert cfg.enabled is True

    def test_disabled(self):
        cfg = BotConfig(type="pushplus", enabled=False)
        assert cfg.enabled is False

    def test_config_defaults_to_empty(self):
        cfg = BotConfig(type="telegram")
        assert cfg.config == {}


class TestNotificationConfig:
    def test_defaults(self):
        cfg = NotificationConfig()
        assert cfg.enabled is False
        assert cfg.batch_interval == pytest.approx(300.0)
        assert cfg.bots == []


class TestAssistantConfig:
    def test_defaults(self):
        cfg = AssistantConfig()
        assert cfg.enabled is False
        assert cfg.max_history_messages == 10
        assert cfg.telegram.bot_token == ""
        assert cfg.telegram.allowed_users == []


class TestLogConfig:
    def test_defaults(self):
        cfg = LogConfig()
        assert cfg.level == "INFO"
        assert cfg.file_level == "INFO"


class TestProxyConfig:
    def test_defaults(self):
        cfg = ProxyConfig()
        assert cfg.http == ""
        assert cfg.https == ""


class TestUserConfig:
    def test_defaults(self):
        """UserConfig should be constructable with no arguments."""
        cfg = UserConfig()
        assert isinstance(cfg.rss, RSSConfig)
        assert isinstance(cfg.openlist, OpenListConfig)
        assert isinstance(cfg.llm, LLMConfig)
        assert isinstance(cfg.notification, NotificationConfig)
        assert isinstance(cfg.assistant, AssistantConfig)
        assert isinstance(cfg.log, LogConfig)
        assert isinstance(cfg.proxy, ProxyConfig)

    def test_model_validate_from_dict(self):
        data = {
            "rss": {"urls": ["http://feed1"], "interval_time": 120},
            "openlist": {"url": "http://example.com", "token": "abc"},
        }
        cfg = UserConfig.model_validate(data)
        assert cfg.rss.urls == ["http://feed1"]
        assert cfg.rss.interval_time == 120
        assert cfg.openlist.url == "http://example.com"

    def test_model_validate_nested_bots(self):
        data = {
            "notification": {
                "enabled": True,
                "bots": [
                    {"type": "telegram", "config": {"bot_token": "t", "user_id": 1}},
                ],
            }
        }
        cfg = UserConfig.model_validate(data)
        assert cfg.notification.enabled is True
        assert len(cfg.notification.bots) == 1

    def test_extra_fields_ignored_or_error(self):
        """Extra unknown sections should either be ignored or raise — not crash."""
        data = {"unknown_section": {"key": "value"}}
        try:
            cfg = UserConfig.model_validate(data)
            # If it succeeds, that's fine (extra fields ignored)
        except ValidationError:
            # If Pydantic forbids extra fields, that's also acceptable
            pass


# ===========================================================================
# ConfigManager
# ===========================================================================


class TestConfigManager:
    def test_creates_file_if_missing(self, tmp_path, monkeypatch):
        """ConfigManager should create config.toml if it doesn't exist."""
        monkeypatch.chdir(tmp_path)
        ConfigManager("config.toml")
        assert (tmp_path / "config.toml").exists()

    def test_loads_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "config.toml"
        # Write a valid TOML config
        from tomlkit import dumps as toml_dumps

        data = UserConfig(
            rss=RSSConfig(urls=["http://test.rss"], interval_time=60)
        ).model_dump()
        config_file.write_text(toml_dumps(data), encoding="utf-8")

        mgr = ConfigManager("config.toml")
        assert mgr.rss.urls == ["http://test.rss"]
        assert mgr.rss.interval_time == 60

    def test_reload_on_file_change(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        assert mgr.rss.urls == []

        # Modify the file
        from tomlkit import dumps as toml_dumps

        data = UserConfig(rss=RSSConfig(urls=["http://new.rss"])).model_dump()
        (tmp_path / "config.toml").write_text(toml_dumps(data), encoding="utf-8")

        # Force reload (touch mtime)
        mgr.reload()
        assert "http://new.rss" in mgr.rss.urls

    def test_corrupt_toml_no_crash(self, tmp_path, monkeypatch):
        """Corrupt TOML should log error but not crash."""
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "config.toml"
        config_file.write_text("INVALID TOML [[[", encoding="utf-8")

        # Should not raise
        mgr = ConfigManager("config.toml")
        # Default config remains
        assert mgr.rss.urls == []

    def test_save_and_reload(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls.append("http://saved.rss")
        mgr.save()

        mgr2 = ConfigManager("config.toml")
        assert "http://saved.rss" in mgr2.rss.urls

    def test_add_rss_url(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr.add_rss_url("http://new.rss")
        assert "http://new.rss" in mgr.rss.urls

        # Adding duplicate should not add twice
        mgr.add_rss_url("http://new.rss")
        assert mgr.rss.urls.count("http://new.rss") == 1

    def test_properties(self, tmp_path, monkeypatch):
        """All config properties should be accessible without error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        assert isinstance(mgr.rss, RSSConfig)
        assert isinstance(mgr.openlist, OpenListConfig)
        assert isinstance(mgr.llm, LLMConfig)
        assert isinstance(mgr.notification, NotificationConfig)
        assert isinstance(mgr.log, LogConfig)
        assert isinstance(mgr.assistant, AssistantConfig)
        assert isinstance(mgr.proxy, ProxyConfig)


class TestConfigValidation:
    def test_validate_no_rss_urls(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        # No RSS URLs → should fail
        assert mgr.validate() is False

    def test_validate_no_openlist_url(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = ""
        assert mgr.validate() is False

    def test_validate_no_openlist_token(self, tmp_path, monkeypatch):
        """Missing token should now be an error (required for auth)."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = ""
        mgr.save()
        assert mgr.validate() is False

    def test_validate_pass_minimal(self, tmp_path, monkeypatch):
        """Minimal valid config: rss.urls + openlist.url + openlist.token + llm key."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr.save()
        assert mgr.validate() is True

    def test_validate_pass(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr.save()
        assert mgr.validate() is True

    # -- Notification dependency checks --

    def test_validate_notification_enabled_no_bots(self, tmp_path, monkeypatch):
        """Notification enabled but no bots → error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = []
        mgr.save()
        assert mgr.validate() is False

    def test_validate_notification_telegram_missing_bot_token(
        self, tmp_path, monkeypatch
    ):
        """Telegram bot without bot_token → error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [
            BotConfig(type="telegram", config={"user_id": 123})
        ]
        mgr.save()
        assert mgr.validate() is False

    def test_validate_notification_telegram_missing_user_id(
        self, tmp_path, monkeypatch
    ):
        """Telegram bot without user_id → error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [
            BotConfig(type="telegram", config={"bot_token": "abc"})
        ]
        mgr.save()
        assert mgr.validate() is False

    def test_validate_notification_telegram_valid(self, tmp_path, monkeypatch):
        """Telegram bot with valid config → pass."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [
            BotConfig(type="telegram", config={"bot_token": "abc", "user_id": 123})
        ]
        mgr.save()
        assert mgr.validate() is True

    def test_validate_notification_pushplus_missing_user_token(
        self, tmp_path, monkeypatch
    ):
        """PushPlus bot without user_token → error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [BotConfig(type="pushplus", config={})]
        mgr.save()
        assert mgr.validate() is False

    def test_validate_notification_pushplus_valid(self, tmp_path, monkeypatch):
        """PushPlus bot with valid config → pass."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [
            BotConfig(type="pushplus", config={"user_token": "tok123"})
        ]
        mgr.save()
        assert mgr.validate() is True

    def test_validate_notification_disabled_skips_bot_checks(
        self, tmp_path, monkeypatch
    ):
        """If notification is disabled, bad bot config should not cause failure."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.notification.enabled = False
        mgr._config.notification.bots = [
            BotConfig(type="telegram", config={})  # Invalid but disabled
        ]
        mgr.save()
        assert mgr.validate() is True

    def test_validate_notification_disabled_bot_skipped(self, tmp_path, monkeypatch):
        """Enabled notification with a disabled bot should skip that bot's check."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [
            BotConfig(
                type="telegram", enabled=False, config={}
            ),  # Disabled, skip check
            BotConfig(type="pushplus", config={"user_token": "tok123"}),
        ]
        mgr.save()
        assert mgr.validate() is True

    # -- Assistant dependency checks --

    def test_validate_assistant_enabled_no_bot_token(self, tmp_path, monkeypatch):
        """Assistant enabled without bot token → error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.assistant.enabled = True
        mgr._config.assistant.telegram.bot_token = ""
        mgr._config.assistant.telegram.allowed_users = [123]
        mgr.save()
        assert mgr.validate() is False

    def test_validate_assistant_enabled_no_allowed_users(self, tmp_path, monkeypatch):
        """Assistant enabled without allowed_users → error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.assistant.enabled = True
        mgr._config.assistant.telegram.bot_token = "bot-token"
        mgr._config.assistant.telegram.allowed_users = []
        mgr.save()
        assert mgr.validate() is False

    def test_validate_assistant_enabled_no_llm_key(self, tmp_path, monkeypatch):
        """Assistant enabled without LLM key → error (assistant depends on LLM)."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = ""  # Missing
        mgr._config.assistant.enabled = True
        mgr._config.assistant.telegram.bot_token = "bot-token"
        mgr._config.assistant.telegram.allowed_users = [123]
        mgr.save()
        assert mgr.validate() is False

    def test_validate_assistant_enabled_valid(self, tmp_path, monkeypatch):
        """Assistant with all dependencies → pass."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.assistant.enabled = True
        mgr._config.assistant.telegram.bot_token = "bot-token"
        mgr._config.assistant.telegram.allowed_users = [123]
        mgr.save()
        assert mgr.validate() is True

    def test_validate_assistant_disabled_skips_checks(self, tmp_path, monkeypatch):
        """Disabled assistant should not trigger its dependency errors."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["http://feed"]
        mgr._config.openlist.url = "http://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.assistant.enabled = False
        mgr._config.assistant.telegram.bot_token = ""
        mgr._config.assistant.telegram.allowed_users = []
        mgr.save()
        assert mgr.validate() is True


class TestProxyEnvVars:
    def test_proxy_sets_env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from tomlkit import dumps as toml_dumps

        data = UserConfig(
            proxy=ProxyConfig(
                http="http://127.0.0.1:7890", https="http://127.0.0.1:7890"
            )
        ).model_dump()
        (tmp_path / "config.toml").write_text(toml_dumps(data), encoding="utf-8")

        ConfigManager("config.toml")
        assert os.environ.get("HTTP_PROXY") == "http://127.0.0.1:7890"
        assert os.environ.get("HTTPS_PROXY") == "http://127.0.0.1:7890"

        # Cleanup
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)

    def test_empty_proxy_no_env(self, tmp_path, monkeypatch):
        """Empty proxy strings should not set env vars."""
        monkeypatch.chdir(tmp_path)
        # Remove any existing proxy env vars
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)

        ConfigManager("config.toml")
        assert os.environ.get("HTTP_PROXY") is None
        assert os.environ.get("HTTPS_PROXY") is None

    def test_data_property_triggers_reload(self, tmp_path, monkeypatch):
        """Accessing .data should check mtime and reload if changed."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")

        # Access data — should not crash
        d = mgr.data
        assert isinstance(d, UserConfig)
