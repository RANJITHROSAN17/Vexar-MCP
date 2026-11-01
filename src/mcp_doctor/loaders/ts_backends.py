"""Optional tree-sitter front end for TypeScript tool registration.

The lexical loader reads source as tokens and can only see a registration whose name is
written as a literal at the call site. Real SDK usage does not look like that:

    const TOOL_NAME = "search_docs";
    server.tool(TOOL_NAME, DESC, createSchema(), handler);

Every part of that line is invisible to a token scanner, and the file reports zero tools —
which the report then shows as "no MCP tools here" rather than "I could not read this".

This module adds an *opt-in* second front end. It parses with tree-sitter, resolves
identifiers to their same-file definitions, and then hands the resolved spans to the
**same** parameter and sink analysers the lexical loader uses. That sharing is deliberate:
two front ends that each grew their own schema interpreter would eventually disagree about
the same file, and a security report whose findings depend on which parser you picked is a
report you cannot trust. tree-sitter contributes *resolution*; every judgement about what a
schema or a handler means is still made in one place.

Three rules keep this honest:

* **Same-file only.** A binding is resolved only if it is declared in this file. Following
  `import` across modules would mean attributing a finding to a file the user did not scan,
  with a line number from somewhere else — a misattribution, which for a diagnostic tool is
  worse than a documented gap.
* **Nothing is invented.** A template string with a substitution, an unmapped import, a
  function whose body has several returns, a name declared conditionally: all resolve to
  ``None``. The registration is then *counted as unresolved* rather than silently dropped,
  so "0 tools" and "3 tools I could not read" stop looking the same.
* **Optional, and not a fallback.** ``tree-sitter`` is an extra. Selecting it without the
  extra installed raises, because silently falling back to the lexical backend would produce
  a report that claims coverage it does not have.
"""

from __future__ import annotations

import bisect
from typing import Any

from mcp_doctor.loaders.typescript_loader import (
    DEFAULT_TS_PARSER,
    TS_PARSERS,
    Extraction,
    _imports,
    _parameters,
    _sinks,
    _Source,
    _Span,
)
from mcp_doctor.models import ToolSpec
from mcp_doctor.tokens import estimate_tool_tokens

#: How many identifier hops to follow before giving up. A chain longer than this is not a
#: constant declaration any more, and guessing where it ends is how a scanner invents one.
_MAX_RESOLVE_DEPTH = 8

_INSTALL_HINT = (
    "the tree-sitter backend is an optional extra: "
    'install it with `pip install "Vexar-MCP[tree-sitter]"` (or `pip install tree-sitter '
    "tree-sitter-typescript`), or run with --ts-parser lexical"
)


class TreeSitterUnavailable(RuntimeError):
    """Raised when the tree-sitter backend is selected but its extra is not installed."""


def tree_sitter_available() -> bool:
    """True when the optional tree-sitter extra can be imported."""
    try:
        _load_language()
    except TreeSitterUnavailable:
        return False
    return True


def _load_language() -> Any:
    """Import tree-sitter and return a TypeScript ``Language``.

    Raises:
        TreeSitterUnavailable: If either the parser runtime or the TypeScript grammar is
            missing, or the installed runtime uses an API this build does not recognise.
    """
    try:
        import tree_sitter_typescript  # noqa: PLC0415
        from tree_sitter import Language  # noqa: PLC0415 - optional dependency, by design
    except ImportError as exc:  # pragma: no cover - exercised when the extra is absent
        raise TreeSitterUnavailable(f"{exc}. {_INSTALL_HINT}") from exc
    try:
        return Language(tree_sitter_typescript.language_typescript())
    except AttributeError as exc:  # pragma: no cover - grammar API changed upstream
        raise TreeSitterUnavailable(
            f"the installed tree_sitter_typescript does not expose language_typescript(): {exc}. "
            f"{_INSTALL_HINT}"
        ) from exc


def _make_parser() -> Any:
    # Resolve the language first: it is the import that raises TreeSitterUnavailable when
    # the extra is missing, and it must do so before anything else is attempted.
    language = _load_language()
    try:
        from tree_sitter import Parser  # noqa: PLC0415 - optional dependency, by design
    except ImportError as exc:  # pragma: no cover - reachable only on a partial install
        raise TreeSitterUnavailable(f"{exc}. {_INSTALL_HINT}") from exc
    try:
        # tree-sitter >= 0.25 takes the language in the constructor.
        return Parser(language)
    except TypeError:  # pragma: no cover - older runtimes
        # tree-sitter < 0.25 has no language argument; it is set afterwards instead. Read
        # through getattr because the installed stubs only describe the newer surface.
        parser = Parser()
        set_language = getattr(parser, "set_language", None)
        if set_language is None:
            raise TreeSitterUnavailable(
                f"this tree-sitter build supports neither Parser(language) nor Parser.set_language(). "
                f"{_INSTALL_HINT}"
            ) from None
        set_language(language)
        return parser


class _Ast:
    """A parsed file with its node offsets mapped onto the lexical analyser's tokens.

    Every span produced here is in the *token index* space of ``_Source``, which is what
    lets :func:`_parameters` and :func:`_sinks` be reused unchanged.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.raw = text.encode("utf-8")
        self.source = _Source(text)
        self._offsets = [token.offset for token in self.source.tokens]
        self._byte_to_char = self._build_byte_map()
        parser = _make_parser()
        self.root = parser.parse(self.raw).root_node

    def _build_byte_map(self) -> list[int] | None:
        """Map UTF-8 byte offsets to ``str`` indices, or ``None`` for pure-ASCII source.

        tree-sitter reports byte offsets; the token analyser uses string indices. They only
        differ once a file contains a non-ASCII character, which is exactly the file where a
        silent off-by-N would misplace every finding.
        """
        if len(self.raw) == len(self.text):
            return None
        mapping = [0] * (len(self.raw) + 1)
        byte_index = 0
        for char_index, char in enumerate(self.text):
            width = len(char.encode("utf-8"))
            for offset in range(width):
                mapping[byte_index + offset] = char_index
            byte_index += width
        mapping[len(self.raw)] = len(self.text)
        return mapping

    def span(self, node: Any) -> _Span:
        """Token span for a node."""
        start, end = node.start_byte, node.end_byte
        if self._byte_to_char is not None:
            start, end = self._byte_to_char[start], self._byte_to_char[end]
        return (bisect.bisect_left(self._offsets, start), bisect.bisect_left(self._offsets, end))

    def text_of(self, node: Any) -> str:
        return self.raw[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def literal(self, node: Any | None) -> str | None:
        """The string value of a literal node, via the lexical analyser's own unescaping."""
        if node is None:
            return None
        return self.source.literal(self.span(node))


# --------------------------------------------------------------------------- resolution


class _Resolver:
    """Resolve identifiers to their same-file definitions, or refuse to."""

    _DECLARATIONS = frozenset({"lexical_declaration", "variable_declaration"})
    _FUNCTIONS = frozenset({"function_declaration", "generator_function_declaration"})
    _WRAPPERS = frozenset(
        {"parenthesized_expression", "as_expression", "satisfies_expression", "non_null_expression"}
    )
    #: Nodes that are themselves a definition. Handed straight to the analysers.
    _VALUES = frozenset(
        {
            "object",
            "array",
            "string",
            "arrow_function",
            "function_declaration",
            "generator_function_declaration",
        }
    )

    def __init__(self, root: Any) -> None:
        self.bindings: dict[str, Any] = {}
        self._collect(root)

    def _collect(self, node: Any) -> None:
        """Record every top-level binding. Nested scopes are deliberately not consulted."""
        for child in node.named_children:
            if child.type == "export_statement":
                self._collect(child)
                continue
            if child.type in self._DECLARATIONS:
                for declarator in child.named_children:
                    if declarator.type != "variable_declarator":
                        continue
                    name = declarator.child_by_field_name("name")
                    value = declarator.child_by_field_name("value")
                    # Only a plain identifier binding is safe to substitute. A destructuring
                    # pattern binds several names at once and resolving it would be a guess.
                    if name is not None and name.type == "identifier" and value is not None:
                        self.bindings.setdefault(self._text(name), value)
            elif child.type in self._FUNCTIONS:
                name = child.child_by_field_name("name")
                if name is not None:
                    self.bindings.setdefault(self._text(name), child)

    @staticmethod
    def _text(node: Any) -> str:
        source = node.text
        return source.decode("utf-8", "replace") if isinstance(source, bytes) else str(source)

    def resolve(self, node: Any | None, *, depth: int = 0) -> Any | None:
        """Follow identifiers and factory calls to the node holding the definition.

        Args:
            node: A tree-sitter node, or ``None``.
            depth: Internal recursion counter; resolution stops past
                :data:`_MAX_RESOLVE_DEPTH`.

        Returns:
            The definition node, or ``None`` when this file cannot prove one. ``None`` is
            never a substitute for a guess: callers count it as unresolved.

        A node that is already a defining expression is returned as-is — an object literal,
        a ``z.object(...)`` call, an arrow function. Only identifiers and factory calls need
        following; anything else is a value this scanner has no rule for, and saying so is
        more useful than pretending.
        """
        if node is None or depth > _MAX_RESOLVE_DEPTH:
            return None
        if node.type in self._WRAPPERS:
            inner = node.named_children[0] if node.named_children else None
            return self.resolve(inner, depth=depth + 1)
        if node.type == "template_string":
            # A substitution makes the value depend on runtime state.
            if any(child.type == "template_substitution" for child in node.named_children):
                return None
            return node
        if node.type == "identifier":
            target = self.bindings.get(self._text(node))
            if target is None or target is node:
                return None
            return self.resolve(target, depth=depth + 1)
        if node.type == "call_expression":
            callee = node.child_by_field_name("function")
            if callee is not None and callee.type == "identifier":
                target = self.bindings.get(self._text(callee))
                if target is not None:
                    # A same-file factory: follow it to the value it returns.
                    return self._returned_value(target, depth=depth + 1)
            # Any other call (`z.object({...})`, an unmapped import) is left alone: the
            # analysers know what to do with it, and following it would be a guess.
            return node
        if node.type in self._VALUES:
            return node
        return None

    def _returned_value(self, function: Any, *, depth: int) -> Any | None:
        """The single expression a same-file function returns, if there is exactly one."""
        if function.type == "arrow_function":
            body = function.child_by_field_name("body")
            if body is not None and body.type != "statement_block":
                return self.resolve(body, depth=depth)
        block = self._body_block(function)
        if block is None:
            return None
        returns = [child for child in block.named_children if child.type == "return_statement"]
        # Several returns mean the value depends on an argument this scanner does not model.
        if len(returns) != 1:
            return None
        expression = returns[0].named_children[0] if returns[0].named_children else None
        return self.resolve(expression, depth=depth)

    def _body_block(self, function: Any) -> Any | None:
        body = function.child_by_field_name("body")
        if body is not None and body.type == "statement_block":
            return body
        for child in function.named_children:
            if child.type == "statement_block":
                return child
        return None


# --------------------------------------------------------------------------- extraction


def _argument_nodes(call: Any) -> list[Any]:
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        for child in call.named_children:
            if child.type == "arguments":
                arguments = child
                break
    return list(arguments.named_children) if arguments is not None else []


def _object_field(node: Any, name: str) -> Any | None:
    if node is None or node.type != "object":
        return None
    for pair in node.named_children:
        if pair.type not in {"pair", "shorthand_property_identifier"}:
            continue
        key = pair.child_by_field_name("key") or (
            pair.named_children[0] if pair.named_children else None
        )
        if key is None:
            continue
        key_text = _Resolver._text(key).strip("\"'`")
        if key_text == name:
            value = pair.child_by_field_name("value")
            return value if value is not None else key
    return None


def _find_registrations(root: Any) -> list[Any]:
    """Every ``<x>.tool(...)`` / ``<x>.registerTool(...)`` call, in source order."""
    found: list[Any] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "call_expression":
            function = node.child_by_field_name("function")
            if (
                function is not None
                and function.type == "member_expression"
                and function.child_by_field_name("property") is not None
                and _Resolver._text(function.child_by_field_name("property"))
                in {"tool", "registerTool"}
            ):
                found.append(node)
        stack.extend(reversed(node.named_children))
    found.sort(key=lambda call: call.start_byte)
    return found


def extract_tools(path_text: str, text: str, *, source_file: str) -> Extraction:
    """Extract tools using tree-sitter and same-file resolution.

    Args:
        path_text: Unused; kept for symmetry with the lexical loader's signature.
        text: File contents.
        source_file: Path recorded on each :class:`ToolSpec`.

    Returns:
        An :class:`Extraction` with the tools found and a count of registrations whose
        arguments could not be resolved.

    Raises:
        TreeSitterUnavailable: If the optional extra is missing.
    """
    ast = _Ast(text)
    resolver = _Resolver(ast.root)
    source = ast.source
    # Imported shell/HTTP aliases are resolved the same way the lexical backend resolves
    # them: a file that does `import { exec as run } from "child_process"` must produce the
    # same sink in either mode, or the parser choice would change the security result.
    functions, namespaces = _imports(source)
    tools: list[ToolSpec] = []
    unresolved = 0

    for call in _find_registrations(ast.root):
        kind = _Resolver._text(call.child_by_field_name("function").child_by_field_name("property"))
        args = _argument_nodes(call)
        if len(args) < 2:
            unresolved += 1
            continue

        name_node = resolver.resolve(args[0])
        name = ast.literal(name_node)
        if name is None:
            # The whole reason this backend exists: count it instead of dropping it.
            unresolved += 1
            continue

        description = ""
        schema_span: _Span = (0, 0)
        if kind == "registerTool":
            metadata = resolver.resolve(args[1])
            description_node = _object_field(metadata, "description")
            description = ast.literal(resolver.resolve(description_node)) or ""
            schema_node = _object_field(metadata, "inputSchema")
            if schema_node is not None:
                resolved = resolver.resolve(schema_node)
                schema_span = ast.span(resolved if resolved is not None else schema_node)
        else:
            first_schema = 1
            literal = ast.literal(resolver.resolve(args[1]))
            if literal is not None:
                description, first_schema = literal, 2
            if first_schema < len(args) - 1:
                resolved = resolver.resolve(args[first_schema])
                if resolved is not None:
                    schema_span = ast.span(resolved)

        handler_node = resolver.resolve(args[-1])
        handler_span = ast.span(handler_node) if handler_node is not None else (0, 0)
        params = _parameters(source, schema_span)
        tools.append(
            ToolSpec(
                name=name,
                description=description,
                source_file=source_file,
                line=source.line(ast.span(call)[0]),
                parameters=params,
                sinks=_sinks(source, handler_span, params, functions, namespaces),
                estimated_tokens=estimate_tool_tokens(name, description, params),
            )
        )
    return Extraction(tools=tuple(tools), backend="tree-sitter", unresolved=unresolved)


__all__ = [
    "DEFAULT_TS_PARSER",
    "TS_PARSERS",
    "TreeSitterUnavailable",
    "extract_tools",
    "tree_sitter_available",
]
