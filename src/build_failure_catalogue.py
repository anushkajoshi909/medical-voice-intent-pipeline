#!/usr/bin/env python3
"""Build a markdown failure catalogue from results/baseline_results.json.

Three-way taxonomy:
  vocabulary_gap   — synonym / paraphrase not in grammar tokens
  type_mismatch    — entity is semantically wrong type for the action
                     (grammar may accept structurally but wrong concept type)
  abbreviation_gap — abbreviated medical term not expanded by grammar
  syntax_gap       — correct vocab, wrong word order / filler / punctuation
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Medical abbreviations in scope (from abbreviations.json)
ABBREVS = {k.lower() for k in json.loads((ROOT / "abbreviations.json").read_text())}

# Synonyms/paraphrases that replace valid action or entity tokens
SYNONYM_HINTS = [
    # action synonyms
    "illustrate", "present", "bring up", "exhibit", "demonstrate", "reveal",
    "display",
    "halt", "freeze",
    "activate", "disable", "deactivate", "terminate",
    "trigger", "begin", "commence", "code red", "crisis", "catastrophe",
    "cancel",
    # change-patient synonyms
    "switch", "update",
    # note synonyms
    "record note", "add note", "log note",
    # message synonyms
    "start message", "begin message", "start new message", "begin new message",
    # medication synonyms
    "set medication regimen", "set medication protocol",
    "start medication plan", "activate drug regimen",
]

# Type-mismatch patterns: entity is a valid concept but wrong semantic type for the action.
# In the 198-command set these are rare (grammar rejects before semantic validation),
# but we flag commands where the spoken entity is plausibly the WRONG concept type.
TYPE_MISMATCH_HINTS = [
    # procedure used where disease expected, etc.
    ("diagnosis", ["hemodialysis", "dialysis", "surgery", "chemotherapy"]),
    ("start", ["hemolysis", "fever", "pain"]),
]


def classify(text: str, category: str) -> str:
    t = text.lower()

    # Check abbreviation first — any token that is an abbreviation
    words = set(t.split())
    if words & ABBREVS:
        return "abbreviation_gap"

    # Check type mismatch
    for action, entities in TYPE_MISMATCH_HINTS:
        if action in category and any(e in t for e in entities):
            return "type_mismatch"

    # Check synonym / paraphrase
    if any(h in t for h in SYNONYM_HINTS):
        return "vocabulary_gap"

    return "syntax_gap"


def main():
    results = json.loads((ROOT / "results" / "baseline_results.json").read_text())
    failures = [r for r in results if not r["correct"]]
    false_accepts = [r for r in failures if not r["valid"]]
    false_rejects = [r for r in failures if r["valid"]]

    lines = []
    lines.append("# Ludyga Baseline — Failure Catalogue\n")
    lines.append(
        f"Test set: 198 commands (100 invalid, 98 valid).  "
        f"Total failures: {len(failures)} "
        f"(false rejects: {len(false_rejects)}, false accepts: {len(false_accepts)}).\n"
    )
    lines.append("## Taxonomy\n")
    lines.append("| Code | Meaning |")
    lines.append("|---|---|")
    lines.append("| `vocabulary_gap` | Synonym / paraphrase not in grammar token list |")
    lines.append("| `type_mismatch` | Entity is the wrong semantic type for the action |")
    lines.append("| `abbreviation_gap` | Abbreviated medical term not expanded by grammar |")
    lines.append("| `syntax_gap` | Correct vocabulary, but word order / filler doesn't match grammar rule |\n")

    lines.append("## False rejects — valid commands the grammar failed to parse\n")
    lines.append("| ID | Category | Command | Failure type |")
    lines.append("|---|---|---|---|")
    for r in sorted(false_rejects, key=lambda x: x["category"]):
        cause = classify(r["text"], r["category"])
        cmd = r["text"].replace("|", "\\|")
        lines.append(f"| {r['id']} | {r['category']} | {cmd} | `{cause}` |")

    if false_accepts:
        lines.append("\n## False accepts — invalid commands the grammar parsed\n")
        lines.append("| ID | Category | Command | Grammar output |")
        lines.append("|---|---|---|---|")
        for r in false_accepts:
            cmd = r["text"].replace("|", "\\|")
            lines.append(f"| {r['id']} | {r['category']} | {cmd} | `{json.dumps(r['grammar_output'])}` |")
    else:
        lines.append("\n## False accepts\n\nNone — all 100 invalid commands correctly rejected.\n")

    from collections import Counter
    causes = Counter(classify(r["text"], r["category"]) for r in false_rejects)
    lines.append("\n## Failure type breakdown (false rejects only)\n")
    lines.append("| Failure type | Count | % of false rejects |")
    lines.append("|---|---|---|")
    for cause, n in causes.most_common():
        lines.append(f"| `{cause}` | {n} | {n/len(false_rejects):.0%} |")

    lines.append("\n## Coverage gaps in the 198-command test set\n")
    lines.append(
        "The current test set has **zero** commands using medical abbreviations "
        "(e.g. `show BP`, `show SpO2`) and **zero** explicit type-mismatch commands "
        "(e.g. `diagnosis hemodialysis`). Both are in-scope failure modes for the "
        "new pipeline (Stage 4c abbreviation expansion, Stage 5 semantic validation) "
        "and require dedicated test cases — see `data/extended_test_cases.json`."
    )

    out = ROOT / "results" / "failure_catalogue.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    print("\nBreakdown:")
    for cause, n in causes.most_common():
        print(f"  {cause}: {n}")

if __name__ == "__main__":
    main()
