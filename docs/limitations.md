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

- **No cross-module data flow.** A parameter passed into a helper in another file is not traced. Only helpers defined in the same module are followed.
- **Taint propagation is one hop.** A parameter passed into `_run_cmd(cmd)` in the same file is followed and the sink inside that helper is attributed to the tool (see below). A sink two helpers away (`tool -> _outer -> _inner`) is not reached: that needs inter-procedural dataflow rather than name matching.
- **Helpers are only followed when they consume the parameter.** A helper containing `os.system("ls")` that the tool calls with no arguments produces no finding, because the tool does not control that command.
- **Methods, classes, and imports are not traced.** `self._run(cmd)`, a closure defined inside the tool, and a function imported from another module all end the trace — following them needs type or inter-module analysis, and a guessed trace produces findings nobody can explain from the code shown next to them.
- **`MCP011` needs the interpolation at the call site.** A statement assigned to a variable first (`q = f"SELECT ... {x}"`, then `cur.execute(q)`) is not traced, so this rule under-reports rather than over-reports.
- **No execution, no imports.** This is deliberate: scanned code cannot run during a scan. It also means dynamic constructs (`getattr`, decorators applied at runtime, factory-built tool registries, `exec`-generated handlers) are invisible.
- **Framework coverage is pattern-based.** FastMCP, the official `mcp` SDK `@server.tool()` style, and `Tool(...)` objects are recognised. Unusual registration helpers may not be detected — a file that yields zero tools is silently skipped, so check that the tool count in the summary looks right.

### What one-hop helper propagation does and does not cover

Rules `MCP005`–`MCP007` (plus `MCP011` and `MCP013`) attribute a sink found inside a module-local helper to the tool that passes a tainted parameter in:

```python
def _run_cmd(cmd):
    return subprocess.run(cmd, shell=True)  # <- the sink


@mcp.tool()
def deploy(command: str) -> str:
    return _run_cmd(command)  # <- MCP005 reported here, for 'command'
```

The finding names the tool's own parameter, not the helper's, and points at the sink's line. Guards are inherited: validating a URL in the tool body before delegating keeps the helper's request from being reported as `MCP006`, and a guard inside the helper covers its own sinks.

Argument mapping is positional and by keyword. A call whose mapping cannot be determined — a `*args`/`**kwargs` spread, or a keyword that the helper does not declare — is **skipped rather than guessed**, so a sink attributed to the wrong parameter is not a failure mode. The cost of that choice is recall: passing user input through an untyped `**kwargs` helper will not be traced.

The trace is deliberately shallow. Each additional hop multiplies the chance of a finding that cannot be explained by the code beside it, and an unexplainable finding is one that gets suppressed wholesale.

## TypeScript and JavaScript coverage

The TS loader is a static scan, not a TypeScript compiler. It recognises common `server.tool(...)` registration shapes. It does not resolve imports, follow generics, or evaluate decorators, so complex or heavily abstracted registrations can be missed.

### Choosing a parser backend: `--ts-parser lexical|tree-sitter`

There are two front ends, and the choice is explicit. **`lexical` is the default and needs
nothing installed** — a bare `pip install Vexar-MCP` uses it automatically.

```bash
vexar-mcp scan .                                  # lexical (default)
vexar-mcp scan . --ts-parser tree-sitter          # opt-in, needs the extra
pip install "Vexar-MCP[tree-sitter]"              # the extra
```

The tree-sitter backend parses the file into a real syntax tree and resolves **same-file**
identifiers before analysing them. It does not replace the analysis: the resolved spans go
through the *same* parameter and sink code the lexical scanner uses, so on a file both
backends can read, they produce the same tools, the same parameters, the same sinks and the
same score. That is enforced by a test over both committed TypeScript fixtures, because a
security report whose findings depend on which optional package is installed is worse than
one with a documented blind spot.

| Construct | `lexical` (default) | `tree-sitter` (opt-in) |
| :--- | :--- | :--- |
| `server.tool("literal_name", ...)` | Recognised | Recognised |
| `const NAME = "x"; server.tool(NAME, ...)` | Unresolved issue | Recognised |
| `const DESC = "..."` used as the description | Invisible (empty description) | Recognised |
| `const SCHEMA = { ... };` used as the schema | Tool kept; unresolved issue | Recognised, with parameters |
| `function makeSchema() { return { ... }; }` → `makeSchema()` | Tool kept; unresolved issue | Recognised when it has exactly one `return` |
| `async function handler() {...}` passed by name | Registered, **sinks not inspected** | Registered, sinks inspected |
| Inline `async () => {}` handler | Recognised | Recognised |
| `` const ID = `tool_${stage}` `` (template substitution) | Unresolved issue | Unresolved issue |
| Identifier imported from another module | Unresolved issue | Unresolved issue |
| `const { ID } = config` (destructuring) | Unresolved issue | Unresolved issue |
| Factory with several `return` statements | Tool kept; unresolved issue | Tool kept; unresolved issue |
| Decorator- or registry-driven registration | Invisible | Invisible |

Unresolvable constructs are deliberately not guessed: a security scanner that invents
a tool's shape grades fiction. Some registration patterns remain invisible altogether.

**Why same-file only.** Following an `import` would attribute a finding to a file the user
may not have scanned, using a line number from somewhere else. For a diagnostic tool a
misattribution is worse than a documented gap, so cross-file resolution is out of scope.

**Missing extra.** Selecting `--ts-parser tree-sitter` without the extra installed is an
error (exit `2`) that names the install command. It does **not** silently fall back to the
lexical scanner: a report must never claim coverage it did not have.

**What the JSON report records.** Every file scan carries a `typescript` block so two runs
are only compared when they measured the same way:

```json
"typescript": {"backend": "tree-sitter", "unresolved_registrations": 3}
```

`unresolved_registrations` counts registrations that were *visible in the syntax* but whose
arguments could not be proved. It is the honest counterpart to the tool count: with the
default backend, computed names and unsupported schema expressions are counted even when
their values cannot be resolved. A non-zero value means the tool list is a
**floor**, not a verdict. The field is `null` when no TypeScript was parsed at all — a live
audit, or a scan that found no `.ts`/`.js` files.

### Unresolved tool signals and fixture-corpus audit

An `unresolved_tool` analysis issue identifies a registration whose name, metadata or
input schema could not be understood, with file and line. Python also flags dynamic
names in recognized decorators and `Tool(name=...)`, and simple imported non-stdlib
parameter annotations (including quoted annotations). A partially understood tool may
still appear in the tool list; unresolved names are not guessed. These issues set status
to `partial`, withhold the score and cause exit 3 unless `--allow-incomplete` is used;
they are not security findings. Successfully parsed files remain counted as analyzed.

Before enabling these signals by default, the four committed files in `tests/fixtures/`
(two Python mock servers and two TypeScript servers) were scanned individually with each
backend. Manual inspection found **0 false-positive issues / 4 files (0%)** for lexical
and **0 / 4 (0%)** for tree-sitter. This tiny, mostly literal corpus cannot establish a
real-world false-positive rate. The Python heuristic treats any simple non-standard-library
imported parameter type as a possible schema, including imported scalars or enums, which
can produce false positives. Likewise `.tool` and `.registerTool` are matched by method
name, not proven to be MCP objects. Conversely, dynamic registration mechanisms, generic
Python annotations, nested schema fields, and named handlers may be missed without an issue;
absence of these signals does not prove complete semantic coverage.

Reproduce the audit (install `.[dev]` for tree-sitter):

```python
from pathlib import Path
from vexar_mcp.engine import scan_paths
from vexar_mcp.models import IssueKind, ScanConfig
for backend in ("lexical", "tree-sitter"):
    for path in sorted(Path("tests/fixtures").glob("*")):
        if path.suffix in {".py", ".ts", ".js", ".jsx", ".json"}:
            report = scan_paths([path], ScanConfig(ts_parser=backend))
            print(backend, path, [i for i in report.parse_issues if i.kind == IssueKind.UNRESOLVED_TOOL])
```

## Token estimates are approximations

`estimate_tool_tokens` is a deterministic offline heuristic based on character counts and schema structure. It is designed to be fast and dependency-free, and to rank tools consistently.

- It is **not** a tokenizer round-trip. Absolute values can differ from your model's tokenizer by a meaningful margin.
- Compare tools against each other and track change over time rather than treating `~425 tokens` as an exact billed figure.
- `MCP002` thresholds are therefore policy knobs, not measurements.

## Configuration discovery is directory-local

Configuration is discovered from the directory of the first scan target, not from per-file directories. In a monorepo with several MCP servers, run one scan per server (or pass `--config`) instead of expecting per-package configuration to be picked up.

## Suppression semantics

`# noqa` must name the rules explicitly (`# noqa: MCP001` or `# noqa: MCP001,MCP008`) or use `ALL`. A bare `# noqa` with no rule list does not suppress anything.

## YAML manifests depend on an optional package

YAML manifests are read only when PyYAML is installed (`pip install 'vexar-mcp[yaml]'`). Without it, a manifest-shaped file is reported as an unanalyzed input rather than being skipped silently, so a bare install can never turn a YAML-only repository into a clean scan. Discovery is also heuristic: a YAML file is only parsed when it contains a `tools:` or `inputSchema:` key, which keeps CI workflows and compose files out of the analysis. A manifest that expresses its tools some other way needs to be named explicitly on the command line.

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
documented model (`src/vexar_mcp/risk.py`) and saturates smoothly; neither is a substitute
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
scanner cannot follow — unless `--ts-parser tree-sitter` is used, in which case some of
those become readable and the rest are counted in `typescript.unresolved_registrations`. Those are documented loader limits (see above), not analysis failures.

So:

- `clean` means "no rule fired on what we could read".
- `partial` / `error` means "do not trust this result at all".
- Neither means "safe to ship".

### Language coverage contracts

| Loader | Recognizes | Does not recognize |
| :--- | :--- | :--- |
| Python (AST) | `@mcp.tool()` / `@server.tool()` decorated functions, `Tool(...)` objects, FastMCP builders, typed parameters with bounds, sinks in the handler body | Tools registered through runtime indirection (registries built from data, decorators applied by name), non-`ast`-parseable syntax, tools defined in non-Python sources |
| TypeScript / JS (`lexical`, default) | `server.tool(...)` / `registerTool` calls with literal names, inline object schemas, inline handlers | Computed identifiers, imported schema factories, externally referenced handler bodies, framework-level registration wrappers, full JS lexical semantics |
| TypeScript / JS (`tree-sitter`, opt-in extra) | Everything the lexical backend recognises, plus same-file constant names and descriptions, same-file schema constants, single-`return` schema factories, and handler bodies referenced by name | Cross-module imports, template strings with substitutions, destructured bindings, factories whose value depends on an argument, decorators, full JS lexical semantics |
| JSON manifests | MCP `tools/list` payloads, OpenAI and Anthropic tool manifests | Manifests produced at runtime, non-JSON formats, references resolved by an external schema registry |

If a construct is outside these contracts, the tool it registers is invisible — and no rule
can fire on it. Prefer `audit-server` for servers whose catalogue is built at runtime, since
it reads what the server actually advertises.
