# Final Pipeline — Complete Design with Files and Worked Examples
*LLM as structured extractor · ANTLR as frame validator · Ontology as semantic classifier*

---

## Do We Still Need domain_config.yaml?

**Yes — but for two separate purposes:**

1. **Action list → sent to the LLM** (short version): tells the LLM which action names are valid outputs. Without this, the LLM could invent actions like "prescribe" or "record" that aren't in our system.

2. **Slot-type mappings → used by Stage 5 only** (not sent to LLM): tells the semantic validator which entity types each action accepts. This is what catches "diagnosis hemodialysis" — the action is valid, the entity is valid, but the *combination* is semantically wrong.

The domain_config is the contract that ties the whole system together. Without it there is no semantic validation, and the LLM output is unchecked.

---

## Files We Create and Use

| File | Still Needed? | Role |
|---|---|---|
| `domain_config.yaml` | ✅ Yes | Action list for LLM prompt + slot-type rules for semantic validation |
| `llm_system_prompt.txt` | ✅ New | The constrained extraction prompt sent to LLM at startup |
| `abbreviations.json` | ✅ Yes | Abbreviation expansion in ontology lookup stage |
| `MedicalSmartGlassesLexer.g4` | ✅ Yes (simplified) | ANTLR validation grammar — canonical tokens only |
| `MedicalSmartGlassesParser.g4` | ✅ Yes (simplified) | ANTLR frame validation rules |
| `ontology_sources.yaml` | ✅ Yes | Which local ontology files to load at startup |
| `action_synonyms.json` | ❌ Dropped | LLM handles all synonym mapping now |
| Terminator list | ❌ Dropped | LLM's `end_signal` field handles all terminator detection |

---

## Full Pipeline

```
Speech text (from ASR — Whisper / VOSK)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 1: Preprocessing                                     │
│  • Lowercase everything                                     │
│  • Strip punctuation (, . : ! ? — keep hyphens in terms)   │
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
│  ANTLR's role: validate that the LLM's extracted            │
│  {action, entity} fits a structurally valid command frame.  │
│  Guards against LLM hallucination.                          │
│                                                             │
│  Checks:                                                    │
│  • action is a known grammar rule                           │
│  • entity is non-empty for actions that require it          │
│  • entity is empty for entity-free actions (emergency)      │
│  • end_signal is valid in context of the action type        │
│                                                             │
│  Grammar tokens: ONLY canonical forms (no synonyms —        │
│  LLM already normalised)                                    │
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

```yaml
# domain_config.yaml
domain: healthcare
trigger_phrase: "ok glasses"

# SHORT action list — this is what gets sent to the LLM prompt
# Keep descriptions brief to minimise token usage
actions:
  start:
    description: "Initiate a treatment, procedure, or medication"
    allowed_slot_types: [treatment, procedure, medication, shared_session, emergency]

  show:
    description: "Display patient data, test results, vital signs, records, or messages"
    allowed_slot_types: [vital_sign, lab_result, patient_info, medication, document, imaging, messaging]

  diagnosis:
    description: "Record a clinical diagnosis"
    allowed_slot_types: [disease, symptom, condition, finding]

  set_medication:
    description: "Prescribe or record a medication with dose"
    allowed_slot_types: [medication]

  set_note:
    description: "Dictate a free-text clinical note"
    allowed_slot_types: [free_text]
    entity_required: true
    uses_end_signal: true

  stop:
    description: "Discontinue a treatment, medication, or session"
    allowed_slot_types: [treatment, procedure, medication, shared_session, emergency, system]

  turn_on:
    description: "Activate a display or system feature"
    allowed_slot_types: [system]
    entity_required: false

  turn_off:
    description: "Deactivate a display or system feature"
    allowed_slot_types: [system]
    entity_required: false

  continue:
    description: "Resume a paused display or operation"
    allowed_slot_types: [system]
    entity_required: false

  change_patient:
    description: "Switch the active patient context"
    allowed_slot_types: [patient_identifier]

  send_message:
    description: "Compose and send a message to a team member"
    allowed_slot_types: [free_text]
    uses_end_signal: true

  emergency:
    description: "Trigger emergency protocol immediately"
    allowed_slot_types: []
    entity_required: false

  accept_emergency:
    description: "Accept or acknowledge an incoming emergency alert"
    allowed_slot_types: []
    entity_required: false

  leave_emergency:
    description: "Exit emergency mode"
    allowed_slot_types: []
    entity_required: false
```

---

### `llm_system_prompt.txt`

```
You are a structured intent extractor for a medical voice interface running on smart glasses.
Your task is to analyse a spoken command and extract its intent as structured JSON.

Available actions (you MUST use exactly these names):
- start:           initiate a treatment, procedure, or medication
- show:            display patient data, test results, vital signs, records, or messages
- diagnosis:       record a clinical diagnosis
- set_note:        dictate a free-text clinical note
- set_medication:  prescribe or record a medication with dose
- stop:            discontinue a treatment, medication, or session
- turn_on:         activate a display or system feature
- turn_off:        deactivate a display or system feature
- continue:        resume a paused display or operation
- change_patient:  switch active patient
- send_message:    compose a message to a team member
- emergency:       trigger emergency protocol immediately
- accept_emergency: accept or acknowledge an incoming emergency alert
- leave_emergency: exit emergency mode

Output ONLY valid JSON in this exact schema:
{
  "action":      <one of the action names above, or "UNKNOWN">,
  "entity":      <the medical content — entity name, note text, patient name, or data type.
                  Strip filler words like 'please', 'can you', 'I want to', 'the', 'a'.
                  For set_note and send_message: include the dictated content but NOT
                  the end-of-dictation signal words>,
  "end_signal":  <true if the user signalled they are done dictating. Signals include:
                  'finish note', 'complete', 'I am done', "that's it", 'wrap up',
                  'okay stop', 'end of note', 'done documenting', 'alright', 'that is all',
                  'finish documentation', 'finish medication', 'finish message',
                  or ANY phrase indicating the user has finished speaking their content.
                  false if no end signal is present>,
  "confidence":  <0.0 to 1.0>
}

Rules:
1. action must be one of the listed action names. Use UNKNOWN if you cannot confidently identify one.
2. Do NOT change medical entity words (drug names, disease names, procedure names).
3. Do NOT add information not present in the command.
4. entity should be empty string "" for entity-free actions (emergency, turn_on, turn_off,
   continue, accept_emergency, leave_emergency).
5. If action is UNKNOWN, set entity to "" and end_signal to false.
6. Output raw JSON only — no explanation, no markdown, no code fences.
```

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

### `MedicalSmartGlassesLexer.g4` (Simplified Validation Grammar)

```antlr
// Validation grammar — canonical forms only.
// LLM already normalised synonyms before this runs.

lexer grammar MedicalSmartGlassesLexer;

TRIGGER          : 'ok glasses' ;

// Canonical action tokens — one per action in domain_config
START            : 'start' ;
SHOW             : 'show' ;
DIAGNOSIS        : 'diagnosis' ;
SET_NOTE         : 'set note' ;
SET_MED          : 'set medication' ;
STOP             : 'stop' ;
TURN_ON          : 'turn on' ;
TURN_OFF         : 'turn off' ;
CONTINUE         : 'continue' ;
CHANGE_PT        : 'change patient' ;
SEND_MSG         : 'send message' ;
EMERGENCY        : 'emergency' ;
ACCEPT_EMERGENCY : 'accept emergency' ;
LEAVE_EMERGENCY  : 'leave emergency' ;

NUMBER           : [0-9]+ ;
WORD             : [a-zA-Z]+ ('-' [a-zA-Z]+)* ;
WS               : [ \t\r\n]+ -> skip ;
```

### `MedicalSmartGlassesParser.g4` (Simplified Validation Grammar)

```antlr
parser grammar MedicalSmartGlassesParser;
options { tokenVocab = MedicalSmartGlassesLexer; }

// Root — validates structural frame of LLM extraction
command
    : TRIGGER action_with_entity EOF      # entityCommand
    | TRIGGER action_no_entity EOF        # entityFreeCommand
    ;

action_with_entity
    : START entity
    | SHOW entity
    | DIAGNOSIS entity
    | SET_NOTE entity
    | SET_MED entity
    | STOP entity
    | CHANGE_PT entity
    | SEND_MSG entity
    ;

action_no_entity
    : TURN_ON entity?         // optional: "turn on" or "turn on display"
    | TURN_OFF entity?
    | CONTINUE entity?
    | EMERGENCY
    | ACCEPT_EMERGENCY
    | LEAVE_EMERGENCY
    ;

entity : (WORD | NUMBER)+ ;
```

---

### `ontology_sources.yaml`

```yaml
sources:
  disease_ontology:
    format: obo
    path: ./ontologies/doid.obo
    provides_semantic_types: [disease, condition, finding, symptom]
    notes: "Human Disease Ontology — free, no license"

  rxnorm:
    format: sqlite
    path: ./ontologies/rxnorm.db
    provides_semantic_types: [medication]
    notes: "Free NLM download — full drug names, synonyms, brand names"

  snomed_procedures:
    format: tsv
    path: ./ontologies/snomed_procedures.tsv
    provides_semantic_types: [treatment, procedure]
    notes: "Curated SNOMED CT subset — ~500 clinical procedures"

  vital_signs:
    format: json
    path: ./ontologies/vital_signs.json
    provides_semantic_types: [vital_sign, lab_result]
    notes: "Custom taxonomy for measurements and observations"

  patient_info:
    format: json
    path: ./ontologies/patient_info_terms.json
    provides_semantic_types: [patient_info, document, imaging]
    notes: "Terms for patient records, charts, scans, history"
```

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
Strip:   "ok glasses illustrate the patients medical history"  (apostrophe removed)
Trigger: found and stripped
Result:  "illustrate the patients medical history"
Urgency: none
```

**Stage 2 — LLM Structured Extraction**
```
Input to LLM: "illustrate the patients medical history"

LLM reasoning (internal):
  "illustrate" → closest action = "show" (display something)
  Entity after stripping: "patient medical history"
  No end signal.

LLM output:
{
  "action":     "show",
  "entity":     "patient medical history",
  "end_signal": false,
  "confidence": 0.95
}

→ Ludyga's grammar: FAILS ("illustrate" is not a lexer token)
→ LLM: SUCCEEDS (semantic understanding, not token matching)
```

**Stage 3 — ANTLR Frame Validation**
```
Reconstruct canonical: "ok glasses show patient medical history"
Token stream: TRIGGER SHOW WORD WORD WORD
Rule matched: action_with_entity → SHOW entity ✓
Entity "patient medical history" is non-empty ✓
Frame: VALID
```

**Stage 4 — Ontology Lookup**
```
raw_entity = "patient medical history"
action     = "show"
allowed_slot_types for show = [vital_sign, lab_result, patient_info,
                                medication, document, imaging, messaging]

Step 4a — Exact match: search patient_info_terms.json
  "patient medical history" → NOT found exactly

Step 4b — Synonym match:
  "medical history" → found in patient_info_terms.json
  concept_label  = "Medical History"
  semantic_type  = "patient_info"
  match_type     = "synonym"

Result:
  matched_label  = "Medical History"
  semantic_type  = "patient_info"
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
      "raw_entity":     "patient medical history",
      "matched_label":  "Medical History",
      "semantic_type":  "patient_info",
      "match_type":     "synonym",
      "confidence":     1.0
    }
  },
  "explanation": {
    "llm_extraction":      "'Illustrate' mapped to action 'show' (semantic synonym). Entity: 'patient medical history'.",
    "grammar_validation":  "SHOW entity frame validated.",
    "ontology_evidence":   "'Patient medical history' resolved to 'Medical History' (patient_info type).",
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

Step 4a — Exact match in SNOMED procedures TSV:
  "hemodialysis" → SNOMED:34910008, "Hemodialysis", semantic_type = "procedure"
  → maps to our slot type: "treatment"

matched_label  = "Hemodialysis"
concept_id     = "SNOMED:34910008"
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
      "concept_id":    "SNOMED:34910008",
      "semantic_type": "treatment",
      "match_type":    "exact",
      "confidence":    1.0
    }
  },
  "explanation": {
    "llm_extraction":      "Action 'start', entity 'hemodialysis' extracted with high confidence.",
    "grammar_validation":  "START entity frame valid.",
    "ontology_evidence":   "'Hemodialysis' matched SNOMED concept 34910008 as a clinical procedure.",
    "semantic_validation": "Action 'start' permits slot type 'treatment'. Accepted."
  }
}
```

**Stage 7 — User Confirmation**
```
Glasses display: "Start treatment: Hemodialysis (SNOMED:34910008). Correct?"

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
  
  BUT: entity "hemodialysis" IS found in SNOMED procedures (edit distance 0 — exact)
  semantic_type: treatment/procedure
  → This match is OUTSIDE the allowed types for "diagnosis"
  → Store as "cross_type_match" for semantic validator
```

**Stage 5 — Semantic Validation**
```
Scenario: entity found but ONLY as wrong semantic type

action = "diagnosis"
allowed_slot_types = [disease, symptom, condition, finding]

resolved_semantic_type = "treatment" (from SNOMED procedures cross-type match)
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
    "concept_id":     "SNOMED:34910008",
    "semantic_type":  "treatment",
    "match_type":     "exact",
    "found_in":       "procedure ontology"
  },
  "expected_semantic_types": ["disease", "symptom", "condition", "finding"],
  "explanation": {
    "llm_extraction":      "Action 'diagnosis', entity 'hemodialysis' extracted.",
    "grammar_validation":  "DIAGNOSIS entity frame structurally valid.",
    "ontology_evidence":   "'Hemodialysis' was found as SNOMED concept 34910008 — a clinical PROCEDURE, not a disease. No match was found within the disease/symptom ontology at acceptable confidence.",
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
| `"start hemodialysis"` | ❌ Returns "undefined" — not in grammar tokens | ✅ ACCEPTED via SNOMED exact match + Stage 7 confirmation |
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
