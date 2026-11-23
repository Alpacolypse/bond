"""Build Bond's Claude Desktop extension: dist/bond-<version>.mcpb.

    uv run python packaging/mcpb/build.py [--out dist]

An .mcpb is a zip with manifest.json at its root. Bond's is a `uv` extension: Claude Desktop installs Python and
the dependencies itself from the bundle's pyproject.toml, then runs server/main.py. Bond isn't on PyPI, so the
bundle carries Bond's source (src/bond, with the built viewer and app view) and a pyproject.toml whose
dependencies include the `mcp` extra's, since a host runs a project without extras. Build the UI first
(cd ui && npm run build): the extension without its view would be text only.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
NEEDED = ("src/bond/static/app/index.html", "src/bond/static/viewer/index.html")


def pyproject(source: dict) -> str:
    """The bundle's pyproject.toml: Bond's project, with the mcp extra's dependencies made its own."""
    project = source["project"]
    dependencies = sorted({*project["dependencies"], *project["optional-dependencies"]["mcp"]})
    lines = [
        "[project]",
        f'name = "{project["name"]}"',
        f'version = "{project["version"]}"',
        f'description = "{project["description"]}"',
        f'requires-python = "{project["requires-python"]}"',
        "dependencies = [",
        *[f'    "{d}",' for d in dependencies],
        "]",
        "",
        "[build-system]",
        f"requires = {json.dumps(source['build-system']['requires'])}",
        f'build-backend = "{source["build-system"]["build-backend"]}"',
        "",
    ]
    return "\n".join(lines)


def build(out: Path) -> Path:
    missing = [p for p in NEEDED if not (ROOT / p).is_file()]
    if missing:
        raise SystemExit(f"build the UI first (cd ui && npm run build): missing {', '.join(missing)}")
    source = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    if manifest["version"] != source["project"]["version"]:
        raise SystemExit(f"manifest.json says version {manifest['version']}, pyproject.toml "
                         f"{source['project']['version']}: make them agree")
    out.mkdir(parents=True, exist_ok=True)
    bundle = out / f"bond-{manifest['version']}.mcpb"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        z.writestr("pyproject.toml", pyproject(source))
        z.write(HERE / "server" / "main.py", "server/main.py")
        for path in sorted((ROOT / "src" / "bond").rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"):
                z.write(path, path.relative_to(ROOT).as_posix())
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="where to write the .mcpb (default: dist)")
    bundle = build(parser.parse_args(argv).out)
    print(f"built {bundle} ({bundle.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
