"""Prepare human labels, run an explicitly requested pilot, compare predictions."""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sys

from backend.cqa.evaluator.service import EvaluatorError, evaluate, get_evaluator_info
from backend.cqa.evaluator.validation import validate_draft
from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, Conversation, DatasetExport, EvaluationDraft
from .metrics import summarize


def read(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path: str, data) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def prepare(args) -> None:
    if Path(args.out).exists():
        raise ValueError("Output already exists; choose a new path to preserve human labels")
    checklist = ChecklistVersion.model_validate(read(args.checklist))
    conversations = normalize(DatasetExport.model_validate(read(args.dataset)))
    groups = defaultdict(list)
    for conversation in conversations:
        groups[conversation.language].append(conversation)
    rng = random.Random(args.seed)
    items = []
    for language in sorted(groups):
        choices = groups[language]
        rng.shuffle(choices)
        selected = choices[:args.per_language]
        for index, conversation in enumerate(selected):
            items.append({"conversation": conversation.model_dump(mode="json"),
                "split": "dev" if index % 2 == 0 else "holdout", "source_family": None,
                "reviewed": False, "reviewer": "", "review_note": "",
                "expected_total": None,
                "expected_results": [{"criterion_id": c.criterion_id, "status": None, "answer": None,
                    "explanation": "", "evidence": [], "checked_span": None, "gross_error": False}
                    for c in checklist.criteria]})
    write(args.out, {"schema_version": 1, "provenance": "human_draft_not_organizer_ground_truth",
        "created_at": datetime.now(timezone.utc).isoformat(), "seed": args.seed,
        "checklist": checklist.model_dump(mode="json"), "items": items})
    print(f"Prepared {len(items)} unlabelled conversations. Group related templates/translations into one split before reviewing.")
    print("No model was called. Fill expected_results, then set reviewed=true; keep holdout hidden during tuning.")


def validate_labels(labels: dict, split: str) -> None:
    checklist = ChecklistVersion.model_validate(labels["checklist"])
    family_splits: dict[str, set[str]] = defaultdict(set)
    for item in labels["items"]:
        if item["split"] not in {"dev", "holdout"}:
            raise ValueError("Every label must use split dev or holdout")
        if item.get("source_family"):
            family_splits[item["source_family"]].add(item["split"])
        if not item.get("reviewed") or (split != "all" and item["split"] != split):
            continue
        conversation = Conversation.model_validate(item["conversation"])
        draft = EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id,
            checklist_id=checklist.checklist_id, checklist_version=checklist.version,
            prompt_version="human-label", results=item["expected_results"])
        validate_draft(conversation, checklist, draft)
    if any(len(splits) > 1 for splits in family_splits.values()):
        raise ValueError("Related source_family appears in both dev and holdout; fix the split first")


async def run(args) -> None:
    if Path(args.out).exists():
        raise ValueError("Output already exists; choose a new path to preserve past runs")
    info = get_evaluator_info()
    if not info["provider_configured"]:
        raise EvaluatorError(info.get("configuration_error", "Model is not configured"))
    labels = read(args.labels)
    validate_labels(labels, args.split)
    checklist = ChecklistVersion.model_validate(labels["checklist"])
    predictions = []
    selected = [item for item in labels["items"] if args.split == "all" or item["split"] == args.split]
    for index, item in enumerate(selected, 1):
        conversation = Conversation.model_validate(item["conversation"])
        record = {"conversation": conversation.model_dump(mode="json"),
            "checklist": checklist.model_dump(mode="json"), "results": [], "score": {}, "error": None}
        try:
            result = await evaluate(conversation, checklist)
            record["results"] = [r.model_dump(mode="json") for r in result.draft.results]
            record["model_info"] = result.model_info
            # Same implementation as the application, imported only after startup.
            from backend.cqa.scoring import calculate_score
            record["score"] = calculate_score(checklist, result.draft.results)
        except EvaluatorError as exc:
            record["error"] = str(exc)
        predictions.append(record)
        write(args.out, {"schema_version": 1, "split": args.split, "evaluations": predictions})
        print(f"{index}/{len(selected)}: {'failed' if record['error'] else 'evaluated'}")
    print("Run saved. Use score with the independently reviewed labels; do not tune on holdout.")


def score(args) -> None:
    labels = read(args.labels)
    validate_labels(labels, args.split)
    document = read(args.predictions)
    predictions = document if isinstance(document, list) else document["evaluations"]
    report = summarize(labels, predictions, args.split)
    if args.out:
        write(args.out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="CQA internal evaluation; no official ground truth is included.")
    commands = result.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="Make an unlabelled stratified dev/holdout template without model calls")
    prep.add_argument("--dataset", required=True)
    prep.add_argument("--checklist", default="fixtures/checklist.example.json")
    prep.add_argument("--out", required=True)
    prep.add_argument("--per-language", type=int, default=8)
    prep.add_argument("--seed", type=int, default=2026)
    for name, description in (("run", "Call configured model on an explicit split"),
                              ("score", "Compare predictions with reviewed human labels")):
        command = commands.add_parser(name, help=description)
        command.add_argument("--labels", required=True)
        command.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
        command.add_argument("--out", required=name == "run")
        if name == "score":
            command.add_argument("--predictions", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "prepare":
            if args.per_language < 1:
                raise ValueError("per-language must be positive")
            prepare(args)
        elif args.command == "run":
            asyncio.run(run(args))
        else:
            score(args)
    except (ValueError, KeyError, OSError, EvaluatorError) as exc:
        print(f"Evaluation error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
