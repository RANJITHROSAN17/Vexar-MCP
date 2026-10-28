"""Output reporters: terminal, JSON, SARIF v2.1.0, Markdown, HTML, and Actions annotations."""

from mcp_doctor.reporters.github_actions import render_github_annotations
from mcp_doctor.reporters.html_report import render_html
from mcp_doctor.reporters.json_reporter import render_json
from mcp_doctor.reporters.markdown import render_markdown
from mcp_doctor.reporters.sarif import render_sarif
from mcp_doctor.reporters.terminal import render_rules_table, render_terminal, render_token_report

__all__ = [
    "render_github_annotations",
    "render_html",
    "render_json",
    "render_markdown",
    "render_rules_table",
    "render_sarif",
    "render_terminal",
    "render_token_report",
]
