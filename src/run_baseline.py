#!/usr/bin/env python3
"""Run the 198-command test set through Ludyga's cloned ANTLR grammar
pipeline (baseline_ludyga/commandToJson.py) and score pass/fail.

A command "passes" if:
  - it is labelled valid    -> grammar returns a non-empty dict
  - it is labelled invalid  -> grammar returns an empty dict ({})
"""
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = ROOT / "baseline_ludyga"
sys.path.insert(0, str(BASELINE_DIR))

logging.disable(logging.CRITICAL)  # silence Ludyga's internal logging

from commandToJson import get_json  # noqa: E402

TRIGGER = "ok glasses"

def main():
    commands = json.loads((ROOT / "data" / "commands_200.json").read_text())
    results = []
    for cmd in commands:
        spoken = f"{TRIGGER} {cmd['text']}"
        try:
            output = get_json(spoken)
            error = None
        except Exception as e:  # grammar/visitor crash, not a clean reject
            output = {}
            error = f"{type(e).__name__}: {e}"

        produced_output = bool(output)
        expected_output = cmd["valid"]
        correct = produced_output == expected_output

        results.append({
            **cmd,
            "spoken_input": spoken,
            "grammar_output": output,
            "error": error,
            "correct": correct,
        })

    out_path = ROOT / "results" / "baseline_results.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    total = len(results)
    correct_n = sum(r["correct"] for r in results)
    valid = [r for r in results if r["valid"]]
    invalid = [r for r in results if not r["valid"]]
    valid_correct = sum(r["correct"] for r in valid)
    invalid_correct = sum(r["correct"] for r in invalid)

    print(f"Total: {total}, correct: {correct_n} ({correct_n/total:.1%})")
    print(f"  Valid commands accepted:    {valid_correct}/{len(valid)} ({valid_correct/len(valid):.1%})")
    print(f"  Invalid commands rejected:  {invalid_correct}/{len(invalid)} ({invalid_correct/len(invalid):.1%})")
    print(f"Results -> {out_path}")

if __name__ == "__main__":
    main()
