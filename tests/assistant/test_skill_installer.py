"""Tests for bundled assistant skill cache and legacy migration."""

from __future__ import annotations

from pathlib import Path

from openlist_ani.assistant.skill import installer
from openlist_ani.assistant.skill.catalog import SkillCatalog


def _write_skill(root: Path, name: str, body: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: demo skill\n---\n{body}",
        encoding="utf-8",
    )
    return skill_dir


def _use_bundled_root(monkeypatch, package_root: Path) -> None:
    monkeypatch.setattr(installer.resources, "files", lambda _package: package_root)


def _write_packaged_skill(package_root: Path, name: str, body: str) -> Path:
    return _write_skill(package_root / "builtin_skills" / "skills", name, body)


def _read_skill(skills_dir: Path, name: str) -> str:
    return (skills_dir / name / "SKILL.md").read_text(encoding="utf-8")


def test_prepares_builtin_skills_cache_from_package_resources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "package"
    _write_packaged_skill(package_root, "demo", "# bundled\n")
    _use_bundled_root(monkeypatch, package_root)

    cache_dir = installer.prepare_builtin_skills_cache(tmp_path / "data")

    assert cache_dir.is_dir()
    assert _read_skill(cache_dir, "demo").endswith("# bundled\n")


def test_catalog_loads_builtin_and_user_override(
    tmp_path: Path,
) -> None:
    builtin_dir = tmp_path / "builtin"
    user_dir = tmp_path / "skills"
    _write_skill(builtin_dir, "demo", "# bundled\n")
    _write_skill(user_dir, "demo", "# user override\n")
    _write_skill(user_dir, "custom", "# custom\n")

    catalog = SkillCatalog(builtin_skills_dir=builtin_dir, user_skills_dir=user_dir)
    catalog.discover()

    assert {skill.name for skill in catalog.all_skills()} == {"demo", "custom"}
    assert catalog.get_skill_content("demo") == "# user override"


def test_deletes_current_copied_builtin_from_user_skills(
    tmp_path: Path,
) -> None:
    builtin_dir = tmp_path / "builtin"
    user_dir = tmp_path / "skills"
    _write_skill(builtin_dir, "demo", "# bundled current\n")
    _write_skill(user_dir, "demo", "# bundled current\n")

    installer.migrate_legacy_copied_builtin_skills(user_dir, builtin_dir)

    assert not (user_dir / "demo").exists()


def test_deletes_known_legacy_copied_builtin_from_user_skills(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builtin_dir = tmp_path / "builtin"
    user_dir = tmp_path / "skills"
    _write_skill(builtin_dir, "demo", "# bundled current\n")
    legacy_dir = _write_skill(user_dir, "demo", "# old bundled\n")
    legacy_hash = installer._hash_path(legacy_dir)
    monkeypatch.setattr(
        installer,
        "LEGACY_COPIED_BUILTIN_SKILL_HASHES",
        {"demo": {legacy_hash: "old-release"}},
    )

    installer.migrate_legacy_copied_builtin_skills(user_dir, builtin_dir)

    assert not (user_dir / "demo").exists()


def test_preserves_unrecognized_builtin_override_and_warns(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builtin_dir = tmp_path / "builtin"
    user_dir = tmp_path / "skills"
    _write_skill(builtin_dir, "demo", "# bundled current\n")
    _write_skill(user_dir, "demo", "# user override\n")
    warnings: list[str] = []
    monkeypatch.setattr(installer.logger, "warning", warnings.append)

    installer.migrate_legacy_copied_builtin_skills(user_dir, builtin_dir)

    assert _read_skill(user_dir, "demo").endswith("# user override\n")
    assert len(warnings) == 1


def test_leaves_non_builtin_user_skill_alone(tmp_path: Path) -> None:
    builtin_dir = tmp_path / "builtin"
    user_dir = tmp_path / "skills"
    _write_skill(builtin_dir, "demo", "# bundled current\n")
    _write_skill(user_dir, "custom", "# custom\n")

    installer.migrate_legacy_copied_builtin_skills(user_dir, builtin_dir)

    assert (user_dir / "custom").exists()
