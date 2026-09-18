"""Provider envelopes must never turn partial output or API errors into decisions."""

from __future__ import annotations

import json

import httpx
import pytest

from etalon.judgment.advisor import AdvisorError, Advisory, Scripted
from etalon.judgment.proposal import Act
from etalon.judgment.providers import HttpAdvisor, transport_from_env


def mock_http(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))
    for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test-secret-not-for-logs")


@pytest.mark.parametrize("provider", ["openai", "deepseek", "anthropic"])
def test_real_advisory_consumes_each_provider_envelope(monkeypatch, provider):
    seen = []

    def handler(request):
        seen.append(request)
        reply = '{"parent_ids":["a"],"reason":"One bounded selection."}'
        if provider == "anthropic":
            return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": reply}]})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": reply}}]})

    mock_http(monkeypatch, handler)
    transport = HttpAdvisor(provider, "operator-selected-model")
    proposal = Advisory(transport, transport.name).ask(Act.SPEND, "Choose one", required=["parent_ids"])
    assert proposal.payload["parent_ids"] == ["a"] and proposal.payload["_attempts"] == 1
    assert "test-secret" not in repr(proposal) + repr(transport)
    body = json.loads(seen[0].content)
    assert body["model"] == "operator-selected-model" and "temperature" not in body
    if provider == "anthropic":
        assert seen[0].url.path == "/v1/messages"
        assert seen[0].headers["anthropic-version"] == "2023-06-01"
    else:
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_completion_tokens" if provider == "openai" else "max_tokens"] == 2048


@pytest.mark.parametrize("status,attempts", [(401, 1), (403, 1), (400, 1), (429, 2), (503, 2)])
def test_failures_are_sanitized_and_retries_are_bounded(monkeypatch, status, attempts):
    calls = []
    mock_http(monkeypatch, lambda req: calls.append(req) or httpx.Response(status, text="test-secret-not-for-logs"))
    monkeypatch.setattr("etalon.judgment.advisor.time.sleep", lambda _: None)
    with pytest.raises(AdvisorError) as error:
        Advisory(HttpAdvisor("openai", "chosen"), "chosen").ask(Act.SPEND, "Pick", required=["ids"])
    assert len(calls) == attempts
    assert "test-secret" not in str(error.value)


def test_transient_failure_is_counted_in_successful_proposal(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ids":[]}'}}]})

    mock_http(monkeypatch, handler)
    monkeypatch.setattr("etalon.judgment.advisor.time.sleep", lambda _: None)
    result = Advisory(HttpAdvisor("deepseek", "chosen"), "chosen").ask(Act.SPEND, "Pick", required=["ids"])
    assert result.payload["_attempts"] == 2 and "429" in result.payload["_earlier_attempts"][0]


@pytest.mark.parametrize("payload", [
    {"choices": [{"finish_reason": "length", "message": {"content": '{"ids":[]}'}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": '{}', "refusal": "no"}}]},
    {"choices": [{"finish_reason": "tool_calls", "message": {"content": '{}'}}]},
    {"choices": []}, {"choices": None}, [], {"error": "wrong envelope"},
])
def test_no_incomplete_or_unexpected_chat_response_is_admitted(monkeypatch, payload):
    mock_http(monkeypatch, lambda _: httpx.Response(200, json=payload))
    with pytest.raises(AdvisorError):
        HttpAdvisor("openai", "chosen").ask("Return JSON")


@pytest.mark.parametrize("payload", [
    {"stop_reason": "max_tokens", "content": [{"type": "text", "text": '{}'}]},
    {"stop_reason": "end_turn", "content": [{"type": "tool_use", "text": '{}'}]},
    {"stop_reason": "end_turn", "content": []},
    {"stop_reason": "end_turn", "content": [None]},
])
def test_anthropic_incomplete_output_is_refused(monkeypatch, payload):
    mock_http(monkeypatch, lambda _: httpx.Response(200, json=payload))
    with pytest.raises(AdvisorError):
        HttpAdvisor("anthropic", "chosen").ask("Return JSON")


def test_missing_key_never_calls_the_network(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: pytest.fail("network must not be touched"))
    with pytest.raises(AdvisorError, match="OPENAI_API_KEY"):
        HttpAdvisor("openai", "chosen").ask("JSON")


@pytest.mark.parametrize("url", ["http://remote.example/v1", "https://user:secret@example.com", "https://example.com?key=secret", "file:///tmp/api"])
def test_invalid_credential_destinations_are_refused(url):
    with pytest.raises(ValueError, match="base_url"):
        HttpAdvisor("openai", "chosen", base_url=url)


def test_environment_factory_requires_explicit_model(monkeypatch):
    monkeypatch.setenv("ETALON_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("ETALON_LLM_MODEL", "chosen")
    monkeypatch.delenv("ETALON_LLM_BASE_URL", raising=False)
    assert transport_from_env().model == "chosen"
    monkeypatch.delenv("ETALON_LLM_MODEL")
    with pytest.raises(ValueError, match="ETALON_LLM_MODEL"):
        transport_from_env()


@pytest.mark.parametrize("reply", ['{"ids":[],"ids":["x"]}', '{"ids":[NaN]}', '{"ids":[1e999]}', '{"ids":[],"reason":true}'])
def test_ambiguous_or_nonfinite_model_json_is_refused(reply):
    with pytest.raises(AdvisorError):
        Advisory(Scripted({"spend": reply}), "test", attempts=1).ask(Act.SPEND, "Pick", required=["ids"])
