# Synthetic authored regression cases

The original six conversations are invented by an assistant. They contain no customer
data. The expected decisions are fallible software test assertions, **not**
independent human labels, organizer answers or a model-quality ranking.

The fixture uses the official checklist snapshot in
`fixtures/checklist.official.json`. `expected.json` pins its normalized hash and
the dataset hash. It intentionally uses a separate comparator instead of the
human-label evaluation workflow.

| Case | Main conditions |
|---|---|
| RU: bot greeting | Bot greeting does not establish operator contact; client already gave enough detail; frustration ignored; farewell alone fails closure. |
| RU: private disclosure | Explicitly unverified visitor receives a private balance; identification fails and critical violation is true, independently of completeness. |
| KZ: secondary detail | Primary question solved; explicitly secondary font-size question omitted; specific empathy and full closure. |
| KZ: partial disconnect | Identity verified; one of two material questions answered; explicit connection loss makes closure N/A. |
| EN: vague guidance | Operator asks a necessary clarifying question but gives vague instructions; client must ask what the wording means. |
| EN: injection | Customer tries to manipulate an evaluator; operator replies incoherently and closes voluntarily. |

All eight criterion expectations are supplied for each original case (48 assertions).
Consultation steps
0, 5, 10, 15 and 20 are covered. The coverage is deliberately small and does not
cover every rubric boundary, private-service offer, insult, language mixture,
long conversation or realistic distribution shift.

Run after the mocked tests pass and only within an authorized live budget:

```powershell
.venv/Scripts/python.exe -m evaluation.benchmark --dataset fixtures/regression/dataset.json --checklist fixtures/checklist.official.json --models gpt-6-luna --reasoning-effort high --max-output-tokens 16384 --timeout-seconds 180 --channel all --out data/evaluations/regression-luna-high --dry-run
.venv/Scripts/python.exe -m evaluation.benchmark --dataset fixtures/regression/dataset.json --checklist fixtures/checklist.official.json --models gpt-6-luna --reasoning-effort high --max-output-tokens 16384 --timeout-seconds 180 --channel all --out data/evaluations/regression-luna-high --resume --budget-usd 1
.venv/Scripts/python.exe -m evaluation.regression --run-dir data/evaluations/regression-luna-high
```

The comparator reads original model results before appeals, counts missing or
failed outputs as failing the planned assertions, and reports per-criterion
differences with the authored rationale. A passing result only means that the
model matched these assertions. The runner's economy defaults are Luna/high,
16,384 output tokens and a 180-second timeout. An optional three-model/max
comparison requires explicit flags and its own reviewed budget; see
[benchmark instructions](../../evaluation/BENCHMARK.md).

For a run containing both real DEV and synthetic conversations, select the
synthetic group explicitly:

```powershell
.venv/Scripts/python.exe -m evaluation.regression --run-dir RUN_DIR --group SYNTHETIC-OFFICIAL-REGRESSION-v1 --out RUN_DIR/regression-original-report.json
```

Replace `RUN_DIR` with the saved run directory. The report identifies the selected
group and skipped records. Without `--group`, unrelated conversations remain a
validation error. Unknown conversations inside the selected group are never
silently excluded, and missing predictions still fail their planned assertions.

## Three prompt-development cases

`tuning-dataset.json` and `tuning-expected.json` contain three additional invented
conversations (RU, KZ and EN), with 24 assistant-authored criterion assertions.
They were written **after inspecting DEV model outputs**, specifically to develop
the prompt. Their purpose is `prompt_development_after_observing_DEV`: they are
**not an independent test**, human labels, organizer ground truth, or evidence of
general accuracy. They do not replace or modify the original six cases above.

| Tuning case | Intended distinction |
|---|---|
| RU: repaired directions | An operator's vague instruction causes an explicit repeat question; later successful repair does not erase that earlier communication failure. |
| KZ: neutral inconvenience | An unavailable operation is described without emotional wording; a generic polite refusal does not address the specific inconvenience. |
| EN: attributed paraphrase | An operator explicitly repeats the client's report without accessing an account; public navigation guidance adds no protected account fact. |

The expected file pins the same official checklist snapshot and its own dataset
hash. After an authorized run of this dataset, compare with
`python -m evaluation.regression --run-dir RUN_DIR --expected fixtures/regression/tuning-expected.json --out RUN_DIR/regression-tuning-report.json`.
For a mixed run, additionally specify `--group SYNTHETIC-PROMPT-TUNING-v1`.
Keep tuning results separate from the original regression results and report
them as prompt-development checks, even when every authored assertion matches.


## Queue responsibility check

`waiting-dataset.json` / `waiting-expected.json` add one invented RU case with
8 authored assertions, created after a development run incorrectly attributed
pre-operator queue time to the operator. Group: `SYNTHETIC-QUEUE-GUARD-v1`.
This is a prompt-development check, not an independent test or human GT.
For mixed runs, pass its expected file and `--group SYNTHETIC-QUEUE-GUARD-v1`.
Use a distinct output such as `--out RUN_DIR/regression-waiting-report.json`.

## Focused speculation check

`speculation-dataset.json` / `speculation-expected.json` add one invented RU
conversation in group `SYNTHETIC-SPECULATION-GUARD-v1`. The operator makes an
unsupported guess about an account operation, then explicitly retracts it and
states that the account was not accessed. Its single authored assertion is
`critical=false` with `gross_error=false`: the withdrawn guess alone does not
establish disclosure of a confidential fact. Other criterion answers are
intentionally unlabelled; this is **one check, not eight**.

This fixture was authored for prompt development after observing DEV outputs.
It is not independent human or organizer ground truth. Its checklist and dataset
hash metadata initially used file-byte hashes; after the run, that metadata was
corrected to the comparator's canonical JSON hashing convention. Neither the
conversation nor the expected decision changed. Preserve that chronology when
reporting the result.

```powershell
.venv/Scripts/python.exe -m evaluation.regression --run-dir RUN_DIR --expected fixtures/regression/speculation-expected.json --group SYNTHETIC-SPECULATION-GUARD-v1 --out RUN_DIR/regression-speculation-report.json
```

## Targeted v6 development result

The 2026-11-07 targeted run using `cqa-evaluator-v6` completed 19/19 evaluations.
The separate authored comparisons passed 48/48 original, 24/24 tuning, 8/8 queue
and 1/1 speculation assertions. Completion measures whether validated output was
returned; the 81 assertion matches measure agreement with these authored checks.
Neither establishes general accuracy, correct semantic evidence in all answers,
or a model winner. These cases were used during development and must not be
presented as an untouched holdout.
