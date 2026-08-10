"""Function-level chunking for the indexing pipeline (SPEC §5.4).

Reuses the same tree-sitter machinery Phase 2 built for diff parsing
(`worker/diff_parser/parser.find_function_ranges`) — the only difference
here is that we chunk an *entire file's* functions, not just the ones a
diff happens to touch. Chunking by function (not fixed-size text blocks)
is the whole point: a chunk cut mid-function destroys the retrieval signal.
"""
from dataclasses import dataclass

from worker.diff_parser.languages import LanguageSpec, get_language_for_file
from worker.diff_parser.parser import find_function_ranges


@dataclass(frozen=True)
class Chunk:
    file_path: str
    function_name: str
    language: str
    start_line: int
    end_line: int
    code: str


def chunk_file(file_path: str, source: str) -> list[Chunk]:
    """Chunk one file's functions/methods. Returns [] for unsupported
    languages or files with no functions — not an error, per SPEC §5.3's
    "skip, don't error" rule, extended here to indexing.
    """
    lang = get_language_for_file(file_path)
    if lang is None:
        return []

    lines = source.splitlines()
    chunks = []
    for fn in find_function_ranges(source, lang):
        # start_line/end_line are 1-indexed inclusive.
        code = "\n".join(lines[fn["start_line"] - 1 : fn["end_line"]])
        chunks.append(
            Chunk(
                file_path=file_path,
                function_name=fn["name"],
                language=lang.name,
                start_line=fn["start_line"],
                end_line=fn["end_line"],
                code=code,
            )
        )
    return chunks


def chunk_files(files: dict[str, str]) -> list[Chunk]:
    """files: {file_path: source_text}. Returns every function chunk across
    every supported-language file.
    """
    chunks = []
    for file_path, source in files.items():
        chunks.extend(chunk_file(file_path, source))
    return chunks
