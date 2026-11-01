"""Compatibility namespace for 0.3.x; migrate to vexar_mcp.reporters before 0.4."""

from importlib import import_module
from typing import Any

from vexar_mcp.reporters import *  # noqa: F403

_canonical = import_module("vexar_mcp.reporters")


def __getattr__(name: str) -> Any:
    return getattr(_canonical, name)
