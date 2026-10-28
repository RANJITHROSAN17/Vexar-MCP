# Security Policy

## Supported Versions

| Version | Supported |
| :--- | :---: |
| `0.1.x` (`main`) | ✅ |

> Package note: the distribution on PyPI named `mcp-doctor` is an unrelated project by a different author. Vexar-MCP is installed from this repository; see [docs/usage.md](docs/usage.md#install).

## Reporting a Vulnerability

Please **do not** report security vulnerabilities (such as crafted input files causing code execution during static analysis) through public GitHub issues.

Instead, use [**GitHub Private Vulnerability Reporting**](https://github.com/RANJITHROSAN17/Vexar-MCP/security/advisories/new) on this repository. Note that Vexar-MCP analyzes Python source files strictly via `ast.parse` and never imports or executes scanned target code.

---

## Trust boundaries in Vexar-MCP

Vexar-MCP is a tool that inspects untrusted input (your source, your manifests, and — with
`audit-server` — the output of a process). This section states exactly where code can run.

| Surface | Executes code? | Notes |
| :--- | :--- | :--- |
| `scan` on `.py` / `.ts` / `.js` / `.json` | **No** | `ast.parse` and `json.loads` only. No imports, no subprocesses. |
| Reporters | **No** | Pure string generation. HTML embeds data as escaped JSON. |
| `audit-server` | **Yes — a server you name** | argv only (no shell), one total deadline, 8 MiB response cap, 50-page cap, child killed on expiry and reaped. |
| Python rule packs (`--rule-pack module`) | **Yes** | Imported on demand, never auto-discovered. Treat as a dependency. |
| Declarative rule packs (`.toml` / `.json`) | **No** | Data only; parsed and validated, never executed. |

If you find a path that violates these statements — for example a static scan that imports
scanned code, or a pack loaded without being named — that is a security bug. Please report it
privately via GitHub Private Vulnerability Reporting rather than in a public issue.

### Hardening guidance for CI

- Run `audit-server` inside a container with only the secrets that server needs; it is a
  client with the privileges of your job.
- Pin rule packs to a reviewed commit and prefer declarative packs.
- Keep the SARIF and HTML artefacts as build outputs; both are designed to be safe to store
  (no secret literals, escaped HTML).
