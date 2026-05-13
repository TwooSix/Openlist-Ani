"""Prepare packaged assistant skills and migrate legacy copied skills."""

from __future__ import annotations

import hashlib
import shlex
import shutil
from importlib import resources
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

from loguru import logger

BUILTIN_CACHE_DIR_NAME = ".builtin_skills"
LEGACY_MANIFEST_FILENAME = ".openlist-ani-builtin-skills.json"
LEGACY_UPDATES_DIR_NAME = ".openlist-ani-updates"
LEGACY_ROOT_SKILLS_VERSION = "v1.0.0.dev260426"

# Hashes of copied built-in skill directories from the old model where bundled
# skills lived in the user-editable skills/ directory. This table is only for
# one-time migration away from that model, so it should not grow after the new
# built-in cache loader ships.
LEGACY_COPIED_BUILTIN_SKILL_HASHES: dict[str, dict[str, str]] = {
    "anime-download": {
        "e8ef39958a3b9e1b43c15789050ee9b468f8863e4680ec2ad7b23435435f1340": (
            LEGACY_ROOT_SKILLS_VERSION
        ),
    },
    "anime-recommend": {
        "dc3fc0cf2859761d1647ede91c2ed5b20502d4c0022aec283961735edb821e32": (
            LEGACY_ROOT_SKILLS_VERSION
        ),
        "146ccf7d4734d0db2b182d04d78ed46c82d8d768fc2e003a54657e30fec19b58": (
            "pre-release:e803f60"
        ),
    },
    "anime-search": {
        "7bff37877f7f4aa2e053ddd1a8f96e03777397e71a0aa37503ed249b2568f856": (
            LEGACY_ROOT_SKILLS_VERSION
        ),
    },
    "bangumi": {
        "5a656ac42b42ec46e3f270b76d72ced31b98e4b73e91a966197fb2a1e69e6b25": (
            LEGACY_ROOT_SKILLS_VERSION
        ),
    },
    "mikan": {
        "9fbd7555f9bc24b9ffcc931a19fefd6ec2f53255ae9de5e0e92298508a17b948": (
            LEGACY_ROOT_SKILLS_VERSION
        ),
    },
    "oani": {
        "c87475d80b3ab28ae4d10004e8ea1c019832fe73f74243257874b9bac2435607": (
            LEGACY_ROOT_SKILLS_VERSION
        ),
        "6a9f901f3e6769ddb5e5d53172796652379ee4448ff2b07a48f21c8a9ac05a6a": (
            "pre-release:e803f60"
        ),
    },
}


def _ignore_generated_files(_dir: str, names: list[str]) -> list[str]:
    return [
        name
        for name in names
        if name == "__pycache__" or name.endswith((".pyc", ".pyo"))
    ]


def _is_generated_path(path: Path) -> bool:
    return any(
        part == "__pycache__" or part.endswith((".pyc", ".pyo")) for part in path.parts
    )


def _hash_path(path: Path) -> str:
    hasher = hashlib.sha256()

    if path.is_file():
        hasher.update(b"file\0")
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        return hasher.hexdigest()

    if not path.is_dir():
        raise FileNotFoundError(path)

    hasher.update(b"dir\0")
    for child in sorted(path.rglob("*"), key=lambda p: p.relative_to(path).as_posix()):
        rel = child.relative_to(path)
        if _is_generated_path(rel):
            continue
        if not child.is_file():
            continue
        hasher.update(rel.as_posix().encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(child.read_bytes())
        hasher.update(b"\0")

    return hasher.hexdigest()


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_tree(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        destination,
        ignore=_ignore_generated_files,
    )


def _replace_tree(source: Path, destination: Path) -> None:
    temp = destination.with_name(f".{destination.name}.tmp-openlist-ani")
    _remove_path(temp)
    try:
        _copy_tree(source, temp)
        _remove_path(destination)
        temp.replace(destination)
    finally:
        _remove_path(temp)


def _current_package_version() -> str:
    try:
        return package_version("openlist-ani")
    except PackageNotFoundError:
        return "unknown"


def _builtin_resource_root():
    return resources.files("openlist_ani").joinpath("builtin_skills", "skills")


def _cache_dir_for(data_dir: Path, bundled_path: Path) -> Path:
    package_version_value = _current_package_version().replace("/", "_")
    root_hash = _hash_path(bundled_path)[:16]
    return (
        data_dir.expanduser()
        / BUILTIN_CACHE_DIR_NAME
        / (f"{package_version_value}-{root_hash}")
    )


def prepare_builtin_skills_cache(data_dir: Path) -> Path:
    """Materialize packaged built-in skills into an internal cache directory."""
    bundled = _builtin_resource_root()
    with resources.as_file(bundled) as bundled_path:
        if not bundled_path.is_dir():
            raise FileNotFoundError("Bundled skills directory is not available")

        cache_dir = _cache_dir_for(data_dir, bundled_path)
        if not cache_dir.exists() or _hash_path(cache_dir) != _hash_path(bundled_path):
            _replace_tree(bundled_path, cache_dir)
            logger.info(f"Prepared bundled assistant skills cache at {cache_dir}")

        _remove_other_builtin_caches(cache_dir)
        return cache_dir


def _remove_other_builtin_caches(current_cache_dir: Path) -> None:
    cache_root = current_cache_dir.parent
    if not cache_root.exists():
        return
    for entry in cache_root.iterdir():
        if entry != current_cache_dir:
            _remove_path(entry)


def migrate_legacy_copied_builtin_skills(
    user_skills_dir: Path,
    builtin_skills_dir: Path,
) -> None:
    """Remove old copied built-ins from user skills and warn on real overrides."""
    user_root = user_skills_dir.expanduser()
    if not user_root.exists() or not user_root.is_dir():
        return

    for builtin_skill in sorted(builtin_skills_dir.iterdir()):
        if not builtin_skill.is_dir():
            continue

        local_skill = user_root / builtin_skill.name
        if not local_skill.exists() or not local_skill.is_dir():
            continue

        local_hash = _hash_path(local_skill)
        current_hash = _hash_path(builtin_skill)
        if local_hash == current_hash:
            _remove_path(local_skill)
            logger.info(
                f"Removed legacy copied built-in assistant skill: {local_skill}"
            )
            continue

        legacy_version = LEGACY_COPIED_BUILTIN_SKILL_HASHES.get(
            builtin_skill.name,
            {},
        ).get(local_hash)
        if legacy_version:
            _remove_path(local_skill)
            logger.info(
                "Removed legacy copied built-in assistant skill "
                f"{local_skill} from {legacy_version}"
            )
            continue

        _warn_user_override(builtin_skill.name, local_skill, builtin_skill)

    _remove_path(user_root / LEGACY_MANIFEST_FILENAME)
    _remove_path(user_root / LEGACY_UPDATES_DIR_NAME)


def _warn_user_override(
    skill_name: str, local_skill: Path, builtin_skill: Path
) -> None:
    local_arg = shlex.quote(str(local_skill))
    builtin_arg = shlex.quote(str(builtin_skill))
    logger.warning(
        f"{local_skill} overrides bundled skill '{skill_name}'. "
        "A newer bundled version is available, but this local copy does not "
        "match any known bundled version, so it may contain user changes. "
        f"Compare with: diff -ru {local_arg} {builtin_arg}. "
        f"To use the bundled version, remove or rename {local_arg} and restart."
    )
