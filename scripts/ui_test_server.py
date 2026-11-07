"""Disposable localhost UI test server with a mechanical evaluator, never a model.

Use with browser_smoke.py and browser_readiness.py. The temporary database is
removed on shutdown. Results exercise UI wiring and have no semantic meaning.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# This process must not select the production path while importing the app module.
# It does not edit .env or change the environment of another running process.
os.environ["CQA_DATABASE_PATH"] = ":memory:"

from backend.cqa.evaluator.service import EvaluationRun
from backend.cqa.evaluator.validation import validate_draft
from backend.cqa.main import create_app
from backend.cqa.models import EvaluationDraft


async def mechanical_evaluate(conversation, checklist) -> EvaluationRun:
    """Arbitrary schema-valid answers; do not consult readiness expected labels."""
    await asyncio.sleep(0.2)  # Keep the asynchronous progress state observable.
    message = next((m for m in conversation.transcript if m.speaker == "operator" and m.text.strip()),
                   next(m for m in conversation.transcript if m.text.strip()))
    results = []
    for criterion in checklist.criteria:
        if criterion.answer_type == "boolean":
            answer = criterion.positive_answer
        elif criterion.answer_type == "scale":
            answer = max(criterion.allowed_values) if criterion.allowed_values else criterion.scale_max
        else:
            answer = max(criterion.choices, key=criterion.choices.get)
        # browser_smoke verifies that an appeal can change this initial result.
        if criterion.criterion_id == "example-greeting":
            answer = False
        results.append({"criterion_id": criterion.criterion_id, "status": "applicable", "answer": answer,
            "explanation": "Synthetic UI wiring output; not an AI assessment.",
            "evidence": [{"message_id": message.message_id, "quote": message.text}],
            "gross_error": criterion.critical_answer is not None and answer == criterion.critical_answer})
    draft = EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id,
        checklist_id=checklist.checklist_id, checklist_version=checklist.version,
        prompt_version="test", results=results)
    validate_draft(conversation, checklist, draft)
    return EvaluationRun(draft, {"model": "isolated-test-double", "mode": "test", "provider": "test"})


def create_test_app(database: Path):
    return create_app(database, evaluator=mechanical_evaluate,
        evaluator_info={"provider_configured": True, "provider": "test", "model": "isolated-test-double",
                        "mode": "test", "prompt_version": "test"},
        checklist_path=ROOT / "fixtures/checklist.example.json")


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=port_number, default=8021)
    args = parser.parse_args(argv)
    with TemporaryDirectory(prefix="cqa-ui-test-") as directory:
        app = create_test_app(Path(directory) / "isolated.sqlite3")
        print(f"Mechanical UI test server: http://127.0.0.1:{args.port}; temporary database; no model calls.", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
