"""Internal diagnostics, not an assertion about the organizers' unknown metric."""
from __future__ import annotations

from collections import defaultdict
import json
import math
from typing import Any

from backend.cqa.models import Conversation


def _decision(result: dict[str, Any] | None) -> str:
    if result is None:
        return "missing"
    answer = result.get("answer")
    if _numeric(answer):
        answer = float(answer)
    return json.dumps([result.get("status"), answer], ensure_ascii=False, sort_keys=True)


def _numeric(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _f1(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    classes = set(left for left, _ in pairs) | set(right for _, right in pairs)
    scores = []
    for label in classes:
        tp = sum(a == label and b == label for a, b in pairs)
        fp = sum(a != label and b == label for a, b in pairs)
        fn = sum(a == label and b != label for a, b in pairs)
        denominator = 2 * tp + fp + fn
        scores.append(2 * tp / denominator if denominator else 0)
    return sum(scores) / len(scores)


def _key(item: dict[str, Any]) -> tuple[str, str]:
    conversation = item.get("conversation", item)
    return conversation["group_id"], conversation["conversation_id"]


def summarize(labels: dict[str, Any], predictions: list[dict[str, Any]], split: str = "all") -> dict[str, Any]:
    expected_items = [item for item in labels["items"] if item.get("reviewed") is True
                      and (split == "all" or item["split"] == split)]
    predicted: dict[tuple[str, str], dict[str, Any]] = {}
    for item in predictions:
        key = _key(item)
        if key in predicted:
            raise ValueError("Predictions contain duplicate conversations; select one run/version before comparing")
        predicted[key] = item
    expected_keys = [_key(item) for item in expected_items]
    if len(expected_keys) != len(set(expected_keys)):
        raise ValueError("Labels contain duplicate conversations")
    pairs: list[tuple[str, str]] = []
    by_criterion: dict[str, dict[str, Any]] = defaultdict(lambda: {"pairs": [], "errors": [], "na_errors": 0})
    by_language: dict[str, list[tuple[str, str]]] = defaultdict(list)
    numeric_errors: list[float] = []
    total_errors: list[float] = []
    evidence_seen = evidence_valid = span_seen = span_valid = 0
    predicted_results_count = applicable_results_count = missing_predictions = 0
    semantic_supported = semantic_reviewed = 0
    checklist = labels["checklist"]
    criterion_ids = {c["criterion_id"] for c in checklist["criteria"]}
    for item in expected_items:
        expected = item["expected_results"]
        if {result["criterion_id"] for result in expected} != criterion_ids or len(expected) != len(criterion_ids):
            raise ValueError("Every reviewed item must label every checklist criterion exactly once")
        if any(result.get("status") not in {"applicable", "not_applicable", "needs_review"} for result in expected):
            raise ValueError("Reviewed labels cannot contain blank statuses")
        prediction = predicted.get(_key(item))
        if prediction is None or prediction.get("error"):
            missing_predictions += 1
        if prediction and prediction.get("checklist"):
            snapshot = prediction["checklist"]
            if (snapshot.get("checklist_id"), snapshot.get("version"), snapshot.get("criteria")) != (
                checklist["checklist_id"], checklist["version"], checklist["criteria"]):
                raise ValueError("Prediction and labels use different checklist snapshots")
        if prediction and prediction.get("conversation"):
            expected_conversation = Conversation.model_validate(item["conversation"]).model_dump(exclude={"audio_url"})
            predicted_conversation = Conversation.model_validate(prediction["conversation"]).model_dump(exclude={"audio_url"})
            if expected_conversation != predicted_conversation:
                raise ValueError("Prediction and labels use different conversation snapshots")
        if prediction and prediction.get("revision", 1) > 1 and (
            "original_results" not in prediction or "original_score" not in prediction
        ):
            raise ValueError("Reviewed predictions require original model results and score")
        # App exports contain human appeal decisions in results/score. Model
        # diagnostics must measure the immutable output before those decisions.
        actual_list = prediction.get("original_results", prediction.get("results", [])) if prediction and not prediction.get("error") else []
        actual = {r["criterion_id"]: r for r in actual_list}
        if len(actual) != len(actual_list):
            raise ValueError("Duplicate predicted criterion")
        if not set(actual) <= criterion_ids:
            raise ValueError("Predictions contain unknown criteria")
        messages = {m["message_id"]: m["text"] for m in item["conversation"]["transcript"]}
        positions = {m["message_id"]: index for index, m in enumerate(item["conversation"]["transcript"])}
        for reference in expected:
            criterion_id = reference["criterion_id"]
            result = actual.get(criterion_id)
            pair = (_decision(reference), _decision(result))
            pairs.append(pair)
            by_criterion[criterion_id]["pairs"].append(pair)
            by_language[item["conversation"]["language"]].append(pair)
            if (reference["status"] == "not_applicable") != (result is not None and result.get("status") == "not_applicable"):
                by_criterion[criterion_id]["na_errors"] += 1
            if result is None:
                continue
            predicted_results_count += 1
            if result.get("status") == "applicable":
                applicable_results_count += 1
            if reference["status"] == "applicable" and result.get("status") == "applicable" and _numeric(reference.get("answer")) and _numeric(result.get("answer")):
                error = abs(reference["answer"] - result["answer"])
                numeric_errors.append(error)
                by_criterion[criterion_id]["errors"].append(error)
            for evidence in result.get("evidence", []):
                evidence_seen += 1
                quote = evidence.get("quote", "")
                if isinstance(quote, str) and quote.strip() and evidence.get("message_id") in messages and quote in messages[evidence["message_id"]]:
                    evidence_valid += 1
            span = result.get("checked_span")
            if span:
                span_seen += 1
                start, end = positions.get(span.get("start_message_id")), positions.get(span.get("end_message_id"))
                if start is not None and end is not None and start <= end:
                    span_valid += 1
            if type(result.get("evidence_supported_by_human")) is bool:
                semantic_reviewed += 1
                semantic_supported += int(result["evidence_supported_by_human"])
        expected_total = item.get("expected_total")
        actual_total = prediction.get("original_score", prediction.get("score", {})).get("total") if prediction and not prediction.get("error") else None
        if _numeric(expected_total) and _numeric(actual_total):
            total_errors.append(abs(expected_total - actual_total))
    def aggregate(subset: list[tuple[str, str]]) -> dict[str, Any]:
        return {"count": len(subset), "agreement": sum(a == b for a, b in subset) / len(subset) if subset else None,
                "macro_f1": _f1(subset), "observed_classes": len(set(a for a, _ in subset) | set(b for _, b in subset))}
    return {
        "metric_status": "internal_diagnostics_not_official_ranking",
        "prediction_basis": "original_model_outputs_before_appeals",
        "label_provenance": labels.get("provenance", "human_draft"), "split": split,
        "reviewed_conversations": len(expected_items), "unreviewed_conversations": sum(item.get("reviewed") is not True for item in labels["items"]),
        "missing_or_failed_predictions": missing_predictions,
        "decisions": aggregate(pairs),
        "result_coverage": predicted_results_count / len(pairs) if pairs else None,
        "applicable_result_fraction": applicable_results_count / len(pairs) if pairs else None,
        "numeric_answer_mae": sum(numeric_errors) / len(numeric_errors) if numeric_errors else None,
        "numeric_answer_mae_pairs": len(numeric_errors),
        "total_mae": sum(total_errors) / len(total_errors) if total_errors else None,
        "total_mae_pairs": len(total_errors),
        "quote_existence": {"valid": evidence_valid, "checked": evidence_seen,
                            "rate": evidence_valid / evidence_seen if evidence_seen else None},
        "span_existence": {"valid": span_valid, "checked": span_seen},
        "semantic_evidence_support": {"supported": semantic_supported, "human_reviewed": semantic_reviewed,
            "rate": semantic_supported / semantic_reviewed if semantic_reviewed else None},
        "by_criterion": {key: {**aggregate(value["pairs"]), "na_errors": value["na_errors"],
            "answer_mae": sum(value["errors"]) / len(value["errors"]) if value["errors"] else None,
            "answer_mae_pairs": len(value["errors"])} for key, value in by_criterion.items()},
        "by_language": {key: aggregate(value) for key, value in by_language.items()},
    }
