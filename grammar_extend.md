# Grammar Extension: Changes from Ludyga Baseline

*Master Thesis: Improving Intent Recognition in Grammar-Based Voice Interfaces*
*Anushka Joshi, TU Dresden, Chair of Software Technology*

---

## Why We Cannot Use Ludyga's Grammar As-Is

Ludyga's grammar covers the use cases documented in the original system. It does **not** cover three command types that the thesis introduces:

**1. `diagnosis` -- does not exist in Ludyga's grammar**

`DIAGNOSIS` is not a lexer token. There is no parser rule for a diagnosis command. Running `"ok glasses diagnosis hemolysis"` through Ludyga's grammar produces a parse error. His system returned "undefined" for any diagnosis command; this is one of the core gaps the thesis addresses.

**2. `start treatment` -- does not exist**

In Ludyga's grammar, `START` begins four commands, each recognised by its second keyword: start emergency, start documentation, start message, and start medication, for example:
```antlr
u09: START EMERGENCY;                          // emergency launch
trig_medication: (SET | START);                // medication trigger
```

There is no rule for "start hemodialysis" (start a clinical treatment or procedure). Again, Ludyga's system returned "undefined"; this is the central motivating example of the thesis.

**3. `stop entity` -- does not exist**

```antlr
u01: STOP (FRAME)?;   // Ludyga: only "stop" or "stop frame"
```

"Stop hemodialysis", "stop the infusion" have no matching rule. Ludyga's `stop` only stops the AR display frame.

---

These three gaps are exactly what the thesis solves. The LLM (Stage 2) can now handle all three, but Stage 3 must validate them; this required extending the grammar. The extension is `grammar_extend.md` (this file), not a grammar replacement.

---

## Source

Ludyga's baseline grammar is in `baseline_ludyga/`:
- `baseline_ludyga/MedicalSmartGlassesLexer.g4`
- `baseline_ludyga/MedicalSmartGlassesParser.g4`

Our extended grammar is at the repo root:
- `MedicalSmartGlassesLexer.g4`
- `MedicalSmartGlassesParser.g4`

The baseline grammar was **not modified**. Our grammar copies it as a starting point and extends it.

---

## What Ludyga's Grammar Defines (Unchanged in Our Version)

| Rule | Description |
|---|---|
| `glasses_command` | Display frame control (stop, continue) and system switches (turn on/off) |
| `request_data` (u13/u14) | `SHOW data [OF LAST N TIME_UNIT]` |
| `protocol` (u15) | `SET NOTE … FINISH NOTE` / `START DOCUMENTATION … FINISH DOCUMENTATION` |
| `set_medication` (u16/u17) | `SET/START MEDICATION medicine INT unit FINISH MEDICATION [AT timestamp]` |
| `request_patient` (u18) | `CHANGE PATIENT TO patient_name` |
| `message` (u20) | `START [NEW] MESSAGE note FINISH [NEW] MESSAGE [AND] SEND IT TO patient_name` -- **preserved exactly** |
| `emergency` (u09–u12) | `START/FINISH/ACCEPT/LEAVE EMERGENCY [MODE]` |

The **medication rule is explicitly preserved**:
```antlr
medication: (medicine INT unit | INT unit OF medicine);
```
This enforces that `set_medication` always requires a drug name, a numeric dose, and a unit. "Set medication paracetamol" (no dose/unit) is a parse error; this is the structural validation the supervisor required.

---

## Extensions We Added

### 1. `diagnosis` command type: new (u21)

**Ludyga had:** no diagnosis command. The system only supported data display, note-taking, and treatment/medication actions.

**We added:**
```antlr
// Parser
diagnosis : u21 ;
u21       : DIAGNOSIS any_text ;
any_text  : . (.)*? ;       // one or more tokens of any kind

// Lexer
DIAGNOSIS : 'diagnosis' ;
```

**Why:** The thesis use case requires distinguishing `diagnosis hemolysis` (recording a finding) from `start hemodialysis` (initiating a procedure). Without a dedicated `diagnosis` command type, Stage 5 cannot apply different `allowed_slot_types` to the two actions.

---

### 2. `treatment` command type: new (u22)

**Ludyga had:** `START` only in commands with a fixed second keyword (emergency, documentation, message, medication), for example:
- `u09: START EMERGENCY` (emergency launch)
- `trig_medication: SET | START` (medication trigger)

There was no rule for "start hemodialysis" as a treatment/procedure.

**We added a separate `treatment` command type, with a fixed keyword:**
```antlr
// Parser: new top-level command type, separate from protocol
command
    : TRIGGER (... | protocol | treatment | ...) EOF ;

protocol  : u15 ;          // set_note (documentation); unchanged from Ludyga
treatment : u22 ;          // start treatment/procedure (new)
u22       : START TREATMENT any_text ;

// Lexer
TREATMENT : 'treatment' ;
```

**Why a fixed keyword:** each of Ludyga's `START` commands (emergency, documentation, message, medication) is recognised by a second keyword, and the open text of a message or documentation lies between fixed keywords. A rule `START any_text` has no such keyword and overlaps with all of them. Because `treatment` is tried before `set_medication` and `message`, it would accept a message without a recipient and a medication without a dose as a treatment, and the parser would never check them (tested with a temporary grammar). Placing it last does not help: complete messages would parse as messages, but incomplete ones would fall through to the treatment rule. The same happens with a strict rule `entity : (WORD | INT)+`, which also refuses entities that contain a grammar keyword (`at`, `of`, `frame`) and needed a replacement of keywords by `x`.

**The user never says "treatment".** Stage 3 rebuilds the command from the template of the action in `domain_config.yaml` (`"{trigger} start treatment {entity}"`); the keyword exists only in the rebuilt command. The entity itself may contain any words.

**Why separate from `protocol`:** In clinical language, `protocol` means documentation (set_note, start documentation). "Start hemodialysis" is initiating a procedure -- a different backend action. Keeping them separate means `command_type` correctly returns `treatment` (not `protocol`) for "start haemodialysis", allowing the backend to route it correctly.

ANTLR4 tries the alternatives of `command` in order and uses the first that matches the whole command: `glasses_command` (which includes `START EMERGENCY`) and `protocol` (`START DOCUMENTATION`) come before `treatment`, so "start emergency" and "start documentation" still route to them.

---

### 3. `stop_cmd` now accepts optional entity: changed (u01)

**Ludyga had:**
```antlr
u01: STOP (FRAME)?;
```
"stop" or "stop frame"; FRAME was the only allowed entity, as a keyword.

**We changed to:**
```antlr
stop_cmd : u01 ;
u01      : STOP any_text? ;
```

**Why:** The pipeline needs `stop hemodialysis`, `stop vasopressors`, `stop infusion`. These are free-text entities, not grammar keywords. Making entity optional also preserves "stop" bare (stop current display).

---

## Command Type Hierarchy (Extended from Ludyga)

```
command
  ├── glasses_command      → display control, emergency, show messages
  ├── request_data         → show <data> [of last N unit]
  ├── protocol             -> set_note (documentation only; unchanged from Ludyga)
  ├── treatment            → start <treatment/procedure>  ← NEW
  ├── stop_cmd             → stop [entity]
  ├── set_medication       → set medication medicine INT unit
  ├── request_patient      → change patient to <name>
  ├── message              → send message <content>
  └── diagnosis            → diagnosis <entity>  ← NEW
```

The command types are returned by Stage 3 (`command_type` field) and can be used by the backend for routing; e.g. all `glasses_command` types control the AR display, all `request_data` types query the patient record system.

---

## Grammar → domain_config Wiring

Each action in `domain_config.yaml` declares a `grammar_command_type` that maps it to a parser rule:

| domain_config action | grammar_command_type |
|---|---|
| `show` | `request_data` |
| `diagnosis` | `diagnosis` |
| `start` | `treatment` |
| `stop` | `stop_cmd` |
| `set_medication` | `set_medication` |
| `set_note` | `protocol` |
| `turn_on`, `turn_off`, `continue`, `emergency`, `accept_emergency`, `leave_emergency` | `glasses_command` |
| `change_patient` | `request_patient` |
| `send_message` | `message` |

`src/domain_loader.py` validates this mapping at startup; if an action is added to `domain_config.yaml` without a matching rule in the parser grammar, the pipeline refuses to start with a clear error message.

---

## Regenerating the Python Parser

After any change to the `.g4` files, run:

```bash
java -jar /tmp/antlr4-4.13.2-complete.jar -Dlanguage=Python3 -o src/grammar \
  MedicalSmartGlassesLexer.g4 MedicalSmartGlassesParser.g4
```

The generated files in `src/grammar/` must be committed alongside the `.g4` files. The ANTLR jar is only needed for regeneration (build time), not at runtime; `antlr4-python3-runtime` (pip) is the only runtime dependency.
