"""Bounded, resumable GPT-Transcribe pilot; private ASR artifacts, never DB writes.

Default --dry-run reads the existing SQLite records and freezes a plan without
network access. --run requires that plan and sends at most one transcription
request per record. Unknown/interrupted requests are never automatically replayed.
The supplied transcript is only a noisy comparison reference, not human ASR GT.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from urllib.parse import quote, urljoin

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.cqa.audio import AudioSourceError, _safe_url, resolve_audio

MODEL = "gpt-transcribe"
ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
RATE_PER_MINUTE = 0.0045
MAX_BYTES = 25_000_000
MAX_RESPONSE_BYTES = 2_000_000
DOWNLOAD_SECONDS = 90
TRANSCRIBE_SECONDS = 600
PROMPT = "Customer support call at Freedom Broker."
MODEL_SOURCE = "https://developers.openai.com/api/docs/models/gpt-transcribe"
FORMAT_SOURCE = "https://developers.openai.com/api/docs/guides/speech-to-text"
MEDIA = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a",
         "audio/x-m4a": ".m4a", "audio/wav": ".wav", "audio/x-wav": ".wav",
         "audio/wave": ".wav", "audio/webm": ".webm", "video/webm": ".webm"}


class PilotError(ValueError):
    """Only fixed, credential-free messages should escape to the CLI."""


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode() + b"\n")
    os.replace(temporary, path)


@contextmanager
def run_lock(out):
    lock = out / "run.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise PilotError("A run lock exists; verify the previous process stopped before manually removing its lock.") from None
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def normalize(text):
    return " ".join(re.findall(r"[^\W_]+", text.casefold().replace("ё", "е"), flags=re.UNICODE))


def comparison(reference, hypothesis, anchors):
    """Term retention and token edit distance against supplied text, never accuracy."""
    ref, hyp = normalize(reference).split(), normalize(hypothesis).split()
    previous = list(range(len(hyp) + 1))
    for index, token in enumerate(ref, 1):
        current = [index]
        for col, actual in enumerate(hyp, 1):
            current.append(min(current[-1] + 1, previous[col] + 1,
                               previous[col - 1] + (token != actual)))
        previous = current
    padded = " " + " ".join(hyp) + " "
    hits = [{"label": a["label"], "present": any(" " + normalize(v) + " " in padded for v in a["variants"])}
            for a in anchors]
    return {"reference_kind": "provided_transcript_not_human_ground_truth", "reference_tokens": len(ref),
            "hypothesis_tokens": len(hyp), "token_edit_distance": previous[-1],
            "reference_normalized_edit_rate": round(previous[-1] / len(ref), 6) if ref else None,
            "anchors": hits, "anchor_hits": sum(x["present"] for x in hits), "anchor_total": len(hits),
            "warning": "Disagreement may be a source ASR error, new ASR error, or different audio scope. Not WER against human GT; no speaker accuracy measured."}


def load_records(db_path, group):
    # mode=ro prevents accidental creation or mutation, even if the path is wrong.
    uri = "file:" + quote(Path(db_path).resolve().as_posix(), safe="/:") + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute("SELECT payload FROM conversations WHERE group_id=? AND channel='voice call' ORDER BY conversation_id", (group,)).fetchall()
    records = [json.loads(row[0]) for row in rows]
    if not records or any(not r.get("audio_url") for r in records):
        raise PilotError("Selected voice records are missing or lack audio sources.")
    return records


def source_fingerprint(records):
    # Hash the URL too, but never persist it or put it in diagnostic output.
    return digest(records)


def make_plan(records, duration_report, anchors):
    durations = {x["conversation_id"]: x["playback"]["duration_seconds"] for x in duration_report["live_calls"] if x.get("passed")}
    items = []
    for record in records:
        cid = record["conversation_id"]
        seconds = durations.get(cid)
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
            raise PilotError("A selected recording lacks a verified playback duration.")
        if cid not in anchors or not anchors[cid]:
            raise PilotError("Frozen content anchors are required for every selected recording.")
        reference = "\n".join(m["text"] for m in record["transcript"] if m["speaker"] != "system")
        padded = " " + normalize(reference) + " "
        for a in anchors[cid]:
            if not a.get("label") or not a.get("variants") or not all(isinstance(v, str) and normalize(v) for v in a["variants"]):
                raise PilotError("Invalid content anchor.")
            if not any(" " + normalize(v) + " " in padded for v in a["variants"]):
                raise PilotError("An anchor is absent from the provided transcript; correct the frozen plan before any model call.")
        items.append({"conversation_id": cid, "group_id": record["group_id"], "language": record["language"],
                      "source_fingerprint": digest(record), "duration_seconds": seconds,
                      "estimated_cost_usd": round(seconds / 60 * RATE_PER_MINUTE, 8),
                      "reservation_usd": round(math.ceil((seconds + 1) / 60) * RATE_PER_MINUTE, 8),
                      "reference": reference, "anchors": anchors[cid],
                      "private_reference_messages": [{k: m[k] for k in ("message_id", "speaker", "text")} for m in record["transcript"]]})
    core = {"schema_version": 1, "model": MODEL, "endpoint_sha256": digest(ENDPOINT), "prompt": PROMPT,
            "language_hints": {"RU": "ru", "EN": "en", "KZ": "kk"}, "rate_per_minute_usd": RATE_PER_MINUTE,
            "price_verified_at": "2026-11-07", "price_source": MODEL_SOURCE, "format_source": FORMAT_SOURCE,
            "maximum_file_bytes": MAX_BYTES, "attempts_per_record": 1, "source_fingerprint": source_fingerprint(records),
            "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "proxy_reference_only": True, "references_never_sent_to_provider": True, "items": items}
    return {**core, "plan_hash": digest(core), "created_at": now()}


def validate_plan(plan, records):
    core = {k: v for k, v in plan.items() if k not in {"plan_hash", "created_at"}}
    if digest(core) != plan["plan_hash"]:
        raise PilotError("Frozen plan was changed.")
    if plan["code_sha256"] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise PilotError("Runner changed after planning; use a new run directory.")
    if plan["source_fingerprint"] != source_fingerprint(records):
        raise PilotError("Source records changed after planning.")


async def download_audio(source, directory, *, client):
    resolved = await resolve_audio(source, client=client)
    current = _safe_url(resolved["audio_url"])
    partial = directory / "audio.partial"
    try:
        async with asyncio.timeout(DOWNLOAD_SECONDS):
            for redirect in range(4):
                async with client.stream("GET", current, follow_redirects=False,
                                         headers={"Accept": "audio/*", "Accept-Encoding": "identity"}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect == 3 or not response.headers.get("location"):
                            raise PilotError("Audio redirect limit exceeded.")
                        current = _safe_url(urljoin(current, response.headers["location"]))
                        continue
                    if response.status_code != 200:
                        raise PilotError("Audio source rejected download.")
                    media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if media not in MEDIA:
                        raise PilotError("Audio format is not supported by this file-transcription pilot.")
                    try:
                        declared = int(response.headers.get("content-length", "0"))
                    except ValueError:
                        raise PilotError("Audio source returned an invalid length.") from None
                    if declared < 0 or declared > MAX_BYTES:
                        raise PilotError("Recording exceeds the 25 MB input limit.")
                    size = 0
                    with partial.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise PilotError("Recording exceeds the 25 MB input limit.")
                            handle.write(chunk)
                    if not size:
                        raise PilotError("Audio source was empty.")
                    target = directory / ("audio" + MEDIA[media])
                    os.replace(partial, target)
                    return target, media
    except (httpx.HTTPError, TimeoutError):
        raise PilotError("Audio download failed or timed out; no transcription was dispatched.") from None
    finally:
        partial.unlink(missing_ok=True)
    raise PilotError("Audio download failed.")


def audio_duration(path):
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                               capture_output=True, timeout=20, check=True, text=True)
        duration = float(json.loads(probe.stdout)["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
        return duration
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        raise PilotError("Local ffprobe could not verify recording duration; no transcription was dispatched.") from None


async def transcribe(path, media, language, api_key, *, client):
    # No retry: a lost response may still have been billed. File name is generic.
    fields = {"model": MODEL, "prompt": PROMPT, "languages[]": language, "response_format": "json"}
    async with asyncio.timeout(TRANSCRIBE_SECONDS):
        with path.open("rb") as file:
            async with client.stream("POST", ENDPOINT, data=fields, files={"file": ("recording" + path.suffix, file, media)},
                                     headers={"Authorization": "Bearer " + api_key}, follow_redirects=False,
                                     timeout=TRANSCRIBE_SECONDS) as response:
                if response.status_code != 200:
                    # Provider error bodies are deliberately not persisted or printed.
                    return {"status": "provider_error", "http_status": response.status_code}
                chunks, count = [], 0
                async for chunk in response.aiter_bytes():
                    count += len(chunk)
                    if count > MAX_RESPONSE_BYTES:
                        return {"status": "invalid_response"}
                    chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks))
        text = body["text"]
        if not isinstance(text, str) or not text.strip() or api_key in text:
            raise ValueError
        languages = body.get("languages", [])
        if not isinstance(languages, list):
            raise ValueError
        clean_languages = [{"code": x["code"]} for x in languages if isinstance(x, dict) and isinstance(x.get("code"), str)
                           and re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2,4})?", x["code"])]
        raw_usage = body.get("usage")
        usage = {k: v for k, v in raw_usage.items() if k in {"seconds", "duration", "input_tokens", "output_tokens", "total_tokens"}
                 and type(v) in (int, float) and math.isfinite(v) and v >= 0} if isinstance(raw_usage, dict) else None
        return {"status": "completed", "text": text, "languages": clean_languages, "usage": usage}
    except (ValueError, KeyError, TypeError):
        return {"status": "invalid_response"}


def summarize(plan, checkpoint):
    rows = list(checkpoint["records"].values())
    known = sum(x.get("estimated_cost_usd", 0) for x in rows if x["status"] == "completed")
    unknown = sum(x.get("reservation_usd", 0) for x in rows if x["status"] in {"dispatching", "interrupted_unknown", "provider_error", "invalid_response", "response_lost_unknown"})
    return {"model": MODEL, "records_planned": len(plan["items"]), "records_completed": sum(x["status"] == "completed" for x in rows),
            "known_duration_price_estimate_usd": round(known, 8), "unknown_request_reservations_usd": round(unknown, 8),
            "accounted_usd": round(known + unknown, 8), "invoice_verified": False,
            "statuses": {k: v["status"] for k, v in checkpoint["records"].items()},
            "quality_reference": "provided_transcript_proxy_not_human_GT", "speaker_labels_measured": False}


def render_comparison(path, item, result, stats):
    lines = ["# Private ASR comparison", "", "The supplied transcript is a noisy proxy, not human audio Ground Truth.",
             "Speaker attribution and word timestamps were not produced or verified.", "", "## Supplied transcript", ""]
    lines.extend(f"{m['message_id']} [{m['speaker']}]: {m['text']}" for m in item["private_reference_messages"])
    lines.extend(["", "## New ASR text (no assigned roles)", "", result["text"], "", "## Frozen term anchors", ""])
    lines.extend(f"- {a['label']}: {'present' if a['present'] else 'not matched'}" for a in stats["anchors"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(plan, records, out, budget, api_key, *, client):
    validate_plan(plan, records)
    checkpoint_path = out / "checkpoint.json"
    checkpoint = read_json(checkpoint_path)
    if checkpoint.get("plan_hash") != plan["plan_hash"]:
        raise PilotError("Checkpoint belongs to a different plan.")
    for row in checkpoint["records"].values():
        if row["status"] == "dispatching":
            row["status"] = "interrupted_unknown"
    write_json(checkpoint_path, checkpoint)
    if any(row.get("http_status") in {400, 401, 403, 404, 422} for row in checkpoint["records"].values()):
        # Persist configuration failures across restarts, not only this process.
        report = summarize(plan, checkpoint)
        write_json(out / "summary.json", report)
        return report
    by_id = {r["conversation_id"]: r for r in records}
    for item in plan["items"]:
        cid = item["conversation_id"]
        prior = checkpoint["records"].get(cid)
        if prior and prior["status"] != "budget_skipped":
            continue
        if summarize(plan, checkpoint)["accounted_usd"] + item["reservation_usd"] > budget + 1e-10:
            checkpoint["records"][cid] = {"status": "budget_skipped", "reservation_usd": item["reservation_usd"]}
            write_json(checkpoint_path, checkpoint)
            continue
        directory = out / "recordings" / digest([item["group_id"], cid])[:16]
        directory.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            path, media = await download_audio(by_id[cid]["audio_url"], directory, client=client)
            seconds = audio_duration(path)
            if abs(seconds - item["duration_seconds"]) > 1:
                raise PilotError("Downloaded recording duration differs from the frozen source.")
        except (PilotError, AudioSourceError):
            checkpoint["records"][cid] = {"status": "download_failed_no_dispatch", "paid_dispatches": 0}
            write_json(checkpoint_path, checkpoint)
            continue
        row = {"status": "dispatching", "dispatched_at": now(), "paid_dispatches": 1,
               "reservation_usd": item["reservation_usd"], "duration_seconds": seconds,
               "audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "audio_bytes": path.stat().st_size,
               "audio_path": path.relative_to(out).as_posix()}
        checkpoint["records"][cid] = row
        write_json(checkpoint_path, checkpoint)  # durable reservation BEFORE network
        request_started = time.monotonic()
        try:
            result = await transcribe(path, media, plan["language_hints"][item["language"]], api_key, client=client)
        except (httpx.HTTPError, TimeoutError):
            result = {"status": "response_lost_unknown"}
        row.update(status=result["status"], request_ms=round((time.monotonic() - request_started) * 1000),
                   total_ms=round((time.monotonic() - started) * 1000), finished_at=now())
        if result["status"] == "completed":
            # API text/usage remains private. Pricing is a duration estimate, not invoice.
            write_json(directory / "transcription.json", result)
            stats = comparison(item["reference"], result["text"], item["anchors"])
            write_json(directory / "comparison.json", stats)
            render_comparison(directory / "review.md", item, result, stats)
            row.update(estimated_cost_usd=round(seconds / 60 * RATE_PER_MINUTE, 8),
                       result_path=(directory / "transcription.json").relative_to(out).as_posix(), comparison=stats)
        elif result.get("http_status") is not None:
            row["http_status"] = result["http_status"]
        write_json(checkpoint_path, checkpoint)
        write_json(out / "summary.json", summarize(plan, checkpoint))
        print(json.dumps({"conversation_id": cid, "status": row["status"], "accounted_usd": summarize(plan, checkpoint)["accounted_usd"]}), flush=True)
        if result.get("http_status") in {400, 401, 403, 404, 422}:
            break  # wrong model/access/parameters: do not repeat across five files
    report = summarize(plan, checkpoint)
    write_json(out / "summary.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/runtime/cqa.sqlite3")
    parser.add_argument("--group", default="DEV")
    parser.add_argument("--duration-report", type=Path, default=ROOT / "docs/validation/audio-audit/live-playback.json")
    parser.add_argument("--anchors", type=Path, required=True, help="Private JSON mapping conversation IDs to frozen label/variants anchors")
    parser.add_argument("--out", type=Path, default=ROOT / "data/evaluations/transcribe-20261107")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--budget-usd", type=float, default=0.25)
    args = parser.parse_args()
    if not math.isfinite(args.budget_usd) or not 0 < args.budget_usd <= 0.25:
        raise PilotError("This pilot requires a positive total budget no greater than $0.25.")
    out = args.out.resolve()
    private_root = (ROOT / "data/evaluations").resolve()
    if not out.is_relative_to(private_root):
        raise PilotError("Pilot artifacts must remain under ignored data/evaluations/.")
    records = load_records(args.db, args.group)
    plan_path = out / "plan.json"
    if args.run and not plan_path.is_file():
        raise PilotError("Run --dry-run first to freeze and inspect the plan.")
    if plan_path.exists():
        plan = read_json(plan_path)
        validate_plan(plan, records)
        if digest(read_json(args.anchors)) != digest({x["conversation_id"]: x["anchors"] for x in plan["items"]}):
            raise PilotError("Anchor file changed after planning.")
    else:
        plan = make_plan(records, read_json(args.duration_report), read_json(args.anchors))
        write_json(plan_path, plan)
        write_json(out / "checkpoint.json", {"plan_hash": plan["plan_hash"], "records": {}})
    if not args.run:
        print(json.dumps({"mode": "dry_run_no_network", "model": MODEL, "records": len(plan["items"]),
                          "minutes": round(sum(x["duration_seconds"] for x in plan["items"]) / 60, 4),
                          "estimated_usd": round(sum(x["estimated_cost_usd"] for x in plan["items"]), 8),
                          "reserved_usd": round(sum(x["reservation_usd"] for x in plan["items"]), 8),
                          "plan_hash": plan["plan_hash"], "paid_calls": 0}))
        return 0
    env = dotenv_values(ROOT / ".env")
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or env.get("LLM_API_KEY") or env.get("OPENAI_API_KEY")
    if not api_key:
        raise PilotError("No configured API key; no requests sent.")
    async def execute():
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            return await run(plan, records, out, args.budget_usd, api_key, client=client)
    with run_lock(out):
        report = asyncio.run(execute())
    print(json.dumps(report))
    return 0 if report["records_completed"] == report["records_planned"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PilotError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
    except Exception:
        print("Transcription pilot stopped; inspect the private checkpoint. No automatic replay.", file=sys.stderr)
        raise SystemExit(2)
