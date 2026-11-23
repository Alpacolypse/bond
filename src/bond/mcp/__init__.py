"""Bond for agents, over MCP: the authoring tools, and the document in the chat.

- server: the authoring tools over whatever workspaces a deployment offers (create_server), with the app view and
  `show` given the view's HTML.
- view: the document, its citations and the form for adding sources, as an MCP App, fetched through app-only
  tools; a deployment can add app-only tools of its own (ViewTools).

Built on the core, and on bond.http for the view's reads. It decides nothing about where workspaces live or who
may reach them: it serves the Workspaces it is given. tests/test_layers.py checks that it never imports bond.local.
"""
