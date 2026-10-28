# Compliance matrix

Which parts of the MCP specification this project implements, and the evidence for each claim.

Vexar-MCP is an **analyzer**: it consumes MCP artifacts and, for `audit-server`, acts as an
MCP **client**. It does not implement an MCP **server**, so requirements that bind a server
(inbound authentication, HTTP transport rules, tool execution) are marked N/A rather than
claimed as satisfied. Each row names the test or file that proves it, because a compliance
table without evidence is a marketing document.

Source: [MCP specifications](https://modelcontextprotocol.io/specification/) and the
[versioning page](https://modelcontextprotocol.io/specification/versioning), read 2026-10-28.
The revision designated **current** at that time is `2026-07-28`.

Legend — **MANDATORY**: normative MUST/MUST NOT in the applicable specification.
**RECOMMENDED**: SHOULD. **PRODUCT**: a deliberate local decision. **N/A**: does not apply to
this architecture, with the reason given.

---

## Protocol revisions

| Revision | Negotiation mode | Fixture that proves it | Status |
| :--- | :--- | :--- | :--- |
| `2026-07-28` | `stateless` — version per request in `params._meta[io.modelcontextprotocol/protocolVersion]`, agreed via `server/discover` | `tests/fixtures/stateless_mcp_server.py` | PASS |
| `2025-11-25` | `handshake` — `initialize`, then `notifications/initialized` | `stateless_mcp_server.py --only-2025-11-25`; `test_a_server_pinned_to_2025_11_25_is_negotiated_down` | PASS |
| `2025-06-18` | `handshake` | `tests/fixtures/mock_mcp_server.py` (`test_legacy_server_is_audited_by_handshake`) | PASS |
| `2025-03-26` | `handshake` | same client code path and same fixture as `2025-06-18`; **not** exercised against a fixture pinned to this revision | PASS (inferred) |
| `2024-11-05` | `handshake` | same as `2025-03-26` | PASS (inferred) |

The last two rows are honest about being inferred: the client sends the same handshake shape it
sends for `2025-06-18`, but no fixture pins those revisions. Closing that gap is part of RM-034;
until then, "PASS (inferred)" is the accurate label.

An unsupported or unknown revision is **reported** as an `MCP012` finding, never silently
accepted.

---

## Client requirements (audit-server)

| # | Requirement | Class | Evidence | Status |
| :--- | :--- | :--- | :--- | :--- |
| C1 | JSON-RPC 2.0 request/response framing with matching ids | MANDATORY | `live.py::_JsonRpcClient.request` | PASS |
| C2 | Declare the version per request in `_meta` on stateless revisions | MANDATORY | `tests/test_live_stateless.py::test_requests_declare_the_version_in_meta` | PASS |
| C3 | Use `server/discover` to learn supported revisions and capabilities | MANDATORY | `live.py::_discover` | PASS |
| C4 | Support the handshake for revisions ≤ `2025-11-25` | MANDATORY if supported | `test_legacy_server_is_audited_by_handshake` | PASS |
| C5 | Fail closed on a revision mismatch, with a message naming both sides | MANDATORY | `test_discovery_failure_names_both_paths` | PASS |
| C6 | Protocol messages on stdout only; diagnostics on stderr | MANDATORY (stdio) | `_stdout_reader` skips banner lines; CLI writes diagnostics to stderr | PASS |
| C7 | Bound response size, page count and total duration | RECOMMENDED | 8 MiB, 50 pages, single deadline | PASS |
| C8 | Terminate the child on expiry; no orphaned processes | RECOMMENDED | `_terminate` (terminate → reap → close) | PASS |
| C9 | Identify the client in the handshake | RECOMMENDED | `clientInfo` in `initialize` | PASS |
| C10 | Treat a hostile server as untrusted input | PRODUCT | `sanitize.py`; `tests/test_sanitize.py` | PASS |

---

## Analyzer requirements (static scan)

| # | Requirement | Class | Evidence | Status |
| :--- | :--- | :--- | :--- | :--- |
| A1 | Never execute or import analyzed code | PRODUCT (security) | `ast.parse` only; `test_analysis_completeness.py` | PASS |
| A2 | Distinguish "clean" from "could not read" | PRODUCT (security) | `ScanStatus`, `ParseIssue`, exit `3` | PASS |
| A3 | Never echo secret literals into a report | PRODUCT (security) | `SecretLiteral` stores pattern/target only | PASS |
| A4 | Escape untrusted text for the output sink | PRODUCT (security) | `sanitize.py`, per-reporter escaping | PASS |
| A5 | Deterministic, machine-readable output with rule versions | RECOMMENDED | `vexar-mcp-report/v1`, `rule_id` + `version` fields | PASS |
| A6 | Findings carry remediation | RECOMMENDED | every rule defines `remediation` | PASS |
| A7 | Confine reads to the scan target | PRODUCT | symlinked directories are not followed; symlinked **files** are read | **PARTIAL** — RM-033 |

---

## Not applicable, and why

| Requirement | Why it does not apply |
| :--- | :--- |
| Inbound authentication, OAuth 2.1, token audience binding, RBAC/ABAC | There is no server and no listener. The only authentication is whatever the audited server does for the *user's* launch command. |
| Streamable HTTP, `MCP-Protocol-Version` routing header, `Origin` validation, CORS, TLS | The audit transport is stdio. Remote auditing is queued as RM-032, and those requirements become binding when it lands. |
| Human-in-the-loop approval for destructive actions | No tool is ever invoked. `audit-server` calls `tools/list` only. |
| Sandboxed execution of dynamic analysis | Nothing dynamic is executed. The stronger property holds by construction. |
| Rate limiting, quota, DDoS protection, readiness probes | A CLI process has no request surface. |
| Immutable audit logging | Scan reports are themselves the audit artefact; server-side audit logging is a server concern. |
| PII redaction before LLM context | No LLM is called, and no user data leaves the machine. |

These are the rows most often claimed as satisfied in projects with this architecture. Marking
them N/A is the accurate answer, and the reason each one is listed here is so a reader can
check the reasoning rather than take it on trust.

---

## Keeping this current

Two of the entries above are the kind that rot:

- the **revision table**, whenever the MCP specification publishes a new revision;
- the **A7 row**, once RM-033 changes it.

`RM-034` tracks automating this: a test that fails when a supported revision has no fixture
proving it. Until then, this file is reviewed by hand at each release.
