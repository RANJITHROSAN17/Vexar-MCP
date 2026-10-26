"""Static extraction of literal MCP SDK registrations and inline handlers.

This is a small text scanner, not a JavaScript interpreter: source is never
imported or executed. Comments and quoted strings are tokenized before matching
calls, and balanced delimiters bound schemas and handlers. Computed registrations,
external schema variables and referenced handler bodies are not resolved. Full
JavaScript semantics (regex literals, template interpolation and lexical scope
resolution) are outside this scanner's supported subset.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from pathlib import Path
from typing import NamedTuple

from mcp_doctor.models import CodeSink, ParameterSpec, ToolSpec
from mcp_doctor.tokens import estimate_tool_tokens

_EXTENSIONS = frozenset({".ts", ".js", ".jsx"})
_LEX = re.compile(
    r"""(?P<comment>//[^\n]*|/\*.*?\*/)|"""
    r"""(?P<string>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|"""
    r"""(?P<word>[A-Za-z_$][\w$]*)|(?P<number>\d+(?:\.\d+)?)|(?P<other>=>|\S)""",
    re.DOTALL,
)
_TYPES = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "bigint": "bigint",
    "date": "date",
    "any": "any",
    "unknown": "unknown",
    "never": "never",
    "array": "list",
    "object": "object",
    "record": "record",
    "tuple": "tuple",
    "map": "map",
    "set": "set",
    "enum": "enum",
    "union": "union",
    "intersection": "intersection",
}
_MEMBERS = {
    "child_process": {"exec", "execSync", "spawn", "spawnSync", "execFile", "execFileSync"},
    "fs": {"unlink", "unlinkSync", "rmdir", "rmdirSync", "rm", "rmSync"},
    "vm": {"runInContext", "runInNewContext", "runInThisContext", "compileFunction"},
    "axios": {"get", "post", "put", "patch", "delete", "head", "options", "request"},
}
_KINDS = {"child_process": "shell", "fs": "filesystem", "vm": "shell", "axios": "ssrf"}
_Span = tuple[int, int]


class _Token(NamedTuple):
    value: str
    offset: int
    kind: str


class _Source:
    def __init__(self, text: str) -> None:
        self.tokens = [
            _Token(m.group(), m.start(), m.lastgroup or "other")
            for m in _LEX.finditer(text)
            if m.lastgroup != "comment"
        ]
        self.lines = [0, *(m.end() for m in re.finditer("\n", text))]
        self.pairs: dict[int, int] = {}
        stack: list[int] = []
        for i, token in enumerate(self.tokens):
            if token.value in {"(", "[", "{"}:
                stack.append(i)
            elif token.value in {")", "]", "}"}:
                if (
                    stack
                    and self.tokens[stack[-1]].value == {")": "(", "]": "[", "}": "{"}[token.value]
                ):
                    self.pairs[stack.pop()] = i
                else:
                    stack.clear()

    def line(self, index: int) -> int:
        return bisect_right(self.lines, self.tokens[index].offset)

    def values(self, start: int, end: int) -> list[str]:
        return [token.value for token in self.tokens[start:end]]

    def literal(self, span: _Span) -> str | None:
        start, end = span
        if end != start + 1 or self.tokens[start].kind != "string":
            return None
        text = self.tokens[start].value
        if text.startswith("`") and "${" in text:
            return None
        escapes = {"n": "\n", "r": "\r", "t": "\t"}
        return re.sub(r"""\\([nrt\\'"`])""", lambda m: escapes.get(m[1], m[1]), text[1:-1])

    def parts(self, start: int, end: int) -> list[_Span]:
        result = []
        index = start
        while index < end:
            if self.tokens[index].value == ",":
                if start < index:
                    result.append((start, index))
                start = index + 1
            index = self.pairs.get(index, index) + 1
        if start < end:
            result.append((start, end))
        return result

    def fields(self, span: _Span) -> dict[str, tuple[int, _Span]]:
        start, end = span
        if start >= end or self.tokens[start].value != "{" or start not in self.pairs:
            return {}
        fields = {}
        for a, b in self.parts(start + 1, self.pairs[start]):
            if a + 2 < b and self.tokens[a + 1].value == ":":
                name = (
                    self.literal((a, a + 1))
                    if self.tokens[a].kind == "string"
                    else self.tokens[a].value
                )
                if name is not None:
                    fields[name] = (a, (a + 2, b))
        return fields

    def schema(self, span: _Span) -> dict[str, tuple[int, _Span]]:
        start, end = span
        if self.values(start, start + 4) == ["z", ".", "object", "("]:
            close = self.pairs.get(start + 3)
            if close is None:
                return {}
            args = self.parts(start + 4, close)
            return self.fields(args[0]) if args else {}
        return self.fields((start, end))


def _parameters(source: _Source, schema: _Span) -> tuple[ParameterSpec, ...]:
    params = []
    pending = [
        (name, key, span, True) for name, (key, span) in reversed(source.schema(schema).items())
    ]
    while pending:
        name, key, (start, end), parent_required = pending.pop()
        if source.values(start, start + 2) != ["z", "."] or start + 3 not in source.pairs:
            continue
        kind = source.tokens[start + 2].value
        required, bounds, description = parent_required, False, ""
        index = source.pairs[start + 3] + 1
        while index + 2 < end and source.tokens[index].value == ".":
            method = source.tokens[index + 1].value
            close = source.pairs.get(index + 2)
            if close is None:
                break
            if method in {"optional", "default"}:
                required = False
            if method in {"max", "min", "length", "email"}:
                bounds = True
            if method == "describe":
                description = source.literal((index + 3, close)) or ""
            index = close + 1
        params.append(
            ParameterSpec(
                name=name,
                type_name=_TYPES.get(kind, "unknown"),
                description=description,
                required=required,
                has_bounds=bounds,
                line=source.line(key),
            )
        )
        if kind == "object":
            pending.extend(
                (f"{name}.{child}", child_key, span, required)
                for child, (child_key, span) in reversed(source.schema((start, end)).items())
            )
    return tuple(params)


def _imports(source: _Source) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    functions: dict[str, tuple[str, str]] = {}
    namespaces = {name: name for name in _MEMBERS}
    tokens = source.tokens
    for i, token in enumerate(tokens[:-1]):
        if (
            token.value != "import"
            or tokens[i + 1].value == "type"
            or tokens[i + 1].kind == "string"
        ):
            continue
        end = i + 1
        while end < len(tokens) and tokens[end].value not in {"from", ";", "import"}:
            end += 1
        if end + 1 >= len(tokens) or tokens[end].value != "from":
            continue
        module = (source.literal((end + 1, end + 2)) or "").removeprefix("node:")
        if module not in _MEMBERS:
            continue
        if tokens[i + 1].kind == "word":
            namespaces[tokens[i + 1].value] = module
        for index in range(i + 1, end):
            if tokens[index].value == "*" and index + 2 < end and tokens[index + 1].value == "as":
                namespaces[tokens[index + 2].value] = module
            if tokens[index].value == "{" and index in source.pairs:
                for a, b in source.parts(index + 1, source.pairs[index]):
                    original = tokens[a].value
                    if original in _MEMBERS[module]:
                        alias = (
                            tokens[a + 2].value
                            if a + 2 < b and tokens[a + 1].value == "as"
                            else original
                        )
                        functions[alias] = (_KINDS[module], f"{module}.{original}")
    return functions, namespaces


def _handler(source: _Source, span: _Span) -> _Span:
    start, end = span
    while start < end and source.tokens[start].value == "(" and source.pairs.get(start) == end - 1:
        start, end = start + 1, end - 1
    index = start
    is_function = "function" in source.values(start, min(start + 2, end))
    while index < end:
        if source.tokens[index].value == "=>":
            return index + 1, end
        if is_function and source.tokens[index].value == "{":
            return index + 1, source.pairs.get(index, end)
        index = source.pairs.get(index, index) + 1
    # A named handler cannot be inspected without resolving another declaration.
    return end, end


def _positive_number(source: _Source, span: _Span) -> bool:
    start, end = span
    return (
        end == start + 1
        and source.tokens[start].kind == "number"
        and float(source.tokens[start].value) > 0
    )


def _bounded_http(source: _Source, args: list[_Span], call: str) -> bool:
    is_fetch = call in {"fetch", "globalThis.fetch"}
    method = call.rsplit(".", 1)[-1]
    # Axios body verbs take config third; request/config overloads differ.
    # Fetch has no native `timeout` option: only a bounded abort signal counts.
    if method in {"post", "put", "patch"}:
        option_index = 2
    elif method in {"axios", "request"}:
        option_index = min(1, len(args) - 1)
    else:
        option_index = 1
    if not args or option_index >= len(args):
        return False
    fields = source.fields(args[option_index])
    timeout = fields.get("timeout")
    if not is_fetch and timeout and _positive_number(source, timeout[1]):
        return True
    signal = fields.get("signal")
    if signal:
        start, end = signal[1]
        if (
            source.values(start, start + 4) == ["AbortSignal", ".", "timeout", "("]
            and source.pairs.get(start + 3) == end - 1
        ):
            return _positive_number(source, (start + 4, end - 1))
    return False


def _sinks(
    source: _Source,
    handler: _Span,
    params: tuple[ParameterSpec, ...],
    functions: dict[str, tuple[str, str]],
    namespaces: dict[str, str],
) -> tuple[CodeSink, ...]:
    result = []
    roots = {p.name.split(".", 1)[0] for p in params}
    start, end = _handler(source, handler)
    tokens = source.tokens
    for index in range(start, end):
        if tokens[index].kind != "word" or (index > start and tokens[index - 1].value == "."):
            continue
        names = [tokens[index].value]
        after = index + 1
        while after + 1 < end and tokens[after].value == "." and tokens[after + 1].kind == "word":
            names.append(tokens[after + 1].value)
            after += 2
        if after >= end or tokens[after].value != "(" or after not in source.pairs:
            continue
        close = source.pairs[after]
        if close >= end:
            continue
        call = ".".join(names)
        imported = functions.get(call)
        kind = imported[0] if imported else None
        canonical = imported[1] if imported else call
        if call in {"eval", "Function", "globalThis.eval", "globalThis.Function"}:
            kind = "shell"
        elif call in {"fetch", "globalThis.fetch"}:
            kind = "ssrf"
        elif names[0] in namespaces:
            module = namespaces[names[0]]
            if (len(names) == 2 and names[1] in _MEMBERS[module]) or (
                len(names) == 1 and module == "axios"
            ):
                kind = _KINDS[module]
                canonical = ".".join([module, *names[1:]])
        if kind is not None:
            args = source.parts(after + 1, close)
            used = next(
                (
                    t.value
                    for t in tokens[after + 1 : close]
                    if t.kind == "word" and t.value in roots
                ),
                None,
            )
            result.append(
                CodeSink(
                    kind=kind,
                    call_name=call,
                    line=source.line(index),
                    uses_param=used,
                    timeout_missing=kind == "ssrf" and not _bounded_http(source, args, canonical),
                )
            )
    return tuple(result)


def load_typescript_file(path: Path) -> list[ToolSpec]:
    """Extract literal SDK tool registrations without executing JavaScript.

    Args:
        path: A TypeScript, JavaScript or JSX source file. Declaration files are ignored.

    Returns:
        Tools in source order, with statically known parameters and inline-handler
        sinks. Unreadable files and unsupported extensions yield an empty list.
    """
    if path.name.endswith(".d.ts") or path.suffix not in _EXTENSIONS:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    source = _Source(text)
    functions, namespaces = _imports(source)
    tools = []
    tokens = source.tokens
    for index in range(2, len(tokens) - 1):
        if (
            tokens[index].value not in {"tool", "registerTool"}
            or tokens[index - 1].value != "."
            or tokens[index - 2].kind != "word"
        ):
            continue
        opening = index + 1
        if tokens[opening].value != "(" or opening not in source.pairs:
            continue
        args = source.parts(opening + 1, source.pairs[opening])
        if len(args) < 2:
            continue
        name = source.literal(args[0])
        if name is None:
            continue
        description = ""
        schema = (0, 0)
        if tokens[index].value == "registerTool":
            if len(args) < 3:
                continue
            metadata = source.fields(args[1])
            if "description" in metadata:
                description = source.literal(metadata["description"][1]) or ""
            if "inputSchema" in metadata:
                schema = metadata["inputSchema"][1]
        else:
            first_schema = 1
            literal = source.literal(args[1])
            if literal is not None:
                description, first_schema = literal, 2
            if first_schema < len(args) - 1:
                schema = args[first_schema]
        params = _parameters(source, schema)
        tools.append(
            ToolSpec(
                name=name,
                description=description,
                source_file=str(path),
                line=source.line(index - 2),
                parameters=params,
                sinks=_sinks(source, args[-1], params, functions, namespaces),
                estimated_tokens=estimate_tool_tokens(name, description, params),
            )
        )
    return tools
