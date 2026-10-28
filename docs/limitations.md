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
| `MCP011` | `.execute()` on a non-SQL client (GraphQL, RPC, job runner) whose statement happens to contain a SQL keyword |
| `MCP013` | `yaml.load` on data that is trusted by construction |

Suppress individual findings with [`noqa` or `ignore`](usage.md#suppressing-a-finding). If a suppression keeps recurring across projects, open an issue — false-negative and false-positive tuning is in scope for the project.

## Analysis depth

The Python loader reads the AST of each file **independently**:

- **No cross-module data flow.** A parameter passed through three helper modules before reaching a sink is not traced.
- **No interprocedural taint.** Helpers that validate in a different function may not be recognised, which inflates `MCP006` / `MCP007` false positives.
- **`MCP011` needs the interpolation at the call site.** A statement assigned to a variable first (`q = f"SELECT ... {x}"`, then `cur.execute(q)`) is not traced, so this rule under-reports rather than over-reports.
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

---

## Policy, baseline, and live-audit limitations

**Policies are only as good as the rules behind them.** A `strict-prod` preset cannot catch
what no rule detects. The preset hierarchy controls *enforcement*, not coverage — see the
rule limitations above.

**The score is a heuristic.** `score = 100 - (15·errors + 6·warnings + 2·info)` saturates at
zero quickly. It is a trend signal, not a maturity model. `risk_index` uses a different,
documented model (`src/mcp_doctor/risk.py`) and saturates smoothly; neither is a substitute
for reading the findings.

**Baseline fingerprints ignore line numbers.** That is intentional, but it has consequences:
moving a vulnerable tool to a different file makes its finding look *new* (safe direction),
while fixing everything in a tool resolves the entry. Fingerprints are
`rule + tool + file`, so two identical rules firing on the same tool in the same file
collapse into one entry.

**In baseline mode the score floor is not enforced.** A baseline exists to tolerate a low
score while a backlog is burned down. If you want the floor enforced, run an additional
non-baseline scan (for example nightly).

**Live auditing sees only what the server exposes at that moment.** Tools behind feature
flags, entitlements, or migrations that have not run will not appear. It also requires the
server to start successfully in the auditing environment — missing credentials typically
produce exit `2` (could not audit), not a clean result.

**Live auditing cannot judge behaviour.** It reads `tools/list` metadata. It does not call
the tools, so it cannot detect a tool that *claims* to be safe and then shells out at
runtime. It complements static analysis; it does not replace it.

**Protocol validation follows the revisions this build knows.** A newer revision than the
supported list is reported as a warning. If a revision is legitimate but not yet listed,
that is an upgrade, not a server bug.

**Rule packs are heuristics you wrote.** A declarative pack cannot prove anything about a
tool beyond name, description, parameter count, and token cost. A Python pack can do
anything, which is why it is a trust boundary rather than a default.

**Presets are conservative, not complete.** `enterprise-governance` fails on informational
findings. That is a deliberate stance for regulated estates and will be noisy for teams that
have not triaged their backlog — pair it with a baseline.

---

## What "coverage" does and does not mean

`status` and the coverage counters answer **"how much was read?"**, not **"how well was it
understood?"**. A TypeScript file with valid syntax counts as `files_analyzed` even when it
registers tools through computed names, external schema modules, or helper functions this
scanner cannot follow. Those are documented loader limits (see above), not analysis failures.

So:

- `clean` means "no rule fired on what we could read".
- `partial` / `error` means "do not trust this result at all".
- Neither means "safe to ship".

### Language coverage contracts

| Loader | Recognizes | Does not recognize |
| :--- | :--- | :--- |
| Python (AST) | `@mcp.tool()` / `@server.tool()` decorated functions, `Tool(...)` objects, FastMCP builders, typed parameters with bounds, sinks in the handler body | Tools registered through runtime indirection (registries built from data, decorators applied by name), non-`ast`-parseable syntax, tools defined in non-Python sources |
| TypeScript / JS | Lexical scan of `server.tool(...)` / `registerTool` calls with literal names, inline object schemas, inline handlers | Computed identifiers, imported schema factories, externally referenced handler bodies, framework-level registration wrappers, full JS lexical semantics |
| JSON manifests | MCP `tools/list` payloads, OpenAI and Anthropic tool manifests | Manifests produced at runtime, non-JSON formats, references resolved by an external schema registry |

If a construct is outside these contracts, the tool it registers is invisible — and no rule
can fire on it. Prefer `audit-server` for servers whose catalogue is built at runtime, since
it reads what the server actually advertises.
