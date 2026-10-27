# Rules

All eleven built-in diagnostics. Run `vexar-mcp rules` for this list from the CLI, or `vexar-mcp rules --format json` for machine-readable output.

| ID | Severity | Category | Slug |
| :--- | :--- | :--- | :--- |
| [`MCP001`](#mcp001) | error | contract | `missing-tool-description` |
| [`MCP002`](#mcp002) | warning | tokens | `excessive-tool-tokens` |
| [`MCP003`](#mcp003) | warning | schema | `untyped-or-any-parameter` |
| [`MCP004`](#mcp004) | info | schema | `unbounded-collection-or-limit` |
| [`MCP005`](#mcp005) | error | security | `dangerous-shell-execution` |
| [`MCP006`](#mcp006) | warning | security | `unvalidated-ssrf-sink` |
| [`MCP007`](#mcp007) | warning | security | `unconfined-filesystem-access` |
| [`MCP008`](#mcp008) | error | contract | `ambiguous-or-duplicate-tool-name` |
| [`MCP009`](#mcp009) | error | security | `hardcoded-secret-in-tool` |
| [`MCP010`](#mcp010) | warning | security | `missing-network-timeout` |
| [`MCP013`](#mcp013) | error | security | `unsafe-deserialization` |

Severity affects the health score (error −15, warning −6, info −2) and the `--fail-on` gate.

`MCP011` and `MCP012` are not implemented. The IDs are reserved by roadmap items and skipped so existing ignore lists and SARIF results stay stable if they land later.

---

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

## MCP002 — excessive-tool-tokens

**warning** · tokens

**Detects:** a single tool definition whose estimated token cost exceeds the per-tool budget (`250` by default, `--max-tokens` or `max-tool-tokens` in config).

**Why it matters:** every tool definition is re-sent to the model on every turn. Verbose descriptions and examples are a permanent context tax.

**Fix:** trim prose, drop redundant examples, or raise the budget deliberately if the tool genuinely needs it. See [token estimation limits](limitations.md#token-estimates-are-approximations).

---

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

## MCP004 — unbounded-collection-or-limit

**info** · schema

**Detects:** a `limit` / `page_size` / `max_results` style parameter with no upper bound.

**Why it matters:** an unbounded result set floods the context window and can stall the agent loop.

**Fix:** `Field(le=200)`, `maximum: 200` in JSON Schema, or clamp with `min(limit, MAX_LIMIT)`.

---

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

## MCP006 — unvalidated-ssrf-sink

**warning** · security

**Detects:** tool input flowing into an outbound HTTP call (`urllib.request.urlopen`, `requests.*`, `httpx.*`) with no visible scheme/host validation in the handler.

**Why it matters:** a model-controlled URL can reach internal services, cloud metadata endpoints (`169.254.169.254`), or `file://` handlers.

**Fix:** parse with `urlparse`, require `https`, check a host allowlist, and reject private/link-local addresses.

---

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

## MCP008 — ambiguous-or-duplicate-tool-name

**error** · contract

**Detects:** duplicate names, names that do not match `[a-z][a-z0-9_-]{2,63}`, and generic verbs such as `run`, `get`, `do`.

**Why it matters:** an agent picks tools by name and description; `run` next to `execute` next to `process` is a routing coin-flip.

**Fix:** use action-oriented `snake_case` — `list_cluster_pods`, not `run`.

---

## MCP009 — hardcoded-secret-in-tool

**error** · security

**Detects:** credential-shaped string literals inside a tool handler — provider token prefixes (`ghp_`, `sk-`, `AKIA`), bearer headers, and assignments to secret-named variables.

**Why it matters:** a key committed inside a tool body is sent to the model provider as part of the tool definition and is visible to anyone with repo access.

**Privacy note:** this rule records only the *shape* of the match — pattern name, variable name, and source line. The literal value is never stored in the diagnostic, JSON report, or SARIF output, so a report cannot leak the key a second time.

**Fix:** read from `os.environ` or a secret manager at runtime, and rotate any key that was ever committed.

---

## MCP010 — missing-network-timeout

**warning** · security

**Detects:** an HTTP call inside a tool handler with no `timeout=` argument (or `timeout=None`).

**Why it matters:** a slow upstream holds the agent turn open indefinitely.

**Fix:** pass an explicit timeout to every outbound call.

---

## MCP013 — unsafe-deserialization

**error** · security

**Detects:** `pickle.loads`, `yaml.load` without `SafeLoader`, and comparable unsafe deserialization of untrusted data.

**Why it matters:** `pickle` payloads execute arbitrary code on load — a remote code execution primitive reachable from model input.

**Fix:** use JSON for untrusted payloads, or `yaml.safe_load`. Never unpickle untrusted data.

---

## Reading a finding

Every diagnostic carries: `rule_id`, `rule_slug`, `severity`, `category`, `message`, `source_file`, `line`, `tool_name`, and `remediation`.

A finding is a **static signal**, not a confirmed vulnerability. Many are true positives; some are patterns a human has already validated and should be suppressed with [`noqa` or `ignore`](usage.md#suppressing-a-finding). See [Limitations](limitations.md) before acting on one as a security verdict.

## Adding a rule

Rules are registered functions in `src/mcp_doctor/rules/`. See [CONTRIBUTING.md](../CONTRIBUTING.md) — a rule is a decorated function plus positive and negative tests.
