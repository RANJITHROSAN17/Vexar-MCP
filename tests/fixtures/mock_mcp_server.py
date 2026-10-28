#!/usr/bin/env python3
"""Minimal MCP server over stdio, used to test live introspection.

Deliberately dependency-free so it runs in any CI matrix without installing the MCP
SDK. It speaks newline-delimited JSON-RPC 2.0 and implements just enough of the spec to
exercise the Vexar-MCP live audit path: ``initialize``,
``notifications/initialized`` and ``tools/list`` (optionally paginated).

Flags exist so tests can reproduce real-world breakage:

    --bad-protocol   negotiate an unknown protocol revision and omit the tools capability
    --paginate        return tools across two pages with a nextCursor
    --no-schema       omit inputSchema from one tool
    --silent          never respond (used to test timeouts)
"""

from __future__ import annotations

import json
import sys
import time

TOOLS_PAGE_1 = [
    {
        "name": "run",
        "description": "",
        "inputSchema": {
            "type": "object",
            "properties": {"cmd": {"description": "shell command"}},
            "required": ["cmd"],
        },
    },
    {
        "name": "search_invoices",
        "description": "Search invoices by free-text query and return matching records.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text query"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
]

TOOLS_PAGE_2 = [
    {
        "name": "export_report",
        "description": "Export the monthly compliance report bundle as a signed archive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["pdf", "csv"],
                    "description": "Output format",
                },
                "month": {"type": "string", "description": "ISO month, e.g. 2025-07"},
            },
            "required": ["format"],
        },
    }
]


def _send(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    bad_protocol = "--bad-protocol" in argv
    paginate = "--paginate" in argv
    no_schema = "--no-schema" in argv
    silent = "--silent" in argv

    for line in sys.stdin:
        if silent:
            time.sleep(60)
            continue
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = message.get("method")
        request_id = message.get("id")

        if method == "initialize" and request_id is not None:
            result: dict[str, object] = {
                "protocolVersion": "1999-01-01" if bad_protocol else "2025-06-18",
                "serverInfo": {"name": "mock-mcp-server", "version": "0.0.1"},
            }
            if not bad_protocol:
                result["capabilities"] = {"tools": {}}
            _send({"jsonrpc": "2.0", "id": request_id, "result": result})

        elif method == "notifications/initialized":
            continue

        elif method == "tools/list" and request_id is not None:
            params = message.get("params") or {}
            cursor = params.get("cursor")
            page_one = list(TOOLS_PAGE_1)
            if no_schema:
                page_one[0] = {"name": "run", "description": ""}
            if paginate and cursor is None:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"tools": page_one, "nextCursor": "page-2"},
                    }
                )
            elif paginate and cursor == "page-2":
                _send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS_PAGE_2}})
            else:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"tools": page_one + TOOLS_PAGE_2},
                    }
                )

        elif request_id is not None:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
