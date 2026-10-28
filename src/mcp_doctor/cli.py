"""Command-line interface for vexar-mcp.

Exit codes are part of the public contract because CI gates depend on them:

* ``0`` - the scan finished and every gate passed
* ``1`` - the scan finished but a gate failed (severity, score floor, or baseline delta)
* ``2`` - usage, configuration, I/O, or live-server error; nothing was gated
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from mcp_doctor import __version__
from mcp_doctor.baseline import (
    BaselineDelta,
    BaselineError,
    build_baseline,
    compare,
    read_baseline,
    write_baseline,
)
from mcp_doctor.config import describe_config, discover_config, load_config
from mcp_doctor.engine import scan_paths, scan_server
from mcp_doctor.live import LiveAuditError
from mcp_doctor.models import Diagnostic, ScanConfig, ScanReport
from mcp_doctor.policies import PolicyViolation, apply_policy, list_policies, resolve_policy
from mcp_doctor.reporters import (
    render_github_annotations,
    render_html,
    render_json,
    render_markdown,
    render_rules_table,
    render_sarif,
    render_terminal,
    render_token_report,
)
from mcp_doctor.rules import get_all_rules, get_rule_catalog
from mcp_doctor.rules.plugin import RulePackError, load_rule_packs

_FORMATS = ("terminal", "json", "sarif", "markdown", "github", "html")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vexar-mcp",
        description=(
            "Security, schema and token-cost analyzer for MCP servers. "
            "Static scans by default; live stdio introspection with `audit-server`."
        ),
    )
    parser.add_argument("--version", action="version", version=f"vexar-mcp {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    # ---------------------------------------------------------------- scan
    scan_p = sub.add_parser("scan", help="Scan MCP server files, directories, or JSON manifests.")
    scan_p.add_argument("paths", nargs="*", default=["."], help="Files or directories to scan.")
    scan_p.add_argument(
        "--format",
        choices=_FORMATS,
        default="terminal",
        help=(
            "Output format (default: terminal). 'github' emits Actions workflow commands for "
            "inline PR annotations; 'html' writes a standalone dashboard."
        ),
    )
    scan_p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the report to this file instead of stdout (required-ish for --format html).",
    )
    scan_p.add_argument(
        "--policy",
        default=None,
        help="Enforcement preset: default, ci, strict-prod, enterprise-governance.",
    )
    scan_p.add_argument(
        "--fail-on",
        choices=("error", "warning", "info", "never"),
        default=None,
        help="Minimum severity that fails the run. Overrides the policy default.",
    )
    scan_p.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="Fail when the health score is below this value (0-100).",
    )
    scan_p.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Override maximum allowed estimated tokens per tool.",
    )
    scan_p.add_argument(
        "--ignore", default="", help="Comma-separated rule IDs to skip (e.g. MCP004,MCP008)."
    )
    scan_p.add_argument(
        "--config", type=Path, default=None, help="Explicit path to a `vexar-mcp.toml` file."
    )
    scan_p.add_argument(
        "--rule-pack",
        action="append",
        default=[],
        metavar="SPEC",
        help=(
            "Opt-in rule pack to load: a .toml/.json file, or a trusted Python module path. "
            "Repeatable."
        ),
    )
    scan_p.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Compare against a committed baseline and gate only on new findings.",
    )
    scan_p.add_argument(
        "--write-baseline",
        type=Path,
        default=None,
        metavar="FILE",
        help="Record the current findings as the accepted baseline, then exit.",
    )
    scan_p.add_argument(
        "--history",
        type=Path,
        default=None,
        metavar="FILE",
        help="JSON array of previous health scores, used for the HTML trend sparkline.",
    )
    scan_p.add_argument(
        "--append-history",
        action="store_true",
        help="Append this run's score to the --history file after scanning.",
    )
    scan_p.add_argument(
        "--print-config",
        action="store_true",
        help="Print the effective configuration and exit without scanning.",
    )

    # ------------------------------------------------------- audit-server
    live_p = sub.add_parser(
        "audit-server",
        help="Introspect a running MCP server over stdio JSON-RPC and scan its tool catalog.",
        description=(
            "Launch an MCP server, complete the initialize handshake, call tools/list, and run "
            "the full rule set over the announced tools. Use `--` before the server command."
        ),
    )
    live_p.add_argument(
        "server_command",
        nargs=argparse.REMAINDER,
        help="Server command, e.g. -- python my_server.py",
    )
    live_p.add_argument("--format", choices=_FORMATS, default="terminal")
    live_p.add_argument("--output", type=Path, default=None)
    live_p.add_argument("--policy", default=None, help="Enforcement preset name.")
    live_p.add_argument("--fail-on", choices=("error", "warning", "info", "never"), default=None)
    live_p.add_argument("--min-score", type=int, default=None)
    live_p.add_argument("--ignore", default="")
    live_p.add_argument("--config", type=Path, default=None)
    live_p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Seconds to allow for the whole handshake and tools/list exchange (default 30).",
    )

    # ------------------------------------------------------------ baseline
    base_p = sub.add_parser("baseline", help="Create or inspect baseline snapshots.")
    base_sub = base_p.add_subparsers(dest="baseline_command", required=True)
    base_write = base_sub.add_parser(
        "write", help="Record current findings as the accepted baseline."
    )
    base_write.add_argument("paths", nargs="*", default=["."])
    base_write.add_argument(
        "--output", type=Path, default=Path(".vexar-baseline.json"), help="Baseline file to write."
    )
    base_write.add_argument("--config", type=Path, default=None)
    base_write.add_argument("--policy", default=None)
    base_write.add_argument("--ignore", default="")
    base_show = base_sub.add_parser("show", help="Summarize an existing baseline file.")
    base_show.add_argument("file", nargs="?", type=Path, default=Path(".vexar-baseline.json"))
    base_show.add_argument("--format", choices=("terminal", "json"), default="terminal")

    # -------------------------------------------------------------- policy
    policy_p = sub.add_parser("policy", help="List built-in enforcement presets.")
    policy_p.add_argument("--format", choices=("terminal", "json"), default="terminal")

    # --------------------------------------------------------------- rules
    rules_p = sub.add_parser("rules", help="List all registered diagnostic rules.")
    rules_p.add_argument("--format", choices=("terminal", "json"), default="terminal")

    # -------------------------------------------------------- token-report
    tok_p = sub.add_parser(
        "token-report", help="Display per-tool context-window token usage leaderboard."
    )
    tok_p.add_argument("paths", nargs="*", default=["."])
    tok_p.add_argument("--config", type=Path, default=None)

    return parser


def _resolve_config(args: argparse.Namespace) -> ScanConfig:
    """Build the effective configuration, loading rule packs before the final validation pass."""
    targets = [Path(p) for p in getattr(args, "paths", ["."])]
    extra_ignored = {x.strip() for x in getattr(args, "ignore", "").split(",") if x.strip()}
    packs = list(getattr(args, "rule_pack", []) or [])

    cfg = load_config(
        config_path=getattr(args, "config", None),
        start_dir=targets[0] if targets else Path.cwd(),
        max_tokens_override=getattr(args, "max_tokens", None),
        extra_ignored_rules=extra_ignored,
        policy_override=getattr(args, "policy", None),
        fail_on_override=getattr(args, "fail_on", None),
        min_score_override=getattr(args, "min_score", None),
        extra_rule_packs=packs,
        allow_unknown_rules=True,
    )

    load_rule_packs(cfg.rule_packs)

    preset = resolve_policy(cfg.policy_name)
    validated = apply_policy(preset, catalog=get_rule_catalog(), ignored_rules=cfg.ignored_rules)
    return replace(cfg, ignored_rules=validated)


def _load_history(path: Path) -> list[int]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read history file {path}: {exc}") from exc
    if not isinstance(raw, list):
        return []
    return [int(v) for v in raw if isinstance(v, (int, float))]


def _append_history(path: Path, score: int) -> None:
    history = _load_history(path)
    history.append(score)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history[-200:], indent=2) + "\n", encoding="utf-8")


def _emit(text: str, output: Path | None) -> None:
    if output is None:
        print(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")


def _render(
    report: ScanReport,
    fmt: str,
    *,
    delta: BaselineDelta | None = None,
    history: Sequence[int] | None = None,
) -> str:
    """Dispatch to the reporter for ``fmt``."""
    if fmt == "json":
        return render_json(report, delta)
    if fmt == "sarif":
        return render_sarif(report)
    if fmt == "markdown":
        return render_markdown(report, delta)
    if fmt == "github":
        return render_github_annotations(report)
    if fmt == "html":
        return render_html(report, delta, history=history)
    return render_terminal(report, delta)


def _gate_failures(
    report: ScanReport,
    config: ScanConfig,
    *,
    delta_new: Sequence[Diagnostic] | None,
) -> list[str]:
    """Return human-readable gate failure reasons; empty means the run passed."""
    # `--fail-on never` means "report only": it disables every gate, including the score
    # floor, so CI can generate SARIF/HTML artefacts without a second failing pass.
    if config.fail_on is None:
        return []

    reasons: list[str] = []
    findings: Sequence[Diagnostic] = delta_new if delta_new is not None else report.diagnostics

    threshold = config.fail_on
    breached = [d for d in findings if d.severity.rank >= threshold.rank]
    if breached:
        label = "new finding(s)" if delta_new is not None else "finding(s)"
        reasons.append(
            f"{len(breached)} {label} at or above '{threshold.value}' "
            f"(policy '{config.policy_name}')"
        )

    # A score floor is meaningless in delta mode: the backlog is accepted risk by design.
    if delta_new is None and report.summary.score < config.min_score:
        reasons.append(
            f"health score {report.summary.score} is below the required floor of {config.min_score} "
            f"(policy '{config.policy_name}')"
        )
    return reasons


def _summary_line(report: ScanReport, config: ScanConfig) -> str:
    s = report.summary
    return (
        f"vexar-mcp: score {s.score}/100 ({s.grade}), risk {s.risk_index}, "
        f"{s.errors} error(s), {s.warnings} warning(s), {s.infos} info, "
        f"policy '{config.policy_name}'"
    )


def _cmd_rules(args: argparse.Namespace) -> int:
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


def _cmd_policy(args: argparse.Namespace) -> int:
    presets = list_policies()
    if args.format == "json":
        print(
            json.dumps(
                [
                    {
                        "name": p.name,
                        "title": p.title,
                        "description": p.description,
                        "fail_on": p.fail_on.value,
                        "min_score": p.min_score,
                        "max_tool_tokens": p.max_tool_tokens,
                        "max_server_tokens": p.max_server_tokens,
                        "protected_categories": [c.value for c in p.protected_categories],
                    }
                    for p in presets
                ],
                indent=2,
            )
        )
        return 0
    lines = [f"{'PRESET':<24} {'FAIL-ON':<10} {'MIN':>4} {'TOK/TOOL':>9}  PROTECTED", "-" * 92]
    for p in presets:
        protected = ",".join(c.value for c in p.protected_categories) or "-"
        lines.append(
            f"{p.name:<24} {p.fail_on.value:<10} {p.min_score:>4} {p.max_tool_tokens:>9}  {protected}"
        )
    lines.append("")
    for p in presets:
        lines.append(f"{p.name}: {p.title}")
        lines.append(f"  {p.description}")
    print("\n".join(lines))
    return 0


def _cmd_baseline(args: argparse.Namespace) -> int:
    if args.baseline_command == "show":
        baseline = read_baseline(args.file)
        if args.format == "json":
            print(json.dumps({"schema": baseline.schema, "entries": len(baseline.entries)}))
            return 0
        print(f"Baseline: {args.file}")
        print(f"  schema      : {baseline.schema}")
        print(f"  created_at  : {baseline.created_at or 'unknown'}")
        print(f"  tool_version: {baseline.tool_version or 'unknown'}")
        print(f"  policy      : {baseline.policy or 'unknown'}")
        print(f"  entries     : {len(baseline.entries)}")
        return 0

    try:
        cfg = _resolve_config(args)
        report = scan_paths([Path(p) for p in args.paths], cfg)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"vexar-mcp: error: {exc}", file=sys.stderr)
        return 2
    baseline = build_baseline(report, policy=cfg.policy_name, tool_version=__version__)
    write_baseline(args.output, baseline)
    print(
        f"vexar-mcp: baseline written to {args.output} with {len(baseline.entries)} accepted "
        f"finding(s). Commit it and gate CI with --baseline {args.output}."
    )
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    try:
        cfg = _resolve_config(args)
    except (ValueError, RulePackError, OSError) as exc:
        print(f"vexar-mcp: error: {exc}", file=sys.stderr)
        return 2

    if args.print_config:
        payload = describe_config(cfg)
        source = discover_config(Path(args.paths[0]) if args.paths else Path.cwd())
        payload["config_file"] = str(source) if source else None
        print(json.dumps(payload, indent=2))
        return 0

    targets = [Path(p) for p in args.paths]
    try:
        report = scan_paths(targets, cfg)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"vexar-mcp: error: {exc}", file=sys.stderr)
        return 2

    delta = None
    if args.write_baseline is not None:
        baseline = build_baseline(report, policy=cfg.policy_name, tool_version=__version__)
        write_baseline(args.write_baseline, baseline)
        print(
            f"vexar-mcp: baseline written to {args.write_baseline} with "
            f"{len(baseline.entries)} accepted finding(s).",
            file=sys.stderr,
        )
        return 0

    if args.baseline is not None:
        try:
            delta = compare(read_baseline(args.baseline), report)
        except BaselineError as exc:
            print(f"vexar-mcp: error: {exc}", file=sys.stderr)
            return 2

    history: list[int] = []
    if args.history is not None:
        try:
            history = _load_history(args.history)
        except ValueError as exc:
            print(f"vexar-mcp: error: {exc}", file=sys.stderr)
            return 2
        history.append(report.summary.score)

    text = _render(report, args.format, delta=delta, history=history)
    _emit(text, args.output)

    if args.append_history and args.history is not None:
        _append_history(args.history, report.summary.score)

    failures = _gate_failures(
        report, cfg, delta_new=delta.new_findings if delta is not None else None
    )
    if args.format not in ("json", "sarif", "html", "github"):
        print(_summary_line(report, cfg), file=sys.stderr)
    for reason in failures:
        print(f"vexar-mcp: gate failed: {reason}", file=sys.stderr)
    return 1 if failures else 0


def _cmd_audit_server(args: argparse.Namespace) -> int:
    command = [part for part in args.server_command if part != "--"]
    if not command:
        print(
            "vexar-mcp: error: no server command given. "
            "Usage: vexar-mcp audit-server -- python my_server.py",
            file=sys.stderr,
        )
        return 2

    try:
        cfg = _resolve_config(args)
    except (ValueError, RulePackError, OSError) as exc:
        print(f"vexar-mcp: error: {exc}", file=sys.stderr)
        return 2

    try:
        report = scan_server(command, cfg, timeout=args.timeout)
    except LiveAuditError as exc:
        print(f"vexar-mcp: live audit failed: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"vexar-mcp: error: {exc}", file=sys.stderr)
        return 2

    _emit(_render(report, args.format), args.output)

    failures = _gate_failures(report, cfg, delta_new=None)
    if args.format not in ("json", "sarif", "html", "github"):
        print(_summary_line(report, cfg), file=sys.stderr)
    for reason in failures:
        print(f"vexar-mcp: gate failed: {reason}", file=sys.stderr)
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint returning POSIX exit status (0=pass, 1=gate failure, 2=error)."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "rules":
            return _cmd_rules(args)
        if args.command == "policy":
            return _cmd_policy(args)
        if args.command == "baseline":
            return _cmd_baseline(args)
        if args.command == "audit-server":
            return _cmd_audit_server(args)
        if args.command == "token-report":
            cfg = _resolve_config(args)
            report = scan_paths([Path(p) for p in args.paths], cfg)
            print(render_token_report(report))
            return 0
        return _cmd_scan(args)
    except (PolicyViolation, RulePackError, BaselineError) as exc:
        print(f"vexar-mcp: error: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"vexar-mcp: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
