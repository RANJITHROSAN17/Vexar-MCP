"""The legacy namespace must not create a second copy of enums/model classes."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

import mcp_doctor
import vexar_mcp


@pytest.mark.parametrize(
    "name", ["ScanConfig", "Diagnostic", "Severity", "scan_paths", "scan_server"]
)
def test_legacy_public_api_is_identical(name: str) -> None:
    assert getattr(mcp_doctor, name) is getattr(vexar_mcp, name)
    assert mcp_doctor.__version__ == vexar_mcp.__version__


MODULES = sorted(
    ".".join(p.relative_to(Path(__file__).parents[1] / "src/vexar_mcp").with_suffix("").parts)
    for p in (Path(__file__).parents[1] / "src/vexar_mcp").rglob("*.py")
    if p.name not in {"__init__.py", "__main__.py"}
)


@pytest.mark.parametrize("module", MODULES)
def test_legacy_submodules_are_canonical_aliases(module: str) -> None:
    assert importlib.import_module(f"mcp_doctor.{module}") is importlib.import_module(
        f"vexar_mcp.{module}"
    )


@pytest.mark.parametrize("package", ["vexar_mcp", "mcp_doctor", "vexar_mcp.cli", "mcp_doctor.cli"])
def test_both_module_entry_points_work(package: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", package, "--version"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert vexar_mcp.__version__ in result.stdout


def test_legacy_pricing_loader_uses_canonical_package_resources() -> None:
    from mcp_doctor.simulate.cost_model import load_pricing_table

    assert load_pricing_table().entries
