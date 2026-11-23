"""The extension's entry point: `bond mcp` with the settings Claude Desktop passes in the environment."""

import sys

from bond.local.desktop import main

sys.exit(main())
