#!/usr/bin/env python3
"""Stage 3: Frame Validation

Validates that the LLM's {action, entity} pair fits a structurally valid
command frame. Implements the same rules as MedicalSmartGlassesParser.g4
without requiring the ANTLR Java toolchain at runtime.

Grammar rules (MedicalSmartGlassesParser.g4):
  action_with_entity : START | SHOW | DIAGNOSIS | SET_NOTE | SET_MED |
                       STOP | CHANGE_PT | SEND_MSG  → entity REQUIRED
  action_no_entity   : TURN_ON | TURN_OFF | CONTINUE → entity optional
                       EMERGENCY | ACCEPT_EMERGENCY | LEAVE_EMERGENCY → entity FORBIDDEN
"""
from dataclasses import dataclass
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
_cfg = yaml.safe_load((ROOT / "domain_config.yaml").read_text())

VALID_ACTIONS    = set(_cfg["actions"].keys())

# Grammar: actions where entity is optional (bare or with entity both valid)
# stop is included: "stop" (display) vs "stop hemodialysis" (treatment)
ENTITY_OPTIONAL  = {"turn_on", "turn_off", "continue", "stop"}

# Grammar: action_no_entity with no entity alternative (must be empty)
ENTITY_FORBIDDEN = {"emergency", "accept_emergency", "leave_emergency"}

# Grammar: action_with_entity (entity required)
ENTITY_REQUIRED  = VALID_ACTIONS - ENTITY_OPTIONAL - ENTITY_FORBIDDEN


@dataclass
class FrameResult:
    valid:          bool
    reason:         str   # "OK" | "INVALID_ACTION" | "MISSING_ENTITY" | "UNEXPECTED_ENTITY"
    canonical_form: str   # reconstructed "ok glasses <action> <entity>"


def validate(action: str, entity: str) -> FrameResult:
    """Validate {action, entity} against the command frame grammar.

    Equivalent to tokenising "ok glasses <action> <entity>" and matching
    the `command` rule in MedicalSmartGlassesParser.g4.
    """
    entity = entity.strip()
    canonical = f"ok glasses {action}" + (f" {entity}" if entity else "")

    if action not in VALID_ACTIONS:
        return FrameResult(False, "INVALID_ACTION", canonical)

    if action in ENTITY_REQUIRED and not entity:
        return FrameResult(False, "MISSING_ENTITY", canonical)

    if action in ENTITY_FORBIDDEN and entity:
        return FrameResult(False, "UNEXPECTED_ENTITY", canonical)

    return FrameResult(True, "OK", canonical)


if __name__ == "__main__":
    cases = [
        ("show",             "blood pressure"),
        ("diagnosis",        "hemodialysis"),
        ("emergency",        ""),
        ("emergency",        "something"),    # should fail
        ("start",            ""),             # should fail
        ("leave_emergency",  "mode"),         # should fail
        ("turn_on",          ""),             # OK — optional
        ("turn_on",          "display"),      # OK — optional with value
        ("change_patient",   "Mary Johnson"),
        ("not_an_action",    ""),             # should fail
    ]
    for action, entity in cases:
        r = validate(action, entity)
        sym = "✓" if r.valid else "✗"
        print(f"[{sym}] {action!r:20s} entity={entity!r:20s}  → {r.reason}")
