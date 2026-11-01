"""Output reporters: terminal, JSON, SARIF v2.1.0, Markdown, HTML, and Actions annotations."""

from vexar_mcp.reporters.github_actions import render_github_annotations
from vexar_mcp.reporters.html_report import render_html
from vexar_mcp.reporters.json_reporter import render_json
from vexar_mcp.reporters.markdown import render_markdown
from vexar_mcp.reporters.sarif import render_sarif
from vexar_mcp.reporters.terminal import render_rules_table, render_terminal, render_token_report

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
