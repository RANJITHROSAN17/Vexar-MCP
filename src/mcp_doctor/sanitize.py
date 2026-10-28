"""Neutralise untrusted text before it reaches an output sink.

Vexar-MCP reads code it did not write — and, for live audits, names and descriptions served
by a *remote* MCP server. All of that text flows into reports a human reads:

* a terminal, where ``\\x1b[2K`` erases the line above it and ``\\x1b]8;;`` turns text into a
  clickable link that points anywhere;
* a Markdown PR comment, where a fenced block or a ``##`` heading changes what the reader
  believes they are looking at;
* an HTML dashboard, where control bytes are noise but structure matters;
* CI logs, where a workflow command on its own line is obeyed by the runner.

None of that is remote code execution, and this module does not pretend otherwise. It is
*content spoofing*: a hostile (or merely careless) input can make a report say something the
scanner never found. For a security tool that is a real failure — the report is the product.

Escaping is the reporter's job and each one already does it for its own syntax (``html.escape``,
workflow-command `%0A` escaping, JSON encoding). What was missing is the layer *before* that:
removing invisible control characters and defusing structural markers that survive escaping.
"""

from __future__ import annotations

import re

#: C0 and C1 control characters except tab and newline, plus the DEL and zero-width characters
#: used to hide payloads from a human reviewer. ``\x1b`` is covered by the C0 range.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\u202a-\u202e]")

#: A run of three or more backticks opens or closes a fenced code block in Markdown.
_FENCE_RE = re.compile(r"`{3,}")

#: A line that starts with hashes is a heading; at the start of a line, `>` quotes, and
#: `::` is a CI workflow command.
_LINE_MARKER_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>#{1,6}|>|::)", re.MULTILINE)


def strip_control_chars(text: str, *, keep_newlines: bool = True) -> str:
    """Remove invisible control and bidirectional-override characters.

    Args:
        text: Untrusted text from a scanned file, manifest, or remote server.
        keep_newlines: Preserve ``\\n`` (reports are multi-line) and convert ``\\r`` to it.

    Returns:
        Text with control characters removed. Ordinary Unicode is untouched — a tool
        description legitimately written in Japanese or containing an emoji must survive.
    """
    if not text:
        return ""
    if keep_newlines:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    else:
        text = _CONTROL_RE.sub("", text.replace("\n", " ").replace("\r", " "))
        return text
    return _CONTROL_RE.sub("", text)


def neutralize_markdown(text: str) -> str:
    """Prevent untrusted text from creating structure in a Markdown document.

    Specifically: no fenced code block, no heading, no blockquote, no CI workflow command.
    Inline syntax is left alone — it is rendered as text by the surrounding context or
    escaped by the reporter, and mangling it would make legitimate descriptions unreadable.

    Args:
        text: Untrusted text that will be embedded in Markdown.

    Returns:
        Text that cannot open a block or a workflow command.
    """
    if not text:
        return ""
    text = strip_control_chars(text)
    # Break fence runs so they can never open/close a block: ``` -> ``\u200b` (invisible sep)
    text = _FENCE_RE.sub(lambda m: m.group(0)[:1] + "\u200b" + m.group(0)[1:], text)
    # Neutralise line-leading markers by prefixing a zero-width space, which renders as
    # nothing but stops the marker from being at the start of the line.
    text = _LINE_MARKER_RE.sub(lambda m: m.group("indent") + "\u200b" + m.group("marker"), text)
    return text


def neutralize_workflow_command(text: str) -> str:
    """Stop untrusted text from starting a GitHub Actions workflow command on a new line.

    The reporter already escapes ``%``, ``\\r`` and ``\\n`` inside a single annotation. This
    guards the case where raw multi-line text reaches a log-adjacent sink.
    """
    if not text:
        return ""
    text = strip_control_chars(text)
    return re.sub(r"(?m)^(::)", "\u200b\\1", text)


def sanitize_for_sink(text: str, *, sink: str) -> str:
    """Apply the right neutralisation for a named sink.

    Args:
        text: Untrusted text.
        sink: One of ``terminal``, ``markdown``, ``html``, ``github``, ``json``, ``sarif``.

    Returns:
        Text safe for that sink. Unknown sinks get the conservative control-character strip.
    """
    if sink == "markdown":
        return neutralize_markdown(text)
    if sink == "github":
        return neutralize_workflow_command(text)
    # json/sarif/html/terminal: the reporter escapes its own syntax, so removing invisible
    # characters is sufficient and keeps the data faithful for machine consumers.
    return strip_control_chars(text)


__all__ = [
    "neutralize_markdown",
    "neutralize_workflow_command",
    "sanitize_for_sink",
    "strip_control_chars",
]
