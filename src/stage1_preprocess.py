#!/usr/bin/env python3
"""Stage 1: Normalisation + Abbreviation Expansion

Input:  raw speech text (from ASR or typed)
Output: PreprocessResult dataclass with:
          clean_text    — normalised command text, trigger stripped, abbrevs expanded
          trigger_found — bool
          urgency       — list of urgency flags detected (stat, urgent, immediately)
          original      — raw input unchanged

Pipeline spec (Final_Pipeline_Complete.md):
  • Lowercase
  • Strip punctuation including hyphens (, . : ! ? - → space)
  • Normalise trigger variants: "ok" / "okie" / "okay" → canonical "ok glasses"
  • Remove trigger from string, keep remainder
  • detect_trigger() is the activation check: run_pipeline.run_command() ignores
    a new command without a trigger before any further processing (Stage 6
    status "inactive", no LLM call)
  • Expand medical abbreviations (abbreviations.json) BEFORE passing to LLM
  • Scan for urgency flags → store separately, remove from text
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Load abbreviations once at import time (exclude metadata keys)
_abbrev_raw = json.loads((ROOT / "abbreviations.json").read_text())
ABBREVIATIONS: dict[str, str] = {
    k: v for k, v in _abbrev_raw.items()
    if not k.startswith("_") and isinstance(v, str)
}

TRIGGER_PATTERN = re.compile(
    r"\b(ok(?:ay|ie)?)\s+glasses\b", re.IGNORECASE
)
# "emergency" is NOT an urgency modifier — it IS the action. Stripping it caused
# Stage 2 to receive "begin" / "start" / "finish" with no context → UNKNOWN.
URGENCY_WORDS = {"stat", "urgent", "urgently", "immediately"}

PUNCTUATION_STRIP = re.compile(r"[-,.:!?;\"'(){}\[\]]")
MULTI_SPACE       = re.compile(r"\s{2,}")

# Inputs containing JSON/code structure: { } [ ] are never natural speech
_NON_SPEECH = re.compile(r"[{}\[\]]")


@dataclass
class PreprocessResult:
    original: str
    trigger_found: bool
    clean_text: str            # post-trigger, post-abbrev, post-urgency
    urgency: list[str] = field(default_factory=list)
    abbreviations_expanded: dict[str, str] = field(default_factory=dict)
    format_invalid: bool = False  # True if raw input is clearly non-speech (JSON, code)


def _strip_punctuation(text: str) -> str:
    text = PUNCTUATION_STRIP.sub(" ", text)
    return MULTI_SPACE.sub(" ", text).strip()


def _expand_abbreviations(text: str) -> tuple[str, dict[str, str]]:
    """Replace known abbreviations with their full form. Returns modified text
    and a dict of what was expanded (for audit trail)."""
    expanded: dict[str, str] = {}
    # Sort longest-first so "HbA1c" matches before "Hb"
    for abbrev in sorted(ABBREVIATIONS, key=len, reverse=True):
        pattern = re.compile(r"\b" + re.escape(abbrev) + r"\b")
        if pattern.search(text):
            full = ABBREVIATIONS[abbrev]
            text = pattern.sub(full, text)
            expanded[abbrev] = full
    return text, expanded


def _extract_urgency(text: str) -> tuple[str, list[str]]:
    found: list[str] = []
    words = text.lower().split()
    for w in URGENCY_WORDS:
        if w in words:
            found.append(w)
    for w in found:
        text = re.sub(r"\b" + w + r"\b", "", text, flags=re.IGNORECASE)
    return MULTI_SPACE.sub(" ", text).strip(), found


def detect_trigger(raw: str) -> bool:
    """Activation check: True if the input contains a trigger phrase ("ok glasses",
    "okay glasses", "okie glasses", any case, punctuation ignored). Text before the
    trigger is allowed. This is a text check on the transcript: it does not stop
    the audio from being recorded or transcribed (that needs an audio wake-word
    detector)."""
    return TRIGGER_PATTERN.search(_strip_punctuation(raw).lower()) is not None


# The same trigger variants as detect_trigger() accepts, on the raw text: any
# punctuation or spaces between "ok" and "glasses" ("ok, glasses", "ok-glasses"),
# and punctuation directly after the trigger is removed with it.
_TRIGGER_IN_REPLY = re.compile(r"\b(ok(?:ay|ie)?)[^\w]+glasses\b[^\w\s]*", re.IGNORECASE)


def strip_trigger(text: str) -> str:
    """Remove trigger phrases from a reply (used for replies inside an active
    interaction, which need no trigger but may contain one). The rest of the
    reply is kept as it is."""
    return MULTI_SPACE.sub(" ", _TRIGGER_IN_REPLY.sub(" ", text)).strip()


def preprocess(raw: str) -> PreprocessResult:
    text = raw

    # 0. Reject non-speech formats before any stripping (JSON, code, symbols)
    #    The ANTLR grammar caught these because { } [ ] are not lexer tokens.
    #    We catch them here on the raw text before punctuation stripping hides them.
    if _NON_SPEECH.search(raw):
        return PreprocessResult(
            original=raw, trigger_found=False,
            clean_text="", format_invalid=True,
        )

    # 1. Strip punctuation (hyphens become spaces: "non-invasive" → "non invasive")
    text = _strip_punctuation(text)

    # 2. Expand abbreviations BEFORE lowercasing — abbrevs are case-sensitive
    #    (SpO2 ≠ spo2; MI ≠ mi). Expansion must happen on original-case text.
    text, abbrev_expanded = _expand_abbreviations(text)

    # 3. Lowercase everything (including expanded text)
    text = text.lower()

    # 4. Detect and normalise trigger
    trigger_match = TRIGGER_PATTERN.search(text)
    trigger_found = trigger_match is not None

    if trigger_found:
        # Keep everything after the trigger
        text = text[trigger_match.end():].strip()

    # 5. Urgency flag extraction
    text, urgency = _extract_urgency(text)

    # 6. Final cleanup
    text = MULTI_SPACE.sub(" ", text).strip()

    return PreprocessResult(
        original=raw,
        trigger_found=trigger_found,
        clean_text=text,
        urgency=urgency,
        abbreviations_expanded=abbrev_expanded,
    )


if __name__ == "__main__":
    test_cases = [
        "ok glasses show BP",
        "okay glasses diagnosis MI stat",
        "Ok glasses start HD",
        "okie glasses, can you pull up ECG results?",
        "ok glasses set note upon examination there was tenderness finish note",
        "ok glasses illustrate the patient's medical history",
        "ok glasses diagnosis hemodialysis",
        "show SpO2",  # no trigger -> run_command() ignores it (status "inactive")
    ]
    for raw in test_cases:
        r = preprocess(raw)
        print(f"IN:      {raw}")
        print(f"trigger: {r.trigger_found}")
        print(f"clean:   {r.clean_text}")
        if r.urgency:
            print(f"urgency: {r.urgency}")
        if r.abbreviations_expanded:
            print(f"abbrevs: {r.abbreviations_expanded}")
        print()
