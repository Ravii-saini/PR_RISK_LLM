"""Per-language tree-sitter grammar registry (SPEC §5.3).

Adding a language means adding one LanguageSpec entry here — the parser
itself (parser.py) is language-agnostic. A file whose extension isn't in
the registry is simply skipped by the caller, not an error.

Each language's grammar has its own quirks about what counts as a
function-like node, how to recover its name, and — critically — whether the
node's own line range already includes its decorators/annotations:
  - Python: a decorated function is wrapped in a `decorated_definition`
    parent that starts at the first `@decorator` line; `function_definition`
    itself starts at `def`. Miss this and a decorator-only change is
    silently un-attributed to its function (a real bug found during
    Python-only testing, before this was generalized).
  - TypeScript: decorators are preceding *siblings* of the method/class,
    not a parent wrapper — same failure mode, different tree shape, so it
    needs its own widening logic.
  - Java: `method_declaration`'s own range already starts at its first
    annotation. No widening needed.
  - Go: no decorator/annotation syntax at all. No widening needed.
"""
from dataclasses import dataclass
from typing import Callable

import tree_sitter_go as tsgo
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node


def _default_range(node: Node) -> tuple:
    return node.start_point, node.end_point


def _field_name(node: Node) -> str | None:
    name_node = node.child_by_field_name("name")
    return name_node.text.decode("utf-8") if name_node else None


def _python_range(node: Node) -> tuple:
    if node.parent is not None and node.parent.type == "decorated_definition":
        return node.parent.start_point, node.parent.end_point
    return node.start_point, node.end_point


def _ts_range(node: Node) -> tuple:
    start = node.start_point
    sibling = node.prev_sibling
    while sibling is not None and sibling.type == "decorator":
        start = sibling.start_point
        sibling = sibling.prev_sibling
    return start, node.end_point


def _js_ts_name(node: Node) -> str | None:
    """`arrow_function` has no name of its own — recover it from how the
    function is actually bound: `const x = () => {}`, `prop = () => {}` on a
    class, or `{ key: () => {} }` in an object literal.
    """
    name = _field_name(node)
    if name is not None or node.type != "arrow_function":
        return name

    parent = node.parent
    if parent is None:
        return None
    if parent.type == "variable_declarator":
        target = parent.child_by_field_name("name")
    elif parent.type == "field_definition":
        # Plain JS grammar names this field "property"...
        target = parent.child_by_field_name("property")
    elif parent.type == "public_field_definition":
        # ...but the TS grammar's equivalent node names it "name" instead.
        target = parent.child_by_field_name("name")
    elif parent.type == "pair":
        target = parent.child_by_field_name("key")
    else:
        target = None
    return target.text.decode("utf-8") if target else None


@dataclass(frozen=True)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...]
    get_language: Callable[[], Language]
    function_node_types: frozenset[str]
    get_name: Callable[[Node], str | None] = _field_name
    get_range: Callable[[Node], tuple] = _default_range


PYTHON = LanguageSpec(
    name="python",
    extensions=(".py",),
    get_language=lambda: Language(tspython.language()),
    function_node_types=frozenset({"function_definition"}),
    get_range=_python_range,
)

_JS_TS_FUNCTION_TYPES = frozenset(
    {
        "function_declaration",
        "method_definition",
        "arrow_function",
        # `function* gen() {}` is a distinct node type from
        # `function_declaration` — generator *methods* (`*m() {}` on a
        # class) are still plain `method_definition`, only top-level
        # generator functions get their own node type. Found missing
        # entirely (empty result, not a wrong range) during adversarial
        # testing.
        "generator_function_declaration",
    }
)

JAVASCRIPT = LanguageSpec(
    name="javascript",
    extensions=(".js", ".jsx", ".mjs", ".cjs"),
    get_language=lambda: Language(tsjavascript.language()),
    function_node_types=_JS_TS_FUNCTION_TYPES,
    get_name=_js_ts_name,
)

TYPESCRIPT = LanguageSpec(
    name="typescript",
    extensions=(".ts",),
    get_language=lambda: Language(tstypescript.language_typescript()),
    function_node_types=_JS_TS_FUNCTION_TYPES,
    get_name=_js_ts_name,
    get_range=_ts_range,
)

TSX = LanguageSpec(
    name="tsx",
    extensions=(".tsx",),
    get_language=lambda: Language(tstypescript.language_tsx()),
    function_node_types=_JS_TS_FUNCTION_TYPES,
    get_name=_js_ts_name,
    get_range=_ts_range,
)

GO = LanguageSpec(
    name="go",
    extensions=(".go",),
    get_language=lambda: Language(tsgo.language()),
    function_node_types=frozenset({"function_declaration", "method_declaration"}),
)

JAVA = LanguageSpec(
    name="java",
    extensions=(".java",),
    get_language=lambda: Language(tsjava.language()),
    function_node_types=frozenset({"method_declaration", "constructor_declaration"}),
)

_ALL_SPECS = (PYTHON, JAVASCRIPT, TYPESCRIPT, TSX, GO, JAVA)

_REGISTRY: dict[str, LanguageSpec] = {
    ext: spec for spec in _ALL_SPECS for ext in spec.extensions
}


def get_language_for_file(filename: str) -> LanguageSpec | None:
    for ext, spec in _REGISTRY.items():
        if filename.endswith(ext):
            return spec
    return None
