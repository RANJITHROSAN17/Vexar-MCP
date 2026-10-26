"""Command-line interface for vexar-mcp."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from mcp_doctor import __version__
from mcp_doctor.config import load_config
from mcp_doctor.engine import scan_paths
from mcp_doctor.models import Severity
from mcp_doctor.reporters import (
    render_json,
    render_markdown,
    render_rules_table,
    render_sarif,
    render_terminal,
    render_token_report,
)
from mcp_doctor.rules import get_all_rules


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-doctor",
        description="Static analyzer, security scanner, and token-budget auditor for MCP servers.",
    )
    parser.add_argument("--version", action="version", version=f"mcp-doctor {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_p = subparsers.add_parser(
        "scan", help="Scan MCP server files, directories, or JSON manifests."
    )
    scan_p.add_argument("paths", nargs="*", default=["."], help="Files or directories to scan.")
    scan_p.add_argument(
        "--format",
        choices=("terminal", "json", "sarif", "markdown"),
        default="terminal",
        help="Output format (default: terminal).",
    )
    scan_p.add_argument(
        "--fail-on",
        choices=("error", "warning", "info", "never"),
        default="error",
        help="Minimum severity triggering exit code 1 (default: error).",
    )
    scan_p.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Override maximum allowed estimated tokens per tool (default: 250).",
    )
    scan_p.add_argument(
        "--ignore",
        default="",
        help="Comma-separated rule IDs to skip (e.g. MCP004,MCP008).",
    )
    scan_p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Explicit path to `.mcp-doctor.toml` configuration file.",
    )

    tok_p = subparsers.add_parser(
        "token-report",
        help="Display per-tool context-window token usage leaderboard.",
    )
    tok_p.add_argument("paths", nargs="*", default=["."], help="Files or directories to analyze.")
    tok_p.add_argument("--config", type=Path, default=None, help="Optional config file path.")

    rules_p = subparsers.add_parser("rules", help="List all built-in diagnostic rules.")
    rules_p.add_argument(
        "--format",
        choices=("terminal", "json"),
        default="terminal",
        help="Output format (default: terminal).",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint returning POSIX exit status (0=pass, 1=findings, 2=usage/io error)."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "rules":
        rules = get_all_rules()
        if args.format == "json":
            print(
                json.dumps(
                    [
                        {
                            "rule_id": r.rule_id,
                            "slug": r.slug,
                            "severity": r.severity.value,
                            "category": r.category.value,
                            "summary": r.summary,
                            "remediation": r.remediation,
                        }
                        for r in rules
                    ],
                    indent=2,
                )
            )
        else:
            print(render_rules_table(rules))
        return 0

    targets = [Path(p) for p in args.paths]
    extra_ignored = {x.strip() for x in getattr(args, "ignore", "").split(",") if x.strip()}
    try:
        cfg = load_config(
            config_path=args.config,
            start_dir=targets[0] if targets else Path.cwd(),
            max_tokens_override=getattr(args, "max_tokens", None),
            extra_ignored_rules=extra_ignored,
        )
        report = scan_paths(targets, cfg)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"mcp-doctor: error: {exc}", file=sys.stderr)
        return 2

    if args.command == "token-report":
        print(render_token_report(report))
        return 0

    if args.format == "json":
        print(render_json(report))
    elif args.format == "sarif":
        print(render_sarif(report))
    elif args.format == "markdown":
        print(render_markdown(report))
    else:
        print(render_terminal(report))

    if args.fail_on == "never":
        return 0

    threshold = Severity(args.fail_on)
    should_fail = any(d.severity.rank >= threshold.rank for d in report.diagnostics)
    return 1 if should_fail else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
