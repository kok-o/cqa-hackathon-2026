"""Offline control-publisher checks; only disposable local Git remotes are used."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import control_publish as control
from evaluation.submission import CRITERION_IDS, official_score


class ControlPublishTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "repo"
        self.root.mkdir()
        self.dataset = self.directory / "source.json"
        self.candidate = self.directory / "candidate.json"
        records = [{"conversation_id": f"conv-{i}", "channel": "agent chat", "language": "RU",
                    "transcript": [{"timestamp": "00:00", "speaker": "operator", "text": "Здравствуйте"}]}
                   for i in range(10)]
        self.source = {"error": "", "isSuccess": True, "data": {"dataset_group_id": "PROD", "total_records": 10, "records": records}}
        criteria = [{"criterion_id": key, "value": 20 if key == "consultation" else 10 if key == "dialogue" else key != "critical",
                     "rationale": "Evidence from the recorded turn", "evidence_turns": [1]} for key in CRITERION_IDS]
        self.document = {"schema_version": "1.0", "team_name": "SetW", "model": {"mode": "external_api", "name": "gpt-6-luna"},
                         "results": [{"conversation_id": row["conversation_id"], "status": "ok", "criteria": deepcopy(criteria), "score": official_score(criteria)} for row in records]}
        self.write()

    def write(self):
        self.dataset.write_text(json.dumps(self.source), encoding="utf-8")
        self.candidate.write_text(json.dumps(self.document), encoding="utf-8")

    def run_cli(self, *extra):
        with patch.object(control, "ROOT", self.root), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return control.main(["--dataset", str(self.dataset), "--input", str(self.candidate), *extra])

    def git(self, *args):
        return control.git(self.root, *args)

    def initialize_git(self):
        remote = self.directory / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        self.git("init", "-b", "main")
        self.git("config", "user.email", "offline@example.invalid")
        self.git("config", "user.name", "Offline tests")
        (self.root / ".gitignore").write_text("data/runtime/\n", encoding="utf-8")
        target = self.root / control.TARGET
        target.parent.mkdir(parents=True)
        target.write_text("previous result\n", encoding="utf-8")
        self.git("add", "--", ".gitignore", control.TARGET)
        self.git("commit", "-m", "Initial")
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-u", "origin", "main")

    def test_dry_run_validates_without_git_or_writes(self):
        before = self.candidate.read_bytes()
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(self.candidate.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_rejects_bad_source_and_metadata(self):
        for group in ["DEV", "PROD_TEST", "synthetic-control", "demo"]:
            self.source["data"]["dataset_group_id"] = group
            self.write()
            self.assertEqual(self.run_cli(), 2)
        self.source["data"]["dataset_group_id"] = "PROD"
        self.document["model"]["name"] = "synthetic-model"
        self.write()
        self.assertEqual(self.run_cli(), 2)

    def test_rejects_missing_duplicate_voice_and_mismatched_ids(self):
        original = deepcopy(self.source)
        for mutation in [lambda d: d["data"]["records"].pop(),
                         lambda d: d["data"]["records"][1].update(conversation_id="conv-0"),
                         lambda d: d["data"]["records"][0].update(channel="voice call"),
                         lambda d: d["data"]["records"][0].update(conversation_id="unknown")]:
            self.source = deepcopy(original)
            mutation(self.source)
            self.write()
            self.assertEqual(self.run_cli(), 2)

    def test_error_rows_require_explicit_opt_in_and_are_preserved(self):
        self.document["results"][0] = {"conversation_id": "conv-0", "status": "error", "criteria": [], "score": None, "error": "Provider timeout"}
        self.write()
        self.assertEqual(self.run_cli(), 2)
        self.assertEqual(self.run_cli("--allow-errors"), 0)
        self.assertIsNone(json.loads(self.candidate.read_text())["results"][0]["score"])

    def test_malformed_json_and_protected_path(self):
        for content in ['{"team_name":"SetW","team_name":"other"}', '{"score":NaN}', '[1]']:
            self.candidate.write_text(content, encoding="utf-8")
            self.assertEqual(self.run_cli(), 2)
        with self.assertRaisesRegex(ValueError, "protected"):
            control.protect_paths(self.root, self.dataset, self.root / control.TARGET)

    def test_publish_backup_remote_verification_and_idempotence(self):
        self.initialize_git()
        self.git("switch", "-c", "codex/control-ready")
        with patch.object(control, "before_deadline"):
            self.assertEqual(self.run_cli("--publish"), 0)
            first = self.git("rev-parse", "HEAD")
            self.assertEqual(self.run_cli("--publish"), 0)
        self.assertEqual(first, self.git("rev-parse", "HEAD"))
        self.assertEqual(first, control.remote_head(self.root))
        self.assertEqual(json.loads((self.root / control.TARGET).read_text(encoding="utf-8")), self.document)
        backups = list((self.root / "data/runtime/control-publish-backups").glob("*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "previous result\n")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_unrelated_unpublished_commit_is_refused(self):
        self.initialize_git()
        (self.root / "preparation.txt").write_text("unpublished work", encoding="utf-8")
        self.git("add", "--", "preparation.txt")
        self.git("commit", "-m", "Preparation")
        before = (self.root / control.TARGET).read_bytes()
        with patch.object(control, "before_deadline"):
            self.assertEqual(self.run_cli("--publish"), 2)
        self.assertEqual((self.root / control.TARGET).read_bytes(), before)

    def test_dirty_checkout_and_deadline_preserve_results(self):
        self.initialize_git()
        target = self.root / control.TARGET
        before = target.read_bytes()
        with patch.object(control, "before_deadline", side_effect=ValueError("cutoff")):
            self.assertEqual(self.run_cli("--publish"), 2)
        self.assertEqual(target.read_bytes(), before)
        (self.root / "user.txt").write_text("keep me", encoding="utf-8")
        self.git("add", "--", "user.txt")
        with patch.object(control, "before_deadline"):
            self.assertEqual(self.run_cli("--publish"), 2)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "user.txt")

    def test_failed_push_retries_existing_commit(self):
        self.initialize_git()
        original_git = control.git
        def failing_push(root, *args, **kwargs):
            if args[:1] == ("push",):
                raise ValueError("Simulated offline remote")
            return original_git(root, *args, **kwargs)
        with patch.object(control, "before_deadline"), patch.object(control, "git", side_effect=failing_push):
            self.assertEqual(self.run_cli("--publish"), 2)
        first = self.git("rev-parse", "HEAD")
        with patch.object(control, "before_deadline"):
            self.assertEqual(self.run_cli("--publish"), 0)
        self.assertEqual(self.git("rev-parse", "HEAD"), first)
        self.assertEqual(control.remote_head(self.root), first)


if __name__ == "__main__":
    unittest.main()
