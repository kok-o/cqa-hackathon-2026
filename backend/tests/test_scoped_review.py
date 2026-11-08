"""Pure merge validation; no provider requests or production examples."""
from copy import deepcopy
from hashlib import sha256
import json
import unittest

from backend.cqa.models import Conversation, EvaluationDraft
from backend.cqa.scoring import calculate_score
from backend.cqa.storage import digest
from evaluation.hybrid import review_checklist
from evaluation.submission import load_approved_checklist
from scripts.merge_scoped_review import merge_scoped_reviews


class ScopedReviewTests(unittest.TestCase):
    def setUp(self):
        self.checklist = load_approved_checklist()
        self.conversation = Conversation(conversation_id="fresh-fictional-chat", group_id="SYNTHETIC",
            channel="agent chat", language="EN", transcript=[{"timestamp": "00:00", "speaker": "operator",
            "message_id": "m1", "text": "Hello. Choose the public map link."}])
        results = [{"criterion_id": c.criterion_id, "status": "applicable",
            "answer": False if c.criterion_id == "critical" else c.scale_max if c.answer_type == "scale" else True,
            "explanation": "Synthetic mechanical assertion", "evidence": [{"message_id": "m1", "quote": "Hello."}],
            "gross_error": False, "checked_span": None} for c in self.checklist.criteria]
        self.draft = EvaluationDraft(conversation_id=self.conversation.conversation_id, group_id=self.conversation.group_id,
            checklist_id=self.checklist.checklist_id, checklist_version=self.checklist.version, prompt_version="v11", results=results)
        results = [r.model_dump(mode="json") for r in self.draft.results]
        score = calculate_score(self.checklist, self.draft.results)
        info = {"model": "gpt-6-luna", "requested_model": "gpt-6-luna", "prompt_version": "v11",
                "attempts": 1, "input_tokens": 10, "output_tokens": 2, "estimated_cost": .01, "usage_complete": True}
        self.source = [{"evaluation_id": "original", "conversation": self.conversation.model_dump(mode="json"),
            "checklist": self.checklist.model_dump(mode="json"), "results": deepcopy(results), "original_results": results,
            "score": score, "original_score": deepcopy(score), "model_info": info, "revision": 1, "appeals": []}]
        reviewed = self.draft.model_dump(mode="json")
        reviewed["results"] = [deepcopy(next(r for r in results if r["criterion_id"] == "dialogue"))]
        reviewed["results"][0]["answer"] = 7
        self.reviews = [{"conversation_id": self.conversation.conversation_id, "group_id": self.conversation.group_id,
            "draft": reviewed, "model_info": deepcopy(info),
            "source_snapshot_sha256": digest(self.conversation.model_dump(mode="json")),
            "checklist_snapshot_sha256": digest(self.checklist.model_dump(mode="json"))}]

    def test_only_selected_results_replaced_inputs_unchanged_and_usage_summed(self):
        before = deepcopy((self.source, self.reviews))
        result = merge_scoped_reviews(self.source, self.reviews)["evaluations"][0]
        self.assertEqual((self.source, self.reviews), before)
        for old, new in zip(self.source[0]["original_results"], result["original_results"]):
            if old["criterion_id"] == "dialogue":
                self.assertEqual(new, self.reviews[0]["draft"]["results"][0])
            else:
                self.assertEqual(new, old)
        self.assertEqual(result["model_info"]["attempts"], 2)
        self.assertEqual(result["model_info"]["input_tokens"], 20)
        self.assertEqual(result["model_info"]["estimated_cost"], .02)
        self.assertEqual(result["model_info"]["prompt_version"], "v11+scoped-review-v1")
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["appeals"], [])
        self.assertNotEqual(result["evaluation_id"], "original")
        self.assertEqual(merge_scoped_reviews(self.source, [])["evaluations"], self.source)

    def test_unsupported_or_contradictory_models_rejected(self):
        for target in (self.source[0], self.reviews[0]):
            for field in ("model", "requested_model"):
                original = target["model_info"][field]
                target["model_info"][field] = "gpt-6.1-sol"
                with self.assertRaises(ValueError):
                    merge_scoped_reviews(self.source, self.reviews)
                target["model_info"][field] = original

    def test_snapshot_hash_mismatch_rejected(self):
        for field in ("source_snapshot_sha256", "checklist_snapshot_sha256"):
            review = deepcopy(self.reviews)
            review[0][field] = "0" * 64
            with self.assertRaisesRegex(ValueError, "snapshot hash mismatch"):
                merge_scoped_reviews(self.source, review)

    def test_duplicate_criterion_and_unresolved_review_rejected(self):
        duplicate = deepcopy(self.reviews)
        duplicate[0]["draft"]["results"] *= 2
        with self.assertRaises(ValueError):
            merge_scoped_reviews(self.source, duplicate)
        self.reviews[0]["draft"]["results"][0].update(status="needs_review", answer=None)
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            merge_scoped_reviews(self.source, self.reviews)

    def test_service_input_hash_binds_source_and_actual_scoped_rubric(self):
        scoped = review_checklist(self.checklist, ["dialogue"])
        payload = {"conversation": self.conversation.model_dump(mode="json"),
                   "checklist": scoped.model_dump(mode="json")}
        self.reviews[0]["model_info"]["input_hash"] = sha256(json.dumps(payload,
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(len(merge_scoped_reviews(self.source, self.reviews)["evaluations"]), 1)
        self.reviews[0]["model_info"]["input_hash"] = digest(payload)
        with self.assertRaisesRegex(ValueError, "service input hash mismatch"):
            merge_scoped_reviews(self.source, self.reviews)

    def test_scoped_draft_identity_must_match_source_and_checklist(self):
        for field, value in (("conversation_id", "other"), ("group_id", "other"),
                             ("checklist_id", "other"), ("checklist_version", 999)):
            review = deepcopy(self.reviews)
            review[0]["draft"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                merge_scoped_reviews(self.source, review)


if __name__ == "__main__":
    unittest.main()
