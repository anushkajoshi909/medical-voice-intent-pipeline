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
    ▼  Stage 4 — Ontology Lookup        [in progress]
    │  Resolves entity to a known medical concept + semantic type
    │  Sources: DOID, HPO, RxNorm, NCIt, vital_signs.json.
    │  Closed-set entities (show action) resolved from domain_config.yaml
    │
    ▼  Stage 5 — Semantic Validation    [in progress]
    │  Checks: does this action allow this semantic type?
    │  Catches "diagnosis hemodialysis" → procedure ≠ disease
    │
    ▼  Stage 6 — Intent JSON Output     [in progress]
       Full audit trail of every stage's decision
```

---

## Results (Stages 1–3)

Evaluated on 243 annotated commands (198 base + 45 extended).

| Metric | Ludyga Baseline | Our Pipeline (Llama 3.3 70B) |
|---|---|---|
| Binary accuracy | 75.3% | **94.4%** |
| Valid recall | 50.0% | **99.1%** |
| Invalid precision | 100% | 90.2% |
| Action accuracy | — | **96.3%** |

---

## Setup

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install openai pyyaml
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
│   ├── stage3_antlr_validate.py    # Real ANTLR4 frame validation
│   ├── domain_loader.py            # Wires grammar + config + prompt at startup
│   ├── grammar/                    # Generated Python parser (from .g4 files, committed)
│   │   ├── MedicalSmartGlassesLexer.py
│   │   ├── MedicalSmartGlassesParser.py
│   │   └── MedicalSmartGlassesParserListener.py
│   ├── run_pipeline.py             # Full pipeline runner + evaluation
│   ├── build_annotated_dataset.py  # Rebuilds data/annotated_commands.json
│   └── run_baseline.py             # Ludyga baseline evaluation
│
├── data/
│   ├── commands_200.json           # 200 base evaluation commands
│   ├── extended_test_cases.json    # Extended commands (abbreviations, type mismatches)
│   └── annotated_commands.json     # Combined + annotated (auto-generated)
│
├── ontologies/
│   ├── vital_signs.json            # 44 vital sign + lab result terms (curated)
│   └── ONTOLOGY_SOURCES.md        # Rationale for every ontology choice
│
├── MedicalSmartGlassesLexer.g4    # ANTLR lexer — grammar authority for structure
├── MedicalSmartGlassesParser.g4   # ANTLR parser — command type hierarchy
├── grammar_extend.md              # Documents changes from Ludyga's baseline grammar
├── domain_config.yaml             # Semantic layer: descriptions, slot types,
│                                  #   grammar_command_type, canonical_entities,
│                                  #   domain rules, few-shot examples
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
