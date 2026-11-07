from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, CriterionResult, DatasetExport, EvaluationDraft
from backend.cqa.scoring import calculate_score
from backend.cqa.storage import ConflictError, Store, digest
from scripts.import_benchmark import import_plan, prepare_import

ROOT = Path(__file__).resolve().parents[2]
MODEL = "gpt-6.1-sol"


class ImportBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.database = self.folder / "app.sqlite3"
        read = lambda name: json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))
        self.conversation = normalize(DatasetExport.model_validate(read("synthetic_dev.json")))[0]
        self.checklist = ChecklistVersion.model_validate(read("checklist.example.json"))
        self.draft = EvaluationDraft(conversation_id=self.conversation.conversation_id, group_id=self.conversation.group_id,
                                    checklist_id=self.checklist.checklist_id, checklist_version=self.checklist.version,
                                    prompt_version="recorded-prompt-v1", results=read("evaluation.example.json")["results"])
        self.record = {"status": "completed", "model": MODEL,
                       "conversation": self.conversation.model_dump(mode="json"),
                       "checklist": self.checklist.model_dump(mode="json"),
                       "original_results": [r.model_dump(mode="json") for r in self.draft.results],
                       "original_score": calculate_score(self.checklist, self.draft.results),
                       "model_info": {"model": MODEL, "requested_model": MODEL, "prompt_version": "recorded-prompt-v1",
                                      "input_tokens": 100, "output_tokens": 80, "reasoning_tokens": 50,
                                      "estimated_cost": 0.001, "attempts": 1, "usage_complete": True},
                       "request_sha256": "request-hash", "revision": 1, "error": None,
                       "started_at": "2026-11-07T10:00:00Z", "finished_at": "2026-11-07T10:00:02Z", "wall_time_ms": 2000}
        self.record["results"] = deepcopy(self.record["original_results"])
        self.record["score"] = deepcopy(self.record["original_score"])

    def write(self, name, value):
        path = self.folder / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def prediction_plan(self, record=None, **kwargs):
        document = {"schema_version": 1, "model": MODEL, "fingerprint": "fixed-original-run",
                    "evaluations": [record if record is not None else self.record]}
        return prepare_import(predictions=self.write("prediction.json", document), **kwargs)

    def create_store(self, seed=None):
        store = Store(self.database)
        store.initialize(seed or self.checklist)
        self.addCleanup(store.close)
        return store

    def test_offline_import_is_idempotent_and_preserves_metadata_and_appeals(self):
        plan = self.prediction_plan()
        with patch("backend.cqa.evaluator.service.evaluate", side_effect=AssertionError("Import must never call a model")) as evaluate:
            report = import_plan(plan, self.database)
            evaluate.assert_not_called()
        self.assertEqual(report["imported_evaluations"], 1)
        store = Store(self.database)
        self.addCleanup(store.close)
        evaluation = store.list_evaluations()[0]
        self.assertEqual(evaluation["original_results"], self.record["original_results"])
        self.assertEqual(evaluation["original_score"], self.record["original_score"])
        for name, value in self.record["model_info"].items():
            self.assertEqual(evaluation["model_info"][name], value)
        self.assertEqual(evaluation["model_info"]["benchmark_import"]["source_record_metadata"]["wall_time_ms"], 2000)
        replacement = CriterionResult.model_validate(evaluation["results"][0])
        replacement.answer = not replacement.answer
        appeal = store.create_appeal(evaluation["evaluation_id"], replacement.criterion_id, "Human review")
        store.decide_appeal(appeal["appeal_id"], "replace", "Reviewed separately", replacement, 1)
        repeated = import_plan(plan, self.database)
        self.assertEqual(repeated["already_imported"], 1)
        self.assertEqual(repeated["imported_evaluations"], 0)
        evaluations = store.list_evaluations()
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(evaluations[0]["revision"], 2)
        self.assertEqual(evaluations[0]["original_results"], self.record["original_results"])
        self.assertNotEqual(evaluations[0]["results"], evaluations[0]["original_results"])

    def test_import_leaves_other_queued_and_running_jobs_untouched(self):
        store = self.create_store()
        store.import_conversations([self.conversation], self.conversation.group_id)
        queued, _ = store.create_job(self.conversation, self.checklist, "existing-app-queued")
        running, _ = store.create_job(self.conversation, self.checklist, "existing-app-running")
        store.begin_job(running["job_id"])
        report = import_plan(self.prediction_plan(), self.database)
        self.assertEqual(report["imported_evaluations"], 1)
        self.assertEqual(store.job(queued["job_id"])["status"], "queued")
        self.assertEqual(store.job(running["job_id"])["status"], "running")
        # Application-startup recovery keeps its prior default behavior.
        store.initialize(self.checklist)
        self.assertEqual(store.job(queued["job_id"])["status"], "failed")
        self.assertEqual(store.job(running["job_id"])["status"], "failed")

    def test_dry_run_creates_no_database_and_does_not_recover_jobs(self):
        plan = self.prediction_plan()
        report = import_plan(plan, self.database, dry_run=True)
        self.assertEqual(report["would_import"], 1)
        self.assertFalse(self.database.exists())
        store = self.create_store()
        job, _ = store.create_job(self.conversation, self.checklist, "active-job")
        store.begin_job(job["job_id"])
        before = self.database.read_bytes()
        import_plan(plan, self.database, dry_run=True)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(store.job(job["job_id"])["status"], "running")
        self.assertEqual(store.list_evaluations(), [])

    def test_changed_existing_conversation_is_rejected_without_overwrite(self):
        store = self.create_store()
        changed = self.conversation.model_copy(deep=True)
        changed.transcript[0].text = "Newer source content already imported into the app"
        store.import_conversations([changed], changed.group_id)
        with self.assertRaisesRegex(ConflictError, "snapshot differs"):
            import_plan(self.prediction_plan(), self.database)
        self.assertEqual(store.conversation(*[changed.group_id, changed.conversation_id]), changed)
        self.assertEqual(store.list_evaluations(), [])

    def test_checklist_versions_are_exact_and_conflicting_snapshot_is_rejected(self):
        changed = self.checklist.model_copy(deep=True)
        changed.title = "Different existing rubric with the same version identity"
        store = self.create_store(changed)
        with self.assertRaisesRegex(ConflictError, "checklist version differs"):
            import_plan(self.prediction_plan(), self.database)
        self.assertEqual(store.checklist(changed.checklist_id, changed.version).title, changed.title)
        record = deepcopy(self.record)
        record["checklist"]["version"] = 7
        import_plan(self.prediction_plan(record), self.database)
        self.assertEqual(store.checklist(self.checklist.checklist_id, 7).version, 7)
        self.assertEqual({c["version"] for c in store.list_checklists()}, {1, 7})

    def test_invalid_evidence_is_rejected_before_destination_creation(self):
        record = deepcopy(self.record)
        record["original_results"][0]["evidence"][0]["quote"] = "Not present in the transcript"
        with self.assertRaisesRegex(ValueError, "exact source quote"):
            self.prediction_plan(record)
        self.assertFalse(self.database.exists())

    def test_failed_records_are_skipped_but_completed_needs_review_is_imported(self):
        record = deepcopy(self.record)
        record["original_results"][0].update(status="needs_review", answer=None, evidence=[], checked_span=None)
        draft = self.draft.model_copy(update={"results": [CriterionResult.model_validate(r) for r in record["original_results"]]})
        record["original_score"] = calculate_score(self.checklist, draft.results)
        failed = {"model": MODEL, "status": "failed", "error": "Provider failed", "results": []}
        document = {"model": MODEL, "fingerprint": "needs-review-run", "evaluations": [record, failed]}
        plan = prepare_import(predictions=self.write("partial.json", document))
        report = import_plan(plan, self.database)
        self.assertEqual(report["completed_records"], 1)
        self.assertEqual(report["needs_review_records"], 1)
        self.assertEqual(report["skipped_failed_records"], 1)
        self.assertEqual(report["imported_evaluations"], 1)
        store = Store(self.database)
        self.addCleanup(store.close)
        self.assertEqual(store.list_evaluations()[0]["score"]["status"], "incomplete")

    def test_changed_output_under_same_fingerprint_is_not_silently_reused(self):
        import_plan(self.prediction_plan(), self.database)
        changed = deepcopy(self.record)
        changed["original_results"][0]["explanation"] = "Different model output under the old fingerprint"
        with self.assertRaisesRegex(ConflictError, "different original model output"):
            import_plan(self.prediction_plan(changed), self.database)

    def test_mixed_model_source_requires_explicit_selection(self):
        other = deepcopy(self.record)
        other["model"] = "gpt-6-luna"
        other["model_info"].update(model="gpt-6-luna", requested_model="gpt-6-luna")
        source = self.write("mixed.json", {"fingerprint": "mixed-run", "evaluations": [self.record, other]})
        with self.assertRaisesRegex(ValueError, "multiple models"):
            prepare_import(predictions=source)
        plan = prepare_import(predictions=source, model=MODEL)
        self.assertEqual(len(plan.items), 1)
        self.assertEqual(plan.counts["other_model_records"], 1)

    def test_legacy_model_metadata_cannot_contradict_the_selected_model(self):
        record = deepcopy(self.record)
        record["model_info"].pop("requested_model")
        record["model_info"]["model"] = "a-different-configured-model"
        with self.assertRaisesRegex(ValueError, "metadata contradicts"):
            self.prediction_plan(record)

    def test_checkpoint_import_verifies_manifest_and_source_snapshot_hashes(self):
        inputs = {"models": {MODEL: {}}, "checklist_sha256": digest(self.checklist.model_dump(mode="json")),
                  "prompt_version": "recorded-prompt-v1",
                  "selected_conversations_sha256": digest([self.conversation.model_dump(mode="json")])}
        manifest = {"inputs": inputs, "fingerprint": digest(inputs), "checklist": self.checklist.model_dump(mode="json"),
                    "tasks": [{"model": MODEL, "conversation": self.conversation.model_dump(mode="json"),
                               "request_sha256": self.record["request_sha256"]}]}
        checkpoint = {"fingerprint": digest(inputs), "records": {"one": self.record}}
        self.write("manifest.json", manifest)
        self.write("checkpoint.json", checkpoint)
        plan = prepare_import(run_dir=self.folder)
        self.assertEqual(len(plan.items), 1)
        checkpoint["records"]["one"] = deepcopy(self.record)
        checkpoint["records"]["one"]["conversation"]["transcript"][0]["text"] = "Tampered source snapshot"
        self.write("checkpoint.json", checkpoint)
        with self.assertRaisesRegex(ValueError, "planned conversation snapshot"):
            prepare_import(run_dir=self.folder)
        manifest["tasks"][0]["conversation"] = deepcopy(checkpoint["records"]["one"]["conversation"])
        self.write("manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "selected source snapshot hash"):
            prepare_import(run_dir=self.folder)
        checkpoint["fingerprint"] = "wrong-run"
        self.write("checkpoint.json", checkpoint)
        with self.assertRaisesRegex(ValueError, "fingerprint integrity"):
            prepare_import(run_dir=self.folder)


if __name__ == "__main__":
    unittest.main()
