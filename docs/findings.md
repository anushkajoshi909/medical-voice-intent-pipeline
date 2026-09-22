# Findings: Pipeline False Accepts

> Diagnostic notes from investigating specific false accepts. Not yet fixed —
> for reference when we pick these up.

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

- Add a translation layer in `stage3_antlr_validate.py` that maps common
  ANTLR failure patterns (missing `INT`/unit token for `medication`, empty
  message body for `send_message`, etc.) to human-readable reasons, instead
  of passing `listener.errors` through verbatim.
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

# Status / next steps

- **Pipeline stages**: **1–7 all implemented.** `src/stage7_confirmation.py`
  presents a Stage 6 payload as a human-readable glasses prompt and requires
  (or, per-action, times out to auto-confirm) an explicit "correct"/"wrong"
  response — driven by a new `confirmation_timeout_sec` field added to each
  action in `domain_config.yaml` (`null`=always block, e.g. `diagnosis`/
  `set_medication`/`start`/`stop`/`change_patient`/`send_message`; `<int>`
  seconds=auto-confirm for low-risk actions, e.g. `show`=5, `set_note`=6,
  `turn_on`/`turn_off`/`continue`=3; `0`=confirm instantly, e.g.
  `emergency`/`accept_emergency`). Run via `python src/run_pipeline.py
  --text "..."` (single command) or `--interactive` (REPL, repeats the
  command on reject). Docs (`README.md`, `Final_Pipeline_Complete.md`,
  `docs/uml_diagrams.md`, `docs/pipeline_evolution.md`) updated accordingly.
- **Candidate fix #1 (few-shot examples) applied** (Finding 6) — 4 new
  examples added, 3/5 known false accepts flipped to correct. Also fixed
  train/test contamination: 11 of 18 pre-existing few-shot examples,
  including both flagship type-mismatch test cases, were verbatim eval-set
  commands. All reworded; zero exact matches remain. A fresh full eval run
  is needed next to see the effect at dataset scale.
- **Fresh full eval run: done** (Finding 5, before the fixes above). Confirmed
  the instability holds at full-dataset scale, not just on a hand-picked
  batch, and definitively killed the confidence-gate idea. Also fixed a real
  `--save` crash
  (`DrugResult` not JSON-serializable) found in the process.
- **Plan**: (1) decide which candidate fix to try first — current read is
  the few-shot-example and confidence-redefinition fixes (both one-line
  prompt/config changes, no hardcoded lists) are the cheapest next
  experiments; (2) re-run the full eval after any prompt change to see if it
  actually moves the numbers, given documented instruction-following gaps;
  (3) separately look at `c172` (medication order bypassing the dose/unit
  safety check) and `c105` (grammar rejecting
  a valid baseline command) — both flagged as higher-priority than the
  confidence/passthrough cluster.
- None of the candidate fixes above have been implemented. Revisit this file
  before deciding which to pick up.
