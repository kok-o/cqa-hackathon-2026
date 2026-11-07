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
