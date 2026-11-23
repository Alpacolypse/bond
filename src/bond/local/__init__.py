"""Bond on one person's machine: the Bond home, the decisions they make in it, and the Claude Desktop extension.

- home: where their workspaces live, by name, and the links and grants they made (home.db).
- grants: `bond mcp --grants NAME`, offering what a person granted (a bond.workspaces.Workspaces), with the
  picker in the chat (on bond.mcp's view).
- desktop: the extension's settings, as a `bond mcp` command.

This is one deployment of Bond: it decides where workspaces live and who may reach them, which the core, bond.mcp
and bond.http leave to whoever opens the workspaces. A deployment on its own stores and access control leaves it
out, and implements Workspaces itself.
"""

from .home import DECISIONS, ENV, Home, check_config, check_name, default_home, is_path

__all__ = ["DECISIONS", "ENV", "Home", "check_config", "check_name", "default_home", "is_path"]
