import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "openlist_ani"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _py_files(path: Path) -> list[Path]:
    return [p for p in path.rglob("*.py") if "__pycache__" not in p.parts]


def test_domain_has_no_outer_layer_imports():
    forbidden = (
        "openlist_ani.application",
        "openlist_ani.adapters",
        "openlist_ani.bootstrap",
        "openlist_ani.integrations",
        "openlist_ani.config",
        "openlist_ani.database",
    )
    violations = []
    for path in _py_files(SRC / "domain"):
        for imported in _imports(path):
            if imported.startswith(forbidden):
                violations.append(f"{path.relative_to(ROOT)} imports {imported}")

    assert violations == []


def test_application_does_not_import_adapters_or_runtime_config():
    forbidden = (
        "openlist_ani.adapters",
        "openlist_ani.bootstrap",
        "openlist_ani.integrations",
        "openlist_ani.config",
        "openlist_ani.database",
    )
    violations = []
    for path in _py_files(SRC / "application"):
        for imported in _imports(path):
            if imported.startswith(forbidden):
                violations.append(f"{path.relative_to(ROOT)} imports {imported}")

    assert violations == []


def test_inbound_adapters_do_not_import_outbound_adapters():
    violations = []
    for path in _py_files(SRC / "adapters" / "inbound"):
        for imported in _imports(path):
            if imported.startswith("openlist_ani.adapters.outbound"):
                violations.append(f"{path.relative_to(ROOT)} imports {imported}")

    assert violations == []


def test_integrations_do_not_import_business_or_adapter_layers():
    forbidden = (
        "openlist_ani.domain",
        "openlist_ani.application",
        "openlist_ani.adapters",
        "openlist_ani.bootstrap",
        "openlist_ani.config",
        "openlist_ani.database",
    )
    violations = []
    for path in _py_files(SRC / "integrations"):
        for imported in _imports(path):
            if imported.startswith(forbidden):
                violations.append(f"{path.relative_to(ROOT)} imports {imported}")

    assert violations == []


def test_outbound_adapter_modules_do_not_import_sibling_adapter_modules():
    prefix = "openlist_ani.adapters.outbound."
    violations = []
    for path in _py_files(SRC / "adapters" / "outbound"):
        top_level_module = path.relative_to(SRC / "adapters" / "outbound").parts[0]
        for imported in _imports(path):
            if not imported.startswith(prefix):
                continue
            imported_top_level = imported[len(prefix) :].split(".", 1)[0]
            if imported_top_level != top_level_module:
                violations.append(f"{path.relative_to(ROOT)} imports {imported}")

    assert violations == []
