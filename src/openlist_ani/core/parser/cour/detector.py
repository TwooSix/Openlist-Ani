from datetime import datetime
from typing import Any

from ..constants import COUR_GAP_DAYS
from ..model import CourGroup


def detect_cours_from_episodes(
    episodes: list[dict[str, Any]], gap_days: int = COUR_GAP_DAYS
) -> list[CourGroup]:
    dated_episodes: list[tuple[int, datetime, str]] = []
    for ep in episodes:
        air_date_str = ep.get("air_date")
        ep_num = ep.get("episode_number")
        if not air_date_str or ep_num is None:
            continue
        try:
            air_date = datetime.strptime(air_date_str, "%Y-%m-%d")
            dated_episodes.append((ep_num, air_date, air_date_str))
        except ValueError:
            continue

    if not dated_episodes:
        return []

    dated_episodes.sort(key=lambda x: (x[1], x[0]))

    cours: list[CourGroup] = []
    current_cour: list[tuple[int, datetime, str]] = [dated_episodes[0]]

    for entry in dated_episodes[1:]:
        _ep_num, air_date, _air_date_str = entry
        prev_date = current_cour[-1][1]
        delta = (air_date - prev_date).days
        if delta >= gap_days:
            cours.append(_build_cour_group(len(cours) + 1, current_cour))
            current_cour = []
        current_cour.append(entry)

    if current_cour:
        cours.append(_build_cour_group(len(cours) + 1, current_cour))

    return cours


def _build_cour_group(
    cour_index: int, entries: list[tuple[int, datetime, str]]
) -> CourGroup:
    return CourGroup(
        cour_index=cour_index,
        start_episode=entries[0][0],
        end_episode=entries[-1][0],
        air_date_start=entries[0][2],
        air_date_end=entries[-1][2],
    )
