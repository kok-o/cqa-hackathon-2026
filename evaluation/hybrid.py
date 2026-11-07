"""Frozen, paired Luna/independent-Sol experiment; never a production router.

Routing sees only the source and validated original Luna decisions. The reviewer
receives the source and rubric, never Luna answers or authored expectations.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import math
import os
from pathlib import Path
import re
import statistics
import time

from backend.cqa.evaluator import service
from backend.cqa.evaluator.service import Settings
from backend.cqa.evaluator.validation import validate_draft
from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, Conversation, CriterionResult, DatasetExport, EvaluationDraft
from backend.cqa.scoring import calculate_score
from . import benchmark
from .hybrid_decision import GATE, deployment_gate
from .submission import checklist_criteria_fingerprint, load_approved_checklist

ROOT = Path(__file__).resolve().parents[1]
LUNA = "gpt-6-luna"
SOL = "gpt-6.1-sol"
MAX_BUDGET_USD = 3.0
CRITERIA = ("contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical")
# Preregistered before inspecting the new pilot dataset or its authored labels.
# Broad privacy mentions can create false positives; client/bot text is excluded.
ROUTING_POLICY = {
    "version": "hybrid-risk-v1",
    "needs_review": "all non-audio criteria",
    "paired_identity_critical": "critical=true or identification=false",
    "consultation_values": [5, 10, 15],
    "dialogue_values": [4, 7],
    "empathy": False,
    "operator_only_patterns": {
        "privacy_ru": r"\b(?:баланс\w*|остат(?:ок|к)\w*|портфел\w*|иин|паспорт\w*)\b",
        "privacy_en": r"\b(?:balance|portfolio|passport)\b|\bcard[\s-]+number\b",
        "privacy_kz": r"\b(?:қалдық\w*|жсн|паспорт\w*)\b",
        "personal_payment_ru": r"(?:перевед\w*|перевод\w*|оплат\w*|заплат\w*).{0,80}(?:мне|мо[юийе]\w*|личн\w*).{0,40}(?:карт\w*|сч[её]т\w*|номер\w*|консультац\w*)",
        "personal_payment_en": r"\b(?:pay|send|transfer)\b.{0,80}\b(?:me|my|personal|private)\b.{0,40}\b(?:card|account|number|consultation|advice)\b",
        "personal_payment_kz": r"(?:маған|менің|жеке).{0,60}(?:карт\w*|шот\w*|нөмір\w*|кеңес\w*).{0,80}(?:аудар\w*|төле\w*)|(?:аудар\w*|төле\w*).{0,80}(?:маған|менің|жеке)",
    },
    "regex_flags": ["IGNORECASE", "DOTALL"],
    "excluded_triggers": ["ordinary N/A", "contact=false", "closure=false", "Luna explanation", "labels", "reviewer output"],
}


def route_criteria(conversation: Conversation, checklist: ChecklistVersion, luna: EvaluationDraft) -> dict:
    validate_draft(conversation, checklist, luna)
    reasons: dict[str, list[str]] = defaultdict(list)
    criteria = {c.criterion_id: c for c in checklist.criteria}

    def add(identifier, reason):
        if identifier in criteria and not criteria[identifier].requires_audio and reason not in reasons[identifier]:
            reasons[identifier].append(reason)

    for result in luna.results:
        identifier = result.criterion_id
        if result.status == "needs_review":
            add(identifier, "needs_review")
        if result.status != "applicable":
            continue
        if identifier == "critical" and result.answer is True or identifier == "identification" and result.answer is False:
            for paired in ("identification", "critical"):
                add(paired, "identity_critical_pair")
        if identifier in {"consultation", "dialogue"} and result.answer in ROUTING_POLICY[identifier + "_values"]:
            add(identifier, "intermediate_scale")
        if identifier == "empathy" and result.answer is False:
            add(identifier, "empathy_false")
    for name, pattern in ROUTING_POLICY["operator_only_patterns"].items():
        if any(message.speaker == "operator" and re.search(pattern, message.text, re.IGNORECASE | re.DOTALL)
               for message in conversation.transcript):
            for paired in ("identification", "critical"):
                add(paired, "operator_" + name)
    selected = [c.criterion_id for c in checklist.criteria if c.criterion_id in reasons]
    return {"policy_version": ROUTING_POLICY["version"], "criterion_ids": selected,
            "reasons": {identifier: reasons[identifier] for identifier in selected}}


def review_checklist(checklist: ChecklistVersion, selected: list[str]) -> ChecklistVersion:
    if not selected or len(set(selected)) != len(selected) or not set(selected) <= {c.criterion_id for c in checklist.criteria}:
        raise ValueError("Invalid independent review scope")
    criteria = [c.model_copy(deep=True) for c in checklist.criteria if c.criterion_id in selected]
    context = {"role": "reference_only_full_official_rubric", "full_rubric": checklist.model_dump(mode="json"),
               "required_output_criterion_ids": [c.criterion_id for c in criteria]}
    criteria[0].instruction += (
        "\n\nINDEPENDENT TARGETED REVIEW: assess the supplied conversation independently. "
        "The complete original rubric below is reference context, including dependencies between criteria. "
        "Return exactly the criteria in required_output_criterion_ids, not the other reference criteria. "
        "No earlier model decisions are supplied. Preserve the original definitions and allowed answers.\n"
        + benchmark.canonical(context).decode("utf-8"))
    return checklist.model_copy(deep=True, update={"criteria": criteria})


def combine_drafts(conversation: Conversation, checklist: ChecklistVersion, luna: EvaluationDraft,
                   reviewer: EvaluationDraft | None, selected: list[str]) -> EvaluationDraft:
    validate_draft(conversation, checklist, luna)
    result = luna.model_copy(deep=True)
    if reviewer is not None:
        validate_draft(conversation, review_checklist(checklist, selected), reviewer)
        replacements = {item.criterion_id: item for item in reviewer.results}
        result.results = [replacements.get(item.criterion_id, item).model_copy(deep=True) for item in luna.results]
        result.prompt_version = f"{luna.prompt_version}+{ROUTING_POLICY['version']}"
    validate_draft(conversation, checklist, result)
    return result


def model_settings(prices: dict, *, api_key: str = "") -> dict[str, Settings]:
    output = {}
    for model, effort, tokens, timeout in ((LUNA, "high", 16384, 180), (SOL, "max", 32768, 600)):
        price = prices["models"][model]
        settings = Settings("https://api.openai.com/v1", model, api_key=api_key,
            api_style="responses", json_mode="json_schema", reasoning_effort=effort,
            max_tokens=tokens, timeout_seconds=timeout,
            input_cost_per_million=price["input_per_million"], output_cost_per_million=price["output_per_million"],
            cached_input_cost_per_million=price.get("cached_input_per_million"),
            cache_write_input_cost_per_million=price.get("cache_write_input_per_million"))
        settings.validate()
        output[model] = settings
    return output


def code_hashes() -> dict:
    paths = ["evaluation/hybrid.py", "evaluation/hybrid_decision.py", "scripts/hybrid_benchmark.py",
             "evaluation/benchmark.py", "evaluation/submission.py", "backend/cqa/evaluator/service.py",
             "backend/cqa/evaluator/provider.py", "backend/cqa/evaluator/validation.py",
             "backend/cqa/models.py", "backend/cqa/scoring.py", "backend/cqa/importer.py", "prompts/evaluator.md"]
    return {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}


def task_for(conversation, checklist, settings, price, *, stage):
    payload = benchmark.request_payload(settings, conversation, checklist)
    reservation, upper_input = benchmark.reserve_cost(payload, settings, price)
    if reservation is None:
        raise ValueError("Verified prices are required")
    request_hash = benchmark.fingerprint({"payload": payload, "config": benchmark.public_settings(settings)})
    return {"task_id": benchmark.fingerprint([stage, settings.model, benchmark.conversation_key(conversation)]),
            "model": settings.model, "stage": stage, "conversation": conversation.model_dump(mode="json"),
            "request_sha256": request_hash, "reservation_usd": reservation,
            "input_tokens_upper_bound": upper_input}


def make_manifest(dataset: dict, checklist: ChecklistVersion, prices: dict, *, expected_sha256: str) -> dict:
    if checklist_criteria_fingerprint(checklist) != checklist_criteria_fingerprint(load_approved_checklist()):
        raise ValueError("Pilot requires the complete approved official rubric")
    conversations = sorted(normalize(DatasetExport.model_validate(dataset)), key=benchmark.conversation_key)
    if any(c.channel != "agent chat" for c in conversations):
        raise ValueError("This preregistered pilot compares text chats only")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Freeze the expected-file SHA256 before any provider calls")
    configs = model_settings(prices)
    tasks = [task_for(c, checklist, configs[LUNA], prices["models"][LUNA], stage="luna") for c in conversations]
    maximum_review = review_checklist(checklist, [c.criterion_id for c in checklist.criteria])
    review_bounds = [task_for(c, maximum_review, configs[SOL], prices["models"][SOL], stage="sol") for c in conversations]
    inputs = {"dataset_sha256": benchmark.fingerprint(dataset), "checklist_sha256": benchmark.fingerprint(checklist.model_dump(mode="json")),
              "expected_file_sha256": expected_sha256, "implementation_sha256": code_hashes(),
              "policy": deepcopy(ROUTING_POLICY), "deployment_gate": deepcopy(GATE),
              "models": {name: benchmark.public_settings(value) for name, value in configs.items()},
              "pricing": deepcopy(prices), "concurrency": 2, "maximum_budget_usd": MAX_BUDGET_USD}
    return {"schema_version": 1, "created_at": benchmark.utc_now(), "fingerprint": benchmark.fingerprint(inputs),
            "inputs": inputs, "dataset": deepcopy(dataset), "checklist": checklist.model_dump(mode="json"),
            "tasks": tasks, "conversation_count": len(tasks), "task_count": len(tasks),
            "luna_worst_case_usd": sum(t["reservation_usd"] for t in tasks),
            "all_criteria_review_worst_case_usd": sum(t["reservation_usd"] for t in review_bounds),
            "review_plan": "Derived only after original Luna output; concurrency 2 within identical review scopes",
            "budget_policy": "Shared durable ledger reserves both attempts before dispatch; unknown spend retains its full reservation."}


def verify_manifest(manifest: dict) -> None:
    rebuilt = make_manifest(manifest["dataset"], ChecklistVersion.model_validate(manifest["checklist"]),
                            manifest["inputs"]["pricing"], expected_sha256=manifest["inputs"]["expected_file_sha256"])
    if (manifest["fingerprint"] != rebuilt["fingerprint"] or manifest["inputs"] != rebuilt["inputs"]
            or manifest["tasks"] != rebuilt["tasks"]):
        raise ValueError("Frozen hybrid source, code, policy, rubric, request, gate or configuration changed")


@contextmanager
def run_lock(out: Path):
    """A process-owned lock prevents two resumes from dispatching the same task."""
    out.mkdir(parents=True, exist_ok=True)
    with (out / ".run.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Another process owns this hybrid run") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def record_draft(record: dict, conversation: Conversation, checklist: ChecklistVersion) -> EvaluationDraft:
    if record.get("status") != "completed" or record.get("error"):
        raise ValueError("Evaluation is not complete")
    if record.get("conversation") != conversation.model_dump(mode="json") or record.get("checklist") != checklist.model_dump(mode="json"):
        raise ValueError("Evaluation snapshot mismatch")
    draft = EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id,
        checklist_id=checklist.checklist_id, checklist_version=checklist.version,
        prompt_version=record.get("model_info", {}).get("prompt_version", service.PROMPT_VERSION),
        results=record["original_results"])
    validate_draft(conversation, checklist, draft)
    return draft


def validate_checkpoint(manifest: dict, checkpoint: dict) -> None:
    """Reject changed snapshots/charges before any new provider dispatch."""
    if checkpoint.get("fingerprint") != manifest["fingerprint"]:
        raise ValueError("Hybrid checkpoint fingerprint mismatch")
    checklist = ChecklistVersion.model_validate(manifest["checklist"])
    allowed = {task["task_id"]: (task, checklist) for task in manifest["tasks"]}
    routes = checkpoint.get("routes", {})
    if not isinstance(routes, dict) or not set(routes) <= set(allowed):
        raise ValueError("Unknown frozen routing task")
    for key, route in routes.items():
        task = allowed[key][0]
        source = Conversation.model_validate(task["conversation"])
        baseline = checkpoint["records"].get(key, {})
        luna = record_draft(baseline, source, checklist)
        predicted = route_criteria(source, checklist, luna)
        if any(route.get(name) != predicted[name] for name in predicted):
            raise ValueError("Frozen route no longer matches original Luna")
        if route.get("luna_original_sha256") != benchmark.fingerprint(luna.model_dump(mode="json")):
            raise ValueError("Frozen Luna original was changed")
        if predicted["criterion_ids"]:
            scoped = review_checklist(checklist, predicted["criterion_ids"])
            prices = manifest["inputs"]["pricing"]
            review = task_for(source, scoped, model_settings(prices)[SOL], prices["models"][SOL], stage="sol")
            if route.get("review_task") != review or route.get("review_checklist_sha256") != benchmark.fingerprint(scoped.model_dump(mode="json")):
                raise ValueError("Frozen review request changed")
            allowed[review["task_id"]] = (review, scoped)
    records = checkpoint.get("records")
    if not isinstance(records, dict) or not set(records) <= set(allowed):
        raise ValueError("Unknown checkpoint request")
    for key, record in records.items():
        task, scoped = allowed[key]
        if any(record.get(name) != task[name] for name in ("task_id", "model", "conversation", "request_sha256", "reservation_usd")):
            raise ValueError("Checkpoint request or reservation changed")
        charge = record.get("budget_charge_usd")
        if type(charge) not in {int, float} or not math.isfinite(charge) or charge < 0:
            raise ValueError("Invalid persisted budget charge")
        actual = benchmark.billable_cost(record.get("model_info", {}),
            zero_request=record.get("model_info", {}).get("attempts") == 0 and record.get("status") == "completed")
        status = record.get("status")
        if status in {"running", "interrupted_unknown"}:
            expected_charge = task["reservation_usd"]
        elif status in {"budget_skipped", "configuration_skipped"}:
            expected_charge = 0.0
        elif status in {"completed", "failed"}:
            expected_charge = actual if actual is not None else task["reservation_usd"]
        else:
            raise ValueError("Unknown checkpoint state")
        if abs(charge - expected_charge) > 1e-10:
            raise ValueError("Persisted budget charge disagrees with usage/reservation")
        if status == "completed":
            record_draft(record, Conversation.model_validate(task["conversation"]), scoped)


async def execute(manifest: dict, checkpoint: dict, out: Path, *, api_key: str, budget_usd: float) -> dict:
    verify_manifest(manifest)
    validate_checkpoint(manifest, checkpoint)
    if not math.isfinite(budget_usd) or not 0 < budget_usd <= MAX_BUDGET_USD:
        raise ValueError("Total pilot budget must be in (0, 3] USD")
    if not api_key:
        raise ValueError("An API credential is required for the explicit live command")
    if sum(benchmark.charged_cost(r) for r in checkpoint["records"].values()) > budget_usd + 1e-10:
        raise ValueError("Persisted charges exceed the requested total budget")
    prices = manifest["inputs"]["pricing"]["models"]
    configs = model_settings(manifest["inputs"]["pricing"], api_key=api_key)
    checklist = ChecklistVersion.model_validate(manifest["checklist"])
    started = time.monotonic()
    for record in checkpoint["records"].values():
        if record.get("status") == "running":
            record.update(status="interrupted_unknown", error="Interrupted after dispatch; full reservation retained. No automatic retry.")

    async def dispatch(tasks, scoped, model):
        # A fatal provider/configuration error applies to the whole experiment,
        # including later review scopes and future resumes. Preserve all earlier
        # terminal results and charges; never refund an interrupted request.
        fatal = any(r.get("error_code") in {"auth", "request"} or r.get("reservation_exceeded")
                    for r in checkpoint["records"].values())
        if fatal:
            for task in tasks:
                old = checkpoint["records"].get(task["task_id"], {})
                if old.get("status") in {"completed", "failed", "interrupted_unknown"}:
                    continue
                checkpoint["records"][task["task_id"]] = {**deepcopy(task), "status": "configuration_skipped",
                    "checklist": scoped.model_dump(mode="json"), "results": [], "original_results": [],
                    "score": {}, "original_score": {}, "revision": 1, "model_info": {},
                    "error": "Experiment stopped after a fatal provider error or exceeded reservation",
                    "budget_charge_usd": 0.0, "actual_cost_usd": 0.0}
            checkpoint.update(updated_at=benchmark.utc_now(), budget_usd=budget_usd,
                charged_or_reserved_usd=sum(benchmark.charged_cost(r) for r in checkpoint["records"].values()))
            benchmark.write_json(out / "checkpoint.json", checkpoint)
        else:
            await benchmark.execute_plan(manifest, tasks, {model: configs[model]}, scoped, out, checkpoint,
                                         budget_usd=budget_usd, concurrency=2)

    await dispatch(manifest["tasks"], checklist, LUNA)
    routes = checkpoint.setdefault("routes", {})
    groups: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for task in manifest["tasks"]:
        record = checkpoint["records"][task["task_id"]]
        if record["status"] != "completed":
            continue
        conversation = Conversation.model_validate(task["conversation"])
        luna = record_draft(record, conversation, checklist)
        route = route_criteria(conversation, checklist, luna)
        route["luna_original_sha256"] = benchmark.fingerprint(luna.model_dump(mode="json"))
        selected = route["criterion_ids"]
        if selected:
            scoped = review_checklist(checklist, selected)
            review_task = task_for(conversation, scoped, configs[SOL], prices[SOL], stage="sol")
            route["review_task"] = review_task
            route["review_checklist_sha256"] = benchmark.fingerprint(scoped.model_dump(mode="json"))
            groups[tuple(selected)].append(review_task)
        previous = routes.get(task["task_id"])
        if previous is not None and previous != route:
            raise ValueError("Frozen Luna result or review route changed on resume")
        routes[task["task_id"]] = route
    # Save exact scopes/request hashes before any reviewer dispatch.
    benchmark.write_json(out / "checkpoint.json", checkpoint)
    for selected, tasks in groups.items():
        await dispatch(tasks, review_checklist(checklist, list(selected)), SOL)
    checkpoint["execution_wall_time_ms"] = checkpoint.get("execution_wall_time_ms", 0) + round((time.monotonic() - started) * 1000)
    benchmark.write_json(out / "checkpoint.json", checkpoint)
    pairs = paired_results(manifest, checkpoint)
    benchmark.write_json(out / "paired.json", pairs)
    return pairs


def paired_results(manifest: dict, checkpoint: dict) -> dict:
    validate_checkpoint(manifest, checkpoint)
    checklist = ChecklistVersion.model_validate(manifest["checklist"])
    pairs = []
    for task in manifest["tasks"]:
        conversation = Conversation.model_validate(task["conversation"])
        baseline = checkpoint["records"].get(task["task_id"], {})
        item = {"conversation_key": benchmark.conversation_key(conversation), "language": conversation.language,
                "completed": False, "luna_task_id": task["task_id"], "route": deepcopy(checkpoint.get("routes", {}).get(task["task_id"], {})),
                "luna_original_results": [], "hybrid_results": [], "luna_score": None, "hybrid_score": None,
                "review_status": "not_requested", "fallback_to_luna": False,
                "luna_wall_time_ms": baseline.get("wall_time_ms", 0), "review_wall_time_ms": 0}
        if baseline.get("status") == "completed":
            luna = record_draft(baseline, conversation, checklist)
            if not item["route"]:
                # A process can stop after Luna finished but before scopes were
                # persisted. Do not report those required reviews as unnecessary.
                item["route"] = route_criteria(conversation, checklist, luna)
            selected = item["route"].get("criterion_ids", [])
            reviewer = None
            if selected:
                review_task = item["route"].get("review_task")
                record = checkpoint["records"].get(review_task["task_id"], {}) if review_task else {}
                item["review_status"] = record.get("status", "not_dispatched" if review_task else "not_planned")
                item["review_wall_time_ms"] = record.get("wall_time_ms", 0)
                if record.get("status") == "completed":
                    reviewer = record_draft(record, conversation, review_checklist(checklist, selected))
                else:
                    item["fallback_to_luna"] = True
            merged = combine_drafts(conversation, checklist, luna, reviewer, selected)
            item.update(completed=True, luna_original_results=[r.model_dump(mode="json") for r in luna.results],
                        hybrid_results=[r.model_dump(mode="json") for r in merged.results],
                        luna_score=calculate_score(checklist, luna.results), hybrid_score=calculate_score(checklist, merged.results))
        pairs.append(item)
    return {"run_fingerprint": manifest["fingerprint"], "policy_version": ROUTING_POLICY["version"],
            "baseline_reused": True, "not_ground_truth": True, "pairs": pairs}


def cold_cost(record: dict, price: dict) -> float | None:
    """No cache-hit discount; conservatively charge every input at cache-write rate."""
    info = record.get("model_info", {})
    if record.get("budget_charge_usd", 0) == 0 and not info.get("attempts"):
        return 0.0
    attempts = info.get("attempt_details")
    if info.get("usage_complete") is not True or not attempts or any(a.get("usage_complete") is not True for a in attempts):
        return None
    total = 0.0
    for attempt in attempts:
        if attempt.get("response_service_tier") != "default":
            return None
        inp, output = attempt.get("input_tokens"), attempt.get("output_tokens")
        if type(inp) is not int or type(output) is not int or min(inp, output) < 0:
            return None
        input_rate = max(price["input_per_million"], price.get("cache_write_input_per_million") or 0)
        output_rate = price["output_per_million"]
        if inp > price.get("long_context_threshold", math.inf):
            input_rate *= price.get("long_context_input_multiplier", 1)
            output_rate *= price.get("long_context_output_multiplier", 1)
        total += (inp * input_rate + output * output_rate) / 1_000_000
    return total


def cost_summary(records: list[dict], prices: dict) -> dict:
    # A failed multi-attempt request can have a known billed first attempt and
    # unknown later usage. Preserve that known part without declaring a total.
    known = sum(record["actual_cost_usd"] if record.get("actual_cost_usd") is not None
                else record.get("model_info", {}).get("known_estimated_cost", 0) or 0 for record in records)
    unknown = sum(record.get("actual_cost_usd") is None and record.get("budget_charge_usd", 0) > 0 for record in records)
    cold = [cold_cost(record, prices[record["model"]]) for record in records]
    return {"known_cost_usd": known, "unknown_cost_requests": unknown,
            "total_cost_usd": None if unknown else known,
            "charged_or_reserved_usd": sum(benchmark.charged_cost(record) for record in records),
            "full_cold_estimate_usd": sum(cold) if all(value is not None for value in cold) else None,
            "full_cold_basis": "All attempts; every input token charged at max(uncached, cache-write) rate; output includes reasoning once"}


def has_complete_scores(pair: dict) -> bool:
    return pair.get("completed") is True and all(
        isinstance(pair.get(name), dict) and pair[name].get("status") == "complete"
        and type(pair[name].get("total")) in {int, float} and math.isfinite(pair[name]["total"])
        for name in ("luna_score", "hybrid_score"))


def comparison_report(manifest: dict, checkpoint: dict, expected: dict) -> dict:
    if expected.get("provenance") != "synthetic_regression_authored_by_assistant" or expected.get("not_ground_truth") is not True:
        raise ValueError("Pilot expectations must be declared authored synthetic assertions, not ground truth")
    if (expected.get("dataset_sha256") != manifest["inputs"]["dataset_sha256"]
            or expected.get("checklist_sha256") != manifest["inputs"]["checklist_sha256"]):
        raise ValueError("Expectation source or rubric hash mismatch")
    reference = {benchmark.conversation_key(item): item for item in expected["items"]}
    pairs = paired_results(manifest, checkpoint)["pairs"]
    if len(reference) != len(expected["items"]) or set(reference) != {item["conversation_key"] for item in pairs}:
        raise ValueError("Expectations must match every planned conversation exactly once")
    checklist = ChecklistVersion.model_validate(manifest["checklist"])
    cases, criterion_counts = [], {identifier: {"checked": 0, "baseline_correct": 0, "hybrid_correct": 0,
                                               "corrections": 0, "regressions": 0} for identifier in CRITERIA}
    luna_errors, hybrid_errors = [], []
    for pair, task in zip(pairs, manifest["tasks"], strict=True):
        authored = reference[pair["conversation_key"]]
        answers = authored["expected_results"]
        if len(answers) != 8 or {r["criterion_id"] for r in answers} != set(CRITERIA):
            raise ValueError("Every pilot case requires all eight authored expected decisions")
        expected_results = [CriterionResult(criterion_id=r["criterion_id"], status=r["status"], answer=r.get("answer"),
            explanation=r.get("rationale") or "Authored regression expectation", evidence=r.get("evidence", []),
            checked_span=r.get("checked_span"), gross_error=r.get("gross_error", False)) for r in answers]
        source = Conversation.model_validate(task["conversation"])
        expected_draft = EvaluationDraft(conversation_id=source.conversation_id, group_id=source.group_id,
            checklist_id=checklist.checklist_id, checklist_version=checklist.version, prompt_version="authored-expectation", results=expected_results)
        validate_draft(source, checklist, expected_draft)
        expected_score = calculate_score(checklist, expected_results)
        before = {r["criterion_id"]: r for r in pair["luna_original_results"]}
        after = {r["criterion_id"]: r for r in pair["hybrid_results"]}
        if not isinstance(authored.get("family"), str) or not authored["family"].strip():
            raise ValueError("Every authored case requires a preregistered archetype family")
        case = {"conversation_key": pair["conversation_key"], "language": pair["language"], "family": authored["family"],
                "completed": has_complete_scores(pair), "validated_draft": pair["completed"],
                "checked": 8, "baseline_correct": 0, "hybrid_correct": 0,
                "critical_regression": False, "corrections": 0, "regressions": 0, "changes": []}
        for answer in answers:
            identifier = answer["criterion_id"]
            def matches(value):
                return value is not None and benchmark.decision(value) == benchmark.decision(answer) and value.get("gross_error", False) == answer.get("gross_error", False)
            old, new = matches(before.get(identifier)), matches(after.get(identifier))
            counts = criterion_counts[identifier]
            counts["checked"] += 1
            for key, amount in (("baseline_correct", int(old)), ("hybrid_correct", int(new)),
                                ("corrections", int(new and not old)), ("regressions", int(old and not new))):
                counts[key] += amount
                case[key] += amount
            if identifier == "critical" and old and not new:
                case["critical_regression"] = True
            if before.get(identifier) and after.get(identifier) and benchmark.decision(before[identifier]) != benchmark.decision(after[identifier]):
                case["changes"].append({"criterion_id": identifier,
                    "baseline": {key: before[identifier].get(key) for key in ("status", "answer", "gross_error")},
                    "hybrid": {key: after[identifier].get(key) for key in ("status", "answer", "gross_error")},
                    "authored_expected": {key: answer.get(key) for key in ("status", "answer", "gross_error")},
                    "correction": new and not old, "regression": old and not new})
        if pair["completed"] and expected_score["total"] is not None:
            if pair["luna_score"]["total"] is not None:
                luna_errors.append(abs(pair["luna_score"]["total"] - expected_score["total"]))
            if pair["hybrid_score"]["total"] is not None:
                hybrid_errors.append(abs(pair["hybrid_score"]["total"] - expected_score["total"]))
        cases.append(case)
    records = list(checkpoint["records"].values())
    prices = manifest["inputs"]["pricing"]["models"]
    costs = cost_summary(records, prices)
    luna_costs = cost_summary([r for r in records if r["model"] == LUNA], prices)
    sol_costs = cost_summary([r for r in records if r["model"] == SOL], prices)
    routed = [p for p in pairs if p["route"].get("criterion_ids")]
    reviews_complete = all(p["review_status"] == "completed" for p in routed)
    latency = lambda values: {"mean_ms": statistics.mean(values) if values else None,
                             "p50_ms": statistics.median(values) if values else None,
                             "p95_ms": sorted(values)[max(0, math.ceil(len(values) * .95) - 1)] if values else None}
    checked = sum(c["checked"] for c in cases)
    return {"schema_version": 1, "run_fingerprint": manifest["fingerprint"], "policy": ROUTING_POLICY,
            "metric_status": "paired_assistant_authored_synthetic_comparison_not_human_or_organizer_accuracy",
            "not_ground_truth": True, "baseline_reused": True, "cases": cases, "by_criterion": criterion_counts,
            "checked_decisions": checked,
            "baseline_exact_accuracy_including_na": sum(c["baseline_correct"] for c in cases) / checked if checked else None,
            "hybrid_exact_accuracy_including_na": sum(c["hybrid_correct"] for c in cases) / checked if checked else None,
            "corrections": sum(c["corrections"] for c in cases), "regressions": sum(c["regressions"] for c in cases),
            "baseline_score_mae": statistics.mean(luna_errors) if luna_errors else None,
            "hybrid_score_mae": statistics.mean(hybrid_errors) if hybrid_errors else None,
            "baseline_score_cases": len(luna_errors), "hybrid_score_cases": len(hybrid_errors),
            "routed_conversations": len(routed), "conversation_routing_rate": len(routed) / len(pairs) if pairs else 0,
            "criterion_routing_rate": sum(len(p["route"].get("criterion_ids", [])) for p in pairs) / checked if checked else 0,
            "review_fallbacks": sum(p["fallback_to_luna"] for p in pairs), "all_requested_reviews_complete": reviews_complete,
            "costs": {"luna_only": luna_costs, "incremental_review": sol_costs, "hybrid": costs},
            "latency": {"luna_only": latency([p["luna_wall_time_ms"] for p in pairs if p["completed"]]),
                        "hybrid": latency([p["luna_wall_time_ms"] + p["review_wall_time_ms"] for p in pairs if p["completed"]])},
            "gate": deployment_gate(cases, uncached_total_usd=costs["full_cold_estimate_usd"],
                                    usage_complete=costs["unknown_cost_requests"] == 0 and costs["full_cold_estimate_usd"] is not None,
                                    requested_reviews_complete=reviews_complete)}
