"""Optional FreedomSpeech file-ASR pilot; no key/tariff means dry-run only.

Requires an explicitly verified private tariff JSON, quota and budget for --run.
Calls at most once per recording; unknown calls and authentication failures are
not replayed on resume. Speaker labels remain provider labels, never inferred
client/operator roles. Production data and official transcripts are read-only.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import transcription_pilot as shared

ENDPOINT = "https://api.freedomspeech.kz/v1/audio/transcriptions"
DOCS = "https://freedomspeech.kz/docs/"
SDK = "https://github.com/freedomspeechkz/freedomspeech-python"
FATAL_STATUSES = {400, 401, 403, 404, 422}
DISPATCHED = {"dispatching", "interrupted_unknown", "completed", "provider_error", "invalid_response", "response_lost_unknown"}


def read_tariff(path):
    if path is None:
        return None
    value = shared.read_json(path)
    for key in ("rate_per_minute_usd", "quota_minutes", "billing_increment_seconds"):
        if type(value.get(key)) not in (int, float) or not math.isfinite(value[key]) or value[key] < 0:
            raise shared.PilotError("Tariff rate, quota and billing increment must be explicit finite numbers.")
    if value["quota_minutes"] <= 0 or value["billing_increment_seconds"] <= 0:
        raise shared.PilotError("Verified quota and billing increment must be positive.")
    if value.get("user_verified") is not True or not isinstance(value.get("source"), str) or not value["source"].strip():
        raise shared.PilotError("Tariff must include user_verified=true and a source for the agreed rate/quota.")
    try:
        date.fromisoformat(value["verified_at"])
    except (TypeError, ValueError, KeyError):
        raise shared.PilotError("Tariff must include verified_at in YYYY-MM-DD format.")
    return {k: value[k] for k in ("rate_per_minute_usd", "quota_minutes", "billing_increment_seconds", "user_verified", "source", "verified_at")}


def reservation(seconds, tariff):
    if tariff is None:
        return {"reserved_minutes": None, "reservation_usd": None}
    minutes = math.ceil((seconds + 1) / tariff["billing_increment_seconds"]) * tariff["billing_increment_seconds"] / 60
    return {"reserved_minutes": round(minutes, 8), "reservation_usd": round(minutes * tariff["rate_per_minute_usd"], 8)}


def make_plan(records, duration_report, anchors, tariff):
    # Reuse source/anchor validation, not GPT model settings or estimated prices.
    reference = shared.make_plan(records, duration_report, anchors)
    items = []
    for original in reference["items"]:
        item = {k: v for k, v in original.items() if k not in {"estimated_cost_usd", "reservation_usd"}}
        item.update(reservation(item["duration_seconds"], tariff))
        items.append(item)
    core = {"schema_version": 1, "provider": "FreedomSpeech", "endpoint_sha256": shared.digest(ENDPOINT),
            "request_fields": {"prettify": "false", "response_format": "diarized_json"},
            "known_speaker_names_sent": False, "speaker_role_mapping": None,
            "docs": DOCS, "sdk": SDK, "verified_at": "2026-11-07", "tariff": tariff,
            "source_fingerprint": shared.source_fingerprint(records), "items": items,
            "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "helper_sha256": hashlib.sha256(Path(shared.__file__).read_bytes()).hexdigest(),
            "local_file_limit_bytes": shared.MAX_BYTES, "local_limit_not_provider_documented": True,
            "attempts_per_record": 1, "proxy_reference_only": True, "anchors_never_sent_to_provider": True}
    return {**core, "plan_hash": shared.digest(core), "created_at": shared.now()}


def validate_plan(plan, records):
    core = {k: v for k, v in plan.items() if k not in {"plan_hash", "created_at"}}
    if shared.digest(core) != plan["plan_hash"]:
        raise shared.PilotError("Frozen FreedomSpeech plan was changed.")
    if plan["source_fingerprint"] != shared.source_fingerprint(records):
        raise shared.PilotError("Source records changed after planning.")
    if plan["code_sha256"] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest() or plan["helper_sha256"] != hashlib.sha256(Path(shared.__file__).read_bytes()).hexdigest():
        raise shared.PilotError("Runner/helper changed after planning; use a new run directory.")


def parse_response(body, duration, api_key):
    try:
        if not isinstance(body, dict) or not isinstance(body.get("text"), str) or not body["text"].strip():
            raise ValueError
        if api_key in body["text"]:
            raise ValueError
        raw_segments = body["segments"]
        if not isinstance(raw_segments, list) or not raw_segments or len(raw_segments) > 20_000:
            raise ValueError
        segments = []
        for index, segment in enumerate(raw_segments):
            start, end = segment["start"], segment["end"]
            speaker, text = segment["speaker"], segment["text"]
            if any(type(x) not in (int, float) or not math.isfinite(x) for x in (start, end)) or not 0 <= start <= end <= duration + 1:
                raise ValueError
            if not isinstance(speaker, str) or not speaker.strip() or len(speaker) > 64 or any(ord(x) < 32 for x in speaker) or api_key in speaker:
                raise ValueError
            if not isinstance(text, str) or not text.strip() or api_key in text:
                raise ValueError
            # Local sequence id avoids persisting unexpected metadata or credentials.
            segments.append({"index": index, "start": start, "end": end, "text": text, "speaker": speaker})
        language = body.get("language")
        if not isinstance(language, str) or len(language) > 12 or not language.replace("-", "").isalpha():
            language = None
        return {"status": "completed", "text": body["text"], "comparison_text": "\n".join(x["text"] for x in segments),
                "segments": segments, "language": language, "speaker_role_mapping": None,
                "diarization_correctness_verified": False}
    except (ValueError, TypeError, KeyError):
        return {"status": "invalid_response"}


async def transcribe(path, media, duration, api_key, *, client):
    if not api_key:
        raise shared.PilotError("FreedomSpeech API key is missing; no request sent.")
    if not 0 < path.stat().st_size <= shared.MAX_BYTES:
        raise shared.PilotError("Recording exceeds this pilot's local file bound.")
    async with asyncio.timeout(shared.TRANSCRIBE_SECONDS):
        with path.open("rb") as audio:
            async with client.stream("POST", ENDPOINT, data={"prettify": "false", "response_format": "diarized_json"},
                                     files={"file": ("recording" + path.suffix, audio, media)},
                                     headers={"X-API-Key": api_key}, follow_redirects=False,
                                     timeout=shared.TRANSCRIBE_SECONDS) as response:
                if response.status_code != 200:
                    return {"status": "provider_error", "http_status": response.status_code}
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > shared.MAX_RESPONSE_BYTES:
                        return {"status": "invalid_response"}
                    chunks.append(chunk)
    try:
        return parse_response(json.loads(b"".join(chunks)), duration, api_key)
    except ValueError:
        return {"status": "invalid_response"}


def reusable_audio(directory, cid, item):
    """Optional read-only reuse after both source identity and file hash match."""
    if directory is None:
        return None
    directory = directory.resolve()
    if not directory.is_relative_to((ROOT / "data/evaluations").resolve()):
        raise shared.PilotError("Reused recordings must remain in private data/evaluations/.")
    plan_path, checkpoint_path = directory / "plan.json", directory / "checkpoint.json"
    if not plan_path.exists() or not checkpoint_path.exists():
        return None
    previous_plan, checkpoint = shared.read_json(plan_path), shared.read_json(checkpoint_path)
    if shared.digest({k: v for k, v in previous_plan.items() if k not in {"plan_hash", "created_at"}}) != previous_plan.get("plan_hash"):
        raise shared.PilotError("Cached recording plan was changed.")
    if checkpoint.get("plan_hash") != previous_plan.get("plan_hash"):
        raise shared.PilotError("Cached recording checkpoint and plan disagree.")
    original = next((x for x in previous_plan.get("items", []) if x["conversation_id"] == cid and x["group_id"] == item["group_id"]), None)
    row = checkpoint.get("records", {}).get(cid, {})
    if original is None or original.get("source_fingerprint") != item["source_fingerprint"] or not row.get("audio_path"):
        return None
    path = (directory / row["audio_path"]).resolve()
    if not path.is_relative_to(directory) or not path.is_file() or not 0 < path.stat().st_size <= shared.MAX_BYTES:
        raise shared.PilotError("Cached recording path or size is invalid.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != row.get("audio_sha256"):
        raise shared.PilotError("Cached recording hash changed; reuse refused.")
    media = next((k for k, ext in shared.MEDIA.items() if ext == path.suffix), None)
    if not media:
        raise shared.PilotError("Cached recording format is unsupported by this pilot.")
    return path, media


def summarize(plan, checkpoint):
    rows = [r for r in checkpoint["records"].values() if r["status"] in DISPATCHED]
    return {"provider": "FreedomSpeech", "records_planned": len(plan["items"]),
            "records_completed": sum(r["status"] == "completed" for r in rows),
            "accounted_usd": round(sum(r.get("reservation_usd", 0) for r in rows), 8),
            "accounted_quota_minutes": round(sum(r.get("reserved_minutes", 0) for r in rows), 8),
            "accounting_basis": "conservative_reserved_amounts_including_completed_and_unknown_requests", "invoice_verified": False,
            "unknown_requests": sum(r["status"] != "completed" for r in rows),
            "statuses": {k: v["status"] for k, v in checkpoint["records"].items()},
            "quality_reference": "provided_transcript_proxy_not_human_GT", "role_mapping_verified": False}


async def run(plan, records, out, budget, api_key, *, client, reuse_from=None):
    if not api_key or plan.get("tariff") is None or budget is None:
        raise shared.PilotError("Live FreedomSpeech requires a key, verified rate/quota and explicit total budget.")
    if type(budget) not in (int, float) or not math.isfinite(budget) or budget < 0:
        raise shared.PilotError("Budget must be a finite nonnegative amount.")
    validate_plan(plan, records)
    checkpoint_path = out / "checkpoint.json"
    checkpoint = shared.read_json(checkpoint_path)
    if checkpoint.get("plan_hash") != plan["plan_hash"]:
        raise shared.PilotError("Checkpoint belongs to a different plan.")
    for row in checkpoint["records"].values():
        if row["status"] == "dispatching":
            row["status"] = "interrupted_unknown"
    shared.write_json(checkpoint_path, checkpoint)
    if any(r.get("http_status") in FATAL_STATUSES for r in checkpoint["records"].values()):
        report = summarize(plan, checkpoint)
        shared.write_json(out / "summary.json", report)
        return report
    by_id = {r["conversation_id"]: r for r in records}
    for item in plan["items"]:
        cid = item["conversation_id"]
        previous = checkpoint["records"].get(cid)
        if previous and previous["status"] != "budget_or_quota_skipped":
            continue
        account = summarize(plan, checkpoint)
        if account["accounted_usd"] + item["reservation_usd"] > budget + 1e-10 or account["accounted_quota_minutes"] + item["reserved_minutes"] > plan["tariff"]["quota_minutes"] + 1e-10:
            checkpoint["records"][cid] = {"status": "budget_or_quota_skipped"}
            shared.write_json(checkpoint_path, checkpoint)
            continue
        directory = out / "recordings" / shared.digest([item["group_id"], cid])[:16]
        directory.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            cached = reusable_audio(reuse_from, cid, item)
            path, media = cached or await shared.download_audio(by_id[cid]["audio_url"], directory, client=client)
            duration = shared.audio_duration(path)
            if abs(duration - item["duration_seconds"]) > 1:
                raise shared.PilotError("Downloaded/cached recording duration differs from frozen source.")
        except (shared.PilotError, shared.AudioSourceError):
            checkpoint["records"][cid] = {"status": "source_failed_no_dispatch", "paid_dispatches": 0}
            shared.write_json(checkpoint_path, checkpoint)
            continue
        row = {"status": "dispatching", "dispatched_at": shared.now(), "paid_dispatches": 1,
               "reservation_usd": item["reservation_usd"], "reserved_minutes": item["reserved_minutes"],
               "duration_seconds": duration, "audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "reused_audio": bool(cached)}
        checkpoint["records"][cid] = row
        shared.write_json(checkpoint_path, checkpoint)
        try:
            result = await transcribe(path, media, duration, api_key, client=client)
        except (httpx.HTTPError, TimeoutError):
            result = {"status": "response_lost_unknown"}
        row.update(status=result["status"], total_ms=round((time.monotonic() - started) * 1000), finished_at=shared.now())
        if result["status"] == "completed":
            shared.write_json(directory / "transcription.json", result)
            stats = shared.comparison(item["reference"], result["comparison_text"], item["anchors"])
            shared.write_json(directory / "comparison.json", stats)
            row.update(comparison=stats, speaker_labels=sorted({s["speaker"] for s in result["segments"]}),
                       result_path=(directory / "transcription.json").relative_to(out).as_posix())
        elif result.get("http_status") is not None:
            row["http_status"] = result["http_status"]
        shared.write_json(checkpoint_path, checkpoint)
        shared.write_json(out / "summary.json", summarize(plan, checkpoint))
        print(json.dumps({"conversation_id": cid, "status": row["status"]}), flush=True)
        if result.get("http_status") in FATAL_STATUSES:
            break
    report = summarize(plan, checkpoint)
    shared.write_json(out / "summary.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/runtime/cqa.sqlite3")
    parser.add_argument("--group", default="DEV")
    parser.add_argument("--duration-report", type=Path, default=ROOT / "docs/validation/audio-audit/live-playback.json")
    parser.add_argument("--anchors", type=Path, default=ROOT / "data/evaluations/transcribe-20261107/anchors.json")
    parser.add_argument("--out", type=Path, default=ROOT / "data/evaluations/freedomspeech-20261107")
    parser.add_argument("--tariff", type=Path, help="Private JSON: verified rate_per_minute_usd, quota_minutes, billing_increment_seconds, source, verified_at, user_verified=true")
    parser.add_argument("--budget-usd", type=float, help="Explicit total authorized run cap; no default")
    parser.add_argument("--reuse-from", type=Path, help="Optional private GPT pilot output; reuse only after source and audio hash checks")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.budget_usd is not None and (not math.isfinite(args.budget_usd) or args.budget_usd < 0):
        raise shared.PilotError("Budget must be a finite nonnegative amount.")
    out = args.out.resolve()
    if not out.is_relative_to((ROOT / "data/evaluations").resolve()):
        raise shared.PilotError("Artifacts must remain in private data/evaluations/.")
    env = dotenv_values(ROOT / ".env")
    key = os.getenv("FREEDOMSPEECH_API_KEY") or env.get("FREEDOMSPEECH_API_KEY")
    tariff = read_tariff(args.tariff)
    records = shared.load_records(args.db, args.group)
    plan_path = out / "plan.json"
    if args.run and not plan_path.exists():
        raise shared.PilotError("Freeze a --dry-run plan with the verified tariff before live execution.")
    if plan_path.exists():
        plan = shared.read_json(plan_path)
        validate_plan(plan, records)
        if tariff != plan["tariff"] or shared.digest(shared.read_json(args.anchors)) != shared.digest({x["conversation_id"]: x["anchors"] for x in plan["items"]}):
            raise shared.PilotError("Tariff or anchors changed; freeze a new run directory.")
    else:
        plan = make_plan(records, shared.read_json(args.duration_report), shared.read_json(args.anchors), tariff)
        shared.write_json(plan_path, plan)
        shared.write_json(out / "checkpoint.json", {"plan_hash": plan["plan_hash"], "records": {}})
    if not args.run:
        blockers = (["missing_key"] if not key else []) + (["missing_verified_tariff"] if tariff is None else []) + (["missing_explicit_budget"] if args.budget_usd is None else [])
        report = {"mode": "dry_run_no_network", "status": "blocked_missing_key_and_tariff" if not key and tariff is None else "blocked" if blockers else "ready_not_executed",
                  "blockers": blockers, "provider": "FreedomSpeech", "records": len(plan["items"]),
                  "minutes": round(sum(x["duration_seconds"] for x in plan["items"]) / 60, 4),
                  "reserved_usd": sum(x["reservation_usd"] for x in plan["items"]) if tariff else None,
                  "tariff_publicly_verified": False, "speaker_role_mapping": None, "plan_hash": plan["plan_hash"], "paid_calls": 0}
        shared.write_json(out / "dry-run.json", report)
        print(json.dumps(report))
        return 0
    async def execute():
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            return await run(plan, records, out, args.budget_usd, key, client=client, reuse_from=args.reuse_from)
    with shared.run_lock(out):
        report = asyncio.run(execute())
    print(json.dumps(report))
    return 0 if report["records_completed"] == report["records_planned"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except shared.PilotError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
    except Exception:
        print("FreedomSpeech pilot stopped; inspect private checkpoint. No automatic replay.", file=sys.stderr)
        raise SystemExit(2)
