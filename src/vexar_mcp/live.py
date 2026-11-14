"""Live MCP server introspection over stdio JSON-RPC and Streamable HTTP.

Static analysis cannot see tools that a server generates at runtime, gates behind
feature flags, or exposes only after a database migration. This module launches a local MCP
server or connects to a remote HTTP endpoint the way a client would, negotiates the protocol,
calls ``tools/list`` (following pagination cursors), and feeds the response through the same
normalisation and rule pipeline used for files.

Safety rails, because "run an untrusted process or contact an untrusted endpoint" deserves
its own boundaries:

* local commands are explicit argv lists - no shell, no string interpolation;
* the whole exchange is bounded by ``timeout`` seconds; children are killed and HTTP sockets
  are closed on expiry;
* each response is size-capped, redirects are limited to the same origin, and remote DNS
  addresses are checked and pinned before connecting;
* HTTPS is required except for explicitly allowed loopback HTTP; private addresses require an
  opt-in;
* HTTP requests avoid shared caching and their authentication values are not reflected into
  reports or error text;
* server output is parsed as JSON or server-sent events, never evaluated.

Results are deliberately *not* treated as trusted: protocol deviations become
:class:`~vexar_mcp.models.Diagnostic` findings under rule ``MCP012`` so a broken or
outdated server is reported rather than silently producing an empty clean scan.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
import queue
import re
import signal
import socket
import ssl
import subprocess
import threading
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import IO, Any, Protocol
from urllib.parse import quote, urljoin, urlsplit

from vexar_mcp import __version__
from vexar_mcp.loaders.json_manifest import parse_tool_entries
from vexar_mcp.models import Category, Diagnostic, Severity, ToolSpec

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
_MAX_HTTP_REDIRECTS = 3
_STDERR_TAIL_CHARS = 800
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_HTTP_RESERVED_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "cache-control",
        "connection",
        "content-length",
        "content-type",
        "host",
        "expect",
        "mcp-protocol-version",
        "mcp-session-id",
        "origin",
        "pragma",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "user-agent",
    }
)


class LiveAuditError(RuntimeError):
    """Raised when a live server cannot be launched or does not speak JSON-RPC."""


@dataclass(frozen=True)
class LiveServerSpec:
    """How to launch and talk to one MCP server."""

    command: tuple[str, ...] = ()
    timeout: float = 30.0
    protocol_version: str = DEFAULT_PROTOCOL_VERSION
    env: dict[str, str] = field(default_factory=dict)
    #: Working directory for the child. Client configurations carry one, and ignoring it
    #: would audit a server from the wrong directory - which produces a plausible report
    #: about the wrong thing, the worst possible outcome for a diagnostic tool.
    cwd: str | None = None
    #: A Streamable HTTP endpoint. Exactly one of ``command`` or ``url`` may be set.
    url: str | None = None
    #: Permit cleartext HTTP only when every resolved address is loopback.
    allow_http_loopback: bool = False
    #: Permit HTTPS connections to private, loopback, link-local, and other non-public IPs.
    allow_private_network: bool = False
    #: Browser origin to send and check against the response's CORS policy. Defaults to the
    #: endpoint origin; it is never inferred from an HTTP redirect.
    origin: str | None = None
    #: Additional HTTP headers, typically Authorization. Values are never included in errors.
    headers: dict[str, str] = field(default_factory=dict)

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


class _RpcClient(Protocol):
    """Operations shared by the stdio and Streamable HTTP JSON-RPC clients."""

    issues: list[str]

    @property
    def stderr_tail(self) -> str: ...

    def set_protocol_version(self, version: str | None, *, stateless: bool = False) -> None: ...

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None: ...

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...


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
        self._stateless = False
        self.issues: list[str] = []
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

    def set_protocol_version(self, version: str | None, *, stateless: bool = False) -> None:
        """Set the negotiated revision for requests made after initialization."""
        self.protocol_version = version
        self._stateless = stateless

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a request and return the matching response object."""
        self._next_id += 1
        request_id = self._next_id
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        merged = dict(params or {})
        if self.protocol_version and self._stateless:
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


def _discover(client: _RpcClient, handshake_error: LiveAuditError) -> dict[str, Any]:
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


@dataclass(frozen=True)
class _HttpEndpoint:
    """Validated URL pieces used by the pinned-address HTTP transport."""

    url: str
    scheme: str
    host: str
    port: int
    origin: str
    target: str


def _http_endpoint(url: str) -> _HttpEndpoint:
    """Parse an HTTP endpoint without accepting URL credentials or ambiguous origins."""
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise LiveAuditError("remote MCP URL contains control characters")
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise LiveAuditError("remote MCP URL is malformed") from exc
    if scheme not in {"https", "http"}:
        raise LiveAuditError("remote MCP URL must use HTTPS")
    if not hostname or parts.username is not None or parts.password is not None:
        raise LiveAuditError(
            "remote MCP URL must include a hostname and must not contain credentials"
        )
    if parts.fragment:
        raise LiveAuditError("remote MCP URL must not contain a fragment")
    if "%" in hostname:
        # IPv6 zone identifiers select a local interface and are never appropriate for a
        # remote endpoint. They also make origin comparisons ambiguous.
        raise LiveAuditError("remote MCP URL must not contain a scoped IP address")
    if any(char.isspace() for char in hostname):
        raise LiveAuditError("remote MCP URL hostname is malformed")
    try:
        host = hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise LiveAuditError("remote MCP URL hostname is malformed") from exc
    with suppress(ValueError):
        host = ipaddress.ip_address(host).compressed
    if not host:
        raise LiveAuditError("remote MCP URL must include a hostname")
    default_port = 443 if scheme == "https" else 80
    selected_port = default_port if port is None else port
    if selected_port < 1:
        raise LiveAuditError("remote MCP URL port must be greater than zero")
    origin_host = f"[{host}]" if ":" in host else host
    origin = f"{scheme}://{origin_host}"
    if selected_port != default_port:
        origin += f":{selected_port}"
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="=&?/:@!$'()*+,;%-._~")
    target = path + (f"?{query}" if query else "")
    return _HttpEndpoint(url, scheme, host, selected_port, origin, target)


def _normalise_origin(value: str) -> str:
    """Validate and canonicalise an Origin header value (an origin, never a URL path)."""
    endpoint = _http_endpoint(value)
    parts = urlsplit(value)
    if parts.path not in {"", "/"} or parts.query:
        raise LiveAuditError("Origin must contain only a scheme, hostname, and optional port")
    return endpoint.origin


def _resolve_endpoint_addresses(
    endpoint: _HttpEndpoint,
    *,
    allow_http_loopback: bool,
    allow_private_network: bool,
    deadline: float,
) -> list[str]:
    """Resolve and check every address before connecting, then pin the chosen IP.

    Name resolution runs in a daemon thread so a slow DNS response cannot extend the audit
    past its total deadline. The connection itself uses the returned numeric address, while
    TLS verification and the HTTP Host header continue to use the original hostname.
    """
    if endpoint.scheme == "http" and not allow_http_loopback:
        raise LiveAuditError(
            "cleartext HTTP is refused; use HTTPS or explicitly allow HTTP for a loopback URL"
        )

    try:
        literal = ipaddress.ip_address(endpoint.host)
    except ValueError:
        resolved: queue.Queue[tuple[list[str], str | None]] = queue.Queue(maxsize=1)

        def lookup() -> None:
            try:
                records = socket.getaddrinfo(endpoint.host, endpoint.port, type=socket.SOCK_STREAM)
                found = list(
                    dict.fromkeys(
                        ipaddress.ip_address(record[4][0]).compressed for record in records
                    )
                )
                resolved.put((found, None))
            except (OSError, ValueError) as exc:
                resolved.put(([], type(exc).__name__))

        threading.Thread(target=lookup, daemon=True, name="vexar-mcp-dns").start()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LiveAuditError("timed out resolving the remote MCP hostname") from None
        try:
            addresses, error_name = resolved.get(timeout=remaining)
        except queue.Empty:
            raise LiveAuditError("timed out resolving the remote MCP hostname") from None
        if error_name is not None or not addresses:
            raise LiveAuditError("could not resolve the remote MCP hostname") from None
    else:
        addresses = [literal.compressed]

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if endpoint.scheme == "http" and not ip.is_loopback:
            raise LiveAuditError("cleartext HTTP is allowed only for loopback addresses")
        if not ip.is_global:
            loopback_http_exception = (
                endpoint.scheme == "http" and allow_http_loopback and ip.is_loopback
            )
            if not allow_private_network and not loopback_http_exception:
                raise LiveAuditError(
                    "refusing to connect to a private or non-public network address; "
                    "use the explicit private-network opt-in only for a trusted endpoint"
                )
    return addresses


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that connects to a previously validated numeric address."""

    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        self._address = address
        super().__init__(host, port, timeout=timeout)

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection with IP pinning and normal hostname certificate validation."""

    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        self._address = address
        self._ssl_context = ssl.create_default_context()
        super().__init__(host, port, timeout=timeout, context=self._ssl_context)

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._ssl_context.wrap_socket(self.sock, server_hostname=self.host)


@dataclass(frozen=True)
class _HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class _StreamableHttpClient:
    """Bounded JSON-RPC client for the MCP Streamable HTTP transport."""

    def __init__(self, spec: LiveServerSpec, deadline: float) -> None:
        if not isinstance(spec.url, str) or not spec.url.strip():
            raise LiveAuditError("no valid URL provided for Streamable HTTP introspection")
        if spec.command:
            raise LiveAuditError("provide either an MCP command or an HTTP URL, not both")
        self._endpoint = _http_endpoint(spec.url)
        self._deadline = deadline
        self._allow_http_loopback = spec.allow_http_loopback
        self._allow_private_network = spec.allow_private_network
        self._origin = (
            _normalise_origin(spec.origin) if spec.origin is not None else self._endpoint.origin
        )
        self._headers = self._validate_headers(spec.headers)
        self._sensitive_values = self._extract_sensitive_values(self._headers)
        self._protocol_version: str | None = None
        self._stateless = False
        self._last_response_version: str | None = None
        self._session_id: str | None = None
        self._next_id = 0
        self.issues: list[str] = []

    @staticmethod
    def _validate_headers(headers: dict[str, str]) -> dict[str, str]:
        validated: dict[str, str] = {}
        seen: set[str] = set()
        for name, value in headers.items():
            if not isinstance(name, str):
                raise LiveAuditError("HTTP header names must be strings")
            lowered = name.lower()
            if not _HTTP_HEADER_NAME.fullmatch(name) or lowered in _HTTP_RESERVED_HEADERS:
                raise LiveAuditError(f"invalid or transport-reserved HTTP header name: {name!r}")
            if lowered in seen:
                raise LiveAuditError(f"duplicate HTTP header name: {name!r}")
            if not isinstance(value, str) or len(value) > 8192:
                raise LiveAuditError(f"invalid value for HTTP header {name!r}")
            if any(ord(char) < 32 and char != "\\t" or ord(char) == 127 for char in value):
                raise LiveAuditError(f"invalid value for HTTP header {name!r}")
            try:
                value.encode("latin-1")
            except UnicodeEncodeError as exc:
                raise LiveAuditError(f"HTTP header {name!r} must use Latin-1 text") from exc
            seen.add(lowered)
            validated[name] = value
        return validated

    @staticmethod
    def _extract_sensitive_values(headers: dict[str, str]) -> tuple[str, ...]:
        markers = ("auth", "token", "key", "secret", "credential", "cookie", "session")
        values: set[str] = set()
        for name, value in headers.items():
            if not any(marker in name.lower() for marker in markers):
                continue
            candidate = value.strip()
            if candidate:
                values.add(candidate)
                scheme, separator, token = candidate.partition(" ")
                if separator and scheme.lower() in {"bearer", "basic", "token"} and token.strip():
                    values.add(token.strip())
        return tuple(sorted(values, key=len, reverse=True))

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            for secret in self._sensitive_values:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {self._redact(str(key)): self._redact(item) for key, item in value.items()}
        return value

    @property
    def stderr_tail(self) -> str:
        """HTTP transport has no child-process stderr."""
        return ""

    def set_protocol_version(self, version: str | None, *, stateless: bool = False) -> None:
        """Use the negotiated HTTP routing header (and stateless request metadata)."""
        self._protocol_version = version
        self._stateless = stateless
        if (
            version is not None
            and self._last_response_version is not None
            and self._last_response_version != version
        ):
            self.issues.append(
                "HTTP response `MCP-Protocol-Version` did not match the negotiated protocol version."
            )

    def _check_response_headers(self, headers: dict[str, str]) -> None:
        response_version = headers.get("mcp-protocol-version")
        if response_version is not None:
            response_version = response_version.strip()
            self._last_response_version = response_version
            if self._protocol_version is not None and response_version != self._protocol_version:
                self.issues.append(
                    "HTTP response `MCP-Protocol-Version` did not match the negotiated protocol version."
                )

        allowed_origin = headers.get("access-control-allow-origin")
        if allowed_origin is None:
            self.issues.append(
                "HTTP response omitted `Access-Control-Allow-Origin` for the requested Origin."
            )
        elif allowed_origin.strip() != self._origin:
            self.issues.append(
                "HTTP response `Access-Control-Allow-Origin` was missing, wildcarded, or foreign to the requested Origin."
            )

        cache_control = headers.get("cache-control", "")
        directives = {item.split("=", 1)[0].strip().lower() for item in cache_control.split(",")}
        explicitly_private = "private" in directives or "no-store" in directives
        cacheable = "public" in directives or "s-maxage" in directives or "max-age" in directives
        if cacheable and not explicitly_private:
            self.issues.append(
                "HTTP response marked the MCP catalog publicly cacheable; sensitive audit data must not use shared caches."
            )

        session_id = headers.get("mcp-session-id")
        if session_id is not None:
            candidate = session_id.strip()
            if (
                not candidate
                or len(candidate) > 1024
                or any(ord(char) < 33 or ord(char) > 126 for char in candidate)
            ):
                raise LiveAuditError("server returned an invalid MCP session identifier")
            self._session_id = candidate

    @staticmethod
    def _sse_has_response(body: bytes, request_id: int) -> bool:
        """Detect a complete SSE event for this request without waiting for stream EOF."""
        text = body.decode("utf-8", errors="ignore")
        for event in re.split(r"\r?\n\r?\n", text):
            data = "\n".join(
                line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:")
            )
            if not data:
                continue
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                return True
        return False

    def _read_body(
        self,
        response: http.client.HTTPResponse,
        connection: http.client.HTTPConnection,
        active_socket: socket.socket | None,
        *,
        request_id: int | None,
    ) -> bytes:
        declared = response.getheader("Content-Length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except ValueError as exc:
                raise LiveAuditError("HTTP response has an invalid Content-Length") from exc
            if declared_size < 0 or declared_size > _MAX_RESPONSE_BYTES:
                raise LiveAuditError(
                    f"HTTP response exceeded the {_MAX_RESPONSE_BYTES}-byte response limit"
                )

        content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        is_sse_response = content_type == "text/event-stream"
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise LiveAuditError("timed out while reading the HTTP response body")
            target_socket = active_socket if active_socket is not None else connection.sock
            if target_socket is not None:
                target_socket.settimeout(remaining)
            try:
                chunk = response.read1(min(65536, _MAX_RESPONSE_BYTES + 1 - total))
            except (OSError, http.client.HTTPException) as exc:
                if time.monotonic() >= self._deadline or isinstance(exc, TimeoutError):
                    raise LiveAuditError("timed out while reading the HTTP response body") from exc
                raise LiveAuditError(
                    f"failed to read HTTP response ({type(exc).__name__})"
                ) from exc
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_RESPONSE_BYTES:
                raise LiveAuditError(
                    f"HTTP response exceeded the {_MAX_RESPONSE_BYTES}-byte response limit"
                )
            chunks.append(chunk)
            if is_sse_response and request_id is not None:
                current_body = b"".join(chunks)
                if self._sse_has_response(current_body, request_id):
                    return current_body
        return b"".join(chunks)

    def _perform_post(
        self,
        endpoint: _HttpEndpoint,
        body: bytes,
        headers: dict[str, str],
        *,
        request_id: int | None,
    ) -> _HttpResponse:
        addresses = _resolve_endpoint_addresses(
            endpoint,
            allow_http_loopback=self._allow_http_loopback,
            allow_private_network=self._allow_private_network,
            deadline=self._deadline,
        )
        last_connect_error: BaseException | None = None
        for address in addresses:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise LiveAuditError("HTTP audit timed out")
            if endpoint.scheme == "https":
                connection: http.client.HTTPConnection = _PinnedHTTPSConnection(
                    endpoint.host, endpoint.port, address, remaining
                )
            else:
                connection = _PinnedHTTPConnection(endpoint.host, endpoint.port, address, remaining)
            socket_ref: list[socket.socket | None] = [None]

            def abort_connection(
                current_connection: http.client.HTTPConnection = connection,
                current_socket_ref: list[socket.socket | None] = socket_ref,
            ) -> None:
                current_connection.close()
                active_socket = current_socket_ref[0]
                if active_socket is not None:
                    with suppress(OSError):
                        active_socket.shutdown(socket.SHUT_RDWR)
                    with suppress(OSError):
                        active_socket.close()

            timer = threading.Timer(remaining, abort_connection)
            timer.daemon = True
            timer.start()
            try:
                try:
                    connection.connect()
                    socket_ref[0] = connection.sock
                except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                    last_connect_error = exc
                    continue
                connection.request("POST", endpoint.target, body=body, headers=headers)
                response = connection.getresponse()
                response_headers: dict[str, str] = {}
                for name, value in response.getheaders():
                    lowered = name.lower()
                    if lowered in response_headers:
                        response_headers[lowered] += ", " + value
                    else:
                        response_headers[lowered] = value
                self._check_response_headers(response_headers)
                if response.status in {301, 302, 303, 307, 308}:
                    response_body = b""
                elif 200 <= response.status < 300:
                    content_encoding = response_headers.get("content-encoding", "identity").lower()
                    if content_encoding not in {"", "identity"}:
                        raise LiveAuditError(
                            "compressed HTTP responses are refused to keep body limits reliable"
                        )
                    response_body = self._read_body(
                        response, connection, socket_ref[0], request_id=request_id
                    )
                else:
                    response_body = b""
                return _HttpResponse(response.status, response_headers, response_body)
            except LiveAuditError:
                raise
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                if time.monotonic() >= self._deadline or isinstance(exc, TimeoutError):
                    raise LiveAuditError("HTTP audit timed out") from exc
                raise LiveAuditError(f"HTTP exchange failed ({type(exc).__name__})") from exc
            finally:
                timer.cancel()
                connection.close()
                if socket_ref[0] is not None:
                    with suppress(OSError):
                        socket_ref[0].close()
        if time.monotonic() >= self._deadline:
            raise LiveAuditError("HTTP audit timed out")
        raise LiveAuditError(
            f"could not connect to the remote MCP server ({type(last_connect_error).__name__ if last_connect_error else 'connection failed'})"
        )

    def _post(self, payload: dict[str, Any]) -> _HttpResponse:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        raw_request_id = payload.get("id")
        request_id = raw_request_id if isinstance(raw_request_id, int) else None
        headers = {
            "Accept": "application/json, text/event-stream",
            "Cache-Control": "no-store",
            "Connection": "close",
            "Content-Type": "application/json",
            "Origin": self._origin,
            "Pragma": "no-cache",
            "User-Agent": f"vexar-mcp/{__version__}",
            **self._headers,
        }
        if self._protocol_version is not None:
            headers["MCP-Protocol-Version"] = self._protocol_version
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id

        url = self._endpoint.url
        for redirects in range(_MAX_HTTP_REDIRECTS + 1):
            endpoint = _http_endpoint(url)
            if endpoint.origin != self._endpoint.origin:
                raise LiveAuditError(
                    "cross-origin redirects are refused to protect credentials and Origin"
                )
            response = self._perform_post(endpoint, body, headers, request_id=request_id)
            if response.status not in {301, 302, 303, 307, 308}:
                return response
            if response.status not in {307, 308}:
                raise LiveAuditError(
                    f"unsafe HTTP redirect status {response.status}; only method-preserving 307/308 redirects are supported"
                )
            location = response.headers.get("location")
            if not location:
                raise LiveAuditError("HTTP redirect response omitted Location")
            if redirects >= _MAX_HTTP_REDIRECTS:
                raise LiveAuditError(f"HTTP redirect limit exceeded ({_MAX_HTTP_REDIRECTS})")
            redirected = urljoin(url, location)
            redirected_endpoint = _http_endpoint(redirected)
            if redirected_endpoint.origin != self._endpoint.origin:
                raise LiveAuditError(
                    "cross-origin redirects are refused to protect credentials and Origin"
                )
            url = redirected
        raise LiveAuditError("HTTP redirect limit exceeded")

    @staticmethod
    def _error_from_response(response: _HttpResponse, method: str) -> LiveAuditError:
        if response.status == 401:
            return LiveAuditError(
                f"HTTP 401 while calling '{method}'; provide authentication with --header or --header-from-env"
            )
        if response.status == 403:
            return LiveAuditError(
                f"HTTP 403 while calling '{method}'; check authentication and the configured Origin"
            )
        return LiveAuditError(f"remote MCP server returned HTTP {response.status} for '{method}'")

    def _parse_message(self, raw: bytes, request_id: int, method: str) -> dict[str, Any] | None:
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveAuditError(f"server returned invalid JSON for '{method}'") from exc
        if not isinstance(message, dict):
            raise LiveAuditError(f"server returned a non-object JSON-RPC message for '{method}'")
        message = self._redact(message)
        if message.get("id") != request_id:
            return None
        if message.get("jsonrpc") != "2.0":
            raise LiveAuditError(f"server returned a non-JSON-RPC 2.0 response for '{method}'")
        if "error" in message:
            # Do not reflect a remote error string: an HTTP server could have copied an
            # Authorization/Cookie header into it, and diagnostics are often pasted into CI.
            raise LiveAuditError(f"server rejected '{method}' with a JSON-RPC error")
        result = message.get("result")
        if not isinstance(result, dict):
            raise LiveAuditError(f"server returned a non-object result for '{method}'")
        return result

    def _decode_sse(self, body: bytes, request_id: int, method: str) -> dict[str, Any]:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LiveAuditError(f"server returned non-UTF-8 SSE data for '{method}'") from exc
        data_lines: list[str] = []

        def dispatch() -> dict[str, Any] | None:
            if not data_lines:
                return None
            raw = "\n".join(data_lines).encode("utf-8")
            data_lines.clear()
            return self._parse_message(raw, request_id, method)

        for line in text.splitlines():
            if not line:
                result = dispatch()
                if result is not None:
                    return result
            elif line.startswith(":"):
                continue
            elif line.startswith("data:"):
                value = line[5:]
                data_lines.append(value[1:] if value.startswith(" ") else value)
        result = dispatch()
        if result is not None:
            return result
        raise LiveAuditError(f"SSE stream contained no response for '{method}'")

    def _response_result(
        self, response: _HttpResponse, request_id: int, method: str
    ) -> dict[str, Any]:
        if not 200 <= response.status < 300:
            raise self._error_from_response(response, method)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            result = self._parse_message(response.body, request_id, method)
            if result is None:
                raise LiveAuditError(f"server returned a mismatched response id for '{method}'")
            return result
        if content_type == "text/event-stream":
            return self._decode_sse(response.body, request_id, method)
        raise LiveAuditError(
            f"server returned unsupported Content-Type for '{method}' (expected JSON or server-sent events)"
        )

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification over HTTP."""
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        merged_headers_response = self._post(payload)
        if not 200 <= merged_headers_response.status < 300:
            raise self._error_from_response(merged_headers_response, method)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a JSON-RPC request and parse a JSON or SSE response."""
        self._next_id += 1
        request_id = self._next_id
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        merged = dict(params or {})
        if self._protocol_version and self._stateless:
            meta = dict(merged.get("_meta") or {})
            meta.setdefault(VERSION_META_KEY, self._protocol_version)
            merged["_meta"] = meta
        if merged:
            payload["params"] = merged
        return self._response_result(self._post(payload), request_id, method)


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


def _introspect_with_client(
    spec: LiveServerSpec,
    client: _RpcClient,
    *,
    started: float,
) -> LiveAuditResult:
    """Negotiate either transport and run the shared read-only tools/list audit."""
    issues: list[str] = []
    negotiated = spec.protocol_version
    server_name = ""
    server_version = ""
    pages = 0
    mode = "handshake"
    try:
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
            client.set_protocol_version(chosen, stateless=True)
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
            client.set_protocol_version(negotiated or None)
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

        fallback_name = spec.command[0] if spec.command else "remote-server"
        tools = tuple(parse_tool_entries(entries, f"mcp://{server_name or fallback_name}"))
        issues.extend(client.issues)
    except LiveAuditError:
        raise

    return LiveAuditResult(
        tools=tools,
        protocol_version=negotiated,
        mode=mode,
        server_name=server_name,
        server_version=server_version,
        duration_ms=int((time.monotonic() - started) * 1000),
        pages=pages,
        stderr_tail=client.stderr_tail,
        issues=tuple(dict.fromkeys(issues)),
    )


def introspect_server(spec: LiveServerSpec) -> LiveAuditResult:
    """Audit a local stdio server or a remote Streamable HTTP MCP endpoint.

    HTTP requests use a single total deadline, bounded JSON/SSE response bodies, a small
    same-origin redirect limit, pinned DNS addresses, HTTPS by default, and no-store cache
    directives. Cleartext and private-network access require explicit opt-ins.

    Raises:
        LiveAuditError: If the endpoint cannot be reached, times out, or speaks something
            other than the expected JSON-RPC/Streamable HTTP protocol.
    """
    if (
        isinstance(spec.timeout, bool)
        or not isinstance(spec.timeout, (int, float))
        or not math.isfinite(spec.timeout)
        or spec.timeout <= 0
    ):
        raise LiveAuditError("live audit timeout must be a finite number greater than zero")
    started = time.monotonic()
    deadline = started + spec.timeout

    if spec.url is not None:
        http_client = _StreamableHttpClient(spec, deadline)
        return _introspect_with_client(spec, http_client, started=started)
    if not spec.command:
        raise LiveAuditError("no command provided for live introspection")

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
            cwd=spec.cwd,
            start_new_session=os.name == "posix",
        )
    except FileNotFoundError as exc:
        raise LiveAuditError(
            f"cannot launch MCP server, executable not found: {spec.command[0]}"
        ) from exc
    except OSError as exc:
        raise LiveAuditError(f"cannot launch MCP server: {exc}") from exc

    client = _JsonRpcClient(process, deadline)
    try:
        return _introspect_with_client(spec, client, started=started)
    finally:
        # Order matters: terminate the child first so the reader threads see EOF, then
        # close pipes. Closing a pipe while a reader is blocked can otherwise hang.
        _terminate(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            with suppress(OSError, ValueError):
                if stream is not None:
                    stream.close()


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
                "Use a supported MCP revision, advertise the `tools` capability, return "
                "`inputSchema` objects, and for HTTP endpoints scope CORS to the requested "
                "Origin and disable shared caching of the catalog."
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
