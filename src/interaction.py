#!/usr/bin/env python3
"""Interaction state across turns (--interactive and --voice sessions).

The pipeline (run_pipeline.run_command) handles one utterance. This module
keeps what has to survive between utterances:

  idle               waiting for a NEW command. A new command needs the trigger
                     phrase; without it run_command() returns "inactive" and the
                     session keeps listening. A clarification request also
                     returns here: the corrected command is a new command and
                     needs the trigger.
  pending dictation  a note or message without an end signal (Stage 6
                     "accepted_partial"). Later utterances need no trigger:
                       * finish / continue / cancel replies (whole utterance,
                         see domain_config.yaml interaction.dictation_replies)
                       * anything else is dictated content and is appended; if
                         it ends with a finish phrase ("... finish note"), the
                         phrase is removed and the dictation is finished.
                     This also holds after the reminder question: any other
                     utterance is kept as dictated content (an implicit
                     "continue"). So text that the user started to type or say
                     before the question appeared, and sends after it, is never
                     lost. Only an empty utterance is "unclear".
                     Finishing re-runs Stages 3-6 on the complete content (no
                     LLM call) and then applies the action's normal Stage 7
                     confirmation policy. Finishing is not a confirmation.
                     Silence never finishes or sends a dictation. If the
                     reminder question gets no answer for dictation_discard_sec
                     (60 s), the open dictation is discarded and the user is told.

Confirmation replies are handled inside Stage 7 (stage7_confirmation.run with
a respond callback) and need no trigger either.

Inactivity for the reminder and the discard is measured from the end of the
last turn's processing. Time between begin_input() and end_input() (recording,
transcription) is excluded: end_input() moves both deadlines on by that time,
also when the recording gives no usable transcript and handle() is not called.
Model/network time inside handle() never counts either.
The clock is injectable for tests.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from stage1_preprocess import strip_trigger

_DEFAULT_REPLIES = {
    "finish": ["finish", "finished", "done", "that is all", "finish note", "finish message", "send it"],
    "continue": ["continue", "keep going", "not finished"],
    "cancel": ["cancel", "discard"],
}
_DEFAULT_REMINDER = ("I did not hear an end phrase for this {kind}. "
                     "Is the {kind} finished, or would you like to continue?")
_DISCARDED = "No answer: the open {kind} was discarded. Nothing was saved or sent."


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


@dataclass
class PendingDictation:
    action: str
    content: str
    recipient: str
    original_input: str
    confidence: float = 0.0
    model: str | None = None
    utterances: list = field(default_factory=list)   # every input of this dictation, in order


@dataclass
class Turn:
    """What one utterance led to. kind is one of:
    inactive | rejected | clarification | confirmation | dictation_open |
    dictation_continued | dictation_resumed | dictation_cancelled |
    dictation_finished | unclear_reply (empty utterance)
    (a discard after silence is reported by expire())"""
    kind: str
    message: str = ""
    result: dict | None = None      # run_pipeline result (new command or finished dictation)
    stage7: object = None           # Stage7Result, if confirmation ran
    pending: PendingDictation | None = None


class InteractionSession:
    def __init__(self, process: Callable[[str], dict],
                 complete: Callable[[PendingDictation], dict],
                 confirm: Callable[[dict, str], object],
                 settings: dict | None = None,
                 clock: Callable[[], float] = time.monotonic):
        """process(text)  -> result of run_pipeline.run_command for a new command
        complete(pending) -> result of run_pipeline.complete_dictation (no LLM call)
        confirm(payload, action) -> Stage7Result (the action's confirmation policy)"""
        settings = settings or {}
        self._process, self._complete, self._confirm = process, complete, confirm
        self._clock = clock
        self.reminder_sec = float(settings.get("dictation_reminder_sec", 5))
        self.discard_sec = float(settings.get("dictation_discard_sec", 60))
        self._reminded_at = None
        self._reminders = settings.get("dictation_reminder") or {}
        replies = settings.get("dictation_replies") or _DEFAULT_REPLIES
        self._replies = {k: {_norm(p) for p in replies.get(k, _DEFAULT_REPLIES[k])}
                         for k in _DEFAULT_REPLIES}
        self.pending: PendingDictation | None = None
        self._reminded = False
        self._busy = False
        self._busy_since = None
        self._last_activity = clock()

    # ── state ────────────────────────────────────────────────────────────────
    @property
    def state(self) -> str:
        return "pending_dictation" if self.pending else "idle"

    def begin_input(self):
        """Recording has started: no reminder or discard until end_input()."""
        if not self._busy:
            self._busy, self._busy_since = True, self._clock()

    def end_input(self):
        """Recording (and transcription) has ended. The busy time does not count as
        inactivity: both deadlines are moved on by it."""
        if not self._busy:
            return
        spent = self._clock() - self._busy_since
        self._last_activity += spent
        if self._reminded_at is not None:
            self._reminded_at += spent
        self._busy, self._busy_since = False, None

    def note_activity(self):
        """The user is typing (a keystroke before Enter): the user is not inactive,
        so both deadlines restart from now. Without this, a note could be discarded
        while a continuation is being typed, because a typed line only arrives on Enter."""
        now = self._clock()
        self._last_activity = now
        if self._reminded:
            self._reminded_at = now

    def seconds_until_reminder(self) -> Optional[float]:
        """None if no reminder is scheduled (no open dictation, already reminded, busy)."""
        if not self.pending or self._reminded or self._busy:
            return None
        return max(0.0, self.reminder_sec - (self._clock() - self._last_activity))

    def seconds_until_next_event(self) -> Optional[float]:
        """Time until the reminder, or after the reminder until the discard; None if nothing is due."""
        if not self.pending or self._busy:
            return None
        if not self._reminded:
            return self.seconds_until_reminder()
        return max(0.0, self.discard_sec - (self._clock() - self._reminded_at))

    def expire(self) -> Optional[str]:
        """Discard the open dictation if the reminder question got no answer for
        discard_sec. Returns the message for the user, or None if nothing happened.
        The discarded dictation is never saved, confirmed, or sent."""
        if (not self.pending or self._busy or not self._reminded
                or self._clock() - self._reminded_at < self.discard_sec):
            return None
        kind = "message" if self.pending.action == "send_message" else "note"
        self.pending, self._reminded = None, False
        return _DISCARDED.format(kind=kind)

    def reminder_due(self) -> bool:
        left = self.seconds_until_reminder()
        return left is not None and left <= 0

    def reminder(self) -> Optional[str]:
        """Return the completion question if it is due (once per waiting period), else None.
        Showing the reminder changes nothing about the dictation itself."""
        if not self.reminder_due():
            return None
        self._reminded = True
        self._reminded_at = self._clock()
        kind = "message" if self.pending.action == "send_message" else "note"
        return self._reminders.get(self.pending.action) or _DEFAULT_REMINDER.format(kind=kind)

    def _touch(self):
        self._last_activity = self._clock()
        self._reminded = False

    # ── one utterance ────────────────────────────────────────────────────────
    def handle(self, text: str) -> Turn:
        self._busy, self._busy_since = True, None
        try:
            turn = self._handle_pending(text) if self.pending else self._handle_new(text)
        finally:
            self._busy = False
            self._touch()                # deadlines restart after processing
        return turn

    def _handle_new(self, text: str) -> Turn:
        r = self._process(text)
        status = r["s6_status"]
        payload = r["s6_payload"]
        if status == "inactive":
            return Turn("inactive", "No trigger phrase: input ignored, still listening.", r)
        if status == "accepted_partial":
            slot = (payload.get("slots") or {}).get("free_text", {})
            self.pending = PendingDictation(
                action=r["action"], content=slot.get("content", r.get("entity", "")),
                recipient=r.get("recipient", "") or "", original_input=r["text"],
                confidence=r.get("confidence", 0.0), model=r.get("model"),
                utterances=[r["text"]])
            kind = "message" if r["action"] == "send_message" else "note"
            return Turn("dictation_open", f"The {kind} is open. Keep dictating, or say 'finish'.",
                        r, pending=self.pending)
        return self._decide(r)

    def _decide(self, r: dict) -> Turn:
        """Shared ending for a new command and for a finished dictation."""
        payload = r["s6_payload"]
        status = r["s6_status"]
        if status == "needs_clarification":
            s7 = self._confirm(payload, r["action"])          # outcome clarification_requested
            return Turn("clarification", s7.final_message, r, s7)
        if status == "accepted":
            s7 = self._confirm(payload, r["action"])
            return Turn("confirmation", s7.final_message, r, s7)
        msg = payload.get("clarification_message") or "Command rejected. Please say the command again."
        return Turn("rejected", msg, r)

    def _classify(self, text: str) -> tuple[str, str]:
        """('finish'|'continue'|'cancel'|'content'|'empty', content_text)."""
        clean = strip_trigger(text)
        n = _norm(clean)
        if not n:
            return "empty", ""
        for kind in ("cancel", "finish", "continue"):
            if n in self._replies[kind]:
                return kind, ""
        # dictated content that ends with a multi-word finish phrase: "... finish note".
        # The phrase is found in the original text (any punctuation between and after
        # its words), and only that span is removed, so numbers and other text stay.
        for phrase in sorted(self._replies["finish"], key=len, reverse=True):
            words = phrase.split()
            if len(words) < 2:
                continue
            m = re.search(r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(map(re.escape, words)) + r"[^a-z0-9]*$",
                          clean, re.IGNORECASE)
            if m and m.start() > 0:
                return "content_finish", clean[:m.start()].rstrip(" ,;:-")
        return "content", clean.strip()

    def _handle_pending(self, text: str) -> Turn:
        p = self.pending
        kind, content = self._classify(text)
        if kind == "cancel":
            self.pending = None
            return Turn("dictation_cancelled", "Dictation cancelled.", pending=p)
        if kind == "continue":
            return Turn("dictation_resumed", "Continue dictating.", pending=p)
        if kind == "finish":
            return self._finish(text)
        if kind == "empty":
            return Turn("unclear_reply",
                        "Please keep dictating, or say 'finish' or 'cancel'.", pending=p)
        p.utterances.append(text)
        p.content = f"{p.content} {content}".strip() if content else p.content
        if kind == "content_finish":
            return self._finish(None)
        return Turn("dictation_continued", "Added to the dictation.", pending=p)

    def _finish(self, text: Optional[str]) -> Turn:
        p = self.pending
        if text is not None:
            p.utterances.append(text)
        self.pending = None
        r = self._complete(p)
        turn = self._decide(r)
        return Turn("dictation_finished", turn.message, r, turn.stage7, pending=p)
