"""Intentionally flawed MCP server that hides its sinks behind module-local helpers.

`vulnerable_server.py` shows sinks written directly in a tool body. This file shows the case
that used to be invisible: the tool looks clean, and the dangerous call lives one hop away in
a private helper -- the shape most production MCP servers actually have.

Scanned by CI as part of `examples/`, and by `tests/test_rules.py` for the propagation rules:

    vexar-mcp scan examples/helper_delegation_server.py

Expected findings:

* `run_backup`      -> MCP005 (shell), attributed to the parameter `command`
* `download_report` -> MCP006 (SSRF) and MCP010 (no timeout)
* `read_note`       -> MCP007 is *not* raised: the helper confines the path itself
* `rotate_logs`     -> nothing: the helper runs a constant command the tool cannot influence
"""

from __future__ import annotations

import os
import subprocess
import urllib.request
from pathlib import Path


class _StubMCP:
    def tool(self, name: str | None = None, description: str | None = None):  # type: ignore[no-untyped-def]
        def _wrap(fn):  # type: ignore[no-untyped-def]
            return fn

        return _wrap


mcp = _StubMCP()

BASE_DIR = Path("/var/lib/notes")


def _run_cmd(cmd: str) -> int:
    return subprocess.run(cmd, shell=True, check=False).returncode


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as response:  # type: ignore[no-untyped-call]
        return bytes(response.read())


def _confined_read(name: str) -> str:
    target = (BASE_DIR / name).resolve()
    if not target.is_relative_to(BASE_DIR):
        raise ValueError("path escapes the notes directory")
    return target.read_text(encoding="utf-8")


def _rotate() -> int:
    return os.system("logrotate /etc/logrotate.d/notes")


@mcp.tool()
def run_backup(command: str) -> str:
    """Run a backup command on the server and report the exit status."""
    return f"exit={_run_cmd(command)}"


@mcp.tool()
def download_report(url: str) -> str:
    """Download a report from a URL and return its size in bytes."""
    return f"bytes={len(_fetch(url))}"


@mcp.tool()
def read_note(note_name: str) -> str:
    """Read a note from the notes directory by file name."""
    return _confined_read(note_name)


@mcp.tool()
def rotate_logs(retention_days: int) -> str:
    """Rotate the service logs and report the retention window that was applied."""
    _rotate()
    return f"rotated, retaining {retention_days} days"
