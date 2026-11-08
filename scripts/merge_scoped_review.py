"""Merge independently evaluated criterion scopes without inference or manual answers."""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.cqa.evaluator.validation import validate_draft
from backend.cqa.models import ChecklistVersion, Conversation, CriterionResult, EvaluationDraft
from backend.cqa.scoring import calculate_score
from backend.cqa.storage import digest, now
from evaluation.hybrid import review_checklist
from evaluation.submission import _read, load_approved_checklist

MODEL = "gpt-6-luna"
PIPELINE = "selective-independent-review-v1"


def _model(info):
    if not isinstance(info, dict) or info.get("model") != MODEL:
        raise ValueError("Only exact gpt-6-luna model identities are supported")
    if info.get("requested_model", MODEL) != MODEL:
        raise ValueError("Requested model identity differs from gpt-6-luna")


def _usage(original, review):
    info = deepcopy(original)
    for field in ("attempts", "retries", "prompt_tokens", "completion_tokens", "input_tokens",
                  "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens",
                  "cache_write_input_tokens", "elapsed_ms"):
        values = [item.get(field) for item in (original, review)]
        if any(value is not None for value in values):
            if any(value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0) for value in values):
                raise ValueError("Invalid usage counter")
            info[field] = sum(value for value in values if value is not None)
            info[field + "_complete"] = all(value is not None for value in values)
    costs = [item.get("estimated_cost") for item in (original, review)]
    known = [item.get("known_estimated_cost", item.get("estimated_cost")) for item in (original, review)]
    for value in costs + known:
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError("Invalid usage cost")
    info["estimated_cost"] = sum(costs) if all(value is not None for value in costs) else None
    info["known_estimated_cost"] = sum(value for value in known if value is not None)
    info["usage_complete"] = all(item.get("usage_complete") is True for item in (original, review))
    info["attempt_details"] = [dict(deepcopy(attempt), pipeline_stage=stage)
        for stage, item in (("original", original), ("scoped_review", review))
        for attempt in item.get("attempt_details", [])]
    for field in ("cached_input_tokens_complete", "reasoning_tokens_complete", "cache_write_input_tokens_complete"):
        info[field] = all(item.get(field) is True for item in (original, review))
    return info


def merge_scoped_reviews(source, reviews):
    """Return new records; neither input is mutated. Review hashes bind the full rubric."""
    if not isinstance(source, list) or not source or not isinstance(reviews, list):
        raise ValueError("Source must be a nonempty list and reviews must be a list")
    approved = load_approved_checklist().model_dump(mode="json")
    originals = {}
    for record in source:
        conversation = Conversation.model_validate(record["conversation"])
        checklist = ChecklistVersion.model_validate(record["checklist"])
        if checklist.model_dump(mode="json") != approved:
            raise ValueError("Source checklist differs from the complete approved checklist")
        _model(record["model_info"])
        if record.get("revision", 1) != 1 or record.get("appeals") or record.get("results") != record["original_results"]:
            raise ValueError("Source must be an unappealed original evaluation")
        draft = EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id,
            checklist_id=checklist.checklist_id, checklist_version=checklist.version,
            prompt_version=record["model_info"]["prompt_version"], results=record["original_results"])
        validate_draft(conversation, checklist, draft)
        if any(result.status == "needs_review" for result in draft.results):
            raise ValueError("Unresolved source results are not supported")
        score = calculate_score(checklist, draft.results)
        if record.get("original_score") != score or record.get("score") != score:
            raise ValueError("Source score differs from validated original results")
        key = (conversation.group_id, conversation.conversation_id)
        if key in originals:
            raise ValueError("Duplicate source conversation")
        originals[key] = (record, conversation, checklist, draft)
    replacements = {}
    for review in reviews:
        key = (review["group_id"], review["conversation_id"])
        if key not in originals or key in replacements:
            raise ValueError("Unknown or duplicate review conversation")
        record, conversation, checklist, original = originals[key]
        _model(review["model_info"])
        scoped_draft = EvaluationDraft.model_validate(review["draft"])
        selected = [result.criterion_id for result in scoped_draft.results]
        scoped = review_checklist(checklist, selected)
        if review["source_snapshot_sha256"] != digest(conversation.model_dump(mode="json")):
            raise ValueError("Review source snapshot hash mismatch")
        if review["checklist_snapshot_sha256"] != digest(checklist.model_dump(mode="json")):
            raise ValueError("Review full checklist snapshot hash mismatch")
        request_hash = sha256(json.dumps({"conversation": conversation.model_dump(mode="json"),
            "checklist": scoped.model_dump(mode="json")}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if "input_hash" in review["model_info"] and review["model_info"]["input_hash"] != request_hash:
            raise ValueError("Review service input hash mismatch")
        validate_draft(conversation, scoped, scoped_draft)
        if any(result.status == "needs_review" for result in scoped_draft.results):
            raise ValueError("Unresolved scoped review is not accepted")
        if review["model_info"].get("prompt_version", scoped_draft.prompt_version) != scoped_draft.prompt_version:
            raise ValueError("Review prompt identity mismatch")
        replacements[key] = (review, scoped_draft, scoped, selected)
    output = []
    for record, conversation, checklist, original in originals.values():
        key = (conversation.group_id, conversation.conversation_id)
        if key not in replacements:
            output.append(deepcopy(record))
            continue
        review, scoped_draft, scoped, selected = replacements[key]
        by_id = {result["criterion_id"]: result for result in review["draft"]["results"]}
        combined = [deepcopy(by_id.get(result["criterion_id"], result)) for result in record["original_results"]]
        prompt_version = original.prompt_version + "+scoped-review-v1"
        draft = original.model_copy(deep=True, update={"results": [CriterionResult.model_validate(r) for r in combined], "prompt_version": prompt_version})
        validate_draft(conversation, checklist, draft)
        score = calculate_score(checklist, draft.results)
        info = _usage(record["model_info"], review["model_info"])
        info.update(model=MODEL, requested_model=MODEL, prompt_version=prompt_version, pipeline=PIPELINE,
            input_hash=digest({"original": record, "review": review}),
            selective_review={"selected_criterion_ids": selected, "original_record": deepcopy(record),
                "review_record": deepcopy(review), "scoped_checklist": scoped.model_dump(mode="json"),
                "source_snapshot_sha256": digest(conversation.model_dump(mode="json")),
                "scoped_checklist_snapshot_sha256": digest(scoped.model_dump(mode="json")),
                "full_checklist_snapshot_sha256": digest(checklist.model_dump(mode="json"))})
        output.append({"evaluation_id": uuid4().hex, "conversation": deepcopy(record["conversation"]),
            "checklist": deepcopy(record["checklist"]), "original_results": combined, "results": deepcopy(combined),
            "original_score": score, "score": deepcopy(score), "model_info": info,
            "revision": 1, "appeals": [], "created_at": now()})
    return {"model": MODEL, "evaluations": output}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--reviews", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.out.resolve() in {args.source.resolve(), args.reviews.resolve()}:
            raise ValueError("Output must not replace either original input")
        result = merge_scoped_reviews(_read(args.source), _read(args.reviews))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps({"evaluations": len(result["evaluations"]), "pipeline": PIPELINE}))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Scoped merge rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
