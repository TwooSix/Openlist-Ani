from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_application_uses_clean_architecture_packages():
    src = ROOT / "src" / "openlist_ani"

    top_level_packages = {path.name for path in src.iterdir() if path.is_dir()}
    assert {
        "bootstrap",
        "adapters",
        "application",
        "domain",
        "integrations",
    } <= top_level_packages
    assert (src / "adapters" / "inbound").is_dir()
    assert (src / "adapters" / "outbound").is_dir()

    assert not (src / "backend").exists()
    assert not (src / "core").exists()
    assert not (src / "config.py").exists()
    assert not (src / "database.py").exists()
    assert not (src / "composition").exists()
    assert not (src / "interfaces").exists()
    assert not (src / "infrastructure").exists()
