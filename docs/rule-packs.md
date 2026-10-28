# Rule packs

Built-in rules cover the MCP ecosystem in general. Every organization also has its own
conventions — namespace prefixes, ownership tags, budget ceilings per team. Rule packs add
those without forking Vexar-MCP.

```bash
vexar-mcp scan . --rule-pack ./policies/acme-rules.toml
```

```toml
# vexar-mcp.toml
rule-packs = ["./policies/acme-rules.toml", "acme_vexar_rules"]
```

## The trust boundary, stated plainly

A rule is code that runs with the scanner's privileges. So:

- **Declarative packs (`.toml` / `.json`) are data.** They are parsed, never executed. This
  is the recommended format and the only one needed for conventions, naming, and budgets.
- **Python packs are code.** `--rule-pack acme_vexar_rules` imports the module and runs its
  registration hook. Load them only after reviewing them like any other dependency, exactly
  as you would review a plugin in any build tool.

Loading is always explicit: nothing is discovered automatically, no entry points, no
`site-packages` scanning. Vexar-MCP will not load a pack because it happens to be installed.

## Declarative format

```toml
[[rule]]
id = "ACME001"                       # ^[A-Z][A-Z0-9_]{2,15}$, never the MCP prefix
slug = "acme-tools-must-be-namespaced"
severity = "warning"                 # error | warning | info
category = "contract"                # security | schema | tokens | contract
summary = "All tools must live in the acme_ namespace."
remediation = "Rename the tool with an acme_ prefix."
message = "Tool '{tool}' is outside the ACME namespace."   # optional, {tool} is substituted

  [rule.match]
  name_regex = "^acme_"              # matches tool name
  invert = true                      # report tools that do NOT match
```

### Match conditions

All configured conditions must hold (logical AND):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `name_regex` | string | Python regex searched against the tool name |
| `description_regex` | string | Regex searched against the description |
| `min_parameters` | int | Tool must declare at least this many parameters |
| `max_parameters` | int | Tool must declare at most this many parameters |
| `min_tokens` | int | Tool must cost at least this many estimated tokens |
| `missing_description` | bool | Tool must have an empty description |
| `invert` | bool | Invert the final result — expresses *requirements*, not bans |

`invert` is what makes convention policies possible:

```toml
  [rule.match]
  name_regex = "^acme_"   # "every tool must start with acme_"
  invert = true
```

A rule with no conditions, an unknown `match` key, an invalid enum value, or an invalid
regex is a hard error — a silently-mistyped rule that matches nothing is worse than no rule.

### Budget examples

```toml
[[rule]]
id = "ACME010"
slug = "acme-tool-too-expensive"
severity = "warning"
category = "tokens"
summary = "ACME tool definitions must stay under 120 estimated tokens."
remediation = "Trim the description or split the tool."
message = "Tool '{tool}' is over the ACME context budget."
  [rule.match]
  min_tokens = 121
```

```toml
[[rule]]
id = "ACME020"
slug = "acme-owner-tag-required"
severity = "info"
category = "contract"
summary = "Tool descriptions must carry an owning team tag."
remediation = "Add `[owner:team-name]` to the tool description."
message = "Tool '{tool}' has no owner tag."
  [rule.match]
  description_regex = "\\[owner:[a-z0-9-]+\\]"
  invert = true
```

The same schema works as JSON: `{"rule": [ ... ]}`.

## Reserved namespaces

- Rule IDs matching `^MCP[0-9]+$` are reserved for the built-in catalog. A pack that tries
  to register `MCP005` is rejected rather than allowed to shadow a built-in rule — silently
  replacing a security check with a no-op is the worst failure mode a scanner can have.
- Duplicate rule IDs and duplicate slugs are rejected.
- Use your own prefix: `ACME001`, `PLATFORM_07`, `SEC_12`.

## Sharing packs

- Generate from code, not from a wiki: JSON from your asset inventory is a great source of
  naming rules.
- Do not ship credentials or internal hostnames inside a pack — packs are committed and
  reviewed.
- Version packs with the repo that uses them. A pack that drifts from the service it
  describes produces noise, and noise gets ignored.

## Python packs

```python
# acme_vexar_rules/__init__.py
from mcp_doctor.models import Category, Diagnostic, Severity
from mcp_doctor.rules import register_rule


def register() -> None:  # called once, on demand
    @register_rule(
        rule_id="ACME100",
        slug="acme-calls-internal-mcp-proxy",
        severity=Severity.WARNING,
        category=Category.SECURITY,
        summary="Tools must not reach the internal proxy directly.",
        remediation="Route through the sanctioned gateway client.",
    )
    def _check(tools, config):
        return [...]  # list[Diagnostic]
```

The hook must register rules itself and return `None`; returning anything else is an error
so a half-written plugin fails loudly. Exceptions and bad registrations abort the scan with
exit code `2` rather than being swallowed.

## Process scope

Loaded packs join a process-global registry: a CLI load applies to every later scan in that
process. That is exactly right for a one-shot CI run. Long-lived hosts and tests that need
isolation should bracket loading:

```python
from mcp_doctor.rules import restore_registry, snapshot_registry
from mcp_doctor.rules.plugin import load_rule_pack

snapshot = snapshot_registry()
try:
    load_rule_pack("./policies/acme.toml")
    report = scan_paths([...])
finally:
    restore_registry(snapshot)
```

## Pack rules and policies

Pack rules are first-class: they appear in `vexar-mcp rules`, in JSON and SARIF output, and
they can be ignored normally. They are *not* in a policy's protected categories, because a
policy protects the security baseline, not organization convention.
