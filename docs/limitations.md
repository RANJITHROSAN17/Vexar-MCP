# Limitations

Vexar-MCP is a static analyzer. Read this before treating a clean scan as approval to ship, or a finding as a confirmed vulnerability.

## It does not prove safety

A scan with zero findings means none of the eleven rules matched the code it could parse. It does not mean the server is secure.

Not covered by any rule, for example:

- authorization and authentication design
- prompt-injection behaviour of the tool descriptions themselves
- secret handling at runtime (env plumbing, rotation, least privilege)
- dependency vulnerabilities in your server or its transitive packages
- business-logic flaws, race conditions, resource exhaustion under concurrency
- transport-level issues (TLS, SSE/stdio framing, session handling)
- anything in languages other than Python, TypeScript/JavaScript, and JSON manifests

Use it as one cheap gate in a review process, alongside dependency scanning, tests, and human review.

## It does not prove a finding is exploitable

Every rule is a static heuristic. A match means a risky *shape* exists at that line, not that an attacker can reach it. `MCP006` flags an unvalidated URL flow; whether that flow is actually reachable from a model-driven path depends on the rest of your code.

Known sources of false positives:

| Rule | Typical false positive |
| :--- | :--- |
| `MCP005` | Shell usage with a hard-coded, non-user-derived command |
| `MCP006` | Host validation done in a helper the analyzer does not follow |
| `MCP007` | Path confinement enforced by a framework or decorator upstream |
| `MCP009` | Test fixtures and placeholder strings shaped like real keys |
| `MCP010` | Timeout configured on a shared session/transport object |
| `MCP013` | `yaml.load` on data that is trusted by construction |

Suppress individual findings with [`noqa` or `ignore`](usage.md#suppressing-a-finding). If a suppression keeps recurring across projects, open an issue — false-negative and false-positive tuning is in scope for the project.

## Analysis depth

The Python loader reads the AST of each file **independently**:

- **No cross-module data flow.** A parameter passed through three helper modules before reaching a sink is not traced.
- **No interprocedural taint.** Helpers that validate in a different function may not be recognised, which inflates `MCP006` / `MCP007` false positives.
- **No execution, no imports.** This is deliberate: scanned code cannot run during a scan. It also means dynamic constructs (`getattr`, decorators applied at runtime, factory-built tool registries, `exec`-generated handlers) are invisible.
- **Framework coverage is pattern-based.** FastMCP, the official `mcp` SDK `@server.tool()` style, and `Tool(...)` objects are recognised. Unusual registration helpers may not be detected — a file that yields zero tools is silently skipped, so check that the tool count in the summary looks right.

## TypeScript and JavaScript coverage

The TS loader is a static scan, not a TypeScript compiler. It recognises common `server.tool(...)` registration shapes. It does not resolve imports, follow generics, or evaluate decorators, so complex or heavily abstracted registrations can be missed.

## Token estimates are approximations

`estimate_tool_tokens` is a deterministic offline heuristic based on character counts and schema structure. It is designed to be fast and dependency-free, and to rank tools consistently.

- It is **not** a tokenizer round-trip. Absolute values can differ from your model's tokenizer by a meaningful margin.
- Compare tools against each other and track change over time rather than treating `~425 tokens` as an exact billed figure.
- `MCP002` thresholds are therefore policy knobs, not measurements.

## Configuration discovery is directory-local

Configuration is discovered from the directory of the first scan target, not from per-file directories. In a monorepo with several MCP servers, run one scan per server (or pass `--config`) instead of expecting per-package configuration to be picked up.

## Suppression semantics

`# noqa` must name the rules explicitly (`# noqa: MCP001` or `# noqa: MCP001,MCP008`) or use `ALL`. A bare `# noqa` with no rule list does not suppress anything.

## Distribution

There is no PyPI release of `vexar-mcp`. `pip install vexar-mcp` does not work, and the `mcp-doctor` package on PyPI is an unrelated project by a different author. Install from the repository as described in [Usage](usage.md#install).

The dashboard in `assets/` is a static viewer. It reads a report file next to itself; it is not a hosted service, and it does not receive data from CI automatically. See [Dashboard](dashboard.md).

## Reporting a limitation

If a rule misfires on realistic code, or misses an obvious sink, that is a bug worth fixing: open an issue with the file, the line, and what you expected. Security issues in Vexar-MCP itself go through [the private channel](../SECURITY.md), not a public issue.
