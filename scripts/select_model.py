"""Preview or explicitly apply a manual model profile; never calls an API.

Public standard USD token prices verified on 2026-11-07. This is a user choice
between profiles, not an automatic fallback or a claim that one is more accurate.
Only the listed non-secret settings can be written; API keys are never loaded.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import set_key

from backend.cqa.evaluator.service import EvaluatorError, Settings


ENV_PATH = ROOT / ".env"
ALLOWED_FIELDS = (
    "LLM_BASE_URL", "LLM_MODEL", "LLM_API_STYLE", "LLM_JSON_MODE", "LLM_REASONING_EFFORT",
    "LLM_MAX_TOKENS", "LLM_TIMEOUT_SECONDS", "LLM_INPUT_COST_PER_MILLION",
    "LLM_OUTPUT_COST_PER_MILLION", "LLM_CACHED_INPUT_COST_PER_MILLION",
    "LLM_CACHE_WRITE_INPUT_COST_PER_MILLION",
)
PROFILES = {
    "luna-economy": {
        "LLM_MODEL": "gpt-6-luna", "LLM_REASONING_EFFORT": "high",
        "LLM_MAX_TOKENS": "16384", "LLM_TIMEOUT_SECONDS": "180",
        "LLM_INPUT_COST_PER_MILLION": "0.1", "LLM_OUTPUT_COST_PER_MILLION": "0.5",
        "LLM_CACHED_INPUT_COST_PER_MILLION": "0.01", "LLM_CACHE_WRITE_INPUT_COST_PER_MILLION": "0.125",
    },
    "sol-reference": {
        "LLM_MODEL": "gpt-6.1-sol", "LLM_REASONING_EFFORT": "max",
        "LLM_MAX_TOKENS": "32768", "LLM_TIMEOUT_SECONDS": "600",
        "LLM_INPUT_COST_PER_MILLION": "2", "LLM_OUTPUT_COST_PER_MILLION": "10",
        "LLM_CACHED_INPUT_COST_PER_MILLION": "0.1", "LLM_CACHE_WRITE_INPUT_COST_PER_MILLION": "2.5",
    },
}


def profile_settings(profile: str) -> dict[str, str]:
    values = {"LLM_BASE_URL": "https://api.openai.com/v1", "LLM_API_STYLE": "responses",
              "LLM_JSON_MODE": "json_schema", **PROFILES[profile]}
    if set(values) != set(ALLOWED_FIELDS):
        raise ValueError("Profile contains an unexpected setting")
    # Construct directly with an empty key. Do not use Settings.from_env or load_dotenv.
    Settings(base_url=values["LLM_BASE_URL"], model=values["LLM_MODEL"], api_key="",
        api_style=values["LLM_API_STYLE"], json_mode=values["LLM_JSON_MODE"],
        reasoning_effort=values["LLM_REASONING_EFFORT"], max_tokens=int(values["LLM_MAX_TOKENS"]),
        timeout_seconds=float(values["LLM_TIMEOUT_SECONDS"]),
        input_cost_per_million=float(values["LLM_INPUT_COST_PER_MILLION"]),
        output_cost_per_million=float(values["LLM_OUTPUT_COST_PER_MILLION"]),
        cached_input_cost_per_million=float(values["LLM_CACHED_INPUT_COST_PER_MILLION"]),
        cache_write_input_cost_per_million=float(values["LLM_CACHE_WRITE_INPUT_COST_PER_MILLION"])).validate()
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=sorted(PROFILES))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only; this is the default")
    mode.add_argument("--apply", action="store_true", help="Write only the displayed non-secret fields to the repository .env")
    args = parser.parse_args(argv)
    try:
        values = profile_settings(args.profile)
        if args.apply:
            for name in ALLOWED_FIELDS:
                success, _, _ = set_key(str(ENV_PATH), name, values[name], quote_mode="never")
                if not success:
                    raise OSError("Could not update profile field")
        print(f"Profile: {args.profile}")
        print("Applied to repository .env." if args.apply else "Dry run: .env was not read or changed.")
        for name in ALLOWED_FIELDS:
            print(f"{name}={values[name]}")
        print("Prices: public standard USD per million tokens, verified 2026-11-07.")
        print("Manual profile selection; no automatic fallback or accuracy ranking.")
        if args.apply:
            print("Restart the API process to use this profile.")
        else:
            print("To apply, repeat with --apply; then restart the API process.")
    except (ValueError, EvaluatorError):
        print("Profile validation failed; .env was not changed.", file=sys.stderr)
        return 2
    except OSError:
        print("Could not finish updating .env. Check file access and reapply the complete profile before restarting the API.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
