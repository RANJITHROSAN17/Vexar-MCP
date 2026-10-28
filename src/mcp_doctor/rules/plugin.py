"""Opt-in rule packs: safe declarative rules plus explicitly trusted Python packs.

Vexar-MCP ships a fixed built-in catalog. Organizations still need their own
conventions, so rule packs exist - but the trust boundary has to be explicit, because
a "rule" is code that runs with the scanner's privileges.

Two pack formats are supported:

1. **Declarative** (``*.toml`` / ``*.json``) - the default and recommended format.
   These files contain *data only* and are never executed. They can match on tool
   name, description, parameter count and token cost, which covers naming conventions,
   ownership tagging and budget drift.

2. **Python module** (``module.path`` or ``module.path:register``) - explicitly
   trusted code, imported only because an operator asked for it by name. Reserved for
   teams auditing packs in review like any other dependency.

Governance rails applied to every pack:

* rule IDs must match ``^[A-Z][A-Z0-9_]{2,15}$`` and may not use the reserved ``MCP``
  prefix, so a pack can never impersonate a built-in rule;
* duplicate IDs and duplicate slugs are rejected (see ``register_definition``);
* packs load once per process, so repeated configuration cannot double-report;
* a broken pack fails loudly with :class:`RulePackError` instead of being skipped.

Loaded packs join a process-global registry, so a pack affects every later scan in the
same process. That is the right behaviour for a CLI run; library hosts and tests that
need isolation should bracket loading with
:func:`mcp_doctor.rules.snapshot_registry` / :func:`mcp_doctor.rules.restore_registry`.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp_doctor.models import Category, Diagnostic, ScanConfig, Severity, ToolSpec
from mcp_doctor.rules.base import (
    RuleDefinition,
    get_all_rules,
    register_definition,
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

RESERVED_RULE_ID_PREFIX = "MCP"
_RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,15}$")
_MATCH_KEYS = frozenset(
    {
        "name_regex",
        "description_regex",
        "min_parameters",
        "max_parameters",
        "min_tokens",
        "missing_description",
        "invert",
    }
)

_LOADED_PACKS: set[str] = set()


class RulePackError(ValueError):
    """Raised when a rule pack is malformed, untrusted, or conflicts with the catalog."""


@dataclass(frozen=True)
class ToolMatcher:
    """Declarative predicate evaluated against a single normalized tool."""

    name_regex: str | None = None
    description_regex: str | None = None
    min_parameters: int | None = None
    max_parameters: int | None = None
    min_tokens: int | None = None
    missing_description: bool = False
    invert: bool = False

    @property
    def is_empty(self) -> bool:
        """True when no condition is configured (such a rule would match everything)."""
        return (
            self.name_regex is None
            and self.description_regex is None
            and self.min_parameters is None
            and self.max_parameters is None
            and self.min_tokens is None
            and not self.missing_description
        )

    def matches(self, tool: ToolSpec) -> bool:
        """Return True when the tool satisfies every configured condition.

        ``invert`` flips the outcome so a pack can express requirements rather than
        bans: ``name_regex = "^acme_"`` with ``invert = true`` flags every tool that is
        *outside* the ACME namespace.
        """
        matched = self._matches_all(tool)
        return not matched if self.invert else matched

    def _matches_all(self, tool: ToolSpec) -> bool:
        if self.name_regex and not re.search(self.name_regex, tool.name):
            return False
        if self.description_regex and not re.search(self.description_regex, tool.description):
            return False
        if self.min_parameters is not None and len(tool.parameters) < self.min_parameters:
            return False
        if self.max_parameters is not None and len(tool.parameters) > self.max_parameters:
            return False
        if self.min_tokens is not None and tool.estimated_tokens < self.min_tokens:
            return False
        return not (self.missing_description and tool.description.strip())


@dataclass(frozen=True)
class DeclarativeRule:
    """A single data-defined rule loaded from a pack file."""

    rule_id: str
    slug: str
    severity: Severity
    category: Category
    summary: str
    remediation: str
    matcher: ToolMatcher
    message: str

    def check(self, tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
        """Emit one diagnostic per tool matching this rule's predicate."""
        del config
        out: list[Diagnostic] = []
        for tool in tools:
            if not self.matcher.matches(tool):
                continue
            out.append(
                Diagnostic(
                    rule_id=self.rule_id,
                    rule_slug=self.slug,
                    severity=self.severity,
                    category=self.category,
                    message=self.message.format(tool=tool.name),
                    source_file=tool.source_file,
                    line=tool.line,
                    tool_name=tool.name,
                    remediation=self.remediation,
                )
            )
        return out

    def to_definition(self) -> RuleDefinition:
        """Convert to the engine's rule protocol."""
        return RuleDefinition(
            rule_id=self.rule_id,
            slug=self.slug,
            severity=self.severity,
            category=self.category,
            summary=self.summary,
            remediation=self.remediation,
            check=self.check,
        )


def _require_str(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RulePackError(f"{where}: missing required string field '{key}'")
    return value.strip()


def _validate_rule_id(rule_id: str, where: str) -> None:
    if not _RULE_ID_RE.match(rule_id):
        raise RulePackError(
            f"{where}: rule id '{rule_id}' must match {_RULE_ID_RE.pattern} (e.g. ACME001)"
        )
    if rule_id.startswith(RESERVED_RULE_ID_PREFIX):
        raise RulePackError(
            f"{where}: rule id '{rule_id}' uses the reserved '{RESERVED_RULE_ID_PREFIX}' "
            "prefix owned by built-in rules. Use an organization prefix such as ACME001."
        )


def _parse_rule(raw: Any, where: str) -> DeclarativeRule:
    if not isinstance(raw, dict):
        raise RulePackError(f"{where}: each [[rule]] entry must be a table/object")

    rule_id = _require_str(raw, "id", where).upper()
    _validate_rule_id(rule_id, where)
    slug = _require_str(raw, "slug", where)
    summary = _require_str(raw, "summary", where)

    severity_raw = _require_str(raw, "severity", where).lower()
    try:
        severity = Severity(severity_raw)
    except ValueError as exc:
        valid = ", ".join(s.value for s in Severity)
        raise RulePackError(
            f"{where}: invalid severity '{severity_raw}'. Use one of: {valid}"
        ) from exc

    category_raw = _require_str(raw, "category", where).lower()
    try:
        category = Category(category_raw)
    except ValueError as exc:
        valid = ", ".join(c.value for c in Category)
        raise RulePackError(
            f"{where}: invalid category '{category_raw}'. Use one of: {valid}"
        ) from exc

    match_raw = raw.get("match", {})
    if not isinstance(match_raw, dict):
        raise RulePackError(f"{where}: 'match' must be a table/object")

    unknown_keys = sorted(set(match_raw) - _MATCH_KEYS)
    if unknown_keys:
        raise RulePackError(
            f"{where}: unknown 'match' key(s): {', '.join(unknown_keys)}. "
            f"Supported: {', '.join(sorted(_MATCH_KEYS))}"
        )

    def _opt_int(key: str) -> int | None:
        value = match_raw.get(key)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise RulePackError(f"{where}: 'match.{key}' must be an integer")
        return value

    def _opt_regex(key: str) -> str | None:
        value = match_raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise RulePackError(f"{where}: 'match.{key}' must be a string")
        try:
            re.compile(value)
        except re.error as exc:
            raise RulePackError(f"{where}: 'match.{key}' is not a valid regex: {exc}") from exc
        return value

    missing_description = match_raw.get("missing_description", False)
    if not isinstance(missing_description, bool):
        raise RulePackError(f"{where}: 'match.missing_description' must be a boolean")

    invert = match_raw.get("invert", False)
    if not isinstance(invert, bool):
        raise RulePackError(f"{where}: 'match.invert' must be a boolean")

    matcher = ToolMatcher(
        name_regex=_opt_regex("name_regex"),
        description_regex=_opt_regex("description_regex"),
        min_parameters=_opt_int("min_parameters"),
        max_parameters=_opt_int("max_parameters"),
        min_tokens=_opt_int("min_tokens"),
        missing_description=missing_description,
        invert=invert,
    )
    if matcher.is_empty:
        raise RulePackError(f"{where}: rule '{rule_id}' defines no 'match' conditions")

    message = raw.get("message")
    if message is not None and not isinstance(message, str):
        raise RulePackError(f"{where}: 'message' must be a string")

    return DeclarativeRule(
        rule_id=rule_id,
        slug=slug,
        severity=severity,
        category=category,
        summary=summary,
        remediation=str(raw.get("remediation", "")).strip(),
        matcher=matcher,
        message=(message or f"Tool '{{tool}}' violates organization rule {rule_id}.").strip(),
    )


def _load_pack_document(path: Path) -> list[Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulePackError(f"cannot read rule pack '{path}': {exc}") from exc
    try:
        doc = json.loads(text) if path.suffix == ".json" else tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RulePackError(f"cannot parse rule pack '{path}': {exc}") from exc

    if not isinstance(doc, dict):
        raise RulePackError(f"rule pack '{path}' must be a table/object with a [[rule]] array")
    rules = doc.get("rule", [])
    if not isinstance(rules, list) or not rules:
        raise RulePackError(f"rule pack '{path}' contains no [[rule]] entries")
    return rules


def load_declarative_pack(path: Path) -> list[DeclarativeRule]:
    """Parse a declarative pack file into rule definitions without registering them."""
    rules = [
        _parse_rule(raw, f"{path}[{index}]") for index, raw in enumerate(_load_pack_document(path))
    ]
    seen: set[str] = set()
    for rule in rules:
        if rule.rule_id in seen:
            raise RulePackError(f"rule pack '{path}' declares duplicate rule id '{rule.rule_id}'")
        seen.add(rule.rule_id)
    return rules


def _load_python_pack(spec: str) -> None:
    module_name, _, attr = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise RulePackError(f"cannot import rule pack module '{module_name}': {exc}") from exc

    hook_name = attr or "register"
    hook = getattr(module, hook_name, None)
    if not callable(hook):
        raise RulePackError(
            f"rule pack module '{module_name}' does not export a callable '{hook_name}'"
        )
    result = hook()
    if result is not None:
        raise RulePackError(
            f"rule pack hook '{module_name}:{hook_name}' must register rules itself and return None"
        )


def load_rule_pack(spec: str) -> list[RuleDefinition]:
    """Load and register one rule pack.

    Args:
        spec: Either a path to a ``.toml``/``.json`` declarative pack, or a dotted
            Python module path (optionally ``module:register``) for trusted code.

    Returns:
        The rule definitions contributed by this pack.

    Raises:
        RulePackError: If the pack is malformed, untrusted, or conflicts with the catalog.
    """
    key = spec.strip()
    if not key:
        raise RulePackError("empty rule pack specification")
    if key in _LOADED_PACKS:
        return []
    _LOADED_PACKS.add(key)

    candidate = Path(key)
    if candidate.suffix in {".toml", ".json"} or candidate.exists():
        try:
            declarative = load_declarative_pack(candidate)
            definitions = [rule.to_definition() for rule in declarative]
            for definition in definitions:
                register_definition(definition)
        except RulePackError as exc:
            raise RulePackError(f"invalid rule pack '{key}': {exc}") from exc
        except ValueError as exc:
            raise RulePackError(f"invalid rule pack '{key}': {exc}") from exc
        return definitions

    if not re.match(r"^[A-Za-z_][\w.]*(:[\w]+)?$", key):
        raise RulePackError(
            f"rule pack '{spec}' is neither an existing .toml/.json file nor a module path"
        )
    before = {definition.rule_id for definition in get_all_rules()}
    try:
        _load_python_pack(key)
    except ValueError as exc:
        if isinstance(exc, RulePackError):
            raise
        raise RulePackError(str(exc)) from exc

    # Python packs register directly through the rule API; report exactly what they added.
    return [definition for definition in get_all_rules() if definition.rule_id not in before]


def load_rule_packs(specs: Sequence[str]) -> list[RuleDefinition]:
    """Load several packs in order, returning every rule they contributed."""
    loaded: list[RuleDefinition] = []
    for spec in specs:
        loaded.extend(load_rule_pack(spec))
    return loaded


def reset_packs() -> None:
    """Clear the loaded-pack cache so a spec can be loaded again (used by tests)."""
    _LOADED_PACKS.clear()


__all__ = [
    "DeclarativeRule",
    "RulePackError",
    "ToolMatcher",
    "load_declarative_pack",
    "load_rule_pack",
    "load_rule_packs",
    "reset_packs",
]
