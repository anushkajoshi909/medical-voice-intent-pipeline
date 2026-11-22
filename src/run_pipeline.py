#!/usr/bin/env python3
"""Pipeline runner: Stages 1-6 for one command, and the three ways to use it.

Stages:
  1  Normalisation + abbreviation expansion  (stage1_preprocess.py)
  2  LLM structured extraction               (stage2_llm_extract.py)
  3  ANTLR frame validation                  (stage3_antlr_validate.py)
  4  Ontology entity lookup                  (stage4_ontology_lookup.py)
  5  Semantic type validation                (stage5_type_validate.py)
  6  Intent JSON assembly                     (stage6_intent_output.py)
  7  User confirmation                        (stage7_confirmation.py)

Usage:
  python src/run_pipeline.py --text "ok glasses show heart rate"   # one command, every stage printed
  python src/run_pipeline.py --interactive                          # typed session, incl. Stage 7
  python src/run_pipeline.py --voice                                # spoken session (Whisper), incl. Stage 7
  add --select to pick the model first

The evaluation on the dataset is evaluation/scripts/run_eval.py (it calls
run_command() and with_trigger() below).

Activation: a new command must contain the trigger phrase ("ok glasses");
without it the result has status "inactive" and nothing after the trigger
check runs. Replies inside an open interaction (confirmation, open dictation,
see interaction.py) need no trigger.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from domain_loader import load as _load_domain
from interaction import InteractionSession, PendingDictation
from stage1_preprocess import preprocess, detect_trigger
from stage2_llm_extract import LLMResult, extract, get_client, select_model_interactive, DEFAULT_MODEL
from stage3_antlr_validate import validate as frame_validate
from stage4_ontology_lookup import lookup as ontology_lookup, not_found
from stage5_type_validate import Stage5Result, validate as type_validate
from stage6_intent_output import assemble as assemble_intent
from stage7_confirmation import run as confirm_intent
from terminal_input import TerminalLineReader
from voice import record_and_transcribe, voice_answer

# run_eval.py replaces preprocess, extract, frame_validate, ontology_lookup,
# type_validate and assemble_intent in this module with timed versions, so these
# names must stay module-level and be looked up at call time.


def _action_cfg() -> dict:
    return _load_domain()["action_config"]


# ── one command through Stages 1-6 ───────────────────────────────────────────

def adhoc_command(text: str, source: str = "adhoc") -> dict:
    """A command typed or spoken by the user: no dataset labels."""
    return {"id": source, "text": text, "valid": None, "expected_action": None,
            "category": source, "action_source": source}


def with_trigger(cmd: dict) -> dict:
    """Benchmark input preparation: the dataset commands have no trigger phrase,
    because they test command recognition, not activation. Prepend the configured
    trigger ("ok glasses ") unless one is already present. The original command
    dict is not changed. The baseline B0 gets the same prefix in run_eval.py."""
    if detect_trigger(cmd["text"]):
        return cmd
    return dict(cmd, text=f"{_load_domain()['trigger_phrase']} {cmd['text']}")


# Result fields of a stage that did not run (used for an ignored command, and as
# the base of every result row, so both always have the same keys).
_NOT_RUN = {
    "trigger_found": False, "format_invalid": False, "clean_text": "",
    "abbrevs_expanded": {}, "urgency": [],
    "action": "", "entity": "", "recipient": "", "end_signal": False, "confidence": 0.0,
    "frame_valid": False, "frame_reason": "not run", "canonical_form": None, "command_type": None,
    "ont_found": False, "ont_canonical": None, "ont_semantic_type": None,
    "ont_ontology_id": None, "ont_match_method": "none", "ont_score": 0.0,
    "ont_source": None, "ont_clarification_type": None,
    "ont_clarification_canonical": None, "ont_drug_list": None,
    "s5_status": "skipped", "s5_reason": "not run", "s5_allowed_types": [],
    "s6_status": None, "s6_payload": None,
    "action_correct": False,
    "llm_error": None, "model": None,
}


def _row(cmd: dict, **fields) -> dict:
    """One result row: the command's own fields, then the stage results."""
    return {"id": cmd.get("id", "?"), "category": cmd.get("category", "?"),
            "valid": cmd.get("valid"), "expected_action": cmd.get("expected_action"),
            "action_source": cmd.get("action_source", ""), "text": cmd["text"],
            **_NOT_RUN, **fields}


def _inactive_result(cmd: dict) -> dict:
    """Result for a new command without a trigger phrase: the input is ignored.
    Nothing after the activation check runs (no preprocessing, no LLM call, no
    validation, no lookup, no confirmation)."""
    payload = {
        "status": "inactive",
        "reason": "trigger_not_found",
        "original_command": cmd["text"],
        "explanation": {"stage1": "No activation phrase ('ok glasses') found; "
                                  "the input is ignored and the system keeps listening."},
    }
    return _row(cmd, frame_reason="not run (inactive)", s5_reason="not run (inactive)",
                s6_status="inactive", s6_payload=payload)


def run_command(client, cmd: dict, model: str) -> dict:
    """Run one NEW command through Stages 1-6. A command without a trigger phrase
    is ignored (status "inactive") before any other processing. Replies inside an
    active interaction (confirmation, open dictation) do not come through here."""
    if not detect_trigger(cmd["text"]):
        return _inactive_result(cmd)

    stage1 = preprocess(cmd["text"])

    # Non-speech format (JSON, code): rejected without an LLM call
    if stage1.format_invalid:
        stage2 = LLMResult(action="UNKNOWN", entity="", end_signal=False,
                           confidence=0.0, raw_response="", model=model,
                           error="format_invalid: non-speech input detected by Stage 1")
    else:
        stage2 = extract(client, stage1.clean_text, model=model)

    return validate_extraction(cmd, stage1, stage2)


def complete_dictation(pending: PendingDictation) -> dict:
    """Finish an open note/message (interaction.py): Stages 3-6 on the complete
    dictated content with end_signal=True. No LLM call: the action and recipient
    come from the extraction that opened the dictation."""
    stage1 = preprocess(pending.original_input)
    stage2 = LLMResult(action=pending.action, entity=pending.content, end_signal=True,
                       confidence=pending.confidence, raw_response="",
                       model=pending.model or "", recipient=pending.recipient)
    cmd = adhoc_command(" ".join(pending.utterances), source="session")
    return validate_extraction(cmd, stage1, stage2)


def validate_extraction(cmd: dict, stage1, stage2) -> dict:
    """Stages 3-6 for a given Stage 1 and Stage 2 result (no LLM call)."""
    known_action = stage2.action not in ("UNKNOWN", "")

    # An action without an entity slot (entity_slot: false) ignores any entity the LLM extracted
    entity = stage2.entity
    if known_action and not _action_cfg().get(stage2.action, {}).get("entity_slot", True):
        entity = ""

    stage3 = frame_validate(stage2.action, entity, recipient=stage2.recipient)

    # Stages 4 and 5 run only for a known action with a valid frame
    if stage3.valid and known_action:
        stage4 = ontology_lookup(stage2.action, entity)
        stage5 = type_validate(stage2.action, stage4)
    else:
        stage4 = not_found(entity)
        stage5 = Stage5Result("skipped", None, [], "stage 3 rejected or action unknown")

    stage6 = assemble_intent(cmd["text"], stage1, stage2, stage3, stage4, stage5, _action_cfg())

    expected = cmd.get("expected_action")
    return _row(
        cmd,
        trigger_found=stage1.trigger_found, format_invalid=stage1.format_invalid,
        clean_text=stage1.clean_text, abbrevs_expanded=stage1.abbreviations_expanded,
        urgency=stage1.urgency,
        action=stage2.action, entity=stage2.entity, recipient=stage2.recipient,
        end_signal=stage2.end_signal, confidence=stage2.confidence,
        frame_valid=stage3.valid, frame_reason=stage3.reason,
        canonical_form=stage3.canonical_form, command_type=stage3.command_type,
        ont_found=stage4.found, ont_canonical=stage4.canonical,
        ont_semantic_type=stage4.semantic_type, ont_ontology_id=stage4.ontology_id,
        ont_match_method=stage4.match_method, ont_score=stage4.score, ont_source=stage4.source,
        ont_clarification_type=stage4.clarification_type,
        ont_clarification_canonical=stage4.clarification_canonical,
        ont_drug_list=stage4.drug_list,
        s5_status=stage5.status, s5_reason=stage5.reason, s5_allowed_types=stage5.allowed_types,
        s6_status=stage6.status, s6_payload=stage6.payload,
        # did Stage 2 return the labelled action (dataset commands only)
        action_correct=expected is not None and stage2.action == expected,
        llm_error=stage2.error, model=stage2.model,
    )


# ── printing ─────────────────────────────────────────────────────────────────

def _print_stages(r: dict):
    """Print step-by-step output for every pipeline stage."""
    W = 68
    print(f"\n  {'─'*W}")
    print(f"  ID     : {r['id']}")
    print(f"  Input  : {r['text']!r}")
    if r['s6_status'] == "inactive":
        print("  ── Stage 1: no trigger phrase -> input ignored (inactive), nothing else runs")
        return
    print("  ── Stage 1: Preprocessing")
    print(f"     clean_text : {r['clean_text']!r}")
    print(f"     trigger    : {'found' if r['trigger_found'] else 'not found'}")
    if r['abbrevs_expanded']:
        print(f"     abbrevs    : {r['abbrevs_expanded']}")
    if r['urgency']:
        print(f"     urgency    : {r['urgency']}")
    if r['format_invalid']:
        print("     format     : REJECTED (non-speech input)")
        return
    print("  ── Stage 2: LLM Extraction")
    print(f"     action     : {r['action']!r}")
    print(f"     entity     : {r['entity']!r}")
    if r.get('recipient'):
        print(f"     recipient  : {r['recipient']!r}")
    print(f"     end_signal : {r['end_signal']}")
    print(f"     confidence : {r['confidence']}")
    if r['llm_error']:
        print(f"     error      : {r['llm_error']}")
    print("  ── Stage 3: ANTLR Frame Validation")
    print(f"     canonical  : {r['canonical_form']!r}")
    frame_sym = "✓" if r['frame_valid'] else "✗"
    print(f"     valid      : [{frame_sym}] {r['frame_reason']}")
    if r['frame_valid']:
        print(f"     cmd_type   : {r['command_type']}")
    if r['frame_valid'] and r['action'] not in ("UNKNOWN", ""):
        print("  ── Stage 4: Ontology Lookup")
        drug_list = r.get('ont_drug_list')
        if drug_list:
            print(f"     compound   : {len(drug_list)} drugs")
            for i, d in enumerate(drug_list, 1):
                sym = "✓" if d.found else "✗"
                id_str = f"  id={d.ontology_id}" if d.ontology_id else ""
                dose_str = f"  dose={d.dose!r}" if d.dose else ""
                can_str = f" → {d.canonical!r}" if d.found else ""
                print(f"     drug {i}     : [{sym}] {d.medicine!r}{can_str}{id_str}{dose_str}  ({d.match_method}, {d.score:.2f})")
            print(f"     sem_type   : {r['ont_semantic_type']}")
        else:
            ont_sym = "✓" if r['ont_found'] else "✗"
            matched_str = (f"  matched={r['ont_canonical']!r}"
                           if r['ont_found'] and r['ont_match_method'] != "passthrough" else "")
            print(f"     found      : [{ont_sym}] method={r['ont_match_method']}  score={r['ont_score']:.2f}{matched_str}")
            if r['ont_found']:
                print(f"     sem_type   : {r['ont_semantic_type']}")
                if r['ont_ontology_id']:
                    print(f"     ont_id     : {r['ont_ontology_id']}")
                print(f"     source     : {r['ont_source']}")
        if r.get('ont_clarification_type'):
            print(f"     clarify    : found as {r['ont_clarification_type']!r}"
                  f" ({r['ont_clarification_canonical']!r}) in global search")
        print("  ── Stage 5: Semantic Type Validation")
        s5_sym = {"accepted": "✓", "skipped": "–", "entity_not_found": "?", "type_mismatch": "✗"}.get(r['s5_status'], "?")
        print(f"     status     : [{s5_sym}] {r['s5_status']}")
        if r['s5_status'] == "type_mismatch":
            print(f"     reason     : {r['s5_reason']}")
            print(f"     allowed    : {r['s5_allowed_types']}")
    print("  ── Stage 6: Intent JSON")
    print(f"     status     : {r['s6_status']}")
    print(json.dumps(r['s6_payload'], indent=6))


def _print_stage7(r: dict, result):
    print("  ── Stage 7: User Confirmation")
    if result.outcome == "clarification_requested":
        print(f"     [Glasses display] {result.final_message}")
        print("     clarification requested: nothing to confirm; the corrected command is a new "
              "command (start it with 'ok glasses')")
    elif not result.needs_confirmation:
        # A structurally incomplete command (Stage 3) carries a message saying what
        # is missing, although there is nothing to confirm: show it here.
        clarification = r['s6_payload'].get('clarification_message')
        if clarification:
            print(f"     [Glasses display] {clarification}")
        print(f"     n/a        : nothing to confirm ({result.final_message})")
    else:
        print(f"     outcome    : {result.outcome}")
        print(f"     {result.final_message}")


def _print_turn(turn):
    """Print one session turn: the stage output (if a pipeline result exists) and
    what the session does next."""
    if turn.result is not None:
        _print_stages(turn.result)
    if turn.stage7 is not None:
        _print_stage7(turn.result, turn.stage7)
    if turn.kind == "dictation_open":
        print(f"\n  [Glasses display] {turn.message}")
    elif turn.kind in ("dictation_continued", "dictation_resumed", "dictation_cancelled", "unclear_reply"):
        print(f"  [Glasses display] {turn.message}")
        if turn.pending is not None and turn.kind != "dictation_cancelled":
            print(f"     dictation so far: {turn.pending.content!r}")
    elif turn.kind == "inactive":
        print(f"  [{turn.message}]")
    elif turn.kind == "rejected":
        print(f"\n  [Glasses display] {turn.message}")
    if turn.stage7 is not None and turn.stage7.outcome in ("rejected", "unclear"):
        print("\n  Please repeat the command.")


# ── sessions (--interactive, --voice) ────────────────────────────────────────

def _wait_with_reminder(session: InteractionSession, reader):
    """Wait for the next line. While a dictation is open and the user is inactive,
    show the completion question once; if it gets no answer, discard the open
    dictation (never complete or send it). Keystrokes count as activity, so typing
    a long continuation never triggers the reminder or the discard. reader is a
    TerminalLineReader: read(timeout_fn) returns a line, or None if the deadline
    passed without a keystroke (the text typed so far is kept)."""
    while True:
        line = reader.read(session.seconds_until_next_event)
        if line is not None:
            return line
        msg = session.reminder()
        if msg:
            print(f"\n  [Glasses display] {msg}  (say 'finish' or 'cancel', or keep dictating)")
            reader.redraw()
            continue
        msg = session.expire()
        if msg:
            print(f"\n  [Glasses display] {msg}")
            reader.redraw()


def _make_session(client, model: str, read_response=None) -> InteractionSession:
    return InteractionSession(
        process=lambda text: run_command(client, adhoc_command(text), model),
        complete=complete_dictation,
        confirm=lambda payload, action: confirm_intent(payload, action, read_response=read_response),
        settings=_load_domain()["interaction"],
    )


def _interactive_loop(client, model: str):
    """REPL: type commands; the session keeps an open dictation across turns and
    applies Stage 7. A new command needs 'ok glasses'; replies inside an open
    dictation or a confirmation do not."""
    print("Interactive mode — type a command starting with 'ok glasses' (or 'exit' to quit).\n")
    read = TerminalLineReader("> ")
    session = _make_session(client, model, read_response=read.read_answer)
    read.on_activity = session.note_activity
    read.bracketed_paste(True)                     # pastes are marked, never a confirmation answer
    try:
        while True:
            try:
                text = _wait_with_reminder(session, read).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if text.lower() in ("exit", "quit"):
                break
            if not text:
                continue
            _print_turn(session.handle(text))
            print()
    finally:
        read.bracketed_paste(False)


def _voice_loop(client, model: str):
    """Push-to-talk voice mode: record from the mic, transcribe with Whisper
    (src/whisper_asr.py), then hand the transcript to the same session as
    --interactive (trigger check, open dictation, Stage 7 confirmation).

    The trigger is checked on the transcript: audio is recorded and transcribed
    before activation is known (an audio wake-word detector would be needed to
    avoid that). Push-to-talk has no ambient listening, so the dictation reminder
    can only appear while the system waits for the next recording. A timed
    confirmation (confirmation_timeout_sec > 0) uses the same deadline as text
    mode, applied to pressing Enter (voice.voice_answer).

    Uses the same OpenAI-compatible client for both Whisper and the LLM —
    both are SCADS.AI endpoints under one API key."""
    print("Voice mode — press Enter to record a command (Ctrl+C to quit).\n")

    wait_for_enter = TerminalLineReader("Press Enter to start recording... ")
    answer = voice_answer(wait_for_enter.read_answer,
                          lambda: record_and_transcribe(client, ask_enter=False))
    session = _make_session(client, model, read_response=answer)
    wait_for_enter.on_activity = session.note_activity
    while True:
        try:
            _wait_with_reminder(session, wait_for_enter)
            session.begin_input()                  # recording + transcription: not inactivity
            try:
                text = record_and_transcribe(client, ask_enter=False)
            finally:
                session.end_input()                # also for an empty transcript (handle() not called)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            print("  (empty transcript — try again)\n")
            continue
        _print_turn(session.handle(text))
        print()


# ── command line ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run the pipeline on one command or in a session. "
                                                 "The dataset evaluation is evaluation/scripts/run_eval.py.")
    parser.add_argument("--text", type=str, help="Run one command and print every stage, then Stage 7")
    parser.add_argument("--interactive", action="store_true", help="Typed session, incl. Stage 7 confirmation")
    parser.add_argument("--voice", action="store_true", help="Push-to-talk session: Whisper transcript, incl. Stage 7")
    parser.add_argument("--select", action="store_true", help="Pick the model interactively first")
    args = parser.parse_args()
    if not (args.text or args.interactive or args.voice):
        parser.print_help()
        return

    client = get_client()
    model = select_model_interactive(client) if args.select else DEFAULT_MODEL

    if args.interactive:
        _interactive_loop(client, model)
    elif args.voice:
        _voice_loop(client, model)
    else:
        r = run_command(client, adhoc_command(args.text), model)
        _print_stages(r)
        if r["s6_status"] == "accepted_partial":
            print("  ── Stage 7: n/a — the dictation is still open (no end signal). One-shot --text "
                  "keeps no session, so it cannot be continued: use --interactive or --voice, "
                  "or end the command with an end phrase such as 'finish note'.")
            return
        _print_stage7(r, confirm_intent(r["s6_payload"], r["action"]))


if __name__ == "__main__":
    main()
