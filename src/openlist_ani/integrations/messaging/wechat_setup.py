from __future__ import annotations

import argparse
import asyncio
import sys

from .wechat_ilink import WechatIlinkMessenger


async def run_wechat_login(
    *, qr_timeout_seconds: int = 480, message_timeout_seconds: int = 300
) -> dict[str, str]:
    messenger = WechatIlinkMessenger(interactive_login=False)
    credentials = await messenger.qr_login(timeout_seconds=qr_timeout_seconds)
    messenger.account_id = credentials["account_id"]
    messenger.token = credentials["token"]
    messenger.base_url = credentials.get("base_url", messenger.base_url).rstrip("/")
    target = await messenger.wait_for_first_message(
        timeout_seconds=message_timeout_seconds
    )
    return {**credentials, "chat_id": target.chat_id}


def _toml_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_wechat_config_report(result: dict[str, str]) -> str:
    account_id = _toml_quote(result.get("account_id", ""))
    token = _toml_quote(result.get("token", ""))
    base_url = _toml_quote(result.get("base_url", "https://ilinkai.weixin.qq.com"))
    chat_id = _toml_quote(result.get("chat_id", ""))
    return f"""# WeChat iLink setup result
# Copy this block into config.toml. Nothing was written to local auth files.

[[notification.bots]]
type = "wechat"
enabled = true
config = {{ account_id = "{account_id}", token = "{token}", base_url = "{base_url}", home_channel = "{chat_id}" }}

[assistant.wechat]
enabled = true
account_id = "{account_id}"
token = "{token}"
base_url = "{base_url}"
home_channel = "{chat_id}"
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run WeChat iLink setup for OpenList-Ani"
    )
    parser.add_argument(
        "--qr-timeout", type=int, default=480, help="QR login timeout seconds"
    )
    parser.add_argument(
        "--message-timeout",
        type=int,
        default=300,
        help="Timeout seconds for waiting the first WeChat message",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(
            run_wechat_login(
                qr_timeout_seconds=args.qr_timeout,
                message_timeout_seconds=args.message_timeout,
            )
        )
    except KeyboardInterrupt:
        print("Setup cancelled.", file=sys.stderr)
        raise SystemExit(130) from None
    except TimeoutError as exc:
        print(f"Setup timed out: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as exc:  # noqa: BLE001
        print(f"Setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    print("\nWeChat iLink setup complete. Copy this config into config.toml:")
    print(build_wechat_config_report(result))
