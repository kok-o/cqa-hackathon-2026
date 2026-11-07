# Synthetic authored regression cases

These six conversations are invented by an assistant. They contain no customer
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

All eight criterion expectations are supplied per case. Consultation steps
0, 5, 10, 15 and 20 are covered. The coverage is deliberately small and does not
cover every rubric boundary, private-service offer, insult, language mixture,
long conversation or realistic distribution shift.

Run after the mocked tests pass and only within an authorized live budget:

```powershell
.venv/Scripts/python.exe -m evaluation.benchmark --dataset fixtures/regression/dataset.json --checklist fixtures/checklist.official.json --channel all --out data/evaluations/regression-max-v1 --budget-usd 15
.venv/Scripts/python.exe -m evaluation.regression --run-dir data/evaluations/regression-max-v1
```

The comparator reads original model results before appeals, counts missing or
failed outputs as failing the planned assertions, and reports per-criterion
differences with the authored rationale. A passing result only means that the
model matched these assertions.

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
`python -m evaluation.regression --run-dir RUN_DIR --expected fixtures/regression/tuning-expected.json`.
For a mixed run, additionally specify `--group SYNTHETIC-PROMPT-TUNING-v1`.
Keep tuning results separate from the original regression results and report
them as prompt-development checks, even when every authored assertion matches.


## Queue responsibility check

`waiting-dataset.json` / `waiting-expected.json` add one invented RU case with
8 authored assertions, created after a development run incorrectly attributed
pre-operator queue time to the operator. Group: `SYNTHETIC-QUEUE-GUARD-v1`.
This is a prompt-development check, not an independent test or human GT.
For mixed runs, pass its expected file and `--group SYNTHETIC-QUEUE-GUARD-v1`.
