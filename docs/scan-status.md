# Scan status, coverage, and exit codes

Vexar-MCP reports **two independent things**:

1. **What the scanner found** — findings, risk, score.
2. **Whether the analysis actually completed** — status and coverage.

They are never merged, because an empty findings list on a scan that could not read your
code means nothing. Before 0.2.1 those two questions shared one answer, and the tool could
print `100/100 (A+)` and exit `0` on a file with a syntax error. That class of bug is now
closed by construction and pinned by tests.

## Status

| Status | Meaning | Graded? |
| :--- | :--- | :---: |
| `clean` | Every candidate file was analyzed and no rule fired | yes |
| `findings` | Every candidate file was analyzed; at least one rule fired | yes |
| `partial` | Some files could not be parsed or read — results are incomplete | **no** |
| `no_matches` | Files were read, but no supported MCP tool was recognized | **no** |
| `error` | Nothing could be analyzed | **no** |

`partial`, `no_matches` and `error` all report `score: null` and `grade: "N/A"`. That is
deliberate: a grade is a claim about your codebase, and it is withheld when part of the
codebase was never read. Counts (errors, warnings, info, tokens) are still reported.

## Coverage

Every report carries five separate counters. They answer different questions and are never
substituted for one another:

| Counter | Question it answers |
| :--- | :--- |
| `candidate_files` | How many supported files were in the requested scope? |
| `files_analyzed` | How many were actually parsed? |
| `files_failed` | How many could not be parsed or read? |
| `files_with_tools` | How many contained at least one recognized tool? |
| `tools_analyzed` | How many tool definitions were examined in total? |

`summary.files_scanned` means **files analyzed**. It previously counted only files that
contained tools, which made a scan of forty clean Python files report `0`.

## Parse issues

Unreadable and unparsable inputs are reported as first-class data:

```json
"parse_issues": [
  {
    "kind": "syntax_error",
    "source_file": "src/servers/broken.py",
    "line": 6,
    "message": "SyntaxError: invalid syntax"
  }
]
```

Kinds: `syntax_error`, `invalid_json`, `unreadable`. They appear in the terminal report, the
Markdown summary, the HTML report (their own section), and in SARIF as
`invocations[0].toolExecutionNotifications` with `executionSuccessful: false` — which is
where SARIF expects "the tool could not do its job". They are **never** emitted as SARIF
`results` or counted as findings, because a parse error is not a security result.

## Exit codes

| Code | Meaning | What to do |
| :---: | :--- | :--- |
| `0` | Analysis completed and every gate passed | nothing |
| `1` | Analysis completed, a gate failed (severity, score floor, baseline delta) | fix the code |
| `2` | Usage, configuration, I/O, or live-server error | fix the invocation |
| `3` | **Analysis incomplete** — files could not be parsed | fix the files, or `--allow-incomplete` |
| `4` | No supported MCP tool found, and `--fail-on-no-matches` was set | check the target or the flag |

`1` and `3` both fail a build, and they must stay distinguishable: `1` means "your code has
problems", `3` means "we could not read your code". Conflating them teaches a team that a
red build is noise, which is how gates die.

## In CI

```bash
# Default posture: an unreadable file fails the build.
vexar-mcp scan . --policy strict-prod

# Downgrade deliberately, e.g. while migrating a legacy tree.
vexar-mcp scan . --policy strict-prod --allow-incomplete

# Require that the scan found something at all.
vexar-mcp scan . --fail-on-no-matches
```

With the composite action, read the `status`, `analysis-complete` and `files-failed`
outputs. `score` is deliberately **empty** when the scan was not graded, so a downstream
step cannot accidentally treat "we measured nothing" as `100`.

## What is still not covered

Coverage says *how much was read*, not *how well it was understood*. A TypeScript file with
no syntax errors may still register tools through computed names and external schemas that
this scanner cannot follow — see the loader contracts in
[limitations.md](limitations.md). Those files count as `files_analyzed`; their tool-level
completeness is a separate, documented limitation.

Treat `clean` as "nothing fired on what we could read", and treat `partial`/`error` as "do
not trust this result at all".
