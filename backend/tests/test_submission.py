from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from backend.cqa.importer import normalize
from backend.cqa.models import DatasetExport
from evaluation.submission import (
    CRITERION_IDS, _read, checklist_criteria_fingerprint, export_submission,
    load_approved_checklist, main, official_score, validate_submission,
)


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.dataset = json.loads((FIXTURES / "synthetic_dev.json").read_text(encoding="utf-8"))
        self.conversation = normalize(DatasetExport.model_validate(self.dataset))[0]
        self.checklist = load_approved_checklist()
        # Exact criterion values from the appendix's fictional example; no real labels implied.
        values = (True, None, None, 15, True, 7, True, False)
        self.results = [{"criterion_id": name, "status": "not_applicable" if value is None else "applicable",
            "answer": value, "explanation": "Unit-test explanation", "evidence": [
                {"message_id": self.conversation.transcript[1].message_id, "quote": "Здравствуйте!"}],
            "checked_span": None, "gross_error": False} for name, value in zip(CRITERION_IDS, values)]
        self.record = {"conversation": self.conversation.model_dump(mode="json"),
            "checklist": self.checklist.model_dump(mode="json"), "original_results": deepcopy(self.results),
            "results": deepcopy(self.results), "revision": 1, "model_info": {"model": "gpt-6.1-sol"},
            "score": {"total": 0}, "original_score": {"total": 0}, "error": None}

    def export(self, **kwargs):
        return export_submission([self.conversation], [self.record], team_name="Test team",
                                 model_mode="external_api", **kwargs)

    def test_official_example_uses_raw_scales_na_weights_and_separate_critical(self):
        document = self.export()
        record = document["results"][0]
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["score"], {"final_score": 85.38, "critical_flag": False})
        self.assertEqual(record["criteria"][3]["value"], 15)
        record["criteria"][-1]["value"] = True
        self.assertEqual(official_score(record["criteria"]), {"final_score": 85.38, "critical_flag": True})

    def test_evidence_uses_source_one_based_order_including_system_and_client(self):
        self.record["original_results"][0]["checked_span"] = {
            "start_message_id": self.conversation.transcript[0].message_id,
            "end_message_id": self.conversation.transcript[2].message_id}
        document = self.export()
        self.assertEqual(document["results"][0]["criteria"][0]["evidence_turns"], [1, 2, 3])
        self.assertEqual(document["results"][0]["criteria"][4]["evidence_turns"], [2])

    def test_export_keeps_original_results_without_mutating_appeals_or_inputs(self):
        self.record["results"][0]["answer"] = False
        self.record["revision"] = 2
        self.record["appeals"] = [{"status": "accepted"}]
        before = deepcopy(self.record)
        original = self.export()
        current = self.export(basis="current")
        self.assertTrue(original["results"][0]["criteria"][0]["value"])
        self.assertFalse(current["results"][0]["criteria"][0]["value"])
        self.assertEqual(self.record, before)
        del self.record["original_results"]
        unresolved = self.export()["results"][0]
        self.assertEqual(unresolved["status"], "error")
        self.assertIsNone(unresolved["score"])

    def test_unresolved_or_forbidden_null_becomes_explicit_error_never_zero(self):
        for status in ("needs_review", "not_applicable"):
            with self.subTest(status=status):
                self.record["original_results"][0].update(status=status, answer=None)
                result = self.export()["results"][0]
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["criteria"], [])
                self.assertIsNone(result["score"])
                self.assertTrue(result["error"])

    def test_fabricated_quote_mismatched_source_and_wrong_scale_cannot_export_ok(self):
        original = deepcopy(self.record)
        mutations = [lambda r: r["original_results"][0]["evidence"][0].update(quote="not spoken"),
                     lambda r: r["conversation"]["transcript"][0].update(text="different source"),
                     lambda r: r["original_results"][3].update(answer=17)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.record = deepcopy(original)
                mutation(self.record)
                self.assertEqual(self.export()["results"][0]["status"], "error")

    def test_missing_and_failed_evaluations_preserve_each_source_id(self):
        second = self.conversation.model_copy(deep=True)
        second.conversation_id = "original-id-second"
        self.record["error"] = "Model timed out"
        document = export_submission([self.conversation, second], [self.record],
            team_name="Test", model_mode="external_api")
        self.assertEqual([r["conversation_id"] for r in document["results"]],
                         [self.conversation.conversation_id, "original-id-second"])
        self.assertTrue(all(r["status"] == "error" and r["score"] is None for r in document["results"]))

    def test_duplicate_evaluations_and_false_model_name_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Multiple evaluations"):
            export_submission([self.conversation], [self.record, self.record], team_name="Test", model_mode="external_api")
        with self.assertRaisesRegex(ValueError, "actually used"):
            self.export(model_name="unrelated-model")

    def test_edited_rules_are_not_silently_exported_as_official(self):
        self.record["checklist"]["criteria"][0]["instruction"] = "Give every operator a passing contact score"
        result = self.export()["results"][0]
        self.assertEqual(result["status"], "error")
        self.assertIn("approved", result["error"])
        self.assertIsNone(result["score"])

    def test_explicitly_approved_replacement_rules_can_be_selected(self):
        approved = self.checklist.model_copy(deep=True)
        approved.criteria[0].instruction = "Explicit replacement instruction supplied by judges"
        self.record["checklist"] = approved.model_dump(mode="json")
        self.assertEqual(self.export()["results"][0]["status"], "error")
        result = self.export(approved_checklist=approved)["results"][0]
        self.assertEqual(result["status"], "ok")

    def test_checklist_identity_and_order_do_not_change_rule_fingerprint(self):
        approved = self.checklist.model_copy(deep=True)
        approved.checklist_id = "copied-official-checklist"
        approved.version = 7
        approved.title = "Renamed checklist"
        approved.criteria.reverse()
        self.assertEqual(checklist_criteria_fingerprint(approved), checklist_criteria_fingerprint(self.checklist))
        self.record["checklist"] = approved.model_dump(mode="json")
        self.assertEqual(self.export()["results"][0]["status"], "ok")

    def test_even_explicit_approval_cannot_silently_change_json_1_scoring_contract(self):
        mutations = [lambda c: setattr(c.criteria[0], "weight", 99),
                     lambda c: setattr(c.criteria[0], "na_allowed", True),
                     lambda c: setattr(c.criteria[0], "positive_answer", False),
                     lambda c: setattr(c.criteria[3], "allowed_values", [0, 10, 20]),
                     lambda c: setattr(c.criteria[3], "higher_is_better", False),
                     lambda c: setattr(c.criteria[-1], "critical_answer", False),
                     lambda c: c.criteria.pop()]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                approved = self.checklist.model_copy(deep=True)
                mutation(approved)
                with self.assertRaisesRegex(ValueError, "updated submission contract"):
                    self.export(approved_checklist=approved)

    def test_validator_rejects_type_coercion_wrong_score_and_bad_evidence_turns(self):
        original = self.export()
        mutations = [lambda d: d["results"][0]["criteria"][0].update(value=1),
                     lambda d: d["results"][0]["criteria"][3].update(value=True),
                     lambda d: d["results"][0]["criteria"][5].update(value=5),
                     lambda d: d["results"][0]["criteria"][0].update(value=None),
                     lambda d: d["results"][0]["criteria"][0].update(evidence_turns=[0]),
                     lambda d: d["results"][0]["criteria"][0].update(evidence_turns=[True]),
                     lambda d: d["results"][0]["criteria"][0].update(evidence_turns=[999]),
                     lambda d: d["results"][0]["score"].update(final_score=85.381),
                     lambda d: d["results"][0]["score"].update(final_score=0),
                     lambda d: d["results"][0]["score"].update(critical_flag=True),
                     lambda d: d.update(schema_version=1),
                     lambda d: d.update(extra="not official")]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                document = deepcopy(original)
                mutation(document)
                with self.assertRaises(ValueError):
                    validate_submission(document, conversations=[self.conversation])

    def test_validator_rejects_duplicates_missing_ids_and_hides_no_failures(self):
        document = self.export()
        document["results"].append(deepcopy(document["results"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_submission(document, conversations=[self.conversation])
        document["results"] = []
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_submission(document, conversations=[self.conversation])
        document = self.export()
        document["results"][0] = {"conversation_id": self.conversation.conversation_id,
            "status": "error", "criteria": [], "score": 0, "error": "failed"}
        with self.assertRaisesRegex(ValueError, "null score"):
            validate_submission(document, conversations=[self.conversation])

    def test_empty_evidence_is_reported_without_inventing_an_undocumented_schema_rule(self):
        document = self.export()
        document["results"][0]["criteria"][1]["evidence_turns"] = []
        report = validate_submission(document, conversations=[self.conversation])
        self.assertEqual(len(report["warnings"]), 1)
        self.assertTrue(report["source_coverage_checked"])

    def test_split_completeness_is_exact(self):
        with self.assertRaisesRegex(ValueError, "10"):
            self.export(split="prod")
        with self.assertRaisesRegex(ValueError, "60"):
            self.export(split="dev")

    def test_cli_roundtrip_prod_errors_and_no_overwrite_of_source(self):
        dataset = deepcopy(self.dataset)
        source = dataset["data"]["records"][0]
        dataset["data"]["records"] = [dict(deepcopy(source), conversation_id=f"original-{index}") for index in range(10)]
        dataset["data"]["total_records"] = 10
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            dataset_path, predictions_path, out = directory / "source.json", directory / "predictions.json", directory / "results.json"
            dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
            predictions_path.write_text('{"evaluations": []}', encoding="utf-8")
            args = ["export", "--dataset", str(dataset_path), "--predictions", str(predictions_path), "--split", "prod",
                    "--team-name", "Команда", "--model-mode", "external_api", "--model-name", "configured-test-model", "--out", str(out)]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(["validate", "--dataset", str(dataset_path), "--split", "prod", "--input", str(out)]), 0)
                self.assertEqual(main(args), 2)
                self.assertEqual(main(args[:-1] + [str(dataset_path), "--replace"]), 2)
            document = _read(out)
            self.assertEqual(len(document["results"]), 10)
            self.assertEqual(document["team_name"], "Команда")
            self.assertTrue(all(record["status"] == "error" for record in document["results"]))
            self.assertEqual(_read(dataset_path), dataset)

    def test_json_reader_rejects_duplicate_keys_and_non_json_numbers(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"
            for text in ('{"schema_version":"1.0","schema_version":"2.0"}', '{"value":NaN}'):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    _read(path)


if __name__ == "__main__":
    unittest.main()
