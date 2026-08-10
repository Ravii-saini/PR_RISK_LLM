"""Request-shape tests for the two LLM clients (mocked HTTP via respx) —
distinct from tests/test_assess.py, which mocks at the module-function
level to test orchestration logic, not what actually goes over the wire.
"""
import json

import httpx
import pytest
import respx

from worker.llm.hosted_client import generate as gemini_generate
from worker.llm.ollama_client import generate as ollama_generate


@respx.mock
def test_ollama_generate_sends_json_format_and_temperature_zero():
    route = respx.post("http://localhost:11434/api/generate").mock(
        return_value=httpx.Response(200, json={"response": '{"risk_level": "low"}'})
    )

    result = ollama_generate("my prompt", "http://localhost:11434", "qwen2.5-coder:1.5b")

    assert result == '{"risk_level": "low"}'
    payload = json.loads(route.calls[0].request.content)
    assert payload["model"] == "qwen2.5-coder:1.5b"
    assert payload["prompt"] == "my prompt"
    assert payload["format"] == "json"
    assert payload["options"]["temperature"] == 0
    assert payload["stream"] is False


@respx.mock
def test_ollama_generate_raises_on_http_error():
    respx.post("http://localhost:11434/api/generate").mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        ollama_generate("prompt", "http://localhost:11434", "model")


@respx.mock
def test_gemini_generate_sends_key_and_json_mime_type():
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": '{"risk_level": "high"}'}]}}]},
        )
    )

    result = gemini_generate("my prompt", "test-api-key", "gemini-3.1-flash-lite")

    assert result == '{"risk_level": "high"}'
    sent = route.calls[0].request
    assert sent.url.params["key"] == "test-api-key"
    payload = json.loads(sent.content)
    assert payload["contents"][0]["parts"][0]["text"] == "my prompt"
    assert payload["generationConfig"]["temperature"] == 0
    assert payload["generationConfig"]["responseMimeType"] == "application/json"


@respx.mock
def test_gemini_generate_raises_on_http_error():
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
    ).mock(return_value=httpx.Response(400, json={"error": "bad key"}))

    with pytest.raises(httpx.HTTPStatusError):
        gemini_generate("prompt", "bad-key", "gemini-3.1-flash-lite")
