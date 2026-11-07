from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.cqa.models import EvaluationDraft
from backend.cqa.scoring import calculate_score
from evaluation import benchmark
from scripts import adversarial_rehearsal as runner


class AdversarialRehearsalTests(unittest.TestCase):
    def saved_mock_run(self, out):
        with redirect_stdout(io.StringIO()):
            runner.plan(out)
        checklist, conversations, expected, relations = runner.audit_fixtures()
        targets = {runner.key(item): {r["criterion_id"]: r for r in item["expected_results"]} for e in expected for item in e["items"]}
        anchors = {runner.key(a): a for a in relations["required_evidence"]}
        manifest = benchmark.read_json(out / "benchmark/manifest.json")
        checkpoint = benchmark.read_json(out / "benchmark/checkpoint.json")
        for task in manifest["tasks"]:
            ident = runner.key(task["conversation"])
            c = conversations[ident]
            first = next(m for m in c.transcript if m.speaker == "operator")
            values = []
            for criterion in checklist.criteria:
                wanted = targets[ident].get(criterion.criterion_id)
                value = wanted["answer"] if wanted else False if criterion.answer_type == "boolean" else criterion.scale_min
                message = c.transcript[anchors[ident]["message_index_zero_based"]] if criterion.criterion_id == "critical" and ident in anchors else first
                values.append({"criterion_id": criterion.criterion_id, "status": "applicable", "answer": value,
                    "explanation": "Mocked unit-test output, not a model quality measurement.",
                    "evidence": [{"message_id": message.message_id, "quote": message.text}],
                    "gross_error": criterion.criterion_id == "critical" and value is True})
            draft = EvaluationDraft(conversation_id=c.conversation_id, group_id=c.group_id,
                checklist_id=checklist.checklist_id, checklist_version=checklist.version,
                prompt_version=manifest["inputs"]["prompt_version"], results=values)
            results = [r.model_dump(mode="json") for r in draft.results]
            score = calculate_score(checklist, draft.results)
            checkpoint["records"][task["task_id"]] = {**task, "checklist": checklist.model_dump(mode="json"),
                "status": "completed", "error": None, "original_results": results, "results": results,
                "original_score": score, "score": score, "wall_time_ms": 1,
                "started_at": "2026-11-07T00:00:00+00:00", "actual_cost_usd": None,
                "model_info": {"model": runner.MODEL, "requested_model": runner.MODEL, "mode": "unit_test_mock",
                    "prompt_version": draft.prompt_version, "attempts": 1, "usage_complete": False}}
        benchmark.write_json(out / "benchmark/checkpoint.json", checkpoint)
        return checkpoint

    def test_new_pair_design_and_decisive_turns_are_frozen_and_grounded(self):
        _, rows, expected, relations = runner.audit_fixtures()
        self.assertEqual(len(rows), 12)
        self.assertEqual(sum(len(c.transcript) == 260 for c in rows.values()), 4)
        self.assertEqual(sum(len(i["expected_results"]) for e in expected for i in e["items"]), 25)
        self.assertEqual(len(relations["pairs"]), 6)
        self.assertEqual(len(relations["required_evidence"]), 4)
        new_ids = {c.conversation_id for c in rows.values()}
        for folder in ("regression", "readiness"):
            for p in (runner.ROOT / "fixtures" / folder).glob("*dataset.json"):
                self.assertFalse(new_ids & {r["conversation_id"] for r in benchmark.read_json(p)["data"]["records"]})

    def test_saved_report_checks_relations_and_anchors_without_live_calls(self):
        with TemporaryDirectory() as temporary, patch("backend.cqa.evaluator.service.evaluate", side_effect=AssertionError("No live calls")):
            out = Path(temporary) / "run"
            self.saved_mock_run(out)
            result = runner.report(out)
            self.assertTrue(result["not_ground_truth"])
            self.assertEqual(result["authored_checks"], {"passed": 25, "checked": 25})
            self.assertEqual(result["relation_checks"], {"passed": 12, "checked": 12})
            self.assertEqual(result["evidence_anchor_checks"], {"passed": 4, "checked": 4})
            self.assertEqual(result["format_validation"]["ok"], 12)
            self.assertIsNone(result["official_split"])
            self.assertIsNone(result["model_metrics"][0]["total_cost_usd"])

    def test_failure_preserved_and_wrong_but_real_quote_fails_anchor(self):
        with TemporaryDirectory() as temporary:
            out = Path(temporary) / "run"
            cp = self.saved_mock_run(out)
            for row in cp["records"].values():
                cid = row["conversation"]["conversation_id"]
                if cid == "adv-meta-base":
                    row.update(status="failed", error="Mocked failure", original_results=[], results=[], original_score={}, score={})
                if cid == "adv-long-ru-middle":
                    first = next(m for m in row["conversation"]["transcript"] if m["speaker"] == "operator")
                    critical = next(r for r in row["original_results"] if r["criterion_id"] == "critical")
                    critical["evidence"] = [{"message_id": first["message_id"], "quote": first["text"]}]
            benchmark.write_json(out / "benchmark/checkpoint.json", cp)
            result = runner.report(out)
            self.assertEqual(result["format_validation"]["errors"], 1)
            self.assertEqual(result["evidence_anchor_checks"]["passed"], 3)
            self.assertEqual(result["relation_checks"]["passed"], 10)
            document = benchmark.read_json(out / "format-supplement-not-prod.json")
            failed = next(r for r in document["results"] if r["conversation_id"] == "adv-meta-base")
            self.assertIsNone(failed["score"])
            self.assertEqual(failed["criteria"], [])

    def test_source_tampering_is_rejected(self):
        with TemporaryDirectory() as temporary:
            out = Path(temporary) / "run"
            cp = self.saved_mock_run(out)
            next(iter(cp["records"].values()))["conversation"]["transcript"][0]["text"] += " changed"
            benchmark.write_json(out / "benchmark/checkpoint.json", cp)
            with self.assertRaisesRegex(ValueError, "snapshot"):
                runner.report(out)

    def test_live_rejects_budget_or_config_tampering_before_dispatch(self):
        with TemporaryDirectory(dir=runner.ROOT / "data/evaluations") as temporary:
            out = Path(temporary) / "run"
            with redirect_stdout(io.StringIO()):
                runner.plan(out)
            with patch.object(runner.benchmark, "main") as call, redirect_stderr(io.StringIO()):
                self.assertEqual(runner.main(["live", "--out", str(out), "--budget-usd", ".26"]), 2)
                manifest = benchmark.read_json(out / "benchmark/manifest.json")
                manifest["inputs"]["models"][runner.MODEL]["reasoning_effort"] = "max"
                benchmark.write_json(out / "benchmark/manifest.json", manifest)
                self.assertEqual(runner.main(["live", "--out", str(out), "--budget-usd", ".25"]), 2)
                call.assert_not_called()

    def test_wrapper_summary_and_error_are_safe_on_real_strict_cp1251_stream(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1251", errors="strict")
        out = runner.ROOT / "data/evaluations/unused-unit-test-Қазақ"
        result = {"planned_evaluations": 12, "format_validation": {"warnings": ["топ/қ: evidence note"]}}
        with patch.object(runner, "plan", return_value=result), redirect_stdout(stream):
            self.assertEqual(runner.main(["plan", "--out", str(out)]), 0)
        with patch.object(runner, "read_plan", side_effect=ValueError("Қазақ source mismatch")), redirect_stderr(stream):
            self.assertEqual(runner.main(["report", "--out", str(out)]), 2)
        stream.flush()
        emitted = buffer.getvalue().decode("cp1251")
        self.assertIn("\\u049b", emitted)
        self.assertIn("\\u049a", emitted)
        self.assertIn("Adversarial rehearsal error", emitted)


if __name__ == "__main__":
    unittest.main()
