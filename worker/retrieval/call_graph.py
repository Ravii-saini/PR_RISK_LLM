"""Simple static call-graph pass (SPEC §5.4: "known callers... built via a
simple static call-graph pass").

Deliberately name-based, not import-resolved: for each call expression in a
file, we record (enclosing function name) -> (callee identifier name). This
is the documented, intentional limitation — SPEC calls for "simple", not a
fully resolved cross-module call graph. Two functions sharing a name across
files/modules will each pick up the other's call sites. Good enough to
demonstrate "called from N other places", not good enough to disambiguate
overloads or shadowed names.
"""
from collections import defaultdict
from dataclasses import dataclass

from tree_sitter import Node, Parser

from worker.diff_parser.languages import LanguageSpec, get_language_for_file


def _py_callee_name(node: Node) -> str | None:
    fn = node.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "identifier":
        return fn.text.decode("utf-8")
    if fn.type == "attribute":
        attr = fn.child_by_field_name("attribute")
        return attr.text.decode("utf-8") if attr else None
    return None


def _js_ts_callee_name(node: Node) -> str | None:
    fn = node.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "identifier":
        return fn.text.decode("utf-8")
    if fn.type == "member_expression":
        prop = fn.child_by_field_name("property")
        return prop.text.decode("utf-8") if prop else None
    return None


def _go_callee_name(node: Node) -> str | None:
    fn = node.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "identifier":
        return fn.text.decode("utf-8")
    if fn.type == "selector_expression":
        field = fn.child_by_field_name("field")
        return field.text.decode("utf-8") if field else None
    return None


def _java_callee_name(node: Node) -> str | None:
    name = node.child_by_field_name("name")
    return name.text.decode("utf-8") if name else None


@dataclass(frozen=True)
class _CallSpec:
    call_node_types: frozenset[str]
    get_callee_name: callable


_CALL_SPECS: dict[str, _CallSpec] = {
    "python": _CallSpec(frozenset({"call"}), _py_callee_name),
    "javascript": _CallSpec(frozenset({"call_expression"}), _js_ts_callee_name),
    "typescript": _CallSpec(frozenset({"call_expression"}), _js_ts_callee_name),
    "tsx": _CallSpec(frozenset({"call_expression"}), _js_ts_callee_name),
    "go": _CallSpec(frozenset({"call_expression"}), _go_callee_name),
    "java": _CallSpec(frozenset({"method_invocation"}), _java_callee_name),
}


def _enclosing_function_name(node: Node, lang: LanguageSpec) -> str | None:
    current = node.parent
    while current is not None:
        if current.type in lang.function_node_types:
            return lang.get_name(current)
        current = current.parent
    return None


def extract_call_edges(file_path: str, source: str) -> list[tuple[str, str]]:
    """Return [(caller_function_name, callee_name), ...] for one file.
    Calls made outside any function (module-level code) are skipped — there's
    no "caller function" to attribute them to.
    """
    lang = get_language_for_file(file_path)
    if lang is None:
        return []
    call_spec = _CALL_SPECS.get(lang.name)
    if call_spec is None:
        return []

    parser = Parser(lang.get_language())
    tree = parser.parse(source.encode("utf-8"))

    edges: list[tuple[str, str]] = []

    def walk(node: Node) -> None:
        if node.type in call_spec.call_node_types:
            callee = call_spec.get_callee_name(node)
            caller = _enclosing_function_name(node, lang)
            if callee and caller:
                edges.append((caller, callee))
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return edges


def build_callers_by_name(files: dict[str, str]) -> dict[str, list[str]]:
    """files: {file_path: source_text}. Returns {callee_name: [sorted
    unique "file_path::caller_function_name" qualified callers]}.
    """
    callers: dict[str, set[str]] = defaultdict(set)
    for file_path, source in files.items():
        for caller_name, callee_name in extract_call_edges(file_path, source):
            callers[callee_name].add(f"{file_path}::{caller_name}")
    return {name: sorted(qualified) for name, qualified in callers.items()}
