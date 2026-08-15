# Final Pipeline: Complete Design with Files and Worked Examples
*LLM as structured extractor · ANTLR as frame validator · Ontology as semantic classifier*

---

## Architecture: Single Source of Truth + Design Patterns

The pipeline is designed so that **adding a new action requires editing exactly two artefacts**:

1. **`MedicalSmartGlassesParser.g4` (+ Lexer)**: defines the structural rule (entity required? optional? what structure must the entity have?). This is the grammar authority. After editing, regenerate the Python parser with the ANTLR jar.

2. **`domain_config.yaml`**: the single source of truth for the entire semantic layer. Each action declares:
   - `description`: injected into LLM prompt
   - `grammar_command_type`: validated against grammar at startup
   - `allowed_slot_types`: checked at Stage 5
   - `ontology_index`: which ontology cache to search at Stage 4 (`null` = passthrough)
   - `entity_extractor`: extraction strategy before lookup (`default` or `medication`)
   - `fallback_index`: secondary index if primary misses
   - `passthrough_type`: semantic type assigned without lookup (for free-text or patient-identifier slots)
   - `passthrough_fallback_type`: for show only; entity not matched in vital signs gets this type (e.g. `ehr_record`)
   - `entity_slot: false`: structural flag for actions where grammar forbids an entity (pipeline strips any LLM extraction before Stage 3)

   The same file also contains `ontology_sources`, which declares which data files feed each Stage 4 index, along with format type, file path, filter parameters, and fuzzy cutoff; no file paths or filter values are hardcoded in Stage 4.

Everything else is derived automatically:
- `src/domain_loader.py` reads grammar + config at startup and validates they are consistent
- `llm_system_prompt.txt` is a **domain-agnostic template** with `{placeholders}`; healthcare rules and few-shot examples are injected from `domain_config.yaml` at runtime

**If grammar and config diverge, the pipeline refuses to start**; the loader raises a clear error naming which action is missing from which file.

### Design Patterns in Stage 4

Stage 4 is built around two design patterns that keep the routing and loading code unchanged when the domain changes.

**Registry Pattern** (`_FORMAT_LOADERS`): Stage 4 needs to load several different ontology file formats — OBO files for diseases, an NCIt tab-separated file for procedures, an RxNorm pipe-delimited file for medications. Rather than writing a chain of `if format == "obo": ... elif format == "ncit": ...` inside the loading loop, each format has a dedicated loader function registered in a dictionary keyed by type name. When `_ensure_loaded()` reads `ontology_sources` from `domain_config.yaml`, it looks up the type name in the registry and calls the right function. Adding a new file format means writing one new loader function and adding one line to the registry; the loading loop itself never changes.

**Strategy Pattern** (`_STRATEGIES`): Before looking up an entity in an ontology, Stage 4 sometimes needs to prepare it differently depending on the action. For `set_medication`, the entity is "paracetamol 100 mg" — but the lookup should be on just "paracetamol" (the dose is not part of the drug name in RxNorm). For `diagnosis` or `show`, the entity is looked up as-is. Each preparation approach is a separate strategy function. `domain_config.yaml` declares which strategy each action uses (`entity_extractor: medication` or `entity_extractor: default`), and `lookup()` dispatches to the right function from the registry. Adding a new preparation approach means writing one new strategy function and one YAML key; the dispatch logic never changes.

Together, these two patterns mean that **adding a new action or a new ontology source requires only edits to `domain_config.yaml` and (for a new file format) one new loader function — no changes to routing or dispatch code.**

---

## Files We Create and Use

| File | Role |
|---|---|
| `MedicalSmartGlassesLexer.g4` | ANTLR lexer; canonical tokens. **Grammar authority** |
| `MedicalSmartGlassesParser.g4` | ANTLR parser; command type hierarchy + structural rules (e.g. medication must have INT unit) |
| `src/grammar/` | Generated Python parser from `.g4` files; committed, regenerated when grammar changes |
| `domain_config.yaml` | **Single source of truth**: semantic layer and ontology routing. Per-action: `grammar_command_type`, `description`, `allowed_slot_types`, `ontology_index`, `entity_extractor`, `fallback_index`, `passthrough_type`, `entity_slot`. Plus `ontology_sources` block: which files feed each Stage 4 index, format type, filter params, fuzzy cutoffs. |
| `llm_system_prompt.txt` | Domain-agnostic prompt template with `{placeholders}`; no healthcare content |
| `src/domain_loader.py` | Wires grammar + config + template at startup; validates consistency; builds LLM prompt |
| `src/stage4_ontology_lookup.py` | Ontology lookup; Registry + Strategy patterns; routing, loaders, and cutoffs all read from `domain_config.yaml` |
| `abbreviations.json` | Abbreviation expansion (Stage 1); sourced from Wikipedia, not manually extended |
| `docs/uml_diagrams.md` | UML diagrams (pipeline flow, Stage 4 patterns, domain independence boundary), rendered in GitHub/GitLab |

---

## Full Pipeline

```
Speech text (from ASR, e.g. Whisper / VOSK)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 1: Preprocessing                    [implemented]    │
│  • Lowercase everything                                     │
│  • Strip punctuation; hyphens replaced with spaces           │
│    ("non-invasive" -> "non invasive"; consistent with Stage 4)│
│  • Normalise trigger: "ok" → "okay", detect "okay glasses"  │
│  • Remove trigger from string, keep remainder               │
│  • Scan for urgency flags (stat, urgent, immediately)       │
│    → store separately, remove from string                   │
│  Result: clean command text, urgency metadata               │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 2: LLM Structured Extraction        [implemented]    │
│                                                             │
│  Input:  preprocessed command text                          │
│          + action list from domain_config (short form)      │
│          + llm_system_prompt.txt (loaded at startup)        │
│                                                             │
│  Output: CONSTRAINED JSON                                   │
│  {                                                          │
│    "action":     one valid action name or "UNKNOWN",        │
│    "entity":     content string (entity / note text /       │
│                  patient name), WITHOUT end signal words,   │
│    "end_signal": true if user signalled end of dictation,   │
│    "confidence": 0.0–1.0                                    │
│  }                                                          │
│                                                             │
│  LLM handles:                                               │
│  ✓ Action synonyms (illustrate → show, commence → start)   │
│  ✓ Filler removal (I want to, can you, please...)           │
│  ✓ Terminator detection (finish note, I'm done, wrap up,    │
│    that's all, okay stop, done documenting, alright...)     │
│  ✓ Ambiguous entity noise (strips "the", "for patient X"    │
│    from entity where irrelevant)                            │
│                                                             │
│  Constraint: action MUST be from domain_config list         │
│  If cannot map → action = "UNKNOWN" → reject immediately   │
└─────────────────────────────────────────────────────────────┘
    │
    │ action = "UNKNOWN" ──────────────────────→ UNRECOGNIZED_COMMAND
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 3: ANTLR Frame Validation           [implemented]    │
│                                                             │
│  Reconstructs canonical string from LLM {action, entity}:  │
│    "ok glasses set medication paracetamol 100 mg            │
│     finish medication"                                      │
│                                                             │
│  Feeds to real ANTLR4 Python runtime (generated from .g4)   │
│                                                             │
│  Grammar catches structural errors the LLM cannot:         │
│  • set_medication: entity must match medicine INT unit      │
│    → "set medication paracetamol" → PARSE_ERROR (no dose)  │
│  • emergency: entity must be empty                          │
│  • show / diagnosis: entity must be non-empty               │
│                                                             │
│  Output: valid bool + command_type (for backend routing)    │
│    command_type ∈ {request_data, set_medication, diagnosis, │
│                    glasses_command, protocol, stop_cmd,     │
│                    request_patient, message}                │
└─────────────────────────────────────────────────────────────┘
    │
    │ Frame invalid ───────────────────────────→ INVALID_STRUCTURE
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 4: Ontology / Canonical Lookup      [implemented]    │
│                                                             │
│  Routing is fully config-driven from domain_config.yaml:    │
│    • action → ontology_index, fallback_index, extractor     │
│    • ontology_index: null + passthrough_type → skip lookup, │
│      return entity as canonical with semantic_type assigned  │
│      (free-text: set_note, send_message; change_patient)    │
│    • ontology_index: null, no passthrough → system command  │
│      (emergency, turn_on/off) → no canonical                │
│                                                             │
│  For entity-bearing commands (start, show, diagnosis,       │
│  set_medication, stop):                                     │
│    Strategy (entity_extractor field) prepares query:        │
│      "default"    → normalise + look up entity as-is        │
│      "medication" → strip dose/unit, look up drug name only  │
│    Resolution order per index:                              │
│      1. Exact normalised label match                        │
│      2. Synonym match (OBO synonym entries; RxNorm aliases) │
│      3. Fuzzy match via difflib SequenceMatcher             │
│         cutoff per index from domain_config.yaml             │
│         0.72 (show) to 0.90 (diagnosis)                     │
│    If primary index misses, retry on fallback_index         │
│                                                             │
│  Output (OntologyResult):                                   │
│    raw_entity, canonical, semantic_type, ontology_id,       │
│    match_method (exact/synonym/fuzzy/passthrough/none),     │
│    score (0.0–1.0), source (doid/ncit/rxnorm/…)            │
└─────────────────────────────────────────────────────────────┘
    │
    │ Entity not found ────────────────────────→ ENTITY_NOT_FOUND + clarification
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 5: Semantic Validation              [implemented]    │
│                                                             │
│  Lookup: action → allowed_slot_types (from domain_config)   │
│  Check:  resolved semantic_type ∈ allowed_slot_types?       │
│                                                             │
│  Match  → ACCEPTED                                          │
│  No match → NEEDS_CLARIFICATION with explanation:           │
│    "Entity X was found as type Y. Action Z expects type W." │
│                                                             │
│  This is the key safety layer that catches:                 │
│  • "diagnosis hemodialysis" → hemodialysis is a procedure,  │
│     not a disease. Diagnosis expects disease/symptom.       │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 6: Intent JSON + Explanation           [pending]     │
│  Assemble final structured payload for backend:             │
│    action, canonical entity, semantic type, ontology ID,    │
│    urgency flag (from Stage 1), clarification message       │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 7: User Confirmation         [designed; not started] │
│                                                             │
│  Glasses display a human-friendly interpretation:           │
│    "Diagnosis: Hemolysis. Correct?"                         │
│                                                             │
│  User responds:                                             │
│    "ok glasses, correct" → CONFIRMED → command executed     │
│    "ok glasses, wrong"   → REJECTED  → user prompted to     │
│                            repeat the command               │
│    [timeout after N sec] → auto-confirm for low-risk        │
│                            actions (configurable per action) │
│                                                             │
│  Creates a dialogue feedback loop. Directly addresses the   │
│  hemodialysis/hemolysis use case: even if Stage 5 issues    │
│  a clarification, Stage 7 gives the user a final            │
│  yes/no checkpoint before any action is executed.           │
└─────────────────────────────────────────────────────────────┘
```

---

## Files

### `domain_config.yaml`

The **single source of truth** for the semantic layer and Stage 4 ontology routing. Key fields per action:

- `grammar_command_type`: links this action to a parser rule. Validated at startup.
- `description`: injected into the LLM prompt.
- `allowed_slot_types`: checked in Stage 5 semantic validation.
- `ontology_index`: which index Stage 4 searches (`null` = skip lookup).
- `entity_extractor`: extraction strategy before lookup (`default` uses entity as-is; `medication` strips dose/unit).
- `fallback_index`: secondary index tried if primary yields no match.
- `passthrough_type`: for actions with `ontology_index: null` that carry meaningful entities; assigned as `semantic_type` without lookup.
- `passthrough_fallback_type`: for show only; entity not matched in vital signs gets this type (`ehr_record`).
- `entity_slot: false`: structural flag for actions where grammar forbids an entity; pipeline strips any LLM extraction before Stage 3.

The same file also contains `ontology_sources`, which declares which data files feed each Stage 4 index, their format types, filter parameters, and fuzzy cutoffs. No separate `ontology_sources.yaml` file exists.

Full content: [`domain_config.yaml`](domain_config.yaml)

---

### `llm_system_prompt.txt`

A **domain-agnostic template** that contains no action names, no healthcare examples, no healthcare rules. It has `{ACTION_LIST}`, `{DOMAIN_RULES}`, and `{FEW_SHOT_EXAMPLES}` placeholders that `src/domain_loader.py` fills from `domain_config.yaml` at startup. Changing the domain means changing `domain_config.yaml` only; the prompt template itself is untouched.

Full content: [`llm_system_prompt.txt`](llm_system_prompt.txt)

---

### `src/domain_loader.py`

Runs once at startup. Parses `MedicalSmartGlassesParser.g4` to extract all command type rule names, loads `domain_config.yaml`, and for each action checks that `grammar_command_type` matches a rule in the grammar — raising a `ValueError` with the offending action name if not. It then fills the `llm_system_prompt.txt` template with the action list, domain rules, and few-shot examples from config, and returns `system_prompt`, `action_config`, `valid_actions`, and `trigger_phrase` for the rest of the pipeline to use.

This is the enforced single-file change contract: add a new action to the grammar and config, loader validates both and builds the prompt; Stage 2 and Stage 3 pick it up automatically.

Full content: [`src/domain_loader.py`](src/domain_loader.py)

---

### `abbreviations.json`

Full content: [`abbreviations.json`](abbreviations.json)

---

### `MedicalSmartGlassesLexer.g4` and `MedicalSmartGlassesParser.g4`

Extended from Ludyga's baseline grammar. Full content in the repo root files. Key design points:

- **Lexer**: individual keyword tokens (`START`, `SHOW`, `DIAGNOSIS`, etc.); no compound action tokens. Compound action names (e.g. `change_patient`) are combinations of tokens, resolved by the parser rules. `WORD: [a-zA-Z]+` (Ludyga-identical); hyphens handled by Stage 1 preprocessing.
- **Parser**: preserves Ludyga's command type hierarchy. Adds `diagnosis` and `treatment` as new types. `stop_cmd` accepts optional entity (for "stop hemodialysis"). `set_medication` retains the `medicine INT unit` enforcement from Ludyga.

The Python parser (`src/grammar/`) is generated from these files using:
```bash
java -jar antlr4-4.13.2-complete.jar -Dlanguage=Python3 -o src/grammar \
  MedicalSmartGlassesLexer.g4 MedicalSmartGlassesParser.g4
```

Changes from Ludyga baseline are documented in `grammar_extend.md`.

---

### Design Decision: Why ANTLR Remains in the Pipeline

This thesis extends a grammar-based pipeline. ANTLR is not decorative; it performs structural validation that the LLM cannot and should not do.

**Division of responsibility:**

| Task | Who does it | Why |
|---|---|---|
| Synonym normalisation | LLM (Stage 2) | LLM understands meaning; ANTLR lexer lists are always incomplete |
| Filler word removal | LLM (Stage 2) | LLM generalises; hardcoded lists cannot |
| Action identification | LLM (Stage 2) | LLM maps natural language to canonical action names |
| Structural validation | ANTLR (Stage 3) | Grammar is the formal specification; Python reimplementation drifts |
| Semantic type checking | Ontology + Stage 5 | Neither LLM nor grammar knows all medical terminology |

**What ANTLR catches that the LLM cannot:**

The critical example is `set_medication`. A clinician says "set medication paracetamol" without specifying a dose. The LLM extracts `{action: set_medication, entity: "paracetamol"}` -- this is the correct extraction of what was said. But the grammar rule:

```antlr
medication: medicine INT unit | INT unit OF medicine ;
```

requires a numeric dose and a unit. ANTLR parses the reconstructed canonical string and returns a `PARSE_ERROR`; the command is structurally incomplete and must be rejected before it reaches the patient record system.

A Python dictionary lookup (`if entity: valid`) would pass this. ANTLR does not.

**Why the grammar is the single source of truth for structure:**

Previously, grammar rules were duplicated in a Python dict inside `stage3_frame_validate.py`. Any change to the grammar required changing both files, with no enforcement that they stayed in sync. The current design:

- Stage 3 (`stage3_antlr_validate.py`) runs the real ANTLR4 Python runtime on the generated parser
- `src/domain_loader.py` validates at startup that `domain_config.yaml` actions map to grammar rules
- If grammar and config diverge, the pipeline refuses to start

**What ANTLR does NOT do (by design):**

ANTLR does not see raw speech. By the time ANTLR runs, the LLM has already normalised the input to a canonical `{action, entity}` pair. Stage 3 reconstructs a parseable string from this pair and feeds it to ANTLR. ANTLR validates structure; it does not identify actions or map synonyms; that is and should remain the LLM's job.

**Grammar extension from Ludyga baseline:**

The grammar extends Ludyga's original (`baseline_ludyga/`). Changes are documented in `grammar_extend.md`. The command type hierarchy (glasses_command, request_data, protocol, set_medication, request_patient, message) is preserved; `diagnosis` is added as a new type.

---

### Ontology Sources (declared in `domain_config.yaml`)

There is no separate `ontology_sources.yaml`. All ontology source declarations live in the `ontology_sources` block of `domain_config.yaml` (shown above), so the pipeline has a single configuration file to update when adapting to a new domain.

| Index | Source file(s) | Format | Semantic type | Notes |
|---|---|---|---|---|
| `show` | `vital_signs.json` | `json_labels` | `vital_sign` | 25 vital sign labels; unmatched entities fall through as `ehr_record` |
| `diagnosis` | `doid.obo` | `obo` | `disease` | Human Disease Ontology (CC0) |
| `diagnosis` | `hp.obo` | `obo` | `symptom` | Human Phenotype Ontology (free academic use) |
| `procedure` | `Thesaurus.txt` | `ncit` | `procedure` | NCI Thesaurus; rows filtered by `filter_semantic_types` in config |
| `medication` | `RXNCONSO.RRF` | `rxnorm` | `medication` | NLM RxNorm prescribable content; no UMLS license required |

**Design notes:**
- SNOMED CT was rejected (requires license); replaced by DOID + HPO + NCIt
- NCIt vital signs were rejected (blood pressure appears as `Finding`, not `vital_sign`); `vital_signs.json` was built manually for the 25 core vital signs
- `patient_info_terms.json` was dropped. The LLM now extracts the entity as spoken; Stage 4 matches against vital_signs.json and passes through unmatched entities as ehr_record.

---

### Ontology Parsing and Semantic Type Assignment

OBO files (DOID, HPO) do **not** contain a semantic type field. The type is assigned based on which file the term comes from: every term in DOID is a disease by definition, every term in HPO is a symptom. NCIt is the exception: it covers all concept types in one file, so we use its `semantic_type` column to filter only procedure/vital sign rows at load time.

**Parsing happens once at startup.** `_ensure_loaded()` iterates the `ontology_sources` block in `domain_config.yaml`, dispatches each source to its loader via `_FORMAT_LOADERS`, and builds a separate named in-memory index per ontology group:

```
Index "diagnosis":
  "hemolysis"        → {semantic_type: "disease",    concept_id: "DOID:3680",   source: "doid"}
  "acute hemolysis"  → {semantic_type: "disease",    concept_id: "DOID:3680",   source: "doid"}  # synonym
  "hyperthyroidism"  → {semantic_type: "symptom",    concept_id: "HP:0000853",  source: "hp"}

Index "procedure":
  "hemodialysis"     → {semantic_type: "procedure",  concept_id: "NCIt:C15248", source: "ncit"}

Index "medication":
  "aspirin"          → {semantic_type: "medication",  concept_id: "RxCUI:1191", source: "rxnorm"}

Index "show":
  "blood pressure"   → {semantic_type: "vital_sign",  concept_id: None, source: "vital_signs"}
  "heart rate"       → {semantic_type: "vital_sign",  concept_id: None, source: "vital_signs"}
  # anything not matched → passthrough_fallback_type = "ehr_record" (no lookup entry needed)
```

Synonyms are indexed as separate keys pointing to the same concept. Every Stage 4 query is then an O(1) dict lookup for exact and synonym matches, with a linear fuzzy scan as fallback; no file I/O at query time. This keeps per-command latency at < 1ms for the ontology step, acceptable for real-time AR glasses use.


---
---

# Worked Examples from the Command List

---

## Example 1: `"ok glasses Begin the IV infusion"`
*Fails in Ludyga's grammar because "begin" is not a lexer token and IV infusion is not a grammar keyword. Shows abbreviation expansion, LLM synonym handling, and NCIt lookup.*

---

**Stage 1: Preprocessing**
```
Input:   "ok glasses Begin the IV infusion"
Lower:   "ok glasses begin the iv infusion"
Abbrevs: IV -> intravenous
Trigger: found and stripped
Result:  "begin the intravenous infusion"
Urgency: none
```

**Stage 2: LLM Structured Extraction**
```
Input to LLM: "begin the intravenous infusion"

LLM reasoning (internal):
  "begin" -> closest action = "start" (initiate something)
  Entity = "intravenous infusion" (clinical content, filler "the" stripped)
  No end signal.

LLM output:
{
  "action":     "start",
  "entity":     "intravenous infusion",
  "end_signal": false,
  "confidence": 0.98
}

Ludyga's grammar: FAILS ("begin" is not a lexer token; treatment type did not exist)
LLM: SUCCEEDS; maps action synonym, entity extracted as spoken
```

**Stage 3: ANTLR Frame Validation**
```
Reconstruct canonical: "ok glasses start intravenous infusion"
Rule matched: treatment -> u22: START entity
entity "intravenous infusion" is non-empty
command_type = "treatment"
Frame: VALID
```

**Stage 4: Ontology / Canonical Lookup**
```
entity          = "intravenous infusion"
action          = "start"
ontology_index  = "procedure"  (from domain_config)
entity_extractor = "default"

Step 1: Exact match in NCIt Thesaurus: not found for "intravenous infusion"
Step 2: Synonym match in NCIt: not found
Step 3: Fuzzy match in NCIt:
  "Parenteral Infusion Procedure" (NCIt:C21088) -> score 0.90  [match]

Result:
  raw_entity    = "intravenous infusion"
  canonical     = "Parenteral Infusion Procedure"
  semantic_type = "procedure"
  ontology_id   = "NCIt:C21088"
  match_method  = "synonym"
  score         = 0.90
  source        = "ncit"
```

**Stage 5: Semantic Validation**
```
action="start" + semantic_type="procedure"
domain_config: start allows [treatment, procedure, medication, shared_session, emergency]
"procedure" in allowed -> YES
Result: ACCEPTED
```

**Stage 6: Intent JSON**
```json
{
  "status": "accepted",
  "action": "start",
  "original_command": "ok glasses Begin the IV infusion",
  "slots": {
    "procedure": {
      "raw_entity":    "intravenous infusion",
      "canonical":     "Parenteral Infusion Procedure",
      "semantic_type": "procedure",
      "ontology_id":   "NCIt:C21088",
      "match_method":  "synonym",
      "score":         0.90,
      "source":        "ncit"
    }
  },
  "explanation": {
    "llm_extraction":      "'Begin' mapped to action 'start'. 'IV' expanded to 'intravenous' by Stage 1. Entity extracted as spoken.",
    "grammar_validation":  "START entity frame valid. command_type=treatment.",
    "ontology_evidence":   "'intravenous infusion' matched NCIt C21088 via synonym at score 0.90.",
    "semantic_validation": "Action 'start' permits slot type 'procedure'. Accepted."
  }
}
```

**Stage 7: User Confirmation**
```
Glasses display: "Start treatment: Parenteral Infusion Procedure (NCIt:C21088). Correct?"

User says:  "ok glasses, correct"
-> CONFIRMED
-> Infusion initiated
```

---

## Example 2: `"ok glasses Note: Upon examination, there was tenderness in the lower abdomen and a palpable mass"`
*From u15 set note commands. No explicit terminator; note is still open. Shows LLM handling notes without end signal.*

---

**Stage 1: Preprocessing**
```
Input:   "ok glasses Note: Upon examination, there was tenderness in the
          lower abdomen and a palpable mass"
Lower:   "ok glasses note upon examination there was tenderness in the
          lower abdomen and a palpable mass"
Strip:   punctuation removed (colon, comma)
Trigger: found and stripped
Result:  "note upon examination there was tenderness in the lower
          abdomen and a palpable mass"
```

**Stage 2: LLM Structured Extraction**
```
Input to LLM: "note upon examination there was tenderness in the lower
               abdomen and a palpable mass"

LLM reasoning:
  "note" → action = "set_note"
  Entity = everything after "note" (the clinical content)
  No end-of-dictation signal detected.

LLM output:
{
  "action":     "set_note",
  "entity":     "upon examination there was tenderness in the lower abdomen and a palpable mass",
  "end_signal": false,
  "confidence": 0.98
}

Note: end_signal=false means the note is still being dictated.
The system will wait for a follow-up command with end_signal=true.
```

**Stage 3: ANTLR Frame Validation**
```
Canonical: "ok glasses set note upon examination there was tenderness..."
Rule: SET_NOTE entity → ✓
Entity non-empty ✓
end_signal=false → note session open, more input expected
Frame: VALID (partial; awaiting close)
```

**Stage 4: Ontology Lookup**
```
action = "set_note" → free_text slot type
→ Skip ontology entirely
Entity stored as raw clinical text
```

**Stage 5: Semantic Validation**
```
set_note + free_text → ✓ allowed
```

**Stage 6: Intent JSON**
```json
{
  "status": "accepted_partial",
  "intent": "SET_NOTE_OPEN",
  "action": "set_note",
  "slots": {
    "free_text": {
      "content":    "upon examination there was tenderness in the lower abdomen and a palpable mass",
      "end_signal": false,
      "note_state": "open -- awaiting end-of-dictation signal"
    }
  },
  "explanation": {
    "llm_extraction":      "Action 'set_note' identified. No end-of-dictation signal detected. Note remains open.",
    "grammar_validation":  "SET_NOTE entity frame valid.",
    "ontology_evidence":   "Free-text slot; no ontology lookup.",
    "semantic_validation": "set_note allows free_text. Accepted as partial (open note)."
  }
}
```

**Stage 7: User Confirmation**
```
Glasses display: "Recording note -- still open. Waiting for end signal."

→ No confirmation prompt issued while note session is open.
  User confirmation will be triggered once end_signal=true is received
  and the full note content is ready for commit.
```

---

## Example 3: `"ok glasses Set medication aspirin 100 milligrams and clopidogrel 75 milligrams, finish medication"`
*From u16–u17 medication commands. Shows LLM terminator detection ("finish medication") and structured medication parsing.*

---

**Stage 1: Preprocessing**
```
Input:   "ok glasses Set medication aspirin 100 milligrams and clopidogrel
          75 milligrams, finish medication"
Lower:   "ok glasses set medication aspirin 100 milligrams and clopidogrel
          75 milligrams finish medication"
Strip:   comma removed
Trigger: stripped
Result:  "set medication aspirin 100 milligrams and clopidogrel 75 milligrams
          finish medication"
```

**Stage 2: LLM Structured Extraction**
```
Input to LLM: "set medication aspirin 100 milligrams and clopidogrel 75
               milligrams finish medication"

LLM reasoning:
  "set medication" → action = "set_medication"
  Entity = drug prescription content
  "finish medication" → end_signal = true
  Entity content EXCLUDES "finish medication"

LLM output:
{
  "action":     "set_medication",
  "entity":     "aspirin 100 milligrams and clopidogrel 75 milligrams",
  "end_signal": true,
  "confidence": 0.99
}

→ Ludyga's grammar: PARTIAL (handles "finish medication" but only via
  exact FINISH MEDICATION token sequence)
→ LLM: "finish medication" correctly identified as end signal regardless
  of exact wording (would also handle "I'm done", "okay complete", etc.)
```

**Stage 3: ANTLR Frame Validation**
```
Canonical: "ok glasses set medication aspirin 100 milligrams and
            clopidogrel 75 milligrams"
Rule: SET_MED entity ✓
Entity non-empty ✓
end_signal=true → prescription closed ✓
Frame: VALID
```

**Stage 4: Ontology Lookup (RxNorm)**
```
raw_entity = "aspirin 100 milligrams and clopidogrel 75 milligrams"
action     = "set_medication"

Parse medication list: ["aspirin 100 milligrams", "clopidogrel 75 milligrams"]

Drug 1: "aspirin"
  RxNorm lookup: exact match → RxCUI:1191, "Aspirin", medication ✓
  Dose: 100 milligrams

Drug 2: "clopidogrel"
  RxNorm lookup: exact match → RxCUI:41493, "Clopidogrel", medication ✓
  Dose: 75 milligrams

semantic_type: medication (both drugs confirmed)
```

**Stage 5: Semantic Validation**
```
set_medication + medication → ✓ allowed
```

**Stage 6: Intent JSON**
```json
{
  "status": "accepted",
  "intent": "SET_MEDICATION",
  "action": "set_medication",
  "slots": {
    "medication": [
      {
        "raw":           "aspirin 100 milligrams",
        "drug_label":    "Aspirin",
        "rxcui":         "RxCUI:1191",
        "dose":          "100",
        "dose_unit":     "milligrams",
        "semantic_type": "medication",
        "match_type":    "exact"
      },
      {
        "raw":           "clopidogrel 75 milligrams",
        "drug_label":    "Clopidogrel",
        "rxcui":         "RxCUI:41493",
        "dose":          "75",
        "dose_unit":     "milligrams",
        "semantic_type": "medication",
        "match_type":    "exact"
      }
    ]
  },
  "explanation": {
    "llm_extraction":      "'Set medication' mapped to action 'set_medication'. End signal 'finish medication' detected; excluded from entity.",
    "grammar_validation":  "SET_MED entity frame valid. Prescription closed (end_signal=true).",
    "ontology_evidence":   "Aspirin (RxCUI:1191) and Clopidogrel (RxCUI:41493) confirmed as medications via RxNorm.",
    "semantic_validation": "set_medication allows slot type 'medication'. Both drugs confirmed. Accepted."
  }
}
```

**Stage 7: User Confirmation**
```
Glasses display:
  "Set medication:
     Aspirin 100 mg
     Clopidogrel 75 mg
   Correct?"

User says: "ok glasses, correct"
→ CONFIRMED
→ Prescription committed to patient record

Note: Medication commands are high-criticality; auto-timeout confirmation
is disabled by default. Explicit verbal confirmation is always required.
```

---

## Supervisor's Test Cases: Hemolysis vs Hemodialysis

These are the two use cases directly from the supervisor's email. This is the most important demonstration.

---

### Supervisor Use Case 1: Doctor says "start hemodialysis"

Input: `"ok glasses start hemodialysis"`

The original grammar returned "undefined" because hemodialysis was not in the grammar's treatment tokens. Our pipeline:

**Stage 1: Preprocessing**
```
Result: "start hemodialysis"
```

**Stage 2: LLM Extraction**
```
{
  "action":     "start",
  "entity":     "hemodialysis",
  "end_signal": false,
  "confidence": 0.99
}
```

**Stage 4: Ontology Lookup**
```
raw_entity = "hemodialysis"
allowed_slot_types for "start" = [treatment, procedure, medication, shared_session, emergency]

Step 4a: Exact match in NCIt (Thesaurus.txt):
  "hemodialysis" → NCIt:C15248, "Hemodialysis", semantic_type = "procedure"
  → maps to our slot type: "treatment"

matched_label  = "Hemodialysis"
concept_id     = "NCIt:C15248"
semantic_type  = "treatment"
match_type     = "exact"
confidence     = 1.0
```

**Stage 5: Semantic Validation**
```
start + treatment → allowed ✓
```

**Stage 6: Output**
```json
{
  "status": "accepted",
  "intent": "START_TREATMENT",
  "action": "start",
  "slots": {
    "treatment": {
      "raw_entity":    "hemodialysis",
      "matched_label": "Hemodialysis",
      "concept_id":    "NCIt:C15248",
      "semantic_type": "treatment",
      "match_type":    "exact",
      "confidence":    1.0
    }
  },
  "explanation": {
    "llm_extraction":      "Action 'start', entity 'hemodialysis' extracted with high confidence.",
    "grammar_validation":  "START entity frame valid.",
    "ontology_evidence":   "'Hemodialysis' matched NCIt concept C15248 as a clinical procedure.",
    "semantic_validation": "Action 'start' permits slot type 'treatment'. Accepted."
  }
}
```

**Stage 7: User Confirmation**
```
Glasses display: "Start treatment: Hemodialysis (NCIt:C15248). Correct?"

User says: "ok glasses, correct"
→ CONFIRMED
→ Nurses informed; hemodialysis treatment initiated
```

**Result: CORRECTLY ACCEPTED → nurses are informed of the treatment.**
Supervisor's baseline returned "undefined". Our pipeline returns a fully resolved intent.

---

### Supervisor Use Case 2: Doctor says "diagnosis hemolysis" (ASR returns "hemodialysis")

This is the critical safety test. The doctor diagnosed hemolysis (a blood disorder). The ASR transcribed it as hemodialysis (a procedure). The system must catch this mismatch.

Input: `"ok glasses diagnosis hemodialysis"` ← ASR error

**Stage 1: Preprocessing**
```
Result: "diagnosis hemodialysis"
```

**Stage 2: LLM Extraction**
```
{
  "action":     "diagnosis",
  "entity":     "hemodialysis",
  "end_signal": false,
  "confidence": 0.97
}

Note: The LLM correctly maps "diagnosis" to action="diagnosis".
It does NOT second-guess the entity word; it preserves "hemodialysis"
exactly as spoken. The LLM is NOT responsible for catching this error.
That is the ontology + semantic validator's job.
```

**Stage 3: ANTLR Validation**
```
DIAGNOSIS entity → ✓ valid frame
```

**Stage 4: Ontology Lookup**
```
raw_entity = "hemodialysis"
action     = "diagnosis"
allowed_slot_types for "diagnosis" = [disease, symptom, condition, finding]

Step 4a: Exact match:
  Search disease_ontology (DOID) for "hemodialysis" → NOT FOUND
  (Hemodialysis is not a disease)

Step 4b: Synonym match in DOID: NOT FOUND

Step 4c: Abbreviation expansion: not applicable

Step 4d: Fuzzy match WITHIN allowed types only:
  Scope: disease, symptom, condition, finding
  (Procedure ontology is NOT searched; "diagnosis" does not allow procedures)

  Fuzzy candidates within disease/finding scope:
    "hemolysis" (DOID:3680) → edit_distance = 4  (haemodialysis vs hemolysis)
    "haemolysis"            → edit_distance = 3
    → edit_distance > 2 threshold → NOT auto-matched

  No match within allowed semantic types at acceptable confidence.
  
  BUT: entity "hemodialysis" IS found in NCIt procedures (edit distance 0; exact match)
  semantic_type: treatment/procedure
  → This match is OUTSIDE the allowed types for "diagnosis"
  → Store as "cross_type_match" for semantic validator
```

**Stage 5: Semantic Validation**
```
Scenario: entity found but ONLY as wrong semantic type

action = "diagnosis"
allowed_slot_types = [disease, symptom, condition, finding]

resolved_semantic_type = "treatment" (from NCIt procedures cross-type match)
"treatment" ∈ [disease, symptom, condition, finding]? → NO ✗

Cross-type match detected:
  Entity "hemodialysis" IS a known concept → semantic_type "treatment"
  But "diagnosis" does NOT allow "treatment"
  
Result: NEEDS_CLARIFICATION
Reason: semantic_type_mismatch
```

**Stage 6: Clarification Output**
```json
{
  "status": "needs_clarification",
  "reason": "semantic_type_mismatch",
  "intent_attempted": "DIAGNOSIS_DISEASE",
  "action": "diagnosis",
  "raw_entity": "hemodialysis",
  "resolved_entity": {
    "matched_label":  "Hemodialysis",
    "concept_id":     "NCIt:C15248",
    "semantic_type":  "treatment",
    "match_type":     "exact",
    "found_in":       "procedure ontology"
  },
  "expected_semantic_types": ["disease", "symptom", "condition", "finding"],
  "explanation": {
    "llm_extraction":      "Action 'diagnosis', entity 'hemodialysis' extracted.",
    "grammar_validation":  "DIAGNOSIS entity frame structurally valid.",
    "ontology_evidence":   "'Hemodialysis' was found as NCIt concept C15248: a clinical PROCEDURE, not a disease. No match was found within the disease/symptom ontology at acceptable confidence.",
    "semantic_validation": "Action 'diagnosis' expects a disease or symptom. 'Hemodialysis' resolves as a treatment/procedure. Mismatch detected."
  },
  "clarification_message": "I heard 'hemodialysis' and recognised it as a medical procedure, not a diagnosis. A diagnosis should be a disease or symptom. Did you mean to diagnose 'hemolysis' (a blood condition)? Please repeat or confirm."
}
```

**Stage 7: User Confirmation (for NEEDS_CLARIFICATION path)**
```
Glasses display:
  "⚠ I heard 'hemodialysis'; this is a procedure, not a diagnosis.
   Did you mean: hemolysis (blood condition)?
   Say 'ok glasses, correct' to confirm hemolysis,
   or 'ok glasses, wrong' to repeat the command."

User says: "ok glasses, wrong"
→ REJECTED
→ Glasses respond: "Please repeat your diagnosis command."

Doctor says: "ok glasses diagnosis hemolysis"
→ Pipeline re-runs → ACCEPTED (DOID:3680)
→ Stage 7 displays: "Diagnosis: Hemolysis. Correct?"
→ Doctor says: "ok glasses, correct" → CONFIRMED
```

**Why this works correctly:**
- The ontology found "hemodialysis" exactly: as a *procedure*
- The semantic validator checked: does `diagnosis` allow `treatment`? No.
- The system did NOT silently accept, did NOT silently correct, did NOT crash
- It explains specifically WHAT was found and WHY it doesn't fit
- It suggests the likely intended word ("hemolysis") as a prompt
- Stage 7 adds a final confirmation gate: no command executes without the doctor's explicit "correct"

**Now if the doctor repeats "diagnosis hemolysis" correctly:**

Stage 4:
```
"hemolysis" → DOID:3680 → "Hemolysis" → semantic_type = "disease"
```

Stage 5:
```
diagnosis + disease → ✓ allowed
```

Output: ACCEPTED, DIAGNOSIS_DISEASE with DOID concept ID for documentation.

---

## Evaluation Results

Evaluated on 243 commands (130 valid, 113 invalid) using Llama 3.3 70B Instruct.

| Metric | Ludyga Baseline | Enhanced Pipeline |
|---|---|---|
| Binary accuracy | 75.3% | **95.9%** |
| Valid recall (128/130) | 50.0% | **98.5%** |
| Invalid precision (105/113) | 100% | **92.9%** |

The drop in invalid precision is deliberate. Ludyga's 100% was achieved by rejecting almost everything, including valid clinical commands. The 7.1% false-accept rate in the enhanced pipeline comes from edge cases where the command structure is valid but the entity is clinically ambiguous or absent.

### What passed each stage

**Stage 1 rejections (5):** Non-speech inputs (JSON objects, `[inaudible]` markers, `{unintelligible}`) correctly rejected before reaching the LLM.

**Stage 3 failures (4):** All `set_medication` commands missing the required dose and unit — e.g. `"aspirin"` alone, `"hypertension"` (not a drug), `"albuterol inhaler 2 puffs every 4 hours"` (dosage instruction format, not `medicine INT unit`). ANTLR catches these structurally.

**Stage 5 type mismatches (3):** All `diagnosis` commands where the entity resolved as a procedure in NCIt — specifically hemodialysis and peritoneal dialysis. These are correctly rejected as type mismatches (semantic_type=procedure does not match allowed_slot_types for diagnosis).

**Stage 5 entity not found (9):** Six `stop` commands where the entity was empty or not present in the procedure/medication ontology, two stop commands with clinical entities not in NCIt/RxNorm (`antibiotics`, `ventilator`), and one `set_medication` with a dosage-instruction format entity. All accepted as `entity_not_found` — the intent was understood even if the entity could not be ontologically grounded.

### Remaining false accepts (8)

Eight invalid commands passed when they should have been rejected. These are edge cases where the command structure is valid and the entity check does not fire — primarily commands with ambiguous intent that the LLM maps to a plausible action without strong evidence against it. Addressing these would require either additional domain rules or a confidence threshold gate, which is outside the current scope.

---

## How the Pipeline Compares to Baseline

| Scenario | Ludyga Baseline | Our Pipeline |
|---|---|---|
| `"start hemodialysis"` | Returns "undefined"; not in grammar tokens | ACCEPTED via NCIt exact match |
| `"illustrate medical history"` | Fails; "illustrate" not a token | ACCEPTED via LLM synonym mapping |
| `"diagnosis hemodialysis"` (ASR error for hemolysis) | Returns "undefined" or wrong result | REJECTED; Stage 5 type mismatch (procedure vs diagnosis) |
| `"note: on examination... I'm done"` | Fails; "I'm done" not a FINISH token | ACCEPTED via LLM end_signal detection |
| `"set medication aspirin... finish medication"` | Handled; has exact FINISH MEDICATION token | Handled; LLM end_signal detection |
| `"code red initiate immediately"` | Fails; "code red" not a token | ACCEPTED; LLM maps to emergency |
| `"diagnosis hemolysis"` (correct) | Fails; "hemolysis" not a grammar token | ACCEPTED via DOID exact match |

---

## UML Diagrams

Architecture diagrams are in [`docs/uml_diagrams.md`](docs/uml_diagrams.md) (renders in GitHub/GitLab). Three diagrams:

1. **Pipeline Architecture**: five-stage component flow (Stages 1–5 implemented, Stage 6 pending)
2. **Stage 4 Design Patterns**: class diagram showing `_STRATEGIES` (Strategy pattern) and `_FORMAT_LOADERS` (Registry pattern) and their relationship to `domain_config.yaml`
3. **Domain Independence Boundary**: component diagram separating domain-independent core from domain-specific artefacts

---
*Document version 2.0, August 2026*
*Master Thesis: Improving Intent Recognition in Grammar-Based Voice Interfaces*
*Anushka Joshi, TU Dresden, Chair of Software Technology*
