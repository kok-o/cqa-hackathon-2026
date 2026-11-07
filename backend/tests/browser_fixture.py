"""Isolated browser-test app; never used by the normal CQA launcher.

Run with CQA_BROWSER_TEST=1 and a throwaway CQA_BROWSER_TEST_DB.
The evaluator below checks UI plumbing, not model quality.
"""

import os
from pathlib import Path

from backend.cqa.evaluator.service import EvaluationRun
from backend.cqa.models import CriterionResult, EvaluationDraft


async def evaluate_fixture(conversation, checklist):
    operator = next((message for message in conversation.transcript if message.speaker == "operator"), None)
    span = {
        "start_message_id": conversation.transcript[0].message_id,
        "end_message_id": conversation.transcript[-1].message_id,
    }
    results = []
    for criterion in checklist.criteria:
        if criterion.requires_audio or operator is None:
            result = CriterionResult(
                criterion_id=criterion.criterion_id, status="needs_review", answer=None,
                explanation="Изолированный тест интерфейса: нет материала для этой проверки.",
            )
        else:
            answer = False if criterion.answer_type == "boolean" else (
                criterion.scale_max if criterion.answer_type == "scale" else next(iter(criterion.choices))
            )
            result = CriterionResult(
                criterion_id=criterion.criterion_id, status="applicable", answer=answer,
                explanation="Тестовый ответ для проверки интерфейса. Это не AI-оценка и не эталон качества.",
                evidence=[] if answer is False else [{"message_id": operator.message_id, "quote": operator.text}],
                checked_span=span if answer is False else None,
            )
        results.append(result)
    return EvaluationRun(
        draft=EvaluationDraft(
            conversation_id=conversation.conversation_id, group_id=conversation.group_id,
            checklist_id=checklist.checklist_id, checklist_version=checklist.version,
            prompt_version="browser-test-fixture", results=results,
        ),
        model_info={"provider": "isolated-test-double", "model": "browser-test-fixture", "attempts": 0},
    )


def create_browser_app():
    if os.getenv("CQA_BROWSER_TEST") != "1" or not os.getenv("CQA_BROWSER_TEST_DB"):
        raise RuntimeError("Browser test requires explicit opt-in and an isolated database.")
    from backend.cqa.main import create_app

    return create_app(
        checklist_path=Path(__file__).resolve().parents[2] / "fixtures/checklist.example.json",
        database_path=os.environ["CQA_BROWSER_TEST_DB"], evaluator=evaluate_fixture,
        evaluator_info={
            "provider_configured": True, "provider": "isolated-test-double",
            "model": "browser-test-fixture", "mode": "test", "prompt_version": "browser-test-fixture",
        },
    )
