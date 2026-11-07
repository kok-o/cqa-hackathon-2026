"""Small frozen adversarial plan/live/report wrapper around evaluation.benchmark.

Only `live` can call a model. Reports use original outputs and never assign GT.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.cqa.evaluator.service import PROMPT_PATH
from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, DatasetExport
from evaluation import benchmark
from evaluation.regression import compare
from evaluation.submission import export_submission, validate_submission
from scripts.import_benchmark import prepare_import

FIXTURES = ROOT / "fixtures/adversarial"
RUBRIC = ROOT / "fixtures/checklist.official.json"
DATASETS = [FIXTURES / f"{name}-dataset.json" for name in ("base", "variants")]
MODEL = "gpt-6-luna"
MAX_BUDGET = 0.25


def key(item):
    return item["group_id"], item["conversation_id"]


def audit_fixtures():
    checklist = ChecklistVersion.model_validate(benchmark.read_json(RUBRIC))
    conversations, expectations = {}, []
    for path in DATASETS:
        raw = benchmark.read_json(path)
        expected = benchmark.read_json(path.with_name(path.name.replace("dataset", "expected")))
        rows = normalize(DatasetExport.model_validate(raw))
        if (expected.get("not_ground_truth") is not True or expected.get("frozen_before_live_model_run") is not True
                or expected.get("provenance") != "synthetic_regression_authored_by_assistant"
                or expected["dataset_sha256"] != benchmark.fingerprint(raw)
                or expected["checklist_sha256"] != benchmark.fingerprint(checklist.model_dump(mode="json"))):
            raise ValueError("Expectation provenance or canonical source hash mismatch")
        if {key(x) for x in expected["items"]} != {(c.group_id, c.conversation_id) for c in rows}:
            raise ValueError("Expectation coverage differs from dataset")
        expectations.append(expected)
        for c in rows:
            ident = c.group_id, c.conversation_id
            if ident in conversations or c.channel != "agent chat":
                raise ValueError("Unexpected duplicate identity or non-chat input")
            conversations[ident] = c
    if len(conversations) != 12:
        raise ValueError("This frozen suite must contain exactly 12 chats")
    relations = benchmark.read_json(FIXTURES / "relations.json")
    if relations.get("not_ground_truth") is not True or relations.get("frozen_before_live_model_run") is not True:
        raise ValueError("Relations must be frozen assistant-authored checks, not GT")
    used = []
    for pair in relations["pairs"]:
        left, right = conversations[key(pair["left"])], conversations[key(pair["right"])]
        used += [key(pair["left"]), key(pair["right"])]
        a = [(m.speaker, m.text) for m in left.transcript]
        b = [(m.speaker, m.text) for m in right.transcript]
        if pair["kind"] == "metadata_only" and a != b:
            raise ValueError("Metadata-only pair changes transcript content")
        if pair["kind"] == "nonoperator_injection" and [x for x in a if x[0] == "operator"] != [x for x in b if x[0] == "operator"]:
            raise ValueError("Injection pair changed operator conduct")
        if pair["kind"] == "single_operator_turn_contrast":
            changed = [(x, y) for x, y in zip(a, b) if x != y]
            if len(a) != 260 or len(b) != 260 or len(changed) != 1 or any(x[0] != "operator" or y[0] != "operator" for x, y in changed):
                raise ValueError("Long-context pair must change exactly one operator turn in 260 turns")
    if len(used) != len(set(used)) or set(used) != set(conversations):
        raise ValueError("Every conversation must appear in exactly one pair")
    for anchor in relations["required_evidence"]:
        m = conversations[key(anchor)].transcript[anchor["message_index_zero_based"]]
        if m.speaker != anchor["speaker"] or not any(text in m.text for text in anchor["quote_contains_any"]):
            raise ValueError("Decisive evidence anchor does not match its source")
    return checklist, conversations, expectations, relations


def bundle_hashes():
    paths = [*DATASETS, *(p.with_name(p.name.replace("dataset", "expected")) for p in DATASETS),
             FIXTURES / "relations.json", RUBRIC, PROMPT_PATH, Path(__file__).resolve(),
             ROOT / "evaluation/benchmark.py", ROOT / "backend/cqa/evaluator/service.py",
             ROOT / "backend/cqa/evaluator/provider.py", ROOT / "backend/cqa/evaluator/validation.py",
             ROOT / "backend/cqa/models.py", ROOT / "backend/cqa/scoring.py"]
    return {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in paths}


def cli_args(out, concurrency):
    return ["--dataset", *map(str, DATASETS), "--checklist", str(RUBRIC), "--out", str(out / "benchmark"),
            "--models", MODEL, "--reasoning-effort", "high", "--max-output-tokens", "16384",
            "--timeout-seconds", "180", "--channel", "agent chat", "--per-language", "100",
            "--concurrency", str(concurrency), "--api-style", "responses"]


def plan(out, concurrency=2):
    audit_fixtures()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a new output directory; existing files are never replaced by planning")
    out.mkdir(parents=True, exist_ok=True)
    if benchmark.main(cli_args(out, concurrency) + ["--dry-run"]):
        raise ValueError("Benchmark planning failed without provider calls")
    manifest = benchmark.read_json(out / "benchmark/manifest.json")
    frozen = {"schema_version": 1, "not_ground_truth": True, "created_at": benchmark.utc_now(),
              "bundle_sha256": bundle_hashes(), "benchmark_fingerprint": manifest["fingerprint"],
              "normalized_model_configs": manifest["inputs"]["models"],
              "concurrency": concurrency, "maximum_budget_usd": MAX_BUDGET,
              "planned_evaluations": manifest["task_count"], "total_worst_case_usd": manifest["total_worst_case_usd"]}
    benchmark.write_json(out / "adversarial-plan.json", frozen)
    return frozen


def read_plan(out):
    frozen = benchmark.read_json(out / "adversarial-plan.json")
    if frozen["bundle_sha256"] != bundle_hashes():
        raise ValueError("Frozen inputs, expectations, relations, prompt or runner changed; use a newly reviewed plan")
    manifest = benchmark.read_json(out / "benchmark/manifest.json")
    if (manifest["fingerprint"] != frozen["benchmark_fingerprint"]
            or benchmark.fingerprint(manifest["inputs"]) != frozen["benchmark_fingerprint"]
            or manifest["inputs"]["models"] != frozen["normalized_model_configs"]):
        raise ValueError("Normalized model configuration or manifest changed after freezing")
    return frozen


def compare_relations(conversations, records, relations):
    pairs, evidence = [], []
    def result(ident, criterion):
        row = records.get(ident)
        if not row or row["status"] != "completed" or row.get("error"):
            return None
        return next((r for r in row["original_results"] if r["criterion_id"] == criterion), None)
    for pair in relations["pairs"]:
        for check in pair["relations"]:
            left, right = result(key(pair["left"]), check["criterion_id"]), result(key(pair["right"]), check["criterion_id"])
            available = all(r is not None and r.get("status") == "applicable" and type(r.get("answer")) is bool for r in (left, right))
            same = available and benchmark.decision(left) == benchmark.decision(right)
            passed = available and (same if check["relation"] == "equal" else not same)
            pairs.append({"pair_id": pair["pair_id"], "kind": pair["kind"], **check,
                          "available": available, "passed": passed,
                          "left": {k: left.get(k) for k in ("status", "answer")} if left else None,
                          "right": {k: right.get(k) for k in ("status", "answer")} if right else None})
    for anchor in relations["required_evidence"]:
        actual = result(key(anchor), anchor["criterion_id"])
        message = conversations[key(anchor)].transcript[anchor["message_index_zero_based"]]
        passed = bool(actual and actual["status"] == "applicable" and actual["answer"] is True and any(
            q["message_id"] == message.message_id and q["quote"] in message.text
            and any(text in q["quote"] for text in anchor["quote_contains_any"])
            for q in actual.get("evidence", [])))
        evidence.append({"group_id": anchor["group_id"], "conversation_id": anchor["conversation_id"],
                         "criterion_id": anchor["criterion_id"], "decisive_message_id": message.message_id,
                         "available": actual is not None, "passed": passed})
    return pairs, evidence


def report(out):
    frozen = read_plan(out)
    checklist, conversations, expectations, relations = audit_fixtures()
    folder = out / "benchmark"
    manifest, checkpoint = benchmark.read_json(folder / "manifest.json"), benchmark.read_json(folder / "checkpoint.json")
    if manifest["fingerprint"] != frozen["benchmark_fingerprint"]:
        raise ValueError("Frozen plan and manifest differ")
    prepare_import(run_dir=folder, model=MODEL)  # Read-only validation of hashes, sources, drafts, exact quotes and scores.
    terminal = {"completed", "failed", "budget_skipped", "configuration_skipped", "interrupted_unknown"}
    if len(checkpoint["records"]) != len(manifest["tasks"]) or any(r["status"] not in terminal for r in checkpoint["records"].values()):
        raise ValueError("Some tasks are unstarted or running; report after the bounded run finishes")
    records = {}
    for task in manifest["tasks"]:
        row = checkpoint["records"][task["task_id"]]
        ident = key(row["conversation"])
        if (ident not in conversations or ident in records or row["conversation"] != conversations[ident].model_dump(mode="json")
                or row["request_sha256"] != task["request_sha256"] or row["model"] != MODEL):
            raise ValueError("Checkpoint source/identity/request differs from the frozen suite")
        records[ident] = row
    authored = []
    for expected in expectations:
        group = expected["items"][0]["group_id"]
        comparison = compare(expected, manifest, checkpoint, group=group)
        authored.append(comparison)
    pairs, anchors = compare_relations(conversations, records, relations)
    # Twelve examples are a format-only supplement, never an official 10-row PROD.
    document = export_submission(list(conversations.values()), list(records.values()), team_name="SYNTHETIC-ADVERSARIAL-NOT-FOR-SUBMISSION",
        model_mode="external_api", model_name=MODEL, split=None, basis="original", approved_checklist=checklist)
    validation = validate_submission(document, conversations=list(conversations.values()), split=None)
    metrics = benchmark.aggregate_report(manifest, checkpoint)
    checks = sum(m["checked_decisions"] for a in authored for m in a["models"])
    passed = sum(m["passed_decisions"] for a in authored for m in a["models"])
    result = {"schema_version": 1, "not_ground_truth": True, "prediction_basis": "original_results_before_appeals",
              "run_fingerprint": manifest["fingerprint"], "authored_checks": {"passed": passed, "checked": checks},
              "relation_checks": {"passed": sum(x["passed"] for x in pairs), "checked": len(pairs)},
              "evidence_anchor_checks": {"passed": sum(x["passed"] for x in anchors), "checked": len(anchors)},
              "format_validation": validation, "official_split": None, "model_metrics": metrics["models"],
              "authored_details": authored, "pair_details": pairs, "evidence_details": anchors,
              "limitations": ["Assistant-authored fallible assertions, not human/organizer GT or accuracy.",
                  "Pair and evidence checks overlap individual assertions; do not add them as independent observations.",
                  "RU/KZ only; synthetic long contexts do not establish population robustness.",
                  "Error rows preserve missing/unresolved results with null scores; they are not successfully scored examples.",
                  "Usage estimates cover original attempts including retries, not provider invoices. Unknown cost remains unknown."]}
    benchmark.write_json(out / "format-supplement-not-prod.json", document)
    benchmark.write_json(out / "adversarial-report.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "live", "report"])
    parser.add_argument("--out", required=True)
    parser.add_argument("--concurrency", type=int, choices=[1, 2], default=2)
    parser.add_argument("--budget-usd", type=float)
    args = parser.parse_args(argv)
    try:
        out = Path(args.out).resolve()
        allowed = (ROOT / "data/evaluations").resolve()
        if out == allowed or not out.is_relative_to(allowed):
            raise ValueError("Output must be a run directory below ignored data/evaluations")
        if args.command == "plan":
            result = plan(out, args.concurrency)
        else:
            frozen = read_plan(out)
            if args.command == "live":
                if args.budget_usd is None or not 0 < args.budget_usd <= MAX_BUDGET:
                    raise ValueError("Live needs an explicit total budget in (0, 0.25], including retries/resume")
                code = benchmark.main(cli_args(out, frozen["concurrency"]) + ["--resume", "--budget-usd", str(args.budget_usd)])
                if code not in {0, 3}:
                    return code
            result = report(out)
        benchmark.safe_print(json.dumps({k: v for k, v in result.items() if k in {"planned_evaluations", "total_worst_case_usd",
            "authored_checks", "relation_checks", "evidence_anchor_checks", "format_validation", "model_metrics"}}, ensure_ascii=False))
        if args.command != "plan":
            counts = [result[n] for n in ("authored_checks", "relation_checks", "evidence_anchor_checks")]
            return 0 if all(x["passed"] == x["checked"] for x in counts) and result["format_validation"]["errors"] == 0 else 1
        return 0
    except (ValueError, KeyError, OSError) as exc:
        benchmark.safe_print(f"Adversarial rehearsal error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
