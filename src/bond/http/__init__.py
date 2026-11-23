"""Bond for software over HTTP: the read-only JSON API in ui/API.md (server), built from whole-workspace reads
(viewer), and the built UI that `bond view` serves with it. Needs the `view` extra.

Built on the core alone; bond.mcp's view answers the same API in process. It never imports bond.mcp or bond.local.
"""
