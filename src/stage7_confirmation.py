#!/usr/bin/env python3
"""Stage 7: User Confirmation

Takes the Stage 6 intent payload and presents a human-readable summary to
the user, requiring an explicit "correct"/"wrong" response before the
command is considered ready for execution.

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

_CONFIRM_WORDS = {"correct", "yes", "yeah", "yep", "confirm", "confirmed",
                   "right", "affirmative", "ok", "okay", "good"}
_REJECT_WORDS = {"wrong", "no", "nope", "incorrect", "negative", "cancel",
                  "repeat", "undo"}


@dataclass
class Stage7Result:
    needs_confirmation: bool
    prompt: str | None
    outcome: str            # "confirmed" | "rejected" | "auto_confirmed" | "unclear" | "not_applicable"
    final_message: str


# ── config cache ─────────────────────────────────────────────────────────────

_cfg: dict = {}
_cfg_loaded = False


def _action_cfg() -> dict:
    global _cfg, _cfg_loaded
    if not _cfg_loaded:
        from domain_loader import load as _load
        _cfg = _load()["action_config"]
        _cfg_loaded = True
    return _cfg


# ── prompt construction ──────────────────────────────────────────────────────

def _summarize_slot(name: str, slot) -> str:
    if isinstance(slot, list):
        parts = [d.get("drug_label") or d.get("raw") for d in slot]
        return ", ".join(p for p in parts if p)
    if isinstance(slot, dict):
        if "content" in slot:
            content = slot["content"]
            return content[:60] + ("..." if len(content) > 60 else "")
        canonical = slot.get("canonical")
        if canonical:
            oid = slot.get("ontology_id")
            return f"{canonical} ({oid})" if oid else canonical
        return slot.get("raw_entity", name)
    return str(slot)


def build_prompt(stage6_payload: dict) -> str | None:
    """Build the human-readable glasses display text. Returns None when
    there's nothing to confirm yet (already rejected, or a note/message
    still open awaiting end-of-dictation)."""
    status = stage6_payload.get("status")

    if status in ("rejected", "accepted_partial"):
        return None

    if status == "needs_clarification":
        return stage6_payload.get("clarification_message")

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
    """Deterministic keyword match — "confirmed" | "rejected" | "unclear"."""
    cleaned = re.sub(r"[^a-z ]", " ", text.lower())
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
        respond: Optional[Callable[[str], str]] = None) -> Stage7Result:
    """Run Stage 7 confirmation for a Stage 6 payload.

    respond — optional callable(prompt) -> str used to supply a scripted
              answer instead of real terminal input (for testing). If not
              given, uses real terminal input, bounded by the action's
              confirmation_timeout_sec.
    """
    prompt = build_prompt(stage6_payload)
    if prompt is None:
        return Stage7Result(False, None, "not_applicable",
                             stage6_payload.get("status", ""))

    timeout = _action_cfg().get(action, {}).get("confirmation_timeout_sec")

    if timeout == 0:
        return Stage7Result(True, prompt, "auto_confirmed",
                             f"{prompt} -> AUTO-CONFIRMED "
                             f"(no delay policy for {action!r})")

    print(f"\n  [Glasses display] {prompt}")

    if respond is not None:
        raw = respond(prompt)
    else:
        if timeout is not None:
            print(f"  (waiting up to {timeout}s for a response — "
                  f"say 'ok glasses, correct' or 'ok glasses, wrong')")
        else:
            print("  (say 'ok glasses, correct' or 'ok glasses, wrong')")
        raw = _wait_for_response(timeout)

    if raw is None:
        return Stage7Result(True, prompt, "auto_confirmed",
                             f"No response within {timeout}s — auto-confirmed.")

    verdict = classify_confirmation(raw)
    if verdict == "confirmed":
        return Stage7Result(True, prompt, "confirmed",
                             "CONFIRMED — command executed.")
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
