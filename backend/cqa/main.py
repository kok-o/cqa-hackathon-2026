"""Single-process local CQA API and built frontend."""

import asyncio
from contextlib import asynccontextmanager
import inspect
import json
import logging
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import Field, ValidationError, field_validator, model_validator

from .audio import AudioSourceError, resolve_audio
from .importer import normalize
from .models import ChecklistVersion, Contract, Conversation, Criterion, CriterionResult, DatasetExport
from .storage import ConflictError, NotFoundError, Store


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)
logger = logging.getLogger("cqa")


class ImportRequest(Contract):
    dataset: DatasetExport | None = None
    source: Literal["dev", "demo"] | None = None

    @model_validator(mode="after")
    def one_source(self):
        if (self.dataset is None) == (self.source is None):
            raise ValueError("Укажите dataset или source")
        return self


class ChecklistConfiguration(Contract):
    title: str = Field(min_length=1, max_length=300)
    provenance: Literal["synthetic_example", "organizer"]
    criteria: list[Criterion] = Field(min_length=1, max_length=100)


class JobRequest(Contract):
    conversation_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    checklist_id: str = Field(min_length=1)
    checklist_version: int = Field(ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class AppealRequest(Contract):
    criterion_id: str = Field(min_length=1)
    comment: str = Field(min_length=1, max_length=5000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("comment")
    @classmethod
    def meaningful_comment(cls, value):
        if not value.strip():
            raise ValueError("Напишите причину апелляции")
        return value.strip()


class DecisionRequest(Contract):
    action: Literal["uphold", "replace"]
    reason: str = Field(min_length=1, max_length=5000)
    replacement_result: CriterionResult | None = None
    expected_revision: int = Field(ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        if not value.strip():
            raise ValueError("Обоснуйте решение")
        return value.strip()

    @model_validator(mode="after")
    def validate_replacement(self):
        if self.action == "replace" and self.replacement_result is None:
            raise ValueError("Для изменения оценки нужен новый результат критерия")
        if self.action == "uphold" and self.replacement_result is not None:
            raise ValueError("Подтверждение оценки не принимает замену результата")
        return self


def _default_database() -> Path:
    configured = Path(os.getenv("CQA_DATABASE_PATH", "data/runtime/cqa.sqlite3"))
    return configured if configured.is_absolute() else ROOT / configured


def _safe_evaluator_info() -> dict:
    try:
        from .evaluator.service import get_evaluator_info
        return get_evaluator_info()
    except ImportError:
        return {"provider_configured": False, "provider": None, "model": None, "mode": "unconfigured", "prompt_version": "unavailable"}


def create_app(database_path: str | Path | None = None, evaluator=None, evaluator_info: dict | None = None,
               *, checklist_path: str | Path | None = None) -> FastAPI:
    """Factory permits isolated test databases and an explicitly injected evaluator."""
    store = Store(database_path if database_path is not None else _default_database())
    try:
        concurrency = max(1, min(8, int(os.getenv("LLM_CONCURRENCY", "2"))))
    except ValueError:
        concurrency = 2

    @asynccontextmanager
    async def lifespan(application):
        seed_path = Path(checklist_path) if checklist_path is not None else ROOT / "fixtures/checklist.official.json"
        seed = ChecklistVersion.model_validate_json(seed_path.read_text(encoding="utf-8"))
        store.initialize(seed)
        application.state.evaluator_semaphore = asyncio.Semaphore(concurrency)
        yield
        store.close()

    application = FastAPI(title="CQA — оценка диалогов", version="1.0.0", lifespan=lifespan)
    application.state.store = store

    @application.exception_handler(NotFoundError)
    async def not_found(request: Request, error):
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @application.exception_handler(ConflictError)
    async def conflict(request: Request, error):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error):
        fields = [{"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]} for item in error.errors()]
        return JSONResponse(status_code=422, content={"detail": "Некорректные данные запроса", "errors": fields})

    @application.exception_handler(ValidationError)
    async def invalid_contract(request: Request, error):
        return JSONResponse(status_code=422, content={"detail": "Конфигурация не прошла проверку. Проверьте критерии, типы и шкалы."})

    @application.exception_handler(ValueError)
    async def invalid_value(request: Request, error):
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @application.get("/health")
    def health():
        return {"status": "ok", "phase": "ready", "version": application.version}

    @application.get("/api/config")
    def config():
        info = evaluator_info if evaluator_info is not None else _safe_evaluator_info()
        return {**info, "team_name": os.getenv("CQA_TEAM_NAME", "").strip()}

    @application.get("/api/demo/conversations", response_model=list[Conversation])
    def demo_conversations():
        source = ROOT / "fixtures/synthetic_dev.json"
        return normalize(DatasetExport.model_validate_json(source.read_text(encoding="utf-8")))

    @application.post("/api/datasets/import")
    def import_dataset(body: ImportRequest):
        dataset = body.dataset
        if dataset is None:
            if body.source == "demo":
                source = ROOT / "fixtures/synthetic_dev.json"
            else:
                directory = Path(os.getenv("CQA_DATA_DIR", "data/raw"))
                if not directory.is_absolute():
                    directory = ROOT / directory
                source = directory / "DEV.json"
            if not source.is_file():
                raise HTTPException(status_code=404, detail="Файл набора не найден. Загрузите JSON через интерфейс или скрипт получения DEV.")
            dataset = DatasetExport.model_validate_json(source.read_text(encoding="utf-8-sig"))
        conversations = normalize(dataset)
        return store.import_conversations(conversations, dataset.data.dataset_group_id)

    @application.get("/api/conversations")
    def conversations(group_id: str | None = None, language: Literal["RU", "KZ", "EN"] | None = None, channel: Literal["agent chat", "voice call"] | None = None, gross_error: bool | None = Query(None, description="Признак последней оценки текущего диалога с учётом апелляций. Неоценённые и изменённые диалоги исключены при true/false.")):
        return store.list_conversations(group_id, language, channel, gross_error)

    @application.get("/api/conversations/{conversation_id:path}", response_model=Conversation)
    def conversation(conversation_id: str, group_id: str = Query(..., min_length=1)):
        return store.conversation(group_id, conversation_id)

    @application.get("/api/audio-source")
    async def audio_source(group_id: str = Query(..., min_length=1), conversation_id: str = Query(..., min_length=1),
                           evaluation_id: str | None = Query(None, min_length=1)):
        if evaluation_id is None:
            conversation = store.conversation(group_id, conversation_id)
        else:
            conversation = Conversation.model_validate(store.evaluation(evaluation_id)["conversation"])
            if (conversation.group_id, conversation.conversation_id) != (group_id, conversation_id):
                raise HTTPException(status_code=404, detail="Аудиозапись не относится к выбранному диалогу.")
        try:
            resolved = await resolve_audio(conversation.audio_url)
        except AudioSourceError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from None
        return JSONResponse(content=resolved, headers={"Cache-Control": "no-store"})

    @application.get("/api/checklists")
    def checklists():
        return store.list_checklists()

    @application.post("/api/checklists", status_code=201)
    def create_checklist(body: ChecklistVersion):
        return store.create_checklist(body)

    @application.post("/api/checklists/{checklist_id}/versions", status_code=201)
    def create_version(checklist_id: str, body: ChecklistConfiguration):
        return store.new_checklist_version(checklist_id, body.model_dump())

    async def run_job(job_id):
        async with application.state.evaluator_semaphore:
            data = store.begin_job(job_id)
            if data is None:
                return
            try:
                conversation = Conversation.model_validate(data["conversation"])
                checklist = ChecklistVersion.model_validate(data["checklist"])
                evaluate_function = evaluator
                if evaluate_function is None:
                    from .evaluator.service import evaluate
                    evaluate_function = evaluate
                result = evaluate_function(conversation, checklist)
                run = await result if inspect.isawaitable(result) else result
                store.complete_job(job_id, conversation, checklist, run.draft, run.model_info)
            except Exception as error:
                error_code = model_info = None
                try:
                    from .evaluator.service import EvaluatorError
                    message = str(error) if isinstance(error, EvaluatorError) else "Оценка не завершена. Проверьте настройки модели и повторите запуск."
                    if isinstance(error, EvaluatorError):
                        error_code, model_info = error.code, error.model_info
                except ImportError:
                    message = "Модуль оценки недоступен. Проверьте установку приложения."
                logger.warning("Evaluation job %s failed (%s)", job_id, type(error).__name__)
                store.fail_job(job_id, message, error_code=error_code, model_info=model_info)

    @application.post("/api/evaluation-jobs", status_code=202)
    async def create_job(body: JobRequest, background: BackgroundTasks):
        conversation = store.conversation(body.group_id, body.conversation_id)
        checklist = store.checklist(body.checklist_id, body.checklist_version)
        job, created = store.create_job(conversation, checklist, body.idempotency_key)
        if created:
            background.add_task(run_job, job["job_id"])
        return job

    @application.get("/api/evaluation-jobs/{job_id}")
    def job(job_id: str):
        return store.job(job_id)

    @application.get("/api/evaluations")
    def evaluations(group_id: str | None = None, conversation_id: str | None = None, gross_error: bool | None = Query(None, description="Признак каждой сохранённой оценки с учётом апелляций, включая предыдущие версии диалога.")):
        return store.list_evaluations(group_id, conversation_id, gross_error)

    @application.get("/api/evaluations/export")
    def export_evaluations(group_id: str | None = None, conversation_id: str | None = None, gross_error: bool | None = Query(None, description="Признак каждой сохранённой оценки с учётом апелляций. Экспортируется история, а не только последние оценки.")):
        data = store.list_evaluations(group_id, conversation_id, gross_error)
        return Response(content=json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="cqa-evaluations.json"'})

    @application.get("/api/evaluations/{evaluation_id}")
    def evaluation(evaluation_id: str):
        return store.evaluation(evaluation_id)

    @application.get("/api/submission/export")
    def official_submission(
        group_id: str,
        model_name: str,
        split: Literal["dev", "prod"],
        team_name: str | None = None,
        model_mode: Literal["local", "external_api", "hybrid"] = Query(...),
    ):
        """Export latest original model decisions in the organizers' JSON 1.0 format."""
        from evaluation.submission import export_submission, load_approved_checklist, checklist_criteria_fingerprint

        team = (team_name if team_name is not None else os.getenv("CQA_TEAM_NAME", "")).strip()
        if not team:
            raise HTTPException(status_code=422, detail="Укажите team_name или CQA_TEAM_NAME.")
        conversations = [Conversation.model_validate(store.conversation(group_id, item["conversation_id"]))
                         for item in store.list_conversations(group_id=group_id)]
        current_sources = {conversation.conversation_id: conversation.model_dump() for conversation in conversations}
        approved = load_approved_checklist()
        approved_fingerprint = checklist_criteria_fingerprint(approved)
        selected = {}
        for item in store.list_evaluations(group_id=group_id):
            info = item.get("model_info", {})
            if ((info.get("requested_model") or info.get("model")) == model_name
                    and item["conversation"] == current_sources.get(item["conversation"]["conversation_id"])
                    and checklist_criteria_fingerprint(item["checklist"]) == approved_fingerprint):
                selected.setdefault(item["conversation"]["conversation_id"], item)
        known_modes = {"local" if item.get("model_info", {}).get("provider") == "local" else "external_api"
                       for item in selected.values()
                       if item.get("model_info", {}).get("provider") in {"local", "openai", "openai_compatible"}}
        inferred_mode = "hybrid" if len(known_modes) > 1 else next(iter(known_modes), None)
        if inferred_mode is not None and model_mode != inferred_mode:
            raise HTTPException(status_code=422, detail="model_mode не соответствует провайдеру выбранных оценок.")
        document = export_submission(conversations, list(selected.values()), team_name=team,
                                     model_mode=model_mode, model_name=model_name,
                                     split=split, basis="original", approved_checklist=approved)
        return Response(content=json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False),
                        media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="results.json"'})

    @application.post("/api/evaluations/{evaluation_id}/appeals", status_code=201)
    def create_appeal(evaluation_id: str, body: AppealRequest):
        return store.create_appeal(evaluation_id, body.criterion_id, body.comment, body.idempotency_key)

    @application.post("/api/appeals/{appeal_id}/decision")
    def decide_appeal(appeal_id: str, body: DecisionRequest):
        return store.decide_appeal(appeal_id, body.action, body.reason, body.replacement_result, body.expected_revision, body.idempotency_key)

    @application.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint не найден")
        directory = (ROOT / "frontend/dist").resolve()
        requested = (directory / path).resolve()
        if not requested.is_relative_to(directory):
            raise HTTPException(status_code=404, detail="Файл не найден")
        if requested.is_file():
            return FileResponse(requested)
        if Path(path).suffix:
            raise HTTPException(status_code=404, detail="Файл не найден")
        index = directory / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><title>CQA</title><h1>API CQA работает</h1><p>Соберите интерфейс командой <code>npm run build</code> из папки frontend.</p><p><a href="/docs">Документация API</a></p></html>')

    return application


app = create_app()
