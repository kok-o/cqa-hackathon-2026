"""Compare synthetic authored expectations; never treat these as human ground truth."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

from .benchmark import conversation_key, decision, fingerprint, read_json, write_json


def compare(expected: dict, manifest: dict, checkpoint: dict, *, group: str | None = None,
            ambiguities: dict | None = None) -> dict:
    tasks = manifest["tasks"]
    checkpoint_records = list(checkpoint["records"].values())
    skipped_tasks = skipped_records = 0
    if group is not None:
        if not isinstance(group, str) or not group.strip():
            raise ValueError("--group must be a non-empty group ID")
        tasks = [task for task in tasks if task["conversation"]["group_id"] == group]
        selected_records = [record for record in checkpoint_records if record["conversation"]["group_id"] == group]
        skipped_tasks = len(manifest["tasks"]) - len(tasks)
        skipped_records = len(checkpoint_records) - len(selected_records)
        checkpoint_records = selected_records
        if not tasks:
            raise ValueError("No planned conversations match --group")
    if expected.get("provenance") != "synthetic_regression_authored_by_assistant" or expected.get("not_ground_truth") is not True:
        raise ValueError("Regression expectations must be explicitly marked as assistant-authored and not ground truth")
    if expected["checklist_sha256"] != manifest["inputs"]["checklist_sha256"]:
        raise ValueError("Regression expectations and run use different checklist snapshots")
    if expected["dataset_sha256"] not in {item["sha256"] for item in manifest["inputs"]["datasets"]}:
        raise ValueError("Regression expectations and run use different synthetic datasets")
    reference = {conversation_key(item): item for item in expected["items"]}
    if len(reference) != len(expected["items"]):
        raise ValueError("Duplicate regression conversation")
    reviewed = {}
    if ambiguities is not None:
        if (ambiguities.get("provenance") != "assistant_expectation_review"
                or ambiguities.get("not_ground_truth") is not True
                or ambiguities.get("expected_sha256") != fingerprint(expected)):
            raise ValueError("Ambiguity review must identify the exact frozen expectations and assistant provenance")
        for item in ambiguities.get("items", []):
            key = (*conversation_key(item), item["criterion_id"])
            source = reference.get(conversation_key(item), {})
            criterion_ids = {x["criterion_id"] for x in source.get("expected_results", [])}
            alternatives = item.get("allowed_results", [])
            if (key in reviewed or item["criterion_id"] not in criterion_ids
                    or not isinstance(item.get("reason"), str) or not item["reason"].strip()
                    or len(alternatives) < 2
                    or len({decision(x) for x in alternatives}) < 2):
                raise ValueError("Invalid or duplicate ambiguity review entry")
            reviewed[key] = item
    if group is not None and any(conversation_key(record["conversation"]) not in reference for record in checkpoint_records):
        raise ValueError("Selected group includes records with no regression expectation")
    output = []
    for model in manifest["inputs"]["models"]:
        planned = {conversation_key(t["conversation"]): t for t in tasks if t["model"] == model}
        if not set(planned) <= set(reference):
            raise ValueError("Run includes conversations with no regression expectation")
        records = {conversation_key(r["conversation"]): r for r in checkpoint_records if r["model"] == model}
        cases = []
        by_criterion = defaultdict(lambda: {"passed": 0, "checked": 0})
        for key in planned:
            item = reference[key]
            prediction = records.get(key)
            complete = prediction is not None and prediction.get("status") == "completed" and not prediction.get("error")
            actual = {r["criterion_id"]: r for r in prediction.get("original_results", [])} if complete else {}
            differences = []
            ambiguous = []
            checked = passed = 0
            for expectation in item["expected_results"]:
                criterion = expectation["criterion_id"]
                result = actual.get(criterion)
                review = reviewed.get((*key, criterion))
                if review:
                    allowed = any(result is not None and decision(result) == decision(option)
                                  and ("gross_error" not in option or result.get("gross_error") is option["gross_error"])
                                  for option in review["allowed_results"])
                    ambiguous.append({"criterion_id": criterion, "within_allowed_set": allowed,
                                      "reason": review["reason"], "allowed_results": review["allowed_results"],
                                      "actual": {k: result.get(k) for k in ("status", "answer", "gross_error")} if result else None})
                    continue
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
                          "passed": passed, "all_authored_expectations_passed": complete and passed == checked
                          and all(a["within_allowed_set"] for a in ambiguous),
                          "differences": differences, "ambiguous": ambiguous})
        total = sum(c["checked"] for c in cases)
        passed = sum(c["passed"] for c in cases)
        output.append({"model": model, "checked_decisions": total, "passed_decisions": passed,
                       "authored_regression_pass_rate": passed / total if total else None,
                       "cases_all_passed": sum(c["all_authored_expectations_passed"] for c in cases),
                       "cases_checked": len(cases),
                       "ambiguous_decisions": sum(len(c["ambiguous"]) for c in cases),
                       "ambiguous_within_allowed_set": sum(a["within_allowed_set"] for c in cases for a in c["ambiguous"]),
                       "by_criterion": dict(by_criterion), "cases": cases})
    return {"schema_version": 1, "metric_status": "synthetic_authored_regression_checks_not_human_or_organizer_accuracy",
            "provenance": expected["provenance"], "not_ground_truth": True,
            "prediction_basis": "original_model_outputs_before_appeals", "expectations_sha256": fingerprint(expected),
            "ambiguity_review_sha256": fingerprint(ambiguities) if ambiguities is not None else None,
            "run_fingerprint": manifest["fingerprint"],
            "filtered_group": group, "skipped_planned_tasks": skipped_tasks,
            "skipped_records": skipped_records, "selected_records": len(checkpoint_records),
            "limitations": "Expected decisions were authored by an assistant for controlled software regression cases. They are fallible test assertions, not independent human labels, official answers, or a model-quality ranking.",
            "models": output}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", default="fixtures/regression/expected.json")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--group", help="Explicitly compare only this dataset group in a mixed run; unknown selected cases still fail validation")
    parser.add_argument("--ambiguities", help="Optional reviewed ambiguities bound to the frozen expectations; reported outside hard agreement")
    parser.add_argument("--out", help="Defaults to RUN_DIR/regression-report.json")
    args = parser.parse_args(argv)
    try:
        root = Path(args.run_dir)
        report = compare(read_json(args.expected), read_json(root / "manifest.json"), read_json(root / "checkpoint.json"), group=args.group,
                         ambiguities=read_json(args.ambiguities) if args.ambiguities else None)
        output = Path(args.out) if args.out else root / "regression-report.json"
        write_json(output, report)
        print(json.dumps({"report": str(output), "metric_status": report["metric_status"],
                          "filtered_group": report["filtered_group"], "skipped_records": report["skipped_records"],
                          "skipped_planned_tasks": report["skipped_planned_tasks"],
                          "models": [{k: v for k, v in m.items() if k not in {"cases", "by_criterion"}} for m in report["models"]]}, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, OSError) as exc:
        print(f"Regression error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
