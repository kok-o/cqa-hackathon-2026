"""Small, explicit adapters for provider responses and billable token usage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ProviderOutputError(ValueError):
    def __init__(self, code: str, *, retryable: bool = True):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    complete: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cached_input_tokens": self.cached_input_tokens, "reasoning_tokens": self.reasoning_tokens,
                "cache_write_input_tokens": self.cache_write_input_tokens, "usage_complete": self.complete}


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def read_usage(body: dict[str, Any], api_style: str) -> TokenUsage:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()
    responses = api_style == "responses"
    inp = usage.get("input_tokens" if responses else "prompt_tokens")
    out = usage.get("output_tokens" if responses else "completion_tokens")
    complete = _count(inp) and _count(out)
    inp, out = (inp if _count(inp) else 0), (out if _count(out) else 0)
    if "total_tokens" in usage:
        complete = complete and _count(usage["total_tokens"]) and usage["total_tokens"] == inp + out
    input_details = usage.get("input_tokens_details" if responses else "prompt_tokens_details")
    output_details = usage.get("output_tokens_details" if responses else "completion_tokens_details")

    def detail(details: Any, key: str, upper: int, missing: int | None) -> int | None:
        nonlocal complete
        if details is None or isinstance(details, dict) and key not in details:
            return missing
        value = details.get(key) if isinstance(details, dict) else None
        if not _count(value) or value > upper:
            complete = False
            return None
        return value

    cached = detail(input_details, "cached_tokens", inp, None if responses else 0)
    reasoning = detail(output_details, "reasoning_tokens", out, None if responses else 0)
    written = detail(input_details, "cache_write_tokens", inp, 0)
    if cached is not None and written is not None and cached + written > inp:
        complete = False
    return TokenUsage(inp, out, cached, reasoning, written, complete)


def extract_content(body: dict[str, Any], api_style: str) -> str:
    """Accept only complete assistant text. Never execute or return tool output."""
    if api_style == "chat_completions":
        choice = body["choices"][0]
        message = choice["message"]
        if message.get("refusal"):
            raise ProviderOutputError("refusal", retryable=False)
        if choice.get("finish_reason") == "content_filter":
            raise ProviderOutputError("content_filter", retryable=False)
        if choice.get("finish_reason") == "length":
            raise ProviderOutputError("incomplete")
        if message.get("tool_calls") or message.get("function_call"):
            raise ProviderOutputError("unexpected_tool_call", retryable=False)
        if choice.get("finish_reason") not in {None, "stop"}:
            raise ProviderOutputError("invalid_response")
        content = message.get("content")
    else:
        # A filtered partial message often has status=incomplete itself. Inspect
        # the response-level cause before treating that message as token truncation.
        details = body.get("incomplete_details")
        if body.get("status") == "incomplete" and isinstance(details, dict) and details.get("reason") == "content_filter":
            raise ProviderOutputError("content_filter", retryable=False)
        output = body.get("output", [])
        if not isinstance(output, list):
            raise ProviderOutputError("invalid_response")
        pieces = []
        for item in output:
            if not isinstance(item, dict):
                raise ProviderOutputError("invalid_response")
            if item.get("type") == "reasoning":
                continue
            if item.get("type") != "message":
                raise ProviderOutputError("unexpected_tool_call", retryable=False)
            if item.get("role") != "assistant" or not isinstance(item.get("content"), list):
                raise ProviderOutputError("invalid_response")
            for part in item["content"]:
                if not isinstance(part, dict):
                    raise ProviderOutputError("invalid_response")
                if part.get("type") == "refusal":
                    raise ProviderOutputError("refusal", retryable=False)
                if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                    raise ProviderOutputError("invalid_response")
                pieces.append(part["text"])
            if item.get("status") not in {None, "completed"}:
                raise ProviderOutputError("incomplete")
        if body.get("status") == "incomplete":
            raise ProviderOutputError("incomplete")
        if body.get("status") != "completed" or body.get("error"):
            raise ProviderOutputError("provider_failed")
        content = "".join(pieces)
    if not isinstance(content, str) or not content.strip():
        raise ProviderOutputError("invalid_response")
    return content


def estimate_cost(usage: TokenUsage, input_price: float | None, output_price: float | None,
                  cached_price: float | None, cache_write_price: float | None) -> float | None:
    """Reasoning tokens are already included in output_tokens; never bill twice."""
    if not usage.complete or input_price is None or output_price is None:
        return None
    cached, written = usage.cached_input_tokens, usage.cache_write_input_tokens
    if cached is None or written is None or cached and cached_price is None or written and cache_write_price is None:
        return None
    return ((usage.input_tokens - cached - written) * input_price
            + cached * (cached_price or 0) + written * (cache_write_price or 0)
            + usage.output_tokens * output_price) / 1_000_000
