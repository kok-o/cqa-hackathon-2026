from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.cqa.importer import normalize
from backend.cqa.main import create_app
from backend.cqa.models import Conversation, DatasetExport, EvaluationDraft


ROOT = Path(__file__).resolve().parents[2]
GROUP = "ATOMIC_SUBMISSION"
MODEL = "synthetic-snapshot-test"


class SubmissionSnapshotTests(unittest.TestCase):
    def sources(self, text):
        dataset = json.loads((ROOT / "fixtures/synthetic_dev.json").read_text(encoding="utf-8"))
        records = []
        for index in range(10):
            record = deepcopy(dataset["data"]["records"][0])
            record["conversation_id"] = f"snapshot-{index:02d}"
            record["transcript"][0]["text"] = text
            records.append(record)
        dataset["data"] = {"dataset_group_id": GROUP, "total_records": 10, "records": records}
        return normalize(DatasetExport.model_validate(dataset))

    def save_evaluations(self, store, sources, rules, contact):
        for source in sources:
            operator = next(message for message in source.transcript if message.speaker == "operator")
            results = []
            for criterion in rules.criteria:
                answer = criterion.scale_max if criterion.answer_type == "scale" else criterion.positive_answer
                if criterion.criterion_id == "contact":
                    answer = contact
                results.append({
                    "criterion_id": criterion.criterion_id, "status": "applicable", "answer": answer,
                    "explanation": "Synthetic snapshot isolation check.", "gross_error": False,
                    "evidence": [{"message_id": operator.message_id, "quote": operator.text}],
                })
            draft = EvaluationDraft(
                conversation_id=source.conversation_id, group_id=source.group_id,
                checklist_id=rules.checklist_id, checklist_version=rules.version,
                prompt_version="snapshot-test", results=results,
            )
            job, _ = store.create_job(source, rules)
            store.begin_job(job["job_id"])
            store.complete_job(job["job_id"], source, rules, draft,
                               {"model": MODEL, "requested_model": MODEL, "provider": "local"})

    def assert_export_scores(self, client, score):
        response = client.get("/api/submission/export", params={
            "group_id": GROUP, "split": "prod", "team_name": "Snapshot test",
            "model_name": MODEL, "model_mode": "local",
        })
        self.assertEqual(response.status_code, 200, response.text)
        results = response.json()["results"]
        self.assertEqual([row["status"] for row in results], ["ok"] * 10)
        self.assertEqual([row["score"]["final_score"] for row in results], [score] * 10)

    def exercise_concurrent_write(self, *, reimport):
        async def forbidden_evaluator(*args, **kwargs):
            raise AssertionError("Snapshot regression must not call a model provider")

        original_sources = self.sources("Source version one")
        changed_sources = self.sources("Source version two") if reimport else original_sources
        with TemporaryDirectory() as temporary:
            with TestClient(create_app(Path(temporary) / "test.sqlite3", evaluator=forbidden_evaluator)) as client:
                store = client.app.state.store
                current = store.list_checklists()[0]
                rules = store.checklist(current["checklist_id"], current["version"])
                store.import_conversations(original_sources, GROUP)
                self.save_evaluations(store, original_sources, rules, True)

                def concurrent_writer():
                    if reimport:
                        store.import_conversations(changed_sources, GROUP)
                    self.save_evaluations(store, changed_sources, rules, False)

                validate = Conversation.model_validate_json
                intercepted = False
                with ThreadPoolExecutor(max_workers=1) as writer:
                    def after_first_source(value, *args, **kwargs):
                        nonlocal intercepted
                        source = validate(value, *args, **kwargs)
                        if source.group_id == GROUP and not intercepted:
                            intercepted = True
                            # A different connection commits before this read continues.
                            # No sleeps or timing assumptions are needed in this race.
                            writer.submit(concurrent_writer).result(timeout=10)
                        return source

                    with patch.object(Conversation, "model_validate_json", side_effect=after_first_source):
                        self.assert_export_scores(client, 100)

                self.assertTrue(intercepted, "The concurrent write was not exercised")
                self.assertEqual(len(store.list_evaluations(group_id=GROUP)), 20)
                for source in changed_sources:
                    self.assertEqual(store.conversation(GROUP, source.conversation_id), source)
                # A subsequent request sees the committed version, while the first
                # request must consistently retain its earlier transaction snapshot.
                self.assert_export_scores(client, 90)

    def test_export_does_not_mix_sources_during_concurrent_reimport(self):
        self.exercise_concurrent_write(reimport=True)

    def test_export_does_not_include_evaluations_completed_after_snapshot(self):
        self.exercise_concurrent_write(reimport=False)


if __name__ == "__main__":
    unittest.main()
