"""Live MCP server introspection over stdio JSON-RPC 2.0.

Static analysis cannot see tools that a server generates at runtime, gates behind
feature flags, or exposes only after a database migration. This module launches an MCP
server the way a client would, performs the real ``initialize`` handshake, calls
``tools/list`` (following pagination cursors), and feeds the response through the same
normalisation and rule pipeline used for files.

Safety rails, because "run an untrusted process" deserves them:

* the command is an explicit argv list - no shell, no string interpolation;
* the whole exchange is bounded by ``timeout`` seconds; the child is killed on expiry;
* each response is size-capped so a hostile server cannot exhaust memory;
* the child's own stdin is closed after the conversation and stderr is captured only
  as a short tail for diagnostics;
* server output is parsed as JSON only, never evaluated.

Results are deliberately *not* treated as trusted: protocol deviations become
:class:`~mcp_doctor.models.Diagnostic` findings under rule ``MCP012`` so a broken or
outdated server is reported rather than silently producing an empty clean scan.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import IO, Any

from mcp_doctor import __version__
from mcp_doctor.loaders.json_manifest import parse_tool_entries
from mcp_doctor.models import Category, Diagnostic, Severity, ToolSpec

# Protocol revisions this build knows how to converse with. Newest last; selection always
# prefers the newest mutually supported revision.
SUPPORTED_PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
    "2026-07-28",
)
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

#: Revisions from 2026-07-28 on are stateless: the handshake is gone and each request declares
#: the version it uses in ``params._meta[VERSION_META_KEY]``.
VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
STATELESS_PROTOCOL_VERSIONS = frozenset({"2026-07-28"})
DISCOVER_METHOD = "server/discover"

_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_PAGES = 50
_STDERR_TAIL_CHARS = 800


class LiveAuditError(RuntimeError):
    """Raised when a live server cannot be launched or does not speak JSON-RPC."""


@dataclass(frozen=True)
class LiveServerSpec:
    """How to launch and talk to one MCP server."""

    command: tuple[str, ...]
    timeout: float = 30.0
    protocol_version: str = DEFAULT_PROTOCOL_VERSION
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_command_line(
        cls, command: str, *, timeout: float = 30.0, protocol_version: str | None = None
    ) -> LiveServerSpec:
        """Build a spec from a shell-style command string.

        Only simple whitespace splitting is used; there is no shell expansion, so
        metacharacters are passed through literally to the server process.
        """
        parts = command.split()
        if not parts:
            raise LiveAuditError("empty command string for live introspection")
        return cls(
            command=tuple(parts),
            timeout=timeout,
            protocol_version=protocol_version or DEFAULT_PROTOCOL_VERSION,
        )


@dataclass(frozen=True)
class LiveAuditResult:
    """Outcome of introspecting one running MCP server."""

    tools: tuple[ToolSpec, ...]
    protocol_version: str
    server_name: str
    server_version: str
    duration_ms: int
    pages: int
    #: How the revision was agreed: "handshake" (initialize) or "stateless" (server/discover).
    #: Recorded because the two modes mean different things for what an audit can prove.
    mode: str = "handshake"
    stderr_tail: str = ""
    issues: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        """True when the handshake and tool listing completed without protocol issues."""
        return not self.issues


class _JsonRpcClient:
    """Minimal newline-delimited JSON-RPC 2.0 client bound to a child process."""

    def __init__(
        self,
        process: subprocess.Popen[str],
        deadline: float,
        *,
        protocol_version: str | None = None,
    ) -> None:
        self._process = process
        self._deadline = deadline
        self.protocol_version: str | None = None
        self._next_id = 0
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._stderr_buf: list[str] = []
        assert process.stdout is not None
        assert process.stderr is not None
        self._stdout_thread = threading.Thread(
            target=self._pump, args=(process.stdout, self._lines), daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._pump, args=(process.stderr, None), daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _pump(self, stream: IO[str], sink: queue.Queue[str | None] | None) -> None:
        try:
            for line in stream:
                if sink is None:
                    if len(self._stderr_buf) < 200:
                        self._stderr_buf.append(line)
                    continue
                sink.put(line)
        except (OSError, ValueError):
            pass
        finally:
            if sink is not None:
                sink.put(None)

    @property
    def stderr_tail(self) -> str:
        """Return a short tail of the child's stderr for troubleshooting."""
        return "".join(self._stderr_buf)[-_STDERR_TAIL_CHARS:].strip()

    def _send(self, payload: dict[str, Any]) -> None:
        assert self._process.stdin is not None
        try:
            self._process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise LiveAuditError(f"failed to write to MCP server stdin: {exc}") from exc

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def set_protocol_version(self, version: str | None) -> None:
        """Declare the protocol version carried on every subsequent request.

        From the stateless revisions on, the version is a per-request fact rather than a
        connection-level negotiation, so it belongs in ``params._meta``.
        """
        self.protocol_version = version

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a request and return the matching response object."""
        self._next_id += 1
        request_id = self._next_id
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        merged = dict(params or {})
        if self.protocol_version:
            meta = dict(merged.get("_meta") or {})
            meta.setdefault(VERSION_META_KEY, self.protocol_version)
            merged["_meta"] = meta
        if merged:
            payload["params"] = merged
        self._send(payload)

        consumed = 0
        while True:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise LiveAuditError(f"timed out waiting for response to '{method}'")
            try:
                line = self._lines.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self._process.poll() is not None:
                    raise LiveAuditError(
                        f"MCP server exited with code {self._process.returncode} during '{method}'"
                    ) from None
                continue
            if line is None:
                raise LiveAuditError(f"MCP server closed stdout during '{method}'")
            consumed += len(line)
            if consumed > _MAX_RESPONSE_BYTES:
                raise LiveAuditError(f"response to '{method}' exceeded {_MAX_RESPONSE_BYTES} bytes")
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = json.loads(stripped)
            except json.JSONDecodeError:
                # Servers that print banners to stdout are common; skip non-JSON lines.
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") != request_id:
                continue  # notifications / unrelated responses
            if "error" in message:
                error = message.get("error")
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise LiveAuditError(f"server rejected '{method}': {detail}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise LiveAuditError(f"server returned a non-object result for '{method}'")
            return result


def _discover(client: _JsonRpcClient, handshake_error: LiveAuditError) -> dict[str, Any]:
    """Negotiate with a stateless server via the mandatory ``server/discover`` RPC.

    Raises:
        LiveAuditError: If discovery is unavailable too. The message names both failures,
            because "the server is broken" and "we speak a protocol it removed" look
            identical from the outside and only one of them is our problem.
    """
    try:
        result = client.request(DISCOVER_METHOD)
    except LiveAuditError as discover_error:
        raise LiveAuditError(
            f"cannot negotiate with this server. The handshake was rejected "
            f"({handshake_error}) and `{DISCOVER_METHOD}` is unavailable ({discover_error}). "
            f"Revisions from 2026-07-28 are stateless and must implement `{DISCOVER_METHOD}`; "
            f"older servers must accept `initialize`."
        ) from discover_error
    if not isinstance(result.get("protocolVersions"), list):
        raise LiveAuditError(
            f"`{DISCOVER_METHOD}` returned no `protocolVersions` list, so the protocol "
            "revision cannot be negotiated."
        )
    return result


def select_protocol_version(supported: Sequence[str]) -> str | None:
    """Return the newest revision both sides support, or ``None`` when they share none.

    Revisions are ``YYYY-MM-DD`` strings, so lexicographic order is chronological order.
    """
    mutual = [version for version in supported if version in SUPPORTED_PROTOCOL_VERSIONS]
    return max(mutual) if mutual else None


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:  # pragma: no cover - windows
            process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover - defensive
        with suppress(OSError):
            process.kill()


def introspect_server(spec: LiveServerSpec) -> LiveAuditResult:
    """Launch an MCP server, complete the handshake, and collect its tool catalog.

    Raises:
        LiveAuditError: If the server cannot be started, times out, or speaks something
            other than JSON-RPC 2.0.
    """
    if not spec.command:
        raise LiveAuditError("no command provided for live introspection")

    started = time.monotonic()
    deadline = started + spec.timeout
    env = {**os.environ, **spec.env}
    try:
        process = subprocess.Popen(
            list(spec.command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=os.name == "posix",
        )
    except FileNotFoundError as exc:
        raise LiveAuditError(
            f"cannot launch MCP server, executable not found: {spec.command[0]}"
        ) from exc
    except OSError as exc:
        raise LiveAuditError(f"cannot launch MCP server: {exc}") from exc

    issues: list[str] = []
    negotiated = spec.protocol_version
    server_name = ""
    server_version = ""
    pages = 0
    client = _JsonRpcClient(process, deadline)
    try:
        mode = "handshake"
        try:
            init = client.request(
                "initialize",
                {
                    "protocolVersion": spec.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "vexar-mcp", "version": __version__},
                },
            )
        except LiveAuditError as handshake_error:
            # The stateless revisions removed the handshake entirely. Rather than reporting
            # the server as broken, discover what it actually speaks and talk that.
            init = _discover(client, handshake_error)
            mode = "stateless"

        if mode == "stateless":
            advertised = init.get("protocolVersions")
            advertised_versions = (
                [str(v) for v in advertised] if isinstance(advertised, list) else []
            )
            chosen = select_protocol_version(advertised_versions)
            if chosen is None:
                raise LiveAuditError(
                    "this server speaks no protocol revision Vexar-MCP supports "
                    f"(server: {', '.join(advertised_versions) or 'none advertised'}; "
                    f"Vexar-MCP: {', '.join(SUPPORTED_PROTOCOL_VERSIONS)})"
                )
            negotiated = chosen
            client.set_protocol_version(chosen)
        else:
            negotiated = str(init.get("protocolVersion", ""))
            if not negotiated:
                issues.append(
                    "Server omitted `protocolVersion` in the initialize result (MCP requires it)."
                )
            elif negotiated not in SUPPORTED_PROTOCOL_VERSIONS:
                supported = ", ".join(SUPPORTED_PROTOCOL_VERSIONS)
                issues.append(
                    f"Server negotiated unsupported protocolVersion '{negotiated}'. "
                    f"Vexar-MCP supports: {supported}."
                )
            client.notify("notifications/initialized")

        capabilities = init.get("capabilities")
        has_tools = isinstance(capabilities, dict) and "tools" in capabilities
        if not has_tools:
            issues.append(
                "Server did not advertise the `tools` capability; tools/list may be empty or "
                "rejected by clients."
            )

        entries: list[Any] = []
        cursor: str | None = None
        while pages < _MAX_PAGES:
            params = {"cursor": cursor} if cursor else {}
            result = client.request("tools/list", params)
            page_tools = result.get("tools", [])
            if not isinstance(page_tools, list):
                raise LiveAuditError("tools/list returned a non-list 'tools' field")
            entries.extend(page_tools)
            pages += 1
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        if pages >= _MAX_PAGES:
            issues.append(
                f"tools/list pagination exceeded {_MAX_PAGES} pages; results may be truncated."
            )

        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                issues.append(f"tools/list entry {index} is not a JSON object.")
                continue
            if not entry.get("name"):
                issues.append(f"tools/list entry {index} has no `name`.")
            schema = entry.get("inputSchema")
            if schema is None:
                issues.append(f"Tool '{entry.get('name', index)}' has no `inputSchema`.")
            elif not isinstance(schema, dict):
                issues.append(f"Tool '{entry.get('name', index)}' has a non-object `inputSchema`.")

        server_info = init.get("serverInfo")
        if isinstance(server_info, dict):
            server_name = str(server_info.get("name", ""))
            server_version = str(server_info.get("version", ""))

        tools = tuple(parse_tool_entries(entries, f"mcp://{server_name or spec.command[0]}"))
    finally:
        # Order matters: terminate the child *first* so the reader threads see EOF, then
        # close the pipes. Closing a pipe while another thread is blocked reading it can
        # deadlock until the child happens to exit, which would hang the audit.
        _terminate(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            with suppress(OSError, ValueError):
                if stream is not None:
                    stream.close()

    return LiveAuditResult(
        tools=tools,
        protocol_version=negotiated,
        mode=mode,
        server_name=server_name,
        server_version=server_version,
        duration_ms=int((time.monotonic() - started) * 1000),
        pages=pages,
        stderr_tail=client.stderr_tail,
        issues=tuple(issues),
    )


def protocol_diagnostics(result: LiveAuditResult) -> tuple[Diagnostic, ...]:
    """Convert live protocol deviations into MCP012 diagnostics."""
    target = f"mcp://{result.server_name or 'server'}"
    return tuple(
        Diagnostic(
            rule_id="MCP012",
            rule_slug="live-protocol-violation",
            severity=Severity.WARNING,
            category=Category.CONTRACT,
            message=issue,
            source_file=target,
            line=1,
            tool_name=result.server_name or "mcp-server",
            remediation=(
                "Re-run the server against a supported MCP revision, advertise the `tools` "
                "capability, and return `inputSchema` objects from tools/list."
            ),
        )
        for issue in result.issues
    )


__all__ = [
    "DEFAULT_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "LiveAuditError",
    "LiveAuditResult",
    "LiveServerSpec",
    "introspect_server",
    "protocol_diagnostics",
]
