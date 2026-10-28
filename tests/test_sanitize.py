"""Output-sink hardening: untrusted text must not become report structure.

The threat here is *content spoofing*, not remote code execution, and the tests say so
plainly. Vexar-MCP reads code it did not write and, in a live audit, names served by a remote
server. Those strings reach a terminal, a PR comment, an HTML dashboard and a CI log. If a
hostile string can erase the line above it or open a heading, the report can be made to say
something the scanner never found — which for a security tool is a failure of the product.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_doctor.models import (
    Category,
    Diagnostic,
    IssueKind,
    ParameterSpec,
    ParseIssue,
    ScanCoverage,
    ScanReport,
    ScanStatus,
    ScanSummary,
    Severity,
    ToolSpec,
)
from mcp_doctor.reporters import (
    render_github_annotations,
    render_html,
    render_json,
    render_markdown,
    render_sarif,
    render_terminal,
)
from mcp_doctor.sanitize import (
    neutralize_markdown,
    neutralize_workflow_command,
    sanitize_for_sink,
    strip_control_chars,
)

ESC = "\x1b"
BELL = "\x07"
#: A realistic hostile payload: erase the line, fake a colour, then break out of any structure
#: the report might rely on.
PAYLOAD = (
    f"{ESC}[2K{ESC}[31mCLEAN\x1b[0m{BELL}|pipe|"
    "</td></tr><script>alert(1)</script>\n\n```\n## Heading\n::error::pwned\n"
    "[link](https://evil.example)"
)


def _report() -> ScanReport:
    tool = ToolSpec(
        name=PAYLOAD,
        description=PAYLOAD,
        source_file=PAYLOAD,
        line=1,
        parameters=(ParameterSpec(name=PAYLOAD, type_name="Any"),),
        estimated_tokens=10,
    )
    diag = Diagnostic(
        rule_id="MCP001",
        rule_slug="missing-tool-description",
        severity=Severity.ERROR,
        category=Category.CONTRACT,
        message=PAYLOAD,
        source_file=PAYLOAD,
        line=1,
        tool_name=PAYLOAD,
        remediation=PAYLOAD,
    )
    return ScanReport(
        tools=(tool,),
        diagnostics=(diag,),
        summary=ScanSummary(
            files_scanned=1,
            tools_scanned=1,
            total_estimated_tokens=10,
            errors=1,
            warnings=0,
            infos=0,
            score=10,
            grade="F",
        ),
        policy_name="default",
        status=ScanStatus.FINDINGS,
        coverage=ScanCoverage(candidate_files=1, files_analyzed=1),
        parse_issues=(
            ParseIssue(kind=IssueKind.SYNTAX_ERROR, source_file=PAYLOAD, line=3, message=PAYLOAD),
        ),
    )


class TestStripControlChars:
    @pytest.mark.parametrize(
        "raw",
        [
            f"{ESC}[2K",
            f"{ESC}]8;;https://evil.example{ESC}\\",
            f"bell{BELL}",
            "zero\u200bwidth",
            "bidi\u202eoverride",
            "nul\x00byte",
        ],
    )
    def test_invisible_characters_are_removed(self, raw: str) -> None:
        cleaned = strip_control_chars(raw)
        assert not any(ch in cleaned for ch in (ESC, BELL, "\u200b", "\u202e", "\x00"))

    def test_legitimate_unicode_survives(self) -> None:
        text = "Fetch the user's 請求書 — safely ✅ (line 2)"
        assert strip_control_chars(text) == text

    def test_newlines_are_kept_for_multiline_reports_but_dropped_for_one_liners(self) -> None:
        assert "\n" in strip_control_chars("a\nb")
        assert "\n" not in strip_control_chars("a\nb", keep_newlines=False)

    def test_crlf_is_normalised(self) -> None:
        assert strip_control_chars("a\r\nb") == "a\nb"

    def test_empty_input(self) -> None:
        assert strip_control_chars("") == ""
        assert neutralize_markdown("") == ""
        assert neutralize_workflow_command("") == ""


class TestMarkdownNeutralisation:
    def test_fences_cannot_open_a_block(self) -> None:
        out = neutralize_markdown("before\n```\n# heading\n```")
        assert "\n```" not in out

    def test_headings_cannot_start_a_line(self) -> None:
        out = neutralize_markdown("text\n## Injected heading")
        for line in out.splitlines():
            assert not line.lstrip().startswith("#"), line

    def test_workflow_commands_cannot_start_a_line(self) -> None:
        out = neutralize_markdown("x\n::error::injected")
        assert not any(line.startswith("::") for line in out.splitlines())

    def test_inline_text_is_left_readable(self) -> None:
        text = "Pass `timeout=10` to `requests.get` — see MCP010."
        assert neutralize_markdown(text) == text


class TestWorkflowCommandNeutralisation:
    def test_leading_double_colon_is_defused(self) -> None:
        assert not neutralize_workflow_command("::set-output name=x::y").startswith("::")

    def test_ansi_is_stripped(self) -> None:
        assert ESC not in neutralize_workflow_command(f"{ESC}[31mred{ESC}[0m")


class TestSinkSelection:
    def test_markdown_sink_neutralises_structure(self) -> None:
        assert "\n```" not in sanitize_for_sink("a\n```", sink="markdown")

    def test_json_sink_keeps_structure_but_drops_control_bytes(self) -> None:
        out = sanitize_for_sink(f"a{ESC}[2K\n## heading", sink="json")
        assert ESC not in out
        assert "## heading" in out, "JSON must stay faithful; its encoder handles escaping"


class TestReporters:
    """Each reporter is the last line of defence for its own sink."""

    @pytest.mark.parametrize(
        "render",
        [render_terminal, render_markdown, render_html, render_json, render_sarif],
    )
    def test_no_reporter_emits_raw_terminal_escapes(self, render) -> None:  # type: ignore[no-untyped-def]
        assert ESC not in render(_report()), "ANSI escape reached a report"

    def test_terminal_does_not_carry_bell_or_backspace(self) -> None:
        out = render_terminal(_report())
        assert BELL not in out
        assert "\x08" not in out

    def test_markdown_cannot_be_used_to_spoof_a_heading(self) -> None:
        out = render_markdown(_report())
        assert not any(line.startswith("## ") and "Heading" in line for line in out.splitlines())

    def test_github_annotations_are_single_line_and_escaped(self) -> None:
        out = render_github_annotations(_report(), root=Path("/tmp"))
        for line in out.splitlines():
            assert line.startswith("::"), "an annotation must start with its level"
            assert ESC not in line
            assert BELL not in line

    def test_html_is_escaped_not_stripped(self) -> None:
        out = render_html(_report())
        assert "<script>alert(1)</script>" not in out, "raw script tag reached the dashboard"
        assert "&lt;script&gt;" in out, "the payload should still be visible, escaped"

    def test_json_keeps_the_original_text_for_machine_consumers(self) -> None:
        out = render_json(_report())
        assert "pwned" in out or "evil.example" in out
        assert ESC not in out


class TestNothingRegressed:
    def test_ordinary_findings_are_unchanged(self) -> None:
        report = _report()
        clean_tool = ToolSpec(
            name="fetch_invoice",
            description="Fetch an invoice by id.",
            source_file="server.py",
            line=12,
        )
        clean_diag = Diagnostic(
            rule_id="MCP001",
            rule_slug="missing-tool-description",
            severity=Severity.ERROR,
            category=Category.CONTRACT,
            message="Tool 'fetch_invoice' has an empty description.",
            source_file="server.py",
            line=12,
            tool_name="fetch_invoice",
            remediation="Document the tool.",
        )
        from dataclasses import replace

        plain = replace(report, tools=(clean_tool,), diagnostics=(clean_diag,), parse_issues=())
        out = render_terminal(plain)
        assert "fetch_invoice" in out
        assert "Tool 'fetch_invoice' has an empty description." in out
        assert "Document the tool." in out
