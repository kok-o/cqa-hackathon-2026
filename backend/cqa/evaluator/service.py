"""Real provider-neutral evaluation. No fabricated scores or implicit audio use."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from ..models import ChecklistVersion, Conversation, CriterionResult, EvaluationDraft
from .validation import validate_draft

ROOT = Path(__file__).resolve().parents[3]
PROMPT_VERSION = "cqa-evaluator-v2"
PROMPT_PATH = ROOT / "prompts" / "evaluator.md"


class EvaluatorError(RuntimeError):
    """Safe error suitable for a public job status."""


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
        if not 1 <= self.timeout_seconds <= 600 or not 128 <= self.max_tokens <= 32768:
            raise EvaluatorError("LLM_TIMEOUT_SECONDS: 1–600; LLM_MAX_TOKENS: 128–32768.")
        if self.json_mode not in {"json_object", "json_schema", "none"}:
            raise EvaluatorError("LLM_JSON_MODE: json_object, json_schema или none.")
        for price in (self.input_cost_per_million, self.output_cost_per_million):
            if price is not None and not 0 <= price < float("inf"):
                raise EvaluatorError("Тариф модели должен быть конечным неотрицательным числом.")

    @property
    def provider(self) -> str:
        return "local" if urlsplit(self.base_url).hostname in {"localhost", "127.0.0.1", "::1"} else "openai_compatible"

    @property
    def endpoint(self) -> str:
        return self.base_url if self.base_url.endswith("/chat/completions") else self.base_url + "/chat/completions"


def get_evaluator_info() -> dict[str, Any]:
    """Read configuration without making calls or revealing credentials/URLs."""
    try:
        settings = Settings.from_env()
    except EvaluatorError as exc:
        return {"provider_configured": False, "provider": "unconfigured", "model": None,
                "mode": "unconfigured", "prompt_version": PROMPT_VERSION, "configuration_error": str(exc)}
    return {"provider_configured": True, "provider": settings.provider, "model": settings.model,
            "mode": "live", "prompt_version": PROMPT_VERSION, "json_mode": settings.json_mode}


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
        "explanation": {"type": "string"}, "evidence": {"type": "array", "items": evidence},
        "checked_span": {"anyOf": [span, {"type": "null"}]}, "gross_error": {"type": "boolean"},
    }
    return {"type": "object", "additionalProperties": False,
            "properties": {"results": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": properties, "required": list(properties)}}}, "required": ["results"]}


def build_messages(conversation: Conversation, checklist: ChecklistVersion) -> list[dict[str, str]]:
    content = {"checklist": checklist.model_dump(mode="json"),
        "conversation": conversation.model_dump(mode="json", exclude={"audio_url"}),
        "available_materials": {"transcript": True, "audio": False}, "output_schema": output_schema()}
    return [{"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
            {"role": "user", "content": json.dumps(content, ensure_ascii=False, allow_nan=False)}]


def _parse_draft(content: str, conversation: Conversation, checklist: ChecklistVersion) -> EvaluationDraft:
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"results"} or not isinstance(data["results"], list):
        raise ValueError("Expected a JSON object containing only results")
    criteria = {criterion.criterion_id: criterion for criterion in checklist.criteria}
    for item in data["results"]:
        if not isinstance(item, dict) or item.get("criterion_id") not in criteria:
            raise ValueError("Unknown criterion_id")
        criterion = criteria[item["criterion_id"]]
        if type(item.get("gross_error", False)) is not bool:
            raise ValueError("gross_error must be a boolean")
        if item.get("status") == "applicable":
            answer = item.get("answer")
            if criterion.answer_type == "boolean" and type(answer) is not bool:
                raise ValueError("A boolean criterion requires true or false")
            if criterion.answer_type == "scale" and type(answer) not in {int, float}:
                raise ValueError("A scale criterion requires a number")
            if criterion.answer_type == "choice" and type(answer) is not str:
                raise ValueError("A choice criterion requires a string")
    draft = EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id,
        checklist_id=checklist.checklist_id, checklist_version=checklist.version,
        prompt_version=PROMPT_VERSION, results=data["results"])
    validate_draft(conversation, checklist, draft)
    draft.results.sort(key=lambda result: list(criteria).index(result.criterion_id))
    return draft


def _usage(response: dict[str, Any]) -> tuple[int, int, bool]:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, False
    inp, out = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if type(inp) is not int or type(out) is not int or inp < 0 or out < 0:
        return 0, 0, False
    return inp, out, True


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
    payload: dict[str, Any] = {"model": settings.model, "messages": messages,
        "temperature": 0, "max_tokens": settings.max_tokens, "stream": False}
    if settings.json_mode == "json_object":
        payload["response_format"] = {"type": "json_object"}
    elif settings.json_mode == "json_schema":
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "cqa_evaluation", "strict": True, "schema": output_schema()}}
    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = "Bearer " + settings.api_key
    owned_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.timeout_seconds, follow_redirects=False)
    prompt_tokens = completion_tokens = 0
    usage_complete = True
    last_error = "Модель не вернула корректную оценку."
    try:
        for attempt in range(1, 3):
            try:
                response = await http.post(settings.endpoint, json=payload, headers=headers,
                                           timeout=settings.timeout_seconds, follow_redirects=False)
            except httpx.RequestError as exc:
                usage_complete = False
                last_error = "Модель не ответила вовремя." if isinstance(exc, httpx.TimeoutException) else "Не удалось соединиться с моделью."
                if attempt < 2:
                    await asyncio.sleep(0.2)
                    continue
                break
            if response.status_code >= 400:
                usage_complete = False
                if response.status_code in {401, 403}:
                    raise EvaluatorError("Провайдер отклонил авторизацию. Проверьте ключ и доступ к модели.")
                if response.status_code in {400, 404, 422}:
                    raise EvaluatorError("Провайдер отклонил запрос. Проверьте LLM_BASE_URL, LLM_MODEL и LLM_JSON_MODE.")
                last_error = f"Модель временно недоступна (HTTP {response.status_code})."
                if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < 2:
                    await asyncio.sleep(0.3)
                    continue
                break
            attempt_usage_seen = False
            try:
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("Expected response object")
                inp, out, known = _usage(body)
                attempt_usage_seen = True
                prompt_tokens += inp
                completion_tokens += out
                usage_complete = usage_complete and known
                choice = body["choices"][0]
                message = choice["message"]
                if message.get("refusal"):
                    raise EvaluatorError("Модель отказалась оценивать этот диалог. Требуется ручная проверка.")
                if choice.get("finish_reason") in {"length", "content_filter"}:
                    raise ValueError("Model output was truncated or filtered")
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("Expected non-empty JSON content")
                draft = _parse_draft(content, conversation, text_checklist)
                results = {r.criterion_id: r for r in draft.results + missing_audio}
                draft.results = [results[c.criterion_id] for c in checklist.criteria]
                validate_draft(conversation, checklist, draft)
            except (ValueError, KeyError, IndexError, TypeError, AttributeError, ValidationError):
                if not attempt_usage_seen:
                    usage_complete = False
                last_error = "Модель дважды вернула ответ с неверным форматом или неподтверждёнными доказательствами."
                if attempt < 2:
                    messages.append({"role": "user", "content":
                        "Previous response failed validation. Return a fresh complete JSON object with every requested criterion exactly once. "
                        "Use exact message IDs and verbatim non-empty quotes from the transcript, proper answer types and bounds. "
                        "For an absent action give a valid checked_span. Do not add metadata or markdown. Keep explanations concise."})
                    continue
                break
            cost = None
            if usage_complete and settings.input_cost_per_million is not None and settings.output_cost_per_million is not None:
                cost = round((prompt_tokens * settings.input_cost_per_million + completion_tokens * settings.output_cost_per_million) / 1_000_000, 8)
            input_hash = sha256(json.dumps({"conversation": conversation.model_dump(mode="json"),
                "checklist": checklist.model_dump(mode="json")}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            return EvaluationRun(draft, {"provider": settings.provider, "model": settings.model,
                "mode": "live", "prompt_version": PROMPT_VERSION, "json_mode": settings.json_mode,
                "temperature": 0, "max_tokens": settings.max_tokens, "attempts": attempt,
                "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens, "usage_complete": usage_complete,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "estimated_cost": cost, "input_hash": input_hash, "audio_processed": False})
    finally:
        if owned_client:
            await http.aclose()
    raise EvaluatorError(last_error)
