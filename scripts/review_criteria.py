"""Blind same-model review of explicitly selected criteria, preserving source evidence."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.cqa.evaluator.service import Settings, evaluate, PROMPT_VERSION
from backend.cqa.models import Conversation, ChecklistVersion
from backend.cqa.storage import digest
from evaluation.hybrid import review_checklist
from evaluation.submission import load_approved_checklist


async def run(args):
    source = json.loads(args.source.read_text(encoding="utf-8-sig"))
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    if not isinstance(plan, list) or not plan:
        raise ValueError("Plan must be a nonempty list of group_id, conversation_id, criterion_ids")
    settings = replace(Settings.from_env(), max_tokens=8192)
    if settings.model != "gpt-6-luna" or settings.reasoning_effort != "high":
        raise ValueError("This explicit review profile requires gpt-6-luna/high")
    approved = load_approved_checklist().model_dump(mode="json")
    selected, seen = [], set()
    for item in plan:
        key = (item["group_id"], item["conversation_id"])
        found = [r for r in source if (r["conversation"]["group_id"], r["conversation"]["conversation_id"]) == key]
        if len(found) != 1 or key in seen:
            raise ValueError("Unknown or duplicate source selection")
        seen.add(key)
        conversation = Conversation.model_validate(found[0]["conversation"])
        checklist = ChecklistVersion.model_validate(found[0]["checklist"])
        if checklist.model_dump(mode="json") != approved:
            raise ValueError("Full original rubric must match the approved checklist")
        scoped = review_checklist(checklist, item["criterion_ids"])
        selected.append((conversation, checklist, scoped))
    if args.out.exists():
        raise ValueError("Output already exists; reuse saved reviews instead of repeating calls")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    protocol = {"plan": plan, "source_records_sha256": digest(source), "prompt_version": PROMPT_VERSION,
                "model": settings.model, "previous_answers_provided": False, "expected_answers_provided": False}
    protocol_path = args.out.with_suffix(".protocol.json")
    if protocol_path.exists():
        raise ValueError("Protocol already exists; inspect a potentially interrupted run before retrying")
    protocol_path.write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    for conversation, checklist, scoped in selected:
        outcome = await evaluate(conversation, scoped, settings=settings)
        results.append({"group_id": conversation.group_id, "conversation_id": conversation.conversation_id,
                        "draft": outcome.draft.model_dump(mode="json"), "model_info": outcome.model_info,
                        "source_snapshot_sha256": digest(conversation.model_dump(mode="json")),
                        "checklist_snapshot_sha256": digest(checklist.model_dump(mode="json"))})
        args.out.with_suffix(".partial.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reviewed_conversations": len(results), "model": settings.model}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
