"""Predeclared deployment gate for a paired, assistant-authored pilot.

Resample conversations, not correlated criteria. This gate is a conservative
development decision, never an estimate of organizer ground-truth accuracy.
"""
from __future__ import annotations

import random
import statistics
from collections import Counter, defaultdict
from typing import Any


GATE = {
    "version": "hybrid-pilot-gate-v2",
    "conversations": 24,
    "criteria_per_conversation": 8,
    "language_counts": {"RU": 8, "KZ": 8, "EN": 8},
    "minimum_net_corrected_decisions": 4,
    "bootstrap_samples": 10000,
    "bootstrap_seed": 20261107,
    "require_family_labels": True,
    "require_family_bootstrap_lower_above_zero": True,
    "minimum_bootstrap_lower_bound": 0,
    "maximum_critical_regressions": 0,
    "allow_language_accuracy_decline": False,
    "maximum_uncached_usd_per_conversation": 0.05,
    "require_complete_usage": True,
    "require_all_requested_reviews_complete": True,
}


def deployment_gate(cases: list[dict[str, Any]], *, uncached_total_usd: float | None,
                    usage_complete: bool, requested_reviews_complete: bool) -> dict:
    """Each case: language, completed, baseline_correct, hybrid_correct,
    checked (8), critical_regression, family, and a unique conversation_key.
    """
    unique = len({c["conversation_key"] for c in cases}) == len(cases)
    full = (len(cases) == GATE["conversations"] and unique
            and dict(Counter(c["language"] for c in cases)) == GATE["language_counts"]
            and all(c["completed"] and c["checked"] == GATE["criteria_per_conversation"] for c in cases))
    if any(type(c[name]) is not int or not 0 <= c[name] <= GATE["criteria_per_conversation"]
           for c in cases for name in ("baseline_correct", "hybrid_correct")):
        raise ValueError("Invalid per-conversation correct decision count")
    deltas = [c["hybrid_correct"] - c["baseline_correct"] for c in cases]
    if any(not isinstance(v, int) or abs(v) > GATE["criteria_per_conversation"] for v in deltas):
        raise ValueError("Invalid per-conversation correctness delta")
    bootstrap = []
    if deltas:
        rng = random.Random(GATE["bootstrap_seed"])
        bootstrap = sorted(sum(rng.choices(deltas, k=len(deltas))) / (len(deltas) * 8)
                           for _ in range(GATE["bootstrap_samples"]))
    lower = bootstrap[int(len(bootstrap) * .025)] if bootstrap else None
    upper = bootstrap[min(len(bootstrap) - 1, int(len(bootstrap) * .975))] if bootstrap else None
    families = defaultdict(list)
    family_labels_valid = all(isinstance(c.get("family"), str) and c["family"].strip() for c in cases)
    if family_labels_valid:
        for case, delta in zip(cases, deltas):
            families[case["family"]].append(delta)
    family_bootstrap = []
    if families:
        groups = list(families.values())
        rng = random.Random(GATE["bootstrap_seed"] + 1)
        for _ in range(GATE["bootstrap_samples"]):
            sampled = rng.choices(groups, k=len(groups))
            family_bootstrap.append(sum(sum(group) for group in sampled) / (8 * sum(len(group) for group in sampled)))
        family_bootstrap.sort()
    family_interval = ([family_bootstrap[int(len(family_bootstrap) * .025)],
                        family_bootstrap[min(len(family_bootstrap) - 1, int(len(family_bootstrap) * .975))]]
                       if family_bootstrap else [None, None])
    by_language = {language: sum(c["hybrid_correct"] - c["baseline_correct"] for c in cases if c["language"] == language)
                   for language in sorted({c["language"] for c in cases})}
    per_case_cost = uncached_total_usd / len(cases) if uncached_total_usd is not None and cases else None
    conditions = {
        "complete_unique_24_by_8": full,
        "net_at_least_four_corrected_decisions": sum(deltas) >= GATE["minimum_net_corrected_decisions"],
        "cluster_bootstrap_lower_bound_above_zero": lower is not None and lower > 0,
        "family_labels_present": family_labels_valid and bool(cases),
        "family_bootstrap_lower_bound_above_zero": family_interval[0] is not None and family_interval[0] > 0,
        "no_new_critical_error": not any(c["critical_regression"] for c in cases),
        "no_language_decline": all(value >= 0 for value in by_language.values()),
        "known_uncached_cost_at_most_five_cents_per_case": per_case_cost is not None and 0 <= per_case_cost <= .05,
        "usage_complete": usage_complete,
        "all_requested_reviews_completed": requested_reviews_complete,
    }
    return {"gate": GATE, "enable_hybrid": all(conditions.values()), "conditions": conditions,
            "net_corrected_decisions": sum(deltas), "by_language_net_corrected": by_language,
            "paired_case_bootstrap_95_interval": [lower, upper],
            "paired_family_bootstrap_95_interval": family_interval, "family_count": len(families),
            "mean_accuracy_delta": statistics.mean(deltas) / 8 if deltas else None,
            "uncached_cost_per_conversation_usd": per_case_cost,
            "interpretation": "Assistant-authored synthetic pilot; interval resamples these cases, not real production traffic or human labels."}
