"""Compatibility alias for vexar_mcp.cli; removed in 0.4."""

import sys
from importlib import import_module

from vexar_mcp.cli import *  # noqa: F403
from vexar_mcp.cli import main

sys.modules[__name__] = import_module("vexar_mcp.cli")

if __name__ == "__main__":
    raise SystemExit(main())
