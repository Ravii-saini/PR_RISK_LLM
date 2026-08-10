"""Google Gemini API free-tier client (SPEC §5.5 fallback backend).
Same provider-agnostic shape as ollama_client.generate: takes a prompt,
returns raw response text for schema.parse_assessment to validate.

Model pinned via GEMINI_MODEL (resolved to gemini-3.1-flash-lite — see
PROBLEMS.md for why the SPEC's original example model, gemini-2.0-flash,
had 0 free-tier quota on this account).
"""
import httpx

DEFAULT_TIMEOUT_SECONDS = 30.0
_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def generate(prompt: str, api_key: str, model: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    resp = httpx.post(
        f"{_API_BASE}/{model}:generateContent",
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]
