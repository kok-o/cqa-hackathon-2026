"""Import validated saved model results into the app without calling a provider.

The default destination is CQA_DATABASE_PATH from environment/.env. --dry-run
validates source and existing snapshots without creating or writing a database.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from pydantic import ValidationError

from backend.cqa.evaluator.validation import validate_draft
from backend.cqa.models import ChecklistVersion, Conversation, EvaluationDraft
from backend.cqa.scoring import calculate_score
from backend.cqa.storage import ConflictError, Store, digest, dumps, now


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def key(conversation: dict | Conversation) -> tuple[str, str]:
    if isinstance(conversation, Conversation):
        return conversation.group_id, conversation.conversation_id
    return conversation["group_id"], conversation["conversation_id"]


def record_model(record: dict, default: str | None = None) -> str:
    info = record.get("model_info") or {}
    model = record.get("model") or default or info.get("requested_model") or info.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("A source record does not identify its model")
    if default and record.get("model") and record["model"] != default:
        raise ValueError("Prediction document and record model identities differ")
    return model


@dataclass(frozen=True)
class ImportItem:
    conversation: Conversation
    checklist: ChecklistVersion
    draft: EvaluationDraft
    model_info: dict
    output_hash: str
    idempotency_key: str


@dataclass(frozen=True)
class ImportPlan:
    source_fingerprint: str
    source_kind: str
    model: str
    items: list[ImportItem]
    counts: dict


def prepare_import(*, run_dir: str | Path | None = None, predictions: str | Path | None = None,
                   model: str | None = None) -> ImportPlan:
    """Validate all selected completed records before any destination writes."""
    if (run_dir is None) == (predictions is None):
        raise ValueError("Choose exactly one --run-dir or --predictions source")
    manifest = None
    if run_dir is not None:
        folder = Path(run_dir)
        manifest = read_json(folder / "manifest.json")
        checkpoint = read_json(folder / "checkpoint.json")
        source_fingerprint = manifest["fingerprint"]
        if checkpoint.get("fingerprint") != source_fingerprint or digest(manifest["inputs"]) != source_fingerprint:
            raise ValueError("Manifest/checkpoint fingerprint integrity check failed")
        records = list(checkpoint["records"].values())
        available_models = list(manifest["inputs"]["models"])
        default_model = None
        source_kind = "benchmark_checkpoint"
    else:
        document = read_json(Path(predictions))
        if not isinstance(document, dict) or not isinstance(document.get("evaluations"), list):
            raise ValueError("Prediction file must contain an evaluations array")
        records = document["evaluations"]
        default_model = document.get("model")
        available_models = sorted({record_model(r, default_model) for r in records})
        source_fingerprint = document.get("fingerprint") or digest(document)
        source_kind = "prediction_export"
    if not isinstance(source_fingerprint, str) or not source_fingerprint:
        raise ValueError("Source fingerprint must be a non-empty string")
    if model is None:
        if len(available_models) != 1:
            raise ValueError("Source contains multiple models or no model; choose --model explicitly")
        model = available_models[0]
    if model not in available_models:
        raise ValueError("Selected model is absent from the source")
    selected = [r for r in records if record_model(r, default_model) == model]
    counts = {"source_records": len(records), "selected_records": len(selected),
              "other_model_records": len(records) - len(selected), "completed_records": 0,
              "skipped_failed_records": 0, "skipped_noncompleted_records": 0, "needs_review_records": 0}
    planned = {}
    manifest_checklist = None
    if manifest is not None:
        manifest_checklist = ChecklistVersion.model_validate(manifest["checklist"])
        if digest(manifest_checklist.model_dump(mode="json")) != manifest["inputs"]["checklist_sha256"]:
            raise ValueError("Manifest checklist hash does not match its snapshot")
        selected_conversations = {}
        for task in manifest["tasks"]:
            identity = (task["model"], *key(task["conversation"]))
            if identity in planned:
                raise ValueError("Duplicate planned model/conversation identity")
            planned[identity] = task
            conversation = Conversation.model_validate(task["conversation"]).model_dump(mode="json")
            conversation_identity = key(conversation)
            if conversation_identity in selected_conversations and selected_conversations[conversation_identity] != conversation:
                raise ValueError("Models in the manifest have different conversation snapshots")
            selected_conversations[conversation_identity] = conversation
        # Tasks are outside the manifest.inputs fingerprint. Tie their ordered,
        # deduplicated source snapshots back to the recorded input selection.
        if digest(list(selected_conversations.values())) != manifest["inputs"]["selected_conversations_sha256"]:
            raise ValueError("Manifest planned conversations differ from the selected source snapshot hash")
    items, identities = [], set()
    source_checklists = {}
    for record in selected:
        status = record.get("status")
        if record.get("error") or status in {"failed", "interrupted_unknown"}:
            counts["skipped_failed_records"] += 1
            continue
        if status is None and source_kind == "prediction_export":
            status = "completed" if record.get("original_results", record.get("results")) else "unknown"
        if status != "completed":
            counts["skipped_noncompleted_records"] += 1
            continue
        conversation = Conversation.model_validate(record["conversation"])
        checklist = ChecklistVersion.model_validate(record["checklist"])
        identity = key(conversation)
        if identity in identities:
            raise ValueError("Duplicate completed conversation for the selected model")
        identities.add(identity)
        checklist_key = (checklist.checklist_id, checklist.version)
        if checklist_key in source_checklists and source_checklists[checklist_key] != checklist.model_dump():
            raise ValueError("Source contains conflicting snapshots for one checklist version")
        source_checklists[checklist_key] = checklist.model_dump()
        if manifest is not None:
            task = planned.get((model, *identity))
            if task is None or Conversation.model_validate(task["conversation"]).model_dump() != conversation.model_dump():
                raise ValueError("Completed record does not match its planned conversation snapshot")
            if checklist.model_dump() != manifest_checklist.model_dump():
                raise ValueError("Completed record differs from the manifest checklist snapshot")
            if record.get("request_sha256") != task.get("request_sha256"):
                raise ValueError("Completed request hash differs from its manifest task")
        if record.get("revision", 1) > 1 and ("original_results" not in record or "original_score" not in record):
            raise ValueError("Revised records require original model results and original score")
        info = record.get("model_info")
        if not isinstance(info, dict):
            raise ValueError("Completed records require a model_info object")
        if info.get("requested_model") and info["requested_model"] != model:
            raise ValueError("Model metadata differs from the selected requested model")
        if not info.get("requested_model") and info.get("model") and info["model"] != model:
            raise ValueError("Legacy model metadata contradicts the selected model")
        prompt_version = info.get("prompt_version") or (manifest or {}).get("inputs", {}).get("prompt_version")
        if not isinstance(prompt_version, str) or not prompt_version:
            raise ValueError("Completed records require their original prompt version")
        draft = EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id,
                                checklist_id=checklist.checklist_id, checklist_version=checklist.version,
                                prompt_version=prompt_version, results=record.get("original_results", record.get("results")))
        validate_draft(conversation, checklist, draft)
        computed_score = calculate_score(checklist, draft.results)
        original_score = record.get("original_score", record.get("score"))
        if original_score is not None and original_score != computed_score:
            raise ValueError("Original saved score differs from current scoring; review the implementation before importing")
        original_results = [r.model_dump(mode="json") for r in draft.results]
        output_hash = digest({"conversation": conversation.model_dump(mode="json"),
                              "checklist": checklist.model_dump(mode="json"), "original_results": original_results,
                              "original_score": computed_score, "model_info": info,
                              "request_sha256": record.get("request_sha256")})
        metadata = {"source_kind": source_kind, "source_fingerprint": source_fingerprint,
                    "requested_model": model, "original_output_sha256": output_hash,
                    "request_sha256": record.get("request_sha256"),
                    "source_record_metadata": {name: record[name] for name in (
                        "task_id", "started_at", "finished_at", "wall_time_ms", "actual_cost_usd", "cost_status",
                        "reservation_usd", "budget_charge_usd") if name in record}}
        preserved_info = {**info, "benchmark_import": metadata}
        idempotency_key = "benchmark-import:" + digest([source_fingerprint, model, *identity])
        items.append(ImportItem(conversation, checklist, draft, preserved_info, output_hash, idempotency_key))
        counts["completed_records"] += 1
        counts["needs_review_records"] += computed_score["status"] == "incomplete"
    return ImportPlan(source_fingerprint, source_kind, model, items, counts)


def check_existing(connection: sqlite3.Connection, items: list[ImportItem]) -> dict:
    """Read-only conflict checks; returns only counts and existing job states."""
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"conversations", "checklist_versions", "evaluation_jobs", "evaluations"}
    if tables and not required <= tables:
        raise ValueError("Destination database lacks the expected CQA schema")
    states = {}
    if not tables:
        return states
    for item in items:
        c, checklist = item.conversation, item.checklist
        existing = connection.execute("SELECT payload FROM conversations WHERE group_id=? AND conversation_id=?", key(c)).fetchone()
        if existing and Conversation.model_validate_json(existing[0]).model_dump() != c.model_dump():
            raise ConflictError("Existing conversation snapshot differs; source was not overwritten (identity_sha256=" + digest(key(c)) + ")")
        existing = connection.execute("SELECT payload FROM checklist_versions WHERE checklist_id=? AND version=?", (checklist.checklist_id, checklist.version)).fetchone()
        if existing and ChecklistVersion.model_validate_json(existing[0]).model_dump() != checklist.model_dump():
            raise ConflictError("Existing checklist version differs; no version was overwritten or renumbered (snapshot_sha256=" + digest(checklist.model_dump()) + ")")
        job = connection.execute("SELECT status,evaluation_id,request_hash FROM evaluation_jobs WHERE idempotency_key=?", (item.idempotency_key,)).fetchone()
        if job is None:
            continue
        if job[2] != digest({"conversation": c.model_dump(), "checklist": checklist.model_dump()}):
            raise ConflictError("Import idempotency key has different source snapshots")
        states[item.idempotency_key] = job[0]
        if job[0] == "completed":
            saved = connection.execute("SELECT payload FROM evaluations WHERE evaluation_id=?", (job[1],)).fetchone()
            if not saved:
                raise ConflictError("Completed import job has no saved evaluation")
            saved = json.loads(saved[0])
            saved_hash = saved.get("model_info", {}).get("benchmark_import", {}).get("original_output_sha256")
            if saved_hash != item.output_hash:
                raise ConflictError("A completed import with this fingerprint has different original model output")
            if saved.get("original_results") != [r.model_dump() for r in item.draft.results]:
                raise ConflictError("Saved original model output no longer matches its import hash")
    return states


def ensure_sources(store: Store, items: list[ImportItem]) -> None:
    """Atomically add missing sources, rejecting changed existing snapshots."""
    with store.transaction() as connection:
        check_existing(connection, items)
        for item in items:
            checklist, c = item.checklist, item.conversation
            connection.execute("INSERT OR IGNORE INTO checklist_versions VALUES(?,?,?,?)",
                               (checklist.checklist_id, checklist.version, dumps(checklist.model_dump()), now()))
            payload = c.model_dump()
            connection.execute("INSERT OR IGNORE INTO conversations VALUES(?,?,?,?,?,?,?)",
                               (c.group_id, c.conversation_id, c.language, c.channel, digest(payload), dumps(payload), now()))


def import_plan(plan: ImportPlan, database: str | Path, *, dry_run: bool = False) -> dict:
    destination = Path(database).resolve()
    states = {}
    if destination.exists():
        # Do not initialize a database for validation or dry-run: initialization
        # can create tables, seed versions and recover unfinished app jobs.
        with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True, timeout=15)) as connection:
            states = check_existing(connection, plan.items)
    already = sum(state == "completed" for state in states.values())
    pending = sum(state == "running" for state in states.values())
    failed = sum(state == "failed" for state in states.values())
    report = {"mode": "dry_run_no_writes" if dry_run else "offline_import_no_provider_calls",
              "source_fingerprint": plan.source_fingerprint, "model": plan.model, **plan.counts,
              "already_imported": already, "skipped_in_progress": pending, "skipped_existing_failed": failed,
              "would_import": len(plan.items) - already - pending - failed, "imported_evaluations": 0}
    if dry_run or not plan.items or already + pending + failed == len(plan.items):
        return report
    store = Store(destination)
    try:
        store.initialize(plan.items[0].checklist, recover_jobs=False)
        ensure_sources(store, plan.items)
        # Actual operation counts may differ from preflight if another importer
        # completes the same job while this process is starting.
        report.update(already_imported=0, skipped_in_progress=0, skipped_existing_failed=0)
        for item in plan.items:
            with store.read() as connection:
                current = check_existing(connection, [item]).get(item.idempotency_key)
            if current == "completed":
                report["already_imported"] += 1
                continue
            if current == "running":
                report["skipped_in_progress"] += 1
                continue
            if current == "failed":
                report["skipped_existing_failed"] += 1
                continue
            job, _ = store.create_job(item.conversation, item.checklist, item.idempotency_key)
            if store.begin_job(job["job_id"]) is None:
                report["skipped_in_progress"] += 1
                continue
            try:
                store.complete_job(job["job_id"], item.conversation, item.checklist, item.draft, item.model_info)
                report["imported_evaluations"] += 1
            except Exception:
                store.fail_job(job["job_id"], "Offline benchmark import failed; no model was called.")
                raise
    finally:
        store.close()
    return report


def default_database() -> Path:
    load_dotenv(ROOT / ".env", override=False)
    configured = Path(os.getenv("CQA_DATABASE_PATH", "data/runtime/cqa.sqlite3"))
    return configured if configured.is_absolute() else ROOT / configured


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-dir")
    source.add_argument("--predictions")
    parser.add_argument("--model", help="Required when a source contains multiple models")
    parser.add_argument("--database", help="Defaults to CQA_DATABASE_PATH from environment/.env")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = prepare_import(run_dir=args.run_dir, predictions=args.predictions, model=args.model)
        report = import_plan(plan, args.database or default_database(), dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if not (report["skipped_in_progress"] or report["skipped_existing_failed"]) else 3
    except (ValueError, KeyError, OSError, sqlite3.Error, ConflictError) as exc:
        # Pydantic errors can contain transcript input values; do not echo them.
        message = "Source contract validation failed; no credentials or transcript values are printed." if isinstance(exc, ValidationError) else str(exc)
        print(f"Import error: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
