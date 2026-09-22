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
"""
import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from stage1_preprocess import preprocess
from stage2_llm_extract import extract, get_client, _select_model_interactive, DEFAULT_MODEL
from stage3_antlr_validate import validate as frame_validate
from stage4_ontology_lookup import lookup as ontology_lookup
from stage5_type_validate import validate as type_validate
from stage6_intent_output import assemble as assemble_intent
from stage7_confirmation import run as confirm_intent
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


def run_command(client, cmd: dict, model: str) -> dict:
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


def _run_confirmation(r: dict):
    """Run Stage 7 for a single pipeline result and print its outcome."""
    result = confirm_intent(r['s6_payload'], r['action'])
    print(f"  ── Stage 7: User Confirmation")
    if not result.needs_confirmation:
        print(f"     n/a        : nothing to confirm ({result.final_message})")
    else:
        print(f"     outcome    : {result.outcome}")
        print(f"     {result.final_message}")
    return result


def _interactive_loop(client, model: str):
    """REPL: type a command, see it run through the full pipeline including
    Stage 7 confirmation. On REJECTED, you're asked to repeat the command,
    same as the real device flow (Final_Pipeline_Complete.md Stage 7)."""
    print("Interactive mode — type a command (or 'exit' to quit).\n")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text or text.lower() in ("exit", "quit"):
            break

        cmd = {"id": "adhoc", "text": text, "valid": None, "expected_action": None,
               "category": "adhoc", "action_source": "adhoc"}
        r = run_command(client, cmd, model)
        _print_stages(r)
        result = _run_confirmation(r)

        if result.outcome == "rejected" or result.outcome == "unclear":
            print("\n  Please repeat the command.\n")
        print()


def _record_and_transcribe(asr_client) -> str:
    """Push-to-talk mic capture (reuses listen.py's recording approach),
    then transcribes with the same Whisper endpoint used in whisper_asr.py."""
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    from whisper_asr import transcribe

    SAMPLE_RATE = 16000
    out_path = ROOT / "data" / "audio" / "mic_capture.wav"

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


def _voice_loop(client, model: str):
    """Push-to-talk voice mode: record from the mic, transcribe with Whisper
    (src/whisper_asr.py), then run the transcript through the same 7-stage
    pipeline as --interactive, including Stage 7 confirmation. One command
    end-to-end: say it, the pipeline triggers on the transcript.

    Uses the same OpenAI-compatible client for both Whisper and the LLM —
    both are SCADS.AI endpoints under one API key."""
    print("Voice mode — press Enter to record a command (Ctrl+C to quit).\n")
    while True:
        try:
            text = _record_and_transcribe(client)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            print("  (empty transcript — try again)\n")
            continue

        cmd = {"id": "voice", "text": text, "valid": None, "expected_action": None,
               "category": "voice", "action_source": "voice"}
        r = run_command(client, cmd, model)
        _print_stages(r)
        result = _run_confirmation(r)

        if result.outcome in ("rejected", "unclear"):
            print("\n  Please repeat the command.\n")
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
        r = run_command(client, cmd, model)
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
