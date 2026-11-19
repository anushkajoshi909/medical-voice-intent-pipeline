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
   - `fallback_extractor`: extraction strategy for the fallback index (`medication` strips dose and unit)
   - `stage3_template`: the text that Stage 3 builds for the grammar, with `{trigger}`, `{entity}`, `{recipient}` (required for every action)
   - `stage3_entity_view`: which copy of the entity the grammar check sees (`medication_core` keeps drug, dose, and unit and drops frequency words)
   - `entity_optional`: `true` if the action is valid without an entity (a bare "stop")
   - `confirmation_timeout_sec`: Stage 7 time limit (`null` waits for an answer, `0` confirms at once)
   - `structure_hint`: the sentence for the message when the structure is incomplete (set_medication, send_message)

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
| `docs/uml_diagrams.md` | Current architecture diagrams of the thesis (UML component, sequence and class diagrams, data-flow figures), images in `docs/figures/` |

---

## Full Pipeline

```
Speech text (from ASR, e.g. Whisper / VOSK)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 1: Preprocessing                    [implemented]    │
│  • Reject input that is clearly not speech (braces, brackets)│
│  • Strip punctuation; hyphens become spaces                 │
│  • Expand abbreviations (abbreviations.json, 183 entries)   │
│  • Lowercase everything                                     │
│  • Find the trigger (ok / okay / okie + glasses); remove it │
│    and all text before it                                   │
│  • Scan for urgency words (stat, urgent, urgently,          │
│    immediately) → store separately, remove from the text    │
│  Result: clean command text, urgency flags, expanded        │
│          abbreviations                                      │
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
│  Output: JSON (server JSON mode, checked in code)           │
│  {                                                          │
│    "action":     one valid action name or "UNKNOWN",        │
│    "entity":     content string (entity / note text /       │
│                  patient name), WITHOUT end signal words,   │
│    "end_signal": true if user signalled end of dictation,   │
│    "confidence": 0.0–1.0   (shown, not used to decide)      │
│  }   send_message also returns "recipient"                  │
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
│  If cannot map, or the call fails, or the answer is not    │
│  valid JSON → action = "UNKNOWN" → reject immediately       │
└─────────────────────────────────────────────────────────────┘
    │
    │ action = "UNKNOWN" ──────────────────────→ UNRECOGNIZED_COMMAND
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 3: ANTLR Frame Validation           [implemented]    │
│                                                             │
│  Rebuilds a command of fixed form from {action, entity}, with│
│  the template of the action (stage3_template in the config):│
│    "ok glasses set medication paracetamol 100 mg            │
│     finish medication"                                      │
│    "ok glasses start treatment dialysis"  (start)           │
│  The user never says 'treatment'; the word exists only in the│
│  rebuilt command and gives the rule a fixed keyword.        │
│                                                             │
│  Feeds it to the real ANTLR4 Python runtime (generated from │
│  the .g4 files)                                             │
│                                                             │
│  Grammar catches structural errors the LLM cannot:          │
│  • set_medication: entity must match medicine INT unit      │
│    → "set medication paracetamol" → PARSE_ERROR (no dose)   │
│  • send_message: a recipient is required                    │
│  • emergency: entity must be empty                          │
│  • show / diagnosis: entity must be non-empty               │
│                                                             │
│  Output: valid bool + command_type (for backend routing)    │
│    command_type ∈ {glasses_command, request_data, protocol, │
│      treatment, stop_cmd, set_medication, request_patient,  │
│      message, diagnosis}                                    │
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
│  STAGE 6: Intent JSON + Explanation        [implemented]    │
│  Assemble final structured payload for backend:             │
│    action, canonical entity, semantic type, ontology ID,    │
│    urgency flag (from Stage 1), clarification message       │
│                                                             │
│  status ∈ {accepted, accepted_partial, needs_clarification, │
│            rejected}                                        │
│  Driven entirely by Stage 1-5 results + domain_config.yaml  │
│  action metadata (passthrough_type, allowed_slot_types);    │
│  no action names hardcoded in src/stage6_intent_output.py   │
│                                                             │
│  rejected + reason=invalid_structure gets its own          │
│  actionable clarification_message when the action/entity    │
│  WERE understood but the grammar rejected it as             │
│  structurally incomplete (e.g. "set medication              │
│  paracetamol" — missing dose/unit). Built from the action's │
│  structure_hint in domain_config.yaml, not hardcoded per     │
│  action — e.g. "I understood you want to set medication     │
│  'paracetamol', but the command is structurally incomplete: │
│  needs a drug name followed by a numeric dose and unit,     │
│  e.g. 'paracetamol 100 mg'. Please repeat with the missing   │
│  detail." This replaces the raw, unreadable ANTLR parser     │
│  error that used to be the only explanation (still shown     │
│  underneath, for debugging). Other rejected reasons          │
│  (non_speech_input, unrecognized_command) get no such        │
│  message — there's no partial understanding to build one     │
│  from when the system has no idea what action was meant.     │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 7: User Confirmation                [implemented]    │
│                                                             │
│  Glasses display a human-friendly interpretation, built     │
│  from the Stage 6 payload (accepted slots or the             │
│  clarification_message):                                    │
│    "Diagnosis: Pneumonia. Correct?"                         │
│                                                             │
│  Design decision: rejected + invalid_structure's             │
│  clarification_message (above) does NOT go through this      │
│  stage's confirm/reject dialogue, even though it's shown     │
│  right alongside it. Confirm/reject only makes sense when    │
│  the system proposes a specific resolved interpretation to   │
│  accept or deny — a structural failure means nothing         │
│  concrete was resolved (e.g. no dose was ever extracted),    │
│  so there's nothing to confirm or deny, just a specific      │
│  "repeat with X" instruction. The pipeline still prompts     │
│  the user to repeat the command in --interactive/--voice     │
│  mode either way.                                            │
│                                                             │
│  User responds:                                             │
│  "ok glasses, correct" → CONFIRMED (not carried out)        │
│    "ok glasses, wrong"   → REJECTED  → user prompted to     │
│                            repeat the command               │
│    [timeout after N sec] → auto-confirm for low-risk        │
│                            actions (per-action                │
│                            confirmation_timeout_sec in       │
│                            domain_config.yaml; 0 = confirm   │
│                            instantly, e.g. emergency;        │
│                            null = always block for an        │
│                            explicit response, e.g.           │
│                            diagnosis/set_medication)          │
│                                                             │
│  Creates a dialogue feedback loop. Directly addresses the   │
│  hemodialysis use case: even if Stage 5 issues              │
│  a clarification, Stage 7 gives the user a final            │
│  yes/no checkpoint before any action is executed.           │
│                                                             │
│  Response classification (src/stage7_confirmation.py) is a  │
│  small deterministic keyword match, not an LLM call — the   │
│  confirmation vocabulary is intentionally narrow and fixed. │
│  Run it: `python src/run_pipeline.py --text "..."` (single  │
│  command) or `--interactive` (REPL with repeat-on-reject).  │
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
- `fallback_extractor`: extraction strategy for the fallback index (`medication` strips dose and unit)
- `stage3_template`: the text that Stage 3 builds for the grammar, with `{trigger}`, `{entity}`, `{recipient}` (required for every action)
- `stage3_entity_view`: which copy of the entity the grammar check sees (`medication_core` keeps drug, dose, and unit and drops frequency words)
- `entity_optional`: `true` if the action is valid without an entity (a bare "stop")
- `confirmation_timeout_sec`: Stage 7 time limit (`null` waits for an answer, `0` confirms at once)
- `structure_hint`: the sentence for the message when the structure is incomplete (set_medication, send_message)

The same file also contains `ontology_sources`, which declares which data files feed each Stage 4 index, their format types, filter parameters, and fuzzy cutoffs. No separate `ontology_sources.yaml` file exists.

Full content: [`domain_config.yaml`](domain_config.yaml)

---

### `llm_system_prompt.txt`

A **domain-agnostic template** that contains no action names, no healthcare examples, no healthcare rules. It has four placeholders, `{DOMAIN_DESCRIPTION}`, `{ACTION_LIST}`, `{DOMAIN_RULES}`, and `{FEW_SHOT_EXAMPLES}`, that `src/domain_loader.py` fills from `domain_config.yaml` at startup. Changing the domain means changing `domain_config.yaml` only; the prompt template itself is untouched.

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
- **Parser**: preserves Ludyga's command type hierarchy. Adds `diagnosis` and `treatment` as new types. `treatment` has a fixed keyword (`START TREATMENT any_text`), so it cannot swallow the other commands that begin with `start`; Stage 3 rebuilds `start treatment <entity>` from the template, and the user never says "treatment". Diagnosis and stop accept any words (`any_text`). `stop_cmd` accepts optional entity (for "stop hemodialysis"). `set_medication` retains the `medicine INT unit` enforcement from Ludyga.

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
  "pneumonia"        → {semantic_type: "disease",    concept_id: "DOID:552",    source: "doid"}
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
Reconstruct canonical: "ok glasses start treatment intravenous infusion"
Rule matched: treatment -> u22: START TREATMENT any_text
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
    "grammar_validation":  "Frame valid. command_type=treatment.",
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

## Example 4: `"set medication paracetamol"`
*No dose or unit given. Shows that a Stage 3 structural rejection isn't a dead end — Stage 6 explains specifically what's missing, built from config, not a raw parser error.*

---

**Stage 1: Preprocessing**
```
Result: "set medication paracetamol"
```

**Stage 2: LLM Structured Extraction**
```
{
  "action":     "set_medication",
  "entity":     "paracetamol",
  "end_signal": false,
  "confidence": 0.9
}
Action and drug name correctly identified. The LLM has no way to know a
dose is required — that's Stage 3's job, not Stage 2's (per the generic
rule: don't judge entity type or completeness, just extract what was said).
```

**Stage 3: ANTLR Frame Validation**
```
Canonical: "ok glasses set medication paracetamol finish medication"
Rule: medication: medicine INT unit | INT unit OF medicine
No INT (dose) or unit token found -> PARSE_ERROR
Frame: INVALID
```

**Stage 6: Intent JSON**
```json
{
  "status": "rejected",
  "reason": "invalid_structure",
  "action": "set_medication",
  "original_command": "set medication paracetamol",
  "explanation": {
    "llm_extraction":     "Action 'set_medication' extracted with confidence 0.9.",
    "grammar_validation":  "Frame invalid: PARSE_ERROR: col 55: no viable alternative at input 'setmedicationparacetamolfinishmedication'."
  },
  "clarification_message": "I understood you want to set medication 'paracetamol', but the command is structurally incomplete: needs a drug name followed by a numeric dose and unit, e.g. 'paracetamol 100 mg'. Please repeat with the missing detail."
}
```
`clarification_message` is built from `set_medication`'s `structure_hint` field
in `domain_config.yaml` — not hardcoded in `stage6_intent_output.py` — so a
new domain adapting this pipeline supplies its own hints per action rather
than editing code.

**Stage 7: User Confirmation**
```
[Glasses display] I understood you want to set medication 'paracetamol',
but the command is structurally incomplete: needs a drug name followed by
a numeric dose and unit, e.g. 'paracetamol 100 mg'. Please repeat with the
missing detail.

No confirm/reject prompt is issued -- there is nothing resolved to confirm
or deny (no dose was ever extracted). The pipeline (--interactive/--voice)
still prompts: "Please repeat the command."

Doctor says: "ok glasses set medication paracetamol 100 mg"
-> Pipeline re-runs -> Stage 3 passes -> proceeds to Stage 4/5/6/7 normally
```

**Why this is better than the previous behaviour:** before this, any Stage 3
failure surfaced only the raw ANTLR diagnostic (e.g. `"no viable alternative
at input 'setmedicationparacetamolfinishmedication'"`) with no indication of
*why* or *what to do about it*. That's distinct from `unrecognized_command`
rejections (Stage 2 returned `UNKNOWN`) and `non_speech_input` rejections
(Stage 1), which get no such message — there, the system has no partial
understanding of the command at all, so there's nothing specific to build a
clarification from.

---

## Supervisor's Test Cases: Hemodialysis

These are the two use cases of the supervisor. The outputs below are real: they come from a stored run of the final evaluation (DeepSeek-V4.1-Flash, first run), command by command, stage by stage. The same two commands are the worked example of the thesis (Section 5.7).

---

### Use Case 1: Doctor says "start HD" (accepted)

The grammar of Ludyga rejects this command, because "HD" is not in the grammar. The pipeline expands the abbreviation, finds the procedure, and accepts.

```
Input:   start HD

Stage 1: start hemodialysis
         (abbreviation expanded: HD -> hemodialysis)
Stage 2: {"action": "start", "entity": "hemodialysis",
          "end_signal": false, "confidence": 0.97}
Stage 3: ok glasses start treatment hemodialysis -> valid (command_type treatment)
Stage 4: Hemodialysis, NCIt:C15248, procedure, exact match, score 1.0
Stage 5: accepted ('procedure' is allowed for 'start')
Stage 6: intent START_PROCEDURE (full output below)
Stage 7: "Start: Hemodialysis. Correct?"   -> "ok glasses, correct" -> confirmed
```

Stage 6 output:
```json
{
  "status": "accepted",
  "intent": "START_PROCEDURE",
  "action": "start",
  "original_command": "start HD",
  "urgency": [],
  "slots": {
    "procedure": {
      "raw_entity": "hemodialysis",
      "canonical": "Hemodialysis",
      "semantic_type": "procedure",
      "ontology_id": "NCIt:C15248",
      "match_method": "exact",
      "score": 1.0,
      "source": "ncit"
    }
  },
  "explanation": {
    "llm_extraction": "Action 'start' extracted with confidence 0.97. Abbreviations expanded: {'HD': 'hemodialysis'}.",
    "grammar_validation": "Frame valid. command_type=treatment.",
    "ontology_evidence": "'hemodialysis' matched ncit concept NCIt:C15248 via exact at score 1.00.",
    "semantic_validation": "'procedure' \u2208 allowed_slot_types for 'start'"
  }
}
```

---

### Use Case 2: "diagnosis hemodialysis" (needs clarification)

The doctor diagnosed hemolysis (a blood disorder), and the recogniser wrote "hemodialysis", a procedure. The grammar accepts this command, because its structure is correct. The pipeline finds that hemodialysis is a procedure and that a diagnosis expects a disease or a symptom.

```
Input:   diagnosis hemodialysis

Stage 1: diagnosis hemodialysis
Stage 2: {"action": "diagnosis", "entity": "hemodialysis",
          "end_signal": false, "confidence": 0.97}
Stage 3: ok glasses diagnosis hemodialysis -> valid (command_type diagnosis)
Stage 4: not found in DOID and HPO (the indexes of a diagnosis);
         found in another index as a procedure (Hemodialysis)
Stage 5: type_mismatch ('procedure'; a diagnosis expects disease,
         symptom, condition, or finding)
Stage 6: status needs_clarification (full output below)
Stage 7: "I heard 'hemodialysis' and recognised it as a procedure,
          not a disease/symptom/condition/finding.
          Did you mean something else? Please repeat or confirm."
```

Stage 6 output:
```json
{
  "status": "needs_clarification",
  "reason": "type_mismatch",
  "intent_attempted": "DIAGNOSIS_DISEASE",
  "action": "diagnosis",
  "original_command": "diagnosis hemodialysis",
  "raw_entity": "hemodialysis",
  "expected_semantic_types": [
    "disease",
    "symptom",
    "condition",
    "finding"
  ],
  "urgency": [],
  "explanation": {
    "llm_extraction": "Action 'diagnosis' extracted with confidence 0.97.",
    "grammar_validation": "Frame valid. command_type=diagnosis.",
    "ontology_evidence": "'hemodialysis' was found as a 'procedure' ('Hemodialysis') in a different ontology index, not in the index searched for this action.",
    "semantic_validation": "'Hemodialysis' is a 'procedure' \u2014 'diagnosis' expects one of ['disease', 'symptom', 'condition', 'finding']"
  },
  "clarification_message": "I heard 'hemodialysis' and recognised it as a procedure, not a disease/symptom/condition/finding. Did you mean something else? Please repeat or confirm.",
  "resolved_entity": {
    "matched_label": "Hemodialysis",
    "concept_id": null,
    "semantic_type": "procedure",
    "match_type": "exact"
  }
}
```

**Why this works:**
- The language model keeps the entity as spoken ("hemodialysis"). It is not asked to judge whether the entity fits the action. That is the job of the ontology lookup and the type check.
- The ontology finds "hemodialysis" exactly, as a procedure, in a different index than the one searched for a diagnosis.
- The type check compares that type with the allowed types of the action and finds a mismatch.
- The pipeline does not accept the command silently, and it does not correct it silently. It says what it found and why it does not fit, and it asks the user.

---

## Evaluation Results

Evaluated on 243 commands (130 valid, 113 invalid) with six systems (four language models; DeepSeek and gpt-oss with 500 and with 4000 tokens), three repetitions each, and the baseline B0 (Ludyga's grammar). The evaluation was made twice. The numbers are from the first run. The full results are in the thesis (Chapter 7), in `evaluation/reports/final/`, and in `evaluation/reports/tables/results_summary.md`.

| Metric | B0 (Ludyga's grammar) | Pipeline (six systems) |
|---|---|---|
| Accuracy | 70.8 % | 90.1 to 95.9 % |
| Valid commands accepted | 46.2 % | 83.1 to 96.9 % |
| Invalid commands wrongly accepted | 0.9 % | 1.8 to 7.1 % |
| Median response time | 0.1 ms | 0.37 to 1.55 s |

The best system is Gemma 4 (accuracy 95.9 %, median 0.37 s). The rise in wrongly accepted invalid commands is the price of the flexibility: Ludyga's grammar rejects almost everything it does not know, including valid commands. The second run gives the same decisions, and the response times depend on the load of the model service.

**Where commands stop** (Gemma 4, first repetition): of the 113 invalid commands, the language model stops 100, Stage 1 stops 5 (not speech), Stage 3 stops 2, and Stage 5 stops 4 (type mismatch). Two are accepted. Of the 130 valid commands, 122 are accepted, 6 are sent to clarification by Stage 5 (entity not found), and 2 are rejected by the language model.

**Remaining false accepts:** two type-mismatch commands are accepted by all systems, because their entity is also the name of a laboratory procedure in the NCI Thesaurus (a name collision, see `docs/findings.md`, Finding 2). Llama 3.3 accepts six more, which are ambiguous or out-of-domain requests that it maps to an action.

---

## How the Pipeline Compares to Baseline

Examples from the evaluation dataset. All six systems give the same outcome on these commands in all three repetitions.

| Command | Ludyga's grammar | Pipeline |
|---|---|---|
| `Activate display` | rejected | accepted: turn on the display |
| `Illustrate the patient's medical history` | rejected | accepted: show, medical history |
| `Pull up the lab results` | rejected | accepted: show, lab results |
| `Stand down from emergency` | rejected | accepted: leave emergency mode |
| `Move to patient Robert Chen` | rejected | accepted: change patient, Robert Chen |
| `diagnosis HTN` | rejected | accepted: diagnosis, hypertension (disease) |
| `start HD` | rejected | accepted: start, hemodialysis (procedure) |
| `diagnosis hemodialysis` | rejected | clarification: a procedure is not a diagnosis |
| `diagnosis peritoneal dialysis` | rejected | clarification: a procedure is not a diagnosis |

---

## UML Diagrams

Architecture diagrams are in [`docs/uml_diagrams.md`](docs/uml_diagrams.md), as images exported from the thesis figures:

1. **Data flow and the seven stages** (informal figures)
2. **Domain independence**: UML component diagram of the pipeline and the domain package
3. **Worked example**: UML sequence diagram of "start HD"
4. **Stage 4 patterns**: two UML class diagrams (entity strategies and ontology loaders; `_STRATEGIES` and `_FORMAT_LOADERS` act as registries)

---
*Document version 3.0, November 2026*
*Master Thesis: Improving Intent Recognition in Grammar-Based Voice Interfaces*
*Anushka Joshi, TU Dresden, Chair of Software Technology*
