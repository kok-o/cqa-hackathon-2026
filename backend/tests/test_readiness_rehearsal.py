from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.cqa.importer import normalize
from backend.cqa.models import DatasetExport
from evaluation.benchmark import conversation_key, read_json, write_json
from scripts import readiness_rehearsal as rehearsal


class ReadinessRehearsalTests(unittest.TestCase):
    def test_new_fixtures_and_canonical_expected_hashes(self):
        datasets, _ = rehearsal.audit_fixtures("all")
        self.assertEqual([len(d["data"]["records"]) for d in datasets], [10, 3])
        covered, counts = set(), []
        for path in rehearsal.files_for("all"):
            expected = read_json(path.with_name(path.name.replace("dataset", "expected")))
            self.assertTrue(expected["not_ground_truth"])
            counts.append(sum(len(x["expected_results"]) for x in expected["items"]))
            covered.update(c["criterion_id"] for x in expected["items"] for c in x["expected_results"])
        self.assertEqual(counts, [34, 7])
        self.assertEqual(covered, {"contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical"})
        new_ids = {r["conversation_id"] for d in datasets for r in d["data"]["records"]}
        for old_path in (rehearsal.ROOT / "fixtures/regression").glob("*dataset.json"):
            old = read_json(old_path)
            self.assertFalse(new_ids & {r["conversation_id"] for r in old["data"]["records"]})

    def test_offline_smoke_api_idempotency_malformed_atomicity_and_voice_boundary(self):
        datasets, rubric = rehearsal.audit_fixtures("all")
        with TemporaryDirectory() as temporary, patch("backend.cqa.evaluator.service.evaluate", side_effect=AssertionError("Provider must not be called")):
            stage = Path(temporary) / "smoke"
            report = rehearsal.api_stage(stage, datasets, rubric)
            self.assertEqual(report["provider_calls_in_this_stage"], 0)
            self.assertFalse(report["semantic_assertions_evaluated_in_this_stage"])
            self.assertEqual(report["injected_evaluator_executions"], 13)
            self.assertTrue(report["malformed_checks_passed"])
            self.assertEqual(len(report["malformed_imports"]), 5)
            self.assertTrue(all(x["status_code"] == 422 and x["zero_rows_written"] for x in report["malformed_imports"]))
            self.assertEqual(report["groups"][0]["official_split_checked"], "prod")
            self.assertIsNone(report["groups"][1]["official_split_checked"])
            self.assertEqual([x["validation"]["records"] for x in report["groups"]], [10, 3])
            # Mechanical stub never reads authored expectations or pretends to grade semantics.
            output = read_json(stage / "chats-submission-rehearsal.json")
            self.assertEqual(output["model"]["name"], "offline-contract-stub")
            self.assertTrue(all(not c["value"] for r in output["results"] for c in r["criteria"] if c["criterion_id"] == "critical"))

    def test_replay_preserves_failed_source_and_original_decisions_without_provider(self):
        datasets, rubric = rehearsal.audit_fixtures("chats")
        conversations = normalize(DatasetExport.model_validate(datasets[0]))
        records = {}
        for c in conversations:
            run = rehearsal.stub_run(c, rubric)
            records[conversation_key(c)] = {"conversation": c.model_dump(mode="json"),
                "checklist": rubric.model_dump(mode="json"), "status": "completed",
                "original_results": [x.model_dump(mode="json") for x in run.draft.results],
                "model_info": {"model": rehearsal.MODEL, "requested_model": rehearsal.MODEL,
                               "provider": "openai", "prompt_version": "mocked-saved-test-output"}}
        records[conversation_key(conversations[0])].update(status="failed", original_results=[])
        original = deepcopy(records)
        with TemporaryDirectory() as temporary, patch("backend.cqa.evaluator.service.evaluate", side_effect=AssertionError("Provider must not be called")):
            stage = Path(temporary) / "replay"
            report = rehearsal.api_stage(stage, datasets, rubric, records=records)
            self.assertEqual(report["stage"], "api_replay_saved_original_outputs")
            self.assertEqual(report["injected_evaluator_executions"], 10)
            self.assertEqual(report["groups"][0]["validation"]["errors"], 1)
            output = read_json(stage / "chats-submission-rehearsal.json")
            row = next(x for x in output["results"] if x["conversation_id"] == conversations[0].conversation_id)
            self.assertEqual(row["status"], "error")
            self.assertIsNone(row["score"])
            self.assertEqual(row["criteria"], [])
        self.assertEqual(records, original)

    def test_refuses_existing_database_directory_and_production_output_paths(self):
        datasets, rubric = rehearsal.audit_fixtures("chats")
        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "already exists"):
                rehearsal.api_stage(Path(temporary), datasets, rubric)
        for path in (rehearsal.ROOT / "data/runtime", rehearsal.ROOT / "results/dev", rehearsal.ROOT / "data/evaluations"):
            with self.assertRaisesRegex(ValueError, "production paths are forbidden"):
                rehearsal.safe_out(str(path))

    def test_plan_is_offline_and_omits_expectations_from_model_request_inputs(self):
        with TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()), patch("backend.cqa.evaluator.service.evaluate", side_effect=AssertionError("Provider must not be called")):
            out = Path(temporary) / "run"
            descriptor = rehearsal.plan(out, "all", 2)
            self.assertEqual(descriptor["planned_evaluations"], 13)
            manifest = read_json(out / "benchmark/manifest.json")
            self.assertEqual({x["filename"] for x in manifest["inputs"]["datasets"]}, {"chats-dataset.json", "voice-dataset.json"})
            self.assertTrue(any("expected" in path for path in descriptor["bundle_sha256"]))
            self.assertEqual(rehearsal.read_plan(out), descriptor)

    def test_live_budget_and_changed_bundle_rejected_before_dispatch(self):
        allowed = rehearsal.ROOT / "data/evaluations"
        allowed.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=allowed) as temporary:
            out = Path(temporary)
            descriptor = {"suite": "chats", "smoke_only": False, "concurrency": 2,
                          "bundle_sha256": rehearsal.bundle_hashes("chats")}
            write_json(out / "readiness-plan.json", descriptor)
            with patch.object(rehearsal.benchmark, "main") as dispatch, redirect_stderr(io.StringIO()):
                self.assertEqual(rehearsal.main(["live", "--out", str(out), "--budget-usd", "0.26"]), 2)
                self.assertEqual(rehearsal.main(["live", "--out", str(out)]), 2)
                with patch.object(rehearsal, "bundle_hashes", return_value={}):
                    self.assertEqual(rehearsal.main(["live", "--out", str(out), "--budget-usd", "0.25"]), 2)
                dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
