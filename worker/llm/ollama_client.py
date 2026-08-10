"""Ollama client (SPEC §5.5 primary backend). Model name is configurable
via OLLAMA_MODEL, not hardcoded. `format: "json"` asks Ollama to constrain
generation to valid JSON — meaningfully improves reliability on a small
(1.5B-3B) quantized model, which is the whole reason to prefer it over
relying on prompt instructions alone.
"""
import httpx

DEFAULT_TIMEOUT_SECONDS = 30.0  # N4


def generate(prompt: str, host: str, model: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Returns the raw response text (not yet parsed/validated — that's
    schema.parse_assessment's job, kept separate so parse failures are
    handled identically regardless of which backend produced them).
    """
    resp = httpx.post(
        f"{host}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            # A risk-assessment tool that flip-flops between "low" and
            # "high" on an unchanged diff undermines its own credibility —
            # pin temperature=0 for reproducible output rather than
            # accepting default sampling variance.
            "options": {"temperature": 0},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["response"]
