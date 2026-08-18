#!/usr/bin/env python3
"""Stage 3: ANTLR Frame Validation

Feeds the LLM's {action, entity} output through the real ANTLR4 parser
(MedicalSmartGlassesParser.g4). This catches structural errors that the
LLM cannot — e.g. "set medication paracetamol" (missing dose and unit)
fails the grammar rule `medication: medicine INT unit`.

The command type returned (request_data, set_medication, diagnosis, …)
matches Ludyga's hierarchy and is used for backend routing.

How it works:
  1. Reconstruct a canonical string from {action, entity}.
  2. Feed to ANTLR lexer → parser.
  3. If parse errors → invalid. Otherwise return command_type from parse tree.
"""
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "grammar"))

from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from MedicalSmartGlassesLexer import MedicalSmartGlassesLexer
from MedicalSmartGlassesParser import MedicalSmartGlassesParser


# ── Canonical string reconstruction ──────────────────────────────────────────
# Each action maps to a template that produces a string the grammar can parse.
# ANTLR then validates the structural correctness of the full command.

def _sanitize(entity: str) -> str:
    """Remove characters the ANTLR lexer cannot tokenise.
    Periods, slashes, colons etc. appear in free-text entity content
    (e.g. 'Dr.', '160/90', '37.2') and cause lexer errors.
    For structure checking we only care about words/numbers, not punctuation.
    """
    import re
    # Normalise "o clock" → "oclock" (grammar expects single OCLOCK token)
    entity = re.sub(r'\bo\s+clock\b', 'oclock', entity, flags=re.IGNORECASE)
    # Replace chars outside [a-zA-Z0-9 ] with a space (hyphens/apostrophes handled by Stage 1)
    entity = re.sub(r"[^a-zA-Z0-9 ]", ' ', entity)
    return re.sub(r' +', ' ', entity).strip()


def _canonical(action: str, entity: str, recipient: str = "") -> str:
    e = _sanitize(entity.strip())
    r = _sanitize(recipient.strip())
    templates = {
        "show":             f"ok glasses show {e}",
        "diagnosis":        f"ok glasses diagnosis {e}",
        "start":            f"ok glasses start {e}",
        "stop":             f"ok glasses stop {e}" if e else "ok glasses stop",
        "continue":         "ok glasses continue",
        # turn_on/turn_off: entity may be "display" (valid grammar keyword) or empty
        "turn_on":          f"ok glasses turn on {e}" if e else "ok glasses turn on",
        "turn_off":         f"ok glasses turn off {e}" if e else "ok glasses turn off",
        "emergency":        "ok glasses start emergency",
        "accept_emergency": "ok glasses accept emergency",
        "leave_emergency":  "ok glasses leave emergency",
        "change_patient":   f"ok glasses change patient to {e}",
        # Restored Ludyga original: START [NEW] MESSAGE note FINISH [NEW] MESSAGE [AND] SEND IT TO patient_name
        "send_message":     f"ok glasses start message {e} finish message and send it to {r}",
        # set_medication: entity must be "medicine INT unit" e.g. "paracetamol 100 mg"
        "set_medication":   f"ok glasses set medication {e} finish medication",
        "set_note":         f"ok glasses set note {e} finish note",
    }
    return templates.get(action, f"ok glasses {action} {e}").strip()


# ── Silent error listener ─────────────────────────────────────────────────────

class _ErrorCollector(ErrorListener):
    def __init__(self):
        super().__init__()
        self.errors: list[str] = []

    def syntaxError(self, recognizer, offendingSymbol, line, col, msg, e):
        self.errors.append(f"col {col}: {msg}")


# ── Command-type extraction from parse tree ───────────────────────────────────

def _command_type(tree, parser) -> str:
    """Walk one level below 'command' to extract the command type name."""
    if tree.getChildCount() < 2:
        return "unknown"
    # child 0 = TRIGGER, child 1 = the type alternative
    type_ctx = tree.getChild(1)
    name = type(type_ctx).__name__.lower()
    # Strip trailing 'context' added by ANTLR
    return name.replace("context", "")


# ── Public API ────────────────────────────────────────────────────────────────

@dataclass
class FrameResult:
    valid:          bool
    reason:         str   # "OK" | "INVALID_ACTION" | "PARSE_ERROR" | "MISSING_ENTITY"
    canonical_form: str
    command_type:   str   # e.g. "request_data", "set_medication", "diagnosis"


_domain_cfg: dict | None = None


def _get_domain_cfg() -> dict:
    global _domain_cfg
    if _domain_cfg is None:
        from domain_loader import load as _domain_load
        _domain_cfg = _domain_load()
    return _domain_cfg


def _valid_actions() -> set[str]:
    return _get_domain_cfg()["valid_actions"]


def _entity_forbidden() -> set[str]:
    cfg = _get_domain_cfg()["action_config"]
    return {name for name, info in cfg.items() if not info.get("entity_slot", True)}


def validate(action: str, entity: str, recipient: str = "") -> FrameResult:
    entity = entity.strip()

    if action not in _valid_actions():
        return FrameResult(False, "INVALID_ACTION", f"ok glasses {action} {entity}".strip(), "unknown")

    # Quick pre-check: entity-forbidden actions
    if action in _entity_forbidden() and entity:
        return FrameResult(False, "UNEXPECTED_ENTITY",
                           _canonical(action, entity), "glasses_command")

    canonical = _canonical(action, entity, recipient=recipient)

    # Run ANTLR parser
    input_stream = InputStream(canonical)
    lexer         = MedicalSmartGlassesLexer(input_stream)
    token_stream  = CommonTokenStream(lexer)
    parser        = MedicalSmartGlassesParser(token_stream)

    # Suppress console output; collect errors
    listener = _ErrorCollector()
    lexer.removeErrorListeners()
    parser.removeErrorListeners()
    lexer.addErrorListener(listener)
    parser.addErrorListener(listener)

    tree = parser.command()

    if listener.errors:
        reason = "PARSE_ERROR: " + "; ".join(listener.errors)
        return FrameResult(False, reason, canonical, "unknown")

    cmd_type = _command_type(tree, parser)
    return FrameResult(True, "OK", canonical, cmd_type)


# ── Quick smoke test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    cases = [
        ("show",             "hemoglobin"),
        ("show",             "patient history"),
        ("diagnosis",        "hemolysis"),
        ("diagnosis",        "hemodialysis"),       # structurally valid; Stage 5 catches type
        ("start",            "hemodialysis"),
        ("set_medication",   "paracetamol 100 mg"),
        ("set_medication",   "paracetamol"),        # FAIL — no dose+unit
        ("set_medication",   "hypertension"),       # FAIL — no dose+unit
        ("emergency",        ""),
        ("emergency",        "something"),          # FAIL — entity forbidden
        ("leave_emergency",  ""),
        ("accept_emergency", ""),
        ("turn_on",          ""),
        ("turn_off",         "display"),            # FAIL — entity forbidden
        ("stop",             "hemodialysis"),
        ("stop",             ""),
        ("change_patient",   "John Smith"),
        ("send_message",     "patient is ready"),        # FAIL — no recipient
        ("send_message",     "patient is ready|Dr Smith"), # note: pass recipient separately
        ("not_an_action",    ""),                   # FAIL
    ]
    for action, entity in cases:
        r = validate(action, entity)
        sym = "✓" if r.valid else "✗"
        print(f"[{sym}] {action:20s} entity={entity!r:28s} type={r.command_type:16s}  {r.reason}")
