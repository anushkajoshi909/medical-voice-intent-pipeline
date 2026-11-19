#!/usr/bin/env python3
"""Full pipeline runner — runs all commands through every implemented stage.

Stages implemented:
  1  Normalisation + abbreviation expansion  (stage1_preprocess.py)
  2  LLM structured extraction               (stage2_llm_extract.py)
  3  ANTLR frame validation                  (stage3_antlr_validate.py)
  4  Ontology entity lookup                  (stage4_ontology_lookup.py)
  5  Semantic type validation                (stage5_type_validate.py)
  6  Intent JSON assembly                     (stage6_intent_output.py)
  7  User confirmation (--text/--interactive) (stage7_confirmation.py)

Data source: data/annotated_commands.json (216 entries = 198 commands + 18 extended)
  Each entry has expected_action for action-level accuracy scoring.

Usage:
  python src/run_pipeline.py              # full set, uses model from .selected_model
  python src/run_pipeline.py --select     # interactive model picker first
  python src/run_pipeline.py --extended   # extended test cases only (18)
  python src/run_pipeline.py --failures   # baseline grammar-rejected valid commands only
  python src/run_pipeline.py --save       # save results JSON to results/
  python src/run_pipeline.py --interactive  # type commands, full pipeline + Stage 7
  python src/run_pipeline.py --voice        # speak commands (Whisper ASR), full pipeline + Stage 7

Activation: a new command must contain the trigger phrase ("ok glasses");
without it the result has status "inactive" and nothing after the trigger
check runs. Replies inside an open interaction (confirmation, open dictation,
see interaction.py) need no trigger. The dataset commands have no trigger, so
batch runs and run_eval.py prepend it (with_trigger), as for the baseline B0.
"""
import argparse
import re
import codecs
import dataclasses
import json
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from stage1_preprocess import preprocess, detect_trigger
from stage2_llm_extract import extract, get_client, _select_model_interactive, DEFAULT_MODEL
from stage3_antlr_validate import validate as frame_validate
from stage4_ontology_lookup import lookup as ontology_lookup
from stage5_type_validate import validate as type_validate
from stage6_intent_output import assemble as assemble_intent
from stage7_confirmation import run as confirm_intent
from interaction import InteractionSession, PendingDictation
from domain_loader import load as _load_domain

_ACTION_CONFIG: dict = {}


def _action_cfg() -> dict:
    global _ACTION_CONFIG
    if not _ACTION_CONFIG:
        _ACTION_CONFIG = _load_domain()["action_config"]
    return _ACTION_CONFIG


# ── helpers ─────────────────────────────────────────────────────────────────

def load_commands(extended: bool = False, failures_only: bool = False) -> list[dict]:
    all_cmds = json.loads((ROOT / "data" / "annotated_commands.json").read_text())

    if extended:
        return [c for c in all_cmds if c["id"].startswith("ext_")]

    base = [c for c in all_cmds if not c["id"].startswith("ext_")]

    if failures_only:
        baseline = json.loads((ROOT / "results" / "baseline_results.json").read_text())
        fail_ids = {r["id"] for r in baseline if r["valid"] and not r["correct"]}
        return [c for c in base if c["id"] in fail_ids]

    return all_cmds  # full set: 198 base + 18 extended


def with_trigger(cmd: dict) -> dict:
    """Benchmark input preparation: the dataset commands have no trigger phrase,
    because they test command recognition, not activation. Prepend the configured
    trigger ("ok glasses ") unless one is already present. The original command
    dict is not changed. The baseline B0 gets the same prefix in run_eval.py."""
    if detect_trigger(cmd["text"]):
        return cmd
    return dict(cmd, text=f"{_load_domain()['trigger_phrase']} {cmd['text']}")


def _inactive_result(cmd: dict) -> dict:
    """Result for a new command without a trigger phrase: the input is ignored.
    Nothing after the activation check runs (no preprocessing, no LLM call, no
    validation, no lookup, no confirmation). Same keys as a normal result."""
    payload = {
        "status": "inactive",
        "reason": "trigger_not_found",
        "original_command": cmd["text"],
        "explanation": {"stage1": "No activation phrase ('ok glasses') found; "
                                  "the input is ignored and the system keeps listening."},
    }
    return {
        "id": cmd.get("id", "?"), "category": cmd.get("category", "?"),
        "valid": cmd.get("valid"), "expected_action": cmd.get("expected_action"),
        "action_source": cmd.get("action_source", ""), "text": cmd["text"],
        "trigger_found": False, "format_invalid": False, "clean_text": "",
        "abbrevs_expanded": {}, "urgency": [],
        "action": "", "entity": "", "recipient": "", "end_signal": False, "confidence": 0.0,
        "frame_valid": False, "frame_reason": "not run (inactive)", "canonical_form": None,
        "command_type": None,
        "ont_found": False, "ont_canonical": None, "ont_semantic_type": None,
        "ont_ontology_id": None, "ont_match_method": "none", "ont_score": 0.0,
        "ont_source": None, "ont_clarification_type": None,
        "ont_clarification_canonical": None, "ont_drug_list": None,
        "s5_status": "skipped", "s5_reason": "not run (inactive)", "s5_allowed_types": [],
        "s6_status": "inactive", "s6_payload": payload,
        "binary_correct": False,   # ignored input is never a correct recognition
        "action_correct": False,
        "llm_error": None, "model": None,
    }


def run_command(client, cmd: dict, model: str) -> dict:
    """Run one NEW command through Stages 1-6. A command without a trigger phrase
    is ignored (status "inactive") before any other processing. Replies inside an
    active interaction (confirmation, open dictation) do not come through here."""
    if not detect_trigger(cmd["text"]):
        return _inactive_result(cmd)

    stage1 = preprocess(cmd["text"])
    clean  = stage1.clean_text

    # Short-circuit: non-speech format (JSON, code) → reject without LLM call
    if stage1.format_invalid:
        from stage2_llm_extract import LLMResult
        stage2 = LLMResult(action="UNKNOWN", entity="", end_signal=False,
                           confidence=0.0, raw_response="", model=model,
                           error="format_invalid: non-speech input detected by Stage 1")
    else:
        stage2 = extract(client, clean, model=model)

    return validate_extraction(cmd, stage1, stage2)


def complete_dictation(pending: PendingDictation) -> dict:
    """Finish an open note/message (interaction.py): Stages 3-6 on the complete
    dictated content with end_signal=True. No LLM call: the action and recipient
    come from the extraction that opened the dictation."""
    from stage2_llm_extract import LLMResult
    stage1 = preprocess(pending.original_input)
    stage2 = LLMResult(action=pending.action, entity=pending.content, end_signal=True,
                       confidence=pending.confidence, raw_response="",
                       model=pending.model or "", recipient=pending.recipient)
    cmd = {"id": "session", "text": " ".join(pending.utterances), "valid": None,
           "expected_action": None, "category": "session", "action_source": "session"}
    return validate_extraction(cmd, stage1, stage2)


def validate_extraction(cmd: dict, stage1, stage2) -> dict:
    """Stages 3-6 for a given Stage 1 and Stage 2 result (no LLM call)."""
    clean = stage1.clean_text

    # Structural entity enforcement: if action config declares entity_slot=false,
    # strip any entity the LLM extracted before passing to Stage 3.
    entity_for_pipeline = stage2.entity
    if stage2.action not in ("UNKNOWN", ""):
        if not _action_cfg().get(stage2.action, {}).get("entity_slot", True):
            entity_for_pipeline = ""

    stage3 = frame_validate(stage2.action, entity_for_pipeline, recipient=stage2.recipient)

    # Stage 4: ontology lookup — only when stage 3 passed and action is known
    if stage3.valid and stage2.action not in ("UNKNOWN", ""):
        stage4 = ontology_lookup(stage2.action, entity_for_pipeline)
    else:
        from stage4_ontology_lookup import OntologyResult
        stage4 = OntologyResult(False, entity_for_pipeline, None, None, None, "none", 0.0, None)

    # Stage 5: semantic type validation — only when stage 3 passed and action is known
    if stage3.valid and stage2.action not in ("UNKNOWN", ""):
        stage5 = type_validate(stage2.action, stage4)
    else:
        from stage5_type_validate import Stage5Result
        stage5 = Stage5Result("skipped", None, [], "stage 3 rejected or action unknown")

    # Stage 6: assemble final intent JSON from Stages 1-5
    stage6 = assemble_intent(cmd["text"], stage1, stage2, stage3, stage4, stage5, _action_cfg())

    expected = cmd.get("expected_action")
    got      = stage2.action
    # stage2_valid overrides valid for type_mismatch cases:
    # these are end-to-end invalid (Stage 5 catches them) but Stage 2 should
    # extract action+entity faithfully and pass them through.
    s2_valid = cmd.get("stage2_valid", cmd.get("valid"))

    s5_rejects  = stage5.status == "type_mismatch"
    cmd_valid   = cmd.get("valid")

    # Binary accept/reject
    if cmd_valid is False:
        if s2_valid is True:
            # Type-mismatch test cases: Stage 2 extracts correctly, Stage 3 or Stage 5 rejects
            binary_correct = (not stage3.valid or s5_rejects)
        else:
            # Regular invalid commands: any stage rejection counts
            binary_correct = (got == "UNKNOWN" or not stage2.valid_action
                              or not stage3.valid or s5_rejects)
    else:
        # Valid command: Stages 2+3 must pass AND Stage 5 must not type-mismatch
        # entity_not_found = accepted with clarification (intent understood)
        binary_correct = (got != "UNKNOWN" and stage2.valid_action and stage3.valid
                          and stage5.status in ("accepted", "skipped", "entity_not_found"))

    # Action-level correct — for type_mismatch, Stage 2 should return expected_action
    action_correct = (expected is not None) and (got == expected)

    return {
        "id":               cmd.get("id", "?"),
        "category":         cmd.get("category", "?"),
        "valid":            cmd.get("valid"),
        "expected_action":  expected,
        "action_source":    cmd.get("action_source", ""),
        "text":             cmd["text"],
        "trigger_found":    stage1.trigger_found,
        "format_invalid":   stage1.format_invalid,
        "clean_text":       clean,
        "abbrevs_expanded": stage1.abbreviations_expanded,
        "urgency":          stage1.urgency,
        "action":           got,
        "entity":           stage2.entity,
        "recipient":        stage2.recipient,
        "end_signal":       stage2.end_signal,
        "confidence":       stage2.confidence,
        "frame_valid":      stage3.valid,
        "frame_reason":     stage3.reason,
        "canonical_form":   stage3.canonical_form,
        "command_type":     stage3.command_type,
        "ont_found":                   stage4.found,
        "ont_canonical":               stage4.canonical,
        "ont_semantic_type":           stage4.semantic_type,
        "ont_ontology_id":             stage4.ontology_id,
        "ont_match_method":            stage4.match_method,
        "ont_score":                   stage4.score,
        "ont_source":                  stage4.source,
        "ont_clarification_type":      stage4.clarification_type,
        "ont_clarification_canonical": stage4.clarification_canonical,
        "ont_drug_list":               stage4.drug_list,
        "s5_status":        stage5.status,
        "s5_reason":        stage5.reason,
        "s5_allowed_types": stage5.allowed_types,
        "s6_status":        stage6.status,
        "s6_payload":       stage6.payload,
        "binary_correct":   binary_correct,
        "action_correct":   action_correct,
        "llm_error":        stage2.error,
        "model":            stage2.model,
    }


def score(results: list[dict]) -> dict:
    valid   = [r for r in results if r["valid"] is True]
    invalid = [r for r in results if r["valid"] is False]

    # Binary (accept/reject)
    v_accepted  = [r for r in valid   if r["binary_correct"]]
    i_rejected  = [r for r in invalid if r["binary_correct"]]
    total       = len(valid) + len(invalid)
    bin_correct = len(v_accepted) + len(i_rejected)

    # Action-level (valid commands with known expected_action)
    actionable  = [r for r in valid if r["expected_action"] is not None]
    act_correct = [r for r in actionable if r["action_correct"]]

    return {
        "total":              total,
        "valid_total":        len(valid),
        "valid_accepted":     len(v_accepted),
        "valid_rejected":     len(valid) - len(v_accepted),
        "invalid_total":      len(invalid),
        "invalid_rejected":   len(i_rejected),
        "invalid_accepted":   len(invalid) - len(i_rejected),
        "binary_accuracy":    round(bin_correct / total * 100, 1) if total else 0,
        "valid_recall":       round(len(v_accepted) / len(valid) * 100, 1) if valid else 0,
        "invalid_precision":  round(len(i_rejected) / len(invalid) * 100, 1) if invalid else 0,
        "action_accuracy":    round(len(act_correct) / len(actionable) * 100, 1) if actionable else 0,
        "action_correct":     len(act_correct),
        "action_total":       len(actionable),
    }


def print_results(results: list[dict], stats: dict, model: str):
    W = 72
    print(f"\n{'─'*W}")
    print(f"  Pipeline results  |  model: {model}")
    print(f"{'─'*W}")

    fmt_invalid = [r for r in results if r.get("format_invalid")]
    if fmt_invalid:
        print(f"\n  [i] Stage 1 format rejections ({len(fmt_invalid)}) — non-speech input, skipped LLM:")
        for r in fmt_invalid:
            print(f"      {r['id']}  {r['text'][:50]!r}")

    errors = [r for r in results if r["llm_error"] and not r.get("format_invalid")]
    if errors:
        print(f"\n  [!] LLM errors: {len(errors)}")
        for r in errors:
            print(f"      {r['id']}: {r['llm_error']}")

    frame_failures = [r for r in results if not r["frame_valid"] and r["action"] != "UNKNOWN"]
    if frame_failures:
        print(f"\n  [!] Stage 3 frame failures: {len(frame_failures)}")
        for r in frame_failures:
            print(f"      {r['id']}  {r['action']!r}  entity={r['entity']!r}  → {r['frame_reason']}")

    type_mismatches = [r for r in results if r.get("s5_status") == "type_mismatch"]
    if type_mismatches:
        print(f"\n  [!] Stage 5 type mismatches: {len(type_mismatches)}")
        for r in type_mismatches:
            print(f"      {r['id']}  {r['action']!r}  entity={r['entity']!r}  → {r['s5_reason']}")

    entity_not_found = [r for r in results if r.get("s5_status") == "entity_not_found"
                        and r["valid"] is True and r["binary_correct"]]
    if entity_not_found:
        print(f"\n  [?] Stage 5 entity not found ({len(entity_not_found)}) — accepted with clarification:")
        for r in entity_not_found:
            print(f"      {r['id']}  {r['action']!r}  entity={r['entity']!r}")

    print(f"""
  Binary (accept / reject)
    Valid commands  : {stats['valid_accepted']}/{stats['valid_total']} accepted   ({stats['valid_recall']}% recall)
    Invalid commands: {stats['invalid_rejected']}/{stats['invalid_total']} rejected  ({stats['invalid_precision']}% precision)
    Overall binary  : {stats['binary_accuracy']}%
    [Baseline Ludyga: 75.3% | valid recall 50.0% | invalid precision 100%]

  Action-level accuracy (valid commands only)
    Correct action  : {stats['action_correct']}/{stats['action_total']} ({stats['action_accuracy']}%)""")
    print(f"{'─'*W}\n")

    false_rejects = [r for r in results if r["valid"] is True and not r["binary_correct"]]
    false_accepts = [r for r in results if r["valid"] is False and not r["binary_correct"]
                     and not r.get("stage2_valid")]
    type_mismatch_passthrough = [r for r in results if r.get("stage2_valid") is True
                                  and r["valid"] is False and r["binary_correct"]]
    wrong_action  = [r for r in results if r["valid"] is True and r["binary_correct"]
                     and r["expected_action"] and not r["action_correct"]]

    if false_rejects:
        print(f"  False rejects ({len(false_rejects)}) — valid commands missed:")
        for r in false_rejects:
            print(f"    {r['id']}  {r['text'][:42]!r:44s}  → {r['action']!r}  (expected {r['expected_action']!r})")

    if false_accepts:
        print(f"\n  False accepts ({len(false_accepts)}) — invalid commands accepted:")
        for r in false_accepts:
            print(f"    {r['id']}  {r['text'][:42]!r:44s}  → {r['action']!r}")

    if type_mismatch_passthrough:
        print(f"\n  Type-mismatch pass-throughs ({len(type_mismatch_passthrough)}) — Stage 2 correct (Stage 5 catches these):")
        for r in type_mismatch_passthrough:
            print(f"    {r['id']}  {r['text'][:42]!r:44s}  → {r['action']!r}  entity={r['entity']!r}")

    if wrong_action:
        print(f"\n  Wrong action ({len(wrong_action)}) — accepted but wrong action:")
        for r in wrong_action:
            print(f"    {r['id']}  {r['text'][:42]!r:44s}  → {r['action']!r}  (expected {r['expected_action']!r})")

    print()


# ── main ─────────────────────────────────────────────────────────────────────

def _print_stages(r: dict):
    """Print step-by-step output for every pipeline stage."""
    W = 68
    print(f"\n  {'─'*W}")
    print(f"  ID     : {r['id']}")
    print(f"  Input  : {r['text']!r}")
    if r['s6_status'] == "inactive":
        print(f"  ── Stage 1: no trigger phrase -> input ignored (inactive), nothing else runs")
        return
    print(f"  ── Stage 1: Preprocessing")
    print(f"     clean_text : {r['clean_text']!r}")
    print(f"     trigger    : {'found' if r['trigger_found'] else 'not found'}")
    if r['abbrevs_expanded']:
        print(f"     abbrevs    : {r['abbrevs_expanded']}")
    if r['urgency']:
        print(f"     urgency    : {r['urgency']}")
    if r['format_invalid']:
        print(f"     format     : REJECTED (non-speech input)")
        return
    print(f"  ── Stage 2: LLM Extraction")
    print(f"     action     : {r['action']!r}")
    print(f"     entity     : {r['entity']!r}")
    if r.get('recipient'):
        print(f"     recipient  : {r['recipient']!r}")
    print(f"     end_signal : {r['end_signal']}")
    print(f"     confidence : {r['confidence']}")
    if r['llm_error']:
        print(f"     error      : {r['llm_error']}")
    print(f"  ── Stage 3: ANTLR Frame Validation")
    print(f"     canonical  : {r['canonical_form']!r}")
    frame_sym = "✓" if r['frame_valid'] else "✗"
    print(f"     valid      : [{frame_sym}] {r['frame_reason']}")
    if r['frame_valid']:
        print(f"     cmd_type   : {r['command_type']}")
    if r['frame_valid'] and r['action'] not in ("UNKNOWN", ""):
        print(f"  ── Stage 4: Ontology Lookup")
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
            matched_str = f"  matched={r['ont_canonical']!r}" if r['ont_found'] and r['ont_match_method'] not in ("passthrough",) else ""
            print(f"     found      : [{ont_sym}] method={r['ont_match_method']}  score={r['ont_score']:.2f}{matched_str}")
            if r['ont_found']:
                print(f"     sem_type   : {r['ont_semantic_type']}")
                if r['ont_ontology_id']:
                    print(f"     ont_id     : {r['ont_ontology_id']}")
                print(f"     source     : {r['ont_source']}")
        if r.get('ont_clarification_type'):
            print(f"     clarify    : found as {r['ont_clarification_type']!r}"
                  f" ({r['ont_clarification_canonical']!r}) in global search")
        print(f"  ── Stage 5: Semantic Type Validation")
        s5_sym = {"accepted": "✓", "skipped": "–", "entity_not_found": "?", "type_mismatch": "✗"}.get(r['s5_status'], "?")
        print(f"     status     : [{s5_sym}] {r['s5_status']}")
        if r['s5_status'] == "type_mismatch":
            print(f"     reason     : {r['s5_reason']}")
            print(f"     allowed    : {r['s5_allowed_types']}")
    print(f"  ── Stage 6: Intent JSON")
    print(f"     status     : {r['s6_status']}")
    print(json.dumps(r['s6_payload'], indent=6))
    exp = r['expected_action']
    correct = "✓ CORRECT" if r['binary_correct'] else "✗ WRONG"
    print(f"  ── Result: {correct}"
          + (f"  (expected {exp!r})" if exp else ""))


def _run_confirmation(r: dict, respond=None):
    """Run Stage 7 for a single pipeline result and print its outcome (--text mode).

    respond — optional callable(prompt) -> str (e.g. the mic in --voice mode)."""
    result = confirm_intent(r['s6_payload'], r['action'], respond=respond)
    _print_stage7(r, result)
    return result


def _print_stage7(r: dict, result):
    print(f"  ── Stage 7: User Confirmation")
    if result.outcome == "clarification_requested":
        print(f"     [Glasses display] {result.final_message}")
        print(f"     clarification requested: nothing to confirm; the corrected command is a new "
              f"command (start it with 'ok glasses')")
    elif not result.needs_confirmation:
        # Stage 3 structural failures (invalid_structure) carry an actionable
        # clarification_message even though there's nothing for Stage 7 to
        # confirm/reject — surface it here rather than leaving it buried in
        # the raw Stage 6 JSON above.
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


_PASTE_START, _PASTE_END = "\x1b[200~", "\x1b[201~"
_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")          # other escape sequences (arrow keys, ...)


class TerminalLineReader:
    """Reads a line key by key, so that typing is visible before Enter.

    read_char(timeout) returns what was typed (bytes from the terminal, or str in
    tests), or None if nothing was typed before the timeout; on_activity() is
    called for every input. Bytes are decoded with an incremental UTF-8 decoder
    kept on the reader, so a character split between two reads ("ü" in
    "Müller") is not lost. Characters after a newline in the same input (a pasted
    block of several lines) stay in the queue and become the next lines; nothing
    is dropped. The text typed so far is kept across read() calls, so a reminder
    or a discard message never loses it. Without a terminal (piped input) it falls
    back to input(), without timers.

    Pasted text: bracketed_paste() switches the terminal to bracketed paste mode
    (xterm, macOS Terminal, iTerm2, ...), which marks every paste with
    ESC[200~ ... ESC[201~. Pasted characters are kept and used as normal input,
    but never as the answer to a confirmation question (read_answer). A terminal
    without this mode sends a paste unmarked; it then cannot be told apart from
    typing, and a pasted line that arrives after the question counts as typed."""

    def __init__(self, prompt: str, on_activity=lambda: None, read_char=None, write=None,
                 clock=time.monotonic):
        self.prompt = prompt
        self.on_activity = on_activity
        self._read_char = read_char
        self._write = write or (lambda t: (sys.stdout.write(t), sys.stdout.flush()))
        self._clock = clock
        self.buffer: list[str] = []
        self.queue: list[tuple[str, bool]] = []   # (character, pasted) not consumed yet
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._esc = ""                            # an escape sequence split between reads
        self._in_paste = False
        self._answering = False
        self._shown = False

    @property
    def pending_text(self) -> str:
        return "".join(ch for ch, _ in self.queue)

    def bracketed_paste(self, on: bool):
        """Switch bracketed paste mode of the terminal on or off (no effect without a terminal)."""
        if self._read_char is None and sys.stdin.isatty():
            self._write("\x1b[?2004h" if on else "\x1b[?2004l")

    def redraw(self):
        self._write(self.prompt + "".join(self.buffer))
        self._shown = True

    def _with_terminal(self, fn):
        if self._read_char is not None:
            return fn(self._read_char)
        with _cbreak_terminal() as read_char:
            return fn(read_char)

    def read(self, timeout_fn):
        if self._read_char is None and not sys.stdin.isatty():
            return input(self.prompt)
        return self._with_terminal(lambda rc: self._read(timeout_fn, rc))

    def read_answer(self, timeout: Optional[float], prompt: str = "  answer> ") -> Optional[str]:
        """Answer to a confirmation question (Stage 7 read_response). Only keys typed
        after the question count: input that is already waiting when the question is
        shown, and pasted text (bracketed paste), goes to the queue and is handled
        later as normal input, so it can never answer the question. One deadline for
        the whole question (timeout None: wait without limit); keys do not extend it.
        None if the deadline passes (a partly typed answer is then dropped)."""
        if self._read_char is None and not sys.stdin.isatty():
            return _stdin_answer(timeout)
        deadline = None if timeout is None else self._clock() + timeout

        def remaining():
            return None if deadline is None else max(0.0, deadline - self._clock())

        def answer(rc):
            while True:                                  # input already waiting: not an answer
                chunk = rc(0)
                if chunk is None:
                    break
                self._ingest(chunk)
            saved = (self.queue, self.buffer, self._shown, self.prompt)
            self.queue, self.buffer, self._shown, self.prompt = [], [], False, prompt
            self._answering, self._held = True, []
            try:
                return self._read(remaining, rc)
            finally:
                rest, held = self.queue, self._held      # held: pasted while answering
                self.queue, self.buffer, self._shown, self.prompt = saved
                self.queue = self.queue + held + rest
                self._answering, self._shown = False, False
        return self._with_terminal(answer)

    def _ingest(self, chunk):
        """Decode a chunk and add it to the queue, marking pasted characters."""
        text = self._esc + (self._decoder.decode(bytes(chunk))
                            if isinstance(chunk, (bytes, bytearray)) else chunk)
        self._esc = ""
        i = 0
        while i < len(text):
            if text[i] == "\x1b":
                rest = text[i:]
                if rest.startswith(_PASTE_START):
                    self._in_paste, i = True, i + len(_PASTE_START)
                    continue
                if rest.startswith(_PASTE_END):
                    self._in_paste, i = False, i + len(_PASTE_END)
                    continue
                m = _CSI.match(rest)
                if m:
                    i += m.end()                         # ignore other escape sequences
                    continue
                if len(rest) < 8 and not re.search(r"[@-~]", rest[2:]):
                    self._esc = rest                     # incomplete: wait for the rest
                    break
                i += 1                                   # a lone ESC
                continue
            self.queue.append((text[i], self._in_paste))
            i += 1

    def _read(self, timeout_fn, read_char):
        if not self._shown:
            self.redraw()
        while True:
            if not self.queue:
                chunk = read_char(timeout_fn())
                if chunk is None:
                    return None                        # deadline passed without input
                self._ingest(chunk)
                self.on_activity()
            line = self._consume()
            if line is not None:
                return line

    def _consume(self) -> Optional[str]:
        """Apply queued characters to the line; return the line at a newline (the rest
        stays queued), else None. While answering a question, pasted characters are
        held back for later instead."""
        while self.queue:
            (ch, pasted), self.queue = self.queue[0], self.queue[1:]
            if self._answering and pasted:
                self._held.append((ch, pasted))
                continue
            if ch in ("\r", "\n"):
                if ch == "\r" and self.queue and self.queue[0][0] == "\n":
                    self.queue = self.queue[1:]          # \r\n is one line end
                line, self.buffer, self._shown = "".join(self.buffer), [], False
                self._write("\n")
                return line
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch == "\x04" and not self.buffer:
                raise EOFError
            if ch in ("\x7f", "\b"):
                if self.buffer:
                    self.buffer.pop()
                    self._write("\b \b")
            elif ch.isprintable():
                self.buffer.append(ch)
                self._write(ch)
        return None


def _stdin_answer(timeout):
    """Piped input: one line, as stage7_confirmation._wait_for_response does."""
    from stage7_confirmation import _wait_for_response
    return _wait_for_response(timeout)


class _cbreak_terminal:
    """Context manager: terminal in cbreak mode (keys arrive one by one, no echo),
    restored afterwards. Yields read_char(timeout)."""

    def __enter__(self):
        import termios
        import tty
        self._fd = sys.stdin.fileno()
        self._old = termios.tcgetattr(self._fd)
        # TCSANOW: the default (TCSAFLUSH) would throw away input that is already
        # waiting (type-ahead, the rest of a paste) every time a read starts
        tty.setcbreak(self._fd, termios.TCSANOW)
        return self._read_char

    def _read_char(self, timeout):
        import os
        import select as _select
        ready, _, _ = _select.select([self._fd], [], [], timeout)
        if not ready:
            return None
        return os.read(self._fd, 1024)          # bytes: decoded incrementally by the reader

    def __exit__(self, *exc):
        import termios
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
        return False


def _make_session(client, model: str, respond=None, read_response=None) -> InteractionSession:
    return InteractionSession(
        process=lambda text: run_command(client, {"id": "adhoc", "text": text, "valid": None,
                                                  "expected_action": None, "category": "adhoc",
                                                  "action_source": "adhoc"}, model),
        complete=complete_dictation,
        confirm=lambda payload, action: confirm_intent(payload, action, respond=respond,
                                                       read_response=read_response),
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


def _record_and_transcribe(asr_client, ask_enter: bool = True) -> str:
    """Push-to-talk mic capture (reuses listen.py's recording approach),
    then transcribes with the same Whisper endpoint used in whisper_asr.py."""
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    from whisper_asr import transcribe

    SAMPLE_RATE = 16000
    out_path = ROOT / "data" / "audio" / "mic_capture.wav"

    if ask_enter:
        input("Press Enter to start recording...")
    print("Recording... press Enter to stop.")
    frames = []

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback)
    with stream:
        input()
    print("Stopped recording.")

    audio = np.concatenate(frames, axis=0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out_path, audio, SAMPLE_RATE)

    print("Transcribing with Whisper...")
    text = transcribe(asr_client, out_path).strip()
    print(f"Transcript: {text!r}\n")
    return text


def voice_answer(read_answer, record):
    """Stage 7 read_response for push-to-talk voice mode.

    The confirmation deadline applies to pressing Enter (starting the recording):
    push-to-talk cannot hear silence, so not pressing Enter is the silence of
    voice mode. No Enter before the action's confirmation_timeout_sec -> None,
    and Stage 7 confirms automatically, as in text mode. Enter in time -> the
    reply is recorded and transcribed (the recording itself has no deadline).
    An explicit-reply action (timeout None) waits for Enter without limit.
    read_answer(timeout, prompt) is TerminalLineReader.read_answer; record() records
    and transcribes one reply."""
    def read_response(timeout):
        prompt = ("  Press Enter to answer by voice"
                  + (f" (no answer within {timeout:g}s: confirmed automatically)" if timeout else "")
                  + "... ")
        if read_answer(timeout, prompt) is None:
            print()
            return None
        return record()
    return read_response


def _voice_loop(client, model: str):
    """Push-to-talk voice mode: record from the mic, transcribe with Whisper
    (src/whisper_asr.py), then hand the transcript to the same session as
    --interactive (trigger check, open dictation, Stage 7 confirmation).

    The trigger is checked on the transcript: audio is recorded and transcribed
    before activation is known (an audio wake-word detector would be needed to
    avoid that). Push-to-talk has no ambient listening, so the dictation reminder
    can only appear while the system waits for the next recording. A timed
    confirmation (confirmation_timeout_sec > 0) uses the same deadline as text
    mode, applied to pressing Enter (voice_answer).

    Uses the same OpenAI-compatible client for both Whisper and the LLM —
    both are SCADS.AI endpoints under one API key."""
    print("Voice mode — press Enter to record a command (Ctrl+C to quit).\n")

    wait_for_enter = TerminalLineReader("Press Enter to start recording... ")
    answer = voice_answer(wait_for_enter.read_answer,
                          lambda: _record_and_transcribe(client, ask_enter=False))
    session = _make_session(client, model, read_response=answer)
    wait_for_enter.on_activity = session.note_activity
    while True:
        try:
            _wait_with_reminder(session, wait_for_enter)
            session.begin_input()                  # recording + transcription: not inactivity
            try:
                text = _record_and_transcribe(client, ask_enter=False)
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--select",   action="store_true", help="Pick model interactively")
    parser.add_argument("--extended", action="store_true", help="Run extended test cases only")
    parser.add_argument("--failures", action="store_true", help="Run baseline grammar-rejected valid commands")
    parser.add_argument("--save",     action="store_true", help="Save results JSON to results/")
    parser.add_argument("--verbose",  action="store_true", help="Print step-by-step stage output per command")
    parser.add_argument("--sample",   type=int,            help="Run N randomly selected commands")
    parser.add_argument("--text",     type=str,            help="Run a single command string and print verbose output")
    parser.add_argument("--interactive", action="store_true", help="REPL: type commands, run full pipeline incl. Stage 7 confirmation")
    parser.add_argument("--voice",     action="store_true", help="Push-to-talk: speak a command, Whisper transcribes it, full pipeline incl. Stage 7 runs on the transcript")
    args = parser.parse_args()

    # Interactive mode: REPL loop with Stage 7 confirmation
    if args.interactive:
        client = get_client()
        model  = _select_model_interactive(client) if args.select else DEFAULT_MODEL
        _interactive_loop(client, model)
        return

    # Voice mode: mic -> Whisper -> full pipeline incl. Stage 7 confirmation
    if args.voice:
        client = get_client()
        model  = _select_model_interactive(client) if args.select else DEFAULT_MODEL
        _voice_loop(client, model)
        return

    # Single-command mode: run one text string, print verbose, exit
    if args.text:
        client = get_client()
        model  = _select_model_interactive(client) if args.select else DEFAULT_MODEL
        cmd = {"id": "adhoc", "text": args.text, "valid": None, "expected_action": None,
               "category": "adhoc", "action_source": "adhoc"}
        r = run_command(client, cmd, model)
        _print_stages(r)
        if r["s6_status"] == "accepted_partial":
            print("  ── Stage 7: n/a — the dictation is still open (no end signal). One-shot --text "
                  "keeps no session, so it cannot be continued: use --interactive or --voice, "
                  "or end the command with an end phrase such as 'finish note'.")
            return
        _run_confirmation(r)
        return

    client = get_client()
    model  = _select_model_interactive(client) if args.select else DEFAULT_MODEL
    print(f"Model: {model}")

    commands = load_commands(extended=args.extended, failures_only=args.failures)
    label    = "extended" if args.extended else ("failures" if args.failures else "full")

    if args.sample:
        import random
        commands = random.sample(commands, min(args.sample, len(commands)))
        label += f"-sample{args.sample}"
        print(f"Running {len(commands)} randomly sampled commands ({label})...\n")
    else:
        print(f"Running {len(commands)} commands ({label} set)...\n")

    results = []
    t0 = time.time()
    for i, cmd in enumerate(commands, 1):
        r = run_command(client, with_trigger(cmd), model)
        r["text"] = cmd["text"]            # report the dataset text, not the prepared input
        results.append(r)

        if r["valid"] is False:
            sym = "✓" if r["binary_correct"] else "✗"
        else:
            if not r["binary_correct"]:
                sym = "✗"
            elif not r["frame_valid"]:
                sym = "F"
            elif r["expected_action"] and not r["action_correct"]:
                sym = "~"
            else:
                sym = "✓"

        exp = f"(exp:{r['expected_action']})" if r["expected_action"] else ""
        print(f"  [{i:3d}/{len(commands)}] {sym} {r['id']}  {r['text'][:38]!r:40s}"
              f"  → {r['action']:18s} {exp}")
        if args.verbose:
            _print_stages(r)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s ({elapsed/len(commands):.1f}s/cmd)")

    stats = score(results)
    print_results(results, stats, model)

    if args.save:
        def _json_default(o):
            if dataclasses.is_dataclass(o) and not isinstance(o, type):
                return dataclasses.asdict(o)
            return str(o)

        out = ROOT / "results" / f"pipeline_results_{label}.json"
        out.write_text(json.dumps({"model": model, "stats": stats, "results": results},
                                   indent=2, default=_json_default))
        print(f"Saved → {out}")


if __name__ == "__main__":
    main()
