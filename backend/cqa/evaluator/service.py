"""Real provider-neutral evaluation. No fabricated scores or implicit audio use."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from ..models import ChecklistVersion, Conversation, CriterionResult, EvaluationDraft
from .provider import ProviderOutputError, TokenUsage, estimate_cost, extract_content, read_usage
from .validation import DraftValidationError, validate_draft

ROOT = Path(__file__).resolve().parents[3]
PROMPT_VERSION = "cqa-evaluator-v11"
PROMPT_PATH = ROOT / "prompts" / "evaluator.md"


class EvaluatorError(RuntimeError):
    """Safe error suitable for a public job status."""

    def __init__(self, message: str, *, code: str = "configuration", model_info: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.model_info = model_info or {}


@dataclass(frozen=True)
class EvaluationRun:
    draft: EvaluationDraft
    model_info: dict[str, Any]


@dataclass(frozen=True)
class Settings:
    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: float = 45.0
    json_mode: str = "json_object"
    max_tokens: int = 6000
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    api_style: str = "chat_completions"
    reasoning_effort: str | None = None
    cached_input_cost_per_million: float | None = None
    cache_write_input_cost_per_million: float | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env", override=False)
        try:
            def cost(name: str) -> float | None:
                value = os.getenv(name, "").strip()
                return float(value) if value else None
            result = cls(
                base_url=os.getenv("LLM_BASE_URL", "").strip().rstrip("/"),
                model=os.getenv("LLM_MODEL", "").strip(),
                api_key=os.getenv("LLM_API_KEY", "").strip(),
                timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "45")),
                json_mode=os.getenv("LLM_JSON_MODE", "json_object").strip(),
                max_tokens=int(os.getenv("LLM_MAX_TOKENS", "6000")),
                input_cost_per_million=cost("LLM_INPUT_COST_PER_MILLION"),
                output_cost_per_million=cost("LLM_OUTPUT_COST_PER_MILLION"),
                api_style=os.getenv("LLM_API_STYLE", "chat_completions").strip(),
                reasoning_effort=os.getenv("LLM_REASONING_EFFORT", "").strip() or None,
                cached_input_cost_per_million=cost("LLM_CACHED_INPUT_COST_PER_MILLION"),
                cache_write_input_cost_per_million=cost("LLM_CACHE_WRITE_INPUT_COST_PER_MILLION"),
            )
        except (ValueError, TypeError) as exc:
            raise EvaluatorError("Проверьте числовые параметры LLM в .env.") from exc
        result.validate()
        return result

    def validate(self) -> None:
        if not self.base_url or not self.model:
            raise EvaluatorError("Модель не подключена. Укажите LLM_BASE_URL и LLM_MODEL в .env; для облачного сервиса также LLM_API_KEY.")
        try:
            parsed = urlsplit(self.base_url)
        except ValueError as exc:
            raise EvaluatorError("Некорректный адрес LLM_BASE_URL.") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise EvaluatorError("LLM_BASE_URL должен быть HTTP(S)-адресом API без ключей и параметров URL.")
        if not 1 <= self.timeout_seconds <= 600 or type(self.max_tokens) is not int or not 128 <= self.max_tokens <= 128000:
            raise EvaluatorError("LLM_TIMEOUT_SECONDS: 1–600; LLM_MAX_TOKENS: 128–128000.")
        if self.json_mode not in {"json_object", "json_schema", "none"}:
            raise EvaluatorError("LLM_JSON_MODE: json_object, json_schema или none.")
        if self.api_style not in {"responses", "chat_completions"}:
            raise EvaluatorError("LLM_API_STYLE: responses или chat_completions.")
        if self.reasoning_effort not in {None, "none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise EvaluatorError("Неверное LLM_REASONING_EFFORT. Укажите поддерживаемый моделью уровень рассуждения.")
        if self.reasoning_effort in {"none", "minimal"} and self.model in {"gpt-6-astra", "gpt-6.1-sol"}:
            raise EvaluatorError("Эта модель не поддерживает none/minimal. Укажите low, medium, high, xhigh или max.")
        for price in (self.input_cost_per_million, self.output_cost_per_million,
                      self.cached_input_cost_per_million, self.cache_write_input_cost_per_million):
            if price is not None and not 0 <= price < float("inf"):
                raise EvaluatorError("Тариф модели должен быть конечным неотрицательным числом.")

    @property
    def provider(self) -> str:
        hostname = urlsplit(self.base_url).hostname
        if hostname in {"localhost", "127.0.0.1", "::1"}:
            return "local"
        return "openai" if hostname == "api.openai.com" else "openai_compatible"

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        for suffix in ("/chat/completions", "/responses"):
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break
        return base + ("/responses" if self.api_style == "responses" else "/chat/completions")


def get_evaluator_info(settings: Settings | None = None) -> dict[str, Any]:
    """Read configuration without making calls or revealing credentials/URLs."""
    try:
        settings = settings or Settings.from_env()
        settings.validate()
    except EvaluatorError as exc:
        return {"provider_configured": False, "provider": "unconfigured", "model": None,
                "mode": "unconfigured", "prompt_version": PROMPT_VERSION, "configuration_error": str(exc)}
    return {"provider_configured": True, "provider": settings.provider, "model": settings.model,
            "mode": "live", "prompt_version": PROMPT_VERSION,
            "json_mode": "json_schema" if settings.api_style == "responses" else settings.json_mode,
            "api_style": settings.api_style, "reasoning_effort": settings.reasoning_effort}


def output_schema() -> dict[str, Any]:
    evidence = {"type": "object", "additionalProperties": False,
                "properties": {"message_id": {"type": "string"}, "quote": {"type": "string"}},
                "required": ["message_id", "quote"]}
    span = {"type": "object", "additionalProperties": False,
            "properties": {"start_message_id": {"type": "string"}, "end_message_id": {"type": "string"}},
            "required": ["start_message_id", "end_message_id"]}
    properties = {
        "criterion_id": {"type": "string"},
        "status": {"type": "string", "enum": ["applicable", "not_applicable", "needs_review"]},
        "answer": {"anyOf": [{"type": "boolean"}, {"type": "number"}, {"type": "string"}, {"type": "null"}]},
        "explanation": {"type": "string", "description": "Краткое обоснование на русском языке, обычно 1–2 предложения. Согласуйте его с ответом, контекстом и порядком реплик."},
        "evidence": {"type": "array", "items": evidence},
        "checked_span": {"anyOf": [span, {"type": "null"}]}, "gross_error": {"type": "boolean"},
    }
    return {"type": "object", "additionalProperties": False,
            "properties": {"results": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": properties, "required": list(properties)}}}, "required": ["results"]}


def build_messages(conversation: Conversation, checklist: ChecklistVersion) -> list[dict[str, str]]:
    content = {"checklist": checklist.model_dump(mode="json"),
        "conversation": conversation.model_dump(mode="json", exclude={"audio_url"}),
        "available_materials": {"transcript": True, "audio": False}, "output_schema": output_schema(),
        "required_output_language": "Russian (ru) for every explanation; verbatim evidence quotes keep their original language."}
    return [{"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
            {"role": "user", "content": json.dumps(content, ensure_ascii=False, allow_nan=False)}]


def _parse_draft(content: str, conversation: Conversation, checklist: ChecklistVersion) -> EvaluationDraft:
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"results"} or not isinstance(data["results"], list):
        raise DraftValidationError("Expected a JSON object containing only results", code="response_shape", path=("results",))
    criteria = {criterion.criterion_id: criterion for criterion in checklist.criteria}
    for result_index, item in enumerate(data["results"]):
        if not isinstance(item, dict) or not isinstance(item.get("criterion_id"), str) or item["criterion_id"] not in criteria:
            raise DraftValidationError("Unknown criterion_id", code="criterion_unknown", path=("results", result_index, "criterion_id"))
        criterion = criteria[item["criterion_id"]]

        def invalid_type(message: str, field: str) -> DraftValidationError:
            return DraftValidationError(message, code="answer_type" if field == "answer" else "gross_error_type",
                criterion_id=criterion.criterion_id, path=("results", result_index, field))

        if type(item.get("gross_error", False)) is not bool:
            raise invalid_type("gross_error must be a boolean", "gross_error")
        if item.get("status") == "applicable":
            answer = item.get("answer")
            if criterion.answer_type == "boolean" and type(answer) is not bool:
                raise invalid_type("A boolean criterion requires true or false", "answer")
            if criterion.answer_type == "scale" and type(answer) not in {int, float}:
                raise invalid_type("A scale criterion requires a number", "answer")
            if criterion.answer_type == "choice" and type(answer) is not str:
                raise invalid_type("A choice criterion requires a string", "answer")
    draft = EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id,
        checklist_id=checklist.checklist_id, checklist_version=checklist.version,
        prompt_version=PROMPT_VERSION, results=data["results"])
    validate_draft(conversation, checklist, draft)
    draft.results.sort(key=lambda result: list(criteria).index(result.criterion_id))
    return draft


def _validation_diagnostic(error: Exception) -> dict[str, Any]:
    """Expose only server-defined codes/paths; exception messages can contain input."""
    if isinstance(error, DraftValidationError):
        detail: dict[str, Any] = {"code": error.code, "path": list(error.path)}
        if error.criterion_id is not None:
            detail["criterion_id"] = error.criterion_id
        if error.message_id is not None:
            detail["message_id"] = error.message_id
        return detail
    if isinstance(error, json.JSONDecodeError):
        return {"code": "json_decode", "type": "JSONDecodeError", "position": error.pos,
                "line": error.lineno, "column": error.colno}
    if isinstance(error, ValidationError):
        # Extra-field names in loc can themselves be malicious provider strings.
        fields = {"results", "criterion_id", "status", "answer", "explanation", "evidence", "message_id", "quote",
                  "checked_span", "start_message_id", "end_message_id", "gross_error", "conversation_id", "group_id",
                  "checklist_id", "checklist_version", "prompt_version", "bool", "float", "str"}
        errors = error.errors(include_input=False, include_context=False, include_url=False)
        safe_errors = []
        for item in errors[:3]:
            path = [part if type(part) is int and part >= 0 or isinstance(part, str) and part in fields else "*"
                    for part in item.get("loc", ())]
            code = item.get("type")
            safe_errors.append({"type": code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,80}", code) else "validation_error", "loc": path})
        return {"code": "schema_validation", "errors": safe_errors, "error_count": len(errors)}
    if isinstance(error, ProviderOutputError):
        return {"code": error.code}
    return {"code": "invalid_response", "type": type(error).__name__}


def _repair_instruction(diagnostic: dict[str, Any]) -> str:
    hints = {
        "quote_not_exact": "For the indicated criterion and message_id, choose a non-empty exact contiguous substring of that source message. Copy characters and punctuation verbatim. Do not normalize whitespace, dashes, quotation marks, spelling, or case; never invent a quote.",
        "evidence_message_unknown": "Use only message_id values present in the original transcript; choose the actual source message and copy its quote exactly.",
        "discrete_value_invalid": "For the indicated criterion choose exactly one of its configured allowed_values. Do not interpolate or invent a scale step.",
        "scale_out_of_bounds": "Use the indicated criterion's configured scale bounds and allowed_values, when specified.",
        "answer_type": "Use the indicated criterion's configured answer_type: JSON booleans for boolean criteria, numbers for scale criteria, and a configured string for choice criteria.",
        "choice_value_invalid": "Use one of the indicated criterion's configured choice keys exactly.",
        "na_not_allowed": "This criterion does not permit not_applicable. Reassess the transcript under its configured rule; do not invent evidence or use N/A to hide missing evidence.",
        "critical_flag_mismatch": "Make gross_error agree with the indicated criterion's configured critical_answer and its actual answer.",
        "gross_error_rule_missing": "Only criteria with a configured gross_error_rule may set gross_error=true.",
        "gross_error_status_invalid": "A not_applicable or needs_review result cannot establish gross_error=true.",
        "gross_error_type": "Use a JSON boolean for gross_error.",
        "checked_span_invalid": "Use existing transcript message IDs for checked_span, with the start no later than the end in original source order.",
        "explanation_empty": "Provide a non-empty concise Russian explanation for the indicated criterion.",
        "criterion_set_mismatch": "Include every configured criterion_id exactly once, without extra or duplicate criteria.",
        "criterion_unknown": "Use only criterion_id values from the configured checklist, each exactly once.",
        "schema_validation": "Correct the listed schema error types and locations. Follow the provided JSON schema, including status/answer/evidence consistency; do not add extra fields.",
        "json_decode": "Return syntactically complete JSON without markdown, comments, or trailing commas.",
        "response_shape": "Return one JSON object with only the results array at its top level.",
        "incomplete": "The preceding response was incomplete. Keep explanations concise and return the entire required JSON object within the output limit.",
    }
    metadata = json.dumps(diagnostic, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return ("The previous response failed server validation. Safe validation metadata: " + metadata + ". "
            + hints.get(diagnostic["code"], "Follow the provided schema and use only supported transcript evidence.")
            + " Return a fresh complete JSON object containing every requested criterion exactly once. "
              "Use exact source quotes; for an absent action use a valid checked_span. Preserve all rubric constraints. "
              "Do not include validation metadata or markdown in the answer. Keep explanations concise and in Russian.")


def build_request(settings: Settings, messages: list[dict[str, str]]) -> dict[str, Any]:
    """Construct a fresh request; Responses always uses strict structured output."""
    if settings.api_style == "responses":
        payload: dict[str, Any] = {"model": settings.model, "input": list(messages),
            "max_output_tokens": settings.max_tokens, "stream": False, "store": False,
            "tools": [], "tool_choice": "none",
            "text": {"format": {"type": "json_schema", "name": "cqa_evaluation",
                                "strict": True, "schema": output_schema()}}}
        if settings.reasoning_effort is not None:
            payload["reasoning"] = {"effort": settings.reasoning_effort}
        if settings.provider == "openai":
            payload["service_tier"] = "default"
        return payload
    payload = {"model": settings.model, "messages": list(messages), "stream": False}
    # Local non-reasoning providers keep their original temperature/max_tokens contract.
    if settings.reasoning_effort is not None or settings.model.startswith(("gpt-6", "gpt-5", "o1", "o3", "o4")):
        payload["max_completion_tokens"] = settings.max_tokens
        if settings.reasoning_effort is not None:
            payload["reasoning_effort"] = settings.reasoning_effort
    else:
        payload.update(temperature=0, max_tokens=settings.max_tokens)
    if settings.provider == "openai":
        payload["store"] = False
        payload["service_tier"] = "default"
    if settings.json_mode == "json_object":
        payload["response_format"] = {"type": "json_object"}
    elif settings.json_mode == "json_schema":
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "cqa_evaluation", "strict": True, "schema": output_schema()}}
    return payload


def _safe_identifier(value: Any, api_key: str = "") -> str | None:
    if not isinstance(value, str) or api_key and api_key in value:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,180}", value) else None


def _retry_after_seconds(value: str | None) -> int | float | None:
    """Parse only standard delay-seconds/HTTP-date, never echo the header text."""
    if not value or len(value) > 200:
        return None
    value = value.strip()
    if re.fullmatch(r"[0-9]+", value):
        return int(value)
    try:
        target = parsedate_to_datetime(value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def _attempt_cost(settings: Settings, usage: TokenUsage, response_service_tier: str | None = None) -> float | None:
    # Standard-rate reservations cannot price a different or unreported OpenAI
    # processing tier. Keep usage but leave billing unknown in that case.
    if settings.provider == "openai" and response_service_tier != "default":
        return None
    inp, out, cached, written = (settings.input_cost_per_million, settings.output_cost_per_million,
                                settings.cached_input_cost_per_million, settings.cache_write_input_cost_per_million)
    # Standard API tier rates on the official GPT-6 model pages (verified 2026-11-07).
    if settings.provider == "openai" and settings.model in {"gpt-6-astra", "gpt-6.1-sol"} and usage.input_tokens > 272000:
        inp, cached, written = (price * 2 if price is not None else None for price in (inp, cached, written))
        out = out * 1.5 if out is not None else None
    return estimate_cost(usage, inp, out, cached, written)


async def evaluate(conversation: Conversation, checklist: ChecklistVersion, *,
                   settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> EvaluationRun:
    """Run current input, retry at most once, and validate every result."""
    started = time.monotonic()
    missing_audio = [CriterionResult(criterion_id=c.criterion_id, status="needs_review", answer=None,
        explanation="Критерий требует прослушивания аудио. Этот запуск анализирует только транскрипт; звук не был обработан.",
        evidence=[], checked_span=None, gross_error=False) for c in checklist.criteria if c.requires_audio]
    text_criteria = [c for c in checklist.criteria if not c.requires_audio]
    if not text_criteria:
        draft = EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id,
            checklist_id=checklist.checklist_id, checklist_version=checklist.version,
            prompt_version=PROMPT_VERSION, results=missing_audio)
        validate_draft(conversation, checklist, draft)
        return EvaluationRun(draft, {"provider": "none", "model": None, "mode": "audio_unavailable",
            "prompt_version": PROMPT_VERSION, "attempts": 0, "prompt_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "usage_complete": True,
            "elapsed_ms": round((time.monotonic() - started) * 1000), "estimated_cost": 0,
            "audio_processed": False})
    settings = settings or Settings.from_env()
    settings.validate()
    text_checklist = checklist.model_copy(update={"criteria": text_criteria})
    messages = build_messages(conversation, text_checklist)
    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = "Bearer " + settings.api_key
    owned_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.timeout_seconds, follow_redirects=False)
    attempts: list[dict[str, Any]] = []
    last_error = "Модель не вернула корректную оценку."
    last_code = "invalid_response"
    input_hash = sha256(json.dumps({"conversation": conversation.model_dump(mode="json"),
        "checklist": checklist.model_dump(mode="json")}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def model_info() -> dict[str, Any]:
        prompt_tokens = sum(a["input_tokens"] for a in attempts)
        completion_tokens = sum(a["output_tokens"] for a in attempts)
        usage_complete = all(a["usage_complete"] for a in attempts)
        costs = [a["estimated_cost"] for a in attempts]
        response_models = list(dict.fromkeys(a["response_model"] for a in attempts if a.get("response_model")))
        response_tiers = list(dict.fromkeys(a["response_service_tier"] for a in attempts if a.get("response_service_tier")))
        info = {"provider": settings.provider, "model": settings.model, "requested_model": settings.model,
            "response_models": response_models, "mode": "live", "prompt_version": PROMPT_VERSION,
            "requested_service_tier": "default" if settings.provider == "openai" else None,
            "response_service_tiers": response_tiers,
            "service_tier_verified": all(a.get("response_service_tier") == "default" for a in attempts) if settings.provider == "openai" else None,
            "json_mode": "json_schema" if settings.api_style == "responses" else settings.json_mode,
            "api_style": settings.api_style, "reasoning_effort": settings.reasoning_effort,
            "max_tokens": settings.max_tokens, "attempts": len(attempts), "retries": max(0, len(attempts) - 1),
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "input_tokens": prompt_tokens, "output_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens, "usage_complete": usage_complete,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "estimated_cost": round(sum(costs), 8) if all(cost is not None for cost in costs) else None,
            "known_estimated_cost": round(sum(cost for cost in costs if cost is not None), 8),
            "cost_currency": "USD", "cost_basis": "configured_standard_token_rates",
            "input_hash": input_hash, "audio_processed": False,
            "attempt_details": [dict(a) for a in attempts]}
        for key in ("cached_input_tokens", "reasoning_tokens", "cache_write_input_tokens"):
            info[key] = sum(a[key] for a in attempts if a[key] is not None)
            info[key + "_complete"] = all(a[key] is not None for a in attempts)
        if settings.api_style == "responses":
            info["max_output_tokens"] = settings.max_tokens
        elif "temperature" in build_request(settings, []):
            info["temperature"] = 0
        return info

    def failure(message: str, code: str) -> EvaluatorError:
        return EvaluatorError(message, code=code, model_info=model_info())

    try:
        for attempt in range(1, 3):
            attempt_started = time.monotonic()
            detail: dict[str, Any] = {"attempt": attempt, **TokenUsage().as_dict(),
                "estimated_cost": None, "response_model": None, "response_id": None,
                "response_service_tier": None,
                "status": "pending", "http_status": None}
            attempts.append(detail)
            try:
                # HTTPX's timeout is per network phase/inactivity. Bound the whole
                # request as well so a slow trickle cannot hold a job indefinitely.
                async with asyncio.timeout(settings.timeout_seconds):
                    response = await http.post(settings.endpoint, json=build_request(settings, messages), headers=headers,
                                               timeout=settings.timeout_seconds, follow_redirects=False)
            except (httpx.RequestError, TimeoutError) as exc:
                last_code = "timeout" if isinstance(exc, (httpx.TimeoutException, TimeoutError)) else "network"
                last_error = "Модель не ответила вовремя." if last_code == "timeout" else "Не удалось соединиться с моделью."
                detail.update(status=last_code, elapsed_ms=round((time.monotonic() - attempt_started) * 1000))
                if attempt < 2:
                    await asyncio.sleep(0.2)
                    continue
                break
            detail.update(http_status=response.status_code, elapsed_ms=round((time.monotonic() - attempt_started) * 1000))
            response_json_error: Exception | None = None
            try:
                body = response.json()
            except (ValueError, UnicodeError) as exc:
                body = None
                response_json_error = exc
            if isinstance(body, dict):
                usage = read_usage(body, settings.api_style)
                detail.update(usage.as_dict())
                tier = body.get("service_tier")
                response_tier = tier if isinstance(tier, str) and tier in {"auto", "default", "flex", "scale", "priority", "fast", "ultrafast"} else None
                detail.update(estimated_cost=_attempt_cost(settings, usage, response_tier),
                              response_service_tier=response_tier,
                              response_model=_safe_identifier(body.get("model"), settings.api_key),
                              response_id=_safe_identifier(body.get("id"), settings.api_key))
            if response.status_code >= 300:
                if response.status_code in {401, 403}:
                    detail["status"] = "auth"
                    raise failure("Провайдер отклонил авторизацию. Проверьте ключ и доступ к модели.", "auth")
                if response.status_code in {400, 404, 422}:
                    detail["status"] = "request"
                    raise failure("Провайдер отклонил запрос. Проверьте LLM_BASE_URL, LLM_MODEL, LLM_API_STYLE и LLM_REASONING_EFFORT.", "request")
                detail["status"] = last_code = "http"
                last_error = f"Модель временно недоступна (HTTP {response.status_code})."
                if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < 2:
                    delay = _retry_after_seconds(response.headers.get("retry-after"))
                    if delay is not None:
                        detail["retry_after_seconds"] = delay
                        if delay > min(settings.timeout_seconds, 30.0):
                            # A long cooldown must not cause an early retry or an
                            # unbounded wait. A later explicit run can retry it.
                            detail["retry_skipped"] = "retry_after_exceeds_limit"
                            break
                    await asyncio.sleep(delay if delay is not None else 0.3)
                    continue
                break
            try:
                if response_json_error is not None:
                    raise response_json_error
                if not isinstance(body, dict):
                    raise DraftValidationError("Expected response object", code="response_shape")
                content = extract_content(body, settings.api_style)
                draft = _parse_draft(content, conversation, text_checklist)
                results = {r.criterion_id: r for r in draft.results + missing_audio}
                draft.results = [results[c.criterion_id] for c in checklist.criteria]
                validate_draft(conversation, checklist, draft)
            except (ValueError, KeyError, IndexError, TypeError, AttributeError, ValidationError) as exc:
                last_code = exc.code if isinstance(exc, ProviderOutputError) else "invalid_response"
                detail["status"] = last_code
                detail["validation_error"] = _validation_diagnostic(exc)
                errors = {
                    "refusal": "Модель отказалась оценивать этот диалог. Требуется ручная проверка.",
                    "content_filter": "Ответ модели остановлен фильтром. Требуется ручная проверка.",
                    "incomplete": "Модель не завершила ответ. Увеличьте LLM_MAX_TOKENS или проверьте состояние провайдера.",
                    "unexpected_tool_call": "Модель вернула неподдерживаемый вызов инструмента. Оценка не сохранена.",
                    "provider_failed": "Провайдер не завершил генерацию ответа.",
                }
                last_error = errors.get(last_code, "Модель дважды вернула ответ с неверным форматом или неподтверждёнными доказательствами.")
                if isinstance(exc, ProviderOutputError) and not exc.retryable:
                    raise failure(last_error, last_code) from None
                if attempt < 2:
                    messages.append({"role": "user", "content": _repair_instruction(detail["validation_error"])})
                    continue
                break
            detail["status"] = "completed"
            return EvaluationRun(draft, model_info())
    finally:
        if owned_client:
            await http.aclose()
    raise failure(last_error, last_code)
