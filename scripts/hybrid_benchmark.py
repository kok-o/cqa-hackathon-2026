"""Plan, explicitly run, or report a frozen paired Luna/Sol pilot. No production changes."""
from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from backend.cqa.models import ChecklistVersion
from evaluation import benchmark, hybrid


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "live", "report"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--dataset", help="Required only for plan; expected labels are never used for routing")
    parser.add_argument("--checklist", default="fixtures/checklist.official.json")
    parser.add_argument("--prices", default="evaluation/model_profiles.json")
    parser.add_argument("--expected", help="Plan hashes raw bytes only; report reads the frozen authored assertions")
    parser.add_argument("--budget-usd", type=float, help="Explicit live total cap, at most $3, including preserved unknown usage")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    try:
        if args.command == "plan":
            if not args.dataset or not args.expected:
                raise ValueError("Plan requires dataset and expected-file paths")
            if (out / "manifest.json").exists() or (out / "checkpoint.json").exists():
                raise ValueError("Plan output exists; choose a new folder")
            manifest = hybrid.make_manifest(benchmark.read_json(args.dataset),
                ChecklistVersion.model_validate(benchmark.read_json(args.checklist)), benchmark.load_prices(args.prices),
                expected_sha256=sha256(Path(args.expected).read_bytes()).hexdigest())
            checkpoint = benchmark.checkpoint_for(out, manifest, resume=False)
            benchmark.write_json(out / "manifest.json", manifest)
            benchmark.write_json(out / "checkpoint.json", checkpoint)
            benchmark.safe_print(benchmark.canonical({"mode": "plan_no_provider_calls", "conversations": manifest["conversation_count"],
                "fingerprint": manifest["fingerprint"], "maximum_budget_usd": hybrid.MAX_BUDGET_USD,
                "luna_worst_case_usd": manifest["luna_worst_case_usd"],
                "all_criteria_review_worst_case_usd": manifest["all_criteria_review_worst_case_usd"]}).decode())
            return 0
        manifest = benchmark.read_json(out / "manifest.json")
        hybrid.verify_manifest(manifest)
        checkpoint = benchmark.checkpoint_for(out, manifest, resume=True)
        if args.command == "live":
            if args.budget_usd is None:
                raise ValueError("Live requires an explicit total budget")
            load_dotenv(ROOT / ".env", override=False)
            key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", "")
            with hybrid.run_lock(out):
                # Re-read under the process lock; an earlier process may have
                # finished between this command starting and acquiring the lock.
                checkpoint = benchmark.checkpoint_for(out, manifest, resume=True)
                result = asyncio.run(hybrid.execute(manifest, checkpoint, out, api_key=key, budget_usd=args.budget_usd))
            complete = sum(pair["completed"] for pair in result["pairs"])
            reviews = all(pair["review_status"] in {"not_requested", "completed"} for pair in result["pairs"])
            benchmark.safe_print(f"Saved paired pilot: {complete}/{len(result['pairs'])} Luna cases; all requested reviews complete={reviews}")
            return 0 if complete == len(result["pairs"]) and reviews else 1
        if not args.expected or sha256(Path(args.expected).read_bytes()).hexdigest() != manifest["inputs"]["expected_file_sha256"]:
            raise ValueError("Report requires the exact preregistered expected file")
        report = hybrid.comparison_report(manifest, checkpoint, benchmark.read_json(args.expected))
        benchmark.write_json(out / "report.json", report)
        benchmark.safe_print(benchmark.canonical({"report": str(out / "report.json"), "gate": report["gate"]}).decode())
        return 0
    except Exception:
        # Validation errors may embed supplied data. Do not echo arbitrary input,
        # provider response text, credentials, or local exception details.
        benchmark.safe_print("Hybrid pilot failed validation or configuration; verify frozen files, prices, credential availability and total budget.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
