"""Security static-analysis rules (MCP005-MCP007, MCP009, MCP010, MCP013)."""

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
    rule_id="MCP010",
    slug="missing-network-timeout",
    severity=Severity.WARNING,
    category=Category.SECURITY,
    summary="Outbound HTTP call inside tool handler omits explicit `timeout=` (or passes `timeout=None`), risking indefinite hangs.",
    remediation="Add `timeout=10` (or appropriate value) to all `requests.get/post`, `httpx.*`, and `urllib.request.urlopen` calls.",
)
def check_network_timeouts(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    del config
    out: list[Diagnostic] = []
    for tool in tools:
        for sink in tool.sinks:
            if sink.kind == "ssrf" and sink.timeout_missing:
                param_note = f" with parameter '{sink.uses_param}'" if sink.uses_param else ""
                out.append(
                    Diagnostic(
                        rule_id="MCP010",
                        rule_slug="missing-network-timeout",
                        severity=Severity.WARNING,
                        category=Category.SECURITY,
                        message=(
                            f"Tool '{tool.name}' performs HTTP sink `{sink.call_name}`{param_note} "
                            "without an explicit `timeout=` argument."
                        ),
                        source_file=tool.source_file,
                        line=sink.line,
                        tool_name=tool.name,
                        remediation="Pass `timeout=<seconds>` to the HTTP call (e.g., `requests.get(url, timeout=10)`).",
                    )
                )
    return out


@register_rule(
    rule_id="MCP013",
    slug="unsafe-deserialization",
    severity=Severity.ERROR,
    category=Category.SECURITY,
    summary="Tool handler invokes unsafe deserialization such as pickle.loads or yaml.load without SafeLoader.",
    remediation="Use JSON for untrusted payloads, or yaml.safe_load / yaml.SafeLoader for YAML; never unpickle untrusted data.",
)
def check_unsafe_deserialization(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    """Report deserializers that can construct unsafe objects from tool input.

    Args:
        tools: Statically extracted tool definitions and their sensitive calls.
        config: Scan configuration; rule suppression is applied by the engine.

    Returns:
        Error diagnostics for each unsafe deserialization sink.
    """
    del config
    return [
        Diagnostic(
            rule_id="MCP013",
            rule_slug="unsafe-deserialization",
            severity=Severity.ERROR,
            category=Category.SECURITY,
            message=f"Tool '{tool.name}' invokes unsafe deserialization `{sink.call_name}`.",
            source_file=tool.source_file,
            line=sink.line,
            tool_name=tool.name,
            remediation="Prefer JSON or yaml.safe_load for untrusted data; avoid pickle, marshal and shelve.",
        )
        for tool in tools
        for sink in tool.sinks
        if sink.kind == "deserialization"
    ]


@register_rule(
    rule_id="MCP009",
    slug="hardcoded-secret-in-tool",
    severity=Severity.ERROR,
    category=Category.SECURITY,
    summary="Tool handler embeds a hardcoded API key, token, or bearer secret in a string literal.",
    remediation="Load credentials from `os.environ` or a secret manager at runtime; rotate any exposed key.",
)
def check_hardcoded_secrets(tools: Sequence[ToolSpec], config: ScanConfig) -> list[Diagnostic]:
    """Report high-confidence credential literals collected from Python tool bodies.

    Args:
        tools: Statically extracted tool definitions and their secret literals.
        config: Scan configuration; rule suppression is applied by the engine.

    Returns:
        Error diagnostics for every hardcoded credential found in a tool handler.
    """
    del config
    # ToolSpec.secret_literals stores pattern/target metadata only. Never echo the
    # raw value here: diagnostic text is serialized to JSON, Markdown and SARIF.
    return [
        Diagnostic(
            rule_id="MCP009",
            rule_slug="hardcoded-secret-in-tool",
            severity=Severity.ERROR,
            category=Category.SECURITY,
            message=(
                f"Tool '{tool.name}' hardcodes a credential ({literal.pattern})"
                + (f" in `{literal.target}`" if literal.target else "")
                + f" on line {literal.line}."
            ),
            source_file=tool.source_file,
            line=literal.line,
            tool_name=tool.name,
            remediation="Read the value from `os.environ` / a secret manager and rotate the exposed key.",
        )
        for tool in tools
        for literal in tool.secret_literals
    ]
