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

Supported revisions: `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`, `2026-07-28`.
An unknown negotiated revision is reported, not silently accepted.

### Two negotiation modes, and why it matters

| Mode | When | How the revision is agreed |
| :--- | :--- | :--- |
| `handshake` | revisions `2025-11-25` and earlier | `initialize` → `notifications/initialized`, version in the result |
| `stateless` | `2026-07-28` and later | `server/discover` → newest mutually supported revision, carried per request in `params._meta["io.modelcontextprotocol/protocolVersion"]` |

The client tries the handshake first and falls back to discovery. This is not a compatibility
nicety: `2026-07-28` **removed** the handshake, so a handshake-only client cannot audit any
server built on the current revision. Before 0.2.2 that is exactly what happened, and the
failure surfaced as `server rejected 'initialize'` — which reads like the server's fault.

Every audit report states which mode was used (`Live protocol: <revision> | negotiation:
<mode>` in the terminal, `"live": {"protocol_version": ..., "negotiation": ...}` in JSON),
because the two modes prove different things about the server.

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

## Auditing a whole fleet

Teams do not run one MCP server. `audit-registry` reads the file that already lists them —
a Claude Desktop or Cursor configuration, or a checked-in registry — audits each server
over the same stdio client, and emits one combined report.

```bash
vexar-mcp audit-registry .vexar-servers.json
vexar-mcp audit-registry ~/.config/Claude/claude_desktop_config.json --format json
vexar-mcp audit-registry fleet.json --dry-run          # validate + show the plan, run nothing
```

### The registry schema

Two shapes are accepted, and a file containing both is refused rather than guessed at.

**Native registry** — `servers` is a list, each entry named:

```json
{
  "version": 1,
  "servers": [
    {"name": "invoices", "command": ["python", "servers/invoices.py"], "timeout": 45},
    {"name": "search",   "command": "npx", "args": ["-y", "@acme/search"], "env": {"API_KEY": "..."}}
  ]
}
```

**Client configuration** — `mcpServers` is an object keyed by server name, read as-is:

```json
{
  "mcpServers": {
    "invoices": {"command": "python", "args": ["servers/invoices.py"], "env": {}, "cwd": "/srv/invoices"}
  }
}
```

| Field | Required | Meaning |
| :--- | :--- | :--- |
| `name` | native only | Unique label used in the report. Keys of `mcpServers` serve this role there. |
| `command` | yes | A **list** (used literally) or a **string** (split on whitespace, never shell-expanded). |
| `args` | no | List of strings. Only valid when `command` is a string. |
| `env` | no | Object of string values, layered over the parent environment. |
| `cwd` | no | Working directory for the child. |
| `timeout` | no | Per-server seconds; falls back to `--timeout`. |
| `version` | no | Registry revision. Currently only `1`. |

### Validation is fail-closed

A registry is a list of commands to execute, which makes it a code path wearing a config
file's clothes. Every ambiguity is an error (exit `2`) that names the offending field:

| Situation | Result |
| :--- | :--- |
| File missing, or not JSON | exit `2`, with the line and column for a syntax error |
| Neither `servers` nor `mcpServers` present | exit `2`, listing the keys it did find |
| Both present | exit `2` — refusing to guess which one to audit |
| `servers` empty, or `mcpServers` empty | exit `2` — an empty registry is **not** a clean fleet |
| Duplicate names, or names differing only by case | exit `2` — report rows must be distinguishable |
| `command` list plus `args`, or an empty command | exit `2` |
| A non-string argv element or `env` value | exit `2`, quoting the value to fix |
| `timeout` of zero, negative, boolean, or non-numeric | exit `2` |
| Unknown `version` | exit `2` naming the versions this build reads |
| Remote entry (`url`, or `type` of `http`/`sse`) | **skipped** — well-formed, out of reach of a stdio client |

That last row is the interesting one: a remote server is not an error, because client
configs legitimately mix transports. It is also not silently dropped, because a report that
lists only what it managed to reach looks more complete than it is. It appears in the table
as `skipped`, and the fleet is marked incomplete.

### Isolation, and what a failure costs

Each server is audited in its own process, sequentially, and every failure mode is caught
and recorded rather than raised:

| Situation | Recorded as | Effect on the run |
| :--- | :--- | :--- |
| Executable missing | `failed`, with the executable named | run continues; exit `3` |
| Timeout | `failed`, child killed as usual | run continues; exit `3` |
| Server speaks non-JSON-RPC | `failed`, with the reason | run continues; exit `3` |
| Server speaks an old/broken revision | `audited`, with `MCP012` findings | exit per gates |
| Remote/SSE transport | `skipped`, with the reason | exit `3` |

Exit `3` means "we could not audit part of your fleet", which is deliberately not the same
as `1` ("we audited everything and something is wrong"). `--allow-incomplete` downgrades it
to the ordinary gate result when the missing servers are known and accepted.

### The fleet score is the worst server

This is the one number the report leads with, and it is the **minimum** across audited
servers, never the average:

```
Fleet score: 60/100 (D)  |  risk: 72  |  tools: 5
Coverage: 2 of 2 server(s) audited
Worst server: 'findings' - the worst audited server, not the average
  mean across audited servers: 80/100 (context only)
```

Four clean servers and one vulnerable one average to a comfortable B, and the B is the only
number anyone reads. A minimum cannot be improved by adding healthy servers, which is what
makes it worth printing. For the same reason `risk_index` is the fleet's **highest**, not a
sum: running the same server twice is not twice the risk.

`--min-score` gates against that fleet score, so a policy of "no server below 70" fails when
any one server is below 70. The mean is reported next to it, labelled as context.

A server that produced no grade (`NO_MATCHES`, or nothing to score) cannot set the floor and
is excluded from the minimum; if no server was graded at all, the fleet score is `null` and
the run does not pretend otherwise.

### Report formats

`--format terminal|json|html`. The JSON document is `vexar-fleet/v1` with `fleet`,
`coverage`, `status`, and a `servers` array; each audited server embeds the complete
`vexar-mcp-report/v1` payload it would have produced alone, so nothing is lost by auditing
in a fleet. The HTML report is a single self-contained file, like the single-server one.

Three keys carry the honesty of the document and should be read together:

```json
{
  "status": "partial",
  "complete": false,
  "coverage": {"servers": 5, "audited": 4, "failed": 1, "skipped": 0},
  "fleet": {"score": 68, "score_semantics": "minimum across audited servers", "mean_score": 84}
}
```

`score: 68` is not "the fleet is at 68". It is "the worst server we managed to audit is at
68, and we did not manage to audit all of them".

## Library use

```python
from mcp_doctor.engine import scan_server
from mcp_doctor.models import ScanConfig

report = scan_server(["python", "my_server.py"], ScanConfig(policy_name="strict-prod"), timeout=60)
print(report.summary.score, report.summary.risk_index)
```
