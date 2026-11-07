"""Compare synthetic authored expectations; never treat these as human ground truth."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

from .benchmark import conversation_key, decision, fingerprint, read_json, write_json


def compare(expected: dict, manifest: dict, checkpoint: dict) -> dict:
    if expected.get("provenance") != "synthetic_regression_authored_by_assistant" or expected.get("not_ground_truth") is not True:
        raise ValueError("Regression expectations must be explicitly marked as assistant-authored and not ground truth")
    if expected["checklist_sha256"] != manifest["inputs"]["checklist_sha256"]:
        raise ValueError("Regression expectations and run use different checklist snapshots")
    if expected["dataset_sha256"] not in {item["sha256"] for item in manifest["inputs"]["datasets"]}:
        raise ValueError("Regression expectations and run use different synthetic datasets")
    reference = {conversation_key(item): item for item in expected["items"]}
    if len(reference) != len(expected["items"]):
        raise ValueError("Duplicate regression conversation")
    output = []
    for model in manifest["inputs"]["models"]:
        planned = {conversation_key(t["conversation"]): t for t in manifest["tasks"] if t["model"] == model}
        if not set(planned) <= set(reference):
            raise ValueError("Run includes conversations with no regression expectation")
        records = {conversation_key(r["conversation"]): r for r in checkpoint["records"].values() if r["model"] == model}
        cases = []
        by_criterion = defaultdict(lambda: {"passed": 0, "checked": 0})
        for key in planned:
            item = reference[key]
            prediction = records.get(key)
            complete = prediction is not None and prediction.get("status") == "completed" and not prediction.get("error")
            actual = {r["criterion_id"]: r for r in prediction.get("original_results", [])} if complete else {}
            differences = []
            checked = passed = 0
            for expectation in item["expected_results"]:
                criterion = expectation["criterion_id"]
                result = actual.get(criterion)
                matched = result is not None and decision(result) == decision(expectation)
                if "gross_error" in expectation:
                    matched = matched and result.get("gross_error") is expectation["gross_error"]
                checked += 1
                passed += bool(matched)
                by_criterion[criterion]["checked"] += 1
                by_criterion[criterion]["passed"] += bool(matched)
                if not matched:
                    differences.append({"criterion_id": criterion,
                                        "expected": {k: v for k, v in expectation.items() if k != "rationale"},
                                        "actual": {k: result.get(k) for k in ("status", "answer", "gross_error")} if result else None,
                                        "authored_rationale": expectation.get("rationale")})
            cases.append({"group_id": item["group_id"], "conversation_id": item["conversation_id"],
                          "scenario": item["scenario"], "completed": complete, "checked": checked,
                          "passed": passed, "all_authored_expectations_passed": passed == checked, "differences": differences})
        total = sum(c["checked"] for c in cases)
        passed = sum(c["passed"] for c in cases)
        output.append({"model": model, "checked_decisions": total, "passed_decisions": passed,
                       "authored_regression_pass_rate": passed / total if total else None,
                       "cases_all_passed": sum(c["all_authored_expectations_passed"] for c in cases),
                       "cases_checked": len(cases), "by_criterion": dict(by_criterion), "cases": cases})
    return {"schema_version": 1, "metric_status": "synthetic_authored_regression_checks_not_human_or_organizer_accuracy",
            "provenance": expected["provenance"], "not_ground_truth": True,
            "prediction_basis": "original_model_outputs_before_appeals", "expectations_sha256": fingerprint(expected),
            "run_fingerprint": manifest["fingerprint"],
            "limitations": "Expected decisions were authored by an assistant for controlled software regression cases. They are fallible test assertions, not independent human labels, official answers, or a model-quality ranking.",
            "models": output}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", default="fixtures/regression/expected.json")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--out", help="Defaults to RUN_DIR/regression-report.json")
    args = parser.parse_args(argv)
    try:
        root = Path(args.run_dir)
        report = compare(read_json(args.expected), read_json(root / "manifest.json"), read_json(root / "checkpoint.json"))
        output = Path(args.out) if args.out else root / "regression-report.json"
        write_json(output, report)
        print(json.dumps({"report": str(output), "metric_status": report["metric_status"],
                          "models": [{k: v for k, v in m.items() if k not in {"cases", "by_criterion"}} for m in report["models"]]}, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, OSError) as exc:
        print(f"Regression error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
