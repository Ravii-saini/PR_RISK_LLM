import scheduler.reindex_job as reindex_job


def test_run_once_indexes_every_configured_repo(monkeypatch):
    monkeypatch.setenv("REINDEX_REPOS", "octo/one,octo/two")
    calls = []

    def fake_index_repo(**kwargs):
        calls.append(kwargs["repo"])
        return {"status": "indexed", "chunks_indexed": 3}

    monkeypatch.setattr(reindex_job, "index_repo", fake_index_repo)

    results = reindex_job.run_once()

    assert calls == ["octo/one", "octo/two"]
    assert [r["status"] for r in results] == ["indexed", "indexed"]


def test_run_once_one_repo_failing_does_not_block_others(monkeypatch):
    monkeypatch.setenv("REINDEX_REPOS", "octo/broken,octo/fine")

    def fake_index_repo(**kwargs):
        if kwargs["repo"] == "octo/broken":
            raise RuntimeError("rate limited")
        return {"status": "indexed", "chunks_indexed": 1}

    monkeypatch.setattr(reindex_job, "index_repo", fake_index_repo)

    results = reindex_job.run_once()

    by_repo = {r["repo"]: r["status"] for r in results}
    assert by_repo == {"octo/broken": "failed", "octo/fine": "indexed"}


def test_run_once_passes_force_full_through(monkeypatch):
    monkeypatch.setenv("REINDEX_REPOS", "octo/one")
    seen = {}

    def fake_index_repo(**kwargs):
        seen["force_full"] = kwargs["force_full"]
        return {"status": "indexed"}

    monkeypatch.setattr(reindex_job, "index_repo", fake_index_repo)
    reindex_job.run_once(force_full=True)
    assert seen["force_full"] is True
