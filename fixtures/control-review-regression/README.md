# Control-review synthetic regression

Twelve fresh assistant-authored short chats: four RU, four KZ, four EN.
The 24 preregistered assertions cover only C2 `discovery` and C6 `dialogue`.
These assertions are fallible software regression expectations, **not ground
truth, human labels, organizer answers, or evidence of general accuracy**.
No model calls were made while authoring this set. No raw FBroker/control chats
or actual control predictions were read. Other synthetic fixture structure and
the official checklist were consulted; no conversation text was copied.

The general conditional procedure does not require private account status.
Matched missing-model/revision examples require clarification: the clarified
cases assert Yes and the assumed cases assert No. A wrong but plainly worded
selection does not automatically lower dialogue.

Operator handling holds have an explicit SYSTEM start event. Unwarned holds
assert 7; a warned hold asserts 10. A bot-announced pre-operator queue, a timestamp
gap alone, explicitly negated waiting, and an immediately completed check all
assert 10. Dialogue never uses N/A. Only unambiguous target criteria are asserted;
this fixture does not attempt a full checklist score.

`dataset.json` is a validated `DatasetExport`, with transcript array order as
source position and monotonically ordered timestamps in each conversation.
`expected.json` uses the existing `evaluation.regression` schema, including
canonical JSON SHA256 bindings for the dataset and official checklist.

After running the evaluator on this dataset, compare with:

```powershell
python -m evaluation.regression --expected fixtures/control-review-regression/expected.json --run-dir RUN_DIR --group SYNTHETIC-CONTROL-REVIEW-v1
```

Freeze these files before predictions. Do not relabel them to match model outputs;
record disagreements separately with the original authored expectations intact.

## Mechanical metadata correction (2026-11-08)

The initial checklist hash used the raw parsed JSON (`565a25b11eff9568fc831ad4d682c65db93429eb8d2417116d2bb99064529cf6`).
The benchmark hashes `ChecklistVersion.model_validate(raw).model_dump(mode="json")`
(`9264b4d2099685078769691b7f72ef1fcb1befe19ec6c9ad94e69a6531cedfaf`; see `evaluation/benchmark.py`, `make_plan`).
Only `expected.json.checklist_sha256` was corrected to that convention. Validation
confirmed every explicitly supplied checklist value is unchanged; normalization
only adds declared schema defaults (and JSON-equivalent numeric representation).
The dataset bytes and all 24 expected decisions, rationales, and case metadata
remain unchanged. No prediction decisions were read for this correction.
Expected-items canonical SHA256: `b644f7af44b1115645e786f31a46963c7f66f0a44dfe2530667439dcf5dfe297`.
Dataset byte SHA256: `e612c48ceb807001970b5eb8eeaea74ec7075c70a058e5f76bbbdb0dce59baa3`.
