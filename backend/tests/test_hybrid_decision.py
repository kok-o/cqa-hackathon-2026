import unittest

from evaluation.hybrid_decision import deployment_gate


class HybridDeploymentGateTests(unittest.TestCase):
    def cases(self):
        return [{"conversation_key": str(i), "family": str(i), "language": ("RU", "KZ", "EN")[i % 3],
                 "completed": True, "checked": 8, "baseline_correct": 6,
                 "hybrid_correct": 7, "critical_regression": False} for i in range(24)]

    def assess(self, cases, **kwargs):
        return deployment_gate(cases, **{"uncached_total_usd": .6, "usage_complete": True,
                                        "requested_reviews_complete": True, **kwargs})

    def test_consistent_improvement_can_pass(self):
        self.assertTrue(self.assess(self.cases())["enable_hybrid"])

    def test_equal_quality_is_not_an_improvement(self):
        cases = self.cases()
        for c in cases:
            c["hybrid_correct"] = c["baseline_correct"]
        self.assertFalse(self.assess(cases)["enable_hybrid"])

    def test_four_correlated_corrections_in_one_case_are_not_enough(self):
        cases = self.cases()
        for c in cases:
            c["hybrid_correct"] = c["baseline_correct"]
        cases[0].update(baseline_correct=4, hybrid_correct=8)
        result = self.assess(cases)
        self.assertTrue(result["conditions"]["net_at_least_four_corrected_decisions"])
        self.assertFalse(result["conditions"]["cluster_bootstrap_lower_bound_above_zero"])

    def test_missing_duplicate_failed_and_unknown_usage_block_activation(self):
        self.assertFalse(self.assess(self.cases()[:-1])["enable_hybrid"])
        cases = self.cases()
        cases[0]["conversation_key"] = cases[1]["conversation_key"]
        self.assertFalse(self.assess(cases)["enable_hybrid"])
        cases = self.cases()
        cases[0]["completed"] = False
        self.assertFalse(self.assess(cases)["enable_hybrid"])
        self.assertFalse(self.assess(self.cases(), usage_complete=False)["enable_hybrid"])
        self.assertFalse(self.assess(self.cases(), requested_reviews_complete=False)["enable_hybrid"])
        self.assertFalse(self.assess(self.cases(), uncached_total_usd=None)["enable_hybrid"])

    def test_critical_regression_language_decline_and_excess_cost_block(self):
        cases = self.cases()
        cases[0]["critical_regression"] = True
        self.assertFalse(self.assess(cases)["enable_hybrid"])
        cases = self.cases()
        for c in cases:
            if c["language"] == "KZ":
                c["hybrid_correct"] = 5
        self.assertFalse(self.assess(cases)["conditions"]["no_language_decline"])
        self.assertFalse(self.assess(self.cases(), uncached_total_usd=1.21)["enable_hybrid"])

    def test_correlated_family_gain_does_not_pass_despite_case_gain(self):
        cases = self.cases()
        for i, case in enumerate(cases):
            if i < 6:
                case["family"] = "related"
            else:
                case["hybrid_correct"] = case["baseline_correct"]
        report = self.assess(cases)
        self.assertTrue(report["conditions"]["cluster_bootstrap_lower_bound_above_zero"])
        self.assertFalse(report["conditions"]["family_bootstrap_lower_bound_above_zero"])
        self.assertFalse(report["enable_hybrid"])
