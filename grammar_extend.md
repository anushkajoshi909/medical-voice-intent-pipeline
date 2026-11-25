# Grammar Extension: Changes from Ludyga's Grammar

*Master Thesis: Improving Intent Recognition in Grammar-Based Voice Interfaces*
*Anushka Joshi, TU Dresden, Chair of Software Technology*

Stage 3 checks the structure of every command with an ANTLR grammar. The grammar is Ludyga's grammar with three extensions, because the thesis needs commands that the original grammar cannot express.

| File | Content |
|---|---|
| `baseline_ludyga/MedicalSmartGlassesLexer.g4`, `MedicalSmartGlassesParser.g4` | Ludyga's grammar, **not modified** (used as system B0) |
| `MedicalSmartGlassesLexer.g4`, `MedicalSmartGlassesParser.g4` (repository root) | Extended grammar used by the pipeline; generated Python parser in `src/grammar/` |

---

## Summary of Changes

| Change | Ludyga's grammar | Extended grammar |
|---|---|---|
| `diagnosis` (new) | no rule, no `DIAGNOSIS` token | `u21 : DIAGNOSIS any_text ;` |
| `treatment` (new) | no rule for "start hemodialysis" | `u22 : START TREATMENT any_text ;` |
| `stop_cmd` (changed) | `u01 : STOP (FRAME)? ;` inside `glasses_command` | `u01 : STOP any_text? ;` as its own command type |
| `switch` renamed | `switch : u03 \| u04 ;` | `switch_cmd : u03 \| u04 ;` (same rule) |

New lexer tokens: `DIAGNOSIS : 'diagnosis' ;` and `TREATMENT : 'treatment' ;`. The helper rule `any_text : . (.)*? ;` accepts one or more tokens of any kind, so an entity may contain any words.

All other rules are unchanged, including `request_data`, `protocol`, `request_patient`, `message`, the emergency commands, and the medication rule:

```antlr
medication : medicine INT unit | INT unit OF medicine ;
```

This rule is the reason "set medication paracetamol" (no dose, no unit) is rejected by Stage 3.

---

## Why Each Change Is Needed

**`diagnosis`.** The central use case distinguishes "diagnosis hemolysis" (recording a finding) from "start hemodialysis" (starting a procedure). Without a separate command type, Stage 5 cannot give the two actions different `allowed_slot_types`. In Ludyga's grammar every diagnosis command is a parse error.

**`treatment`.** In Ludyga's grammar `START` begins four commands (emergency, documentation, message, medication), and each is recognised by its second keyword. A rule `START any_text` has no such keyword and overlaps with all of them: because `treatment` is tried before `set_medication` and `message`, it would accept a message without a recipient or a medication without a dose as a treatment, so these would never be checked. Moving the rule to the end does not help, because incomplete messages and medications would still fall through to it. The fixed keyword `TREATMENT` removes the overlap.

The user never says "treatment". Stage 3 rebuilds the command from the template in `domain_config.yaml` (`"{trigger} start treatment {entity}"`), so the keyword exists only in the rebuilt command. `treatment` is kept apart from `protocol`, which means documentation, so that `command_type` names the right backend action.

**`stop_cmd`.** The pipeline needs "stop hemodialysis" or "stop infusion", where the entity is free text. The entity is optional, so a bare "stop" is still valid.

---

## Command Types

ANTLR tries the alternatives in this order and uses the first one that matches the whole command, so "start emergency" and "start documentation" still reach `glasses_command` and `protocol`.

```
command : TRIGGER ( ... ) EOF
  ├── glasses_command   continue frame, turn on/off, emergency, show messages
  ├── request_data      show <data> [of last N unit]
  ├── protocol          set note … finish note / start documentation …
  ├── treatment         start treatment <entity>              NEW
  ├── stop_cmd          stop [entity]                          CHANGED
  ├── set_medication    set medication <drug> <dose> <unit> …
  ├── request_patient   change patient to <name>
  ├── message           start message … send it to <name>
  └── diagnosis         diagnosis <entity>                     NEW
```

Stage 3 returns the matched type as `command_type`.

---

## Link to `domain_config.yaml`

Each action declares the command type it belongs to:

| Action | `grammar_command_type` |
|---|---|
| `show` | `request_data` |
| `diagnosis` | `diagnosis` |
| `start` | `treatment` |
| `stop` | `stop_cmd` |
| `set_medication` | `set_medication` |
| `set_note` | `protocol` |
| `change_patient` | `request_patient` |
| `send_message` | `message` |
| `turn_on`, `turn_off`, `continue`, `emergency`, `accept_emergency`, `leave_emergency` | `glasses_command` |

At startup `src/domain_loader.py` checks that every `grammar_command_type` is a rule of the parser grammar; otherwise the pipeline does not start and names the action.

---

## Regenerating the Python Parser

After a change to the `.g4` files, regenerate the parser and commit it together with the grammar:

```bash
java -jar antlr4-4.13.2-complete.jar -Dlanguage=Python3 -o src/grammar \
  MedicalSmartGlassesLexer.g4 MedicalSmartGlassesParser.g4
```

The ANTLR jar is needed only for this step. At runtime the only dependency is `antlr4-python3-runtime`.
