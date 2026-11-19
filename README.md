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
    ▼  Stage 1 — Preprocessing
    │  Normalise text, expand abbreviations (BP→blood pressure),
    │  detect trigger ("ok glasses"), extract urgency flags,
    │  reject non-speech input (JSON, code)
    │
    ▼  Stage 2 — LLM Structured Extraction
    │  LLM maps natural speech to constrained JSON:
    │  {action, entity, end_signal, confidence}
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
       Glasses display a human-friendly summary; user says
       "ok glasses, correct/wrong". Per-action confirmation_timeout_sec
       in domain_config.yaml: null=always block, N=auto-confirm after
       N idle seconds, 0=confirm instantly (e.g. emergency).
```

---

## Results (Stages 1–5)

Evaluated on 243 annotated commands (130 valid, 113 invalid).

| Metric | Ludyga Baseline | Our Pipeline (Llama 3.3 70B) |
|---|---|---|
| Binary accuracy | 75.3% | **95.9%** |
| Valid recall | 50.0% | **98.5%** |
| Invalid precision | 100% | **92.9%** |
| Action accuracy | — | **96.3%** |

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

The pipeline uses an OpenAI-compatible API endpoint (tested with Llama 3.3 70B via Groq and GPT-4o via OpenAI).

```bash
export OPENAI_API_KEY=your_key_here
export OPENAI_BASE_URL=https://api.groq.com/openai/v1   # if using Groq
```

Or create a `.env` file (never committed):
```
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.groq.com/openai/v1
```

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

### Full run (all 243 commands)
```bash
python src/run_pipeline.py
```

### Extended test cases only
```bash
python src/run_pipeline.py --extended
```

### Random sample of N commands
```bash
python src/run_pipeline.py --sample 20
```

### Verbose output (step-by-step output per command across all stages)
```bash
python src/run_pipeline.py --verbose
```

### Pick model interactively
```bash
python src/run_pipeline.py --select
```

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

### Save results to `results/`
```bash
python src/run_pipeline.py --save
```

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
│   ├── stage7_confirmation.py      # User confirmation dialogue (--text/--interactive)
│   ├── interaction.py              # Session state across turns: open dictation, reminder, finish/continue/cancel
│   ├── domain_loader.py            # Wires grammar + config + prompt at startup
│   ├── grammar/                    # Generated Python parser (from .g4 files, committed)
│   │   ├── MedicalSmartGlassesLexer.py
│   │   ├── MedicalSmartGlassesParser.py
│   │   └── MedicalSmartGlassesParserListener.py
│   ├── run_pipeline.py             # Full pipeline runner + evaluation (--text for single command)
│   ├── build_annotated_dataset.py  # Rebuilds data/annotated_commands.json
│   └── run_baseline.py             # Ludyga baseline evaluation
│
├── data/                           # The dataset: commands and labels
│   ├── commands_200.json           # 200 base evaluation commands
│   ├── extended_test_cases.json    # Extended commands (abbreviations, type mismatches)
│   ├── annotated_commands.json     # Combined + annotated (auto-generated), 243 commands
│   └── labels/                     # Labels for the evaluation
│       ├── gold_labels.json            # Expected entity and end signal (by command id)
│       ├── gold_labels_worksheet.csv   # Worksheet from which the gold labels were made
│       └── entity_resolution_gold.csv  # Entity-resolution set E3 (98 pairs)
│
├── docs/
│   ├── uml_diagrams.md            # Current architecture diagrams (UML component, sequence and
│   │                              #   class diagrams, data-flow figures), images in docs/figures/
│   ├── pipeline_evolution.md      # Ludyga baseline vs enhanced pipeline
│   └── findings.md                # Development log of false accepts, with a status table
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

The baseline folder is excluded from this repository (not our contribution). Evaluation data from the baseline is in `results/baseline_results.json`.

---

## Thesis Context

- **University:** TU Dresden, Chair of Software Technology
- **Supervisor:** Lidia Roszko
- **Topic:** Improving Intent Recognition in Grammar-Based Voice Interfaces
- **Key contribution:** Replacing rigid grammar token matching with LLM + ontology semantic validation, while retaining formal grammar structure as a frame validation layer
