# Final Pipeline — Complete Design with Files and Worked Examples
*LLM as structured extractor · ANTLR as frame validator · Ontology as semantic classifier*

---

## Architecture: Single Source of Truth

The pipeline is designed so that **adding a new action requires editing exactly two files**:

1. **`MedicalSmartGlassesParser.g4` (+ Lexer)** — defines the structural rule (entity required? optional? what structure must the entity have?). This is the grammar authority. After editing, regenerate the Python parser with the ANTLR jar.

2. **`domain_config.yaml`** — defines the semantic layer: action description (injected into LLM prompt), `allowed_slot_types` (checked in Stage 5), `grammar_command_type` (validated against grammar at startup), and `canonical_entities` for closed-set actions like `show`.

Everything else is derived automatically:
- `src/domain_loader.py` reads grammar + config at startup and validates they are consistent
- `llm_system_prompt.txt` is a **domain-agnostic template** with `{placeholders}` — healthcare rules and few-shot examples are injected from `domain_config.yaml` at runtime
- Stage 3 runs the real ANTLR4 Python runtime on the grammar — no Python reimplementation of grammar rules

**If grammar and config diverge, the pipeline refuses to start** — the loader raises a clear error naming which action is missing from which file.

---

## Files We Create and Use

| File | Role |
|---|---|
| `MedicalSmartGlassesLexer.g4` | ANTLR lexer — canonical tokens. **Grammar authority — edit here to add actions** |
| `MedicalSmartGlassesParser.g4` | ANTLR parser — command type hierarchy + structural rules (e.g. medication must have INT unit) |
| `src/grammar/` | Generated Python parser from `.g4` files — committed, regenerated when grammar changes |
| `domain_config.yaml` | Semantic layer: descriptions, `allowed_slot_types`, `grammar_command_type`, `canonical_entities`, domain rules, few-shot examples |
| `llm_system_prompt.txt` | Domain-agnostic prompt template with `{placeholders}` — no healthcare content |
| `src/domain_loader.py` | Wires grammar + config + template at startup; validates consistency; builds LLM prompt |
| `abbreviations.json` | Abbreviation expansion (Stage 1) |
| `action_synonyms.json` | ❌ Dropped — LLM handles synonym mapping |
| Terminator list | ❌ Dropped — LLM `end_signal` field handles terminator detection |
| `patient_info_terms.json` synonym lookup | ❌ Dropped — LLM maps to canonical entities defined in `domain_config.yaml` |

---

## Full Pipeline

```
Speech text (from ASR — Whisper / VOSK)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 1: Preprocessing                                     │
│  • Lowercase everything                                     │
│  • Strip punctuation — hyphens replaced with spaces         │
│    ("non-invasive" → "non invasive"; consistent with Stage 4)│
│  • Normalise trigger: "ok" → "okay", detect "okay glasses"  │
│  • Remove trigger from string, keep remainder               │
│  • Scan for urgency flags (stat, urgent, immediately)       │
│    → store separately, remove from string                   │
│  Result: clean command text, urgency metadata               │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 2: LLM Structured Extraction                         │
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
│  STAGE 3: ANTLR Frame Validation                            │
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
│  STAGE 4: Ontology / Taxonomy Lookup                        │
│                                                             │
│  For entity-bearing commands (start, show, diagnosis, stop) │
│  Ordered resolution:                                        │
│    4a. Exact label match (local ontology files)             │
│    4b. Synonym match (synonymous descriptions)              │
│    4c. Abbreviation expansion → re-run 4a + 4b             │
│    4d. Fuzzy match — ONLY within semantic types that        │
│        the action ALLOWS (from domain_config). Edit         │
│        distance ≤ 2 only.                                   │
│                                                             │
│  For free-text commands (set_note, send_message):           │
│    → Skip ontology. Store entity as raw text.               │
│                                                             │
│  For set_medication:                                        │
│    → Run drug name through RxNorm lookup. Dosage/timing     │
│       stored as structured free text.                       │
│                                                             │
│  Output: resolved concept + semantic_type + match_type      │
└─────────────────────────────────────────────────────────────┘
    │
    │ Entity not found ────────────────────────→ ENTITY_NOT_FOUND + clarification
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 5: Semantic Validation                               │
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
│  STAGE 6: Intent JSON + Explanation                         │
│  Full audit trail of every stage's decision                 │
│  Clarification message if status ≠ accepted                 │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 7: User Confirmation  ★ [designed — implement        │
│                                  if time allows]            │
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

Holds the **semantic layer only** — structure lives in the grammar. Key fields per action:

- `grammar_command_type`: links this action to a parser rule. Validated at startup.
- `description`: injected into the LLM prompt.
- `allowed_slot_types`: checked in Stage 5 semantic validation.
- `canonical_entities` *(show only)*: closed-set record categories matched at Stage 4. The LLM preserves the entity as spoken ("patient history"); Stage 4 matches it against this list to resolve the canonical name ("Medical History") and semantic_type. Replaces `patient_info_terms.json` — no synonym file needed.

```yaml
domain: healthcare
domain_description: "medical voice interface running on smart glasses worn by clinicians"
trigger_phrase: "ok glasses"

actions:
  show:
    grammar_command_type: request_data
    description: "Display patient data, test results, vital signs, records, or messages"
    allowed_slot_types: [vital_sign, lab_result, patient_info, medication, document, imaging, messaging, procedure]
    canonical_entities:          # grouped by semantic_type → Stage 4 reads type from here
      patient_info:
        - Medical History
        - Medication List
        - Allergy List
        - Problem List
        - Vital Signs Record
        - Patient Demographics
      document:
        - Discharge Summary
        - Progress Notes
        - Referral Letter
        - Consultation Note
        - Operative Note
        - Lab Report
      messaging:
        - Messages

  diagnosis:
    grammar_command_type: diagnosis
    description: "Record a clinical diagnosis"
    allowed_slot_types: [disease, symptom, condition, finding]

  set_medication:
    grammar_command_type: set_medication
    description: "Prescribe medication — entity MUST include drug name, dose, and unit"
    allowed_slot_types: [medication]

  # ... (all 14 actions, each with grammar_command_type)

domain_rules:
  - "This is a MEDICAL clinical interface. Non-medical commands → UNKNOWN."
  - "If 'start'/'begin'/'trigger' appears near 'emergency', action is ALWAYS 'emergency'."
  - "set_medication entity MUST contain drug name, dose number, and unit."
  - "For show commands, extract the data type exactly as spoken — do not normalise or rename it."
  # ...

few_shot_examples:
  - input: "show patient history"
    output: '{"action":"show","entity":"patient history","end_signal":false,"confidence":0.97}'
  - input: "set medication hypertension"
    output: '{"action":"set_medication","entity":"hypertension","end_signal":false,"confidence":0.95}'
  # ...
```

---

### `llm_system_prompt.txt`

A **domain-agnostic template** — contains no action names, no healthcare examples, no healthcare rules. All domain-specific content is injected at runtime by `src/domain_loader.py` from `domain_config.yaml`.

```
You are a structured intent extractor for a {DOMAIN_DESCRIPTION}.
Your task is to understand the INTENT behind a spoken command and extract it as structured JSON.

Available actions (you MUST use exactly these names):
{ACTION_LIST}

Output ONLY valid JSON in this exact schema:
{
  "action":      <one of the action names above, or "UNKNOWN">,
  "entity":      <the relevant content. Strip filler words.
                  For set_note and send_message: full dictated content, NOT end-signal words.
                  For entity-free actions: "">,
  "end_signal":  <true if user signalled they are done dictating, false otherwise>,
  "confidence":  <0.0 to 1.0>
}

Domain-specific rules:
{DOMAIN_RULES}

Generic rules:
1. Identify intent from meaning, not from specific trigger words.
2. Do NOT judge entity type — preserve as spoken. Semantic validation is a later stage.
3. Do NOT invent information not present in the command.
4. Do NOT modify medical terms.
5. Output raw JSON only.

Examples:
{FEW_SHOT_EXAMPLES}
```

`{ACTION_LIST}`, `{DOMAIN_RULES}`, and `{FEW_SHOT_EXAMPLES}` are filled from `domain_config.yaml`. Changing the domain = change `domain_config.yaml` only; the prompt template is untouched.

---

### `src/domain_loader.py`

Runs once at startup. Wires grammar → config → prompt and enforces consistency.

```
1. Parse MedicalSmartGlassesParser.g4
   → extract command type rule names:
     {glasses_command, request_data, protocol, set_medication,
      stop_cmd, request_patient, message, diagnosis}

2. Load domain_config.yaml
   → for each action, check grammar_command_type ∈ grammar rules
   → if mismatch: raise ValueError with the name of the missing rule
   → pipeline CANNOT start with inconsistent grammar + config

3. Fill llm_system_prompt.txt template:
   {ACTION_LIST}        ← action name + description from config
                          (canonical_entities NOT injected into prompt — used by Stage 4)
   {DOMAIN_RULES}       ← domain_rules list from config
   {FEW_SHOT_EXAMPLES}  ← few_shot_examples from config

4. Return: system_prompt, action_config, valid_actions, trigger_phrase
```

This is the enforced single-file change contract: add a new action to the grammar and config, loader validates both and builds the prompt — Stage 2 and Stage 3 pick it up automatically.

---

### `abbreviations.json`

```json
{
  "BP":   "blood pressure",
  "HR":   "heart rate",
  "RR":   "respiratory rate",
  "SpO2": "oxygen saturation",
  "ECG":  "electrocardiogram",
  "EKG":  "electrocardiogram",
  "CBC":  "complete blood count",
  "BMP":  "basic metabolic panel",
  "RRT":  "renal replacement therapy",
  "HD":   "hemodialysis",
  "PD":   "peritoneal dialysis",
  "IV":   "intravenous",
  "UTI":  "urinary tract infection",
  "MI":   "myocardial infarction",
  "CHF":  "congestive heart failure",
  "COPD": "chronic obstructive pulmonary disease",
  "DM":   "diabetes mellitus",
  "HTN":  "hypertension",
  "DVT":  "deep vein thrombosis",
  "MRI":  "magnetic resonance imaging",
  "CT":   "computed tomography",
  "A1c":  "glycated hemoglobin",
  "INR":  "international normalised ratio",
  "WBC":  "white blood cell count",
  "GFR":  "glomerular filtration rate"
}
```

---

### `MedicalSmartGlassesLexer.g4` and `MedicalSmartGlassesParser.g4`

Extended from Ludyga's baseline grammar. Full content in the repo root files. Key design points:

- **Lexer**: individual keyword tokens (`START`, `SHOW`, `DIAGNOSIS`, etc.) — no compound action tokens. Compound action names (e.g. `change_patient`) are combinations of tokens, resolved by the parser rules. `WORD: [a-zA-Z]+` — Ludyga-identical; hyphens handled by Stage 1 preprocessing.
- **Parser**: preserves Ludyga's command type hierarchy. Adds `diagnosis` and `treatment` as new types. `stop_cmd` accepts optional entity (for "stop hemodialysis"). `set_medication` retains the `medicine INT unit` enforcement from Ludyga.

The Python parser (`src/grammar/`) is generated from these files using:
```bash
java -jar antlr4-4.13.2-complete.jar -Dlanguage=Python3 -o src/grammar \
  MedicalSmartGlassesLexer.g4 MedicalSmartGlassesParser.g4
```

Changes from Ludyga baseline are documented in `grammar_extend.md`.

---

### Design Decision: Why ANTLR Remains in the Pipeline

This thesis extends a grammar-based pipeline. ANTLR is not decorative — it performs structural validation that the LLM cannot and should not do.

**Division of responsibility:**

| Task | Who does it | Why |
|---|---|---|
| Synonym normalisation | LLM (Stage 2) | LLM understands meaning; ANTLR lexer lists are always incomplete |
| Filler word removal | LLM (Stage 2) | LLM generalises; hardcoded lists cannot |
| Action identification | LLM (Stage 2) | LLM maps natural language to canonical action names |
| Structural validation | ANTLR (Stage 3) | Grammar is the formal specification; Python reimplementation drifts |
| Semantic type checking | Ontology + Stage 5 | Neither LLM nor grammar knows all medical terminology |

**What ANTLR catches that the LLM cannot:**

The critical example is `set_medication`. A clinician says "set medication paracetamol" without specifying a dose. The LLM extracts `{action: set_medication, entity: "paracetamol"}` — this is the correct extraction of what was said. But the grammar rule:

```antlr
medication: medicine INT unit | INT unit OF medicine ;
```

requires a numeric dose and a unit. ANTLR parses the reconstructed canonical string and returns a `PARSE_ERROR` — the command is structurally incomplete and must be rejected before it reaches the patient record system.

A Python dictionary lookup (`if entity: valid`) would pass this. ANTLR does not.

**Why the grammar is the single source of truth for structure:**

Previously, grammar rules were duplicated in a Python dict inside `stage3_frame_validate.py`. Any change to the grammar required changing both files — with no enforcement that they stayed in sync. The current design:

- Stage 3 (`stage3_antlr_validate.py`) runs the real ANTLR4 Python runtime on the generated parser
- `src/domain_loader.py` validates at startup that `domain_config.yaml` actions map to grammar rules
- If grammar and config diverge, the pipeline refuses to start

**What ANTLR does NOT do (by design):**

ANTLR does not see raw speech. By the time ANTLR runs, the LLM has already normalised the input to a canonical `{action, entity}` pair. Stage 3 reconstructs a parseable string from this pair and feeds it to ANTLR. ANTLR validates structure; it does not identify actions or map synonyms — that is and should remain the LLM's job.

**Grammar extension from Ludyga baseline:**

The grammar extends Ludyga's original (`baseline_ludyga/`). Changes are documented in `grammar_extend.md`. The command type hierarchy (glasses_command, request_data, protocol, set_medication, request_patient, message) is preserved; `diagnosis` is added as a new type.

---

### `ontology_sources.yaml`

```yaml
sources:
  disease_ontology:
    format: obo
    path: ./ontologies/doid.obo
    provides_semantic_types: [disease, condition, finding]
    semantic_type_assignment: hardcoded  # every term in DOID = disease by definition
    notes: "Human Disease Ontology — CC0, no license required"

  phenotype_ontology:
    format: obo
    path: ./ontologies/hp.obo
    provides_semantic_types: [symptom, clinical_finding]
    semantic_type_assignment: hardcoded  # every term in HPO = symptom/phenotype
    notes: "Human Phenotype Ontology — free for academic use"

  rxnorm:
    format: rrf
    path: ./ontologies/RXNCONSO.RRF
    provides_semantic_types: [medication]
    semantic_type_assignment: hardcoded  # every term in RxNorm = medication
    notes: "NLM Prescribable Content — no UMLS license required"

  ncit_procedures:
    format: flat
    path: ./ontologies/Thesaurus.txt
    provides_semantic_types: [treatment, procedure]
    semantic_type_assignment: column     # filter rows where semantic_type contains "Therapeutic or Preventive Procedure" or "Diagnostic Procedure"
    notes: "NCI Thesaurus flat file — free, NCI Terms of Use. Replaces SNOMED CT (license rejected). Vital signs NOT sourced from NCIt — blood pressure appears as Finding not vital_sign in NCIt."

  # patient_info_terms.json is NOT used as an ontology lookup source.
  # Stage 4 matches the raw entity ("patient history", "discharge letter") against the
  # canonical_entities list in domain_config.yaml to resolve canonical name + semantic_type.
  # The LLM preserves the entity as spoken; resolution is Stage 4's responsibility.
```

---

### Ontology Parsing and Semantic Type Assignment

OBO files (DOID, HPO) do **not** contain a semantic type field. The type is assigned based on which file the term comes from — every term in DOID is a disease by definition, every term in HPO is a symptom. NCIt is the exception: it covers all concept types in one file, so we use its `semantic_type` column to filter only procedure/vital sign rows at load time.

**Parsing happens once at startup** — all files are read and merged into a single in-memory index:

```
"hemolysis"      → {semantic_type: "disease",   concept_id: "DOID:3680",   source: "doid"}
"hemodialysis"   → {semantic_type: "procedure", concept_id: "NCIt:C15248", source: "ncit"}
"aspirin"        → {semantic_type: "medication", concept_id: "RxCUI:1191", source: "rxnorm"}
```

Synonyms are indexed as separate keys pointing to the same concept. Every Stage 4 query is then an O(1) dict lookup — no file I/O at query time. This keeps per-command latency at < 1ms for the ontology step, acceptable for real-time AR glasses use.


---
---

# Worked Examples from the Command List

---

## Example 1: `"ok glasses Illustrate the patient's medical history"`
*From u13–u14 show commands. Fails in Ludyga's grammar because "illustrate" is not a token. Shows LLM synonym handling.*

---

**Stage 1 — Preprocessing**
```
Input:   "ok glasses Illustrate the patient's medical history"
Lower:   "ok glasses illustrate the patient's medical history"
Strip:   "ok glasses illustrate the patients medical history"  (apostrophe, hyphens → space)
Trigger: found and stripped
Result:  "illustrate the patients medical history"
Urgency: none
```

**Stage 2 — LLM Structured Extraction**
```
Input to LLM: "illustrate the patients medical history"

LLM reasoning (internal):
  "illustrate" → closest action = "show" (display something)
  "patients medical history" → entity preserved as spoken
  No end signal.

LLM output:
{
  "action":     "show",
  "entity":     "patients medical history",
  "end_signal": false,
  "confidence": 0.95
}

→ Ludyga's grammar: FAILS ("illustrate" is not a lexer token)
→ LLM: SUCCEEDS — maps action synonym, entity preserved as spoken
```

**Stage 3 — ANTLR Frame Validation**
```
Reconstruct canonical: "ok glasses show patients medical history"
Rule matched: request_data → u14: SHOW data ✓
data "patients medical history" is non-empty ✓
command_type = "request_data"
Frame: VALID
```

**Stage 4 — Ontology / Canonical Lookup**
```
entity = "patients medical history"
action = "show"

Match against canonical_entities in domain_config.yaml (no external file):
  "patients medical history" → fuzzy match → "Medical History" (patient_info group)

Result:
  raw_entity     = "patients medical history"
  canonical      = "Medical History"
  semantic_type  = "patient_info"
  match_type     = "canonical"
  confidence     = 1.0
```

**Stage 5 — Semantic Validation**
```
action="show" + semantic_type="patient_info"
domain_config: show allows [vital_sign, lab_result, patient_info, ...]
"patient_info" ∈ allowed? → YES ✓
Result: ACCEPTED
```

**Stage 6 — Intent JSON**
```json
{
  "status": "accepted",
  "intent": "SHOW_PATIENT_INFO",
  "original_command": "ok glasses Illustrate the patient's medical history",
  "action": "show",
  "slots": {
    "patient_info": {
      "raw_entity":    "patients medical history",
      "canonical":     "Medical History",
      "semantic_type": "patient_info",
      "match_type":    "canonical",
      "confidence":    1.0
    }
  },
  "explanation": {
    "llm_extraction":      "'Illustrate' mapped to action 'show'. Entity 'patients medical history' preserved as spoken — canonical resolution is Stage 4's responsibility.",
    "grammar_validation":  "SHOW data frame valid. command_type=request_data.",
    "ontology_evidence":   "'patients medical history' matched to canonical 'Medical History' (patient_info) via domain_config canonical_entities. No external file lookup.",
    "semantic_validation": "Action 'show' permits slot type 'patient_info'. Accepted."
  }
}
```

**Stage 7 — User Confirmation**
```
Glasses display: "Show: Patient Medical History. Correct?"

User says:  "ok glasses, correct"
→ CONFIRMED
→ Command executed: patient medical history record displayed
```

---

## Example 2: `"ok glasses Note: Upon examination, there was tenderness in the lower abdomen and a palpable mass"`
*From u15 set note commands. No explicit terminator — note is still open. Shows LLM handling notes without end signal.*

---

**Stage 1 — Preprocessing**
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

**Stage 2 — LLM Structured Extraction**
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

**Stage 3 — ANTLR Frame Validation**
```
Canonical: "ok glasses set note upon examination there was tenderness..."
Rule: SET_NOTE entity → ✓
Entity non-empty ✓
end_signal=false → note session open, more input expected
Frame: VALID (partial — awaiting close)
```

**Stage 4 — Ontology Lookup**
```
action = "set_note" → free_text slot type
→ Skip ontology entirely
Entity stored as raw clinical text
```

**Stage 5 — Semantic Validation**
```
set_note + free_text → ✓ allowed
```

**Stage 6 — Intent JSON**
```json
{
  "status": "accepted_partial",
  "intent": "SET_NOTE_OPEN",
  "action": "set_note",
  "slots": {
    "free_text": {
      "content":    "upon examination there was tenderness in the lower abdomen and a palpable mass",
      "end_signal": false,
      "note_state": "open — awaiting end-of-dictation signal"
    }
  },
  "explanation": {
    "llm_extraction":      "Action 'set_note' identified. No end-of-dictation signal detected. Note remains open.",
    "grammar_validation":  "SET_NOTE entity frame valid.",
    "ontology_evidence":   "Free-text slot — no ontology lookup.",
    "semantic_validation": "set_note allows free_text. Accepted as partial (open note)."
  }
}
```

**Stage 7 — User Confirmation**
```
Glasses display: "Recording note — still open. Waiting for end signal."

→ No confirmation prompt issued while note session is open.
  User confirmation will be triggered once end_signal=true is received
  and the full note content is ready for commit.
```

---

## Example 3: `"ok glasses Set medication aspirin 100 milligrams and clopidogrel 75 milligrams, finish medication"`
*From u16–u17 medication commands. Shows LLM terminator detection ("finish medication") and structured medication parsing.*

---

**Stage 1 — Preprocessing**
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

**Stage 2 — LLM Structured Extraction**
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

**Stage 3 — ANTLR Frame Validation**
```
Canonical: "ok glasses set medication aspirin 100 milligrams and
            clopidogrel 75 milligrams"
Rule: SET_MED entity ✓
Entity non-empty ✓
end_signal=true → prescription closed ✓
Frame: VALID
```

**Stage 4 — Ontology Lookup (RxNorm)**
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

**Stage 5 — Semantic Validation**
```
set_medication + medication → ✓ allowed
```

**Stage 6 — Intent JSON**
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
    "llm_extraction":      "'Set medication' mapped to action 'set_medication'. End signal 'finish medication' detected — excluded from entity.",
    "grammar_validation":  "SET_MED entity frame valid. Prescription closed (end_signal=true).",
    "ontology_evidence":   "Aspirin (RxCUI:1191) and Clopidogrel (RxCUI:41493) confirmed as medications via RxNorm.",
    "semantic_validation": "set_medication allows slot type 'medication'. Both drugs confirmed. Accepted."
  }
}
```

**Stage 7 — User Confirmation**
```
Glasses display:
  "Set medication:
     Aspirin 100 mg
     Clopidogrel 75 mg
   Correct?"

User says: "ok glasses, correct"
→ CONFIRMED
→ Prescription committed to patient record

Note: Medication commands are high-criticality — auto-timeout confirmation
is disabled by default. Explicit verbal confirmation is always required.
```

---

## Supervisor's Test Cases — Hemolysis vs Hemodialysis

These are the two use cases directly from the supervisor's email. This is the most important demonstration.

---

### Supervisor Use Case 1: Doctor says "start hemodialysis"

Input: `"ok glasses start hemodialysis"`

The original grammar returned "undefined" because hemodialysis was not in the grammar's treatment tokens. Our pipeline:

**Stage 1 — Preprocessing**
```
Result: "start hemodialysis"
```

**Stage 2 — LLM Extraction**
```
{
  "action":     "start",
  "entity":     "hemodialysis",
  "end_signal": false,
  "confidence": 0.99
}
```

**Stage 4 — Ontology Lookup**
```
raw_entity = "hemodialysis"
allowed_slot_types for "start" = [treatment, procedure, medication, shared_session, emergency]

Step 4a — Exact match in NCIt (Thesaurus.txt):
  "hemodialysis" → NCIt:C15248, "Hemodialysis", semantic_type = "procedure"
  → maps to our slot type: "treatment"

matched_label  = "Hemodialysis"
concept_id     = "NCIt:C15248"
semantic_type  = "treatment"
match_type     = "exact"
confidence     = 1.0
```

**Stage 5 — Semantic Validation**
```
start + treatment → allowed ✓
```

**Stage 6 — Output**
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

**Stage 7 — User Confirmation**
```
Glasses display: "Start treatment: Hemodialysis (NCIt:C15248). Correct?"

User says: "ok glasses, correct"
→ CONFIRMED
→ Nurses informed; hemodialysis treatment initiated
```

**Result: CORRECTLY ACCEPTED → nurses are informed of the treatment.**
Supervisor's baseline returned "undefined". Our pipeline returns a fully resolved intent.

---

### Supervisor Use Case 2: Doctor says "diagnosis hemolysis" — ASR returns "hemodialysis"

This is the critical safety test. The doctor diagnosed hemolysis (a blood disorder). The ASR transcribed it as hemodialysis (a procedure). The system must catch this mismatch.

Input: `"ok glasses diagnosis hemodialysis"` ← ASR error

**Stage 1 — Preprocessing**
```
Result: "diagnosis hemodialysis"
```

**Stage 2 — LLM Extraction**
```
{
  "action":     "diagnosis",
  "entity":     "hemodialysis",
  "end_signal": false,
  "confidence": 0.97
}

Note: The LLM correctly maps "diagnosis" to action="diagnosis".
It does NOT second-guess the entity word — it preserves "hemodialysis"
exactly as spoken. The LLM is NOT responsible for catching this error.
That is the ontology + semantic validator's job.
```

**Stage 3 — ANTLR Validation**
```
DIAGNOSIS entity → ✓ valid frame
```

**Stage 4 — Ontology Lookup**
```
raw_entity = "hemodialysis"
action     = "diagnosis"
allowed_slot_types for "diagnosis" = [disease, symptom, condition, finding]

Step 4a — Exact match:
  Search disease_ontology (DOID) for "hemodialysis" → NOT FOUND
  (Hemodialysis is not a disease)

Step 4b — Synonym match in DOID: NOT FOUND

Step 4c — Abbreviation expansion: not applicable

Step 4d — Fuzzy match WITHIN allowed types only:
  Scope: disease, symptom, condition, finding
  (Procedure ontology is NOT searched — "diagnosis" does not allow procedures)

  Fuzzy candidates within disease/finding scope:
    "hemolysis" (DOID:3680) → edit_distance = 4  (haemodialysis vs hemolysis)
    "haemolysis"            → edit_distance = 3
    → edit_distance > 2 threshold → NOT auto-matched

  No match within allowed semantic types at acceptable confidence.
  
  BUT: entity "hemodialysis" IS found in NCIt procedures (edit distance 0 — exact)
  semantic_type: treatment/procedure
  → This match is OUTSIDE the allowed types for "diagnosis"
  → Store as "cross_type_match" for semantic validator
```

**Stage 5 — Semantic Validation**
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

**Stage 6 — Clarification Output**
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
    "ontology_evidence":   "'Hemodialysis' was found as NCIt concept C15248 — a clinical PROCEDURE, not a disease. No match was found within the disease/symptom ontology at acceptable confidence.",
    "semantic_validation": "Action 'diagnosis' expects a disease or symptom. 'Hemodialysis' resolves as a treatment/procedure. Mismatch detected."
  },
  "clarification_message": "I heard 'hemodialysis' and recognised it as a medical procedure, not a diagnosis. A diagnosis should be a disease or symptom. Did you mean to diagnose 'hemolysis' (a blood condition)? Please repeat or confirm."
}
```

**Stage 7 — User Confirmation (for NEEDS_CLARIFICATION path)**
```
Glasses display:
  "⚠ I heard 'hemodialysis' — this is a procedure, not a diagnosis.
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
- The ontology found "hemodialysis" exactly — as a *procedure*
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

Output: ACCEPTED, DIAGNOSIS_DISEASE with SNOMED/ICD-10 code for documentation.

---

## How the Pipeline Compares to Baseline

| Scenario | Ludyga Baseline | Our Pipeline |
|---|---|---|
| `"start hemodialysis"` | ❌ Returns "undefined" — not in grammar tokens | ✅ ACCEPTED via NCIt exact match + Stage 7 confirmation |
| `"illustrate medical history"` | ❌ Fails — "illustrate" not a token | ✅ ACCEPTED via LLM semantic mapping + Stage 7 confirmation |
| `"diagnosis hemodialysis"` (ASR error for hemolysis) | ❌ Returns "undefined" or wrong result | ✅ NEEDS_CLARIFICATION with explanation + Stage 7 re-prompt loop |
| `"note: on examination... I'm done"` | ❌ Fails — "I'm done" not a FINISH token | ✅ ACCEPTED via LLM end_signal detection |
| `"set medication aspirin... finish medication"` | ✅ Handled — has exact FINISH MEDICATION token | ✅ Handled — LLM end_signal + Stage 7 explicit confirmation (medication = high-criticality) |
| `"code red initiate immediately"` | ❌ Fails — "code red" not a token | ✅ LLM maps to emergency action (Stage 7 timeout = 0 for emergencies) |
| `"diagnosis hemolysis"` (correct) | ❌ Fails — "hemolysis" not a grammar token | ✅ ACCEPTED via DOID exact match + Stage 7 confirmation |

---
*Document version 1.0 — May 2026*
*Master Thesis: Improving Intent Recognition in Grammar-Based Voice Interfaces*
*Anushka Joshi — TU Dresden, Chair of Software Technology*
