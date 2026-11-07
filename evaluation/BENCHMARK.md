# Reproducible model benchmark

The runner defaults to `gpt-6-luna` with reasoning effort `high`, a 16,384-token
output limit (including reasoning), a 180-second timeout, one request at a time,
and two conversations per RU/KZ/EN language. The provider adapter may retry once.
It never silently reduces reasoning effort or output limits. Multiple explicitly
selected models receive the same checklist, conversations, prompt and output schema.

Specify the checklist explicitly. Use `fixtures/checklist.official.json` for the
official rubric after checking its source documents. The example checklist is
only a software fixture. Private transcripts, raw provider outputs and keys stay
out of Git. Run outputs under `data/evaluations/` are ignored. The reviewed
official submission files under `results/` are tracked separately.

## Plan, run, resume

From the repository root in PowerShell:

```powershell
.venv/Scripts/python.exe -m evaluation.benchmark --dataset data/raw/DEV.json --checklist fixtures/checklist.official.json --models gpt-6-luna --reasoning-effort high --max-output-tokens 16384 --timeout-seconds 180 --channel all --per-language 2 --out data/evaluations/luna-dev-high-pilot --dry-run
.venv/Scripts/python.exe -m evaluation.benchmark --dataset data/raw/DEV.json --checklist fixtures/checklist.official.json --models gpt-6-luna --reasoning-effort high --max-output-tokens 16384 --timeout-seconds 180 --channel all --per-language 2 --out data/evaluations/luna-dev-high-pilot --resume --budget-usd 1
```

The dry run makes no provider requests. Credentials come from `LLM_API_KEY` or
`OPENAI_API_KEY` in the environment or ignored `.env`. No API key or endpoint URL
is written into run artifacts. Endpoint identity is hashed. Use `--models` to
select explicit model IDs; only Luna is selected by default. The example budget
is a maximum authorization for that run, not a predicted charge.

`--channel all` includes all transcript channels in the official DEV dataset.
The default `agent chat` selects chat only. For a chat control set use that
default. `--groups GROUP ...` and `--conversation-ids ID GROUP/ID ...` filter
before sampling. Multiple `--dataset` paths are supported; duplicate group/ID
pairs are rejected. Sampling is seed-stable, independent of source row order,
balanced across available languages, and round-robin across available groups.
The manifest records actual language counts and whether all three are balanced;
missing languages are never reported as a balanced sample.

`--per-language` is a cap per language. The default produces at most six
conversations and six model evaluations with the default single model. Changing
the sample size or selected models is an explicit scope change.
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

For an optional comparison using the earlier three-model maximum-effort setup,
select every model and setting explicitly in a new directory:

```powershell
.venv/Scripts/python.exe -m evaluation.benchmark --dataset data/raw/DEV.json --checklist fixtures/checklist.official.json --models gpt-6.1-sol gpt-6-astra gpt-6-luna --reasoning-effort max --max-output-tokens 16384 --timeout-seconds 300 --channel all --per-language 2 --out data/evaluations/three-model-max-comparison --dry-run
```

That plan has up to 18 model evaluations. Inspect its reservation before an
authorized live run; repeat the same flags with `--resume` and an explicitly
reviewed `--budget-usd` value. It is not the economy default or an automatic fallback.

## Artifacts and interpretation

- `manifest.json`: source/configuration hashes, selected conversations, rubric,
  request hashes, exact rates and source URLs, and conservative reservations.
- `prompt.snapshot.md`: the exact system prompt at planning time.
- `checkpoint.json`: durable dispatch reservations, timestamps, statuses,
  original model predictions, all-attempt usage and safe error diagnostics.
- `predictions/<model>-<id-hash>.json`: the existing `evaluation.cli score` format, one
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
.venv/Scripts/python.exe -m evaluation.benchmark --out data/evaluations/luna-dev-high-pilot --report-only
```

Assistant-authored synthetic regression expectations belong in a separate test
comparator. They must not be relabelled as independent human ground truth.
For a mixed run, use the comparator's explicit `--group` filter and a distinct
output file per expectation set; unknown cases inside the selected group still
fail validation. See [the regression fixture notes](../fixtures/regression/README.md).

The targeted `cqa-evaluator-v6` development run on 2026-11-07 completed 19/19
evaluations. Its authored checks passed: 48 original regression assertions,
24 tuning assertions, 8 queue-responsibility assertions and 1 focused speculation
assertion. These 81 matches are development checks, not independent accuracy,
a model ranking, or evidence that every criterion in every completed evaluation
is correct. The speculation fixture's hash metadata was corrected after the run
from file-byte hashes to canonical JSON hashes; its text and expected decision
were unchanged.

## Manual profile selection

```powershell
python scripts/select_model.py luna-economy
python scripts/select_model.py luna-economy --apply
python scripts/select_model.py sol-reference --apply
```

The default is a dry run. Only explicit `--apply` writes the allowlisted model
settings to the root `.env`; keys and unrelated settings are preserved. Restart
the API after applying. `luna-economy` uses Luna/high/16384/180 seconds;
`sol-reference` uses Sol/max/32768/600 seconds. These are selectable profiles,
not automatic escalation or a claim that Sol is more accurate. The included
standard prices were verified on 2026-11-07.
This selector changes the working API configuration. Benchmark model, reasoning,
token and timeout choices come from the runner's flags/defaults independently.

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
Those processes retained the original imported adapter; later source changes
did not alter their requests. Their frozen manifests and outputs are kept.

The current adapter explicitly requests `service_tier="default"` for official
OpenAI Responses and Chat Completions. It records the returned tier per attempt;
missing or unexpected tiers leave cost unknown and preserve the conservative
budget reservation. Local and compatible providers receive no tier parameter.
See [OpenAI Fast mode](https://developers.openai.com/api/docs/guides/fast-mode).
Do not resume an old fingerprint after code changes; use report-only for analysis
or a new run directory for a deliberate new experiment.
