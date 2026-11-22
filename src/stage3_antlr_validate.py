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
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "grammar"))

from domain_loader import load as _get_domain_cfg

from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from MedicalSmartGlassesLexer import MedicalSmartGlassesLexer
from MedicalSmartGlassesParser import MedicalSmartGlassesParser


# ── Canonical string reconstruction ──────────────────────────────────────────
# Each action's template (stage3_template in domain_config.yaml) produces a string the grammar can parse.
# ANTLR then validates the structural correctness of the full command.

def _sanitize(entity: str) -> str:
    """Remove characters the ANTLR lexer cannot tokenise.
    Periods, slashes, colons etc. appear in free-text entity content
    (e.g. 'Dr.', '160/90', '37.2') and cause lexer errors.
    For structure checking we only care about words/numbers, not punctuation.
    """
    # Decimals ('2.5 mg') would otherwise split into two INT tokens
    entity = re.sub(r"(?<=\d)[.,](?=\d)", "", entity)
    # Replace chars outside [a-zA-Z0-9 ] with a space (hyphens/apostrophes handled by Stage 1)
    entity = re.sub(r"[^a-zA-Z0-9 ]", ' ', entity)
    # Normalise "o clock" / "o'clock" → "oclock" (grammar expects single OCLOCK token);
    # must run after the punctuation step so "o'clock" is already "o clock"
    entity = re.sub(r'\bo\s+clock\b', 'oclock', entity, flags=re.IGNORECASE)
    return re.sub(r' +', ' ', entity).strip()


# ── Entity views for the structure check ─────────────────────────────────────
# Stage 3 only checks the command FRAME. The entity handed to the parser is a
# copy; the original entity continues unchanged to Stages 4-6. Which view an
# action uses is declared in domain_config.yaml (stage3_entity_view).

def _medication_core(entity: str) -> str:
    """Keep 'drug dose unit' per drug and drop trailing instructions
    ('2 puffs every 4 hours as needed', 'once daily'), which the grammar
    cannot express. An optional 'at <time>' suffix is kept (rule u17).
    A missing dose or unit is left untouched so the parser still rejects it."""
    tail = ""
    m = re.search(r"\s+at\s+\d+\s*(oclock|pm|am)\s*$", entity, flags=re.IGNORECASE)
    if m:
        tail, entity = m.group(0), entity[:m.start()]
    core = []
    for seg in re.split(r"\s+and\s+", entity, flags=re.IGNORECASE):
        toks = seg.split()
        idx = next((i for i, tok in enumerate(toks) if tok.isdigit()), None)
        if idx is not None and idx > 0 and idx + 1 < len(toks):
            toks = toks[:idx + 2]
        core.append(" ".join(toks))
    return " and ".join(core) + tail


def _structure_view(action: str, entity: str) -> str:
    mode = _get_domain_cfg()["action_config"].get(action, {}).get("stage3_entity_view")
    if mode == "medication_core":
        return _medication_core(entity)
    return entity


def _canonical(action: str, entity: str, recipient: str = "") -> str:
    domain = _get_domain_cfg()
    e = _structure_view(action, _sanitize(entity.strip()))
    r = _sanitize(recipient.strip())
    template = domain["action_config"].get(action, {}).get("stage3_template")
    if template is None:  # unknown action: keep the raw form for the error message
        template = "{trigger} " + action + " {entity}"
    return (template
            .replace("{trigger}", domain["trigger_phrase"])
            .replace("{entity}", e)
            .replace("{recipient}", r)).strip()


# ── Silent error listener ─────────────────────────────────────────────────────

class _ErrorCollector(ErrorListener):
    def __init__(self):
        super().__init__()
        self.errors: list[str] = []

    def syntaxError(self, recognizer, offendingSymbol, line, col, msg, e):
        self.errors.append(f"col {col}: {msg}")


# ── Command-type extraction from parse tree ───────────────────────────────────

def _command_type(tree) -> str:
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
    reason:         str   # "OK" | "INVALID_ACTION" | "UNEXPECTED_ENTITY" | "PARSE_ERROR: <details>"
    canonical_form: str
    command_type:   str   # e.g. "request_data", "set_medication", "diagnosis"


def _valid_actions() -> set[str]:
    return _get_domain_cfg()["valid_actions"]


def _entity_forbidden() -> set[str]:
    cfg = _get_domain_cfg()["action_config"]
    return {name for name, info in cfg.items() if not info.get("entity_slot", True)}


def validate(action: str, entity: str, recipient: str = "") -> FrameResult:
    entity = entity.strip()

    if action not in _valid_actions():
        trigger = _get_domain_cfg()["trigger_phrase"]
        return FrameResult(False, "INVALID_ACTION", f"{trigger} {action} {entity}".strip(), "unknown")

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

    cmd_type = _command_type(tree)
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
        ("send_message",     "patient is ready"),   # FAIL — no recipient
        ("not_an_action",    ""),                   # FAIL
    ]
    for action, entity in cases:
        r = validate(action, entity)
        sym = "✓" if r.valid else "✗"
        print(f"[{sym}] {action:20s} entity={entity!r:28s} type={r.command_type:16s}  {r.reason}")
