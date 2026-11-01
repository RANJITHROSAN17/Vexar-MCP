"""Compatibility alias for vexar_mcp.rules.token_rules; removed in 0.4."""

import sys
from importlib import import_module

from vexar_mcp.rules.token_rules import *  # noqa: F403

sys.modules[__name__] = import_module("vexar_mcp.rules.token_rules")
