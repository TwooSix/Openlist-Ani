"""Install bundled assistant skills into the configured runtime directory."""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from loguru import logger


def _ignore_generated_files(_dir: str, names: list[str]) -> list[str]:
    return [
        name
        for name in names
        if name == "__pycache__" or name.endswith((".pyc", ".pyo"))
    ]


def _copy_missing_entries(source: Path, target: Path) -> list[str]:
    installed: list[str] = []
    target.mkdir(parents=True, exist_ok=True)

    for item in source.iterdir():
        if item.name == "__pycache__" or item.name.endswith((".pyc", ".pyo")):
            continue

        destination = target / item.name
        if destination.exists():
            continue

        if item.is_dir():
            shutil.copytree(
                item,
                destination,
                ignore=_ignore_generated_files,
            )
        else:
            shutil.copy2(item, destination)
        installed.append(item.name)

    return installed


def install_bundled_skills_if_missing(skills_dir: Path) -> bool:
    """Copy packaged default skills that are absent from *skills_dir*.

    Existing directories are never overwritten so users can freely customize
    the installed skills after the first run, while new bundled skills can still
    appear beside user-created skills.
    """
    target = skills_dir.expanduser()

    bundled = resources.files("openlist_ani").joinpath("builtin_skills", "skills")
    try:
        with resources.as_file(bundled) as bundled_path:
            if not bundled_path.is_dir():
                logger.debug("Bundled skills directory is not available")
                return False

            installed = _copy_missing_entries(bundled_path, target)
    except Exception as exc:
        logger.warning(f"Failed to install bundled skills to {target}: {exc}")
        return False

    if installed:
        logger.info(
            f"Installed bundled assistant skills to {target}: {', '.join(installed)}"
        )
        return True

    return False
