"""Bounded, interchangeable HTTP advisors. Model names are chosen by the operator.

These transports propose JSON decisions; they do not execute tools or grant authority.
Credentials are read at request time and never stored in a transport or a proposal.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from etalon.judgment.advisor import AdvisorError, ClaudeCli, Transport

_PROVIDERS = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
}


@dataclass(frozen=True, slots=True)
class HttpAdvisor:
    """One non-streaming request per ask; Advisory owns the bounded retry count.

    OpenAI and DeepSeek use Chat Completions; Anthropic uses Messages. Custom
    gateways must implement the selected API. HTTP is allowed only on loopback.
    Responses with truncation, refusal, tool calls or missing text are not decisions.
    """

    provider: str
    model: str
    base_url: str = ""
    api_key_env: str = ""
    timeout: float = 60.0
    max_tokens: int = 2048

    def __post_init__(self) -> None:
        if self.provider not in _PROVIDERS:
            raise ValueError("provider must be openai, deepseek or anthropic")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("an explicit model name is required")
        if (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float))
                or not math.isfinite(self.timeout) or self.timeout <= 0):
            raise ValueError("timeout must be finite and positive")
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 65536:
            raise ValueError("max_tokens must be an integer between 1 and 65536")
        default_url, default_key = _PROVIDERS[self.provider]
        url = self.base_url or default_url
        parsed = urlsplit(url)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or any(c.isspace() for c in url)
                or (parsed.scheme != "https" and not (
                    parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}))):
            raise ValueError("base_url must use HTTPS (or loopback HTTP), without credentials/query/fragment")
        key_env = self.api_key_env or default_key
        if not key_env.isidentifier():
            raise ValueError("api_key_env must name an environment variable")
        object.__setattr__(self, "base_url", url.rstrip("/"))
        object.__setattr__(self, "api_key_env", key_env)

    @property
    def name(self) -> str:
        return f"{self.provider}-http:{self.model}"

    def ask(self, prompt: str) -> str:
        try:
            import httpx
        except ImportError:
            raise AdvisorError("HTTP advisors require pip install -e '.[llm]'") from None
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise AdvisorError(f"set {self.api_key_env} before using {self.provider}")
        if not isinstance(prompt, str) or not prompt.strip():
            raise AdvisorError("an advisor prompt must be nonempty text")
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}]}
        headers = {"Content-Type": "application/json"}
        if self.provider == "anthropic":
            route = "/messages"
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
            body["max_tokens"] = self.max_tokens
        else:
            route = "/chat/completions"
            headers["Authorization"] = "Bearer " + key
            body["response_format"] = {"type": "json_object"}
            body["max_completion_tokens" if self.provider == "openai" else "max_tokens"] = self.max_tokens
        # No SDK-level retries or redirects that could hide calls or forward credentials.
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
                response = client.post(self.base_url + route, headers=headers, json=body)
        except httpx.TransportError as error:
            raise AdvisorError(f"{self.provider} transport failed ({type(error).__name__})",
                               retryable=True) from None
        if response.status_code != 200:
            transient = response.status_code in {408, 429, 500, 502, 503, 504, 529}
            # The server body can echo input or keys. Only record status, never that body.
            raise AdvisorError(f"{self.provider} returned HTTP {response.status_code}",
                               retryable=transient) from None
        try:
            data = response.json()
            if self.provider == "anthropic":
                if data.get("stop_reason") != "end_turn":
                    raise AdvisorError("Anthropic response did not finish a text turn; check token limit/refusal")
                blocks = data["content"]
                if not isinstance(blocks, list) or any(b.get("type") != "text" for b in blocks):
                    raise AdvisorError("Anthropic response contains non-text blocks")
                content = "".join(b["text"] for b in blocks)
            else:
                choices = data["choices"]
                if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
                    raise AdvisorError("chat response is incomplete or contains tool calls; check token limit/refusal")
                message = choices[0]["message"]
                if message.get("refusal") or message.get("tool_calls"):
                    raise AdvisorError("chat response refused or requested tools instead of a decision")
                content = message["content"]
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AdvisorError(f"{self.provider} returned an invalid response envelope") from None
        if not isinstance(content, str) or not content.strip():
            raise AdvisorError(f"{self.provider} returned no decision text")
        return content


def transport_from_env() -> Transport:
    """Configure an advisor without embedding secrets in campaign files.

    ETALON_LLM_PROVIDER and ETALON_LLM_MODEL are required. API keys use the
    provider's standard environment variable; ETALON_LLM_BASE_URL is optional.
    claude-cli keeps using the operator's existing CLI login.
    """
    provider = os.environ.get("ETALON_LLM_PROVIDER", "").strip().lower()
    model = os.environ.get("ETALON_LLM_MODEL", "").strip()
    if not model:
        raise ValueError("set ETALON_LLM_MODEL to an available model in your account")
    if provider == "claude-cli":
        return ClaudeCli(model=model)
    return HttpAdvisor(provider, model, base_url=os.environ.get("ETALON_LLM_BASE_URL", ""))
