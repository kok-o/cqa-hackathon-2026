"""Reproducible, budget-reserved model comparison; no ground truth is invented.

Run ``python -m evaluation.benchmark --help``. Provider requests happen only when
--dry-run is absent. Checkpoints retain the original predictions before appeals.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import csv
import io
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import sys
import time
from typing import Any

from dotenv import load_dotenv

from backend.cqa.evaluator import service
from backend.cqa.evaluator.service import EvaluatorError, Settings, evaluate
from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, Conversation, DatasetExport
from backend.cqa.scoring import calculate_score
from .cli import validate_labels
from .metrics import summarize

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = ["gpt-6-luna"]
LANGUAGES = ("RU", "KZ", "EN")
SCHEMA_VERSION = 1
MAX_ATTEMPTS = 2  # Evaluator contract: initial request plus one validation/network retry.


def safe_print(message: str, *, file=None, flush: bool = False) -> None:
    """Console encoding must not turn a saved result into a cancelled run.

    Keep Unicode on capable streams; escape only characters the actual stream
    cannot encode (for example Kazakh IDs on a strict Windows cp1251 console).
    """
    stream = sys.stdout if file is None else file
    try:
        print(message, file=stream, flush=flush)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        printable = message.encode(encoding, errors="backslashreplace").decode(encoding)
        print(printable, file=stream, flush=flush)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def fingerprint(value: Any) -> str:
    return sha256(canonical(value)).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def conversation_key(conversation: Conversation | dict) -> str:
    c = conversation.model_dump() if isinstance(conversation, Conversation) else conversation
    return json.dumps([c["group_id"], c["conversation_id"]], ensure_ascii=False, separators=(",", ":"))


def select_conversations(conversations: list[Conversation], *, per_language: int, seed: int,
                         groups: list[str] | None = None, ids: list[str] | None = None,
                         channel: str = "agent chat") -> list[Conversation]:
    """Stable input-order-independent language-balanced sampling, then group round robin."""
    if per_language < 1:
        raise ValueError("--per-language must be positive")
    seen = set()
    buckets: dict[str, dict[str, list[Conversation]]] = defaultdict(lambda: defaultdict(list))
    for c in conversations:
        key = conversation_key(c)
        if key in seen:
            raise ValueError("Duplicate conversation key across datasets")
        seen.add(key)
        if groups and c.group_id not in groups:
            continue
        if ids and c.conversation_id not in ids and f"{c.group_id}/{c.conversation_id}" not in ids:
            continue
        if channel != "all" and c.channel != channel:
            continue
        buckets[c.language][c.group_id].append(c)
    selected = []
    for language in LANGUAGES:
        grouped = buckets[language]
        queues = []
        for group in sorted(grouped):
            choices = sorted(grouped[group], key=conversation_key)
            random.Random(f"{seed}/{language}/{group}").shuffle(choices)
            queues.append(choices)
        picks = []
        while queues and len(picks) < per_language:
            for queue in queues:
                if queue and len(picks) < per_language:
                    picks.append(queue.pop())
            queues = [q for q in queues if q]
        selected.extend(picks)
    if not selected:
        raise ValueError("No conversations match selection")
    # An explicit ID/group filter may intentionally select fewer languages. The
    # manifest reports the actual counts instead of silently claiming balance.
    return selected


def load_prices(path: str | Path) -> dict:
    prices = read_json(path)
    if prices.get("currency") != "USD" or not prices.get("verified_at"):
        raise ValueError("Price file requires currency USD and verified_at provenance")
    for model, price in prices.get("models", {}).items():
        for name in ("input_per_million", "output_per_million", "cached_input_per_million", "cache_write_input_per_million"):
            value = price.get(name)
            if value is not None and (type(value) not in {float, int} or not math.isfinite(value) or value < 0):
                raise ValueError(f"Invalid price for {model}: {name}")
        if not price.get("source"):
            raise ValueError(f"Price provenance missing for {model}")
        for name in ("long_context_input_multiplier", "long_context_output_multiplier"):
            multiplier = price.get(name, 1)
            if type(multiplier) not in {float, int} or not math.isfinite(multiplier) or multiplier < 1:
                raise ValueError(f"Invalid conservative price multiplier for {model}: {name}")
        threshold = price.get("long_context_threshold", 1)
        if type(threshold) is not int or threshold <= 0:
            raise ValueError(f"Invalid long-context threshold for {model}")
    return prices


def request_payload(settings: Settings, conversation: Conversation, checklist: ChecklistVersion) -> dict:
    text_checklist = checklist.model_copy(update={"criteria": [c for c in checklist.criteria if not c.requires_audio]})
    if not text_checklist.criteria:
        return {"model": settings.model, "no_provider_request": True}
    return service.build_request(settings, service.build_messages(conversation, text_checklist))


def reserve_cost(payload: dict, settings: Settings, price: dict | None) -> tuple[float | None, int]:
    """Conservative byte-level input bound plus all permitted output and retries.

    For byte-tokenized OpenAI text input, UTF-8 bytes upper-bound ordinary text
    tokens. Serializing the entire payload includes schema text, and 4096 extra
    tokens cover framing and the fixed repair instruction. No caching discount
    is assumed. The bound applies only to this text-only, tool-free adapter.
    """
    if payload.get("no_provider_request"):
        return 0.0, 0
    upper_input = len(canonical(payload)) + 4096
    if not price or price.get("input_per_million") is None or price.get("output_per_million") is None:
        return None, upper_input
    input_rate = max(price["input_per_million"], price.get("cache_write_input_per_million") or price["input_per_million"])
    output_rate = price["output_per_million"]
    if upper_input > price.get("long_context_threshold", math.inf):
        input_rate *= price.get("long_context_input_multiplier", 1)
        output_rate *= price.get("long_context_output_multiplier", 1)
    return MAX_ATTEMPTS * (upper_input * input_rate + settings.max_tokens * output_rate) / 1_000_000, upper_input


def public_settings(settings: Settings) -> dict:
    values = asdict(settings)
    values.pop("api_key", None)
    # Endpoint identity affects reproducibility, but a private hostname need not
    # be published in reports. Credentials are never included in any digest.
    values["endpoint_sha256"] = fingerprint(values.pop("base_url"))
    return values


def make_plan(args: argparse.Namespace) -> tuple[dict, list[dict], dict[str, Settings], ChecklistVersion]:
    if not args.dataset or not args.checklist:
        raise ValueError("--dataset and --checklist are required unless --report-only is used")
    if not 1 <= args.concurrency <= 32:
        raise ValueError("--concurrency must be between 1 and 32")
    if len(args.models) != len(set(args.models)):
        raise ValueError("--models contains duplicate model names")
    checklist = ChecklistVersion.model_validate(read_json(args.checklist))
    sources, conversations = [], []
    for path in args.dataset:
        raw = read_json(path)
        sources.append({"filename": Path(path).name, "sha256": fingerprint(raw)})
        conversations.extend(normalize(DatasetExport.model_validate(raw)))
    selected = select_conversations(conversations, per_language=args.per_language, seed=args.seed,
                                    groups=args.groups, ids=args.conversation_ids, channel=args.channel)
    prices = load_prices(args.prices)
    load_dotenv(ROOT / ".env", override=False)
    configs = {}
    for model in args.models:
        price = prices["models"].get(model, {})
        configs[model] = Settings(
            base_url=args.base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"), model=model,
            api_key=os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
            api_style=args.api_style, json_mode="json_schema", reasoning_effort=None if args.reasoning_effort == "omit" else args.reasoning_effort,
            timeout_seconds=args.timeout_seconds, max_tokens=args.max_output_tokens,
            input_cost_per_million=price.get("input_per_million"), output_cost_per_million=price.get("output_per_million"),
            cached_input_cost_per_million=price.get("cached_input_per_million"),
            cache_write_input_cost_per_million=price.get("cache_write_input_per_million"))
        configs[model].validate()
    code_paths = [Path(__file__), ROOT / "backend/cqa/evaluator/service.py", ROOT / "backend/cqa/evaluator/provider.py", ROOT / "backend/cqa/evaluator/validation.py",
                  ROOT / "backend/cqa/scoring.py", ROOT / "backend/cqa/models.py"]
    inputs = {"schema_version": SCHEMA_VERSION, "datasets": sorted(sources, key=lambda s: (s["filename"], s["sha256"])),
              "checklist_sha256": fingerprint(checklist.model_dump(mode="json")),
              "prompt_sha256": sha256(service.PROMPT_PATH.read_bytes()).hexdigest(),
              "prompt_version": service.PROMPT_VERSION,
              "implementation_sha256": {str(p.relative_to(ROOT)).replace("\\", "/"): sha256(p.read_bytes()).hexdigest() for p in code_paths},
              "selection": {"seed": args.seed, "per_language": args.per_language, "groups": sorted(args.groups or []),
                            "ids": sorted(args.conversation_ids or []), "channel": args.channel},
              "models": {m: public_settings(s) for m, s in configs.items()}, "pricing": prices,
              "selected_conversations_sha256": fingerprint([c.model_dump(mode="json") for c in selected])}
    run_fingerprint = fingerprint(inputs)
    tasks = []
    # Round robin by model keeps bounded early stops representative across models.
    for c in selected:
        for model, settings in configs.items():
            payload = request_payload(settings, c, checklist)
            reserve, upper_input = reserve_cost(payload, settings, prices["models"].get(model))
            request_hash = fingerprint({"payload": payload, "config": public_settings(settings)})
            tasks.append({"task_id": fingerprint([model, conversation_key(c)]), "model": model,
                          "conversation": c.model_dump(mode="json"), "request_sha256": request_hash,
                          "reservation_usd": reserve, "input_tokens_upper_bound": upper_input})
    manifest = {"schema_version": SCHEMA_VERSION, "created_at": utc_now(), "fingerprint": run_fingerprint,
                "inputs": inputs, "conversation_count": len(selected), "task_count": len(tasks),
                "language_counts": dict(Counter(c.language for c in selected)),
                "group_counts": dict(Counter(c.group_id for c in selected)),
                "balanced_ru_kz_en": len({sum(c.language == lang for c in selected) for lang in LANGUAGES}) == 1,
                "budget_policy": "Reserve every permitted attempt at uncached/cache-write and maximum output prices; unknown usage keeps the full reservation.",
                "reservation_basis": "UTF-8 serialized request bytes plus 4096 framing/repair tokens per attempt; two attempts maximum; text only, no tools.",
                "total_worst_case_usd": sum(t["reservation_usd"] for t in tasks) if all(t["reservation_usd"] is not None for t in tasks) else None,
                "checklist": checklist.model_dump(mode="json"), "tasks": tasks}
    return manifest, tasks, configs, checklist


def checkpoint_for(out: Path, manifest: dict, *, resume: bool) -> dict:
    path = out / "checkpoint.json"
    if (out / "manifest.json").exists() or path.exists():
        if not resume:
            raise ValueError("Output exists; use --resume for identical input/configuration, or choose a new --out")
        previous = read_json(out / "manifest.json")
        if previous["fingerprint"] != manifest["fingerprint"]:
            raise ValueError("Resume fingerprint mismatch: source, checklist, prompt, code, selection, model or pricing changed; choose a new output directory")
        if path.exists():
            checkpoint = read_json(path)
            if checkpoint.get("fingerprint") != manifest["fingerprint"]:
                raise ValueError("Checkpoint fingerprint mismatch")
            return checkpoint
    return {"schema_version": SCHEMA_VERSION, "fingerprint": manifest["fingerprint"], "records": {}, "updated_at": utc_now()}


def billable_cost(info: dict, *, zero_request: bool = False) -> float | None:
    value = info.get("estimated_cost")
    if zero_request:
        return 0.0
    if info.get("usage_complete") is True and type(value) in {int, float} and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def charged_cost(record: dict) -> float:
    return float(record.get("budget_charge_usd", 0))


async def execute_plan(manifest: dict, tasks: list[dict], configs: dict[str, Settings], checklist: ChecklistVersion,
                       out: Path, checkpoint: dict, *, budget_usd: float, concurrency: int) -> dict:
    if not math.isfinite(budget_usd) or budget_usd <= 0:
        raise ValueError("--budget-usd must be a positive finite total run limit")
    if any(t["reservation_usd"] is None for t in tasks):
        raise ValueError("Missing verified prices: paid runs require a conservative reservation for every model. Add an explicit price profile (local zero-cost profiles are supported).")
    records = checkpoint["records"]
    used = sum(charged_cost(r) for r in records.values())
    if used > budget_usd + 1e-10:
        raise ValueError("Run already has charges/reservations above the requested budget; use a larger total cap")
    failed_models = {r["model"] for r in records.values() if r.get("error_code") == "request"}
    abort = any(r.get("error_code") == "auth" for r in records.values())
    lock = asyncio.Lock()
    terminal = {"completed", "failed", "interrupted_unknown"}
    # A process interruption after dispatch may still have billed the provider.
    # Preserve that reservation and do not auto-repeat the request on resume.
    for record in records.values():
        if record.get("status") == "running":
            record.update(status="interrupted_unknown", error="Interrupted after dispatch; provider usage is unknown. Preserved reservation; inspect provider logs before a new run.")

    def save() -> None:
        checkpoint["updated_at"] = utc_now()
        checkpoint["budget_usd"] = budget_usd
        checkpoint["charged_or_reserved_usd"] = sum(charged_cost(r) for r in records.values())
        write_json(out / "checkpoint.json", checkpoint)

    async def call(task: dict) -> None:
        nonlocal used, abort
        key, model = task["task_id"], task["model"]
        async with lock:
            if key in records and records[key].get("status") in terminal:
                if records[key]["request_sha256"] != task["request_sha256"]:
                    raise ValueError("Task request fingerprint mismatch")
                return
            record = {"task_id": key, "model": model, "conversation": task["conversation"],
                      "checklist": checklist.model_dump(mode="json"), "request_sha256": task["request_sha256"],
                      "results": [], "original_results": [], "score": {}, "original_score": {}, "revision": 1,
                      "model_info": {}, "error": None, "reservation_usd": task["reservation_usd"], "budget_charge_usd": 0.0}
            records[key] = record
            if abort or model in failed_models:
                record.update(status="configuration_skipped", error="Stopped after authentication or model/request failure")
                save()
                return
            reserved = task["reservation_usd"]
            if used + reserved > budget_usd + 1e-10:
                record.update(status="budget_skipped", error="Remaining budget is below the conservative request reservation")
                save()
                return
            used += reserved
            record.update(status="running", started_at=utc_now(), budget_charge_usd=reserved)
            save()  # Persist reservation before dispatch, so crashes cannot erase possible spend.
        started = time.monotonic()
        try:
            result = await evaluate(Conversation.model_validate(task["conversation"]), checklist, settings=configs[model])
            values = [r.model_dump(mode="json") for r in result.draft.results]
            score = calculate_score(checklist, result.draft.results)
            record.update(status="completed", results=values, original_results=values,
                          score=score, original_score=score, model_info=result.model_info)
        except EvaluatorError as exc:
            record.update(status="failed", error=str(exc), error_code=getattr(exc, "code", "evaluation"),
                          model_info=getattr(exc, "model_info", {}))
            if record["error_code"] == "auth":
                abort = True
            if record["error_code"] == "request":
                failed_models.add(model)
        except Exception:
            # Avoid persisting arbitrary exception text, which could contain
            # transport headers or credentials. Preserve uncertain billing.
            record.update(status="failed", error="Unexpected internal evaluation failure; inspect local traceback without publishing secrets", error_code="internal")
            abort = True
            raise
        finally:
            record.update(finished_at=utc_now(), wall_time_ms=round((time.monotonic() - started) * 1000))
            cost = billable_cost(record["model_info"], zero_request=record["model_info"].get("attempts") == 0 and record["status"] == "completed")
            record["actual_cost_usd"] = cost
            record["cost_status"] = "usage_based_estimate" if cost is not None else "unknown_preserved_reservation"
            async with lock:
                charge = cost if cost is not None else reserved
                used += charge - reserved
                record["budget_charge_usd"] = charge
                if charge > reserved + 1e-10:
                    abort = True
                    record["reservation_exceeded"] = True
                save()
            safe_print(f"{model}: {task['conversation']['group_id']}/{task['conversation']['conversation_id']} {record['status']}", flush=True)

    # Validate credentials/model configuration on a single request per model
    # before concurrency can duplicate a fatal failure for that model.
    probe_ids = set()
    for model in configs:
        pending = next((t for t in tasks if t["model"] == model and records.get(t["task_id"], {}).get("status") not in terminal), None)
        if pending:
            await call(pending)
            probe_ids.add(pending["task_id"])
    semaphore = asyncio.Semaphore(concurrency)
    async def worker(task: dict) -> None:
        async with semaphore:
            await call(task)
    await asyncio.gather(*(worker(t) for t in tasks if t["task_id"] not in probe_ids))
    save()
    return checkpoint


def load_independent_labels(path: str | None, split: str) -> dict | None:
    if not path:
        return None
    labels = read_json(path)
    if labels.get("independent_human_review") is not True:
        raise ValueError("Quality metrics require labels.independent_human_review=true, attesting an independent human review; generated labels are not ground truth")
    if any(item.get("reviewed") is True and not str(item.get("reviewer", "")).strip() for item in labels["items"]):
        raise ValueError("Every reviewed label must identify its human reviewer")
    validate_labels(labels, split)
    return labels


def validate_label_snapshots(labels: dict | None, tasks: list[dict], checklist: ChecklistVersion) -> None:
    """Reject incompatible reference material before any provider spending."""
    if labels is None:
        return
    reference_checklist = ChecklistVersion.model_validate(labels["checklist"])
    if reference_checklist.model_dump() != checklist.model_dump():
        raise ValueError("Labels and benchmark use different checklist snapshots")
    selected = {conversation_key(t["conversation"]): t["conversation"] for t in tasks}
    for item in labels["items"]:
        key = conversation_key(item["conversation"])
        if key in selected:
            expected = Conversation.model_validate(item["conversation"]).model_dump(exclude={"audio_url"})
            actual = Conversation.model_validate(selected[key]).model_dump(exclude={"audio_url"})
            if expected != actual:
                raise ValueError("Labels and benchmark use different conversation snapshots")


def decision(result: dict) -> str:
    answer = result.get("answer")
    if type(answer) in {int, float}:
        answer = float(answer)
    return json.dumps([result.get("status"), answer], ensure_ascii=False, sort_keys=True)


def aggregate_report(manifest: dict, checkpoint: dict, *, labels: dict | None = None, split: str = "all") -> dict:
    records = list(checkpoint["records"].values())
    models = []
    completed_by_model = {}
    for model in manifest["inputs"]["models"]:
        selected = [r for r in records if r["model"] == model]
        complete = [r for r in selected if r.get("status") == "completed"]
        dispatched = [r for r in selected if r.get("started_at")]
        latencies = [r["wall_time_ms"] for r in dispatched if "wall_time_ms" in r]
        costs = [r.get("actual_cost_usd") for r in dispatched]
        infos = [r.get("model_info", {}) for r in dispatched]
        token_fields = {name: sum(i.get(name) or 0 for i in infos) for name in
                        ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens", "reasoning_tokens")}
        unknown_usage = sum(i.get("usage_complete") is not True for i in infos)
        valid_quotes = checked_quotes = valid_spans = checked_spans = needs_review = criteria = 0
        for record in complete:
            messages = {m["message_id"]: m["text"] for m in record["conversation"]["transcript"]}
            positions = {m["message_id"]: index for index, m in enumerate(record["conversation"]["transcript"])}
            for result in record["original_results"]:
                criteria += 1
                needs_review += result["status"] == "needs_review"
                for quote in result.get("evidence", []):
                    checked_quotes += 1
                    text = quote.get("quote", "")
                    valid_quotes += bool(text.strip() and quote.get("message_id") in messages and text in messages[quote["message_id"]])
                span = result.get("checked_span")
                if span:
                    checked_spans += 1
                    start, end = positions.get(span["start_message_id"]), positions.get(span["end_message_id"])
                    valid_spans += start is not None and end is not None and start <= end
        row = {"model": model, "planned": sum(t["model"] == model for t in manifest["tasks"]),
               "dispatched": len(dispatched), "completed": len(complete),
               "failed": sum(r.get("status") in {"failed", "interrupted_unknown"} for r in selected),
               "budget_skipped": sum(r.get("status") == "budget_skipped" for r in selected),
               "configuration_skipped": sum(r.get("status") == "configuration_skipped" for r in selected),
               "technical_completion_rate": len(complete) / len(dispatched) if dispatched else None,
               "mean_latency_ms": statistics.mean(latencies) if latencies else None,
               "p50_latency_ms": statistics.median(latencies) if latencies else None,
               "max_latency_ms": max(latencies) if latencies else None,
               "retries": sum(max(0, int(i.get("attempts", 0)) - 1) for i in infos),
               "known_cost_usd": sum(r.get("actual_cost_usd") if r.get("actual_cost_usd") is not None
                                     else (r.get("model_info", {}).get("known_estimated_cost") or 0) for r in dispatched),
               "total_cost_usd": sum(costs) if all(c is not None for c in costs) else None,
               "unknown_cost_requests": sum(c is None for c in costs),
               "budget_charged_or_reserved_usd": sum(charged_cost(r) for r in selected),
               "tokens_observed": token_fields, "usage_incomplete_requests": unknown_usage,
               "quote_existence": {"valid": valid_quotes, "checked": checked_quotes},
               "span_existence": {"valid": valid_spans, "checked": checked_spans},
               "needs_review_fraction": needs_review / criteria if criteria else None}
        if labels is not None:
            chosen = {conversation_key(r["conversation"]) for r in selected}
            subset = dict(labels, items=[item for item in labels["items"] if conversation_key(item["conversation"]) in chosen])
            row["human_quality"] = summarize(subset, selected, split)
        models.append(row)
        completed_by_model[model] = {conversation_key(r["conversation"]): r for r in complete}
    agreement = []
    names = list(completed_by_model)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            pairs = []
            by_criterion = defaultdict(list)
            shared = sorted(completed_by_model[left].keys() & completed_by_model[right].keys())
            for key in shared:
                a = {r["criterion_id"]: r for r in completed_by_model[left][key]["original_results"]}
                b = {r["criterion_id"]: r for r in completed_by_model[right][key]["original_results"]}
                for criterion in sorted(a.keys() & b.keys()):
                    match = decision(a[criterion]) == decision(b[criterion])
                    pairs.append(match)
                    by_criterion[criterion].append(match)
            agreement.append({"models": [left, right], "shared_completed_conversations": len(shared),
                              "criterion_decisions": len(pairs), "agreement": sum(pairs) / len(pairs) if pairs else None,
                              "by_criterion": {c: {"count": len(v), "agreement": sum(v) / len(v)} for c, v in by_criterion.items()}})
    return {"schema_version": SCHEMA_VERSION, "created_at": utc_now(), "fingerprint": manifest["fingerprint"],
            "metric_status": "independent_human_reference_diagnostics" if labels else "technical_validity_and_model_agreement_only_no_ground_truth",
            "prediction_basis": "original_model_outputs_before_appeals",
            "limitations": ["Successful schema/quote checks do not establish semantic correctness.",
                            "Agreement between models is not accuracy and may reflect shared errors.",
                            "Small stratified sample; no official ranking or population claim.",
                            "Reported costs are token-usage estimates at the recorded public rates, not provider invoices."],
            "pricing": manifest["inputs"]["pricing"], "language_counts": manifest["language_counts"],
            "labels_sha256": fingerprint(labels) if labels else None, "models": models, "pairwise_agreement": agreement,
            "budget_usd": checkpoint.get("budget_usd"),
            "charged_or_reserved_usd": sum(charged_cost(r) for r in records)}


def write_reports(out: Path, manifest: dict, checkpoint: dict, *, labels: dict | None, split: str) -> dict:
    report = aggregate_report(manifest, checkpoint, labels=labels, split=split)
    write_json(out / "report.json", report)
    columns = ["model", "planned", "dispatched", "completed", "failed", "budget_skipped", "configuration_skipped",
               "technical_completion_rate", "mean_latency_ms", "p50_latency_ms", "max_latency_ms", "retries",
               "known_cost_usd", "total_cost_usd", "unknown_cost_requests", "budget_charged_or_reserved_usd"]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(report["models"])
    (out / "report.csv").write_text(buffer.getvalue(), encoding="utf-8-sig")
    lines = ["# CQA model benchmark", "", f"Metric status: `{report['metric_status']}`.", "",
             "| Model | Completed / planned | Mean seconds | Retries | Cost USD | Unknown-cost requests |",
             "|---|---:|---:|---:|---:|---:|"]
    for row in report["models"]:
        latency = f"{row['mean_latency_ms'] / 1000:.2f}" if row["mean_latency_ms"] is not None else "—"
        cost = f"{row['total_cost_usd']:.6f}" if row["total_cost_usd"] is not None else f"unknown (known {row['known_cost_usd']:.6f})"
        lines.append(f"| {row['model']} | {row['completed']} / {row['planned']} | {latency} | {row['retries']} | {cost} | {row['unknown_cost_requests']} |")
    lines += ["", "Agreement is between original criterion decisions; it is not accuracy.", ""]
    for pair in report["pairwise_agreement"]:
        value = f"{pair['agreement']:.1%}" if pair["agreement"] is not None else "unavailable"
        lines.append(f"- {' / '.join(pair['models'])}: {value} ({pair['criterion_decisions']} decisions, {pair['shared_completed_conversations']} shared conversations).")
    lines += ["", *[f"- {item}" for item in report["limitations"]], "", f"Pricing verified: {report['pricing']['verified_at']}; standard USD per million tokens."]
    for model, price in report["pricing"]["models"].items():
        lines.append(f"- [{model}]({price['source']}): input {price.get('input_per_million')}, cached {price.get('cached_input_per_million')}, cache write {price.get('cache_write_input_per_million')}, output {price.get('output_per_million')}.")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for model in manifest["inputs"]["models"]:
        slug = re.sub(r"[^A-Za-z0-9_.-]", "_", model) + "-" + fingerprint(model)[:8]
        predictions = [r for r in checkpoint["records"].values() if r["model"] == model]
        write_json(out / "predictions" / (slug + ".json"), {"schema_version": 1, "split": split,
                   "model": model, "fingerprint": manifest["fingerprint"], "evaluations": predictions})
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", nargs="+", help="One or more DatasetExport JSON files")
    p.add_argument("--checklist", help="Explicit rubric snapshot; required for planning or live runs")
    p.add_argument("--out", required=True, help="Run directory; prefer ignored data/evaluations/<run>")
    p.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    p.add_argument("--per-language", type=int, default=2)
    p.add_argument("--groups", nargs="+")
    p.add_argument("--conversation-ids", nargs="+", help="IDs or group/id; sampling applies after filtering")
    p.add_argument("--channel", choices=["agent chat", "voice call", "all"], default="agent chat")
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--reasoning-effort", default="high", choices=["omit", "none", "minimal", "low", "medium", "high", "xhigh", "max"],
                   help="high by default; omit is available for non-reasoning local providers")
    p.add_argument("--max-output-tokens", type=int, default=16384, help="Explicit per-attempt total output limit including reasoning; never silently reduced")
    p.add_argument("--timeout-seconds", type=float, default=180)
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--api-style", choices=["responses", "chat_completions"], default="responses")
    p.add_argument("--base-url", help="Override API endpoint; API key comes only from environment/.env")
    p.add_argument("--prices", default=str(ROOT / "evaluation/model_profiles.json"))
    p.add_argument("--budget-usd", type=float, help="Total run cap; required for live runs, includes unknown-usage reservations")
    p.add_argument("--dry-run", action="store_true", help="Persist a plan and hashes without provider requests")
    p.add_argument("--resume", action="store_true", help="Continue the identical plan; completed/failed/uncertain requests are never repeated")
    p.add_argument("--report-only", action="store_true", help="Regenerate reports from saved original predictions without provider requests")
    p.add_argument("--labels", help="Optional independently human-reviewed labels with independent_human_review:true")
    p.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        out = Path(args.out)
        labels = load_independent_labels(args.labels, args.split)  # Validate before spending.
        if args.report_only:
            report = write_reports(out, read_json(out / "manifest.json"), read_json(out / "checkpoint.json"), labels=labels, split=args.split)
            safe_print(json.dumps({"report": str(out / "report.json"), "metric_status": report["metric_status"]}))
            return 0
        manifest, tasks, configs, checklist = make_plan(args)
        validate_label_snapshots(labels, tasks, checklist)
        checkpoint = checkpoint_for(out, manifest, resume=args.resume)
        out.mkdir(parents=True, exist_ok=True)
        if not (out / "manifest.json").exists():
            write_json(out / "manifest.json", manifest)
            (out / "prompt.snapshot.md").write_bytes(service.PROMPT_PATH.read_bytes())
        write_json(out / "checkpoint.json", checkpoint)
        if args.dry_run:
            safe_print(json.dumps({"mode": "dry_run_no_provider_calls", "conversations": manifest["conversation_count"],
                  "language_counts": manifest["language_counts"], "tasks": len(tasks),
                  "total_worst_case_usd": manifest["total_worst_case_usd"], "fingerprint": manifest["fingerprint"]}, ensure_ascii=False))
            return 0
        if args.budget_usd is None:
            raise ValueError("Live runs require --budget-usd; use --dry-run to inspect the plan first")
        try:
            checkpoint = asyncio.run(execute_plan(manifest, tasks, configs, checklist, out, checkpoint,
                                                 budget_usd=args.budget_usd, concurrency=args.concurrency))
        finally:
            write_reports(out, manifest, checkpoint, labels=labels, split=args.split)
        complete = sum(r.get("status") == "completed" for r in checkpoint["records"].values())
        safe_print(f"Saved {complete}/{len(tasks)} completed predictions and reports in {out}")
        return 0 if complete == len(tasks) else 3
    except (ValueError, KeyError, OSError, EvaluatorError) as exc:
        safe_print(f"Benchmark error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
