import worker.review as review_module
from worker.llm.schema import Assessment
from worker.retrieval.query import RetrievalResult


class FakeSettings:
    def __init__(self, key_path):
        self.github_app_private_key_path = key_path
        self.github_app_id = "12345"
        self.database_url = "postgresql://fake"
        self.embedding_model = "fake-model"
        self.ollama_host = "http://fake"
        self.ollama_model = "fake"
        self.gemini_api_key = "fake"
        self.gemini_model = "fake"


def _settings(tmp_path):
    key_path = tmp_path / "key.pem"
    key_path.write_text("fake-private-key")
    return FakeSettings(str(key_path))


def test_review_pr_skips_when_no_changed_functions(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(review_module, "get_installation_token_for_repo", lambda *a, **k: "tok")
    monkeypatch.setattr(review_module, "retrieve_context_for_pr", lambda *a, **k: [])

    result = review_module.review_pr("o", "r", 1, "sha", settings)

    assert result == {"status": "skipped_no_changes"}


def test_review_pr_full_happy_path_wires_everything_together(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    results = [
        RetrievalResult(
            file_path="a.py", function_name="fn", already_indexed=True, code="def fn():\n    pass\n"
        )
    ]

    monkeypatch.setattr(review_module, "get_installation_token_for_repo", lambda *a, **k: "tok")
    monkeypatch.setattr(review_module, "retrieve_context_for_pr", lambda *a, **k: results)
    monkeypatch.setattr(
        review_module,
        "assess_pr",
        lambda *a, **k: Assessment(risk_level="low", reasons=["x"], suggested_checks=["y"]),
    )

    posted = {}

    def fake_post(owner, repo, pr, body, marker, token=None):
        posted.update(owner=owner, repo=repo, pr=pr, body=body, marker=marker, token=token)
        return {"id": 42, "html_url": "https://github.com/o/r/pull/1#issuecomment-42"}

    monkeypatch.setattr(review_module, "post_or_update_comment", fake_post)

    result = review_module.review_pr("o", "r", 1, "sha123", settings)

    assert result["status"] == "reviewed"
    assert result["risk_level"] == "low"
    assert result["degraded"] is False
    assert result["functions_reviewed"] == 1
    assert result["comment_id"] == 42
    assert posted["token"] == "tok"
    assert posted["marker"] == review_module.MARKER
    assert posted["body"].startswith(review_module.MARKER)


def test_review_pr_uses_installation_token_for_retrieval(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(review_module, "get_installation_token_for_repo", lambda *a, **k: "the-real-token")

    seen = {}

    def fake_retrieve(owner, repo, pr, sha, db_url, model, token=None):
        seen["token"] = token
        return []

    monkeypatch.setattr(review_module, "retrieve_context_for_pr", fake_retrieve)

    review_module.review_pr("o", "r", 1, "sha", settings)

    assert seen["token"] == "the-real-token"
