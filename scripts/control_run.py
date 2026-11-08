"""Run ten PROD chats through the running product; stage an auditable JSON only.

No credentials are read, no model fallback is used, and no Git operation occurs.
Repeat the same command to resume. Only --retry-failed authorizes new failed-job attempts.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.cqa.evaluator.service import PROMPT_VERSION
from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, DatasetExport
from evaluation.submission import _read, export_submission, load_approved_checklist, validate_submission


class RunError(RuntimeError):
    pass


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def run_lock(path):
    """OS lock releases after interruption; another runner cannot duplicate dispatch."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RunError("Another runner holds this state lock.") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def safe_paths(args):
    runtime = (ROOT / "data/runtime").resolve()
    default = runtime / ("rehearsal" if args.allow_test_mode else "control-run")
    args.state = Path(args.state).resolve() if args.state else default / "state.json"
    args.out = Path(args.out).resolve() if args.out else default / "results.json"
    for path in (args.state, args.out):
        if not path.is_relative_to(runtime) or path == runtime:
            raise RunError("State and output must stay inside this repository's ignored data/runtime directory.")
        if args.allow_test_mode and not path.is_relative_to(runtime / "rehearsal"):
            raise RunError("Test mode requires state and output inside data/runtime/rehearsal.")
    if args.state == args.out or Path(args.dataset).resolve() in {args.state, args.out}:
        raise RunError("Dataset, state and output paths must be different.")


def load_dataset(path, allow_test_mode=False):
    try:
        dataset = DatasetExport.model_validate(_read(path))
    except (ValueError, TypeError):
        raise RunError("Dataset is not a valid successful DatasetExport (check counts, unique IDs and message fields).") from None
    if dataset.error or len(dataset.data.records) != 10:
        raise RunError("PROD requires a successful export with exactly 10 unique chats.")
    if any(c.channel != "agent chat" for c in dataset.data.records):
        raise RunError("PROD accepts agent chat records only; voice calls are not allowed.")
    group = dataset.data.dataset_group_id.strip().upper()
    if re.search(r"(^|[^A-Z])DEV([^A-Z]|$)", group) or group == "DEV":
        raise RunError("A DEV dataset cannot be used for the PROD control run.")
    if not allow_test_mode and any(word in group for word in ("SYNTHETIC", "DEMO", "REHEARSAL")):
        raise RunError("Synthetic/demo groups require --allow-test-mode.")
    return dataset


def checked_config(config, test=False):
    if not isinstance(config, dict):
        raise RunError("Product configuration is malformed.")
    if not test and (config.get("mode") != "live" or config.get("provider_configured") is not True
                     or config.get("model") != "gpt-6-luna" or config.get("prompt_version") != PROMPT_VERSION
                     or config.get("api_style") != "responses" or config.get("json_mode") != "json_schema"
                     or config.get("reasoning_effort") != "high"):
        raise RunError("Product must use live gpt-6-luna/current prompt with responses, json_schema and high reasoning.")
    if test and config.get("mode") != "test":
        raise RunError("--allow-test-mode requires a product explicitly configured in test mode.")
    if not config.get("model") or not config.get("prompt_version"):
        raise RunError("Product must report its model and prompt version.")
    return {key: config.get(key) for key in ("provider_configured", "provider", "model", "mode",
            "prompt_version", "json_mode", "api_style", "reasoning_effort")}


class API:
    def __init__(self, client, deadline, timeout):
        self.client, self.deadline, self.timeout = client, deadline, timeout

    def request(self, method, path, *, finalizing=False, **kwargs):
        # Retrying POST is safe only because every mutation here is an upsert or
        # an evaluation POST with the same persisted idempotency key.
        for attempt in range(3):
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise RunError("Wall-clock deadline reached; resume with the same state to reconcile existing jobs.")
            try:
                response = self.client.request(method, path,
                    timeout=min(self.timeout, max(0.1, remaining)), **kwargs)
                if response.status_code in {502, 503, 504} and attempt < 2:
                    continue
                if response.status_code >= 400:
                    raise RunError(f"Product request {method} {path.split('?')[0]} returned HTTP {response.status_code}.")
                return response.json()
            except httpx.TransportError:
                if attempt == 2:
                    raise RunError("Product connection failed; resume the same state (existing idempotency keys are retained).") from None
            except ValueError:
                raise RunError("Product returned invalid JSON.") from None


def verify_group(api, conversations, *, finalizing=False):
    group = conversations[0].group_id
    rows = api.request("GET", "/api/conversations", params={"group_id": group}, finalizing=finalizing)
    if not isinstance(rows, list) or sorted(r.get("conversation_id", "") for r in rows) != sorted(c.conversation_id for c in conversations):
        raise RunError("Product group must contain exactly these ten source IDs; old extra records cannot be retained.")
    for conversation in conversations:
        current = api.request("GET", "/api/conversations/" + quote(conversation.conversation_id, safe=""),
            params={"group_id": group}, finalizing=finalizing)
        if current != conversation.model_dump(mode="json"):
            raise RunError("Product source snapshot differs from the supplied dataset.")


def prepare_state(path, identity, conversations):
    if path.exists():
        state = _read(path)
        if state.get("identity") != identity:
            raise RunError("Resume rejected: dataset, checklist, server, team, mode or configuration changed.")
        if set(state.get("jobs", {})) != {c.conversation_id for c in conversations}:
            raise RunError("Resume state has an invalid job inventory.")
        return state
    state = {"schema_version": 1, "identity": identity, "status": "prepared", "jobs": {
        c.conversation_id: {"idempotency_key": "control-" + uuid4().hex, "job_id": None,
                            "status": "pending", "evaluation_id": None, "attempt": 1}
        for c in conversations}}
    atomic_json(path, state)
    return state


def run(args, client=None):
    safe_paths(args)
    dataset = load_dataset(args.dataset, args.allow_test_mode)
    conversations = normalize(dataset)
    checklist = load_approved_checklist().model_dump(mode="json")
    parsed = urlsplit(args.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RunError("Use a plain HTTP(S) product base URL without credentials or query parameters.")
    own_client = client is None
    client = client or httpx.Client(base_url=args.base_url.rstrip("/"), follow_redirects=False)
    duration = args.deadline_seconds
    if not args.allow_test_mode:
        local_now = datetime.now(timezone(timedelta(hours=5)))
        cutoff = datetime(2026, 11, 8, 12, tzinfo=timezone(timedelta(hours=5)))
        duration = min(duration, (cutoff - local_now).total_seconds() - 90)
        if duration <= 0:
            raise RunError("Paid dispatch cutoff reached: this control run stops 90 seconds before 12:00 UTC+5.")
    api = API(client, time.monotonic() + duration, args.request_timeout)
    try:
        configuration = api.request("GET", "/api/config")
        config = checked_config(configuration, args.allow_test_mode)
        team = (args.team_name or configuration.get("team_name") or "").strip()
        if not team:
            raise RunError("Supply --team-name or configure CQA_TEAM_NAME in the product.")
        available = api.request("GET", "/api/checklists")
        if not any(ChecklistVersion.model_validate(item).model_dump(mode="json") == checklist for item in available):
            raise RunError("Product does not contain the exact canonical official checklist from fixtures.")
        identity = {"dataset_sha256": digest(dataset.model_dump(mode="json")), "checklist_sha256": digest(checklist),
                    "prompt_sha256": sha256((ROOT / "prompts/evaluator.md").read_bytes()).hexdigest(),
                    "config": config, "base_url": args.base_url.rstrip("/"), "team_name": team,
                    "test_mode": args.allow_test_mode, "group_id": dataset.data.dataset_group_id,
                    "output": str(args.out)}
        if args.check_only:
            if args.state.exists() and _read(args.state).get("identity") != identity:
                raise RunError("Resume preflight rejected: inputs or configuration changed.")
            print("Preflight passed: 10 chats, current model/prompt and exact official checklist; no import or paid jobs dispatched.")
            return 0
        with run_lock(args.state.with_suffix(".lock")):
            state = prepare_state(args.state, identity, conversations)
            state["status"] = "running"
            state.pop("output_sha256", None)
            atomic_json(args.state, state)
            api.request("POST", "/api/datasets/import", json={"dataset": dataset.model_dump(mode="json")})
            verify_group(api, conversations)
            if args.retry_failed:
                for job in state["jobs"].values():
                    if job["status"] == "failed":
                        history = job.setdefault("previous_attempts", [])
                        history.append({key: job[key] for key in ("idempotency_key", "job_id", "status", "attempt")})
                        job.update(idempotency_key="control-" + uuid4().hex, job_id=None, status="pending",
                                   evaluation_id=None, attempt=job["attempt"] + 1)
                atomic_json(args.state, state)
            last_progress = None
            stopped = None
            try:
                while True:
                    for job in state["jobs"].values():
                        if job["job_id"] and job["status"] not in {"completed", "failed"}:
                            reply = api.request("GET", "/api/evaluation-jobs/" + quote(job["job_id"], safe=""))
                            job.update({key: reply.get(key) for key in ("status", "evaluation_id")})
                            if job["status"] not in {"queued", "running", "completed", "failed"}:
                                raise RunError("Product returned an unknown job status.")
                            atomic_json(args.state, state)
                    active = sum(bool(j["job_id"]) and j["status"] in {"queued", "running"} for j in state["jobs"].values())
                    for conversation_id, job in state["jobs"].items():
                        if active >= 2:
                            break
                        if job["status"] not in {"pending", "dispatching"} or job["job_id"]:
                            continue
                        if checked_config(api.request("GET", "/api/config"), args.allow_test_mode) != config:
                            raise RunError("Product configuration changed during the run; dispatch stopped.")
                        if sha256((ROOT / "prompts/evaluator.md").read_bytes()).hexdigest() != identity["prompt_sha256"]:
                            raise RunError("Local prompt content changed during the run; dispatch stopped.")
                        job["status"] = "dispatching"
                        atomic_json(args.state, state)
                        reply = api.request("POST", "/api/evaluation-jobs", json={"conversation_id": conversation_id,
                            "group_id": dataset.data.dataset_group_id, "checklist_id": checklist["checklist_id"],
                            "checklist_version": checklist["version"], "idempotency_key": job["idempotency_key"]})
                        if not reply.get("job_id") or reply.get("status") not in {"queued", "running", "completed", "failed"}:
                            raise RunError("Product returned an invalid evaluation job.")
                        job.update({key: reply.get(key) for key in ("job_id", "status", "evaluation_id")})
                        atomic_json(args.state, state)
                        active += job["status"] in {"queued", "running"}
                    done = sum(j["status"] == "completed" for j in state["jobs"].values())
                    failed = sum(j["status"] == "failed" for j in state["jobs"].values())
                    progress = (done, failed)
                    if progress != last_progress:
                        print(f"Completed {done}/10; failed {failed}/10.", flush=True)
                        last_progress = progress
                    if done + failed == 10:
                        break
                    if time.monotonic() >= api.deadline:
                        raise RunError("Wall-clock deadline reached; existing jobs may still be running.")
                    time.sleep(min(args.poll_seconds, max(0, api.deadline - time.monotonic())))
            except RunError as error:
                stopped = str(error)
            # Final export contains honest error records if a run was incomplete.
            # Compare the product export against these particular persisted jobs,
            # preventing a previous run's result from silently replacing a failure.
            api.deadline = min(time.monotonic() + 30, api.deadline + 30)
            verify_group(api, conversations, finalizing=True)
            records = []
            for job in state["jobs"].values():
                if job["job_id"]:
                    reply = api.request("GET", "/api/evaluation-jobs/" + quote(job["job_id"], safe=""), finalizing=True)
                    job.update({key: reply.get(key) for key in ("status", "evaluation_id")})
                if job["status"] == "completed" and job["evaluation_id"]:
                    record = api.request("GET", "/api/evaluations/" + quote(job["evaluation_id"], safe=""), finalizing=True)
                    info = record.get("model_info", {})
                    if (info.get("requested_model") or info.get("model")) != config["model"] or info.get("prompt_version") != config["prompt_version"]:
                        raise RunError("Completed evaluation model/prompt does not match this run.")
                    records.append(record)
            mode = "local" if config["provider"] == "local" else "external_api"
            expected = export_submission(conversations, records, team_name=team, model_mode=mode,
                model_name=config["model"], split="prod", approved_checklist=checklist)
            exported = api.request("GET", "/api/submission/export", params={"group_id": dataset.data.dataset_group_id,
                "model_name": config["model"], "split": "prod", "team_name": team, "model_mode": mode}, finalizing=True)
            report = validate_submission(exported, conversations=conversations, split="prod")
            # Storage exports in ID order; organizer inputs may use another order.
            # Validate unique/full coverage first, then ignore only outer record order.
            canonical_export = {**exported, "results": sorted(exported["results"], key=lambda item: item["conversation_id"])}
            canonical_expected = {**expected, "results": sorted(expected["results"], key=lambda item: item["conversation_id"])}
            if canonical_export != canonical_expected:
                raise RunError("Product export differs from this run's job snapshots; no staged output was replaced.")
            atomic_json(args.out, exported)
            state.update(status="completed" if report["errors"] == 0 and stopped is None else "incomplete", report=report,
                         output_sha256=sha256(args.out.read_bytes()).hexdigest(), stopped_reason=stopped)
            atomic_json(args.state, state)
            print(f"Staged {report['ok']}/10 successful records; {report['errors']} errors: {args.out}")
            if stopped:
                print(stopped, file=sys.stderr)
            return 0 if report["errors"] == 0 and stopped is None else 2
    finally:
        if own_client:
            client.close()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--dataset", required=True)
    result.add_argument("--base-url", default="http://127.0.0.1:8061")
    result.add_argument("--team-name")
    result.add_argument("--state")
    result.add_argument("--out")
    result.add_argument("--deadline-seconds", type=float, default=900)
    result.add_argument("--request-timeout", type=float, default=15)
    result.add_argument("--poll-seconds", type=float, default=2)
    result.add_argument("--check-only", action="store_true")
    result.add_argument("--retry-failed", action="store_true")
    result.add_argument("--allow-test-mode", action="store_true", help="Mechanical rehearsal only; requires data/runtime/rehearsal outputs")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if not 0 < args.deadline_seconds <= 900 or not 0 < args.request_timeout <= 60 or not 0 <= args.poll_seconds <= 30:
            raise RunError("Deadline must be 0–900s, request timeout 0–60s, and poll interval 0–30s.")
        return run(args)
    except (ValidationError, KeyError, TypeError):
        print("Control run stopped: malformed product response or persisted state; no data was fabricated.", file=sys.stderr)
    except (RunError, OSError, ValueError) as error:
        print(f"Control run stopped: {error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
