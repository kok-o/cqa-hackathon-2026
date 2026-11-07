# Reproducible model benchmark

This runner compares the same checklist, original conversations, prompt and output
schema across `gpt-6.1-sol`, `gpt-6-astra` and `gpt-6-luna`. It defaults to reasoning
effort `max`, a stated 16,384-token output limit (including reasoning), one request
at a time, and two conversations per RU/KZ/EN language. The provider adapter may
retry once. It never silently reduces reasoning effort or output limits.

Specify the checklist explicitly. Use `fixtures/checklist.official.json` for the
official rubric after checking its source documents. The example checklist is
only a software fixture. No private transcript, result or key belongs in Git.
Run outputs under `data/evaluations/` are ignored.

## Plan, run, resume

From the repository root in PowerShell:

```powershell
.venv/Scripts/python.exe -m evaluation.benchmark --dataset data/raw/DEV.json --checklist fixtures/checklist.official.json --channel all --per-language 2 --out data/evaluations/openai-dev-max-v1 --dry-run
.venv/Scripts/python.exe -m evaluation.benchmark --dataset data/raw/DEV.json --checklist fixtures/checklist.official.json --channel all --per-language 2 --out data/evaluations/openai-dev-max-v1 --resume --budget-usd 15
```

The dry run makes no provider requests. Credentials come from `LLM_API_KEY` or
`OPENAI_API_KEY` in the environment or ignored `.env`. No API key or endpoint URL
is written into run artifacts. Endpoint identity is hashed. Use `--models` to
select explicit model IDs; the defaults preserve all three requested names.

`--channel all` includes all transcript channels in the official DEV dataset.
The default `agent chat` selects chat only. For a chat control set use that
default. `--groups GROUP ...` and `--conversation-ids ID GROUP/ID ...` filter
before sampling. Multiple `--dataset` paths are supported; duplicate group/ID
pairs are rejected. Sampling is seed-stable, independent of source row order,
balanced across available languages, and round-robin across available groups.
The manifest records actual language counts and whether all three are balanced;
missing languages are never reported as a balanced sample.

`--per-language` is a cap per language. The default produces at most six
conversations and 18 model evaluations. Changing it is an explicit scope change.
Do not expand from a pilot to the full dataset without reviewing its budget.

Each dispatch first reserves the worst permitted output for both attempts plus
a conservative text-input bound, at the largest applicable input/cache-write
rate. Long-context premiums are included. Actual complete usage releases spare
capacity. Unknown usage, timeouts and interrupted dispatches retain the full
reservation. A request is skipped before dispatch if it cannot fit the remaining
budget. `--budget-usd` is the total run cap, including existing checkpoints; it is
not an additional allowance on resume. Provider invoices may differ from public
rate estimates. The reservation model assumes this text-only, tool-free adapter.

Resume requires matching dataset, checklist, prompt, code, selection, model
configuration and price hashes. Completed, failed and interrupted requests are
not automatically repeated. Budget-skipped work may continue with a larger total
cap. Authentication failure stops the run; a fatal model/request failure stops
that model. Each model is initially probed serially before concurrent work.
Use a new output directory after a deliberate change. Do not edit code or the
prompt between a plan and its live run.

## Artifacts and interpretation

- `manifest.json`: source/configuration hashes, selected conversations, rubric,
  request hashes, exact rates and source URLs, and conservative reservations.
- `prompt.snapshot.md`: the exact system prompt at planning time.
- `checkpoint.json`: durable dispatch reservations, timestamps, statuses,
  original model predictions, all-attempt usage and safe error diagnostics.
- `predictions/<model>.json`: the existing `evaluation.cli score` format, one
  model per file. `original_results` and `original_score` preserve predictions
  before any appeal edits.
- `report.json`, `report.csv`, `report.md`: completion, failures, latency, retries,
  observed tokens, costs and pairwise criterion-decision agreement.

Schema validation and quote existence establish technical validity only.
Agreement between models does not establish correctness. Without independent
human labels, no accuracy, F1, model winner or organizer-ranking claim is made.
Unknown billable usage remains unknown; known subtotals are not called total cost.

Optional quality reporting requires `--labels PATH`, where the existing label
format additionally has `independent_human_review: true` and a non-empty `reviewer`
for every reviewed item. Only set this after an independent human review. The
runner validates label/checklist/conversation compatibility before spending.
Holdout reference labels must remain hidden during tuning.

Regenerate reports without provider calls:

```powershell
.venv/Scripts/python.exe -m evaluation.benchmark --out data/evaluations/openai-dev-max-v1 --report-only
```

Assistant-authored synthetic regression expectations belong in a separate test
comparator. They must not be relabelled as independent human ground truth.

## Local models later

The same runner supports `--api-style chat_completions --base-url
http://localhost:11434/v1 --models YOUR_MODEL --reasoning-effort omit` with a
separate `--prices` JSON. Explicit zero-token-fee profiles are valid for a local
endpoint but do not measure hardware/electricity costs. Keep local runs in a new
directory. Every profile needs `currency: "USD"`, `verified_at`, and `models`,
with each model's `input_per_million`, `output_per_million` and `source` provenance.
Missing rates block dispatch rather than assuming a model is free.

Run mocked checks with `.venv/Scripts/python.exe -m unittest
backend.tests.test_benchmark -v`. These checks do not call any live provider.

## Service tier and the 2026-11-07 pilot

The initial `openai-dev-max-v1`, `full-dev-sol-v1` and `regression-max-v1`
processes started before service-tier verification was added. They did not send
`service_tier`, so project defaults applied. Their recorded costs are standard-rate
estimates with an unverified tier, not provider invoices or a hard billing cap.
The running Python processes retain the original imported adapter; later source
changes do not alter their requests. Their frozen manifests and outputs are kept.

The current adapter explicitly requests `service_tier="default"` for official
OpenAI Responses and Chat Completions. It records the returned tier per attempt;
missing or unexpected tiers leave cost unknown and preserve the conservative
budget reservation. Local and compatible providers receive no tier parameter.
See [OpenAI Fast mode](https://developers.openai.com/api/docs/guides/fast-mode).
Do not resume an old fingerprint after code changes; use report-only for analysis
or a new run directory for a deliberate new experiment.
