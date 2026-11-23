"""Bond as a Claude Desktop extension (.mcpb): how the extension's settings become a `bond mcp` command.

The extension (packaging/mcpb, built with packaging/mcpb/build.py) runs `bond mcp` through uv, with its settings
in the environment: BOND_HOME (the Bond home; empty means the platform's default) and BOND_WORKSPACES
(workspaces to bind statically, comma-separated). With none listed the extension starts in grant mode under the
grants name BOND_GRANTS ("desktop" unless set): the person grants workspaces from the picker in the chat.

A setting the person left empty isn't substituted: Claude Desktop passes its placeholder through as it stands
(`${user_config.workspaces}`). Such a value means unset, never a workspace or a directory by that name.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping

GRANTS = "desktop"
SETTINGS = ("BOND_HOME", "BOND_WORKSPACES", "BOND_GRANTS")
PLACEHOLDER = re.compile(r"\$\{[^}]*\}")


def settings(environ: Mapping[str, str]) -> dict[str, str]:
    """The extension's settings that were given: trimmed, without the empty ones or ones left as a placeholder."""
    given = {}
    for name in SETTINGS:
        value = environ.get(name, "").strip()
        if value and not PLACEHOLDER.search(value):
            given[name] = value
    return given


def command(environ: Mapping[str, str]) -> list[str]:
    """The `bond mcp` arguments the extension's settings ask for."""
    given = settings(environ)
    names = [n.strip() for n in given.get("BOND_WORKSPACES", "").split(",") if n.strip()]
    if names:
        return ["mcp", *[arg for name in names for arg in ("--workspace", name)]]
    return ["mcp", "--grants", given.get("BOND_GRANTS", GRANTS)]


def main() -> int:
    from ..cli import main as bond

    given = settings(os.environ)
    for name in SETTINGS:  # the home is read from the environment: an unset one must not reach it as a placeholder
        if name not in given:
            os.environ.pop(name, None)
        else:
            os.environ[name] = given[name]
    return bond(command(os.environ))


if __name__ == "__main__":
    sys.exit(main())
