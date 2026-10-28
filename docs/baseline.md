# Baselines: adopting a gate on code that is already dirty

A gate nobody can turn on is a gate nobody uses. If your server has 240 existing findings,
"make CI fail on everything" means the feature is switched off on day one. A baseline turns
that into a ratchet: accept what exists today, fail only on what is introduced tomorrow, and
burn the backlog down deliberately.

## Workflow

```bash
# 1. Record what exists today. Commit the result.
vexar-mcp baseline write . --output .vexar-baseline.json

# 2. Gate CI on the delta only.
vexar-mcp scan . --baseline .vexar-baseline.json --policy strict-prod

# 3. Inspect what a baseline is holding.
vexar-mcp baseline show .vexar-baseline.json

# 4. After fixing a tranche, refresh it — resolved entries simply disappear.
vexar-mcp baseline write . --output .vexar-baseline.json
```

`--write-baseline` on `scan` does the same as `baseline write` when you are already running
a scan:

```bash
vexar-mcp scan . --write-baseline .vexar-baseline.json
```

## What the gate does in baseline mode

- New findings at or above the fail threshold fail the run (`exit 1`).
- Pre-existing findings are reported as `unchanged` and do not fail the run.
- The score floor is **reported but not enforced**, because a low score is exactly what a
  baseline exists to tolerate. Use the absolute (non-baseline) scan in a nightly job if you
  want the floor enforced as a burn-down target.
- Resolved findings are listed, so progress is visible on every pull request.

## Fingerprints

A finding's identity is:

```
sha256(rule_id | tool_name | normalized_source_file)
```

Line numbers and message text are deliberately **excluded**. Including them would make
every unrelated edit look like a brand-new violation — which trains teams to ignore the
tool. A finding is identified by *what it is*, not where it currently sits.

Consider the consequence honestly: if you move a vulnerable tool to another file, the
finding looks new and the gate fires. That is the safe direction to be wrong in. If you fix
the tool's name, the entry is resolved and disappears.

## File format

Versioned JSON, sorted deterministically, so it reviews and diffs cleanly in a pull request:

```json
{
  "schema": "vexar-mcp-baseline/v1",
  "tool_version": "0.2.0",
  "policy": "strict-prod",
  "created_at": "2026-10-28T06:19:30+00:00",
  "entry_count": 9,
  "entries": [
    {
      "fingerprint": "0a1b2c3d...",
      "rule_id": "MCP005",
      "tool_name": "run",
      "source_file": "examples/vulnerable_server.py",
      "severity": "error"
    }
  ]
}
```

Any malformed, truncated, or newer-schema baseline fails closed with exit code `2` and a
message naming the file — a baseline is a security artefact, so it is never silently
treated as empty.

## Governance expectations

Because a baseline is a list of accepted risk:

- **Commit it.** A baseline in `.gitignore` is an invisible exception list.
- **Review changes to it** like code. A baseline diff that only shrinks is a good PR.
- **Never add an entry by hand.** Regenerate from a scan so every accepted finding is one
  the tool actually reported.
- **Give it an owner and an expiry.** An accepted-risk list with no owner becomes permanent.
  Tag the issue that burns it down.
- **Keep one baseline per policy.** `strict-prod` and `enterprise-governance` accept
  different things by design.

## How this differs from ignoring a rule

Ignoring a rule silences a class of findings everywhere, forever, including new code.
A baseline accepts specific findings on specific tools in specific files, and any new
instance still fails the build. Prefer the baseline; use `ignore` only for a rule that is
genuinely wrong for the whole project.
