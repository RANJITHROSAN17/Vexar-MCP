# Rules

All twelve built-in diagnostics. Run `vexar-mcp rules` for this list from the CLI, or `vexar-mcp rules --format json` for machine-readable output.

| ID | Severity | Category | Slug |
| :--- | :--- | :--- | :--- |
| [`MCP001`](#mcp001--missing-tool-description) | error | contract | `missing-tool-description` |
| [`MCP002`](#mcp002--excessive-tool-tokens) | warning | tokens | `excessive-tool-tokens` |
| [`MCP003`](#mcp003--untyped-or-any-parameter) | warning | schema | `untyped-or-any-parameter` |
| [`MCP004`](#mcp004--unbounded-collection-or-limit) | info | schema | `unbounded-collection-or-limit` |
| [`MCP005`](#mcp005--dangerous-shell-execution) | error | security | `dangerous-shell-execution` |
| [`MCP006`](#mcp006--unvalidated-ssrf-sink) | warning | security | `unvalidated-ssrf-sink` |
| [`MCP007`](#mcp007--unconfined-filesystem-access) | warning | security | `unconfined-filesystem-access` |
| [`MCP008`](#mcp008--ambiguous-or-duplicate-tool-name) | error | contract | `ambiguous-or-duplicate-tool-name` |
| [`MCP009`](#mcp009--hardcoded-secret-in-tool) | error | security | `hardcoded-secret-in-tool` |
| [`MCP010`](#mcp010--missing-network-timeout) | warning | security | `missing-network-timeout` |
| [`MCP011`](#mcp011--sql-string-interpolation) | error | security | `sql-string-interpolation` |
| [`MCP013`](#mcp013--unsafe-deserialization) | error | security | `unsafe-deserialization` |

Severity affects the health score (error −15, warning −6, info −2) and the `--fail-on` gate.

Thirteen rules run on static scans. `MCP012` is the fourteenth and runs only under
`vexar-mcp audit-server`, because protocol conformance can only be observed on a live
connection — it is documented [at the end of this page](#mcp012--live-protocol-violation).

---

<a id="MCP001"></a>

## MCP001 — missing-tool-description

**error** · contract

**Detects:** a tool whose description (docstring or `description=` argument) is shorter than 12 characters.

**Why it matters:** the description is the only signal a model has when choosing between tools. Short or missing descriptions cause wrong tool selection and hallucinated arguments.

```python
@mcp.tool()
def run(cmd):  # no docstring → MCP001
    return cmd
```

**Fix:** state what the tool does, what each argument means, and what it returns.

```python
@mcp.tool()
def list_cluster_pods(namespace: str, limit: int = 50) -> str:
    """List pods in a Kubernetes namespace.

    Args:
        namespace: Namespace to inspect.
        limit: Maximum pods to return (capped at 200).
    """
```

---

<a id="MCP002"></a>

## MCP002 — excessive-tool-tokens

**warning** · tokens

**Detects:** a single tool definition whose estimated token cost exceeds the per-tool budget (`250` by default, `--max-tokens` or `max-tool-tokens` in config).

**Why it matters:** every tool definition is re-sent to the model on every turn. Verbose descriptions and examples are a permanent context tax.

**Fix:** trim prose, drop redundant examples, or raise the budget deliberately if the tool genuinely needs it. See [token estimation limits](limitations.md#token-estimates-are-approximations).

---

<a id="MCP003"></a>

## MCP003 — untyped-or-any-parameter

**warning** · schema

**Detects:** a tool parameter with no annotation, or annotated as `Any` / a bare `dict` with no constraints.

**Why it matters:** unconstrained parameters produce unvalidated input, which is the precondition for `MCP005`–`MCP007`.

```python
def fetch(payload: Any): ...  # MCP003
def fetch(payload: dict): ...  # MCP003
```

**Fix:** use concrete types, `Literal[...]` for enums, `TypedDict` / Pydantic models for structured input, and JSON Schema constraints in manifests.

---

<a id="MCP004"></a>

## MCP004 — unbounded-collection-or-limit

**info** · schema

**Detects:** a `limit` / `page_size` / `max_results` style parameter with no upper bound.

**Why it matters:** an unbounded result set floods the context window and can stall the agent loop.

**Fix:** `Field(le=200)`, `maximum: 200` in JSON Schema, or clamp with `min(limit, MAX_LIMIT)`.

---

<a id="MCP005"></a>

## MCP005 — dangerous-shell-execution

**error** · security

**Detects:** `subprocess.*(shell=True)`, `os.system`, `os.popen`, `pty.spawn`, `eval`, or `exec` reached inside a tool handler, with the parameter that flows into it when traceable.

```python
@mcp.tool()
def run(cmd: str) -> str:
    """Run a command."""
    return subprocess.run(cmd, shell=True, check=False)  # MCP005
```

**Fix:** pass argument lists with `shell=False`, validate against an allowlist, and never `eval` model-supplied strings.

---

<a id="MCP006"></a>

## MCP006 — unvalidated-ssrf-sink

**warning** · security

**Detects:** tool input flowing into an outbound HTTP call (`urllib.request.urlopen`, `requests.*`, `httpx.*`) with no visible scheme/host validation in the handler.

**Why it matters:** a model-controlled URL can reach internal services, cloud metadata endpoints (`169.254.169.254`), or `file://` handlers.

**Fix:** parse with `urlparse`, require `https`, check a host allowlist, and reject private/link-local addresses.

---

<a id="MCP007"></a>

## MCP007 — unconfined-filesystem-access

**warning** · security

**Detects:** a tool input used in `open()`, path joins, or delete operations without base-directory confinement.

**Fix:**

```python
candidate = (BASE_DIR / user_path).resolve()
if not candidate.is_relative_to(BASE_DIR):
    raise ValueError("path escapes workspace root")
```

---

<a id="MCP008"></a>

## MCP008 — ambiguous-or-duplicate-tool-name

**error** · contract

**Detects:** duplicate names, names that do not match `[a-z][a-z0-9_-]{2,63}`, and generic verbs such as `run`, `get`, `do`.

**Why it matters:** an agent picks tools by name and description; `run` next to `execute` next to `process` is a routing coin-flip.

**Fix:** use action-oriented `snake_case` — `list_cluster_pods`, not `run`.

---

<a id="MCP009"></a>

## MCP009 — hardcoded-secret-in-tool

**error** · security

**Detects:** credential-shaped string literals inside a tool handler — provider token prefixes (`ghp_`, `sk-`, `AKIA`), bearer headers, and assignments to secret-named variables.

**Why it matters:** a key committed inside a tool body is sent to the model provider as part of the tool definition and is visible to anyone with repo access.

**Privacy note:** this rule records only the *shape* of the match — pattern name, variable name, and source line. The literal value is never stored in the diagnostic, JSON report, or SARIF output, so a report cannot leak the key a second time.

**Fix:** read from `os.environ` or a secret manager at runtime, and rotate any key that was ever committed.

---

<a id="MCP010"></a>

## MCP010 — missing-network-timeout

**warning** · security

**Detects:** an HTTP call inside a tool handler with no `timeout=` argument (or `timeout=None`).

**Why it matters:** a slow upstream holds the agent turn open indefinitely.

**Fix:** pass an explicit timeout to every outbound call.

---

<a id="MCP011"></a>

## MCP011 — sql-string-interpolation

**error** · security

**Detects:** a `.execute(...)` / `.executemany(...)` call whose statement is built by interpolating a tool parameter — an f-string, `%` formatting, `+` concatenation, or `.format(...)`.

```python
cur.execute(f"SELECT * FROM users WHERE id = '{user_id}'")  # flagged
cur.execute("SELECT * FROM users WHERE id = '%s'" % user_id)  # flagged
cur.execute("SELECT * FROM {t} LIMIT 1".format(t=table))  # flagged
cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))  # clean
```

**Why it matters:** database-backed MCP servers are among the most common kinds, and model-generated arguments reach the query text directly. Interpolation lets a tool argument terminate the string and append SQL — the classic injection primitive, with the agent as the delivery mechanism.

**Heuristics, stated plainly:** a finding requires all three of — an interpolated first argument, a reference to a tool parameter, and a statement that either contains a SQL keyword or is executed on a database-shaped receiver (`cursor`, `conn`, `session`, `db`, `engine`, and similar). That last condition is what keeps `graphql_client.execute(f"query {q}")` and generic job/RPC `.execute()` calls out of this error-level rule.

**Known gap:** the statement must be interpolated *at the call*. A query assembled into a variable on an earlier line and then passed to `.execute(sql, params)` is not followed — see [Limitations](limitations.md).

**Fix:** bind values instead of formatting them, and take identifiers that cannot be bound from a fixed allowlist.

---

<a id="MCP013"></a>

## MCP013 — unsafe-deserialization

**error** · security

**Detects:** `pickle.loads`, `yaml.load` without `SafeLoader`, and comparable unsafe deserialization of untrusted data.

**Why it matters:** `pickle` payloads execute arbitrary code on load — a remote code execution primitive reachable from model input.

**Fix:** use JSON for untrusted payloads, or `yaml.safe_load`. Never unpickle untrusted data.

---

## MCP014 — excessive-server-tokens

**warning** · tokens

**Detects:** the summed token footprint of *every* tool on a server exceeds
`max-server-tokens` (default `8000`). `MCP002` polices one bloated tool; this polices the
catalog as a whole, which is what actually reaches the model.

**Why it matters:** the tool catalog is re-sent on every request. Twenty reasonable tools can
cost more context than the conversation they are meant to support, and the bill scales with
call volume. [`vexar-mcp simulate`](usage.md#vexar-mcp-simulate-paths---model-name) turns that
into money per month.

```python
# 40 tools x ~250 tokens each = ~10,000 tokens/turn -> MCP014
```

**Fix:** remove tools the model never uses, shorten descriptions, and push rarely-needed tools
behind a single dispatcher tool. The finding names the total, the budget, and the largest
contributing tools.

---

## Reading a finding

Every diagnostic carries: `rule_id`, `rule_slug`, `severity`, `category`, `message`, `source_file`, `line`, `tool_name`, and `remediation`.

A finding is a **static signal**, not a confirmed vulnerability. Many are true positives; some are patterns a human has already validated and should be suppressed with [`noqa` or `ignore`](usage.md#suppressing-a-finding). See [Limitations](limitations.md) before acting on one as a security verdict.

## Adding a rule

Rules are registered functions in `src/mcp_doctor/rules/`. See [CONTRIBUTING.md](../CONTRIBUTING.md) — a rule is a decorated function plus positive and negative tests.

---

## `MCP012` — live protocol violation

**Severity:** warning · **Category:** contract · **Source:** live audit only

Emitted by `vexar-mcp audit-server` when a running server deviates from the MCP transport
contract:

| Condition | Why it matters |
| :--- | :--- |
| Negotiated `protocolVersion` is not one Vexar-MCP supports | Clients and servers disagree about message shapes in ways that fail at runtime, not at review time |
| `initialize` result omits `protocolVersion` | The handshake is non-conformant; clients fall back to guessing |
| Server does not advertise the `tools` capability | `tools/list` may be rejected or empty for conformant clients |
| A `tools/list` entry is not an object, or has no `name` | The tool cannot be routed to by an LLM at all |
| A tool has no `inputSchema`, or a non-object one | The agent cannot construct valid arguments; schema rules cannot run |
| Pagination exceeds 50 pages | Cursor loops or unbounded catalogs; results may be truncated |

Because these findings describe the *server's* conformance rather than one tool's source,
they are reported against `mcp://<server-name>` rather than a file path, and they appear in
JSON/SARIF like any other finding.

Remediation: re-run the server against a supported MCP revision, advertise `tools` in
`initialize`, and return real `inputSchema` objects from `tools/list`. Full details:
[live-audit.md](live-audit.md).
