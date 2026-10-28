"""GitHub Actions workflow-command reporter for inline Pull Request annotations.

Emits ``::error``, ``::warning`` and ``::notice`` workflow commands so findings
are rendered directly on the changed lines of a Pull Request diff. Unlike the
SARIF reporter this needs neither GitHub Advanced Security nor a Code Scanning
upload, which makes it usable in any public or private repository.

Reference: https://docs.github.com/en/actions/reference/workflow-commands-for-github-actions
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from mcp_doctor.models import Diagnostic, ScanReport, Severity
from mcp_doctor.sanitize import neutralize_workflow_command

_ANNOTATION_LEVEL = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "notice",
}


def _escape_data(value: str) -> str:
    """Escape workflow-command message data per the Actions specification.

    Percent signs are replaced first; otherwise the escapes injected for carriage
    returns and newlines would themselves be escaped into literal ``%250D`` text.

    Args:
        value: Raw message text that may contain ``%``, ``\\r`` or ``\\n``.

    Returns:
        Escaped text safe to place after the closing ``::`` of a command.
    """
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    """Escape a workflow-command property value.

    Property values use the stricter escape set: along with ``%``, ``\\r`` and
    ``\\n`` the characters ``:`` and ``,`` must be escaped because they delimit
    the ``key=value`` pairs inside the command header.

    Args:
        value: Raw property text.

    Returns:
        Escaped text safe to place inside a ``::cmd key=value::`` header.
    """
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")


def _normalize_source_file(source_file: str, root: Path) -> str:
    """Normalize a source path into the repository-relative form GitHub expects.

    GitHub only annotates files that live inside the checkout, so absolute paths
    are rebased onto ``root`` (the checkout directory during a workflow run) and
    Windows separators become POSIX separators.

    Args:
        source_file: Path exactly as recorded on the diagnostic.
        root: Repository root used to rebase absolute paths.

    Returns:
        Path with forward slashes and no leading current-directory marker.
    """
    path = Path(source_file.replace("\\", "/"))
    if path.is_absolute():
        # Outside the checkout: keep the absolute path rather than invent one.
        with contextlib.suppress(ValueError):
            path = path.relative_to(root.resolve())
    # ``Path`` already collapses ``./`` prefixes, so a single ``as_posix`` call
    # yields the ``src/server.py`` form GitHub matches against the diff.
    return path.as_posix()


def _compose_message(diagnostic: Diagnostic) -> str:
    """Build the human-readable annotation body for one diagnostic.

    Args:
        diagnostic: Finding to describe.

    Returns:
        ``[tool] message`` plus an optional ``Fix: remediation`` suffix.
    """
    # Untrusted text (a tool name can come from a remote server during a live audit) must not
    # carry ANSI escapes into a CI log, nor start a workflow command of its own.
    message = f"[{neutralize_workflow_command(diagnostic.tool_name)}] "
    message += neutralize_workflow_command(diagnostic.message)
    if diagnostic.remediation:
        message = f"{message} Fix: {neutralize_workflow_command(diagnostic.remediation)}"
    return message


def render_annotation(diagnostic: Diagnostic, *, root: Path | None = None) -> str:
    """Render a single diagnostic as one GitHub Actions workflow command.

    Args:
        diagnostic: Finding to annotate.
        root: Repository root used to rebase absolute source paths. Defaults to
            the current working directory, which is the checkout root in Actions.

    Returns:
        A line such as
        ``::error file=server.py,line=24,title=MCP005 (dangerous-shell-execution)::[run] ...``
        where an ``ERROR`` maps to ``::error``, a ``WARNING`` to ``::warning`` and
        an ``INFO`` to ``::notice``.
    """
    level = _ANNOTATION_LEVEL[diagnostic.severity]
    effective_root = root if root is not None else Path.cwd()
    properties = (
        (
            "file",
            _normalize_source_file(
                neutralize_workflow_command(diagnostic.source_file), effective_root
            ),
        ),
        ("line", str(max(1, diagnostic.line))),
        ("title", f"{diagnostic.rule_id} ({diagnostic.rule_slug})"),
    )
    header = ",".join(f"{key}={_escape_property(value)}" for key, value in properties)
    return f"::{level} {header}::{_escape_data(_compose_message(diagnostic))}"


def render_github_annotations(report: ScanReport, *, root: Path | None = None) -> str:
    """Serialize a scan report as GitHub Actions workflow commands.

    One annotation is emitted per diagnostic, in the order the engine reported
    them. A clean scan yields an empty string, so piping the output into a log
    adds nothing when there are no findings.

    Args:
        report: Completed scan report.
        root: Repository root used to rebase absolute source paths. Defaults to
            the current working directory.

    Returns:
        Newline-joined workflow commands, or ``""`` when there are no findings.
    """
    effective_root = root if root is not None else Path.cwd()
    return "\n".join(
        render_annotation(diagnostic, root=effective_root) for diagnostic in report.diagnostics
    )
