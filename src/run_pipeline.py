#!/usr/bin/env python3
"""Full pipeline runner — runs all commands through every implemented stage.

Stages implemented:
  1  Normalisation + abbreviation expansion  (stage1_preprocess.py)
  2  LLM structured extraction               (stage2_llm_extract.py)
  3  Frame validation (ANTLR-equivalent)     (stage3_frame_validate.py)
  [4-5 to be added as implemented]

Data source: data/annotated_commands.json (216 entries = 198 commands + 18 extended)
  Each entry has expected_action for action-level accuracy scoring.

Usage:
  python src/run_pipeline.py              # full set, uses model from .selected_model
  python src/run_pipeline.py --select     # interactive model picker first
  python src/run_pipeline.py --extended   # extended test cases only (18)
  python src/run_pipeline.py --failures   # baseline grammar-rejected valid commands only
  python src/run_pipeline.py --save       # save results JSON to results/
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from stage1_preprocess import preprocess
from stage2_llm_extract import extract, get_client, _select_model_interactive, DEFAULT_MODEL
from stage3_frame_validate import validate as frame_validate


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
    stage3 = frame_validate(stage2.action, stage2.entity)

    expected     = cmd.get("expected_action")
    got          = stage2.action
    # stage2_valid overrides valid for type_mismatch cases:
    # these are end-to-end invalid (Stage 5 catches them) but Stage 2 should
    # extract action+entity faithfully and pass them through.
    s2_valid = cmd.get("stage2_valid", cmd.get("valid"))

    # Binary accept/reject (Stages 2 + 3 must both pass for acceptance)
    if s2_valid is False:
        # Invalid command: correct if Stage 2 OR Stage 3 rejects it
        binary_correct = (got == "UNKNOWN" or not stage2.valid_action or not stage3.valid)
    else:
        # Valid command: correct if Stage 2 maps to known action AND Stage 3 validates frame
        binary_correct = (got != "UNKNOWN" and stage2.valid_action and stage3.valid)

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
        "end_signal":       stage2.end_signal,
        "confidence":       stage2.confidence,
        "frame_valid":      stage3.valid,
        "frame_reason":     stage3.reason,
        "canonical_form":   stage3.canonical_form,
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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--select",   action="store_true", help="Pick model interactively")
    parser.add_argument("--extended", action="store_true", help="Run extended test cases only")
    parser.add_argument("--failures", action="store_true", help="Run baseline grammar-rejected valid commands")
    parser.add_argument("--save",     action="store_true", help="Save results JSON to results/")
    parser.add_argument("--verbose",  action="store_true", help="Print action/entity/frame per command")
    args = parser.parse_args()

    client = get_client()
    model  = _select_model_interactive(client) if args.select else DEFAULT_MODEL
    print(f"Model: {model}")

    commands = load_commands(extended=args.extended, failures_only=args.failures)
    label    = "extended" if args.extended else ("failures" if args.failures else "full")
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
                sym = "F"   # Stage 3 frame failure
            elif r["expected_action"] and not r["action_correct"]:
                sym = "~"   # accepted but wrong action
            else:
                sym = "✓"

        exp = f"(exp:{r['expected_action']})" if r["expected_action"] else ""
        print(f"  [{i:3d}/{len(commands)}] {sym} {r['id']}  {r['text'][:38]!r:40s}"
              f"  → {r['action']:18s} {exp}")
        if args.verbose:
            frame_sym = "✓" if r["frame_valid"] else "✗"
            print(f"          S2: action={r['action']!r}  entity={r['entity']!r}"
                  f"  S3: [{frame_sym}] {r['frame_reason']}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s ({elapsed/len(commands):.1f}s/cmd)")

    stats = score(results)
    print_results(results, stats, model)

    if args.save:
        out = ROOT / "results" / f"pipeline_results_{label}.json"
        out.write_text(json.dumps({"model": model, "stats": stats, "results": results}, indent=2))
        print(f"Saved → {out}")


if __name__ == "__main__":
    main()
