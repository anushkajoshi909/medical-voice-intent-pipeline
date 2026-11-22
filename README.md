# Medical Voice Intent Pipeline

**Master's Thesis — TU Dresden, Chair of Software Technology**
*Improving Intent Recognition in Grammar-Based Voice Interfaces for Medical Smart Glasses*

---

## What This Is

A 7-stage NLP pipeline that processes voice commands spoken by doctors wearing smart glasses in a clinical setting. The core problem: grammar-based systems (like the baseline by Ludyga) fail on synonyms, informal phrasing, and medical abbreviations. This pipeline replaces rigid grammar token matching with an LLM extractor, while keeping formal structural validation and ontology-based semantic checking for safety.

**Central use case:** A doctor says *"diagnosis hemolysis"* but ASR transcribes it as *"diagnosis hemodialysis"*. The pipeline must catch this semantic mismatch — hemodialysis is a procedure, not a diagnosis — and prompt the doctor to clarify.

---

## Pipeline Stages

```
Speech input (ASR / typed)
    │
    ▼  Activation check
    │  A new command must contain the trigger ("ok glasses"); otherwise it
    │  is ignored (status "inactive") and nothing else runs
    │
    ▼  Stage 1 — Preprocessing
    │  Normalise text, expand abbreviations (BP→blood pressure),
    │  remove the trigger, extract urgency flags,
    │  reject non-speech input (JSON, code)
    │
    ▼  Stage 2 — LLM Structured Extraction
    │  LLM maps natural speech to JSON:
    │  {action, entity, end_signal, confidence} (+ recipient for messages),
    │  checked against a strict schema; a failed check gives UNKNOWN
    │  Handles synonyms, fillers, informal phrasing
    │
    ▼  Stage 3 — ANTLR Frame Validation
    │  Reconstructs canonical string from LLM {action, entity},
    │  feeds to real ANTLR4 Python runtime (generated from .g4 files).
    │  Catches structural errors: missing dose/unit for set_medication,
    │  entity present where forbidden, etc. Returns command_type.
    │
    ▼  Stage 4 — Ontology Lookup        [implemented]
    │  Resolves entity to a known medical concept + semantic type.
    │  Sources: DOID, HPO, RxNorm, NCIt, vital_signs.json.
    │  Compound prescriptions (e.g. "aspirin and clopidogrel") look up
    │  each drug independently. Registry + Strategy design patterns.
    │
    ▼  Stage 5 — Semantic Validation    [implemented]
    │  Checks: does this action allow this semantic type?
    │  Catches "diagnosis hemodialysis" → procedure ≠ disease
    │
    ▼  Stage 6 — Intent JSON Output     [implemented]
    │  Assembles final structured payload: action, canonical entity,
    │  semantic type, ontology ID, urgency flag from Stage 1
    │  status ∈ {accepted, accepted_partial, needs_clarification, rejected}
    │
    ▼  Stage 7 — User Confirmation      [implemented]
       Only an accepted result is confirmed: the glasses show a summary and
       the user says "correct" or "wrong". Per-action confirmation_timeout_sec
       in domain_config.yaml: null=always wait for an answer, N=auto-confirm
       after N seconds without an answer, 0=confirm instantly (e.g. emergency).
       A clarification shows its message and waits for a corrected command;
       an open note or message stays open until it is finished.
```

---

## Results (Stages 1–6, final run of 8 November 2026)

243 commands (130 valid, 113 invalid), every model three times; mean over the repetitions. Full results: `evaluation/final_run/reports/final/main_results_2026-11-08.md`.

| System | Accuracy | Valid accepted | Invalid wrongly accepted | Median time |
|---|---|---|---|---|
| Ludyga's grammar (B0) | 70.8 % | 46.2 % | 0.9 % | — |
| Gemma 4 26B-A4B | **95.8 %** | 93.6 % | 1.8 % | 0.42 s |
| Llama 3.3 70B | 95.4 % | **96.7 %** | 6.2 % | 0.64 s |
| gpt-oss-120b (500 / 4000 tokens) | 93.8 / 94.8 % | 91.0 / 93.3 % | 3.0 / 3.5 % | 1.06 / 1.08 s |
| DeepSeek V4.1 Flash (500 / 4000 tokens) | 91.1 / 93.0 % | 84.9 / 88.5 % | 1.8 / 1.8 % | 1.02 / 0.92 s |

---

## Setup

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Offline tests (no model calls, no microphone, no real waiting):

```bash
python3 -m unittest discover -s tests -v
```

### 2. Set API key

The language models and Whisper are called through the OpenAI-compatible interface of the ScaDS.AI service (`https://llm.scads.ai/v1`). The key is read from the first line of `~/.scadsai-api-key` (outside the repository, never committed):

```bash
echo "your_key_here" > ~/.scadsai-api-key
chmod 600 ~/.scadsai-api-key
```

The model is taken from `.selected_model` (written by `--select`), otherwise `alias-ha`.

### 3. Download ontology files

These files are too large for the repository. Place them in `ontologies/`:

| File | Source | Purpose |
|---|---|---|
| `doid.obo` | [disease-ontology.org](https://disease-ontology.org) | Disease lookup |
| `hp.obo` | [hpo.jax.org](https://hpo.jax.org) | Symptom lookup |
| `RXNCONSO.RRF` | [NLM RxNorm Prescribable Content](https://www.nlm.nih.gov/research/umls/rxnorm/docs/rxnormfiles.html) | Medication lookup |
| `Thesaurus.txt` | [NCI Thesaurus FLAT](https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/) — unzip `Thesaurus.FLAT.zip` | Procedure lookup |

See `ontologies/ONTOLOGY_SOURCES.md` for full rationale on each choice and what was rejected (SNOMED CT, LOINC, OPS).

---

## Running the Pipeline

The evaluation on the 243 commands is run with `evaluation/scripts/run_eval.py` and `run_all.py` (see `evaluation/evaluation.md`). `src/run_pipeline.py` runs single commands and sessions. Add `--select` to any of the commands below to pick the model first.

### Run a single command verbosely
```bash
python src/run_pipeline.py --text "ok glasses set medication aspirin 100 milligrams and clopidogrel 75 milligrams finish medication"
```
Prints step-by-step output for every stage without running the full evaluation set. Also runs Stage 7: the glasses-display confirmation prompt, which either blocks for your terminal input ("ok glasses, correct"/"ok glasses, wrong") or auto-confirms after a per-action timeout, depending on that action's `confirmation_timeout_sec` in `domain_config.yaml`.

### Interactive REPL (full pipeline + Stage 7, repeat-on-reject)
```bash
python src/run_pipeline.py --interactive
```
Type a command starting with "ok glasses" and see it run through all 7 stages. A command without the trigger phrase is ignored (status `inactive`) and the session keeps listening. Only an accepted command is confirmed; a command that needs clarification shows its message and the session waits for the corrected command (again with "ok glasses"). A note or message without an end phrase stays open: further lines are added to it without a trigger, "finish", "continue", and "cancel" control it, after 5 s without input the glasses say that no end phrase was heard and ask whether the note is finished, and if that question gets no answer for 60 s the open note is discarded (`src/interaction.py`, settings under `interaction` in `domain_config.yaml`). Type `exit` to quit.

### Voice session (push-to-talk, Whisper)
```bash
python src/run_pipeline.py --voice
```
Press Enter, speak, press Enter again. The transcript goes through the same session as `--interactive`. A confirmation question with a time limit is confirmed automatically if Enter is not pressed in time.

---

## Project Structure

```
├── src/
│   ├── stage1_preprocess.py        # Normalisation, abbreviation expansion, urgency
│   ├── stage2_llm_extract.py       # LLM structured extraction (loaded via domain_loader)
│   ├── stage3_antlr_validate.py    # Real ANTLR4 frame validation; action list + entity
│   │                               #   rules loaded from domain_config.yaml at runtime
│   ├── stage4_ontology_lookup.py   # Ontology lookup: Registry + Strategy patterns
│   ├── stage5_type_validate.py     # Semantic type validation against domain_config rules
│   ├── stage6_intent_output.py     # Assembles final intent JSON from Stages 1-5 results
│   ├── stage7_confirmation.py      # User confirmation dialogue
│   ├── interaction.py              # Session state across turns: open dictation, reminder, finish/continue/cancel
│   ├── terminal_input.py           # Key-by-key keyboard input with deadlines (sessions)
│   ├── voice.py                    # Push-to-talk recording + voice confirmation answer
│   ├── whisper_asr.py              # Whisper transcription via the ScaDS.AI service
│   ├── domain_loader.py            # Wires grammar + config + prompt at startup
│   ├── grammar/                    # Generated Python parser (from .g4 files, committed)
│   │   ├── MedicalSmartGlassesLexer.py
│   │   ├── MedicalSmartGlassesParser.py
│   │   └── MedicalSmartGlassesParserListener.py
│   └── run_pipeline.py             # Stages 1-6 for one command; --text, --interactive, --voice
│
├── data/                           # The dataset: commands and labels
│   ├── commands_200.json           # 200 base evaluation commands
│   ├── extended_test_cases.json    # Extended commands (abbreviations, type mismatches)
│   ├── annotated_commands.json     # Combined + annotated (auto-generated), 243 commands
│   ├── scripts/                    # How the dataset was built: parse_commands.py (Ludyga's 200
│   │                               #   commands), build_annotated_dataset.py (adds labels + extended)
│   └── labels/                     # Labels for the evaluation
│       ├── gold_labels.json            # Expected entity and end signal (by command id)
│       ├── gold_labels_worksheet.csv   # Worksheet from which the gold labels were made
│       └── entity_resolution_gold.csv  # Entity-resolution set E3 (98 pairs)
│
├── docs/
│   ├── uml_diagrams.md            # Current architecture diagrams (UML component, sequence and
│   │                              #   class diagrams, data-flow figures), images in docs/figures/
│   └── pipeline_evolution.md      # Ludyga baseline vs enhanced pipeline
│
├── ontologies/
│   ├── vital_signs.json            # 43 vital sign + lab result terms (curated)
│   └── ONTOLOGY_SOURCES.md        # Rationale for every ontology choice
│
├── evaluation/                     # The evaluation (see evaluation/evaluation.md)
│   ├── evaluation.md               # Dataset, metrics, folders, how to run
│   ├── scripts/                    # run_all.py, run_eval.py, run_e3.py, build_final_report.py, ...
│   ├── final_run/                  # The reported evaluation (8 Nov 2026): runs/, reports/final/, reports/tables/
│   └── old_runs/                   # Earlier runs (3-5 Nov 2026, before the fixes), kept unchanged
│
├── MedicalSmartGlassesLexer.g4    # ANTLR lexer — grammar authority for structure
├── MedicalSmartGlassesParser.g4   # ANTLR parser — command type hierarchy (u21 diagnosis,
│                                  #   u22 treatment added)
├── grammar_extend.md              # Documents changes from Ludyga's baseline grammar
├── domain_config.yaml             # Single source of truth: actions, slot rules,
│                                  #   grammar_command_type, ontology sources,
│                                  #   domain description, few-shot examples
├── llm_system_prompt.txt          # Domain-agnostic prompt template ({placeholders})
├── abbreviations.json             # Medical abbreviation expansion table
└── Final_Pipeline_Complete.md     # Full pipeline design + worked examples
```

---

## Baseline Comparison

The baseline system (`baseline_ludyga/`) uses an ANTLR grammar with fixed token lists. It fails on:
- Synonyms: *"illustrate"* → not a grammar token
- Informal phrasing: *"let me see the vitals"*
- Medical abbreviations: *"show BP"*
- Free-form notes and messages

The baseline folder is excluded from this repository (not our contribution). The evaluation runs the baseline as system B0 (`evaluation/scripts/run_eval.py`); its results are in the run folders `evaluation/final_run/runs/*_B0-ludyga_*`.

---

## Thesis Context

- **University:** TU Dresden, Chair of Software Technology
- **Supervisor:** Lidia Roszko
- **Topic:** Improving Intent Recognition in Grammar-Based Voice Interfaces
- **Key contribution:** Replacing rigid grammar token matching with LLM + ontology semantic validation, while retaining formal grammar structure as a frame validation layer
