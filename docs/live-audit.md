# Live server auditing

Static analysis cannot see tools a server generates at runtime, gated behind feature flags,
or assembled from a database. `audit-server` launches the server the way a client would,
completes the real `initialize` handshake, calls `tools/list`, and runs the entire rule set
over the tools that are *actually* exposed.

```bash
vexar-mcp audit-server -- python my_server.py
vexar-mcp audit-server --format sarif --output live.sarif -- npx -y @acme/mcp-server
vexar-mcp audit-server --policy strict-prod --timeout 60 -- uv run python -m acme.server
```

Everything after `--` is the server command, passed as an argv list with **no shell**.

## What is validated

**Protocol**

| Check | Reported as |
| :--- | :--- |
| Server negotiates a supported protocol revision | `MCP012` warning |
| `initialize` result includes `protocolVersion` | `MCP012` warning |
| Server advertises the `tools` capability | `MCP012` warning |
| Every `tools/list` entry is a JSON object with a `name` | `MCP012` warning |
| Every tool returns an object `inputSchema` | `MCP012` warning |
| Pagination is followed and does not run away | `MCP012` warning past 50 pages |

**Tool quality** — the fetched catalog is normalized by the same code path as file scans
(`loaders.json_manifest.parse_tool_entries`), so `MCP001`–`MCP011` and `MCP013` apply
identically. Live and static results are directly comparable, and a baseline taken from one
is meaningful against the other.

Supported revisions: `2024-11-05`, `2025-03-26`, `2025-06-18`. An unknown negotiated
revision is reported, not silently accepted.

## Safety rails

Running an untrusted process is not a small thing, so the boundaries are explicit:

- **argv only, never `shell=True`.** Nothing in the command is shell-expanded.
- **Hard deadline.** One total timeout covers the handshake and every `tools/list` page;
  the child is killed (process group SIGTERM, then SIGKILL) when it expires.
- **Bounded responses.** A single response may not exceed 8 MiB, and pagination stops at 50
  pages.
- **JSON only.** Server output is parsed, never evaluated. Non-JSON banners on stdout are
  skipped so chatty servers remain auditable.
- **No credential handling.** `audit-server` passes the parent environment through so the
  server can find its own configuration, and reads nothing from its own config.
- **Vexar-MCP still does not scan by executing your code.** `scan` remains pure static
  analysis; `audit-server` is the one command that starts a process, and it does so only
  when you name the command yourself. Rule packs are the only other code-executing feature,
  and they are opt-in — see [rule-packs.md](rule-packs.md).

Recommended posture: run `audit-server` in a container or sandbox in CI, with only the
secrets that server needs. It is a *client*, so treat it like one.

## Failure behaviour

| Situation | Result |
| :--- | :--- |
| Executable missing | `LiveAuditError`, exit `2`, message names the executable |
| Server exits mid-handshake | exit `2`, includes the exit code and stderr tail |
| Server never responds | exit `2` after `--timeout`, child killed |
| Server rejects `tools/list` | exit `2`, includes the server's error message |
| Server speaks an old revision | exit `0`/`1` depending on gates, with `MCP012` findings |

Exit `2` means "we could not audit this server" — which is not the same as "the server is
clean". Treat it as a hard failure in CI.

## Debugging a live audit

```bash
# Inspect the raw protocol exchange
vexar-mcp audit-server --format json --fail-on never -- python my_server.py | jq '.diagnostics[] | select(.rule_id=="MCP012")'

# Give a slow cold start more room
vexar-mcp audit-server --timeout 120 -- docker run --rm -i my-mcp-server
```

`stderr_tail` from the server is available to library callers through
`mcp_doctor.live.introspect_server()` when a report needs more context than the diagnostic.

## Library use

```python
from mcp_doctor.engine import scan_server
from mcp_doctor.models import ScanConfig

report = scan_server(["python", "my_server.py"], ScanConfig(policy_name="strict-prod"), timeout=60)
print(report.summary.score, report.summary.risk_index)
```
