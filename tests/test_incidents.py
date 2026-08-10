from worker.retrieval.incidents import load_incident_tags


def test_load_incident_tags_count_in_spec_range():
    # SPEC §5.4: "even a manually-curated list of 5-10 ... entries is
    # enough to demonstrate the concept"
    tags = load_incident_tags()
    total_entries = sum(len(v) for v in tags.values())
    assert 5 <= total_entries <= 10


def test_load_incident_tags_entries_have_required_fields():
    tags = load_incident_tags()
    for (file_path, function_name), entries in tags.items():
        assert file_path
        assert function_name
        for entry in entries:
            assert entry["tag"]
            assert entry["summary"]
            assert entry["reference"].startswith("https://github.com/")


def test_load_incident_tags_keyed_by_file_and_function():
    tags = load_incident_tags()
    assert ("src/requests/utils.py", "get_netrc_auth") in tags
    assert tags[("src/requests/utils.py", "get_netrc_auth")][0]["tag"] == "CVE-2024-47081"
