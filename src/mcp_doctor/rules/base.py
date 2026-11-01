"""Compatibility alias for vexar_mcp.rules.base; removed in 0.4."""

import sys
from importlib import import_module

from vexar_mcp.rules.base import *  # noqa: F403

sys.modules[__name__] = import_module("vexar_mcp.rules.base")
