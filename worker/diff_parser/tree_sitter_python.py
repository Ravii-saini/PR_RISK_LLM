"""Function-level diff parsing (SPEC §5.3, F2).

Regex-based function detection breaks on real-world code — multi-line
signatures, decorators, nested defs. Instead: parse the full post-change file
with tree-sitter's Python grammar to get every function/method's exact line
range, then report the ones whose range overlaps a line actually touched by
the diff. This is deliberately not "which lines changed" — it's "which
functions changed," which is what SPEC §5.3/§5.4 needs downstream.
"""
import re

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

PY_LANGUAGE = Language(tspython.language())

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_patch_changed_lines(patch: str) -> set[int]:
    """Return the set of new-file (post-change) line numbers touched by a
    unified diff patch, as returned by GitHub's PR files API. Only the '+'
    side is tracked, since those are the line numbers that exist in the
    post-change source we parse with tree-sitter.
    """
    changed: set[int] = set()
    new_line = None
    for line in patch.splitlines():
        hunk = _HUNK_HEADER.match(line)
        if hunk:
            new_line = int(hunk.group(1))
            continue
        if new_line is None:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            changed.add(new_line)
            new_line += 1
        elif line.startswith("-"):
            continue  # removed line — doesn't exist in the new file
        else:
            new_line += 1
    return changed


def find_function_ranges(source: str) -> list[dict]:
    """Return every function/method definition in source as
    {"name": ..., "start_line": ..., "end_line": ...} (1-indexed, inclusive).
    """
    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(source.encode("utf-8"))

    functions: list[dict] = []

    def walk(node) -> None:
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            # A decorated function's real node is wrapped in a
            # `decorated_definition` that starts at the first @decorator
            # line, not at `def`. Use that wider range so a change to only
            # the decorator (e.g. a route path, a retry policy, a permission
            # check) still correctly counts as a change to this function.
            range_node = node
            if node.parent is not None and node.parent.type == "decorated_definition":
                range_node = node.parent
            functions.append(
                {
                    "name": name_node.text.decode("utf-8") if name_node else "<anonymous>",
                    "start_line": range_node.start_point[0] + 1,
                    "end_line": range_node.end_point[0] + 1,
                }
            )
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return functions


def changed_functions(source: str, patch: str) -> list[dict]:
    """Given a file's post-change source and its diff patch, return the
    functions whose line range overlaps at least one changed line.
    """
    changed_lines = parse_patch_changed_lines(patch)
    if not changed_lines:
        return []

    functions = find_function_ranges(source)
    return [
        fn
        for fn in functions
        if any(fn["start_line"] <= ln <= fn["end_line"] for ln in changed_lines)
    ]


def analyze_pr_diff(
    owner: str, repo: str, pr_number: int, head_sha: str, token: str | None = None
) -> list[dict]:
    """End-to-end (SPEC §5.3, F2): fetch a PR's changed files, then for each
    changed Python file, report the functions actually touched.
    """
    from worker.github_client import fetch_file_content, fetch_pr_files

    files = fetch_pr_files(owner, repo, pr_number, token=token)
    results = []
    for f in files:
        filename = f["filename"]
        if not filename.endswith(".py") or f["status"] == "removed":
            continue
        patch = f.get("patch")
        if not patch:
            continue  # GitHub omits `patch` for very large per-file diffs
        source = fetch_file_content(owner, repo, filename, head_sha, token=token)
        fns = changed_functions(source, patch)
        if fns:
            results.append({"file": filename, "functions": fns})
    return results
