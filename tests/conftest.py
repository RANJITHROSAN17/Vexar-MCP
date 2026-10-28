"""Shared pytest fixtures.

Vexar-MCP's rule registry is process-global by design (a CLI loads packs once, then
scans once). That makes test order matter unless every test starts from the built-in
catalog, so this fixture brackets each test with a registry snapshot and clears the
loaded-pack cache.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from mcp_doctor.rules import restore_registry, snapshot_registry
from mcp_doctor.rules.plugin import reset_packs


@pytest.fixture(autouse=True)
def _isolated_rule_registry() -> Iterator[None]:
    snapshot = snapshot_registry()
    try:
        yield
    finally:
        restore_registry(snapshot)
        reset_packs()
