"""The Claude Desktop extension: its settings become a bond mcp command, and its bundle carries what a host runs."""

from __future__ import annotations

import importlib.util
import json
import tomllib
import zipfile
from pathlib import Path

import pytest

from bond.cli import parse
from bond.local.desktop import command, settings

ROOT = Path(__file__).resolve().parents[1]


def test_with_no_workspaces_listed_the_extension_starts_in_grant_mode():
    assert command({}) == ["mcp", "--grants", "desktop"]
    assert command({"BOND_WORKSPACES": " , "}) == ["mcp", "--grants", "desktop"]
    assert command({"BOND_GRANTS": "work-laptop"}) == ["mcp", "--grants", "work-laptop"]
    assert command({"BOND_WORKSPACES": "smith, jones"}) == ["mcp", "--workspace", "smith", "--workspace", "jones"]
    assert parse(command({})).grants == "desktop"  # every command it makes is one bond mcp takes


def test_a_setting_left_empty_arrives_as_its_placeholder_and_means_unset():
    # What Claude Desktop passes for settings nobody filled in: the placeholders, unsubstituted.
    left_empty = {"BOND_HOME": "${user_config.bond_home}", "BOND_WORKSPACES": "${user_config.workspaces}"}
    assert command(left_empty) == ["mcp", "--grants", "desktop"]
    assert settings(left_empty) == {}  # so the home falls back to the platform's, not a directory named ${...}
    filled = {"BOND_HOME": " C:\\Users\\me\\Bond ", "BOND_WORKSPACES": "smith"}
    assert settings(filled) == {"BOND_HOME": "C:\\Users\\me\\Bond", "BOND_WORKSPACES": "smith"}


def test_the_manifest_runs_the_entry_point_with_uv_and_passes_the_settings():
    manifest = json.loads((ROOT / "packaging" / "mcpb" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["server"]["type"] == "uv" and manifest["manifest_version"] == "0.4"
    assert (ROOT / "packaging" / "mcpb" / manifest["server"]["entry_point"]).is_file()
    # mcp_config needs a command even for a uv server (the 0.4 schema); this is the official uv example's.
    config = manifest["server"]["mcp_config"]
    assert [config["command"], *config["args"]] == ["uv", "run", "--directory", "${__dirname}",
                                                     manifest["server"]["entry_point"]]
    env = config["env"]
    assert env == {"BOND_HOME": "${user_config.bond_home}", "BOND_WORKSPACES": "${user_config.workspaces}"}
    assert set(manifest["user_config"]) == {"bond_home", "workspaces"}
    assert manifest["version"] == tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


@pytest.mark.skipif(not (ROOT / "src/bond/static/app/index.html").is_file(), reason="the UI isn't built")
def test_the_bundle_carries_bonds_source_its_view_and_the_dependencies_a_host_installs(tmp_path):
    spec = importlib.util.spec_from_file_location("build_mcpb", ROOT / "packaging" / "mcpb" / "build.py")
    assert spec is not None and spec.loader is not None
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    bundle = build.build(tmp_path)
    with zipfile.ZipFile(bundle) as z:
        names = set(z.namelist())
        project = tomllib.loads(z.read("pyproject.toml").decode())["project"]
    assert {"manifest.json", "pyproject.toml", "server/main.py", "src/bond/cli.py",
            "src/bond/static/app/index.html"} <= names
    assert not any("__pycache__" in n for n in names)
    assert any(d.startswith("mcp") for d in project["dependencies"])  # the mcp extra, made a dependency
