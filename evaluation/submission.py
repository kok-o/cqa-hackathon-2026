"""Export and validate the organizer's Results JSON 1.0, without model calls.

Reference: CQA_Student_Guide_Appendix_Results.pdf, all three pages.
The submission format is intentionally separate from internal audit snapshots.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

from pydantic import ValidationError

from backend.cqa.evaluator.validation import validate_draft
from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, Conversation, DatasetExport, EvaluationDraft


CRITERION_IDS = ("contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical")
WEIGHTS = {"contact": 10, "discovery": 15, "identification": 20, "consultation": 20,
           "empathy": 15, "dialogue": 15, "closure": 5}
NULL_ALLOWED = {"discovery", "identification", "consultation", "closure"}
SCALES = {"consultation": {0, 5, 10, 15, 20}, "dialogue": {0, 4, 7, 10}}
MODEL_MODES = {"local", "external_api", "hybrid"}
DEFAULT_APPROVED_CHECKLIST = Path(__file__).resolve().parents[1] / "fixtures" / "checklist.official.json"


def load_approved_checklist(path: str | Path | None = None) -> ChecklistVersion:
    """Load the explicitly approved rubric; default to the checked-in organizer copy."""
    checklist = ChecklistVersion.model_validate(_read(path or DEFAULT_APPROVED_CHECKLIST))
    _validate_approved_contract(checklist)
    return checklist


def _validate_approved_contract(checklist: ChecklistVersion) -> None:
    """A replacement instruction cannot silently redefine the JSON 1.0 scoring contract."""
    if set(c.criterion_id for c in checklist.criteria) != set(CRITERION_IDS):
        raise ValueError("Approved rubric is incompatible with Results JSON 1.0: an updated submission contract is required for changed criterion IDs")
    for criterion in checklist.criteria:
        criterion_id = criterion.criterion_id
        valid = criterion.weight == WEIGHTS.get(criterion_id, 0) and criterion.na_allowed == (criterion_id in NULL_ALLOWED)
        if criterion_id in SCALES:
            valid = valid and criterion.answer_type == "scale" and criterion.scale_min == 0
            valid = valid and criterion.scale_max == max(SCALES[criterion_id]) and criterion.higher_is_better
            valid = valid and criterion.allowed_values is not None and set(criterion.allowed_values) == SCALES[criterion_id]
        else:
            valid = valid and criterion.answer_type == "boolean" and criterion.positive_answer == (criterion_id != "critical")
        valid = valid and criterion.critical_answer is (True if criterion_id == "critical" else None)
        if not valid:
            raise ValueError(f"Approved rubric is incompatible with Results JSON 1.0 for {criterion_id}: changed weights, scales, N/A rules or critical semantics require an updated submission contract; use the internal export meanwhile")


def checklist_criteria_fingerprint(checklist: ChecklistVersion | dict) -> str:
    """Compare all normalized criterion rules, excluding checklist identity metadata."""
    if not isinstance(checklist, ChecklistVersion):
        checklist = ChecklistVersion.model_validate(checklist)
    criteria = sorted((item.model_dump(mode="json") for item in checklist.criteria),
                      key=lambda item: item["criterion_id"])
    content = json.dumps(criteria, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return sha256(content.encode("utf-8")).hexdigest()


def _object(value: Any, keys: set[str], context: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{context}: expected exactly these fields: {', '.join(sorted(keys))}")
    return value


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: expected a non-empty string")
    return value


def _validate_value(criterion_id: str, value: Any) -> None:
    if value is None:
        if criterion_id not in NULL_ALLOWED:
            raise ValueError(f"{criterion_id}: null is not permitted by Results JSON 1.0")
    elif criterion_id in SCALES:
        if type(value) not in {int, float} or value not in SCALES[criterion_id]:
            raise ValueError(f"{criterion_id}: value must be one of {sorted(SCALES[criterion_id])}")
    elif type(value) is not bool:
        raise ValueError(f"{criterion_id}: expected a JSON boolean")


def official_score(criteria: list[dict]) -> dict:
    """Weight the raw discrete values, redistribute N/A, and keep critical separate."""
    values = {item["criterion_id"]: item["value"] for item in criteria}
    if set(values) != set(CRITERION_IDS) or len(criteria) != len(CRITERION_IDS):
        raise ValueError("Expected every official criterion exactly once")
    earned = Decimal(0)
    denominator = Decimal(0)
    for criterion_id, value in values.items():
        _validate_value(criterion_id, value)
        if criterion_id == "critical" or value is None:
            continue
        weight = Decimal(WEIGHTS[criterion_id])
        denominator += weight
        normalized = Decimal(str(value)) / max(SCALES[criterion_id]) if criterion_id in SCALES else Decimal(int(value))
        earned += weight * normalized
    score = (100 * earned / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"final_score": float(score), "critical_flag": values["critical"]}


def _source_index(conversations: list[Conversation], split: str | None) -> dict[str, Conversation]:
    sources = {c.conversation_id: c for c in conversations}
    if len(sources) != len(conversations):
        raise ValueError("Submission IDs must be unique across all source groups")
    if split == "prod":
        if len(conversations) != 10 or any(c.channel != "agent chat" for c in conversations):
            raise ValueError("Official prod requires exactly 10 source chats")
    elif split == "dev":
        if Counter(c.channel for c in conversations) != {"agent chat": 60, "voice call": 5}:
            raise ValueError("Official dev requires exactly 60 source chats and 5 voice calls")
    elif split is not None:
        raise ValueError("split must be dev or prod")
    return sources


def validate_submission(document: Any, *, conversations: list[Conversation] | None = None,
                        split: str | None = None) -> dict:
    """Validate exact shape, score, source coverage and evidence indices.

    Without source conversations only the JSON contract can be checked; the CLI
    always requires sources. Empty evidence lists are preserved and reported,
    because the appendix supplies no formal minimum list length.
    """
    _object(document, {"schema_version", "team_name", "model", "results"}, "submission")
    if document["schema_version"] != "1.0":
        raise ValueError("schema_version must be the string '1.0'")
    _text(document["team_name"], "team_name")
    model = _object(document["model"], {"mode", "name"}, "model")
    if model["mode"] not in MODEL_MODES:
        raise ValueError("model.mode must be local, external_api, or hybrid")
    _text(model["name"], "model.name")
    results = document["results"]
    if not isinstance(results, list):
        raise ValueError("results must be an array")
    if split not in {None, "dev", "prod"}:
        raise ValueError("split must be dev or prod")
    expected_count = {"dev": 65, "prod": 10}.get(split)
    if expected_count is not None and len(results) != expected_count:
        raise ValueError(f"Official {split} requires exactly {expected_count} records, including errors")
    sources = _source_index(conversations, split) if conversations is not None else None
    seen: set[str] = set()
    warnings = []
    errors = 0
    for record in results:
        if not isinstance(record, dict):
            raise ValueError("Every result must be an object")
        conversation_id = _text(record.get("conversation_id"), "conversation_id")
        if conversation_id in seen:
            raise ValueError(f"Duplicate conversation_id: {conversation_id}")
        seen.add(conversation_id)
        if sources is not None and conversation_id not in sources:
            raise ValueError(f"Unknown conversation_id: {conversation_id}")
        status = record.get("status")
        if status == "error":
            _object(record, {"conversation_id", "status", "criteria", "score", "error"}, conversation_id)
            if record["criteria"] != [] or record["score"] is not None:
                raise ValueError(f"{conversation_id}: an error requires empty criteria and null score")
            _text(record["error"], f"{conversation_id}.error")
            errors += 1
            continue
        if status != "ok":
            raise ValueError(f"{conversation_id}: status must be ok or error")
        _object(record, {"conversation_id", "status", "criteria", "score"}, conversation_id)
        criteria = record["criteria"]
        if not isinstance(criteria, list) or len(criteria) != 8:
            raise ValueError(f"{conversation_id}: status ok requires eight criteria")
        criterion_ids = []
        for item in criteria:
            _object(item, {"criterion_id", "value", "rationale", "evidence_turns"}, "criterion")
            criterion_id = item["criterion_id"]
            if criterion_id not in CRITERION_IDS:
                raise ValueError(f"Unknown official criterion_id: {criterion_id}")
            criterion_ids.append(criterion_id)
            _validate_value(criterion_id, item["value"])
            _text(item["rationale"], f"{criterion_id}.rationale")
            turns = item["evidence_turns"]
            if not isinstance(turns, list) or any(type(turn) is not int or turn < 1 for turn in turns):
                raise ValueError(f"{criterion_id}: evidence_turns must contain one-based integer source indices")
            if sources is not None and any(turn > len(sources[conversation_id].transcript) for turn in turns):
                raise ValueError(f"{criterion_id}: evidence turn is outside the source transcript")
            if not turns:
                warnings.append(f"{conversation_id}/{criterion_id}: no supporting source turns were recorded")
        if set(criterion_ids) != set(CRITERION_IDS):
            raise ValueError(f"{conversation_id}: every official criterion is required exactly once")
        score = _object(record["score"], {"final_score", "critical_flag"}, "score")
        final = score["final_score"]
        if type(final) not in {int, float} or not 0 <= final <= 100 or Decimal(str(final)) != Decimal(str(final)).quantize(Decimal("0.01")):
            raise ValueError("final_score must be finite, between 0 and 100, and rounded to two decimal places")
        if type(score["critical_flag"]) is not bool or score != official_score(criteria):
            raise ValueError(f"{conversation_id}: score must match the official weighted formula and critical value")
    if sources is not None and seen != set(sources):
        raise ValueError("Submission is missing source conversation IDs")
    return {"records": len(results), "ok": len(results) - errors, "errors": errors,
            "source_coverage_checked": sources is not None, "warnings": warnings}


def _error(conversation_id: str, reason: str) -> dict:
    return {"conversation_id": conversation_id, "status": "error", "criteria": [], "score": None, "error": reason}


def _export_record(conversation: Conversation, record: dict | None, basis: str,
                   approved_fingerprint: str) -> dict:
    if record is None:
        return _error(conversation.conversation_id, "No evaluation was provided for this source conversation.")
    if record.get("error"):
        return _error(conversation.conversation_id, str(record["error"]))
    try:
        snapshot = Conversation.model_validate(record["conversation"])
        if snapshot != conversation:
            raise ValueError("Evaluation snapshot does not match the original source conversation")
        checklist = ChecklistVersion.model_validate(record["checklist"])
        if set(c.criterion_id for c in checklist.criteria) != set(CRITERION_IDS):
            raise ValueError("Evaluation did not use all eight official criterion IDs")
        if checklist_criteria_fingerprint(checklist) != approved_fingerprint:
            raise ValueError("Evaluation rubric does not match the approved criterion rules; no official score was fabricated")
        if basis == "original":
            raw = record.get("original_results")
            if raw is None:
                if record.get("revision", 1) != 1 or record.get("appeals"):
                    raise ValueError("Original results are absent from a reviewed evaluation")
                raw = record.get("results")
        else:
            raw = record.get("results")
        draft = EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id,
            checklist_id=checklist.checklist_id, checklist_version=checklist.version,
            prompt_version="submission-validation", results=raw)
        validate_draft(conversation, checklist, draft)
        positions = {message.message_id: index + 1 for index, message in enumerate(conversation.transcript)}
        by_id = {item.criterion_id: item for item in draft.results}
        criteria = []
        for criterion_id in CRITERION_IDS:
            item = by_id[criterion_id]
            if item.status == "needs_review":
                raise ValueError(f"Unresolved criterion {criterion_id}; no value was fabricated")
            value = item.answer
            _validate_value(criterion_id, value)
            if criterion_id in SCALES and value is not None:
                value = int(value)
            turns = {positions[e.message_id] for e in item.evidence}
            if item.checked_span:
                start, end = positions[item.checked_span.start_message_id], positions[item.checked_span.end_message_id]
                turns.update(range(start, end + 1))
            criteria.append({"criterion_id": criterion_id, "value": value,
                             "rationale": item.explanation, "evidence_turns": sorted(turns)})
        return {"conversation_id": conversation.conversation_id, "status": "ok", "criteria": criteria,
                "score": official_score(criteria)}
    except (ValueError, KeyError, TypeError) as exc:
        # Validation errors describe contract/source issues, never provider response bodies.
        if isinstance(exc, ValidationError):
            reason = "Evaluation fails the internal data contract; no score was fabricated."
        elif isinstance(exc, KeyError):
            reason = "Evaluation is missing required source, checklist, or result data."
        else:
            reason = str(exc)
        return _error(conversation.conversation_id, reason)


def _evaluation_model(record: Any) -> str | None:
    """Read optional metadata without leaking malformed input into exceptions."""
    if not isinstance(record, dict):
        raise ValueError("Every evaluation must be an object")
    info = record.get("model_info", {})
    if not isinstance(info, dict):
        raise ValueError("Evaluation model_info must be an object when provided")
    for model in (info.get("model"), record.get("model")):
        if model is not None and not isinstance(model, str):
            raise ValueError("Evaluation model name must be a string or null")
    return info.get("model") or record.get("model")


def export_submission(conversations: list[Conversation], evaluations: list[dict], *, team_name: str,
                      model_mode: str, model_name: str | None = None, split: str | None = None,
                      basis: str = "original", approved_checklist: ChecklistVersion | dict | None = None) -> dict:
    """Create a new document without changing sources or appeals.

    An explicit approved_checklist is a caller's approval of replacement rules;
    it must not be automatically inferred from the predictions being exported.
    """
    if basis not in {"original", "current"}:
        raise ValueError("basis must be original or current")
    if not isinstance(evaluations, list):
        raise ValueError("Evaluations must be an array")
    sources = _source_index(conversations, split)
    approved = load_approved_checklist() if approved_checklist is None else (
        approved_checklist if isinstance(approved_checklist, ChecklistVersion) else ChecklistVersion.model_validate(approved_checklist))
    _validate_approved_contract(approved)
    approved_fingerprint = checklist_criteria_fingerprint(approved)
    by_id = {}
    actual_models = set()
    for record in evaluations:
        model = _evaluation_model(record)
        if not isinstance(record.get("conversation"), dict):
            raise ValueError("Every evaluation must contain its original conversation snapshot")
        conversation_id = _text(record["conversation"].get("conversation_id"), "Evaluation conversation_id")
        if conversation_id not in sources:
            raise ValueError("Evaluation refers to a conversation outside the supplied dataset")
        if conversation_id in by_id:
            raise ValueError("Multiple evaluations for one conversation; select one model/run explicitly")
        by_id[conversation_id] = record
        if isinstance(model, str) and model.strip():
            actual_models.add(model)
    if model_name is None:
        if not actual_models:
            raise ValueError("Actual model name is missing; provide explicit model_name metadata")
        model_name = ", ".join(sorted(actual_models))
    elif any(model not in model_name for model in actual_models):
        raise ValueError("model.name must include every model actually used by the selected evaluations")
    document = {"schema_version": "1.0", "team_name": team_name,
                "model": {"mode": model_mode, "name": model_name},
                "results": [_export_record(c, by_id.get(c.conversation_id), basis, approved_fingerprint) for c in conversations]}
    validate_submission(document, conversations=conversations, split=split)
    return document


def _read(path: str | Path) -> Any:
    def reject_constant(value):
        raise ValueError(f"Non-JSON numeric constant: {value}")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON object key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=reject_constant,
                      object_pairs_hook=unique_keys)


def _evaluations(document: Any, select_model: str | None) -> list[dict]:
    if isinstance(document, list):
        records = document
    elif isinstance(document, dict) and isinstance(document.get("evaluations"), list):
        records = document["evaluations"]
    elif isinstance(document, dict) and isinstance(document.get("records"), dict):
        records = list(document["records"].values())
    else:
        raise ValueError("Predictions must be an evaluations array, internal export, or benchmark checkpoint")
    models = [_evaluation_model(record) for record in records]
    if select_model:
        records = [record for record, model in zip(records, models) if model == select_model]
        if not records:
            raise ValueError("No predictions match --select-model")
    return records


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("export", "validate"):
        command = commands.add_parser(name)
        command.add_argument("--dataset", nargs="+", required=True, help="Original organizer API export JSON files")
        command.add_argument("--split", choices=["dev", "prod"], required=True)
        if name == "validate":
            command.add_argument("--input", required=True)
        else:
            command.add_argument("--predictions", nargs="+", required=True)
            command.add_argument("--team-name", required=True)
            command.add_argument("--model-mode", choices=sorted(MODEL_MODES), required=True)
            command.add_argument("--model-name", help="Defaults to actual selected model metadata")
            command.add_argument("--select-model", help="Required when a checkpoint has several models for the same chats")
            command.add_argument("--basis", choices=["original", "current"], default="original")
            command.add_argument("--approved-checklist", help="Explicit judge-approved replacement instructions; must retain the JSON 1.0 IDs, weights, scales, N/A and critical semantics")
            command.add_argument("--out", help="Defaults to results/<split>/results.json")
            command.add_argument("--replace", action="store_true", help="Explicitly replace an existing submission file")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        conversations = [c for path in args.dataset for c in normalize(DatasetExport.model_validate(_read(path)))]
        if args.command == "validate":
            document = _read(args.input)
        else:
            evaluations = [r for path in args.predictions for r in _evaluations(_read(path), args.select_model)]
            document = export_submission(conversations, evaluations, team_name=args.team_name,
                model_mode=args.model_mode, model_name=args.model_name, split=args.split, basis=args.basis,
                approved_checklist=load_approved_checklist(args.approved_checklist))
            destination = Path(args.out or f"results/{args.split}/results.json")
            if destination.exists() and not args.replace:
                raise ValueError("Output already exists; choose another --out or explicitly use --replace")
            input_paths = {Path(path).resolve() for path in args.dataset + args.predictions}
            input_paths.add(Path(args.approved_checklist or DEFAULT_APPROVED_CHECKLIST).resolve())
            if destination.resolve() in input_paths:
                raise ValueError("Output cannot overwrite an original dataset, prediction snapshot, or approved checklist")
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            destination.write_text(content, encoding="utf-8")
        report = validate_submission(document, conversations=conversations, split=args.split)
        print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Submission error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
