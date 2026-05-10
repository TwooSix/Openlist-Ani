"""Pure validation for user configuration."""

from __future__ import annotations

import re
import string

from openlist_ani.logger import FATAL_LEVEL, logger

from .settings import BotConfig, UserConfig

_SUPPORTED_RENAME_FIELDS: frozenset[str] = frozenset(
    {"anime_name", "season", "episode", "fansub", "quality", "languages"}
)


class ConfigValidator:
    """Validate config values without reading files or calling external systems."""

    def __init__(self, data: UserConfig, load_failed: bool = False) -> None:
        self._data = data
        self._load_failed = load_failed

    def validate(self) -> bool:
        if self._load_failed:
            return False

        errors: list[str] = []
        warnings: list[str] = []

        self._validate_core_config(errors)
        self._validate_rename_format(errors)
        self._validate_exclude_patterns(errors)
        self._validate_llm_config(errors)
        self._validate_notification_config(errors, warnings)
        self._validate_assistant_config(errors, warnings)
        self._log_validation_results(errors, warnings)

        return not errors

    def _validate_core_config(self, errors: list[str]) -> None:
        if not self._data.rss.urls:
            errors.append("No RSS URLs configured. Please add RSS URLs in [rss] urls.")

        if not self._data.openlist.url:
            errors.append("OpenList URL is not configured in [openlist] url.")

        if not self._data.openlist.token:
            errors.append(
                "OpenList token is not configured in [openlist] token. "
                "Authentication will fail without a valid token."
            )

    def _validate_rename_format(self, errors: list[str]) -> None:
        fmt = self._data.openlist.rename_format
        if not fmt:
            return

        formatter = string.Formatter()
        try:
            parsed_fields = {
                field_name
                for _, field_name, _, _ in formatter.parse(fmt)
                if field_name is not None
            }
        except (ValueError, KeyError) as e:
            errors.append(f"Invalid rename_format syntax: '{fmt}'. Error: {e}")
            return

        unsupported = parsed_fields - _SUPPORTED_RENAME_FIELDS
        if unsupported:
            errors.append(
                f"rename_format contains unsupported fields: {unsupported}. "
                f"Supported fields: {sorted(_SUPPORTED_RENAME_FIELDS)}"
            )

    def _validate_exclude_patterns(self, errors: list[str]) -> None:
        for i, pattern in enumerate(self._data.rss.filter.exclude_patterns):
            try:
                re.compile(pattern)
            except re.error as e:
                errors.append(
                    f"rss.filter.exclude_patterns[{i}] is not a valid regex: "
                    f"'{pattern}'. Error: {e}"
                )

    def _validate_llm_config(self, errors: list[str]) -> None:
        if not self._data.llm.openai_api_key:
            errors.append("OpenAI API key is missing in [llm] openai_api_key. ")

    def _validate_notification_config(
        self, errors: list[str], warnings: list[str]
    ) -> None:
        if not self._data.notification.enabled:
            return

        if not self._data.notification.bots:
            errors.append(
                "Notification is enabled but no bots are configured. "
                "Please add bot entries in [[notification.bots]]."
            )
            return

        for i, bot_cfg in enumerate(self._data.notification.bots):
            if not bot_cfg.enabled:
                continue
            self._validate_notification_bot(i, bot_cfg, errors, warnings)

    def _validate_notification_bot(
        self,
        index: int,
        bot_cfg: BotConfig,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        bot_label = f"notification.bots[{index}] (type={bot_cfg.type})"

        bot_type = bot_cfg.type.strip().lower()
        validators = {
            "telegram": self._validate_telegram_notification_bot,
            "pushplus": self._validate_pushplus_notification_bot,
            "wechat": self._validate_wechat_notification_bot,
            "feishu": self._validate_feishu_notification_bot,
        }
        validator = validators.get(bot_type)
        if validator is None:
            warnings.append(f"{bot_label}: Unknown bot type '{bot_cfg.type}'.")
            return
        validator(bot_label, bot_cfg, errors, warnings)

    def _require_bot_config(
        self,
        bot_label: str,
        bot_cfg: BotConfig,
        key: str,
        bot_name: str,
        errors: list[str],
        hint: str = "",
    ) -> None:
        if bot_cfg.config.get(key):
            return
        message = f"{bot_label}: '{key}' is required for {bot_name} bot."
        errors.append(f"{message} {hint}".rstrip())

    def _validate_telegram_notification_bot(
        self,
        bot_label: str,
        bot_cfg: BotConfig,
        errors: list[str],
        _warnings: list[str],
    ) -> None:
        self._require_bot_config(bot_label, bot_cfg, "bot_token", "Telegram", errors)
        self._require_bot_config(bot_label, bot_cfg, "user_id", "Telegram", errors)

    def _validate_pushplus_notification_bot(
        self,
        bot_label: str,
        bot_cfg: BotConfig,
        errors: list[str],
        _warnings: list[str],
    ) -> None:
        self._require_bot_config(bot_label, bot_cfg, "user_token", "PushPlus", errors)

    def _validate_wechat_notification_bot(
        self,
        bot_label: str,
        bot_cfg: BotConfig,
        errors: list[str],
        _warnings: list[str],
    ) -> None:
        hint = "Run openlist-ani-wechat-login and copy the printed config."
        self._require_bot_config(
            bot_label, bot_cfg, "account_id", "WeChat", errors, hint
        )
        self._require_bot_config(bot_label, bot_cfg, "token", "WeChat", errors, hint)
        if bot_cfg.config.get("chat_id") or bot_cfg.config.get("home_channel"):
            return
        errors.append(
            f"{bot_label}: 'home_channel' or 'chat_id' is required for "
            "WeChat notification. Run openlist-ani-wechat-login, send "
            "one message to the bot, and copy the printed config."
        )

    def _validate_feishu_notification_bot(
        self,
        bot_label: str,
        bot_cfg: BotConfig,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        self._require_bot_config(bot_label, bot_cfg, "app_id", "Feishu", errors)
        self._require_bot_config(bot_label, bot_cfg, "app_secret", "Feishu", errors)
        if bot_cfg.config.get("receive_id"):
            return
        warnings.append(
            f"{bot_label}: 'receive_id' is not set. Start the Feishu "
            "assistant and send /set-notify-home before notifications "
            "can be delivered."
        )

    def _validate_assistant_config(
        self, errors: list[str], warnings: list[str]
    ) -> None:
        if not self._data.assistant.enabled:
            return

        if not self._data.llm.openai_api_key:
            errors.append(
                "Assistant is enabled but API key is missing. "
                "Assistant requires LLM. Please set [llm] openai_api_key."
            )

        if self._data.llm.provider_type not in ("openai", "anthropic"):
            errors.append(
                f"Unknown LLM provider_type: '{self._data.llm.provider_type}'. "
                "Supported values: 'openai', 'anthropic'."
            )

        telegram_enabled = self._assistant_telegram_enabled()
        wechat_enabled = self._data.assistant.wechat.enabled
        feishu_enabled = self._data.assistant.feishu.enabled

        if not (telegram_enabled or wechat_enabled or feishu_enabled):
            errors.append(
                "Assistant is enabled but no frontend is configured. "
                "Enable [assistant.telegram], [assistant.wechat], or [assistant.feishu]."
            )
            return

        self._validate_telegram_assistant(telegram_enabled, errors, warnings)
        self._validate_wechat_assistant(wechat_enabled, errors)
        self._validate_feishu_assistant(feishu_enabled, errors, warnings)

    def _assistant_telegram_enabled(self) -> bool:
        return bool(
            self._data.assistant.telegram.enabled
            or self._data.assistant.telegram.bot_token
        )

    def _validate_telegram_assistant(
        self, enabled: bool, errors: list[str], warnings: list[str]
    ) -> None:
        if not enabled:
            return
        telegram_cfg = self._data.assistant.telegram
        if not telegram_cfg.bot_token:
            errors.append(
                "Telegram assistant is enabled but bot_token is missing. "
                "Please set [assistant.telegram] bot_token."
            )
        if not telegram_cfg.allowed_users:
            warnings.append(
                "Assistant allowed_users is empty - all Telegram users can interact. "
                "Consider adding specific user IDs in [assistant.telegram] allowed_users."
            )

    def _validate_wechat_assistant(self, enabled: bool, errors: list[str]) -> None:
        if not enabled:
            return
        wechat_cfg = self._data.assistant.wechat
        hint = "Run openlist-ani-wechat-login and copy the printed config."
        if not wechat_cfg.account_id:
            errors.append(
                f"WeChat assistant is enabled but account_id is missing. {hint}"
            )
        if not wechat_cfg.token:
            errors.append(f"WeChat assistant is enabled but token is missing. {hint}")
        if not wechat_cfg.home_channel:
            errors.append(
                f"WeChat assistant is enabled but home_channel is missing. {hint}"
            )

    def _validate_feishu_assistant(
        self, enabled: bool, errors: list[str], warnings: list[str]
    ) -> None:
        if not enabled:
            return
        feishu_cfg = self._data.assistant.feishu
        if not feishu_cfg.app_id:
            errors.append(
                "Feishu assistant is enabled but app_id is missing. "
                "Please set [assistant.feishu] app_id."
            )
        if not feishu_cfg.app_secret:
            errors.append(
                "Feishu assistant is enabled but app_secret is missing. "
                "Please set [assistant.feishu] app_secret."
            )
        if not feishu_cfg.allowed_users:
            warnings.append(
                "Feishu assistant allowed_users is empty - all admitted Feishu "
                "users can interact."
            )

    def _log_validation_results(self, errors: list[str], warnings: list[str]) -> None:
        for warning in warnings:
            logger.warning(f"Config Warning: {warning}")
        for error in errors:
            logger.log(FATAL_LEVEL, f"Config Error: {error}")
