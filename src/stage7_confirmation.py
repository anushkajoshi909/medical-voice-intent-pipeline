#!/usr/bin/env python3
"""Stage 7: User Confirmation

Takes the Stage 6 intent payload and presents a human-readable summary to
the user, requiring an explicit "correct"/"wrong" response before the
command is considered ready for execution.

Only a Stage 6 result with status "accepted" can be confirmed. A
"needs_clarification" result is shown with its clarification message and
returns outcome "clarification_requested": a yes/no reply cannot resolve an
unknown or incompatible entity, so the user has to say a corrected command,
which runs through the whole pipeline again (and needs the trigger phrase).
"rejected", "accepted_partial" (open dictation, handled by interaction.py)
and "inactive" return "not_applicable".

No clinical application is connected: "confirmed" means the user confirmed
the interpretation, not that anything was executed.

Policy comes from each action's `confirmation_timeout_sec` in
domain_config.yaml:
  null  -> always block for an explicit response (high-risk actions:
           diagnosis, set_medication, start, stop, change_patient, ...)
  <int> -> wait up to N seconds for a response; auto-confirm if none arrives
           (low-risk actions: show, set_note, turn_on/off, continue)
  0     -> skip the prompt entirely and confirm immediately (any delay is
           unacceptable, e.g. emergency)

This stage never calls the LLM. The confirmation vocabulary is
intentionally narrow and fixed ("ok glasses, correct" / "ok glasses,
wrong"), so response classification is a small deterministic keyword
match rather than free-text understanding.
"""
from __future__ import annotations

import re
import select
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from domain_loader import load as _domain
from stage1_preprocess import strip_trigger

_CONFIRM_WORDS = {"correct", "yes", "yeah", "yep", "confirm", "confirmed",
                   "right", "affirmative", "ok", "okay", "good"}
_REJECT_WORDS = {"wrong", "no", "nope", "incorrect", "negative", "cancel",
                  "repeat", "undo"}


_NOT_EXECUTED = "no clinical application is connected, so nothing was executed"


@dataclass
class Stage7Result:
    needs_confirmation: bool
    prompt: str | None
    outcome: str            # "confirmed" | "rejected" | "auto_confirmed" | "unclear"
                            # | "clarification_requested" | "not_applicable"
    final_message: str


# ── configuration ────────────────────────────────────────────────────────────

def _action_cfg() -> dict:
    """Per-action settings from domain_config.yaml (confirmation_timeout_sec)."""
    return _domain()["action_config"]


# ── prompt construction ──────────────────────────────────────────────────────

def _summarize_slot(name: str, slot) -> str:
    if isinstance(slot, list):
        # several drugs: each is shown with its dose and unit, so the user confirms the amount as well
        parts = []
        for d in slot:
            label = d.get("drug_label") or d.get("raw")
            dose = " ".join(x for x in (d.get("dose"), d.get("dose_unit")) if x)
            if label:
                parts.append(f"{label} {dose}".strip())
        return ", ".join(parts)
    if isinstance(slot, dict):
        if "content" in slot:
            content = slot["content"]
            return content[:60] + ("..." if len(content) > 60 else "")
        canonical = slot.get("canonical")
        if canonical:
            # the concept identifier stays in the Stage 6 payload (audit trail) but is not shown to the user
            if slot.get("semantic_type") == "medication" and slot.get("raw_entity"):
                return slot["raw_entity"]   # a medication is confirmed with its dose, unit and timing as extracted
            return canonical
        return slot.get("raw_entity", name)
    return str(slot)


def build_prompt(stage6_payload: dict) -> str | None:
    """Build the confirmation question shown on the glasses. Returns None for
    every status except "accepted": there is nothing to confirm (rejected,
    inactive, still-open dictation) or the command needs clarification, which
    is a different interaction (see run())."""
    if stage6_payload.get("status") != "accepted":
        return None

    # accepted
    action = stage6_payload.get("action", "")
    label = action.replace("_", " ").title()
    slots = stage6_payload.get("slots") or {}
    if not slots:
        return f"{label}. Correct?"

    summaries = [_summarize_slot(name, slot) for name, slot in slots.items()]
    return f"{label}: {', '.join(summaries)}. Correct?"


# ── response classification ──────────────────────────────────────────────────

def classify_confirmation(text: str) -> str:
    """Deterministic keyword match — "confirmed" | "rejected" | "unclear".
    The trigger phrase is removed first: its "ok" must not count as a yes, so
    "ok glasses" alone, or a new command such as "ok glasses show X", is unclear."""
    cleaned = re.sub(r"[^a-z ]", " ", strip_trigger(text).lower())
    words = set(cleaned.split())
    if words & _REJECT_WORDS:
        return "rejected"
    if words & _CONFIRM_WORDS:
        return "confirmed"
    return "unclear"


# ── terminal I/O ──────────────────────────────────────────────────────────────

def _wait_for_response(timeout_sec: Optional[float]) -> Optional[str]:
    """Block for a line of terminal input, optionally bounded by a timeout.
    Returns None on timeout, or immediately if stdin isn't interactive."""
    if timeout_sec is None:
        return input().strip()
    if not sys.stdin.isatty():
        return None
    ready, _, _ = select.select([sys.stdin], [], [], timeout_sec)
    if not ready:
        return None
    return sys.stdin.readline().strip()


# ── main entry point ─────────────────────────────────────────────────────────

def run(stage6_payload: dict, action: str,
        respond: Optional[Callable[[str], str]] = None,
        read_response: Optional[Callable[[Optional[float]], Optional[str]]] = None) -> Stage7Result:
    """Run Stage 7 confirmation for a Stage 6 payload.

    respond — optional callable(prompt) -> str used to supply a scripted
              answer instead of real terminal input (for testing). If not
              given, uses real terminal input, bounded by the action's
              confirmation_timeout_sec.
    read_response — optional callable(timeout) -> str | None that reads the
              terminal answer instead of input() (the --interactive session
              passes TerminalLineReader.read_answer, which only takes keys typed
              after the question, so pasted text cannot answer it).
    """
    status = stage6_payload.get("status", "")
    if status == "needs_clarification":
        # never enters the confirmation policy: no yes/no, no auto-confirmation
        message = (stage6_payload.get("clarification_message")
                   or "The command could not be resolved. Please say the corrected command.")
        return Stage7Result(False, message, "clarification_requested", message)

    prompt = build_prompt(stage6_payload)
    if prompt is None:
        return Stage7Result(False, None, "not_applicable", status)

    timeout = _action_cfg().get(action, {}).get("confirmation_timeout_sec")

    if timeout == 0:
        return Stage7Result(True, prompt, "auto_confirmed",
                             f"{prompt} -> AUTO-CONFIRMED "
                             f"(no delay policy for {action!r}; {_NOT_EXECUTED})")

    print(f"\n  [Glasses display] {prompt}")

    if respond is not None:
        raw = respond(prompt)
    else:
        if timeout is not None:
            print(f"  (waiting up to {timeout}s for a response — "
                  f"say 'ok glasses, correct' or 'ok glasses, wrong')")
        else:
            print("  (say 'ok glasses, correct' or 'ok glasses, wrong')")
        raw = (read_response or _wait_for_response)(timeout)

    if raw is None:
        return Stage7Result(True, prompt, "auto_confirmed",
                             f"No response within {timeout}s — auto-confirmed ({_NOT_EXECUTED}).")

    verdict = classify_confirmation(raw)
    if verdict == "confirmed":
        return Stage7Result(True, prompt, "confirmed",
                             f"CONFIRMED — interpretation confirmed by the user ({_NOT_EXECUTED}).")
    if verdict == "rejected":
        return Stage7Result(True, prompt, "rejected",
                             "REJECTED — please repeat the command.")
    return Stage7Result(True, prompt, "unclear",
                         f"Unclear response {raw!r} — treating as rejected; "
                         f"please repeat.")


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cases = [
        ({"status": "accepted", "action": "start",
          "slots": {"procedure": {"canonical": "Hemodialysis", "ontology_id": "NCIt:C15248"}}},
         "start", lambda p: "ok glasses, correct"),
        ({"status": "needs_clarification", "action": "diagnosis",
          "clarification_message": "I heard 'hemodialysis' and recognised it as a "
                                    "procedure, not a disease. Did you mean 'hemolysis'?"},
         "diagnosis", lambda p: "ok glasses, wrong"),
        ({"status": "accepted", "action": "show",
          "slots": {"ehr_record": {"canonical": "discharge letter"}}},
         "show", None),  # no responder -> exercises real timeout path
        ({"status": "accepted", "action": "emergency", "slots": {}},
         "emergency", None),
        ({"status": "rejected", "action": "UNKNOWN"}, "UNKNOWN", None),
    ]
    for payload, action, responder in cases:
        r = run(payload, action, respond=responder)
        print(f"  -> needs_confirmation={r.needs_confirmation} outcome={r.outcome!r}")
        print(f"     {r.final_message}\n")
