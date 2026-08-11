"""Retry-with-fallback-then-degrade tests (SPEC §7): the two failure modes
that matter most — Ollama down (fallback engages) and both backends down
(honest degrade, not a crash/hang) — verified with the backends replaced
by controllable fakes, not the real network.
"""
import worker.llm.assess as assess_module
from worker.llm.assess import assess_pr
from worker.retrieval.query import RetrievalResult, SimilarFunction

RESULTS = [
    RetrievalResult(
        file_path="a.py",
        function_name="fn",
        already_indexed=True,
        callers=["b.py::caller"],
        code="def fn():\n    pass\n",
    )
]

_SIMILAR = SimilarFunction(
    file_path="z.py", function_name="near", similarity=0.9, callers=[], has_tests=False, incident_tags=[]
)
MULTI_RESULTS = [
    RetrievalResult(
        file_path="a.py", function_name="fn1", already_indexed=True, code="def fn1():\n    pass\n", similar=[_SIMILAR]
    ),
    RetrievalResult(
        file_path="a.py", function_name="fn2", already_indexed=True, code="def fn2():\n    pass\n", similar=[_SIMILAR]
    ),
]

VALID_JSON = '{"risk_level": "high", "reasons": ["r"], "suggested_checks": ["c"]}'


def test_assess_pr_happy_path_uses_ollama_only(monkeypatch):
    calls = []
    monkeypatch.setattr(
        assess_module.ollama_client, "generate", lambda *a, **k: calls.append("ollama") or VALID_JSON
    )
    monkeypatch.setattr(
        assess_module.hosted_client, "generate", lambda *a, **k: calls.append("gemini") or VALID_JSON
    )

    a = assess_pr("o", "r", 1, RESULTS, "host", "model", "key", "gmodel")

    assert calls == ["ollama"]
    assert a.risk_level == "high"
    assert a.degraded is False


def test_assess_pr_trims_first_attempt_for_multi_function_pr(monkeypatch):
    prompts_seen = {}

    def fake_ollama(prompt, *a, **k):
        prompts_seen["prompt"] = prompt
        return VALID_JSON

    monkeypatch.setattr(assess_module.ollama_client, "generate", fake_ollama)

    assess_pr("o", "r", 1, MULTI_RESULTS, "host", "model", "key", "gmodel")

    # SPEC §7 amendment: >1 changed function trims the *first* Ollama
    # attempt too, dropping the semantic-neighbor section.
    assert "Semantically similar" not in prompts_seen["prompt"]


def test_assess_pr_keeps_full_prompt_on_first_attempt_when_single_function_has_similar(monkeypatch):
    single_with_similar = [MULTI_RESULTS[0]]
    prompts_seen = {}

    def fake_ollama(prompt, *a, **k):
        prompts_seen["prompt"] = prompt
        return VALID_JSON

    monkeypatch.setattr(assess_module.ollama_client, "generate", fake_ollama)

    assess_pr("o", "r", 1, single_with_similar, "host", "model", "key", "gmodel")

    assert "Semantically similar" in prompts_seen["prompt"]


def test_assess_pr_falls_back_to_gemini_on_ollama_timeout(monkeypatch):
    def failing_ollama(*a, **k):
        raise TimeoutError("simulated Ollama timeout")

    prompts_seen = {}

    def fake_gemini(prompt, *a, **k):
        prompts_seen["prompt"] = prompt
        return VALID_JSON

    monkeypatch.setattr(assess_module.ollama_client, "generate", failing_ollama)
    monkeypatch.setattr(assess_module.hosted_client, "generate", fake_gemini)

    a = assess_pr("o", "r", 1, RESULTS, "host", "model", "key", "gmodel")

    assert a.risk_level == "high"
    assert a.degraded is False
    # SPEC §5.5: fallback uses a trimmed prompt (drops the similar-code
    # section) — RESULTS has no `similar` entries, so just confirm the
    # fallback path actually ran by checking a prompt was captured.
    assert "prompt" in prompts_seen


def test_assess_pr_falls_back_on_unparseable_ollama_response(monkeypatch):
    monkeypatch.setattr(assess_module.ollama_client, "generate", lambda *a, **k: "not json at all")
    monkeypatch.setattr(assess_module.hosted_client, "generate", lambda *a, **k: VALID_JSON)

    a = assess_pr("o", "r", 1, RESULTS, "host", "model", "key", "gmodel")

    assert a.risk_level == "high"
    assert a.degraded is False


def test_assess_pr_degrades_honestly_when_both_backends_fail(monkeypatch):
    def failing(*a, **k):
        raise ConnectionError("simulated failure")

    monkeypatch.setattr(assess_module.ollama_client, "generate", failing)
    monkeypatch.setattr(assess_module.hosted_client, "generate", failing)

    a = assess_pr("o", "r", 1, RESULTS, "host", "model", "key", "gmodel")

    assert a.degraded is True
    assert a.risk_level in ("low", "medium", "high")  # still schema-valid, not a crash
    assert len(a.reasons) > 0


def test_assess_pr_degrade_path_does_not_raise_or_hang(monkeypatch):
    """The literal SPEC §7 requirement: both backends failing must not
    raise or hang the caller — it must return, with an honest result."""
    monkeypatch.setattr(
        assess_module.ollama_client, "generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    monkeypatch.setattr(
        assess_module.hosted_client, "generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("y"))
    )

    a = assess_pr("o", "r", 1, RESULTS, "host", "model", "key", "gmodel")  # must not raise
    assert a.degraded is True
