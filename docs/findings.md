# Findings: Pipeline False Accepts

> Development log from October 2026: diagnostic notes from investigating false
> accepts. The text of the findings is kept as it was written at the time. The
> table below shows what is still true after the final evaluation (November
> 2026) and where each finding appears in the thesis.

## Status (November 2026)

**Report follow-up (8 November 2026):** the highlighted implementation passages have been rewritten to describe the fixes, keeping their Finding 7–12 colours for wording review. Chapters 7–9 contain temporary “Review after rerun” pointers. Numerical results and plots remain historical until the fresh batch is reviewed. The detailed follow-up checklist is [post_fix_report_review.md](thesis_review/post_fix_report_review.md). Original UML sources and bibliography entries were not changed.

| # | Finding | Status now | Relevant for the thesis? | Where in the thesis |
|---|---|---|---|---|
| 1 | False accepts via `passthrough_fallback` (`show` accepts any term as an EHR record) and the unused confidence value | **Open.** No fix except the few-shot examples of Finding 6. Out-of-domain commands that look like a data request are accepted if the language model maps them to `show`. | Yes, as a limit | Section 5.8 (only the language model decides whether a command belongs to the domain); Chapter 7 (Llama 3.3 accepts "Look into whatever that is") |
| 2 | NCIt synonym collision: "hemolysis" is a synonym of the laboratory term "Hemolytic Index" | **Open, on purpose.** A dataset-specific exclusion was tried and removed (`evaluation/evaluation.md`, section 14). It is the reason why "start hemolysis" and "stop hemolysis" are accepted. The wrong reference `DOID:3680` is still in `Final_Pipeline_Complete.md` (an old document). | Yes | Chapter 7: entity resolution (name collisions) and the table of type-mismatch commands |
| 3 | Unreadable ANTLR messages; `set medication hypertension` tests the structure check, not the type check | **Resolved.** Stage 6 builds a readable message from `structure_hint` in `domain_config.yaml` (`_structure_clarification`). | Yes, as the description of Stage 6 | Section 5.3 (Stage 6); Chapter 7: type-mismatch table |
| 4 | Manual re-test: the confidence value is not reproducible; `send_message` has the same free-text flaw as `set_note` | **Confirmed again.** A second complete run gave other answers at temperature 0 (accuracy within 1.2 points). The free-text flaw is open. | Yes, for reproducibility | Section 6.1 (three repetitions); Chapter 7: paragraph on the second run |
| 5 | Fresh full run: the confidence gate has no separation; two kinds of language-model failure; a crash of `--save` | **Crash fixed.** Confidence is shown in the explanation and is not used for any decision. | Yes | Section 5.8; Chapter 9 (use the confidence value) |
| 6 | Few-shot examples as a fix; 11 of 18 old examples were verbatim commands of the evaluation set | **Done.** Four examples added and eleven reworded. No example is a command of the dataset (substring check). | Yes, for the integrity of the evaluation | Comment in `domain_config.yaml`; Chapter 8 (threats to validity) |
| 7 | Stage 7 treats clarification requests as confirmable commands | **Fixed on 8 November 2026** (code; report wording updated in colour for review). Only `status=accepted` enters confirmation; `needs_clarification` returns `clarification_requested`, shows the message, asks for a corrected command, and the session goes back to listening. The trigger is removed before a reply is classified. Tests: `tests/test_stage7_clarification.py`. Was: reproduced on 6 November 2026. | Yes, as the corrected interaction | Sections 5.3, 5.7, 5.8, 6.3.5 and 6.3.6 now describe accepted-only confirmation in the Finding 7 colour. |
| 8 | Missing trigger phrase does not prevent command processing | **Fixed on 8 November 2026** (code; report wording updated in colour for review). A new command without a trigger returns status `inactive` before any processing. The benchmark prepends the trigger to the pipeline input, as it already did for B0. Tests: `tests/test_trigger_gate.py`. Was: confirmed on 7 November 2026. | Yes | Section 6.3.1 and Figure 6.2 now show inactive input; Section 7.1 describes trigger preparation. Fresh results await review. |
| 9 | Stage 2 lacks explicit extraction-schema validation | **Fixed on 8 November 2026** (code; report wording updated in colour for review). Strict Pydantic schema in Stage 2 (`ExtractionSchema`), `error_kind` api/syntax/schema. All 8,465 stored model answers parse identically. Tests: `tests/test_stage2_schema.py`. Was: confirmed on 7 November 2026. | Yes | Sections 6.3.2 and 5.8; purple Finding 9 review markers. |
| 10 | Unfinished notes/messages have no completion reminder or persistent continuation state | **Fixed on 8 November 2026** (code; report wording updated in colour for review). Session state in `src/interaction.py`: open dictation across turns, reminder after 5 s of inactivity (configurable), finish/continue/cancel replies, normal confirmation after finishing. Tests: `tests/test_interaction.py`. Was: confirmed on 7 November 2026. | Yes | Sections 5.3, 5.8, and 6.3.5; green Finding 10 review markers. |
| 11 | Compound medications can be accepted with unresolved drug segments | **Fixed on 8 November 2026** (code; report wording updated in colour for review). A compound medication is accepted only if every segment is found with an allowed type; otherwise clarification naming the failing segments. Tests: `tests/test_compound_medication.py`. Was: reproduced on 7 November 2026. | Yes | Sections 5.3, 5.8, 6.3.4, and 6.3.5; red Finding 11 markers. |
| 12 | Action and entity metric labels need clearer, distinct terminology | **Done on 8 November 2026 in the reporting code** (report metric wording updated in colour; plots await fresh results). Display names in `evaluation/scripts/metric_labels.py`; JSON keys and scores unchanged; denominators counted from the labels. Tests: `tests/test_metric_labels.py`. Historical reports were not regenerated. | Yes | Sections 7.1 and 7.3 and the extraction figure; grey Finding 12 markers. |
| – | "Candidate fixes": confidence redefinition, self-consistency vote, ontology grounding of the free-text fallback | **Not pursued.** The thesis makes no dataset-specific fixes. | As future work | Chapter 9 |

---

# Finding 1: False accepts via `passthrough_fallback` / unused confidence signal

## The triggering case

```
ID: c049
Text: "Proceed with the thing and do the usual check"
Category: invalid_ambiguous (valid: false, expected_action: null)
```

Pipeline result: **accepted** as `show` → `ehr_record`, when it should have
been rejected as ambiguous/non-clinical.

```json
{
  "status": "accepted",
  "intent": "SHOW_EHR_RECORD",
  "action": "show",
  "slots": {
    "ehr_record": {
      "raw_entity": "the thing",
      "canonical": "the thing",
      "semantic_type": "ehr_record",
      "ontology_id": null,
      "match_method": "passthrough_fallback",
      "score": 1.0,
      "source": null
    }
  }
}
```

Stage 2 (LLM) extracted `action=show`, `entity="the thing"`, `confidence=0.8`.

---

## Root cause chain

**1. The LLM has an explicit rule that should have caught this — but didn't apply it.**

`domain_config.yaml`'s `domain_rules` (injected into `llm_system_prompt.txt`)
already say:

> *"This is a MEDICAL clinical interface. If the command is clearly unrelated
> to clinical work (e.g. home appliances, vehicles, vague commands with no
> medical content), return action=UNKNOWN."*

and, specifically for `show`:

> *"If the requested data has no plausible clinical meaning (e.g. 'show
> movie', 'show my emails'), return UNKNOWN."*

There's even a near-identical few-shot example baked into the prompt:
`"get the stuff for patient"` → `{"action":"UNKNOWN", "confidence":0.1}`.

So this is not a missing rule — it's an **LLM instruction-following gap**.
The model generalized "do the usual check" loosely toward `show` instead of
recognizing it as content-free filler, and only gave it `confidence: 0.8`
(vs. 0.95–0.99 on clear commands) — the model itself was hedging, but nothing
downstream reads that signal.

**2. `show`'s ontology fallback provides zero real backstop.**

`stage4_ontology_lookup.py:534-539`:

```python
passthrough_fallback = action_info.get("passthrough_fallback_type")
if passthrough_fallback:
    return OntologyResult(
        True, raw_entity, raw_entity, passthrough_fallback,
        None, "passthrough_fallback", 1.0, None,
    )
```

`show` is the only action with `passthrough_fallback_type` set (`ehr_record`
in `domain_config.yaml`). This is a deliberate design choice — EHR records
(discharge letters, lab reports, imaging, etc.) have no closed ontology like
diseases or drugs do, so anything that isn't a recognized vital sign is
assumed to be "some other kind of record." But the assumption has **no
verification behind it**: `ontology_id=None`, `source=None`, and a hardcoded
`score=1.0` regardless of what the entity actually was. `"the thing"` and
`"discharge letter"` are treated identically.

**3. `confidence` is computed everywhere and used nowhere.**

Grepped across `stage1_preprocess.py`, `stage2_llm_extract.py`,
`run_pipeline.py`: `confidence` is captured by Stage 2, stored in results,
and printed — but never gates any accept/reject decision at any stage.
Likewise `trigger_found` (checked and ruled out as a possible signal — see
below).

**Ruled out**: `trigger_found=False` (no "ok glasses" in the input). Checked
across the full annotated dataset: 130 valid *and* 108 invalid commands lack
the trigger phrase, so its presence/absence carries no signal in this test
set (by design — it simulates already-triggered dictation).

---

## Evidence this isn't a one-off

Pulled `results/pipeline_results_full.json` (a full saved evaluation run).
8 documented false accepts total; 3 share the *exact* mechanism as c049 —
LLM maps vague/off-domain text to `show`, Stage 4 hits the unconditional
`passthrough_fallback`:

| id | text | action | confidence | mechanism |
|---|---|---|---|---|
| c064 | "Look into whatever that is" | show | 0.6 | passthrough_fallback → ehr_record |
| c078 | "Review the new software update" | show | 0.8 | passthrough_fallback → ehr_record |
| c049 | "Proceed with the thing and do the usual check" | show | 0.8 | passthrough_fallback → ehr_record |

The other 5 false accepts have different mechanisms (worth a separate look
later, not covered by the analysis below):

| id | text | action | mechanism |
|---|---|---|---|
| c050 | "Proceed with the medical" | start | `entity_not_found` accepted as understood-but-ungrounded |
| c051 | "Continue with the thing for patient Bruce Wayne" | continue | system command, entity discarded, `skipped` |
| c070 | "Finish up with the usual, maybe" | set_note | free-text `passthrough` — any text is valid free text by design |
| ext_type_02 / ext_type_05 | "start/stop hemolysis" | start/stop | genuine ontology synonym match (`"hemolysis"` matches an NCIt procedure synonym — a Stage 4 *data* gap, unrelated to confidence; see Finding 2 below) |
| ext_new_inv_02 | "Call Dr. Patel" | send_message | free-text `passthrough`, confidence 0.9 |

### Confidence distribution for `show` + `passthrough_fallback` specifically

Filtering the saved run to `action=show` and `ont_match_method=passthrough_fallback`:

- **Invalid** (should be rejected): confidence **0.6, 0.8**
- **Valid** (correctly accepted): confidence **0.95 – 0.98**, no exceptions

Clean separation, no overlap, in this dataset. A confidence gate scoped
*narrowly* to `match_method == "passthrough_fallback"` with a cutoff around
**0.9** would catch all three known false accepts of this type with zero
regressions on currently-valid `show` commands.

This does **not** generalize cleanly to the other `passthrough` (non-fallback)
cases — e.g. `send_message`: invalid "Call Dr. Patel" sits at confidence 0.9,
valid "Start a new message: Document the change..." sits at 0.92. Too close
to separate with one global threshold; would need a per-action or
per-match-method cutoff, or a different signal entirely.

> **⚠ Update — see Finding 4.** A later manual re-test of `c078` on a fresh
> LLM sampling run got `confidence: 0.95` for the *exact same text* that
> scored `0.8` here. The "clean separation at 0.9" conclusion above does not
> hold up — confidence is not stable across runs for identical input, so a
> plain threshold gate is weaker than this section originally concluded.
> Don't implement a fix based on this section alone; read Finding 4 first.

---

## Implementation risk notes (for whenever we do this)

From a codebase check of every place that touches `Stage5Result`/`s5_status`:

- **No test suite exists** for `stage5_type_validate.py`, `stage6_intent_output.py`,
  or `run_pipeline.py` (checked project-wide, excluding `.venv`).
- `Stage5Result(...)` is constructed **positionally in 7 places**
  (`stage5_type_validate.py` ×6, `run_pipeline.py` ×1) — adding a `confidence`
  field to the dataclass without a default touches all 7.
- The only call site of `stage5_type_validate.validate()` is
  `run_pipeline.py:98` (`stage5 = type_validate(stage2.action, stage4)`) —
  would need `stage2.confidence` threaded in.
- **Fallthrough risk**: `run_pipeline.py:129`
  (`stage5.status in ("accepted", "skipped", "entity_not_found")`) and
  `stage6_intent_output.py:217` (`if stage5.status != "skipped":`) are both
  permissive checks with **no catch-all `else`**. A brand-new status string
  (e.g. `"low_confidence_passthrough"`) would silently fall through into the
  *accepted* path in both places rather than triggering clarification —
  exactly backwards from the intent. Any fix needs these two call sites
  updated explicitly, not just a new status value added and left to "just work."

---

## Open question for later

Where should the gate live?
- **Stage 5** (`stage5_type_validate.py`) — architecturally the "semantic
  safety layer," but requires threading `confidence` through its signature
  and touching all 7 `Stage5Result` construction sites.
- **Stage 6** (`stage6_intent_output.py`) — already receives both
  `stage2.confidence` and `stage4.match_method`, so could downgrade an
  `accepted` Stage 5 result to `needs_clarification` without changing
  Stage 5's contract at all. Less invasive, but muddies Stage 5's stated
  role as "the key safety layer."

Not decided yet — revisit when we're ready to implement.

---

# Finding 2: NCIt "hemolysis" synonym collision — breaks the thesis's own flagship example

This is a separate, unrelated root cause from Finding 1 (not a confidence
problem — it's an ontology *data* gap plus a documentation error). Found
while sanity-checking whether "hemolysis" is a disease or a procedure.

## The documentation error

`Final_Pipeline_Complete.md`'s worked example for the supervisor's flagship
use case claims:

```
"hemolysis" → DOID:3680 → "Hemolysis" → semantic_type = "disease"
```

Checked `DOID:3680` against the actual `ontologies/doid.obo` file the
pipeline loads:

```
id: DOID:3680
name: obsolete malignant neoplasm of lip, oral cavity and pharynx
```

**`DOID:3680` is an obsolete oral cancer term, not hemolysis.** The concept
ID in the docs does not correspond to anything hemolysis-related in the real
Disease Ontology file. This ID appears to have been fabricated/guessed when
writing the worked example rather than looked up from the actual file.

## The underlying data gap

Grepped `doid.obo` and `hp.obo` directly: **bare "hemolysis" does not exist
as a term name or synonym in either file.**

- DOID only has compound disease names: *"congenital nonspherocytic
  hemolytic anemia 1/3/4/5/8"*, etc.
- HP only has compound phenotype names: *"Hemolytic anemia" (HP:0001878)*,
  *"Exercise-induced hemolysis"*, *"Cold-induced hemolysis"* — the bare word
  itself is never a standalone term or synonym.

Meanwhile `Thesaurus.txt` (NCIt) has:

```
C111208	Hemolytic Index|HEMOLYSI|Hemolysis	...	Laboratory Procedure
```

"Hemolysis" is an **exact synonym** for NCIt concept `C111208`
("Hemolytic Index" — a lab test measuring RBC breakdown in a blood sample).
That's a legitimate clinical term in its own right, just a different sense
of the word than "the patient has a hemolytic disorder."

## Consequence: the thesis's own "correct" branch doesn't work as documented

The supervisor's test case has two branches:
1. `"diagnosis hemodialysis"` (ASR error) → should be REJECTED — this part
   genuinely works, per the earlier investigation.
2. `"diagnosis hemolysis"` (correct input) → docs claim ACCEPTED via
   `DOID:3680` — **this does not work**, because bare "hemolysis" isn't in
   DOID/HP at all. It would hit the exact same cross-type NCIt match as
   branch 1 and get flagged `type_mismatch`/`needs_clarification` instead of
   cleanly accepting.

## Concrete repro: `start hemolysis` (dataset id `ext_type_02`)

```bash
python src/run_pipeline.py --text "start hemolysis" --verbose
```

Ground truth (`data/annotated_commands.json`):
```json
{
  "id": "ext_type_02",
  "category": "type_mismatch",
  "valid": false,
  "expected_failure_mode": "type_mismatch",
  "note": "'start' expects [treatment, procedure, medication]. 'hemolysis' is a disease/condition, not a treatment. Should trigger NEEDS_CLARIFICATION."
}
```

Actual pipeline output: **`accepted`**, `intent: START_PROCEDURE`, matched
`canonical: "Hemolytic Index"`, `ontology_id: NCIt:C111208`,
`match_method: synonym`, `semantic_type: procedure`. Because `procedure` is
in `start`'s `allowed_slot_types`, Stage 5 passes it — but only because
Stage 4 coincidentally resolved "hemolysis" to the unrelated NCIt lab-test
synonym, not because the system understood the command correctly. It
accepted for the wrong reason. This is the same `ext_type_02` row already
counted among the 8 documented false accepts in
`results/pipeline_results_full.json`.

**Caveat on `--text` mode's `✓ CORRECT` label**: running via `--text` prints
`✓ CORRECT` for this case, but that's misleading — `--text` builds a
throwaway `cmd` dict with `"valid": None` (it doesn't look up the real
dataset annotation), so `run_command()`'s scoring falls into its
"assume this is a valid command, did anything reject it?" branch and reports
success whenever nothing rejected the input. It does **not** reflect the
actual ground-truth label. Running the same text through
`python src/run_pipeline.py --extended` (which does load real annotations)
correctly scores this as a false accept. Don't trust `✓/✗` from `--text`
mode for any command that already exists in `data/annotated_commands.json`
with `valid: false` — only use it to *observe* pipeline behavior, not to
score correctness.

## Live confirmation (via Stage 7 testing)

Ran `"diagnosis hemolysis"` through the real, now-implemented 7-stage pipeline
via `--interactive`. Confirmed the prediction above directly: it does **not**
cleanly accept. Stage 4 fails to match "hemolysis" in `doid`/`hp`, finds it
via global clarification search as NCIt `Hemolytic Index` (procedure), and
Stage 5/6/7 correctly flag it as `needs_clarification` with the Stage 7
prompt *"I heard 'hemolysis' and recognised it as a procedure, not a
disease/symptom/condition/finding..."* — i.e. **both branches of the
supervisor's flagship test case now produce the same (for one of them,
wrong) outcome**, live, not just as a predicted consequence.

## Open items

- Fix or remove the fabricated `DOID:3680` reference in
  `Final_Pipeline_Complete.md`'s worked example.
- Decide whether to re-run/re-verify the hemolysis/hemodialysis flagship
  example against the real ontology files before citing exact outcomes in
  the thesis text.
- The NCIt synonym collision itself (`"hemolysis"` → `Hemolytic Index`) is a
  Stage 4 ontology-data ambiguity, not obviously fixable without either
  excluding certain NCIt lab-test synonyms from the procedure index, or
  scoping cross-type matches more carefully — needs its own design
  discussion, separate from Finding 1's confidence-gate fix.

---

# Finding 3: `set_medication`/"hypertension" tests the wrong stage, and Stage 3's error messages are unreadable

## Right verdict, wrong reason

```bash
python src/run_pipeline.py --text "set medication hypertension" --verbose
```

Dataset ground truth (`ext_type_04`):
```json
{
  "category": "type_mismatch",
  "expected_failure_mode": "type_mismatch",
  "note": "'set_medication' expects [medication]; 'hypertension' is a disease. Doctor likely confused action — should clarify."
}
```

This test was written to demonstrate **Stage 5's semantic check** (hypertension
resolves as a disease, not a medication → `needs_clarification`). But the
grammar rule `medication: medicine INT unit | INT unit OF medicine` requires
a numeric dose and unit, which "hypertension" also lacks — so **Stage 3
rejects it first**, and the command never reaches Stage 4/5 at all. The
overall accept/reject verdict (reject) is correct and already documented in
`Final_Pipeline_Complete.md`'s eval results ("Stage 3 failures... e.g.
`hypertension` (not a drug)"), but this specific test case doesn't exercise
what it was designed to exercise. A clean version of this test would need
dose+unit attached (e.g. `"set medication hypertension 10 mg"`) so it clears
Stage 3 and only fails at Stage 5.

## The ANTLR error message is not human-readable

Actual output for the case above:
```
PARSE_ERROR: col 56: no viable alternative at input 'setmedicationhypertensionfinishmedication'
```

`stage3_antlr_validate.py:157` passes ANTLR's raw default diagnostic text
straight through with zero translation:
```python
reason = "PARSE_ERROR: " + "; ".join(listener.errors)
```
The garbled, space-free concatenation is just ANTLR's default rendering of
"remaining unconsumed input" for a `NoViableAltException` — harmless
internally, but it says nothing about *why* (no mention of "dose" or "unit"
anywhere). It flows unprocessed into Stage 6's `explanation.grammar_validation`
field, so if this were ever shown to a developer reading eval output, or
worse a clinician via Stage 7, it would be actively unhelpful.

## Open items

- ~~Add a translation layer... to human-readable reasons~~ **Done** — not in
  `stage3_antlr_validate.py` itself (the raw ANTLR message is still kept
  there, for debugging), but in `stage6_intent_output.py`'s new
  `_structure_clarification()`: for `invalid_structure` rejections where the
  action/entity were understood, builds a `clarification_message` from the
  action's new `structure_hint` field in `domain_config.yaml` (added for
  `set_medication` and `send_message` so far — config-driven, not
  hardcoded per action in code). Deliberately does **not** route through
  Stage 7's confirm/reject dialogue — discussed and decided this needs an
  informative message, not a yes/no prompt, since nothing concrete was
  resolved to confirm or deny. See `Final_Pipeline_Complete.md`'s Example 4
  for the worked example.
- Consider whether `ext_type_04` (and similar dual-failure-mode dataset
  entries) should be revised to isolate the semantic check they're meant to
  test, or whether it's fine to leave them ambiguous and document the
  Stage-3-fires-first behavior as expected.

---

# Finding 4: Manual re-test of the 8 candidate false accepts — 4 confirmed, `send_message` shares the free-text flaw, and results are non-reproducible run-to-run

Manually re-ran the earlier candidate list (`Finding 1`'s c049/c064/c078 plus
five more ambiguous/off-domain commands) via `--text --verbose` against a
fresh LLM sampling pass. Checked each against real dataset ground truth
(all 8 are `valid: false`, `expected_action: null`).

| # | Command | id | Outcome | Verdict |
|---|---|---|---|---|
| 1 | "Proceed with the thing and do the usual check" | c049 | accepted → show/ehr_record | **false accept** |
| 2 | "Look into whatever that is" | c064 | accepted → show/ehr_record, conf 0.5 | **false accept** |
| 3 | "Review the new software update" | c078 | accepted → show/ehr_record, conf **0.95** | **false accept** |
| 4 | "Get that thing" | c045 | UNKNOWN, rejected | correct (mislabeled `✗ WRONG` by `--text`) |
| 5 | "Do the needful for patient Peter Parker" | c052 | UNKNOWN, rejected | correct (mislabeled `✗ WRONG` by `--text`) |
| 6 | "Work on it, you know what I mean" | c041 | UNKNOWN, rejected | correct (mislabeled `✗ WRONG` by `--text`) |
| 7 | "Finish up with the usual, maybe" | c070 | accepted → set_note/free_text | **false accept** |
| 8 | "Call Dr. Patel" | ext_new_inv_02 | rejected via Stage 3 PARSE_ERROR (empty message body) | correct, but by luck — see below |

## New issue 1: confidence is not reproducible for identical input

`c078` ("Review the new software update" — flatly non-medical, category
`invalid_out_of_domain`, not even ambiguous) scored `confidence: 0.95` here,
vs. `0.8` in the `Finding 1` saved run for the exact same text. This directly
contradicts Finding 1's "clean separation at a 0.9 cutoff" conclusion — see
the update note added there. The takeaway isn't just "confidence is noisy
in absolute terms," it's that **it isn't even stable for the same input
across runs**, which undermines using it as a hard gate at all.

## New issue 2: `send_message` has the same unconditional free-text flaw as `set_note`, and this exact test case flips outcome between runs

`ext_new_inv_02` ("Call Dr. Patel") is already a documented false accept in
`results/pipeline_results_full.json` — there, the LLM extracted a non-empty
entity and `send_message`'s free-text passthrough accepted it outright. In
this run, the LLM extracted an **empty** entity for the message body, so
Stage 3's grammar (which requires content between `START MESSAGE` and
`FINISH MESSAGE`) rejected it — correctly, but purely by luck of what Stage 2
happened to put in `entity`, not because the pipeline recognized "there is
no `call` action" (the dataset's own note: *"Medical but no matching action
(no 'call' action). Tests LLM domain boundary."*). Had the doctor said "Call
Dr. Patel and tell him the patient is stable," that non-empty entity would
sail through `send_message`'s passthrough exactly like `c070` does for
`set_note` — nothing checks whether free-text content is actually a
legitimate message vs. some other action being misclassified as
`send_message`.

**Implication**: the same identical test case (`ext_new_inv_02`) scores as
pass or fail depending on LLM sampling variance alone. This is a
reproducibility concern for the evaluation methodology as a whole, not just
a one-off command issue — reported accuracy numbers (e.g. the 95.9%/98.5%/
92.9% headline figures) may shift somewhat between runs for reasons that
have nothing to do with the pipeline logic changing.

## New issue 3: `--text` mode's `✗ WRONG` mislabeling, confirmed again

3 of 8 in this batch (`c045`, `c052`, `c041`) were correctly rejected but
printed `✗ WRONG` — the same `--text` scoring artifact documented in
Finding 2 (it assumes every ad-hoc input is a "should be valid" command, so
any rejection prints `WRONG` regardless of the real dataset label). Fresh
concrete confirmation; no new mechanism here.

---

# Finding 5: Fresh full eval run — confidence gate definitively dead, two distinct LLM failure subtypes, plus a `--save` crash fix

Ran `python src/run_pipeline.py --save` (full 243-command set, same model,
`meta-llama/Llama-3.3-70B-Instruct`). This surfaced a real bug and produced
the most decisive evidence yet against using `confidence` as a gate.

## Bug fixed: `--save` crashed on compound medication results

```
TypeError: Object of type DrugResult is not JSON serializable
```

`run_pipeline.py`'s `--save` step calls `json.dumps()` on the full results
list, which includes `ont_drug_list` — a list of `DrugResult` dataclass
instances (from `stage4_ontology_lookup.py`, populated whenever a
compound `set_medication` command is looked up). Nothing converted these to
plain dicts before serialization, so any run containing a compound
medication command crashed *after* printing all console output but *before*
`out.write_text()` ran — meaning the results file was silently not written
even though the run appeared to complete. Fixed in `run_pipeline.py`'s
`--save` block with a `default=` handler that converts any dataclass to a
dict via `dataclasses.asdict()`. Verified in isolation before the next full
run picked it up successfully.

## Confidence gate: now zero separation (not just overlap)

Full breakdown of `show` + `passthrough_fallback` rows, sorted by confidence:

```
c064  invalid  conf=0.50  'Look into whatever that is'
c078  invalid  conf=0.95  'Review the new software update'
c081  invalid  conf=0.95  'Evaluate the efficiency of solar panels...'
c092  invalid  conf=0.95  'Draft a proposal for urban green spaces'
c141  valid    conf=0.95  'show age'
c188  valid    conf=0.95  'Show me the messages'
c146  valid    conf=0.96  'Display diagnostic information'
...
```

Three of four invalid rows sit at **exactly** the same confidence (0.95) as
valid rows immediately below them. Same story for the `turn_on` family:

```
c018  invalid  conf=0.80  'Is this thing on?'
c029  invalid  conf=0.80  'Blue over green'
c103  valid*   conf=0.80  'Freeze frame'   (*wrong-action, but binary-correct)
```

There is no threshold that separates these. This isn't "noisy signal around
a real boundary" — it's coincidence. **Retract the confidence-threshold idea
from Finding 1 entirely; it does not work at any cutoff, on this dataset, on
two independent runs.**

## Two distinct LLM failure subtypes — refines the candidate fixes

The new false accepts split into two mechanisms that look identical in
outcome but need different fixes:

| Subtype | Example | Confidence | What's actually wrong |
|---|---|---|---|
| (a) Vague/referential, no concrete noun | "whatever that is", "the thing" | Low (0.5–0.8) | Content-free filler |
| (b) Well-formed, content-rich, but topically off-domain | "solar panels", "urban green spaces", "software update" | **High (0.95)** | Real sentence structure, wrong domain |

Subtype (b) is now the *majority* of `show` false accepts in this run (3 of
4). A wordlist-based filter (a vague-word/pronoun stoplist, or a
"vagueness ratio" pre-filter) would only ever catch subtype (a) — "Evaluate
the efficiency of solar panels in low-light conditions" contains no vague
words at all; it's a perfectly well-formed sentence about a real
(non-medical) topic. This is one reason (beyond the "no hardcoded lists"
steer — see the Candidate Fixes section) those approaches were dropped
rather than kept as a partial fix.

## Checked: is there already a rule tying confidence to domain-relevance? No.

Went back to `llm_system_prompt.txt` and `domain_config.yaml`'s
`domain_rules` specifically looking for this. Found only a **hard binary**
instruction:

> *"This is a MEDICAL clinical interface. If the command is clearly
> unrelated to clinical work... return `action=UNKNOWN`."*

The `confidence` field itself is defined generically in the prompt schema:
```
"confidence":  <0.0 to 1.0, your confidence that this mapping is correct>
```
"Confidence that this mapping is correct" asks *"is `show` the right action
label,"* not *"is this clinically relevant at all."* Nothing in the prompt
asks the model to fold domain-relevance uncertainty into the confidence
score — it's an all-or-nothing UNKNOWN decision with no graduated fallback.
This is exactly why confidence can't discriminate subtype (b): when the LLM
wrongly decides "solar panels" is show-able, it's fully confident about the
*shape* of the mapping (yes, "evaluate X" reads like a display request) —
it never separately judged domain fit, because nothing asked it to.

## Other findings from this run (not confidence-related)

- **`c172`** — *"Activate drug regimen for Mr. Johnson: Start..."* →
  misclassified as `start` instead of `set_medication` (wrong action, but
  binary-accepted). This is more than a scoring miss: `set_medication`'s
  grammar enforces `medicine INT unit` (Stage 3 rejects missing dose/unit);
  `start` has no such requirement. A real medication order phrased this way
  **bypasses the one structural safety check specifically built for
  medication commands.** Worth prioritizing over the other "wrong action"
  cases.
- **`c105`** — *"Stop now frame"*, a documented valid baseline command
  (`valid_u01_u04`), fails at **Stage 3** (`PARSE_ERROR: extraneous input
  'frame' expecting <EOF>`), not Stage 2 — Stage 2 extracted it correctly
  (`action=stop, entity=frame`). The grammar itself rejects a legitimate
  baseline phrasing, likely a token/rule conflict inherited from the Ludyga
  grammar around "frame." Separate from the LLM-side issues above; needs a
  grammar-level look, not a prompt or confidence fix.
- Other wrong-action confusions this run: `c103` ("Freeze frame" →
  `turn_off`, expected `stop`), `c184` ("Switch to messages" → `turn_on`,
  expected `show`), `c194` ("Start a new message..." → `set_note`, expected
  `send_message`) — all action-boundary confusions between semantically
  adjacent actions, lower priority than `c172`/`c105`.
- Overall stats this run: 95.1% binary / 97.7% valid recall / 92.0% invalid
  precision — a third distinct set of numbers across three runs now (95.9%/
  98.5%/92.9% originally, 94.7%/97.7%/91.2% from the console-only run, this
  one 95.1%/97.7%/92.0%). Further confirms Finding 4's non-reproducibility
  point; don't treat any single run's headline numbers as exact.

---

# Candidate fixes (discussed, none implemented yet)

> **Rewritten after Finding 5 and a direct steer from the user**: no
> hardcoded wordlists/stoplists as fixes — inconsistent with this codebase's
> own "nothing hardcoded, everything routes through config" design
> (`domain_config.yaml` as single source of truth). The earlier versions of
> this section proposed a vague-word blacklist, a vagueness-ratio filter,
> and an EHR-keyword list; all three are removed. What's left below either
> strengthens the LLM's existing, correct domain-relevance rule, or reuses
> config-driven data that's already loaded for another purpose — nothing
> that requires hand-curating a new list of words.

## Why the existing domain rule isn't a reliable filter today

`domain_rules[0]` already says the right thing: *"If the command is clearly
unrelated to clinical work... return `action=UNKNOWN`."* This is the
correct design — the gap is reliability, not wording. Two concrete reasons
it under-fires, both fixable without hardcoding anything:

1. The only few-shot example demonstrating a `→ UNKNOWN` rejection
   (`"get the stuff for patient"`) is a vague/filler sentence (failure
   subtype (a) from Finding 5). The model has never been shown an example of
   the now-dominant subtype (b): a well-formed, grammatically normal
   sentence about a real but non-medical topic (`"evaluate the efficiency of
   solar panels"`). Without a matching example, it defaults to completing
   the schema with a plausible action instead of admitting UNKNOWN.
2. A natural-language rule is a *preference* the model weighs, not a
   deterministic gate — it competes against the model's general bias toward
   producing a complete, helpful-looking answer. Expecting one sentence of
   instruction to work as a hard filter every time isn't realistic for an
   LLM; it needs either more/better examples, or independent verification.

## Candidate fixes

1. **Add few-shot examples covering subtype (b), not just subtype (a).**
   `domain_config.yaml`'s existing `few_shot_examples` list already has one
   `→ UNKNOWN` example; add 2–3 more using real subtype-(b) phrasing (e.g.
   something shaped like `"evaluate the efficiency of X"` for a clearly
   non-medical `X`) so the model has a matching pattern to generalize from.
   This uses the config's existing mechanism for teaching domain rules — no
   new hardcoded classifier, no wordlist. Cheapest to try; effect size
   unproven without a fresh eval run.
2. **Redefine what `confidence` measures, via an explicit rule.** Per
   Finding 5's rule-check, `confidence` currently only means "is this action
   mapping correct," never "is this clinically relevant at all." Add one
   instruction to `domain_rules` telling the model to fold domain-relevance
   uncertainty into the score — e.g. lower confidence substantially whenever
   it isn't sure the command belongs in a clinical interface, even if it
   still emits an action. One prompt-rule addition, no lists. Untested —
   needs a fresh eval run to check whether the model actually follows it,
   given the instruction-following gaps already documented.
3. **Self-consistency / majority vote for borderline outputs.** When Stage 2
   lands on an action whose Stage 4 result carries no real grounding
   (`passthrough_fallback`, or a null-ontology-index/`skipped` action), fire
   a second LLM call and only accept if both agree. This treats "the LLM
   might be wrong" as the actual problem instead of trying to catch specific
   phrasings, so it isn't limited to either failure subtype and needs no
   curated content at all. Costs 2x latency, but only on inputs that hit
   these low-grounding paths. The most rigorous option on this list.
4. **Reuse already-loaded ontology data as a loose grounding check** (only
   if (1)-(3) don't move the needle enough). Before letting `show`'s
   `ehr_record` fallback — or any other null-index passthrough — fire,
   check whether the raw entity has *any* fuzzy overlap with terms already
   indexed from DOID/HP/NCIt/RxNorm/`vital_signs.json`, which are already
   loaded in Stage 4 for other indices. This isn't a new hardcoded list —
   it's reusing config-driven data the pipeline already has for a different
   purpose. Risk: those ontologies cover diseases/procedures/drugs/vitals,
   not administrative record types (discharge letters, correspondence), so
   it may need a low cutoff and won't be a complete signal on its own.

**Priority order**: (1) and (2) first — both are one-line prompt/config
changes, directly target the root cause (missing examples, confidence's
narrow definition), and need a fresh eval run to check effect size before
doing anything more invasive. (3) is the fallback if prompt-side changes
aren't enough — it doesn't depend on LLM instruction-following at all. (4)
is a last-resort structural layer, not a first choice, given its partial
coverage of record types outside the loaded ontologies.

Separately, still open from Finding 5 and not related to this cluster:
`c172` (medication order bypassing the dose/unit safety check) and `c105`
(grammar rejecting a valid baseline command) — both flagged as
higher-priority than the confidence/passthrough issues above.

---

# Finding 6: Applied candidate fix #1 (few-shot examples) — plus a bigger, separate problem it uncovered: train/test contamination in the pre-existing few-shot list

## The fix: 4 new few-shot examples for failure subtype (b)

Added 4 new examples to `domain_config.yaml`'s `few_shot_examples` — well-formed,
professional-sounding sentences about real non-medical topics (finance,
packaging, facilities, budgeting), deliberately reworded from anything in
`data/annotated_commands.json` rather than reusing eval-set text verbatim
(checked programmatically — see below). Spot-tested against the actual
known false accepts from Finding 5 (fair test, since none of these exact
sentences are in the new examples):

| Command | Before | After |
|---|---|---|
| "Review the new software update" | `show`, conf 0.95 | **`UNKNOWN`, conf 0.1** ✓ |
| "Draft a proposal for urban green spaces" | `show`, conf 0.95 | **`UNKNOWN`, conf 0.1** ✓ |
| "Blue over green" | `turn_on`, conf 0.8 | **`UNKNOWN`, conf 0.1** ✓ |
| "Evaluate the efficiency of solar panels..." | `show`, conf 0.95 | unchanged |
| "Is this thing on?" | `turn_on`, conf 0.8 | unchanged |

**3 of 5 flipped to correct.** The two that didn't: "Is this thing on?" is a
different shape (idiomatic vague-referent, subtype (a)) not covered by these
subtype-(b) examples; "solar panels" is structurally close to the added
"packaging design" example yet still didn't transfer — a reminder that even
well-matched few-shot coverage doesn't guarantee generalization. Real,
partial improvement, exactly the "reduces but doesn't eliminate" outcome
predicted for this fix.

## The bigger finding: 11 of the 18 *pre-existing* few-shot examples were verbatim eval-set commands

While adding the new examples, checked the existing list against
`data/annotated_commands.json` programmatically (exact string match, both
directions). Found **11 of 18** existing few-shot inputs were word-for-word
identical to real evaluation commands:

| Few-shot input (before) | Eval id |
|---|---|
| "start emergency" | c121 |
| "begin emergency" | c129 |
| "trigger emergency situation" | c130 |
| "finish emergency" | c122 |
| "stop emergency" | c133 |
| "cancel emergency mode" | c138 |
| "halt animation" | c104 |
| "diagnosis hemodialysis" | **ext_type_01** |
| "set medication hypertension" | **ext_type_04** |
| "show patient history" | ext_ont_02 |
| "get the stuff for patient" | c042 |

The two bolded ones are significant: `ext_type_01` and `ext_type_04` are the
dataset's flagship **type-mismatch** test cases (the hemodialysis/hemolysis
supervisor use case and its medication analogue). The model was being handed
the literal correct Stage 2 extraction for these exact test inputs inside
its own prompt — meaning reported "action accuracy" on those specific items
wasn't testing generalization at all, just recall of a memorized example.
This is real train/test leakage, not a hypothetical concern — ~4.5% of the
243-command eval set (11 commands) had this property before the fix.

## Fixed: reworded all 11, preserving each one's teaching point

Each replacement keeps the underlying lesson (e.g. "start/begin/trigger near
emergency → `emergency` action," "entity preserved verbatim even when it
looks like the wrong semantic type for the action") but uses different
wording, verified via the same substring-collision check against the full
eval set (not just exact-match — a first draft accidentally *embedded* a
different eval command as a substring, e.g. "start the emergency protocol
immediately" contains the separate eval item "start the emergency"
(`c125`); caught and reworded). Final check confirms **zero exact matches**
between any of the 18 few-shot inputs and any of the 243 eval commands.

Notably, `"diagnosis hemodialysis"` and `"set medication hypertension"`
were replaced with `"diagnosis appendectomy"` and `"set medication
migraine"` — different procedure/disease words, same pedagogical point
(the LLM must preserve the entity as spoken without judging whether it's
the "right" semantic type — that's Stage 4/5's job, not Stage 2's).

## Verified: no regression after removing the literal answers

Re-ran the four affected commands after the fix — all still extract
correctly without being handed the exact answer anymore:

```
"diagnosis hemodialysis"       -> diagnosis / hemodialysis / conf 0.97  (unchanged)
"set medication hypertension"  -> set_medication / hypertension / conf 0.50  (was 0.95)
"get the stuff for patient"    -> UNKNOWN / conf 0.1  (unchanged)
"show patient history"         -> show / patient history / conf 0.97  (unchanged)
```

Three are unchanged — genuine generalization from the domain rules and the
remaining analogous examples, not memorization. One notable side effect:
`"set medication hypertension"`'s confidence dropped from the old
memorized-example 0.95 to **0.50** now that the model has to actually reason
about it rather than recall a template. Arguably *more* honest calibration
— that command genuinely is borderline/incomplete-looking (see Finding 3),
so a lower confidence is more appropriate than the old artificially-high
one. Small, unplanned data point relevant to the "redefine what confidence
means" candidate fix.

## Open items

- Keep applying the same verbatim-eval-text check to any future few-shot
  example additions — a substring check (not just exact-match) against
  `data/annotated_commands.json`, since a naive rewrite can accidentally
  re-embed a *different* eval command as a substring.
- Re-run the full eval (`--save`) to see whether removing the memorized
  flagship examples changes `ext_type_01`/`ext_type_04`'s pass/fail status
  now that they're being genuinely generalized to rather than recalled.

---

# Finding 7: Clarification requests enter the confirmation path

**Date:** 6 November 2026  
**Status at the time of the finding:** Open (see Resolution below). Reproduced locally; application code has not been changed.

## Expected behaviour

Only Stage 6 results with `status=accepted` should enter the action's
confirmation policy. A `needs_clarification` result should display the
clarification message and request a corrected command. That command must
pass through the pipeline again before it can be confirmed. Saying “yes”
does not resolve an unknown entity or correct a semantic type mismatch.

## Reproduced behaviour

An isolated Stage 7 check supplied the following payload, using the current
explicit-confirmation policy for `diagnosis` and a scripted “yes” response:

```json
{
  "status": "needs_clarification",
  "action": "diagnosis",
  "clarification_message": "Hemodialysis is a procedure, not a permitted diagnosis type. Please repeat or rephrase."
}
```

Observed output:

```text
Stage 6 status: needs_clarification
Stage 7 outcome: confirmed
Stage 7 message: CONFIRMED — command executed.
```

The original Stage 6 status remains `needs_clarification`; Stage 7 produces
a contradictory confirmation result. This check did not call a language
model, rerun Stages 1–6, or execute a clinical application action. The
execution claim is a returned message, not evidence of application execution.

## Cause

- In `src/stage7_confirmation.py`, `build_prompt()` returns the clarification
  message for `needs_clarification`, rather than treating it as a separate
  interaction from confirmation.
- `run()` treats any non-empty prompt returned by `build_prompt()` as eligible
  for the action's confirmation policy. It does not first require
  `status=accepted`.
- Consequently, a positive reply returns `confirmed`. The same path also
  contains automatic-confirmation branches for actions with timeout policies.
  The local reproduction above tested the explicit “yes” branch only.
- `src/run_pipeline.py::_run_confirmation()` passes the Stage 6 payload to this
  function without a separate guard for clarification results.

## Proposed correction, not yet implemented

1. Restrict confirmation and automatic confirmation to `status=accepted`.
2. Display clarification messages without offering yes/no confirmation, and
   request a corrected command in both interactive and voice modes.
3. Reprocess the corrected command through the pipeline.
4. Replace the prototype's “command executed” message with wording that
   accurately reports confirmation unless application execution is integrated.
5. Verify that clarification results cannot become `confirmed` or
   `auto_confirmed`, including positive replies and timeout policies, while
   accepted commands retain their configured confirmation behaviour.

## Relation to the thesis

Section 5.3 now describes the intended separation between clarification and
confirmation. This finding records the remaining implementation discrepancy;
it has not been resolved by changing the thesis text. The reproduction alone
does not establish an effect on the previously reported evaluation scores.


## Resolution (8 November 2026)

**Status:** Fixed in the code. The historical evidence above is unchanged.

Changes:
- `src/stage7_confirmation.py`: `run()` returns the new outcome
  `clarification_requested` for `needs_clarification` before any confirmation
  policy is applied (no yes/no question, no automatic confirmation for timed or
  zero-timeout actions). `build_prompt()` now builds a prompt only for
  `accepted`. `rejected`, `accepted_partial`, and `inactive` stay
  `not_applicable`.
- Wording: "CONFIRMED — command executed." is replaced by a message that says
  the user confirmed the interpretation and that no clinical application is
  connected, so nothing was executed. The Stage 6 clarification messages no
  longer say "Please repeat or confirm" / "repeat or rephrase"; they ask for
  the corrected command.
- Additional defect found while fixing this (not in the original finding):
  the confirmation vocabulary contains "ok", so a reply "ok glasses" or a new
  command "ok glasses show X" was classified as a yes. `classify_confirmation()`
  now removes the trigger phrase first.
- `src/interaction.py` / `src/run_pipeline.py`: after a clarification the
  session returns to listening (idle). The corrected command is a new command:
  it needs the trigger and runs through the whole pipeline again.

Tests (`tests/test_stage7_clarification.py`, `tests/test_interaction.py`):
clarification with yes / "ok glasses correct" / no / unclear / no reply; under
explicit, timed (5 s), and immediate (0) policies; accepted commands keep the
explicit, timed, and immediate behaviour; rejected, partial, and inactive
results are not confirmable; a corrected command is processed as a new command.

Remaining limitations: no clinical application is integrated, so a confirmed
command is not executed anywhere. The confirmation keywords are a fixed list.

---

# Finding 8: Missing trigger phrase does not prevent command processing

**Date:** 7 November 2026  
**Status at the time of the finding:** Open (see Resolution below). Confirmed through a local preprocessing check and inspection
of the caller; application code has not been changed.

## Intended activation behaviour

The user clarified that the trigger phrase is an activation requirement,
similar to a wake phrase. A recognised “ok glasses” variant should activate
command processing. Without a trigger, input should be ignored and the
system should remain waiting: no intent-extraction call, structural or
semantic validation, or confirmable command should follow.

Minimal local processing to detect activation is still necessary. In the
current voice implementation, audio is transcribed before the textual
trigger is checked. Preventing transcription or remote audio submission
before activation would require an additional audio wake-phrase detector;
a text-level trigger guard alone would not provide this behaviour.

## Confirmed current behaviour

The local preprocessing check produced:

```text
Input:          start HD
trigger_found:  false
clean_text:     start hemodialysis
format_invalid: false
```

In `src/stage1_preprocess.py`, `preprocess()` records whether the trigger was
found and removes it when present. An absent trigger does not stop processing.

In `src/run_pipeline.py`, `run_command()` checks `stage1.format_invalid`
before invoking the model, but does not check `stage1.trigger_found`.
Consequently, this input reaches the Stage 2 extraction call despite having
no activation phrase. The subsequent result depends on the extracted action
and validation results. No remote model call was made for this finding;
the call path was confirmed by inspecting the code.

## Proposed correction, not yet implemented

1. Enforce activation before intent extraction. Missing-trigger input should
   produce an inactive/ignored outcome, rather than a command awaiting
   confirmation or a normal intent-processing result.
2. Ensure the inactive path makes no model call and does not run command
   validation, entity resolution, or confirmation. Place the trigger check
   early enough to avoid unnecessary command preprocessing as well.
3. Apply the requirement consistently to the relevant runtime entry points.
   Define how activation interacts with confirmation replies and any ongoing
   dictation: the current interface accepts replies such as “yes” without a
   trigger, so this behaviour needs an explicit decision rather than an
   accidental change.
4. Verify supported trigger variants, missing and incorrect triggers, and
   text preceding a recognised trigger. Test that missing-trigger inputs
   cannot produce an accepted or confirmed command and do not invoke the model.
5. If activation must precede speech recognition itself, implement and test
   wake-phrase detection separately from the current Whisper transcription path.

## Evaluation and documentation after the fixes

Many existing evaluation commands omit the trigger because they were used
to assess command interpretation rather than activation. Enabling a trigger
guard without accounting for this would change what the evaluation measures.

For the planned rerun after the outstanding issues are fixed:

- Define and record how trigger phrases are supplied for the command-recognition
  comparison, using a consistent preparation policy for the pipeline and baseline.
- Add separate activation cases with and without valid triggers, including
  checks that the model is not called for inactive input.
- Include checks for the clarification/confirmation issue in Finding 7;
  the historical Stages 1–6 evaluation does not test Stage 7.
- Preserve the previous run records and write new results with the updated
  code version, configuration, dataset preparation, and counting rules.
- Update Section 6.3.1, Figure 6.2, execution instructions, and the reported
  evaluation results to reflect the implemented activation policy.

The current thesis description of processing without a trigger records the
existing implementation; it is not the user's intended activation behaviour.
No code fix or evaluation rerun has been performed for this finding.


## Resolution (8 November 2026)

**Status:** Fixed in the code (text-level activation). The historical evidence
above is unchanged.

Runtime policy:
- A NEW command needs a trigger phrase ("ok glasses", "okay glasses",
  "okie glasses", any case, punctuation ignored, text before the trigger
  allowed). `stage1_preprocess.detect_trigger()` runs first in
  `run_pipeline.run_command()`. Without a trigger the result has Stage 6 status
  `inactive` (reason `trigger_not_found`) and nothing else runs: no
  preprocessing, no LLM call, no ANTLR check, no lookup, no type check, no
  confirmation. The result has the same keys as a normal result.
- Replies inside an active interaction need no trigger: Stage 7 confirmation
  replies and the replies/content of an open dictation (`src/interaction.py`).
  A trigger inside such a reply is removed.
- A corrected command after a clarification is a new command and needs the
  trigger.
- Boundary: the check works on the transcript. In `--voice` mode the audio is
  still recorded and transcribed before activation is known; avoiding that
  needs an audio wake-word detector, which is not part of this fix.

Evaluation preparation: the 243 dataset commands contain no trigger (they test
command recognition, not activation). `run_pipeline.with_trigger()` prepends
the configured trigger ("ok glasses ") to the pipeline input; the dataset files
are unchanged. B0 already received the same prefix (`get_json(f"ok glasses
{text}")`) and now uses the same helper. Stage 1 strips the trigger again, so
the cleaned text is identical. In `run_eval.py` an `inactive` result is counted
as `IGNORED`, which is never a correct decision for a valid or an invalid
command.

Offline check: a replay of all 18 first-run folders (4,374 results, stored
Stage 2 answers, no LLM calls, written to a scratch folder) gave 0 inactive
results and 0 changed outcomes. B0 run again: 243 commands, 0 changed outcomes.

Tests (`tests/test_trigger_gate.py`, `tests/test_interaction.py`): variants and
case, missing / wrong trigger, text before the trigger, spies that assert no
preprocessing, LLM, Stage 3-6 calls for inactive input, new command versus
follow-up reply.

Remaining limitations: no audio wake-word detection (see boundary). Activation
is not part of the command-recognition benchmark; it is covered by the tests.

---

# Finding 9: Stage 2 lacks explicit extraction-schema validation

**Date:** 7 November 2026  
**Status at the time of the finding:** Open (see Resolution below). Strict Pydantic validation is proposed, not implemented.

## Current behaviour and cause

`src/stage2_llm_extract.py` parses JSON and constructs `LLMResult` using
`dict.get()` defaults and the conversions `str()`, `bool()`, and `float()`.
An ordinary data class does not enforce its type annotations at runtime.
The `valid_action` property checks configured action membership, but the
response is not validated against a complete extraction schema. Additional
response keys are not checked.

JSON syntax validity therefore does not establish that the required fields,
types, and value constraints are satisfied.

## Offline reproduction

The existing `extract()` function was called with a fake client returning
supplied JSON responses. No remote model or application action was invoked.
Starting from an otherwise valid extraction, each change below was tried
independently:

| Supplied response change | Current Stage 2 result |
|---|---|
| `end_signal` is the string `"false"` | Converted to Boolean `True`, with no error |
| `entity` is JSON `null` | Converted to the string `"None"`, with no error |
| `confidence` is `2.0` | Retained without an error or range check |
| `end_signal` is absent | Defaults to `False`, with no error |

These are constructed test cases confirming parser behaviour, not evidence
that these malformed responses occurred in the reported evaluation. An
invalid confidence currently affects reporting rather than acceptance,
since confidence is not used as a decision threshold. Wrongly converted
entity or completion fields may affect subsequent processing.

## Proposed correction, not yet implemented

Use an explicit response model, for example Pydantic, between JSON parsing
and construction of the pipeline result:

1. Require the agreed response fields, and validate their types before
   converting or defaulting them. Keep empty strings distinct from null or
   absent values.
2. Configure strict validation deliberately: Pydantic also performs coercion
   by default. In particular, require a genuine Boolean for `end_signal` and
   string values for action, entity, and recipient where applicable.
3. Constrain confidence to a finite value in the interval [0, 1].
4. Preserve action membership checks against the configured actions plus
   UNKNOWN. Define required recipient handling for message extractions and
   decide whether additional response fields should be rejected.
5. Record schema-validation failures distinctly, retaining the raw response
   and validation details for inspection. Do not pass malformed extractions
   to structural or semantic validation as if they were valid responses.
6. Test missing fields, nulls, wrong types, invalid actions, out-of-range
   confidence, extra fields, and valid responses. Rerun evaluation after the
   validation policy and other pending fixes are implemented.

Schema validation checks the response representation. It does not establish
that the extracted action or entity matches the user's intent, and it does
not replace ANTLR structural validation, entity grounding, or type checks.

## Report and sources

Section 6.3.2 describes the current parsing and conversions. Section 5.8
records the limitation. Purple Finding 9 markers identify the passages to
revisit after implementation. Pydantic is a proposed improvement, not a
claimed dependency or implemented validation layer. No bibliography entries
have been changed.

- [Pydantic strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/)
- [Pydantic models and validation](https://docs.pydantic.dev/latest/concepts/models/)


## Resolution (8 November 2026)

**Status:** Fixed in the code. The historical evidence above is unchanged.

Changes (`src/stage2_llm_extract.py`):
- `ExtractionSchema` (Pydantic v2, `strict=True`, `extra="forbid"`): required
  `action` (str), `entity` (str, may be empty), `end_signal` (real Boolean),
  `confidence` (finite number in [0, 1]); `recipient` (str) required for
  `send_message`, optional (becomes "") for other actions. The action must be a
  configured action or UNKNOWN. Extra fields are rejected.
- `parse_extraction(raw, model)`: syntax error -> `error_kind="syntax"`; JSON
  that is not an object or breaks the schema -> `error_kind="schema"`, error
  text "schema error: <field>: <message>"; call failure or null content
  (`None`) -> `error_kind="api"`; an empty or whitespace-only string is not
  JSON -> `error_kind="syntax"`. In every failure case the action is UNKNOWN (Stage 6:
  rejected) and `raw_response` is kept. `LLMResult` has a new optional field
  `error_kind`; existing callers are unchanged.
- Dependency: `pydantic==2.11.7` in the new `requirements.txt`.

Offline check: all 8,465 stored model answers of all run folders were parsed
with the new code. Every result is identical to the stored Stage 2 result (16
answers were already JSON syntax errors). The schema therefore changes no
reported number.

Tests (`tests/test_stage2_schema.py`): missing fields, nulls, wrong scalar and
container types, `end_signal="false"`, unknown actions versus UNKNOWN,
negative / >1 / NaN / infinite confidence, extra fields, recipient rules, empty
entity, ordinary and fenced JSON, the three error kinds.

Remaining limitations: the schema checks the form of the answer, not whether
the action or entity is what the user meant.

---

# Finding 10: Unfinished dictation has no completion reminder or continuation state

**Date:** 7 November 2026  
**Status at the time of the finding:** Open (see Resolution below). A completion reminder and pending dictation state are proposed,
not implemented.

## Current behaviour

For a note or message whose extracted `end_signal` is false, Stage 6 returns
`accepted_partial` with an open-dictation marker. Stage 7's `build_prompt()`
returns `None` for this status, and `run()` returns `not_applicable`. There is
no user-facing completion question or inactivity reminder. Diagnostic terminal
output can still show the partial result; this is not the same as guiding the
user to finish the interaction.

The interactive and voice loops process each subsequent input as a new
command. They do not maintain a pending dictation buffer to which a later
utterance can be appended. The open status alone therefore does not provide
a complete multi-turn dictation interaction.

## Offline check and code inspection

A supplied Stage 6 payload with `status=accepted_partial`, `action=set_note`,
and `end_signal=false` produced:

```text
Completion/confirmation prompt: None
Stage 7 outcome: not_applicable
```

No remote model or application action was invoked. The absence of a pending
session was established by inspecting `_interactive_loop()` and `_voice_loop()`
in `src/run_pipeline.py`. Relevant modules are `src/stage6_intent_output.py`
and `src/stage7_confirmation.py`.

## Proposed interaction, not yet implemented

After a configurable period without further dictation, for example five
seconds, prompt the user:

> Are you finished with this note, or would you like to continue?

Use equivalent wording for a message. Five seconds is a proposed starting
value, not an evaluated usability threshold. The prompt must not interrupt
active recording or treat model/network processing time as user inactivity.

The interaction should distinguish:

- **Finished:** close dictation and run any required checks before applying
  the normal action-specific confirmation policy. Indicating completion does
  not itself confirm sending a message or committing the command.
- **Continue:** preserve the current text, collect further dictation, and append
  it to the pending content.
- **Cancel:** discard the pending interaction when cancellation is explicit.
- **No or unclear response:** keep the interaction pending according to a
  defined timeout/cancellation policy. Silence must not automatically complete
  the dictation or send the message.

A completion reminder is separate from both clarification (Finding 7) and
confirmation of a completed interpretation.

## Implementation considerations

1. Retain a pending session with its action, dictated content, message recipient,
   original input, and other information needed to resume processing. Preserve
   the distinction between dictated text and interaction-control replies.
2. Define inactivity measurement for text and push-to-talk voice modes. The
   current push-to-talk implementation has no continuous listening timer.
3. Interpret replies in the pending interaction's context. A reply such as
   “continue” must not accidentally become a new independent command.
4. Define how continuation and completion replies interact with trigger gating
   (Finding 8), rather than rejecting a legitimate follow-up by accident.
5. Validate extraction fields using the agreed schema policy (Finding 9).
   Missing `end_signal` must remain distinguishable from a valid Boolean false
   under that policy.
6. Verify notes and messages with and without explicit completion signals,
   continuation over multiple turns, recipient retention, cancellation,
   inactivity prompts, unclear responses, and the normal confirmation policy.

## Report and evaluation

The report describes the current behaviour and labels this as a proposed
interaction improvement. Green Finding 10 markers identify the relevant
passages to revise after implementation. The existing single-command
Stages 1–6 evaluation does not establish the usability or correctness of a
multi-turn completion interaction. Add separate interaction tests after the
fix and assess the reminder interval with users if a usability study is run.

No code fix or evaluation rerun has been performed for this finding.


## Resolution (8 November 2026)

**Status:** Fixed in the code for `--interactive` and `--voice`. The historical
evidence above is unchanged.

Changes:
- New module `src/interaction.py` (`InteractionSession`, `PendingDictation`):
  keeps an open note/message (action, content, recipient, original input,
  confidence, model, all utterances). Clock, pipeline, and confirmation are
  injected, so the tests need no model, microphone, or real waiting.
- Replies in an open dictation need no trigger and make no LLM call. Whole
  utterances "finish" / "continue" / "cancel" (and variants, configured in
  `domain_config.yaml` under `interaction.dictation_replies`) control the
  dictation. Anything else is dictated content and is appended. Content that
  ends with a multi-word finish phrase ("... finish note") is appended without
  the phrase and finishes the dictation.
- Reminder: after `interaction.dictation_reminder_sec` (5 s, an initial value,
  not evaluated with users) of inactivity the question "I did not hear an end
  phrase for this note. Is the note finished, or would you like to continue?"
  (message: "... this message ...") is shown once per waiting period.
  Inactivity is measured from the end of the last turn's processing. Time
  between `begin_input()` and `end_input()` (recording and transcription) is
  excluded: `end_input()` moves the reminder and discard deadlines on by that
  time, also when the transcript is empty and `handle()` is not called. After
  the reminder, finish / continue / cancel control the dictation, and any other
  utterance is kept as dictated content (an implicit "continue"), so text that
  was started before the question appeared is never lost. Only an empty
  utterance is "unclear".
- Finish: `run_pipeline.complete_dictation()` runs Stages 3-6 on the full
  content with `end_signal=True` (no LLM call), then the action's normal Stage 7
  policy applies. Finishing is not a confirmation; a message still needs the
  explicit confirmation of `send_message`.
- Silence never finishes or sends a dictation. If the question gets no answer
  for `interaction.dictation_discard_sec` (60 s, added on 8 November 2026 at
  the user's request), the open dictation is discarded and the user is told
  "No answer: the open note was discarded. Nothing was saved or sent." Cancel
  clears it at once.
- `--text` (one command, no session): an open dictation is reported as still
  open, with the hint to use `--interactive` / `--voice` or an end phrase.
- `domain_loader.load()` returns the new `interaction` block. The block is not
  used in the LLM prompt.

Tests (`tests/test_interaction.py`, fake clock and scripted replies): notes and
messages with and without end signal, several continuation turns, recipient and
content kept, finish / continue / cancel / unclear / silence, reminder timing
(4.9 s none, 5 s once), discard 60 s after an unanswered question (59.9 s not
yet; no discard while recording; an answer stops the discard), recording time
excluded after the recording ends (also with an empty transcript), a
continuation sent after the reminder is kept, punctuated and hyphenated finish
phrases remove only their own span, no reminder while recording or during processing,
trigger-free follow-ups, normal confirmation after finishing, state cleared and
isolated between sessions.

Remaining limitations: in push-to-talk voice mode there is no ambient
listening, so the reminder can only appear while the system waits for the next
recording. While a dictation is open, a new command (even with the trigger) is
treated as dictated content; the user has to finish or cancel first. The
control phrases are a fixed English list. The 5 s and 60 s intervals are not
validated with users. A discarded dictation is lost; it is not kept as a draft.

### Review of the fixes (8 November 2026)

An independent review of the code changes confirmed the clarification guard
and the per-drug validation, and found five bugs. All five are fixed, each with
a test that fails on the earlier code:

1. Interaction: text started before the reminder and sent after it was taken
   as an unclear answer and not appended. Now any non-control utterance after
   the reminder is dictated content (`tests/test_interaction.py`).
2. `strip_trigger()` did not remove a punctuated trigger ("ok, glasses"), so its
   "ok" still counted as a confirmation. It now accepts the same variants as
   `detect_trigger()` (`tests/test_stage7_clarification.py`).
3. A punctuated finish phrase ("98%. Finish-the-note.") was removed by
   counting words, which cut dictated text ("oxygen saturation"). Now only the
   character span of the phrase is removed (`tests/test_interaction.py`).
4. Recording time still counted towards the discard deadline after the
   recording ended (empty transcript path). `end_input()` now moves the
   deadlines on (`tests/test_interaction.py`).
5. `run_eval.score_pipeline()` raised `KeyError: 'IGNORED'` in the per-category
   counts for inactive input (`tests/test_run_eval_scoring.py`).

Also from the review: E3 runs now get the batch tag in their folder name
(`run_e3.py --tag`), and `run_all.py` names the E3 folder explicitly in the
report, so a second batch on the same day cannot reuse an older E3 result.

A second review confirmed these fixes and found one more input-loss case: in
the terminal a typed line only arrives on Enter, so the 60 s discard could
delete an open note while the user was still typing a continuation. Fix:
`run_pipeline.TerminalLineReader` reads the terminal key by key (cbreak mode,
own echo, backspace), calls `InteractionSession.note_activity()` for every
keystroke, which restarts the reminder and discard deadlines, and keeps the
typed text across a reminder or discard message. Real silence still discards.
`tests/test_terminal_typing.py` types continuously across both deadlines
(the note and the full continuation are kept) and checks real silence; the two
typing tests fail if keystrokes are not counted. Piped (non-terminal) input
falls back to `input()` without timers. In `--voice` mode a recording counts as
activity through `begin_input`/`end_input`. Note on the "hmm becomes content"
policy: the confirmation question shows free text shortened to 60 characters,
so a short addition at the end of a long note is not necessarily visible
there.

A third review found two input-preservation bugs in the terminal reader, and
checking them in a pseudo-terminal showed a third one. All are fixed in
`run_pipeline.TerminalLineReader` with tests in `tests/test_terminal_typing.py`:

1. Several lines in one input (a pasted block) lost everything after the first
   newline. Unread characters now stay in a queue and become the next lines.
   A confirmation question is answered by `read_answer()` (passed to Stage 7 as
   the new optional `read_response`): it first moves input that is already
   waiting into the queue and then takes only keys typed after the question, so
   a pasted "yes" can never confirm a command. The pasted lines are kept and
   handled afterwards as normal inputs.
2. A UTF-8 character split between two reads ("ü" in "Müller") was deleted,
   because each chunk was decoded on its own with errors ignored. The reader now
   keeps an incremental UTF-8 decoder.
3. `tty.setcbreak()` was called with its default `TCSAFLUSH`, which discards input
   that is already waiting (type-ahead, the rest of a paste) every time a read
   starts. It is now called with `TCSANOW`. Checked in a pseudo-terminal: a
   two-line paste and a split "ü" give "first line", "second line", "Müller".

A fourth review found two problems in the confirmation reader, both fixed:

1. Pasted text was told apart from typing by a 50 ms quiet period, a timing
   guess: a paste arriving 100 ms after the question could still confirm a
   command. The guess is removed. The interactive session now switches the
   terminal to bracketed paste mode, which marks every paste (ESC[200~ ...
   ESC[201~). Pasted characters are kept and handled later as normal input,
   but never used as the answer to a confirmation question, whatever the
   timing. Input that is already waiting when the question is shown is not an
   answer either. Policy for unmarked input: a terminal without bracketed paste
   sends a paste like typing; such text that arrives after the question counts
   as typed (a documented limitation; macOS Terminal, iTerm2 and xterm support
   the mode).
2. The confirmation timeout restarted with every input chunk (a 5 s question
   accepted "yes" after 16 s). `read_answer()` now uses one deadline for the
   whole question, including the time to collect waiting input; keys do not
   extend it. No timeout (`null` policy) still waits without limit. A partly
   typed answer at the deadline is dropped and the action's policy applies.

Other escape sequences (arrow keys) are no longer inserted as text. Tests in
`tests/test_terminal_typing.py` (marked paste after 0.1 s and 3 s gaps, paste
markers split between reads, pasted text as normal input, deadline with keys
every 4 s, waiting input counting towards the deadline, no limit for `null`,
the 5 s `show` policy through Stage 7). 99 tests pass.

---

# Finding 11: Compound medications can be accepted with unresolved drug segments

**Date:** 7 November 2026  
**Status at the time of the finding:** Open (see Resolution below). Correction required; not implemented.

## Reproduction

The supplied extraction was for:

```text
ok glasses set medication aspirin 100 milligrams and zqxvnonexistent 50 milligrams
```

`zqxvnonexistent` is an invented test name. Local preprocessing, structural
validation, lookup, type validation, and output assembly produced:

```text
trigger_found: true
structural validation: valid
aspirin: found
zqxvnonexistent: not found
Stage 5 status: accepted
Stage 6 status: accepted
```

Stage 2 was supplied with a controlled extraction; no model was called and
no application action was executed. This confirms an implementation failure
on a constructed test case, not its frequency in the recorded evaluation.

## Cause

`src/stage4_ontology_lookup.py::_lookup_medication()` records each segment
in `drug_list`, but returns a successful top-level result if any segment
matches. Its top-level type comes from the first successfully resolved drug.
`src/stage5_type_validate.py::validate()` checks that top-level result without
requiring every `DrugResult` to be found. Stage 6 can therefore assemble an
accepted intent while an individual drug is unresolved.

## Required correction, not yet implemented

- Require successful resolution and permitted type for every medication
  segment before producing an accepted compound-medication intent.
- Preserve the successful results and identify the unresolved or incompatible
  segment in a clarification request. Do not discard the original content
  or automatically replace an unresolved drug with a guessed concept.
- Carry per-drug validation information into output assembly and the displayed
  summary. Coordinate clarification routing with Finding 7 so that unresolved
  segments cannot be confirmed or automatically confirmed.
- Test all-known drugs, one unknown drug, all unknown drugs, reversed ordering,
  more than two segments, and unsupported types where applicable. Ensure one
  successful segment never hides another segment's failure.
- Rerun the relevant evaluation after implementation and preserve the previous
  results. Avoid a dataset-specific exception for this invented test name.

Red Finding 11 markers identify the affected report passages in Sections
5.3, 5.8, 6.3.4, and 6.3.5. The source code is unchanged.


## Resolution (8 November 2026)

**Status:** Fixed in the code. The historical evidence above is unchanged.

Changes:
- `src/stage4_ontology_lookup.py`: `DrugResult` has a new field
  `semantic_type`. For a compound (two or more segments), `drug_list` always
  holds one result per segment. The top-level result is found only if every
  segment is found; otherwise `found=False` with the full `drug_list` (also when
  all segments fail). A single drug behaves as before.
- `src/stage5_type_validate.py`: for a `drug_list`, every drug must be found
  (else `entity_not_found`) and have an allowed type (else `type_mismatch`).
- `src/stage6_intent_output.py`: `_compound_clarification()` builds a
  `needs_clarification` payload that names the unresolved or incompatible
  segments (`unresolved_segments`), keeps all per-drug results (`drugs`) and
  the original command, lists the recognised drugs, and guesses nothing.
  Through Finding 7 such a result cannot be confirmed.
- Behaviour change for start/stop (fallback to the medication index) and
  set_medication: a compound with a failed segment now stops at the medication
  index, so the global clarification search is not run for the whole compound
  string.

Reproduction now: "aspirin 100 milligrams and zqxvnonexistent 50 milligrams" ->
Stage 5 `entity_not_found`, Stage 6 `needs_clarification` ("I couldn't find
'zqxvnonexistent 50 milligrams' ... Recognised: aspirin."), Stage 7
`clarification_requested`.

Offline check: the replay of the first run changed no outcome (the three
compound medication commands of the dataset resolve all their drugs).

Tests (`tests/test_compound_medication.py`, local ontology index, controlled
Stage 2): all known, one unknown in either position, all unknown, three
segments, an incompatible type (patched lookup), single-drug regression,
Stage 4-7 path with replies "yes" / "ok glasses correct" / none.

---

# Finding 12: Clarify action and entity extraction metric names

**Date:** 7 November 2026  
**Status at the time of the finding:** Open documentation and reporting task (see Resolution below). No calculation error is
established by this finding; no scoring code or existing results were changed.

## Current definitions and proposed labels

| Current label/key | What is measured | Proposed display label |
|---|---|---|
| Action accuracy / `action_accuracy_pct` | Correct expected action on valid commands with an action label (130 commands in the reported evaluation). | Action classification accuracy |
| Entity accuracy / `entity_accuracy_pct` | Extracted text equals the reference text after normalisation, on the 92 valid commands with entity labels. | Entity extraction accuracy (normalised exact match) |

The entity score is not entity-type classification accuracy, token-level NER
F1, or ontology-linking accuracy. It compares the extracted text, which can
include medication instructions or dictated message content. These two
metrics use different denominators and should not be merged under one
unqualified “classification accuracy” label.

## Verified scoring behaviour

`evaluation/scripts/run_eval.py::score_pipeline()` compares action names and
normalised extracted entity text with the gold labels. The report builder
uses the same entity normalisation: lowercase, replacement of punctuation
other than forward slash, and whitespace normalisation. The report-level
metric definitions already describe these comparisons; the requested change
is primarily the clarity and consistency of their names.

## Proposed reporting update, not yet implemented

- Apply the distinct display labels in metric definitions, narrative text,
  generated tables, figure legends, and report-generation scripts.
- Keep the underlying definitions, denominators, and values unchanged unless
  a separate scoring change is explicitly justified.
- Internal JSON keys need not change merely to improve display names. If
  renamed, maintain compatibility with historical run records and consumers.
- Review `evaluation/scripts/run_eval.py`, `build_final_report.py`,
  `make_extraction_figure.py`, `make_figures.py`, and `make_report_tables.py`.
- Regenerate reporting artifacts and check their names consistently after the
  implementation fixes and planned rerun. Regeneration of labels alone does
  not require new remote model calls.

Grey Finding 12 markers are placed in the metric definitions and extraction
results/figure in Chapter 7. Existing plotted labels remain unchanged until
this coordinated update.


## Resolution (8 November 2026)

**Status:** Done in the reporting code. The thesis and the generated reports of
the historical runs were not changed.

Changes:
- `evaluation/scripts/metric_labels.py`: `ACTION_LABEL = "Action
  classification accuracy"`, `ENTITY_LABEL = "Entity extraction accuracy
  (normalised exact match)"`, `END_SIGNAL_LABEL = "End-signal F1"`, and
  `label_counts()`, which counts commands, valid / invalid commands, action
  labels (130), entity labels (92), and end-signal labels (20) from the dataset
  and `data/labels/gold_labels.json`.
- `build_final_report.py`, `make_report_tables.py`, `make_figures.py`, and
  `make_extraction_figure.py` use these names, and the report texts take their
  denominators from `label_counts()` instead of fixed numbers. JSON keys
  (`action_accuracy_pct`, `entity_accuracy_pct`, ...) and the scoring are
  unchanged. `make_figures.py` has new options `--out` and `--no-copy` so that
  figures can be made without overwriting the thesis figures.

Tests (`tests/test_metric_labels.py`): rebuilding the first-run report from the
stored run folders into a temporary folder gives exactly the same numbers as
`all_results_2026-11-03.json`; the definitions and the Markdown report use the
new names; the denominators are 130 and 92.

Note: `make_extraction_figure.py` writes directly into the thesis folder and
`make_figures.py` copies there unless `--no-copy` is given. They were not run.

---

# Status / next steps (updated November 2026)

- **Fixes (8 November 2026):** Findings 7 to 12 are fixed in the code, with
  99 offline tests (`python3 -m unittest discover -s tests`), after four
  independent reviews found more bugs, which are fixed (Finding 10, "Review of
  the fixes"). An offline replay
  of the first run and a new B0 run changed no outcome. Report descriptions
  in Chapters 5 and 6 have now been updated in colour for review. Chapters 7–9
  retain historical numbers and contain temporary pointers for review after
  the fresh evaluation; its results have not been incorporated. The user is
  managing that rerun using the commands in `evaluation/evaluation.md`
  (section "Rerun after the fixes of November 2026").
- **Planned follow-up (7 November 2026):** fix the outstanding implementation
  issues, including clarification/confirmation routing (Finding 7) and missing
  trigger gating (Finding 8), extraction-schema validation (Finding 9), and
  unfinished-dictation interaction (Finding 10), and compound medication
  validation (Finding 11). Coordinate metric terminology (Finding 12), then
  rerun the evaluation with the activation
  policy and test coverage documented. *(Superseded by the entry above.)*
- **Pipeline:** all seven stages are implemented. Stage 7 was implemented and
  tried, but it is not part of the evaluation.
- **Evaluation:** done. Two complete runs on the 243 commands with six systems
  (four models, two token budgets for DeepSeek and gpt-oss), the baseline B0,
  and the entity-resolution set E3. The reports are in
  `evaluation/old_runs/reports/final/` (first run: `*_2026-11-03.*`, second run:
  `*_2026-11-05.*`). The final run with the fixed code (8 November 2026) is in
  `evaluation/final_run/`. The decisions are the same in both runs, and the response
  times differ with the load of the model service.
- **Grammar:** the keyword workaround of Stage 3 is gone. The rule for a
  treatment has a fixed keyword (`START TREATMENT any_text`), and diagnosis and
  stop accept any words. This also removed the grammar rejection of "Stop now
  frame" that Finding 5 describes under `c105`. The command now parses, and it
  is sent to clarification in some runs because "frame" is not in an ontology.
- **`c172`** ("Activate drug regimen for Mr. Johnson: Start with 50 micrograms
  of Levothyroxine"): still depends on the model. It was accepted in 15 of the 18
  repetitions of the first run and rejected in 3.
- **Candidate fixes:** not pursued beyond the few-shot examples (Finding 6).
  The thesis makes no dataset-specific fixes. The ideas stay as future work.
