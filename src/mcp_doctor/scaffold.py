"""Compatibility alias for vexar_mcp.scaffold; removed in 0.4."""

import sys
from importlib import import_module

from vexar_mcp.scaffold import *  # noqa: F403

sys.modules[__name__] = import_module("vexar_mcp.scaffold")
