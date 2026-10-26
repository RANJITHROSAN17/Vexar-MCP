"""Security static-analysis rules for MCP server tool handlers (MCP005, MCP006, MCP007)."""

from __future__ import annotations

from collections.abc import Sequence

from mcp_doctor.models import Category, Diagnostic, ScanConfig, Severity, ToolSpec
from mcp_doctor.rules.base import register_rule


@register_rule(
    rule_id="MCP005",
    slug="dangerous-shell-execution",
    severity=Severity.ERROR,
    category=Category.SECURITY,
    summary="Tool handler invokes shell execution or dynamic code evaluation (`shell=True`, `os.system`, `eval`).",
    remediation="Pass argument lists with `shell=False` and avoid `os.system` / `eval` / `exec` inside MCP tools.",
)
def check_shell_execution(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    del config
    out: list[Diagnostic] = []
    for tool in tools:
        for sink in tool.sinks:
            if sink.kind == "shell":
                param_note = f" with parameter '{sink.uses_param}'" if sink.uses_param else ""
                out.append(
                    Diagnostic(
                        rule_id="MCP005",
                        rule_slug="dangerous-shell-execution",
                        severity=Severity.ERROR,
                        category=Category.SECURITY,
                        message=(
                            f"Tool '{tool.name}' invokes dangerous execution sink `{sink.call_name}`{param_note}."
                        ),
                        source_file=tool.source_file,
                        line=sink.line,
                        tool_name=tool.name,
                        remediation="Use `subprocess.run([cmd, arg], shell=False, check=True)` with validated arguments.",
                    )
                )
    return out


@register_rule(
    rule_id="MCP006",
    slug="unvalidated-ssrf-sink",
    severity=Severity.WARNING,
    category=Category.SECURITY,
    summary="Tool performs outbound HTTP requests using tool input without scheme/host allowlist validation.",
    remediation="Parse URLs via `urllib.parse.urlparse`, enforce `https`, block private/link-local IPs, and check an allowlist.",
)
def check_ssrf_sinks(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    del config
    out: list[Diagnostic] = []
    for tool in tools:
        for sink in tool.sinks:
            if sink.kind == "ssrf" and not sink.validated:
                out.append(
                    Diagnostic(
                        rule_id="MCP006",
                        rule_slug="unvalidated-ssrf-sink",
                        severity=Severity.WARNING,
                        category=Category.SECURITY,
                        message=(
                            f"Tool '{tool.name}' passes parameter '{sink.uses_param}' into outbound request "
                            f"`{sink.call_name}` without visible URL/host validation (potential SSRF)."
                        ),
                        source_file=tool.source_file,
                        line=sink.line,
                        tool_name=tool.name,
                        remediation="Validate scheme/hostname with `urlparse` and reject RFC1918 / metadata IPs (`169.254.169.254`).",
                    )
                )
    return out


@register_rule(
    rule_id="MCP007",
    slug="unconfined-filesystem-access",
    severity=Severity.WARNING,
    category=Category.SECURITY,
    summary="Tool reads, writes, or deletes filesystem paths derived from user input without base-dir confinement.",
    remediation="Resolve paths with `Path(user_path).resolve()` and verify `resolved.is_relative_to(BASE_DIR)`.",
)
def check_filesystem_sinks(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    del config
    out: list[Diagnostic] = []
    for tool in tools:
        for sink in tool.sinks:
            if sink.kind == "filesystem" and not sink.validated:
                out.append(
                    Diagnostic(
                        rule_id="MCP007",
                        rule_slug="unconfined-filesystem-access",
                        severity=Severity.WARNING,
                        category=Category.SECURITY,
                        message=(
                            f"Tool '{tool.name}' passes parameter '{sink.uses_param}' to `{sink.call_name}` "
                            "without `.resolve()` / base-directory confinement (path traversal risk)."
                        ),
                        source_file=tool.source_file,
                        line=sink.line,
                        tool_name=tool.name,
                        remediation="Canonicalize paths via `.resolve()` and enforce `.is_relative_to(ALLOWED_ROOT)`.",
                    )
                )
    return out

@register_rule(
    rule_id="MCP013",
    slug="unsafe-deserialization",
    severity=Severity.ERROR,
    category=Category.SECURITY,
    summary="Tool handler deserializes untrusted data using pickle, marshal, shelve, or unsafe yaml.load.",
    remediation="Avoid pickle/marshal/shelve on untrusted input. Use yaml.safe_load or yaml.load(..., Loader=yaml.SafeLoader).",
)
def check_unsafe_deserialization(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    del config
    out: list[Diagnostic] = []
    for tool in tools:
        for sink in tool.sinks:
            if sink.kind == "deserialization":
                param_note = f" with parameter '{sink.uses_param}'" if sink.uses_param else ""
                out.append(
                    Diagnostic(
                        rule_id="MCP013",
                        rule_slug="unsafe-deserialization",
                        severity=Severity.ERROR,
                        category=Category.SECURITY,
                        message=(
                            f"Tool '{tool.name}' performs unsafe deserialization via `{sink.call_name}`{param_note}."
                        ),
                        source_file=tool.source_file,
                        line=sink.line,
                        tool_name=tool.name,
                        remediation="Use safe alternatives such as `yaml.safe_load` or validated JSON. Never use pickle on untrusted data.",
                    )
                )
    return out
