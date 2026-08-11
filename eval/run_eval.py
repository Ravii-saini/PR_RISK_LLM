"""Offline evaluation harness (SPEC §8): runs retrieval + LLM assessment
against eval/labeled_prs.json's real historical PRs, through both backends
independently (not the production fallback chain — assess_pr() retries
Ollama-then-Gemini on failure, which isn't what we want here; this runs
both against the identical full prompt for a fair side-by-side comparison),
without posting any comments. Scores retrieval quality, grounding, and
precision/recall against the ground-truth labels, and records latency/token
usage for the Ollama-vs-Gemini writeup.

Run: .venv/Scripts/python.exe eval/run_eval.py
Writes eval/results.json and prints a human-readable summary.
"""
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from worker.github_client import get_installation_token_for_repo
from worker.llm.prompt_builder import build_prompt
from worker.llm.schema import AssessmentParseError, parse_assessment
from worker.retrieval.query import retrieve_context_for_pr

LABELED_PRS_PATH = Path(__file__).parent / "labeled_prs.json"
RESULTS_PATH = Path(__file__).parent / "results.json"

# risk_level -> binary classification for precision/recall (SPEC §8 Q3).
# "medium" counts as an elevated/flagged prediction, same judgment call
# assess.py itself makes for the degraded-fallback default: it's "elevated
# scrutiny warranted," not "confirmed safe."
FLAGGED_LEVELS = {"medium", "high"}


def ollama_generate_with_usage(prompt: str, host: str, model: str, timeout: float = 30.0) -> dict:
    """Same request shape as worker.llm.ollama_client.generate, but also
    captures token usage (eval_count/prompt_eval_count) for the eval
    report — the production client discards these since assess.py doesn't
    need them.
    """
    resp = httpx.post(
        f"{host}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "text": data["response"],
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
    }


def gemini_generate_with_usage(prompt: str, api_key: str, model: str, timeout: float = 30.0) -> dict:
    resp = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usageMetadata", {})
    return {
        "text": data["candidates"][0]["content"]["parts"][0]["text"],
        "prompt_tokens": usage.get("promptTokenCount"),
        "output_tokens": usage.get("candidatesTokenCount"),
    }


def score_retrieval(entry: dict, results: list) -> dict:
    """SPEC §8 Q1: did retrieval surface the actually-relevant context?
    Automated check against the PR's own ground-truth basis — for
    incident_tag PRs, confirm the expected tag string actually came back;
    for all PRs, confirm the function was recognized as already-indexed
    with non-fabricated structural facts.
    """
    changed = {f"{r.file_path}::{r.function_name}" for r in results}
    expected = set(entry["changed_functions"])
    missing_functions = sorted(expected - changed)

    expected_tag = entry.get("expected_incident_tag")
    tag_found = None
    if expected_tag:
        all_tags = [t.get("tag", "") for r in results for t in r.incident_tags]
        tag_found = expected_tag in all_tags

    return {
        "expected_functions_found": sorted(expected & changed),
        "expected_functions_missing": missing_functions,
        "expected_incident_tag": expected_tag,
        "expected_incident_tag_found": tag_found,
        "functions_already_indexed": [r.already_indexed for r in results],
        "functions_with_callers": [len(r.callers) for r in results],
        "functions_with_tests": [r.has_tests for r in results],
    }


def score_grounding(assessment_text_blob: str, results: list) -> dict:
    """SPEC §8 Q2: does the assessment reference specific retrieved facts,
    or read as generic boilerplate? Heuristic proxy only (substring match
    of real caller names / incident tags / the has_tests fact against the
    reasons text) — supplements, doesn't replace, manual reading of the
    actual transcripts in the eval writeup.
    """
    blob = assessment_text_blob.lower()
    facts = []
    for r in results:
        facts.extend(c.lower() for c in r.callers)
        facts.extend(t.get("tag", "").lower() for t in r.incident_tags if t.get("tag"))
        if not r.has_tests:
            facts.append("no test coverage")
    facts = [f for f in facts if f]
    hits = [f for f in facts if f and f in blob]
    return {
        "candidate_facts": len(facts),
        "facts_referenced": len(set(hits)),
        "referenced": sorted(set(hits)),
    }


def run_backend(backend: str, prompt: str, settings) -> dict:
    start = time.monotonic()
    try:
        if backend == "ollama":
            raw = ollama_generate_with_usage(prompt, settings.ollama_host, settings.ollama_model)
        else:
            raw = gemini_generate_with_usage(prompt, settings.gemini_api_key, settings.gemini_model)
        latency = time.monotonic() - start
        assessment = parse_assessment(raw["text"])
        return {
            "latency_s": round(latency, 2),
            "risk_level": assessment.risk_level,
            "reasons": assessment.reasons,
            "suggested_checks": assessment.suggested_checks,
            "predicted_label": "risky" if assessment.risk_level in FLAGGED_LEVELS else "safe",
            "prompt_tokens": raw["prompt_tokens"],
            "output_tokens": raw["output_tokens"],
            "parse_ok": True,
            "error": None,
        }
    except AssessmentParseError as e:
        return {"latency_s": round(time.monotonic() - start, 2), "parse_ok": False, "error": str(e),
                "predicted_label": None, "risk_level": None, "reasons": [], "suggested_checks": [],
                "prompt_tokens": None, "output_tokens": None}
    except Exception as e:
        return {"latency_s": round(time.monotonic() - start, 2), "parse_ok": False, "error": f"{type(e).__name__}: {e}",
                "predicted_label": None, "risk_level": None, "reasons": [], "suggested_checks": [],
                "prompt_tokens": None, "output_tokens": None}


def compute_prf(pairs: list[tuple[str, str]]) -> dict:
    """pairs: list of (true_label, predicted_label), predicted_label may be None on failure."""
    scored = [(t, p) for t, p in pairs if p is not None]
    tp = sum(1 for t, p in scored if t == "risky" and p == "risky")
    fn = sum(1 for t, p in scored if t == "risky" and p == "safe")
    fp = sum(1 for t, p in scored if t == "safe" and p == "risky")
    tn = sum(1 for t, p in scored if t == "safe" and p == "safe")
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 2) if precision is not None else None,
        "recall": round(recall, 2) if recall is not None else None,
        "scored": len(scored), "failed": len(pairs) - len(scored),
    }


def latency_distribution(values: list[float]) -> dict:
    if not values:
        return {}
    s = sorted(values)
    return {
        "min": round(s[0], 2),
        "p50": round(statistics.median(s), 2),
        "p90": round(s[int(0.9 * (len(s) - 1))], 2),
        "max": round(s[-1], 2),
        "mean": round(statistics.mean(s), 2),
        "n": len(s),
    }


def main():
    settings = config.get_settings()
    with open(LABELED_PRS_PATH, encoding="utf-8") as f:
        labeled_prs = json.load(f)

    with open(settings.github_app_private_key_path) as f:
        private_key = f.read()
    token = get_installation_token_for_repo(
        settings.github_app_id, private_key, "Ravii-saini", "pr-risk-copilot"
    )

    results_out = []
    for i, entry in enumerate(labeled_prs, 1):
        pr = entry["pr_number"]
        print(f"[{i}/{len(labeled_prs)}] {entry['owner']}/{entry['repo']}#{pr} ({entry['label']}) ...", flush=True)

        t0 = time.monotonic()
        try:
            results = retrieve_context_for_pr(
                entry["owner"], entry["repo"], pr, entry["head_sha"],
                settings.database_url, settings.embedding_model, token=token,
            )
            retrieval_latency = time.monotonic() - t0
            retrieval_error = None
        except Exception as e:
            results = []
            retrieval_latency = time.monotonic() - t0
            retrieval_error = f"{type(e).__name__}: {e}"

        if not results:
            results_out.append({
                "pr_number": pr, "label": entry["label"], "label_basis": entry["label_basis"],
                "retrieval_latency_s": round(retrieval_latency, 2),
                "retrieval_error": retrieval_error or "no supported-language function changes retrieved",
                "retrieval": None, "ollama": None, "gemini": None,
            })
            print("    -> skipped (no changed functions retrieved)")
            continue

        prompt = build_prompt(entry["owner"], entry["repo"], pr, results, trimmed=False)
        retrieval_score = score_retrieval(entry, results)

        ollama_result = run_backend("ollama", prompt, settings)
        ollama_result["grounding"] = score_grounding(
            " ".join(ollama_result["reasons"] + ollama_result["suggested_checks"]), results
        )

        time.sleep(1.5)  # light pacing against Gemini free-tier rate limits
        gemini_result = run_backend("gemini", prompt, settings)
        gemini_result["grounding"] = score_grounding(
            " ".join(gemini_result["reasons"] + gemini_result["suggested_checks"]), results
        )

        e2e_ollama = retrieval_latency + ollama_result["latency_s"]

        results_out.append({
            "pr_number": pr, "label": entry["label"], "label_basis": entry["label_basis"],
            "retrieval_latency_s": round(retrieval_latency, 2),
            "retrieval_error": None,
            "end_to_end_ollama_path_s": round(e2e_ollama, 2),
            "retrieval": retrieval_score,
            "ollama": ollama_result,
            "gemini": gemini_result,
        })
        print(f"    -> ollama={ollama_result['risk_level']} ({ollama_result['latency_s']}s)  "
              f"gemini={gemini_result['risk_level']} ({gemini_result['latency_s']}s)")

    ollama_pairs = [(r["label"], r["ollama"]["predicted_label"]) for r in results_out if r["ollama"]]
    gemini_pairs = [(r["label"], r["gemini"]["predicted_label"]) for r in results_out if r["gemini"]]
    ollama_latencies = [r["ollama"]["latency_s"] for r in results_out if r["ollama"] and r["ollama"]["parse_ok"]]
    gemini_latencies = [r["gemini"]["latency_s"] for r in results_out if r["gemini"] and r["gemini"]["parse_ok"]]
    e2e_latencies = [r["end_to_end_ollama_path_s"] for r in results_out if "end_to_end_ollama_path_s" in r]

    incident_checks = [r["retrieval"]["expected_incident_tag_found"] for r in results_out
                        if r["retrieval"] and r["retrieval"]["expected_incident_tag_found"] is not None]

    summary = {
        "ollama": {**compute_prf(ollama_pairs),
                   "latency_s": latency_distribution(ollama_latencies),
                   "avg_prompt_tokens": round(statistics.mean(
                       [r["ollama"]["prompt_tokens"] for r in results_out
                        if r["ollama"] and r["ollama"]["prompt_tokens"]]), 0)
                   if any(r["ollama"] and r["ollama"]["prompt_tokens"] for r in results_out) else None,
                   "avg_output_tokens": round(statistics.mean(
                       [r["ollama"]["output_tokens"] for r in results_out
                        if r["ollama"] and r["ollama"]["output_tokens"]]), 0)
                   if any(r["ollama"] and r["ollama"]["output_tokens"] for r in results_out) else None},
        "gemini": {**compute_prf(gemini_pairs),
                   "latency_s": latency_distribution(gemini_latencies),
                   "avg_prompt_tokens": round(statistics.mean(
                       [r["gemini"]["prompt_tokens"] for r in results_out
                        if r["gemini"] and r["gemini"]["prompt_tokens"]]), 0)
                   if any(r["gemini"] and r["gemini"]["prompt_tokens"] for r in results_out) else None,
                   "avg_output_tokens": round(statistics.mean(
                       [r["gemini"]["output_tokens"] for r in results_out
                        if r["gemini"] and r["gemini"]["output_tokens"]]), 0)
                   if any(r["gemini"] and r["gemini"]["output_tokens"] for r in results_out) else None},
        "retrieval": {
            "incident_tag_prs_checked": len(incident_checks),
            "incident_tag_found_count": sum(1 for x in incident_checks if x),
            "prs_with_missing_functions": sum(
                1 for r in results_out if r["retrieval"] and r["retrieval"]["expected_functions_missing"]
            ),
        },
        "end_to_end_latency_ollama_path_s": latency_distribution(e2e_latencies),
    }

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "ollama_model": settings.ollama_model,
        "gemini_model": settings.gemini_model,
        "embedding_model": settings.embedding_model,
        "n_prs": len(labeled_prs),
        "results": results_out,
        "summary": summary,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n=== Summary ===")
    print(json.dumps(summary, indent=2))
    print(f"\nFull report written to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
