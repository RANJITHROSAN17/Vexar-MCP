"""Registry files: auditing a fleet of MCP servers in one run.

A team does not run one MCP server. It runs six, and they are described in one client
config file — Claude Desktop, Cursor, or a checked-in registry. Auditing them one command
at a time is the difference between a tool used daily and a tool used once, so this module
reads that file and audits every server in it.

The interesting decisions are all about what happens when something goes wrong.

**A registry is a list of commands to execute.** It is data that names programs, which
makes it a code path wearing a config file's clothes: pointing this at an untrusted
document runs whatever it names. The schema is therefore validated *fail-closed* — an
unknown shape, a non-string argv element, a duplicate name, or an empty server list is a
hard error rather than a guess. Nothing here decides to run something the file did not
clearly ask for, and `--dry-run` exists so you can see the plan without executing it.

**One broken server must not cost you the other five.** Each audit is isolated: a server
that cannot start, hangs, or speaks something other than JSON-RPC is recorded as a
failure and the run continues. The result is a :class:`FleetReport` that distinguishes
three outcomes — audited, failed, skipped — and a fleet that has any of the last two is
*incomplete*, which is reported as such rather than averaged away.

**The fleet score is the worst server, not the average.** Four clean servers and one
vulnerable one average to a comfortable B, and the B is the only number anyone reads.
Reporting the minimum means the headline cannot be improved by adding healthy servers,
which is the property that makes it worth printing at all. The mean is still available
in the JSON payload, labelled, for anyone who wants it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_doctor.engine import scan_server
from mcp_doctor.live import LiveAuditError
from mcp_doctor.models import ScanConfig, ScanReport, ScanStatus

#: Registry schema revisions this build understands.
SUPPORTED_REGISTRY_VERSIONS = frozenset({1})
DEFAULT_REGISTRY_VERSION = 1

#: Key that marks a native registry file (a list of servers).
NATIVE_KEY = "servers"
#: Key that marks a Claude Desktop / Cursor style client configuration.
CLIENT_KEY = "mcpServers"

#: Transports this client can actually speak. Anything else is recorded as skipped
#: rather than silently dropped, because "we did not audit it" is not "it is fine".
SUPPORTED_TRANSPORT = "stdio"
UNSUPPORTED_TRANSPORT_NAMES = frozenset({"sse", "http", "streamable-http", "streamable", "ws"})

OUTCOME_AUDITED = "audited"
OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED = "skipped"


class RegistryError(ValueError):
    """A registry file is missing, malformed, or asks for something unsafe to guess at."""


@dataclass(frozen=True)
class RegistryServer:
    """One server entry from a registry file, resolved into something launchable."""

    name: str
    command: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    timeout: float | None = None
    cwd: str | None = None
    #: Set when the entry is well-formed but cannot be audited over stdio, e.g. a remote
    #: SSE server. Such an entry is reported as skipped, never as clean.
    unsupported_reason: str | None = None

    @property
    def auditable(self) -> bool:
        """True when this entry can be launched and introspected."""
        return self.unsupported_reason is None and bool(self.command)


@dataclass(frozen=True)
class Registry:
    """A validated registry file."""

    servers: tuple[RegistryServer, ...]
    source_format: str
    path: str

    @property
    def auditable(self) -> tuple[RegistryServer, ...]:
        """Entries that can be launched and introspected."""
        return tuple(server for server in self.servers if server.auditable)


@dataclass(frozen=True)
class ServerAudit:
    """The outcome of auditing one server in a fleet."""

    name: str
    outcome: str
    command: tuple[str, ...] = ()
    report: ScanReport | None = None
    error: str | None = None
    duration_ms: int = 0

    @property
    def score(self) -> int | None:
        """Health score, or ``None`` when this server produced no grade."""
        return None if self.report is None else self.report.summary.score


@dataclass(frozen=True)
class FleetReport:
    """Combined result for every server in a registry.

    ``score`` is deliberately the **minimum** across audited servers. See the module
    docstring: an average lets one vulnerable server hide behind four healthy ones.
    """

    audits: tuple[ServerAudit, ...]
    registry_path: str
    source_format: str
    policy_name: str = "default"

    @property
    def audited(self) -> tuple[ServerAudit, ...]:
        """Servers that were launched and graded."""
        return tuple(a for a in self.audits if a.outcome == OUTCOME_AUDITED)

    @property
    def failed(self) -> tuple[ServerAudit, ...]:
        """Servers that could not be audited: launch failure, timeout, or protocol error."""
        return tuple(a for a in self.audits if a.outcome == OUTCOME_FAILED)

    @property
    def skipped(self) -> tuple[ServerAudit, ...]:
        """Entries that were never attempted because the transport is out of scope."""
        return tuple(a for a in self.audits if a.outcome == OUTCOME_SKIPPED)

    @property
    def complete(self) -> bool:
        """True only when every server in the registry was actually audited."""
        return not self.failed and not self.skipped

    @property
    def graded(self) -> tuple[ServerAudit, ...]:
        """Audited servers that produced a score; an ungraded server cannot set the floor."""
        return tuple(a for a in self.audited if a.score is not None)

    @property
    def score(self) -> int | None:
        """Fleet health score: the worst audited server, or ``None`` if nothing was graded."""
        scores = [a.score for a in self.graded if a.score is not None]
        return min(scores) if scores else None

    @property
    def grade(self) -> str:
        """Grade of the worst-graded server, so the letter matches the number beside it."""
        worst = self.worst_server
        if worst is None or worst.report is None:
            return "N/A"
        return worst.report.summary.grade

    @property
    def worst_server(self) -> ServerAudit | None:
        """The audited server that set the fleet score."""
        graded = self.graded
        if not graded:
            return None
        return min(graded, key=lambda a: (a.score if a.score is not None else 0, a.name))

    @property
    def mean_score(self) -> int | None:
        """Arithmetic mean across graded servers. Reported for context, never as the headline."""
        scores = [a.score for a in self.graded if a.score is not None]
        return round(sum(scores) / len(scores)) if scores else None

    @property
    def risk_index(self) -> int:
        """Highest risk index in the fleet; risk cannot be averaged into safety either."""
        indices = [a.report.summary.risk_index for a in self.audited if a.report is not None]
        return max(indices) if indices else 0

    @property
    def tools(self) -> int:
        """Total tools exposed across every audited server."""
        return sum(a.report.summary.tools_scanned for a in self.audited if a.report is not None)

    @property
    def diagnostics(self) -> tuple[tuple[str, Any], ...]:
        """``(server name, diagnostic)`` pairs across the fleet, for gate evaluation."""
        pairs: list[tuple[str, Any]] = []
        for audit in self.audited:
            if audit.report is None:
                continue
            pairs.extend((audit.name, diagnostic) for diagnostic in audit.report.diagnostics)
        return tuple(pairs)

    @property
    def counts(self) -> tuple[int, int, int]:
        """``(errors, warnings, infos)`` summed across audited servers."""
        errors = warnings = infos = 0
        for audit in self.audited:
            if audit.report is None:
                continue
            summary = audit.report.summary
            errors += summary.errors
            warnings += summary.warnings
            infos += summary.infos
        return errors, warnings, infos

    @property
    def status(self) -> ScanStatus:
        """Fleet status, using the same vocabulary as a single scan.

        A fleet with a failed or skipped member is ``PARTIAL`` even when every server we
        did audit is spotless: the honest answer to "is my fleet clean?" is "we did not
        look at all of it".
        """
        if not self.audited:
            return ScanStatus.ERROR
        if not self.complete:
            return ScanStatus.PARTIAL
        if not any(a.report.summary.tools_scanned for a in self.audited if a.report is not None):
            return ScanStatus.NO_MATCHES
        if any(a.report.diagnostics for a in self.audited if a.report is not None):
            return ScanStatus.FINDINGS
        return ScanStatus.CLEAN


# --------------------------------------------------------------------------- loading


def _fail(message: str) -> RegistryError:
    return RegistryError(message)


def _as_str(value: object, *, where: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{where}: '{field_name}' must be a non-empty string")
    return value


def _argv(entry: Mapping[str, Any], *, where: str) -> tuple[str, ...]:
    """Resolve ``command``/``args`` into a literal argv list.

    A string command is accepted (that is the client-config shape) and split on whitespace
    with no shell expansion. A pre-split list is used as-is. Anything else is rejected:
    guessing at an argv is how a config typo becomes the wrong program being executed.
    """
    command = entry.get("command")
    raw_args = entry.get("args", [])
    if not isinstance(raw_args, list) or any(not isinstance(arg, str) for arg in raw_args):
        raise _fail(f"{where}: 'args' must be a list of strings")

    if isinstance(command, str):
        if not command.strip():
            raise _fail(f"{where}: 'command' must be a non-empty string")
        argv = [*command.split(), *raw_args]
    elif isinstance(command, list):
        if not command:
            raise _fail(f"{where}: 'command' list is empty")
        if any(not isinstance(part, str) or not part for part in command):
            raise _fail(f"{where}: every 'command' element must be a non-empty string")
        if raw_args:
            raise _fail(f"{where}: use either a 'command' list or 'command' + 'args', not both")
        argv = list(command)
    else:
        raise _fail(f"{where}: 'command' must be a string or a list of strings")

    if not argv:
        raise _fail(f"{where}: resolves to an empty command")
    return tuple(argv)


def _env(entry: Mapping[str, Any], *, where: str) -> dict[str, str]:
    raw = entry.get("env", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _fail(f"{where}: 'env' must be an object of string values")
    resolved: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise _fail(f"{where}: 'env' keys must be non-empty strings")
        # Fail closed: a JSON number here means the user believed it would be a port, not
        # a string "8080", and silently stringifying it hides a config mistake.
        if not isinstance(value, str):
            raise _fail(
                f"{where}: env var '{key}' must be a string "
                f'(got {type(value).__name__}; quote it as "{value}")'
            )
        resolved[key] = value
    return resolved


def _timeout(entry: Mapping[str, Any], *, where: str) -> float | None:
    raw = entry.get("timeout")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise _fail(f"{where}: 'timeout' must be a number of seconds")
    if raw <= 0:
        raise _fail(f"{where}: 'timeout' must be greater than zero")
    return float(raw)


def _unsupported_reason(entry: Mapping[str, Any]) -> str | None:
    """Describe why an entry cannot be audited over stdio, or ``None`` if it can."""
    declared = entry.get("type", entry.get("transport"))
    if isinstance(declared, str) and declared.lower() in UNSUPPORTED_TRANSPORT_NAMES:
        return f"transport '{declared}' is not supported by the stdio client"
    url = entry.get("url", entry.get("serverUrl"))
    if isinstance(url, str) and url.strip() and "command" not in entry:
        return "remote server (url only) — the stdio client cannot reach it"
    if "command" not in entry and isinstance(declared, str):
        return f"transport '{declared}' has no command to launch"
    return None


def _parse_server(name: str, entry: object, *, index: int) -> RegistryServer:
    where = f"server[{index}] '{name}'"
    if not isinstance(entry, dict):
        raise _fail(f"{where}: each server must be an object")

    reason = _unsupported_reason(entry)
    if reason is not None:
        # Still validated for shape, so a malformed *and* unsupported entry is reported as
        # malformed rather than quietly filed under "skipped".
        if "command" in entry:
            _argv(entry, where=where)
        return RegistryServer(name=name, unsupported_reason=reason)

    command = _argv(entry, where=where)
    cwd_raw = entry.get("cwd")
    if cwd_raw is not None and not isinstance(cwd_raw, str):
        raise _fail(f"{where}: 'cwd' must be a string path")
    return RegistryServer(
        name=name,
        command=command,
        env=_env(entry, where=where),
        timeout=_timeout(entry, where=where),
        cwd=cwd_raw,
    )


def _check_unique(names: Sequence[str]) -> None:
    seen: dict[str, str] = {}
    for name in names:
        lowered = name.lower()
        if lowered in seen:
            other = seen[lowered]
            if other == name:
                raise _fail(
                    f"duplicate server name '{name}'; names identify reports, so they must be unique"
                )
            raise _fail(
                f"server names '{other}' and '{name}' differ only by case; "
                "report tables cannot tell them apart"
            )
        seen[lowered] = name


def parse_registry(data: object, *, path: str = "<memory>") -> Registry:
    """Validate already-parsed registry data.

    Args:
        data: The decoded JSON document.
        path: Where it came from, used in error messages and the report.

    Returns:
        A validated :class:`Registry`.

    Raises:
        RegistryError: If the document is not a registry this build can read, or asks for
            something ambiguous. Every failure names the offending server or field.
    """
    if not isinstance(data, dict):
        raise _fail(f"{path}: a registry must be a JSON object, got {type(data).__name__}")

    has_client = CLIENT_KEY in data
    has_native = NATIVE_KEY in data
    if has_client and has_native:
        raise _fail(
            f"{path}: contains both '{NATIVE_KEY}' and '{CLIENT_KEY}'; "
            "refusing to guess which one to audit (remove one)"
        )
    if not has_client and not has_native:
        found = ", ".join(sorted(str(key) for key in data)) or "no keys"
        raise _fail(
            f"{path}: not a registry file — expected a '{NATIVE_KEY}' list or a "
            f"'{CLIENT_KEY}' object, found: {found}"
        )

    version = data.get("version", DEFAULT_REGISTRY_VERSION)
    if version not in SUPPORTED_REGISTRY_VERSIONS:
        raise _fail(
            f"{path}: unsupported registry version {version!r}; "
            f"this build reads version(s) {sorted(SUPPORTED_REGISTRY_VERSIONS)}"
        )

    servers: list[RegistryServer] = []
    if has_client:
        raw_client = data[CLIENT_KEY]
        if not isinstance(raw_client, dict):
            raise _fail(f"{path}: '{CLIENT_KEY}' must be an object keyed by server name")
        for index, (name, entry) in enumerate(raw_client.items()):
            if not isinstance(name, str) or not name.strip():
                raise _fail(f"{path}: '{CLIENT_KEY}' keys must be non-empty server names")
            servers.append(_parse_server(name, entry, index=index))
        source_format = CLIENT_KEY
    else:
        raw_native = data[NATIVE_KEY]
        if not isinstance(raw_native, list):
            raise _fail(f"{path}: '{NATIVE_KEY}' must be a list of server objects")
        for index, entry in enumerate(raw_native):
            if not isinstance(entry, dict):
                raise _fail(f"{path}: server[{index}] must be an object")
            name = _as_str(entry.get("name"), where=f"{path}: server[{index}]", field_name="name")
            servers.append(_parse_server(name, entry, index=index))
        source_format = NATIVE_KEY

    if not servers:
        # An empty registry is not a clean fleet. It is a file that audits nothing, and
        # reporting exit 0 for it is the same false confidence as scoring an unread file 100.
        raise _fail(
            f"{path}: registry contains no servers; refusing to report an empty fleet as clean"
        )

    _check_unique([server.name for server in servers])
    return Registry(servers=tuple(servers), source_format=source_format, path=path)


def load_registry(path: Path) -> Registry:
    """Read and validate a registry file.

    Args:
        path: Path to a JSON registry or client configuration.

    Returns:
        The validated :class:`Registry`.

    Raises:
        RegistryError: If the file is missing, is not JSON, or fails validation.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise _fail(f"registry file not found: {path}") from exc
    except OSError as exc:
        raise _fail(f"cannot read registry file {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _fail(
            f"{path}: not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc
    return parse_registry(data, path=str(path))


# --------------------------------------------------------------------------- running


def _describe_failure(exc: BaseException) -> str:
    if isinstance(exc, LiveAuditError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def audit_server_entry(
    server: RegistryServer,
    config: ScanConfig,
    *,
    default_timeout: float = 30.0,
) -> ServerAudit:
    """Audit one registry entry, converting any failure into a recorded outcome.

    This function never raises for a server problem: isolation is the whole contract of a
    fleet run. Only a programming error inside this module would escape.
    """
    if server.unsupported_reason is not None:
        return ServerAudit(
            name=server.name,
            outcome=OUTCOME_SKIPPED,
            command=server.command,
            error=server.unsupported_reason,
        )

    started = time.monotonic()
    try:
        report = scan_server(
            server.command,
            config,
            timeout=server.timeout if server.timeout is not None else default_timeout,
            cwd=server.cwd,
            env=server.env,
        )
    except (LiveAuditError, OSError, ValueError) as exc:
        return ServerAudit(
            name=server.name,
            outcome=OUTCOME_FAILED,
            command=server.command,
            error=_describe_failure(exc),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 - isolation beats fail-fast here, see docstring
        return ServerAudit(
            name=server.name,
            outcome=OUTCOME_FAILED,
            command=server.command,
            error=f"unexpected failure while auditing: {_describe_failure(exc)}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    return ServerAudit(
        name=server.name,
        outcome=OUTCOME_AUDITED,
        command=server.command,
        report=report,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def run_fleet(
    registry: Registry,
    config: ScanConfig | None = None,
    *,
    default_timeout: float = 30.0,
    on_start: Callable[[RegistryServer], None] | None = None,
) -> FleetReport:
    """Audit every server in a registry, sequentially, isolating each failure.

    Servers run one at a time on purpose: each audit spawns a process, and a fleet audit
    that launches twelve at once is a load test of the machine rather than a measurement
    of the servers.

    Args:
        registry: Validated registry.
        config: Scan configuration applied identically to every server.
        default_timeout: Per-server timeout when the entry does not set one.
        on_start: Called with each entry just before it is audited, for progress output.

    Returns:
        A :class:`FleetReport` covering every entry, including the ones that failed.
    """
    effective_cfg = config or ScanConfig()
    audits: list[ServerAudit] = []
    for server in registry.servers:
        if on_start is not None:
            on_start(server)
        audits.append(audit_server_entry(server, effective_cfg, default_timeout=default_timeout))
    return FleetReport(
        audits=tuple(audits),
        registry_path=registry.path,
        source_format=registry.source_format,
        policy_name=effective_cfg.policy_name,
    )


def load_and_run(
    path: Path,
    config: ScanConfig | None = None,
    *,
    default_timeout: float = 30.0,
    on_start: Callable[[RegistryServer], None] | None = None,
) -> FleetReport:
    """Convenience wrapper: read a registry file and audit everything in it."""
    return run_fleet(
        load_registry(path), config, default_timeout=default_timeout, on_start=on_start
    )


__all__ = [
    "CLIENT_KEY",
    "NATIVE_KEY",
    "OUTCOME_AUDITED",
    "OUTCOME_FAILED",
    "OUTCOME_SKIPPED",
    "SUPPORTED_REGISTRY_VERSIONS",
    "FleetReport",
    "Registry",
    "RegistryError",
    "RegistryServer",
    "ServerAudit",
    "audit_server_entry",
    "load_and_run",
    "load_registry",
    "parse_registry",
    "run_fleet",
]
