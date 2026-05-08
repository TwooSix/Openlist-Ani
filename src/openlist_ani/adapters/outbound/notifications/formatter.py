"""Notification message formatting."""

from __future__ import annotations


class NotificationFormatter:
    def batch_message(self, queue: dict[str, list[str]]) -> tuple[str, int]:
        message_parts = ["你订阅的番剧更新啦："]
        count = 0
        for anime_name, titles in queue.items():
            message_parts.append(f"\n[{anime_name}]:")
            for title in titles:
                message_parts.append(f"  • {title}")
                count += 1
        return "\n".join(message_parts), count

    def download_complete_message(self, anime_name: str, title: str) -> str:
        return f"你订阅的番剧[{anime_name}] 更新啦：\n{title}\n"
