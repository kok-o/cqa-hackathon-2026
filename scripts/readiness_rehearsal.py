"""Budgeted synthetic rehearsal, then an isolated API replay of saved outputs.

Only the explicit `live` command can call a provider. `smoke` uses a mechanical
contract stub and never measures semantic quality. No production database is used.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.cqa.evaluator.service import EvaluationRun, EvaluatorError, PROMPT_PATH
from backend.cqa.evaluator.validation import validate_draft
from backend.cqa.importer import normalize
from backend.cqa.main import create_app
from backend.cqa.models import ChecklistVersion, DatasetExport, EvaluationDraft
from evaluation import benchmark
from evaluation.regression import compare
from evaluation.submission import export_submission, validate_submission
from scripts.import_benchmark import prepare_import

FIXTURES = ROOT / "fixtures/readiness"
RUBRIC = ROOT / "fixtures/checklist.official.json"
MODEL = "gpt-6-luna"
MAX_LIVE_BUDGET_USD = 0.25


def files_for(suite: str) -> list[Path]:
    return [FIXTURES / f"{name}-dataset.json" for name in ("chats", "voice") if suite in {name, "all"}]


def audit_fixtures(suite: str) -> tuple[list[dict], ChecklistVersion]:
    rubric = ChecklistVersion.model_validate(benchmark.read_json(RUBRIC))
    datasets = []
    seen = set()
    for path in files_for(suite):
        raw = benchmark.read_json(path)
        parsed = DatasetExport.model_validate(raw)
        is_chat = path.name.startswith("chats-")
        if len(parsed.data.records) != (10 if is_chat else 3):
            raise ValueError("Readiness fixture count changed; review and version the suite")
        if any(r.channel != ("agent chat" if is_chat else "voice call") for r in parsed.data.records):
            raise ValueError("Chat and voice fixtures must remain separate")
        expected = benchmark.read_json(path.with_name(path.name.replace("dataset", "expected")))
        if (expected.get("provenance") != "synthetic_regression_authored_by_assistant"
                or expected.get("not_ground_truth") is not True
                or expected["dataset_sha256"] != benchmark.fingerprint(raw)
                or expected["checklist_sha256"] != benchmark.fingerprint(rubric.model_dump(mode="json"))):
            raise ValueError("Fixture expectations have incorrect provenance or canonical hashes")
        source_ids = {r.conversation_id for r in parsed.data.records}
        if source_ids & seen:
            raise ValueError("Duplicate fixture identity")
        seen |= source_ids
        if {r["conversation_id"] for r in expected["items"]} != source_ids or len(expected["items"]) != len(source_ids):
            raise ValueError("Every fixture needs exactly one narrow expectation entry")
        for item in expected["items"]:
            checks = item["expected_results"]
            if item["group_id"] != parsed.data.dataset_group_id or not checks:
                raise ValueError("Invalid expectation group or empty check list")
            if len({c["criterion_id"] for c in checks}) != len(checks):
                raise ValueError("Duplicate expected criterion")
        datasets.append(raw)
    return datasets, rubric


def bundle_hashes(suite: str) -> dict:
    files = [RUBRIC, PROMPT_PATH, Path(__file__).resolve(), FIXTURES / "malformed.json"]
    for dataset in files_for(suite):
        files.extend([dataset, dataset.with_name(dataset.name.replace("dataset", "expected"))])
    return {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in files}


def safe_out(value: str) -> Path:
    out = Path(value).resolve()
    allowed = (ROOT / "data/evaluations").resolve()
    if out == allowed or not out.is_relative_to(allowed):
        raise ValueError("--out must be a new directory below data/evaluations; production paths are forbidden")
    return out


def benchmark_args(out: Path, suite: str, concurrency: int) -> list[str]:
    return ["--dataset", *map(str, files_for(suite)), "--checklist", str(RUBRIC),
            "--out", str(out / "benchmark"), "--models", MODEL, "--reasoning-effort", "high",
            "--max-output-tokens", "16384", "--timeout-seconds", "180", "--api-style", "responses",
            "--channel", "all", "--per-language", "100", "--concurrency", str(concurrency)]


def plan(out: Path, suite: str, concurrency: int, *, smoke: bool = False) -> dict:
    audit_fixtures(suite)
    if out.exists() and any(out.iterdir()):
        raise ValueError("Output directory is not empty; use the saved plan or choose a new directory")
    out.mkdir(parents=True, exist_ok=True)
    descriptor = {"schema_version": 1, "created_at": benchmark.utc_now(), "suite": suite,
                  "provenance": "synthetic_readiness_rehearsal_not_ground_truth", "not_ground_truth": True,
                  "smoke_only": smoke, "concurrency": concurrency, "model": MODEL,
                  "bundle_sha256": bundle_hashes(suite), "maximum_live_budget_usd": MAX_LIVE_BUDGET_USD,
                  "api_evidence": "In-process FastAPI replay after budgeted evaluator calls; not a second live-provider end-to-end test"}
    benchmark.write_json(out / "readiness-plan.json", descriptor)
    if not smoke:
        code = benchmark.main(benchmark_args(out, suite, concurrency) + ["--dry-run"])
        if code:
            raise ValueError("Benchmark planning failed; no provider request was made")
        manifest = benchmark.read_json(out / "benchmark/manifest.json")
        descriptor["benchmark_fingerprint"] = manifest["fingerprint"]
        descriptor["planned_evaluations"] = manifest["task_count"]
        descriptor["total_worst_case_usd"] = manifest["total_worst_case_usd"]
        benchmark.write_json(out / "readiness-plan.json", descriptor)
    return descriptor


def read_plan(out: Path) -> dict:
    descriptor = benchmark.read_json(out / "readiness-plan.json")
    if descriptor["bundle_sha256"] != bundle_hashes(descriptor["suite"]):
        raise ValueError("Frozen fixtures, expectations, rubric, prompt or runner changed; create a newly reviewed plan")
    audit_fixtures(descriptor["suite"])
    return descriptor


def stub_run(conversation, checklist) -> EvaluationRun:
    """Mechanically valid arbitrary answers, deliberately not expectation-driven."""
    span = {"start_message_id": conversation.transcript[0].message_id,
            "end_message_id": conversation.transcript[-1].message_id}
    results = [{"criterion_id": c.criterion_id, "status": "applicable",
                "answer": False if c.answer_type == "boolean" else c.scale_min,
                "explanation": "Mechanical offline API contract stub; not a semantic evaluation.",
                "evidence": [], "checked_span": span, "gross_error": False} for c in checklist.criteria]
    draft = EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id,
        checklist_id=checklist.checklist_id, checklist_version=checklist.version,
        prompt_version="offline-contract-stub", results=results)
    validate_draft(conversation, checklist, draft)
    return EvaluationRun(draft, {"provider": "local", "model": "offline-contract-stub",
                                "requested_model": "offline-contract-stub", "mode": "mock", "attempts": 0})


def api_stage(stage: Path, datasets: list[dict], checklist: ChecklistVersion, *, records: dict | None = None) -> dict:
    """Fresh DB; explicit injected stub/replay prevents any provider access."""
    if stage.exists():
        raise ValueError("API stage directory already exists; never reuse an unknown database")
    stage.mkdir(parents=True)
    calls = []
    smoke = records is None
    model = "offline-contract-stub" if smoke else MODEL

    async def injected(conversation, current_checklist):
        key = benchmark.conversation_key(conversation)
        calls.append(key)
        if smoke:
            return stub_run(conversation, current_checklist)
        record = records.get(key)
        if not record or record.get("status") != "completed":
            raise EvaluatorError("Saved evaluation did not complete; no score is fabricated.", code="saved_failure")
        if record["conversation"] != conversation.model_dump(mode="json") or record["checklist"] != current_checklist.model_dump(mode="json"):
            raise ValueError("Replay source snapshot differs from API import")
        draft = EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id,
            checklist_id=checklist.checklist_id, checklist_version=checklist.version,
            prompt_version=record["model_info"]["prompt_version"], results=record["original_results"])
        validate_draft(conversation, current_checklist, draft)
        return EvaluationRun(draft, {**deepcopy(record["model_info"]),
                                    "rehearsal_replay": True, "replayed_usage_is_not_new_billing": True})

    app = create_app(stage / "isolated.sqlite3", evaluator=injected,
                     evaluator_info={"provider_configured": False, "model": model,
                                     "mode": "offline_stub" if smoke else "saved_output_replay"}, checklist_path=RUBRIC)
    report = {"stage": "offline_contract_smoke" if smoke else "api_replay_saved_original_outputs",
              "provider_calls_in_this_stage": 0, "not_ground_truth": True,
              "semantic_assertions_evaluated_in_this_stage": False,
              "database": str(stage / "isolated.sqlite3"), "malformed_imports": [], "groups": [], "jobs": []}
    with TestClient(app) as client:
        for bad in benchmark.read_json(FIXTURES / "malformed.json")["cases"]:
            before = len(client.get("/api/conversations").json())
            response = client.post("/api/datasets/import", json={"dataset": bad["dataset"]})
            after = len(client.get("/api/conversations").json())
            passed = response.status_code == bad["expected_http_status"] and before == after
            report["malformed_imports"].append({"case_id": bad["case_id"], "status_code": response.status_code,
                                               "zero_rows_written": before == after, "passed": passed})
        for raw in datasets:
            normalized = normalize(DatasetExport.model_validate(raw))
            group = raw["data"]["dataset_group_id"]
            response = client.post("/api/datasets/import", json={"dataset": raw}); response.raise_for_status()
            again = client.post("/api/datasets/import", json={"dataset": raw}); again.raise_for_status()
            if again.json().get("unchanged") != len(normalized):
                raise ValueError("Repeated source import was not idempotent")
            for c in normalized:
                body = {"conversation_id": c.conversation_id, "group_id": group,
                        "checklist_id": checklist.checklist_id, "checklist_version": checklist.version,
                        "idempotency_key": "readiness-" + benchmark.fingerprint([group, c.conversation_id])}
                created = client.post("/api/evaluation-jobs", json=body); created.raise_for_status()
                job_id = created.json()["job_id"]
                duplicate = client.post("/api/evaluation-jobs", json=body); duplicate.raise_for_status()
                if duplicate.json()["job_id"] != job_id:
                    raise ValueError("Repeated API job did not preserve idempotency")
                job = client.get("/api/evaluation-jobs/" + job_id).json()
                report["jobs"].append({"group_id": group, "conversation_id": c.conversation_id, "status": job["status"]})
                if job["status"] == "completed" and not smoke:
                    saved = client.get("/api/evaluations/" + job["evaluation_id"]).json()
                    if saved["original_results"] != records[benchmark.conversation_key(c)]["original_results"]:
                        raise ValueError("API replay altered original model decisions")
            histories = client.get("/api/evaluations/export", params={"group_id": group}).json()
            is_chat = all(c.channel == "agent chat" for c in normalized)
            if is_chat:
                response = client.get("/api/submission/export", params={"group_id": group, "model_name": model,
                    "split": "prod", "team_name": "SYNTHETIC-READINESS-NOT-FOR-SUBMISSION",
                    "model_mode": "local" if smoke else "external_api"})
                response.raise_for_status()
                document = response.json()
                validation = validate_submission(document, conversations=normalized, split="prod")
            else:
                # Three voices cannot satisfy official DEV's 60 chats + 5 calls.
                response = client.get("/api/submission/export", params={"group_id": group, "model_name": model,
                    "split": "dev", "team_name": "SYNTHETIC-READINESS", "model_mode": "local" if smoke else "external_api"})
                if response.status_code != 422:
                    raise ValueError("Partial voice suite unexpectedly accepted as official DEV")
                document = export_submission(normalized, histories, team_name="SYNTHETIC-READINESS-VOICE-SUPPLEMENT",
                    model_mode="local" if smoke else "external_api", model_name=model, split=None)
                validation = validate_submission(document, conversations=normalized, split=None)
            filename = "chats-submission-rehearsal.json" if is_chat else "voice-format-supplement.json"
            benchmark.write_json(stage / filename, document)
            benchmark.write_json(stage / ("chats-history.json" if is_chat else "voice-history.json"), histories)
            report["groups"].append({"group_id": group, "official_split_checked": "prod" if is_chat else None,
                                     "validation": validation, "artifact": filename})
    if len(calls) != sum(len(d["data"]["records"]) for d in datasets):
        raise ValueError("API idempotency repeated or omitted evaluator execution")
    report["injected_evaluator_executions"] = len(calls)
    report["malformed_checks_passed"] = all(x["passed"] for x in report["malformed_imports"])
    benchmark.write_json(stage / "api-report.json", report)
    return report


def check(out: Path) -> dict:
    descriptor = read_plan(out)
    if descriptor["smoke_only"]:
        raise ValueError("A mechanical smoke cannot be scored as a model rehearsal")
    folder = out / "benchmark"
    manifest, checkpoint = benchmark.read_json(folder / "manifest.json"), benchmark.read_json(folder / "checkpoint.json")
    if manifest["fingerprint"] != descriptor["benchmark_fingerprint"]:
        raise ValueError("Readiness plan and benchmark manifest differ")
    # Validates manifest, selected sources, checklist, draft evidence and model identity.
    prepare_import(run_dir=folder, model=MODEL)
    terminal = {"completed", "failed", "budget_skipped", "configuration_skipped", "interrupted_unknown"}
    if len(checkpoint["records"]) != manifest["task_count"] or any(r["status"] not in terminal for r in checkpoint["records"].values()):
        raise ValueError("Benchmark has unstarted or running tasks; finish it before API replay")
    datasets, rubric = audit_fixtures(descriptor["suite"])
    records = {benchmark.conversation_key(r["conversation"]): r for r in checkpoint["records"].values()}
    stage = out / ("api-replay-" + uuid4().hex[:12])
    api = api_stage(stage, datasets, rubric, records=records)
    authored = []
    for path, raw in zip(files_for(descriptor["suite"]), datasets):
        result = compare(benchmark.read_json(path.with_name(path.name.replace("dataset", "expected"))), manifest,
                         checkpoint, group=raw["data"]["dataset_group_id"])
        name = path.name.replace("dataset", "authored-checks")
        benchmark.write_json(stage / name, result)
        authored.extend({"group_id": raw["data"]["dataset_group_id"], **{k: v for k, v in m.items() if k not in {"cases", "by_criterion"}}} for m in result["models"])
    result = {"not_ground_truth": True, "api_stage": api, "authored_checks": authored,
              "provider_usage_and_cost": benchmark.read_json(folder / "report.json")["models"],
              "cost_notice": "All original attempts are accounted in benchmark; API replay makes zero paid calls. Standard usage estimates are not invoices.",
              "interpretation": "A source-valid error record is faithful handling, not successful scoring. Client/system-only cases may legitimately remain unresolved on unasserted criteria.",
              "api_report": str(stage / "api-report.json")}
    benchmark.write_json(out / "readiness-report.json", result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "live", "check", "smoke"])
    parser.add_argument("--out", required=True)
    parser.add_argument("--suite", choices=["chats", "voice", "all"], default="all", help="Used only when creating a plan/smoke; live/check use its frozen suite")
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--budget-usd", type=float, help="Explicit live total cap, at most $0.25; not an added resume allowance")
    args = parser.parse_args(argv)
    try:
        out = safe_out(args.out)
        if args.command == "plan":
            result = plan(out, args.suite, args.concurrency)
        elif args.command == "smoke":
            plan(out, args.suite, args.concurrency, smoke=True)
            datasets, rubric = audit_fixtures(args.suite)
            result = api_stage(out / "api-smoke", datasets, rubric)
        elif args.command == "live":
            descriptor = read_plan(out)
            if descriptor["smoke_only"] or args.budget_usd is None or not 0 < args.budget_usd <= MAX_LIVE_BUDGET_USD:
                raise ValueError("Live requires a reviewed non-smoke plan and an explicit total budget in (0, 0.25]")
            code = benchmark.main(benchmark_args(out, descriptor["suite"], descriptor["concurrency"])
                + ["--resume", "--budget-usd", str(args.budget_usd)])
            if code not in {0, 3}:
                return code
            result = check(out)
        else:
            result = check(out)
        print(json.dumps({"command": args.command, "out": str(out),
                          "not_ground_truth": True, "provider_calls_in_check_or_smoke": 0,
                          "authored_checks": result.get("authored_checks"),
                          "planned_evaluations": result.get("planned_evaluations"),
                          "api_report": result.get("api_report")}, ensure_ascii=False))
        if args.command == "smoke":
            return 0 if result["malformed_checks_passed"] else 1
        if args.command in {"live", "check"}:
            matches = all(x["checked_decisions"] == x["passed_decisions"] for x in result["authored_checks"])
            usable = all(g["validation"]["errors"] == 0 for g in result["api_stage"]["groups"])
            return 0 if matches and usable and result["api_stage"]["malformed_checks_passed"] else 1
        return 0
    except (ValueError, KeyError, OSError, EvaluatorError) as exc:
        print(f"Readiness rehearsal error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
