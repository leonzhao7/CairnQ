from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_LLM_CONFIG = Path("llm.json")
SUPPORTED_ENDPOINT = "/chat/completions"
DETAIL_LIMIT = 300


class LLMError(RuntimeError):
    """The LLM call could not be completed or returned an unusable response."""


class LLMConfig(BaseModel):
    """OpenAI-compatible chat completions config, loaded from a JSON file."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    model: str = Field(min_length=1)
    endpoint: str = SUPPORTED_ENDPOINT
    max_tokens: int = Field(default=65536, gt=0)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    timeout: int = Field(default=600, gt=0)
    retries: int = Field(default=2, ge=0)
    proxy: str | None = None
    user_agent: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        path = value.strip().rstrip("/")
        if not path.startswith("/"):
            path = "/" + path
        if path != SUPPORTED_ENDPOINT:
            raise ValueError(f"only {SUPPORTED_ENDPOINT} is supported, got: {value}")
        return path

    @classmethod
    def load(cls, path: Path) -> "LLMConfig":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("llm config must be a JSON object")
        return cls.model_validate(data)

    def chat_url(self) -> str:
        base = self.url.strip().rstrip("/")
        if base.endswith(self.endpoint):
            return base
        return base + self.endpoint

    def proxies(self) -> dict[str, str] | None:
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    def describe(self) -> str:
        return f"POST {self.chat_url()} (model={self.model}, max_tokens={self.max_tokens})"


def chat(config: LLMConfig, messages: list[dict[str, str]]) -> str:
    """Call the chat completions endpoint and return the assistant text.

    Retries transient failures (connection errors, 429, 5xx) up to config.retries times;
    a 4xx response is returned as a final error since retrying will not change it.
    """
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "content-type": "application/json",
        **config.extra_headers,
    }
    if config.user_agent:
        headers["User-Agent"] = config.user_agent
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "stream": False,
    }
    if config.top_p is not None:
        payload["top_p"] = config.top_p

    last_error = ""
    for attempt in range(config.retries + 1):
        try:
            response = requests.post(
                config.chat_url(),
                headers=headers,
                json=payload,
                timeout=config.timeout,
                proxies=config.proxies(),
            )
        except requests.RequestException as exc:
            last_error = _clip(str(exc))
        else:
            if 200 <= response.status_code < 300:
                return _extract_text(response)
            last_error = f"HTTP {response.status_code}: {_clip(response.text)}"
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable:
                break
        if attempt == config.retries:
            break
    raise LLMError(last_error or "llm request failed")


def _extract_text(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError as exc:
        raise LLMError(f"response is not JSON: {_clip(response.text)}") from exc
    if not isinstance(payload, dict):
        raise LLMError("response is not a JSON object")

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError(f"response has no choices: {_clip(json.dumps(payload, ensure_ascii=False))}")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise LLMError("response choice has no message")

    text = _flatten_content(message.get("content"))
    if not text:
        # Some reasoning models put the answer in reasoning_content when content is empty.
        text = _flatten_content(message.get("reasoning_content"))
    if not text:
        raise LLMError("response message has no text content")
    return text


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return "\n".join(parts).strip()
    return ""


def _clip(text: str) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= DETAIL_LIMIT else compact[:DETAIL_LIMIT] + "..."
