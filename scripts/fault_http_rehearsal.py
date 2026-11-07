"""Exercise real localhost HTTP and process faults using only synthetic data.

Starts its own API process, temporary SQLite database and local provider double.
It never contacts a paid provider or opens an existing application database.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time

import httpx


ROOT = Path(__file__).resolve().parents[1]
CANARY = "TEST_SECRET_FAULT_PROVIDER_BODY_NEVER_PUBLIC"


class Provider:
    def __init__(self):
        self.calls = Counter()
        self.scenarios = {}
        self.entered = {}
        self.release = {}
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["content-length"])))
                source = json.loads(request["input"][1]["content"])
                conversation = source["conversation"]
                identity = (conversation["group_id"], conversation["conversation_id"])
                with outer.lock:
                    outer.calls[identity] += 1
                    attempt = outer.calls[identity]
                    scenario = outer.scenarios.get(identity, "valid")
                    entered = outer.entered.setdefault(identity, threading.Event())
                    released = outer.release.setdefault(identity, threading.Event())
                entered.set()
                if scenario == "hold" and attempt == 1:
                    released.wait(30)
                if scenario == "timeout":
                    time.sleep(1.3)
                body = outer.response(source)
                status = 200
                if scenario in {"http429", "http503"}:
                    status = int(scenario[4:])
                    body = {"error": {"message": CANARY}, "usage": body["usage"]}
                elif scenario == "refusal":
                    body["output"][0]["content"] = [{"type": "refusal", "refusal": CANARY}]
                elif scenario == "schema":
                    body["output"][0]["content"][0]["text"] = json.dumps({"results": [{"criterion_id": CANARY}]})
                elif scenario == "repair" and attempt == 1:
                    body["output"][0]["content"][0]["text"] = '{"results":[' + CANARY
                raw = (CANARY if scenario == "corrupt" else json.dumps(body)).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @staticmethod
    def response(source):
        conversation, checklist = source["conversation"], source["checklist"]
        message = next(m for m in conversation["transcript"] if m["speaker"] == "operator")
        results = [{"criterion_id": c["criterion_id"], "status": "applicable",
                    "answer": c["scale_max"] if c["answer_type"] == "scale" else c["positive_answer"],
                    "explanation": "Synthetic HTTP reliability check, not a quality label.",
                    "evidence": [{"message_id": message["message_id"], "quote": message["text"]}],
                    "checked_span": None, "gross_error": False} for c in checklist["criteria"]]
        return {"id": "resp_fault_test", "model": "fault-test-provider", "status": "completed",
                "output": [{"type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": json.dumps({"results": results})}]}],
                "usage": {"input_tokens": 100, "output_tokens": 20,
                          "input_tokens_details": {"cached_tokens": 10, "cache_write_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 5}}}

    def close(self):
        for event in self.release.values():
            event.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


class ApiProcess:
    def __init__(self, folder: Path, provider: Provider):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.folder = folder
        self.provider = provider
        self.process = None
        self.log = None
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=5, trust_env=False)

    def start(self):
        environment = os.environ.copy()
        environment.update({"CQA_DATABASE_PATH": str(self.folder / "temporary.sqlite3"),
            "LLM_BASE_URL": f"http://127.0.0.1:{self.provider.server.server_port}/v1", "LLM_MODEL": "fault-test-provider",
            "LLM_API_KEY": "TEST_LOCAL_PROVIDER_KEY", "OPENAI_API_KEY": "", "LLM_API_STYLE": "responses",
            "LLM_REASONING_EFFORT": "high", "LLM_JSON_MODE": "json_schema", "LLM_MAX_TOKENS": "1024",
            "LLM_TIMEOUT_SECONDS": "1", "LLM_CONCURRENCY": "2", "LLM_INPUT_COST_PER_MILLION": "1",
            "LLM_OUTPUT_COST_PER_MILLION": "2", "LLM_CACHED_INPUT_COST_PER_MILLION": "0.1",
            "LLM_CACHE_WRITE_INPUT_COST_PER_MILLION": "1.25", "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "",
            "NO_PROXY": "127.0.0.1,localhost,::1"})
        self.log = (self.folder / "api.log").open("ab")
        self.process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.cqa.main:app", "--host", "127.0.0.1",
                "--port", str(self.port), "--no-access-log", "--log-level", "warning"], cwd=ROOT, env=environment,
                stdout=self.log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("Isolated API exited before readiness")
            try:
                info = self.client.get("/api/config").json()
                if info.get("provider") == "local" and info.get("model") == "fault-test-provider":
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.1)
        raise AssertionError("Isolated API readiness timed out")

    def kill(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        if self.log is not None:
            self.log.close()
            self.log = None

    def close(self):
        self.kill()
        self.client.close()


def dataset(group, ids):
    return {"isSuccess": True, "error": "", "data": {"dataset_group_id": group, "total_records": len(ids),
            "records": [{"conversation_id": identity, "channel": "agent chat", "language": "RU",
                "audio_url": None, "transcript": [{"speaker": "client", "timestamp": "00:00", "text": "Synthetic request"},
                    {"speaker": "operator", "timestamp": "00:01", "text": "Synthetic operator response"}]} for identity in ids]}}


def run() -> dict:
    checks, findings = [], []
    provider = Provider()
    with TemporaryDirectory(prefix="cqa-http-fault-") as temporary:
        app = ApiProcess(Path(temporary), provider)
        try:
            app.start()
            client = app.client
            rules = client.get("/api/checklists").raise_for_status().json()[0]

            def imported(data):
                return client.post("/api/datasets/import", json={"dataset": data}).raise_for_status().json()

            def submitted(group, identity, key):
                return client.post("/api/evaluation-jobs", json={"group_id": group, "conversation_id": identity,
                    "checklist_id": rules["checklist_id"], "checklist_version": rules["version"],
                    "idempotency_key": key}).raise_for_status().json()

            def finished(job, *, timeout=8):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    state = client.get("/api/evaluation-jobs/" + job["job_id"]).raise_for_status().json()
                    if state["status"] in {"completed", "failed"}:
                        return state
                    time.sleep(0.03)
                raise AssertionError("Synthetic job did not finish within its bound")

            def evaluated(job):
                state = finished(job)
                assert state["status"] == "completed", "Expected a completed synthetic job"
                return client.get("/api/evaluations/" + state["evaluation_id"]).raise_for_status().json()

            # Same source ID in two groups stays isolated.
            imported(dataset("GROUP_A", ["same"]))
            imported(dataset("GROUP_B", ["same"]))
            one = evaluated(submitted("GROUP_A", "same", "identity-a"))
            two = evaluated(submitted("GROUP_B", "same", "identity-b"))
            assert one["evaluation_id"] != two["evaluation_id"]
            assert one["conversation"]["group_id"] == "GROUP_A" and two["conversation"]["group_id"] == "GROUP_B"
            checks.append("same_id_different_group_isolated")

            # Concurrent duplicate keys and an unread/lost HTTP response dispatch once.
            imported(dataset("DUPLICATE", ["same"]))
            with ThreadPoolExecutor(max_workers=8) as pool:
                duplicates = list(pool.map(lambda _: submitted("DUPLICATE", "same", "duplicate-key"), range(8)))
            assert len({item["job_id"] for item in duplicates}) == 1
            evaluated(duplicates[0])
            assert provider.calls[("DUPLICATE", "same")] == 1
            request = client.build_request("POST", "/api/evaluation-jobs", json={"group_id": "DUPLICATE", "conversation_id": "same",
                "checklist_id": rules["checklist_id"], "checklist_version": rules["version"], "idempotency_key": "lost-response-key"})
            unread = client.send(request, stream=True)
            assert unread.status_code == 202
            unread.close()
            evaluated(submitted("DUPLICATE", "same", "lost-response-key"))
            assert provider.calls[("DUPLICATE", "same")] == 2
            checks.append("concurrent_and_lost_response_idempotency")

            # Old snapshot may complete after the current snapshot has already completed.
            source = dataset("CHANGED", ["same"] + [f"other-{n}" for n in range(9)])
            imported(source)
            provider.scenarios[("CHANGED", "same")] = "hold"
            old_job = submitted("CHANGED", "same", "old-snapshot")
            assert provider.entered.setdefault(("CHANGED", "same"), threading.Event()).wait(3)
            source["data"]["records"][0]["transcript"][1]["text"] = "Changed current operator response"
            imported(source)
            current = evaluated(submitted("CHANGED", "same", "new-snapshot"))
            provider.release[("CHANGED", "same")].set()
            old = evaluated(old_job)
            assert old["conversation"]["transcript"][1]["text"] == "Synthetic operator response"
            assert current["conversation"]["transcript"][1]["text"] == "Changed current operator response"
            summary = next(item for item in client.get("/api/conversations", params={"group_id": "CHANGED"}).json() if item["conversation_id"] == "same")
            if summary["latest_evaluation_id"] != current["evaluation_id"]:
                findings.append("Late old-snapshot completion hides the completed current-snapshot evaluation")
            else:
                checks.append("late_old_snapshot_does_not_hide_current_result")
            exported = client.get("/api/submission/export", params={"group_id": "CHANGED", "model_name": "fault-test-provider",
                    "team_name": "Synthetic fault test", "model_mode": "local", "split": "prod"}).raise_for_status().json()
            exported_current = next(item for item in exported["results"] if item["conversation_id"] == "same")
            if exported_current["status"] != "ok":
                findings.append("Official export selects stale completed snapshot over valid current result")
            else:
                checks.append("official_export_selects_current_snapshot_after_late_completion")

            # Review concurrency: a stale decision cannot silently overwrite a newer revision.
            appeals = [client.post(f"/api/evaluations/{one['evaluation_id']}/appeals", json={"criterion_id": criterion,
                       "comment": "Synthetic concurrent review"}).raise_for_status().json() for criterion in ("contact", "empathy")]
            decision = {"action": "uphold", "reason": "Synthetic reviewed decision", "expected_revision": 1}
            assert client.post(f"/api/appeals/{appeals[0]['appeal_id']}/decision", json=decision).status_code == 200
            assert client.post(f"/api/appeals/{appeals[1]['appeal_id']}/decision", json=decision).status_code == 409
            preserved = client.get(f"/api/evaluations/{one['evaluation_id']}").json()
            assert preserved["revision"] == 2 and preserved["original_results"] == one["original_results"]
            checks.append("stale_appeal_conflict_preserves_original")

            # Actual process kill while two jobs run and a third waits for the semaphore.
            imported(dataset("RESTART", ["run-a", "run-b", "queued"]))
            for identity in ("run-a", "run-b"):
                provider.scenarios[("RESTART", identity)] = "hold"
            interrupted = [submitted("RESTART", identity, "restart-" + identity) for identity in ("run-a", "run-b", "queued")]
            for identity in ("run-a", "run-b"):
                assert provider.entered.setdefault(("RESTART", identity), threading.Event()).wait(3)
            assert client.get("/api/evaluation-jobs/" + interrupted[2]["job_id"]).json()["status"] == "queued"
            app.kill()
            for identity in ("run-a", "run-b"):
                provider.release[("RESTART", identity)].set()
            app.start()
            for job in interrupted:
                assert finished(job)["status"] == "failed"
            after_restart = client.get(f"/api/evaluations/{one['evaluation_id']}").raise_for_status().json()
            assert after_restart == preserved
            assert provider.calls[("RESTART", "queued")] == 0
            replay = submitted("RESTART", "run-a", "restart-run-a")
            assert replay["status"] == "failed" and replay["job_id"] == interrupted[0]["job_id"]
            evaluated(submitted("RESTART", "run-a", "restart-explicit-new-key"))
            checks.append("kill_restart_preserves_history_and_fails_pending_without_replay")

            faults = {}
            for scenario, expected_attempts in (("corrupt", 2), ("schema", 2), ("refusal", 1), ("http429", 2), ("http503", 2), ("timeout", 2), ("repair", 2)):
                imported(dataset("FAULT", [scenario]))
                provider.scenarios[("FAULT", scenario)] = scenario
                state = finished(submitted("FAULT", scenario, "fault-" + scenario))
                assert provider.calls[("FAULT", scenario)] == expected_attempts
                assert state["status"] == ("completed" if scenario == "repair" else "failed")
                assert CANARY not in json.dumps(state)
                if state["status"] == "completed":
                    result = client.get("/api/evaluations/" + state["evaluation_id"]).json()
                    info = result["model_info"]
                    assert info["attempts"] == 2 and info["input_tokens"] == 200
                    assert CANARY not in json.dumps(result)
                else:
                    info = state.get("model_info")
                    if scenario in {"schema", "refusal", "http429", "http503"} and not info:
                        findings.append(f"Failed API job loses observed token usage and cost diagnostics ({scenario})")
                if info:
                    assert info["attempts"] == expected_attempts
                    if scenario in {"corrupt", "timeout"}:
                        assert info["usage_complete"] is False and info["estimated_cost"] is None
                    else:
                        assert info["usage_complete"] is True and info["input_tokens"] == 100 * expected_attempts
                        assert abs(info["estimated_cost"] - 0.000131 * expected_attempts) < 1e-10
                faults[scenario] = {"status": state["status"], "attempts": provider.calls[("FAULT", scenario)],
                                    "estimated_cost": info.get("estimated_cost") if info else None,
                                    "usage_complete": info.get("usage_complete") if info else None}
            checks.append("real_http_faults_have_bounded_attempts_and_no_public_secret")
            assert CANARY not in (Path(temporary) / "api.log").read_text(encoding="utf-8", errors="replace")
            return {"mode": "local_http_and_owned_process_only", "paid_provider_calls": 0,
                    "checks": checks, "findings": findings, "provider_faults": faults}
        finally:
            app.close()
            provider.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = run()
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(content, encoding="utf-8")
    print(content)
    return bool(report["findings"])


if __name__ == "__main__":
    raise SystemExit(main())
