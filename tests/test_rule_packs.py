"""Tests for the opt-in rule pack system and its trust rails."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from mcp_doctor.engine import scan_paths
from mcp_doctor.models import Category, ScanConfig, Severity, ToolSpec
from mcp_doctor.rules import get_all_rules, get_rule_catalog, register_definition
from mcp_doctor.rules.base import RuleDefinition
from mcp_doctor.rules.plugin import (
    DeclarativeRule,
    RulePackError,
    ToolMatcher,
    load_declarative_pack,
    load_rule_pack,
    load_rule_packs,
    reset_packs,
)

EXAMPLES = Path(__file__).parent.parent / "examples"

VALID_PACK = """
[[rule]]
id = "ACME001"
slug = "acme-tools-must-be-owner-tagged"
severity = "warning"
category = "contract"
summary = "Every ACME tool must be prefixed with the team namespace."
remediation = "Rename the tool to the `acme_` namespace."
message = "Tool '{tool}' is outside the ACME namespace."
  [rule.match]
  name_regex = "^acme_"
  invert = true
"""


@pytest.fixture(autouse=True)
def _clean_packs() -> None:
    reset_packs()
    yield
    reset_packs()


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def test_declarative_pack_parses(tmp_path: Path) -> None:
    rules = load_declarative_pack(_write(tmp_path, "pack.toml", VALID_PACK))
    assert len(rules) == 1
    rule = rules[0]
    assert rule.rule_id == "ACME001"
    assert rule.severity is Severity.WARNING
    assert rule.category is Category.CONTRACT


def test_unknown_match_keys_are_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "typo.toml",
        """
        [[rule]]
        id = "ACME005"
        slug = "typo"
        severity = "error"
        category = "security"
        summary = "x"
          [rule.match]
          name_regexx = "^acme_"
        """,
    )
    with pytest.raises(RulePackError, match="unknown 'match' key"):
        load_rule_pack(str(path))


def test_rule_without_conditions_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "empty-match.toml",
        """
        [[rule]]
        id = "ACME006"
        slug = "empty"
        severity = "error"
        category = "security"
        summary = "x"
          [rule.match]
        """,
    )
    with pytest.raises(RulePackError, match="no 'match' conditions"):
        load_rule_pack(str(path))


def test_declarative_rule_matches_tools() -> None:
    rule = DeclarativeRule(
        rule_id="ACME002",
        slug="acme-banned-name",
        severity=Severity.ERROR,
        category=Category.CONTRACT,
        summary="banned",
        remediation="rename",
        matcher=ToolMatcher(name_regex="^internal_"),
        message="Tool {tool} is internal-only.",
    )
    tools = [
        ToolSpec(name="internal_dump", description="d" * 20, source_file="a.py", line=1),
        ToolSpec(name="public_read", description="d" * 20, source_file="a.py", line=2),
    ]
    findings = rule.check(tools, ScanConfig())
    assert [f.tool_name for f in findings] == ["internal_dump"]
    assert "internal_dump" in findings[0].message


def test_invert_expresses_namespace_requirements() -> None:
    """`invert` lets a pack require a convention instead of banning a pattern."""
    matcher = ToolMatcher(name_regex="^acme_", invert=True)
    namespaced = ToolSpec(name="acme_export", description="d" * 20, source_file="a.py", line=1)
    outsider = ToolSpec(name="export_report", description="d" * 20, source_file="a.py", line=2)
    assert matcher.matches(namespaced) is False
    assert matcher.matches(outsider) is True


def test_inverted_rule_flags_namespace_violations(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "namespace.toml",
        """
        [[rule]]
        id = "ACME070"
        slug = "acme-namespace-required"
        severity = "warning"
        category = "contract"
        summary = "All tools must live in the acme_ namespace."
        remediation = "Rename the tool with an acme_ prefix."
        message = "Tool '{tool}' is not namespaced."
          [rule.match]
          name_regex = "^acme_"
          invert = true
        """,
    )
    rules = load_declarative_pack(path)
    tools = [
        ToolSpec(name="acme_ok", description="d" * 20, source_file="a.py", line=1),
        ToolSpec(name="not_ok", description="d" * 20, source_file="a.py", line=2),
    ]
    findings = rules[0].check(tools, ScanConfig())
    assert [f.tool_name for f in findings] == ["not_ok"]


def test_tool_matcher_supports_all_predicates() -> None:
    tool = ToolSpec(
        name="acme_export",
        description="",
        source_file="a.py",
        line=1,
        estimated_tokens=50,
    )
    assert ToolMatcher(missing_description=True).matches(tool)
    assert ToolMatcher(description_regex="compliance").matches(tool) is False
    assert ToolMatcher(min_parameters=1).matches(tool) is False
    assert ToolMatcher(max_parameters=0).matches(tool)
    assert ToolMatcher(min_tokens=10).matches(tool)
    assert ToolMatcher(min_tokens=500).matches(tool) is False


def test_json_pack_is_supported(tmp_path: Path) -> None:
    path = write = tmp_path / "pack.json"
    write.write_text(
        """
        {"rule": [{
          "id": "ACME010",
          "slug": "acme-json-rule",
          "severity": "info",
          "category": "schema",
          "summary": "json pack",
          "match": {"name_regex": "^acme_"},
          "remediation": "n/a"
        }]}
        """,
        encoding="utf-8",
    )
    rules = load_declarative_pack(path)
    assert rules[0].rule_id == "ACME010"
    assert rules[0].category is Category.SCHEMA


def test_reserved_prefix_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "pack.toml",
        """
        [[rule]]
        id = "MCP900"
        slug = "shadow"
        severity = "error"
        category = "security"
        summary = "shadow a built-in"
          [rule.match]
          name_regex = ".*"
        """,
    )
    with pytest.raises(RulePackError, match="reserved 'MCP' prefix"):
        load_rule_pack(str(path))


def test_invalid_rule_id_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "pack.toml",
        """
        [[rule]]
        id = "bad-id!"
        slug = "s"
        severity = "error"
        category = "security"
        summary = "x"
          [rule.match]
          name_regex = ".*"
        """,
    )
    with pytest.raises(RulePackError, match="must match"):
        load_rule_pack(str(path))


def test_invalid_enum_values_are_rejected(tmp_path: Path) -> None:
    bad_severity = _write(
        tmp_path,
        "s.toml",
        """
        [[rule]]
        id = "ACME020"
        slug = "s"
        severity = "catastrophic"
        category = "security"
        summary = "x"
          [rule.match]
          name_regex = ".*"
        """,
    )
    with pytest.raises(RulePackError, match="invalid severity"):
        load_rule_pack(str(bad_severity))

    bad_category = _write(
        tmp_path,
        "c.toml",
        """
        [[rule]]
        id = "ACME021"
        slug = "s"
        severity = "error"
        category = "vibes"
        summary = "x"
          [rule.match]
          name_regex = ".*"
        """,
    )
    with pytest.raises(RulePackError, match="invalid category"):
        load_rule_pack(str(bad_category))


def test_invalid_regex_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "pack.toml",
        """
        [[rule]]
        id = "ACME030"
        slug = "s"
        severity = "error"
        category = "security"
        summary = "x"
          [rule.match]
          name_regex = "[unclosed"
        """,
    )
    with pytest.raises(RulePackError, match="not a valid regex"):
        load_rule_pack(str(path))


def test_duplicate_ids_within_pack_are_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "pack.toml",
        """
        [[rule]]
        id = "ACME040"
        slug = "one"
        severity = "error"
        category = "security"
        summary = "x"
          [rule.match]
          name_regex = ".*"

        [[rule]]
        id = "ACME040"
        slug = "two"
        severity = "info"
        category = "schema"
        summary = "y"
          [rule.match]
          name_regex = ".*"
        """,
    )
    with pytest.raises(RulePackError, match="duplicate rule id"):
        load_rule_pack(str(path))


def test_empty_or_malformed_pack_is_rejected(tmp_path: Path) -> None:
    empty = _write(tmp_path, "empty.toml", "# nothing here\n")
    with pytest.raises(RulePackError, match="no \\[\\[rule\\]\\] entries"):
        load_rule_pack(str(empty))

    broken = _write(tmp_path, "broken.toml", "[[rule]\n")
    with pytest.raises(RulePackError, match="cannot parse"):
        load_rule_pack(str(broken))

    missing = tmp_path / "missing.toml"
    with pytest.raises(RulePackError, match="cannot read"):
        load_rule_pack(str(missing))


def test_packs_load_once_per_process(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "pack.toml",
        """
        [[rule]]
        id = "ACME050"
        slug = "once"
        severity = "warning"
        category = "contract"
        summary = "x"
          [rule.match]
          name_regex = ".*"
        """,
    )
    first = load_rule_pack(str(path))
    second = load_rule_pack(str(path))
    assert len(first) == 1
    assert second == []


def test_register_definition_refuses_to_shadow_builtin() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_definition(
            RuleDefinition(
                rule_id="MCP005",
                slug="totally-new-slug",
                severity=Severity.INFO,
                category=Category.SECURITY,
                summary="shadow",
                remediation="none",
                check=lambda tools, config: [],
            )
        )


def test_register_definition_refuses_duplicate_slug() -> None:
    with pytest.raises(ValueError, match="slug"):
        register_definition(
            RuleDefinition(
                rule_id="ACME999",
                slug="dangerous-shell-execution",
                severity=Severity.INFO,
                category=Category.SECURITY,
                summary="dup slug",
                remediation="none",
                check=lambda tools, config: [],
            )
        )


def test_python_pack_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path,
        "acme_pack.py",
        '''
        """Trusted Python rule pack used by tests."""

        from mcp_doctor.models import Category, Diagnostic, Severity
        from mcp_doctor.rules import register_rule


        def register() -> None:
            @register_rule(
                rule_id="ACME100",
                slug="acme-python-pack",
                severity=Severity.INFO,
                category=Category.CONTRACT,
                summary="Python pack rule",
                remediation="none",
            )
            def _check(tools, config):
                return [
                    Diagnostic(
                        rule_id="ACME100",
                        rule_slug="acme-python-pack",
                        severity=Severity.INFO,
                        category=Category.CONTRACT,
                        message="python pack hit",
                        source_file=t.source_file,
                        line=t.line,
                        tool_name=t.name,
                    )
                    for t in tools
                ]
        ''',
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        loaded = load_rule_pack("acme_pack:register")
        assert [r.rule_id for r in loaded] == ["ACME100"]
        assert "ACME100" in get_rule_catalog()
    finally:
        sys.modules.pop("acme_pack", None)


def test_python_pack_must_return_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path,
        "acme_bad_pack.py",
        """
        def register():
            return ["not", "allowed"]
        """,
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with pytest.raises(RulePackError, match="must register rules itself"):
            load_rule_pack("acme_bad_pack:register")
    finally:
        sys.modules.pop("acme_bad_pack", None)


def test_python_pack_missing_hook_and_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "acme_no_hook.py", "x = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with pytest.raises(RulePackError, match="does not export a callable"):
            load_rule_pack("acme_no_hook")
    finally:
        sys.modules.pop("acme_no_hook", None)

    with pytest.raises(RulePackError, match="cannot import rule pack module"):
        load_rule_pack("definitely_not_a_real_module_xyz")


def test_invalid_spec_and_empty_spec_are_rejected() -> None:
    with pytest.raises(RulePackError, match="empty rule pack"):
        load_rule_pack("   ")
    with pytest.raises(RulePackError, match="neither an existing"):
        load_rule_pack("../../etc/passwd")


def test_load_rule_packs_accumulates() -> None:
    assert load_rule_packs([]) == []


def test_rule_pack_rules_participate_in_real_scans(tmp_path: Path) -> None:
    pack = _write(
        tmp_path,
        "acme.toml",
        """
        [[rule]]
        id = "ACME060"
        slug = "acme-unprefixed-tool"
        severity = "error"
        category = "contract"
        summary = "Tools must use the acme_ namespace."
        remediation = "Rename the tool with an acme_ prefix."
        message = "Tool '{tool}' is not namespaced."
          [rule.match]
          name_regex = "^acme_"
        """,
    )
    # The pack matches acme_* tools, which the vulnerable example does not contain,
    # so loading it must not change the baseline finding set of that file.
    before = scan_paths([EXAMPLES / "vulnerable_server.py"])
    load_rule_pack(str(pack))
    after = scan_paths([EXAMPLES / "vulnerable_server.py"])

    assert {d.rule_id for d in before.diagnostics} == {d.rule_id for d in after.diagnostics}
    assert "ACME060" in {r.rule_id for r in get_all_rules()}
