#!/usr/bin/env python3
"""Cross-check our reproduction against Ludyga's own published grammar
outputs in baseline_ludyga_wiki/Evaluation-data.md, instead of only
inferring pass/fail from the valid/invalid labels.

This answers: "how does Ludyga's baseline give accuracy, where are the
correct results" -- they are his own logged transcript of running his
grammar over the same 198 commands, which we parse here.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "baseline_ludyga_wiki" / "Evaluation-data.md"
OURS = ROOT / "results" / "baseline_results.json"

def parse_ludyga_log():
    text = LOG.read_text(encoding="utf-8")
    # restrict to the "Grammar solution output" section only
    section = text.split("# BERT for sequence classifier solution output")[0]
    blocks = re.split(r"\nInput: ", section)[1:]  # drop preamble
    records = {}
    for block in blocks:
        lines = block.splitlines()
        input_text = lines[0].strip()
        out_match = re.search(r"Output:\n(\{.*?\n\}|\{\})", block, re.DOTALL)
        raw_json = out_match.group(1) if out_match else "{}"
        try:
            output = json.loads(raw_json)
        except json.JSONDecodeError:
            output = {}
        records[input_text] = output
    return records

def main():
    ludyga = parse_ludyga_log()
    ours = json.loads(OURS.read_text())

    matched, mismatched, not_found = 0, [], []
    for r in ours:
        spoken = r["spoken_input"]
        # Ludyga's log keys on the raw command text (without "ok glasses " prefix in some, with in others)
        key = r["text"]
        if key not in ludyga:
            not_found.append(key)
            continue
        ludyga_output = ludyga[key]
        same = (bool(ludyga_output) == bool(r["grammar_output"]))
        if same:
            matched += 1
        else:
            mismatched.append({
                "id": r["id"], "text": key,
                "ludyga_output": ludyga_output,
                "our_output": r["grammar_output"],
            })

    print(f"Compared: {len(ours)} commands")
    print(f"  Found in Ludyga's log: {len(ours) - len(not_found)}")
    print(f"  Not found (key mismatch): {len(not_found)}")
    print(f"  Same pass/fail outcome as Ludyga's own run: {matched}")
    print(f"  Different outcome: {len(mismatched)}")
    if mismatched:
        print("\nMismatches:")
        for m in mismatched:
            print(f"  {m['id']}: {m['text']!r}")
            print(f"    Ludyga: {m['ludyga_output']}")
            print(f"    Ours:   {m['our_output']}")
    if not_found:
        print("\nNot found in log (first 10):")
        for t in not_found[:10]:
            print(f"  {t!r}")

if __name__ == "__main__":
    main()
