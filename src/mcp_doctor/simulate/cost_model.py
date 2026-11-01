"""Compatibility alias for vexar_mcp.simulate.cost_model; removed in 0.4."""

import sys
from importlib import import_module

from vexar_mcp.simulate.cost_model import *  # noqa: F403

sys.modules[__name__] = import_module("vexar_mcp.simulate.cost_model")
