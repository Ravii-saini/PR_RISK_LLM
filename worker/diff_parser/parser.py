"""Language-agnostic function-level diff parsing (SPEC §5.3, F2).

Regex-based function detection breaks on real-world code — multi-line
signatures, decorators/annotations, nested definitions. Instead: parse the
full post-change file with the matched language's tree-sitter grammar (see
languages.py for the per-language registry and its documented quirks), get
every function/method's exact line range, and report the ones whose range
overlaps a line actually touched by the diff.
"""
import re

from tree_sitter import Parser

from worker.diff_parser.languages import LanguageSpec, get_language_for_file

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


def find_function_ranges(source: str, lang: LanguageSpec) -> list[dict]:
    """Return every function/method definition in source (per `lang`'s
    grammar) as {"name": ..., "start_line": ..., "end_line": ...}
    (1-indexed, inclusive).
    """
    parser = Parser(lang.get_language())
    tree = parser.parse(source.encode("utf-8"))

    functions: list[dict] = []

    def walk(node) -> None:
        if node.type in lang.function_node_types:
            start, end = lang.get_range(node)
            functions.append(
                {
                    "name": lang.get_name(node) or "<anonymous>",
                    "start_line": start[0] + 1,
                    "end_line": end[0] + 1,
                }
            )
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return functions


def changed_functions(source: str, patch: str, lang: LanguageSpec) -> list[dict]:
    """Given a file's post-change source and its diff patch, return the
    functions whose line range overlaps at least one changed line.
    """
    changed_lines = parse_patch_changed_lines(patch)
    if not changed_lines:
        return []

    functions = find_function_ranges(source, lang)
    return [
        fn
        for fn in functions
        if any(fn["start_line"] <= ln <= fn["end_line"] for ln in changed_lines)
    ]


def analyze_pr_diff(
    owner: str, repo: str, pr_number: int, head_sha: str, token: str | None = None
) -> list[dict]:
    """End-to-end (SPEC §5.3, F2): fetch a PR's changed files, then for each
    file in a supported language, report the functions actually touched.
    Files in an unsupported language are skipped, not an error — the rest
    of the PR is still reviewed.
    """
    from worker.github_client import fetch_file_content, fetch_pr_files

    files = fetch_pr_files(owner, repo, pr_number, token=token)
    results = []
    for f in files:
        filename = f["filename"]
        lang = get_language_for_file(filename)
        if lang is None or f["status"] == "removed":
            continue
        patch = f.get("patch")
        if not patch:
            continue  # GitHub omits `patch` for very large per-file diffs
        source = fetch_file_content(owner, repo, filename, head_sha, token=token)
        fns = changed_functions(source, patch, lang)
        if fns:
            results.append({"file": filename, "functions": fns})
    return results
