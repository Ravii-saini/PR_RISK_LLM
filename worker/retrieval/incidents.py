"""Manually curated past-incident tags (SPEC §5.4): "even a manually-curated
list of 5-10 'here's a bug that happened here before' entries is enough to
demonstrate the concept." Curated against psf/requests (the pinned eval
repo) from real merged bug/CVE-fix PRs — see incident_tags.json for the
PR references each entry was verified against.
"""
import json
from collections import defaultdict
from pathlib import Path

_TAGS_PATH = Path(__file__).parent / "incident_tags.json"


def load_incident_tags() -> dict[tuple[str, str], list[dict]]:
    """Returns {(file_path, function_name): [tag_dict, ...]}."""
    raw = json.loads(_TAGS_PATH.read_text())
    by_function: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for entry in raw:
        key = (entry["file_path"], entry["function_name"])
        by_function[key].append(
            {
                "tag": entry["tag"],
                "summary": entry["summary"],
                "reference": entry["reference"],
            }
        )
    return dict(by_function)
