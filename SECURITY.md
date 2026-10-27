# Security Policy

## Supported Versions

| Version | Supported |
| :--- | :---: |
| `0.1.x` (`main`) | ✅ |

> Package note: the distribution on PyPI named `mcp-doctor` is an unrelated project by a different author. Vexar-MCP is installed from this repository; see [docs/usage.md](docs/usage.md#install).

## Reporting a Vulnerability

Please **do not** report security vulnerabilities (such as crafted input files causing code execution during static analysis) through public GitHub issues.

Instead, use [**GitHub Private Vulnerability Reporting**](https://github.com/RANJITHROSAN17/Vexar-MCP/security/advisories/new) on this repository. Note that Vexar-MCP analyzes Python source files strictly via `ast.parse` and never imports or executes scanned target code.
