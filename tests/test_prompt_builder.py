from worker.llm.prompt_builder import build_prompt
from worker.retrieval.query import RetrievalResult, SimilarFunction

RESULT = RetrievalResult(
    file_path="a.py",
    function_name="risky_fn",
    already_indexed=True,
    callers=["b.py::caller_one"],
    has_tests=False,
    incident_tags=[{"tag": "CVE-1234", "summary": "a bad bug", "reference": "https://github.com/x/y/pull/1"}],
    similar=[
        SimilarFunction(
            file_path="c.py",
            function_name="similar_fn",
            similarity=0.9,
            callers=[],
            has_tests=True,
            incident_tags=[],
        )
    ],
    code="def risky_fn():\n    pass\n",
)


def test_build_prompt_includes_function_code():
    prompt = build_prompt("o", "r", 1, [RESULT])
    assert "def risky_fn():" in prompt


def test_build_prompt_includes_own_metadata():
    prompt = build_prompt("o", "r", 1, [RESULT])
    assert "b.py::caller_one" in prompt
    assert "CVE-1234" in prompt
    assert "Has test coverage: False" in prompt


def test_build_prompt_full_mode_includes_similar_section():
    prompt = build_prompt("o", "r", 1, [RESULT], trimmed=False)
    assert "similar_fn" in prompt


def test_build_prompt_trimmed_mode_drops_similar_section():
    prompt = build_prompt("o", "r", 1, [RESULT], trimmed=True)
    assert "similar_fn" not in prompt
    # higher-priority own facts must still be present when trimmed
    assert "CVE-1234" in prompt
    assert "b.py::caller_one" in prompt


def test_build_prompt_instructs_json_only_output():
    prompt = build_prompt("o", "r", 1, [RESULT])
    assert "risk_level" in prompt
    assert '"low", "medium", "high"'.replace('"', "") in prompt.replace('"', "") or "low|medium|high" in prompt


def test_build_prompt_multiple_functions_each_get_a_section():
    other = RetrievalResult(
        file_path="d.py", function_name="other_fn", already_indexed=False, code="def other_fn():\n    pass\n"
    )
    prompt = build_prompt("o", "r", 1, [RESULT, other])
    assert "risky_fn" in prompt
    assert "other_fn" in prompt
    assert "2 changed function(s)" in prompt
