"""The lines between Bond's layers: the core imports none of the APIs over it (bond.mcp for agents, bond.http for
software) nor the local deployment (bond.local); bond.http imports neither of the others, and bond.mcp never imports
bond.local. So a deployment can take the core alone, or with either API, on its own stores and access."""

from __future__ import annotations

import ast
from pathlib import Path

import bond

PACKAGE = Path(bond.__file__).parent
TIES = {"cli.py", "__main__.py"}  # the command line is where the layers meet


def imports(path: Path) -> set[str]:
    """The bond modules a file imports, as dotted names from the package root."""
    package = path.relative_to(PACKAGE.parent).parent.parts  # what a relative import is relative to
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = list(package[: len(package) - node.level + 1]) if node.level else []
            name = ".".join([*base, *(node.module.split(".") if node.module else [])])
            found |= {name} | {f"{name}.{a.name}" for a in node.names}
    return {n for n in found if n == "bond" or n.startswith("bond.")}


def reaching(files: list[Path], *layers: str) -> dict[str, list[str]]:
    """Each file that imports one of layers, with what it imports from them."""
    found = {str(p.relative_to(PACKAGE)): sorted(n for n in imports(p) if n.split(".")[1:2] in [[x] for x in layers])
             for p in files}
    return {p: names for p, names in found.items() if names}


def layer(name: str) -> list[Path]:
    return sorted((PACKAGE / name).rglob("*.py"))


def test_the_core_imports_no_api_and_no_deployment():
    core = [p for p in PACKAGE.rglob("*.py")
            if p.relative_to(PACKAGE).parts[0] not in ("mcp", "http", "local") and p.name not in TIES]
    assert PACKAGE / "workspaces.py" in core and PACKAGE / "sources.py" in core
    assert reaching(core, "mcp", "http", "local") == {}


def test_the_http_api_imports_neither_mcp_nor_the_local_deployment():
    assert layer("http") and reaching(layer("http"), "mcp", "local") == {}


def test_the_mcp_api_never_imports_the_local_deployment():
    assert layer("mcp") and reaching(layer("mcp"), "local") == {}


def test_the_check_sees_relative_imports_of_each_layer():
    assert {"bond.local", "bond.sources", "bond.http.viewer"} <= imports(PACKAGE / "cli.py")
    assert {"bond.mcp.server", "bond.mcp.view"} <= imports(PACKAGE / "local" / "grants.py")
    assert "bond.http.server" in imports(PACKAGE / "mcp" / "view.py")
