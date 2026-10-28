# Policies and gates

A policy is a named, versioned bundle of **detection** settings (token budgets) and
**enforcement** settings (when the build fails). Policies exist so a team can adopt one
reviewable standard instead of passing a pile of flags that drift between laptops and CI.

```bash
vexar-mcp policy                 # list presets and what they enforce
vexar-mcp scan . --policy strict-prod
vexar-mcp scan . --print-config  # exactly what will be enforced, and from which file
```

## Built-in presets

| Preset | Fails on | Score floor | Token budget / tool | Unignorable categories |
| :--- | :--- | :---: | :---: | :--- |
| `default` | `error` | no floor | 250 | — |
| `ci` | `warning` | 80 (B) | 230 | security |
| `strict-prod` | `warning` | 90 (A) | 200 | security |
| `enterprise-governance` | `info` | 97 (A+) | 180 | security, contract |

- **`default`** — developer laptop. Fails only on errors and enforces no floor, so local
  feedback stays fast and honest.
- **`ci`** — pull-request gate. Warnings fail and the score must stay at B or better.
- **`strict-prod`** — release gate for servers exposed to real users or agent swarms.
- **`enterprise-governance`** — audit profile for regulated or multi-team estates; even
  informational findings fail, because budget and routing drift are what compound.

Presets are ordered: each step tightens the threshold, raises the floor, and shrinks the
budget. `vexar-mcp policy --format json` gives the same data machine-readably.

## Precedence

Highest wins:

1. explicit CLI flags — `--policy`, `--fail-on`, `--min-score`, `--max-tokens`, `--ignore`
2. the `[policy]`-style keys in the project configuration file
3. the selected preset's defaults

Run `--print-config` in CI and commit the output if you want enforcement to be visible in
review. It also prints which file was used, so "why is CI stricter than my laptop" is a
five-second answer.

## Protected rules

A preset may protect categories. Rules in those categories **cannot be ignored** — not
from the CLI, not from a configuration file:

```console
$ vexar-mcp scan . --policy strict-prod --ignore MCP005
vexar-mcp: error: policy 'strict-prod' protects security rules; these cannot be ignored:
MCP005. Demote or fix the finding instead, or choose a weaker preset.
```

Exit code `2` — a configuration error, distinct from `1` (a real gate failure). This is
deliberate: the most common way a security gate dies is not being defeated, it is being
quietly silenced one rule at a time. If a finding is genuinely wrong, the answer is to fix
the rule, use an in-source suppression with justification, or pick a weaker preset on
purpose — all of which are visible in review.

Unknown rule IDs in an ignore list are also rejected, so a typo cannot silently disable
your intent.

## Configuration file

`vexar-mcp.toml`, `.mcp-doctor.toml`, or a `[tool.vexar-mcp]` / `[tool.mcp-doctor]` table
in `pyproject.toml`. Discovery walks up from the scan target to the first `.git`, so one
project-level file applies to every subdirectory scan.

```toml
policy = "strict-prod"       # default | ci | strict-prod | enterprise-governance
fail-on = "warning"          # error | warning | info | never
min-score = 90               # 0-100
max-tool-tokens = 200
max-server-tokens = 3000
ignore = ["MCP004"]          # rejected if protected by the active policy
exclude = ["vendor", "generated"]
rule-packs = ["./policies/acme-rules.toml"]
baseline = ".vexar-baseline.json"
```

Every key is optional. Unknown keys are a hard error rather than a silent no-op, because a
scan that quietly ignores a typo is worse than no scan at all.

## Exit codes

| Code | Meaning |
| :---: | :--- |
| `0` | Scan completed and every gate passed |
| `1` | Scan completed, a gate failed (severity, score floor, or baseline delta) |
| `2` | Usage, configuration, I/O, or live-server error — nothing was gated |

CI must distinguish `1` from `2`: `1` means "fix your code", `2` means "fix your
invocation". Treating both as "build broken" is how teams learn to ignore a gate.

## Typical CI wiring

```yaml
- name: Security gate
  run: |
    vexar-mcp scan . \
      --policy strict-prod \
      --baseline .vexar-baseline.json \
      --format sarif \
      --output vexar.sarif
```

Pair a strict preset with a baseline when adopting on an existing codebase — see
[baseline.md](baseline.md).

## Changing a preset is a versioned act

Tightening a built-in preset changes other people's build outcomes, so it follows the same
rules as any other behaviour change:

- tightening within a **minor** release requires a changelog "Policy changes" note and a
  rationale in the PR;
- loosening any preset is reviewed as a **security-relevant** change;
- adding a new preset is always additive and safe.

Full governance for rule and policy changes: [governance.md](governance.md).
