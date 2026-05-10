"""Tests for ConfigManager and Pydantic config models."""

import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from openlist_ani.adapters.outbound.configuration import (
    AssistantConfig,
    BotConfig,
    ConfigManager,
    ConfigValidator,
    DownloaderConfig,
    FileRenamerConfig,
    LLMConfig,
    LogConfig,
    MetadataFilterConfig,
    MetadataParserConfig,
    NotificationConfig,
    OpenListConfig,
    ProxyConfig,
    RSSConfig,
    FeishuAssistantConfig,
    WechatAssistantConfig,
    UserConfig,
)

# ===========================================================================
# Pydantic model defaults & validation
# ===========================================================================


class TestRSSConfig:
    def test_defaults(self):
        cfg = RSSConfig()
        assert cfg.urls == []
        assert cfg.interval_time == 300

    def test_custom_values(self):
        cfg = RSSConfig(urls=["https://feed.example/rss1", "https://feed.example/rss2"], interval_time=60)
        assert len(cfg.urls) == 2
        assert cfg.interval_time == 60


class TestOpenListConfig:
    def test_defaults(self):
        cfg = OpenListConfig()
        assert cfg.url == "http://localhost:5244"
        assert cfg.token == ""
        assert cfg.download_path == "/"
        assert cfg.offline_download_tool == "qBittorrent"

    def test_offline_tool_strips_whitespace(self):
        cfg = OpenListConfig(offline_download_tool=" aria2 ")
        assert cfg.offline_download_tool == "aria2"

    def test_offline_tool_normalizes_known_tool_case(self):
        cfg = OpenListConfig(offline_download_tool=" QBittorrent ")
        assert cfg.offline_download_tool == "qBittorrent"

    def test_empty_offline_tool_raises(self):
        with pytest.raises(ValidationError):
            OpenListConfig(offline_download_tool="")


class TestLLMConfig:
    def test_defaults(self):
        cfg = LLMConfig()
        assert cfg.provider_type == "openai"
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
        assert cfg.telegram.enabled is False
        assert cfg.telegram.bot_token == ""
        assert cfg.telegram.allowed_users == []
        assert isinstance(cfg.wechat, WechatAssistantConfig)
        assert cfg.wechat.enabled is False
        assert isinstance(cfg.feishu, FeishuAssistantConfig)
        assert cfg.feishu.enabled is False
        assert cfg.feishu.connection_mode == "websocket"
        assert cfg.feishu.state_dir == "data/messaging"


class TestLogConfig:
    def test_defaults(self):
        cfg = LogConfig()
        assert cfg.level == "INFO"


class TestProxyConfig:
    def test_defaults(self):
        cfg = ProxyConfig()
        assert cfg.http == ""
        assert cfg.https == ""


class TestUserConfig:
    def test_defaults(self):
        """UserConfig should be constructable with no arguments."""
        cfg = UserConfig()
        assert isinstance(cfg.downloader, DownloaderConfig)
        assert isinstance(cfg.file_renamer, FileRenamerConfig)
        assert isinstance(cfg.metadata_parser, MetadataParserConfig)
        assert isinstance(cfg.rss, RSSConfig)
        assert isinstance(cfg.openlist, OpenListConfig)
        assert isinstance(cfg.llm, LLMConfig)
        assert isinstance(cfg.notification, NotificationConfig)
        assert isinstance(cfg.assistant, AssistantConfig)
        assert isinstance(cfg.log, LogConfig)
        assert isinstance(cfg.proxy, ProxyConfig)

    def test_model_validate_from_dict(self):
        data = {
            "rss": {"urls": ["https://feed.example/rss1"], "interval_time": 120},
            "openlist": {"url": "http://example.com", "token": "abc"},
        }
        cfg = UserConfig.model_validate(data)
        assert cfg.rss.urls == ["https://feed.example/rss1"]
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


# ===========================================================================
# ConfigManager
# ===========================================================================


class TestConfigManager:
    def test_package_import_has_no_file_side_effect(self, tmp_path):
        """Importing configuration symbols should not create config.toml."""
        script = "\n".join(
            [
                "from pathlib import Path",
                "from openlist_ani.adapters.outbound.configuration import ConfigManager",
                "raise SystemExit(1 if Path('config.toml').exists() else 0)",
            ]
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr or result.stdout

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

    def test_new_manager_loads_file_change(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        assert mgr.rss.urls == []

        # Modify the file
        from tomlkit import dumps as toml_dumps

        data = UserConfig(rss=RSSConfig(urls=["https://new.rss"])).model_dump()
        (tmp_path / "config.toml").write_text(toml_dumps(data), encoding="utf-8")

        assert mgr.rss.urls == []

        mgr2 = ConfigManager("config.toml")
        assert "https://new.rss" in mgr2.rss.urls

    def test_corrupt_toml_no_crash(self, tmp_path, monkeypatch):
        """Corrupt TOML should log error but not crash."""
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "config.toml"
        config_file.write_text("INVALID TOML [[[", encoding="utf-8")

        # Should not raise
        mgr = ConfigManager("config.toml")
        # Default config remains
        assert mgr.rss.urls == []

    def test_save_and_new_manager_loads(self, tmp_path, monkeypatch):
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
        assert isinstance(mgr.downloader, DownloaderConfig)
        assert isinstance(mgr.file_renamer, FileRenamerConfig)
        assert isinstance(mgr.openlist, OpenListConfig)
        assert isinstance(mgr.llm, LLMConfig)
        assert isinstance(mgr.notification, NotificationConfig)
        assert isinstance(mgr.log, LogConfig)
        assert isinstance(mgr.assistant, AssistantConfig)
        assert isinstance(mgr.proxy, ProxyConfig)

    def test_package_import_loads_config_without_environment_cycle(self, tmp_path):
        """Fresh process import should not trip a settings/environment cycle."""
        from tomlkit import dumps as toml_dumps

        config_file = tmp_path / "config.toml"
        data = UserConfig(proxy=ProxyConfig(http="http://127.0.0.1:7890")).model_dump()
        config_file.write_text(toml_dumps(data), encoding="utf-8")

        script = "\n".join(
            [
                "from openlist_ani.adapters.outbound.configuration import config",
                "raise SystemExit(1 if config.load_failed else 0)",
            ]
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr or result.stdout


class TestConfigValidation:
    def test_validate_no_rss_urls(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        # No RSS URLs → should fail
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_no_openlist_url(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = ""
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_no_openlist_token(self, tmp_path, monkeypatch):
        """Missing token should now be an error (required for auth)."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = ""
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_pass_minimal(self, tmp_path, monkeypatch):
        """Minimal valid config: rss.urls + openlist.url + openlist.token + llm key."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    # -- Notification dependency checks --

    def test_validate_notification_enabled_no_bots(self, tmp_path, monkeypatch):
        """Notification enabled but no bots → error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = []
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_notification_telegram_missing_bot_token(
        self, tmp_path, monkeypatch
    ):
        """Telegram bot without bot_token → error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [
            BotConfig(type="telegram", config={"user_id": 123})
        ]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_notification_telegram_missing_user_id(
        self, tmp_path, monkeypatch
    ):
        """Telegram bot without user_id → error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [
            BotConfig(type="telegram", config={"bot_token": "abc"})
        ]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_notification_telegram_valid(self, tmp_path, monkeypatch):
        """Telegram bot with valid config → pass."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [
            BotConfig(type="telegram", config={"bot_token": "abc", "user_id": 123})
        ]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_validate_notification_pushplus_missing_user_token(
        self, tmp_path, monkeypatch
    ):
        """PushPlus bot without user_token → error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [BotConfig(type="pushplus", config={})]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_notification_pushplus_valid(self, tmp_path, monkeypatch):
        """PushPlus bot with valid config → pass."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [
            BotConfig(type="pushplus", config={"user_token": "tok123"})
        ]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_validate_notification_wechat_requires_credentials_and_home_channel(
        self, tmp_path, monkeypatch
    ):
        """WeChat notification must be configured from openlist-ani-wechat-login output."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [BotConfig(type="wechat", config={})]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_notification_wechat_with_login_output_is_valid(
        self, tmp_path, monkeypatch
    ):
        """WeChat notification accepts credentials and chat_id from setup command."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [
            BotConfig(
                type="wechat",
                config={
                    "account_id": "bot@im.bot",
                    "token": "token",
                    "home_channel": "user@im.wechat",
                },
            )
        ]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_validate_notification_feishu_requires_app_credentials(
        self, tmp_path, monkeypatch
    ):
        """Feishu notification needs app credentials even if target is bound later."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [BotConfig(type="feishu", config={})]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_notification_feishu_target_optional(
        self, tmp_path, monkeypatch
    ):
        """Feishu target can be configured later with /set-notify-home."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.notification.enabled = True
        mgr._config.notification.bots = [
            BotConfig(
                type="feishu",
                config={"app_id": "cli_xxx", "app_secret": "secret"},
            )
        ]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_validate_notification_disabled_skips_bot_checks(
        self, tmp_path, monkeypatch
    ):
        """If notification is disabled, bad bot config should not cause failure."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.notification.enabled = False
        mgr._config.notification.bots = [
            BotConfig(type="telegram", config={})  # Invalid but disabled
        ]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_validate_notification_disabled_bot_skipped(self, tmp_path, monkeypatch):
        """Enabled notification with a disabled bot should skip that bot's check."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
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
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    # -- Assistant dependency checks --

    def test_validate_assistant_enabled_no_bot_token(self, tmp_path, monkeypatch):
        """Assistant enabled without any frontend → error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.assistant.enabled = True
        mgr._config.assistant.telegram.bot_token = ""
        mgr._config.assistant.telegram.allowed_users = [123]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_assistant_enabled_with_wechat_frontend(
        self, tmp_path, monkeypatch
    ):
        """WeChat assistant frontend requires QR login credentials in config."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.assistant.enabled = True
        mgr._config.assistant.wechat.enabled = True
        mgr._config.assistant.wechat.account_id = "bot@im.bot"
        mgr._config.assistant.wechat.token = "token"
        mgr._config.assistant.wechat.home_channel = "user@im.wechat"
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_validate_assistant_enabled_with_wechat_missing_credentials(
        self, tmp_path, monkeypatch
    ):
        """WeChat assistant blocks startup until setup command output is configured."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.assistant.enabled = True
        mgr._config.assistant.wechat.enabled = True
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_assistant_enabled_with_feishu_frontend(
        self, tmp_path, monkeypatch
    ):
        """Feishu assistant frontend requires app credentials."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.assistant.enabled = True
        mgr._config.assistant.feishu.enabled = True
        mgr._config.assistant.feishu.app_id = "cli_xxx"
        mgr._config.assistant.feishu.app_secret = "secret"
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_validate_assistant_enabled_feishu_missing_secret(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.assistant.enabled = True
        mgr._config.assistant.feishu.enabled = True
        mgr._config.assistant.feishu.app_id = "cli_xxx"
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_assistant_enabled_no_allowed_users(self, tmp_path, monkeypatch):
        """Assistant enabled without allowed_users → warning (not error)."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.assistant.enabled = True
        mgr._config.assistant.telegram.enabled = True
        mgr._config.assistant.telegram.bot_token = "bot-token"
        mgr._config.assistant.telegram.allowed_users = []
        mgr.save()
        # Empty allowed_users is allowed (= allow all) but produces a warning
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_validate_assistant_enabled_no_llm_key(self, tmp_path, monkeypatch):
        """Assistant enabled without LLM key → error (assistant depends on LLM)."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = ""  # Missing
        mgr._config.assistant.enabled = True
        mgr._config.assistant.telegram.enabled = True
        mgr._config.assistant.telegram.bot_token = "bot-token"
        mgr._config.assistant.telegram.allowed_users = [123]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_validate_assistant_enabled_valid(self, tmp_path, monkeypatch):
        """Assistant with all dependencies → pass."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.assistant.enabled = True
        mgr._config.assistant.telegram.enabled = True
        mgr._config.assistant.telegram.bot_token = "bot-token"
        mgr._config.assistant.telegram.allowed_users = [123]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_validate_assistant_disabled_skips_checks(self, tmp_path, monkeypatch):
        """Disabled assistant should not trigger its dependency errors."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://feed.example/rss"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.assistant.enabled = False
        mgr._config.assistant.telegram.bot_token = ""
        mgr._config.assistant.telegram.allowed_users = []
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True


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

    def test_data_property_returns_snapshot(self, tmp_path, monkeypatch):
        """Accessing .data should not implicitly reload changed files."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")

        from tomlkit import dumps as toml_dumps

        data = UserConfig(rss=RSSConfig(urls=["https://new.rss"])).model_dump()
        (tmp_path / "config.toml").write_text(toml_dumps(data), encoding="utf-8")

        assert isinstance(mgr.data, UserConfig)
        assert mgr.data.rss.urls == []

        mgr2 = ConfigManager("config.toml")
        assert mgr2.data.rss.urls == ["https://new.rss"]


class TestRenameFormatValidation:
    """Tests for rename_format field validation in ConfigValidator."""

    def test_valid_default_format_passes(self, tmp_path, monkeypatch):
        """Default rename_format should pass validation."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://example.com/feed"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_valid_custom_format_passes(self, tmp_path, monkeypatch):
        """Custom format with only supported fields should pass."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://example.com/feed"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.openlist.rename_format = "{anime_name} E{episode:02d}"
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_unsupported_field_fails(self, tmp_path, monkeypatch):
        """Format with unsupported field name should fail validation."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://example.com/feed"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.openlist.rename_format = "{anime_name} {nonexistent_field}"
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_empty_format_no_error(self, tmp_path, monkeypatch):
        """Empty format string should not cause validation error."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://example.com/feed"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.openlist.rename_format = ""
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_all_supported_fields_pass(self, tmp_path, monkeypatch):
        """Format using all supported fields should pass."""
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://example.com/feed"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.openlist.rename_format = (
            "{anime_name} S{season}E{episode} {fansub} {quality} {languages}"
        )
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True


class TestRSSConfigStrict:
    """Tests for the strict field in RSSConfig."""

    def test_default_is_false(self):
        cfg = RSSConfig()
        assert cfg.strict is False

    def test_set_to_true(self):
        cfg = RSSConfig(strict=True)
        assert cfg.strict is True


class TestRSSConfigExcludePatterns:
    """Tests for the exclude_patterns field in MetadataFilterConfig."""

    def test_default_is_empty(self):
        cfg = MetadataFilterConfig()
        assert cfg.exclude_patterns == []

    def test_set_patterns(self):
        cfg = MetadataFilterConfig(exclude_patterns=["合集", "SP\\d+"])
        assert len(cfg.exclude_patterns) == 2
        assert "合集" in cfg.exclude_patterns


class TestMetadataFilterConfig:
    """Tests for MetadataFilterConfig defaults and values."""

    def test_defaults(self):
        cfg = MetadataFilterConfig()
        assert cfg.exclude_fansub == []
        assert cfg.exclude_quality == []
        assert cfg.exclude_languages == []
        assert cfg.exclude_patterns == []

    def test_custom_values(self):
        cfg = MetadataFilterConfig(
            exclude_fansub=["BadSub"],
            exclude_quality=["480p"],
            exclude_languages=["未知"],
        )
        assert cfg.exclude_fansub == ["BadSub"]
        assert cfg.exclude_quality == ["480p"]
        assert cfg.exclude_languages == ["未知"]

    def test_rss_config_includes_filter(self):
        cfg = RSSConfig()
        assert isinstance(cfg.filter, MetadataFilterConfig)


class TestExcludePatternsValidation:
    """Tests for exclude_patterns regex validation in ConfigValidator."""

    def test_valid_patterns_pass(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://example.com/feed"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.rss.filter.exclude_patterns = ["合集", "SP\\d+", "HEVC"]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_invalid_regex_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://example.com/feed"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.rss.filter.exclude_patterns = ["[invalid"]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False

    def test_empty_patterns_pass(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://example.com/feed"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.rss.filter.exclude_patterns = []
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is True

    def test_mixed_valid_invalid_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfigManager("config.toml")
        mgr._config.rss.urls = ["https://example.com/feed"]
        mgr._config.openlist.url = "https://localhost"
        mgr._config.openlist.token = "tok"
        mgr._config.llm.openai_api_key = "key"
        mgr._config.rss.filter.exclude_patterns = ["valid_regex", "(unclosed"]
        mgr.save()
        assert ConfigValidator(mgr.data, mgr.load_failed).validate() is False
