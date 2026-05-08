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

        if bot_cfg.type == "telegram":
            if not bot_cfg.config.get("bot_token"):
                errors.append(f"{bot_label}: 'bot_token' is required for Telegram bot.")
            if not bot_cfg.config.get("user_id"):
                errors.append(f"{bot_label}: 'user_id' is required for Telegram bot.")
            return

        if bot_cfg.type == "pushplus":
            if not bot_cfg.config.get("user_token"):
                errors.append(
                    f"{bot_label}: 'user_token' is required for PushPlus bot."
                )
            return

        warnings.append(f"{bot_label}: Unknown bot type '{bot_cfg.type}'.")

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

        if not self._data.assistant.telegram.bot_token:
            errors.append(
                "Assistant is enabled but Telegram bot_token is missing. "
                "Please set [assistant.telegram] bot_token."
            )

        if not self._data.assistant.telegram.allowed_users:
            warnings.append(
                "Assistant allowed_users is empty - all Telegram users can interact. "
                "Consider adding specific user IDs in [assistant.telegram] allowed_users."
            )

    def _log_validation_results(self, errors: list[str], warnings: list[str]) -> None:
        for warning in warnings:
            logger.warning(f"Config Warning: {warning}")
        for error in errors:
            logger.log(FATAL_LEVEL, f"Config Error: {error}")
