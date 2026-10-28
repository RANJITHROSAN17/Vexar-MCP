#!/usr/bin/env python3
"""Mock MCP server implementing the stateless ``2026-07-28`` protocol revision.

This is the counterpart to ``mock_mcp_server.py`` (which speaks the older handshake-based
revisions). It exists to prove one specific claim: *can Vexar-MCP audit a spec-current
server?* Before 0.2.2 the answer was no — the client only knew how to send ``initialize``,
which this revision removed, so every modern server simply looked broken.

Behaviour, per the 2026-07-28 revision:

* there is **no** ``initialize`` handshake; a client that sends one gets an error
  (``MethodNotFoundError``) — this fixture answers with ``UnsupportedProtocolVersionError``
  for the handshake call, which is what a strict server does when a client insists on it;
* ``server/discover`` is the mandatory RPC that returns supported versions, capabilities and
  server identity in one response;
* every request carries its protocol version in
  ``params._meta["io.modelcontextprotocol/protocolVersion"]``;
* an unsupported requested version is rejected per-request with
  ``UnsupportedProtocolVersionError`` listing what the server does support.

Flags exist to make each failure mode testable.
"""

from __future__ import annotations

import json
import sys

VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
SUPPORTED = ["2026-07-28", "2025-11-25"]

TOOLS_PAGE = [
    {
        "name": "list_incidents",
        "description": "List open security incidents for a tenant.",
        "inputSchema": {
            "type": "object",
            "properties": {"tenant": {"type": "string", "maxLength": 64}},
            "required": ["tenant"],
        },
    },
    {
        "name": "run",
        "description": "run",
        "inputSchema": {"type": "object", "properties": {"cmd": {}}, "required": ["cmd"]},
    },
]


def _error(request_id: object, code: int, message: str, data: dict | None = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": err}


def main(argv: list[str]) -> int:
    """Serve JSON-RPC on stdio until stdin closes."""
    no_discover = "--no-discover" in argv
    only_newest = "--only-2026-07-28" in argv
    only_2025 = "--only-2025-11-25" in argv
    supported = ["2026-07-28"] if only_newest else ["2025-11-25"] if only_2025 else SUPPORTED

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps(_error(None, -32700, "Parse error")) + "\n")
            sys.stdout.flush()
            continue
        if not isinstance(request, dict):
            continue

        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}
        meta = params.get("_meta") if isinstance(params, dict) else None
        requested_version = meta.get(VERSION_KEY) if isinstance(meta, dict) else None

        # --- the handshake that this revision removed -------------------------
        if method == "initialize":
            sys.stdout.write(
                json.dumps(
                    _error(
                        request_id,
                        -32601,
                        "Method not found: 'initialize' was removed in 2026-07-28; "
                        "send the protocol version in params._meta and call server/discover.",
                    )
                )
                + "\n"
            )
            sys.stdout.flush()
            continue

        # --- mandatory discovery RPC -----------------------------------------
        if method == "server/discover":
            if no_discover:
                sys.stdout.write(json.dumps(_error(request_id, -32601, "Method not found")) + "\n")
                sys.stdout.flush()
                continue
            sys.stdout.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "protocolVersions": supported,
                            "capabilities": {"tools": {"listChanged": False}},
                            "serverInfo": {"name": "stateless-fixture", "version": "1.0.0"},
                        },
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
            continue

        # --- every other request must declare a supported version -------------
        if requested_version is None:
            sys.stdout.write(
                json.dumps(
                    _error(
                        request_id,
                        -32602,
                        f"Missing required params._meta['{VERSION_KEY}']",
                    )
                )
                + "\n"
            )
            sys.stdout.flush()
            continue
        if requested_version not in supported:
            sys.stdout.write(
                json.dumps(
                    _error(
                        request_id,
                        -32000,
                        "UnsupportedProtocolVersionError",
                        {"supported": supported, "requested": requested_version},
                    )
                )
                + "\n"
            )
            sys.stdout.flush()
            continue

        if method == "tools/list":
            sys.stdout.write(
                json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS_PAGE}})
                + "\n"
            )
            sys.stdout.flush()
            continue

        if method is not None and request_id is not None:
            sys.stdout.write(json.dumps(_error(request_id, -32601, "Method not found")) + "\n")
            sys.stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
