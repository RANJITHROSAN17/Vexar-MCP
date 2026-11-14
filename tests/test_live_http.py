"""Streamable HTTP live-audit tests, including the transport safety boundaries."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

import vexar_mcp.live as live
from vexar_mcp.cli import main
from vexar_mcp.live import (
    VERSION_META_KEY,
    LiveAuditError,
    LiveServerSpec,
    introspect_server,
    protocol_diagnostics,
)

_TOOL = {
    "name": "lookup_record",
    "description": "Look up a record by its stable identifier.",
    "inputSchema": {"type": "object", "properties": {"record_id": {"type": "string"}}},
}
Responder = Callable[[BaseHTTPRequestHandler, dict[str, Any]], tuple[int, dict[str, str], bytes]]


def _rpc(request: dict[str, Any], result: dict[str, Any]) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()


def _base_headers(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    headers = {
        "Access-Control-Allow-Origin": handler.headers.get("Origin", ""),
        "Cache-Control": "private, no-store",
    }
    protocol_version = handler.headers.get("MCP-Protocol-Version")
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    return headers


def _handshake_responder(
    handler: BaseHTTPRequestHandler, request: dict[str, Any]
) -> tuple[int, dict[str, str], bytes]:
    headers = _base_headers(handler)
    method = request["method"]
    if method == "notifications/initialized":
        return 202, headers, b""
    if method == "initialize":
        headers["Content-Type"] = "application/json"
        return (
            200,
            headers,
            _rpc(
                request,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "http-fixture", "version": "1.0"},
                },
            ),
        )
    if method == "tools/list":
        headers["Content-Type"] = "application/json"
        return 200, headers, _rpc(request, {"tools": [_TOOL]})
    raise AssertionError(f"unexpected method: {method}")


@contextmanager
def _serve(responder: Responder) -> Iterator[tuple[str, ThreadingHTTPServer]]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(content_length))
            records = self.server.requests  # type: ignore[attr-defined]
            records.append(
                (request, {key.lower(): value for key, value in self.headers.items()}, self.path)
            )
            status, response_headers, body = responder(self, request)
            self.send_response(status)
            for name, value in response_headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.requests = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _spec(url: str, **kwargs: Any) -> LiveServerSpec:
    timeout = kwargs.pop("timeout", 5)
    return LiveServerSpec(url=url, allow_http_loopback=True, timeout=timeout, **kwargs)


def test_streamable_http_handshake_sends_negotiated_version_and_origin() -> None:
    with _serve(_handshake_responder) as (url, server):
        result = introspect_server(_spec(url))

    assert result.mode == "handshake"
    assert result.protocol_version == "2025-06-18"
    assert result.server_name == "http-fixture"
    assert [tool.name for tool in result.tools] == ["lookup_record"]
    assert result.clean
    requests = server.requests  # type: ignore[attr-defined]
    assert [request[0]["method"] for request in requests] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]
    for payload, headers, _path in requests:
        assert headers["origin"] == url.rsplit("/", 1)[0]
        assert headers["cache-control"] == "no-store"
        assert headers["pragma"] == "no-cache"
        if payload["method"] == "initialize":
            assert "mcp-protocol-version" not in headers
        else:
            assert headers["mcp-protocol-version"] == "2025-06-18"


def test_http_session_id_is_reused_for_follow_up_requests() -> None:
    def responder(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        if request["method"] != "initialize":
            assert handler.headers.get("Mcp-Session-Id") == "fixture-session"
        status, headers, body = _handshake_responder(handler, request)
        if request["method"] == "initialize":
            headers["Mcp-Session-Id"] = "fixture-session"
        return status, headers, body

    with _serve(responder) as (url, _server):
        result = introspect_server(_spec(url))

    assert result.server_name == "http-fixture"


def test_stateless_http_uses_both_protocol_header_and_request_metadata() -> None:
    def responder(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        headers = _base_headers(handler)
        method = request["method"]
        if method == "initialize":
            headers["Content-Type"] = "application/json"
            return (
                200,
                headers,
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "error": {"code": -32601, "message": "initialize was removed"},
                    }
                ).encode(),
            )
        if method == "server/discover":
            headers["Content-Type"] = "application/json"
            return (
                200,
                headers,
                _rpc(
                    request,
                    {
                        "protocolVersions": ["2025-11-25", "2026-07-28"],
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "stateless-http", "version": "2"},
                    },
                ),
            )
        assert method == "tools/list"
        assert handler.headers.get("MCP-Protocol-Version") == "2026-07-28"
        assert request["params"]["_meta"][VERSION_META_KEY] == "2026-07-28"
        headers["Content-Type"] = "application/json"
        return 200, headers, _rpc(request, {"tools": [_TOOL]})

    with _serve(responder) as (url, _server):
        result = introspect_server(_spec(url))

    assert result.mode == "stateless"
    assert result.protocol_version == "2026-07-28"
    assert result.server_name == "stateless-http"


def test_http_server_sent_events_are_parsed_as_json_rpc() -> None:
    def responder(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        if request["method"] != "tools/list":
            return _handshake_responder(handler, request)
        headers = _base_headers(handler)
        headers["Content-Type"] = "text/event-stream"
        message = {"jsonrpc": "2.0", "id": request["id"], "result": {"tools": [_TOOL]}}
        return 200, headers, f"event: message\ndata: {json.dumps(message)}\n\n".encode()

    with _serve(responder) as (url, _server):
        result = introspect_server(_spec(url))

    assert [tool.name for tool in result.tools] == ["lookup_record"]
    assert result.clean


def test_sse_response_finishes_when_its_matching_event_arrives() -> None:
    class StreamingHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if request["method"] == "tools/list":
                headers = _base_headers(self)
                headers["Content-Type"] = "text/event-stream"
                self.send_response(200)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                message = {"jsonrpc": "2.0", "id": request["id"], "result": {"tools": [_TOOL]}}
                event = f"data: {json.dumps(message)}\n\n".encode()
                self.wfile.write(f"{len(event):X}\r\n".encode() + event + b"\r\n")
                self.wfile.flush()
                time.sleep(0.4)  # A long-lived server stream should not delay the client.
                return
            status, headers, body = _handshake_responder(self, request)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), StreamingHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/mcp"
        result = introspect_server(_spec(url, timeout=0.15))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.tools[0].name == "lookup_record"
    assert result.duration_ms < 150


def test_origin_version_and_public_cache_violations_become_mcp012() -> None:
    def responder(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        if request["method"] != "tools/list":
            return _handshake_responder(handler, request)
        headers = _base_headers(handler)
        headers["Access-Control-Allow-Origin"] = "https://foreign.example"
        headers["Cache-Control"] = "public, s-maxage=600"
        headers["MCP-Protocol-Version"] = "2024-11-05"
        headers["Content-Type"] = "application/json"
        return 200, headers, _rpc(request, {"tools": [_TOOL]})

    with _serve(responder) as (url, _server):
        result = introspect_server(_spec(url))

    assert any("MCP-Protocol-Version" in issue for issue in result.issues)
    assert any(
        "Access-Control-Allow-Origin" in issue and "foreign" in issue for issue in result.issues
    )
    assert any("publicly cacheable" in issue for issue in result.issues)
    assert {diagnostic.rule_id for diagnostic in protocol_diagnostics(result)} == {"MCP012"}


def test_missing_access_control_origin_is_a_protocol_finding() -> None:
    def responder(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        status, headers, body = _handshake_responder(handler, request)
        headers.pop("Access-Control-Allow-Origin", None)
        return status, headers, body

    with _serve(responder) as (url, _server):
        result = introspect_server(_spec(url))

    assert any("omitted `Access-Control-Allow-Origin`" in issue for issue in result.issues)


def test_http_auth_header_is_sent_but_never_reflected_in_errors() -> None:
    secret = "Bearer fixture-secret"

    def responder(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        if handler.headers.get("Authorization") != secret:
            return 401, _base_headers(handler), b""
        if request["method"] == "tools/list":
            headers = _base_headers(handler)
            headers["Content-Type"] = "application/json"
            tool = _TOOL | {"description": f"Server accidentally echoed {secret}."}
            return 200, headers, _rpc(request, {"tools": [tool]})
        return _handshake_responder(handler, request)

    with _serve(responder) as (url, server):
        with pytest.raises(LiveAuditError) as error:
            introspect_server(_spec(url, headers={"Authorization": "Bearer wrong-secret"}))
        assert "wrong-secret" not in str(error.value)
        result = introspect_server(_spec(url, headers={"Authorization": secret}))

    assert result.server_name == "http-fixture"
    assert "fixture-secret" not in result.tools[0].description
    assert "[REDACTED]" in result.tools[0].description
    requests = server.requests  # type: ignore[attr-defined]
    assert any(headers["authorization"] == secret for _request, headers, _path in requests)
    assert any(
        headers["authorization"] == "Bearer wrong-secret" for _request, headers, _path in requests
    )


def test_remote_json_rpc_errors_do_not_echo_auth_values() -> None:
    secret = "Bearer error-secret"

    def echo_error(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        headers = _base_headers(handler)
        headers["Content-Type"] = "application/json"
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32000, "message": f"invalid credential {secret}"},
            }
        ).encode()
        return 200, headers, body

    with _serve(echo_error) as (url, _server), pytest.raises(LiveAuditError) as error:
        introspect_server(_spec(url, headers={"Authorization": secret}))

    assert "error-secret" not in str(error.value)


def test_cli_audits_http_url_and_accepts_auth_headers(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "Bearer cli-secret"
    monkeypatch.setenv("VEXAR_HTTP_TOKEN", secret)

    def responder(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        if handler.headers.get("Authorization") != secret:
            return 401, _base_headers(handler), b""
        return _handshake_responder(handler, request)

    with _serve(responder) as (url, _server):
        code = main(
            [
                "audit-server",
                "--url",
                url,
                "--allow-http-loopback",
                "--header-from-env",
                "Authorization:VEXAR_HTTP_TOKEN",
                "--format",
                "json",
                "--fail-on",
                "never",
            ]
        )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["tools_scanned"] == 1
    assert payload["live"] == {"protocol_version": "2025-06-18", "negotiation": "handshake"}
    assert secret not in json.dumps(payload)


def test_https_only_and_private_network_require_explicit_opt_in() -> None:
    with pytest.raises(LiveAuditError, match="cleartext HTTP is refused"):
        introspect_server(LiveServerSpec(url="http://127.0.0.1:12345/mcp", timeout=1))
    with pytest.raises(LiveAuditError, match="private or non-public"):
        introspect_server(LiveServerSpec(url="https://127.0.0.1:12345/mcp", timeout=1))


def test_redirects_are_same_origin_and_bounded() -> None:
    def one_redirect(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        if handler.path == "/start":
            headers = _base_headers(handler)
            headers["Location"] = "/mcp"
            return 307, headers, b""
        return _handshake_responder(handler, request)

    with _serve(one_redirect) as (url, _server):
        result = introspect_server(_spec(url.rsplit("/", 1)[0] + "/start"))
    assert result.server_name == "http-fixture"

    def redirecting(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        headers = _base_headers(handler)
        headers["Location"] = "/mcp"
        return 307, headers, b""

    with (
        _serve(redirecting) as (url, _server),
        pytest.raises(LiveAuditError, match="redirect limit"),
    ):
        introspect_server(_spec(url))

    def foreign_redirect(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        headers = _base_headers(handler)
        headers["Location"] = "https://foreign.example/mcp"
        return 307, headers, b""

    with (
        _serve(foreign_redirect) as (url, _server),
        pytest.raises(LiveAuditError, match="cross-origin redirects"),
    ):
        introspect_server(_spec(url))


def test_http_response_body_size_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    def oversized(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        if request["method"] != "tools/list":
            return _handshake_responder(handler, request)
        headers = _base_headers(handler)
        headers["Content-Type"] = "application/json"
        return 200, headers, _rpc(request, {"tools": [_TOOL | {"description": "x" * 1200}]})

    monkeypatch.setattr(live, "_MAX_RESPONSE_BYTES", 512)
    with _serve(oversized) as (url, _server), pytest.raises(LiveAuditError, match="response limit"):
        introspect_server(_spec(url))


def test_total_http_deadline_is_enforced() -> None:
    def slow(
        handler: BaseHTTPRequestHandler, request: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        if request["method"] == "initialize":
            time.sleep(0.3)
        return _handshake_responder(handler, request)

    with _serve(slow) as (url, _server), pytest.raises(LiveAuditError, match="timed out"):
        introspect_server(_spec(url, timeout=0.1))
