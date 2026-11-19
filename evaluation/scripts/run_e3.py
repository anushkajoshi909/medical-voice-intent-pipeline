#!/usr/bin/env python3
"""E3 - entity resolution and type checking (Stages 4 and 5), no LLM involved.

Reads data/labels/entity_resolution_gold.csv, sends every (action, entity) row through
Stage 4 (ontology lookup) and Stage 5 (type check) of the pipeline and compares the result with the
expected status / type / concept of the row. Writes an append-only run folder

  evaluation/runs/<date>_E3_entity-resolution[_<tag>]/
      results.jsonl   one line per row: gold fields + what Stages 4 and 5 returned + verdicts
      summary.json    metrics (overall, per category, per action)
      summary.md      the same as tables, plus the list of failed rows

Verdicts per row
  status_ok    Stage 5 status equals expected_status (accepted / type_mismatch / entity_not_found)
  type_ok      semantic type equals expected_type (rows with an expected type)
  concept_ok   accepted rows: the resolved concept id (or label, for show) equals the expected one
  full_ok      status_ok and, where the row defines them, type_ok and concept_ok
Error classes
  incorrect_acceptance   expected not accepted, system accepted
  incorrect_rejection    expected accepted, system did not accept
  wrong_concept          expected accepted, system accepted a different concept or type
  wrong_status           expected one non-accepted status, system returned another
Same concept in two ontologies (pneumonia: DOID and HP) counts as correct when the label is the same
and the action allows both types (diagnosis: disease, symptom).

Usage: python evaluation/scripts/run_e3.py [--runs-dir DIR] [--tag TAG]
  --tag   added to the folder name, so a batch made with changed code on the same day gets its own E3
          folder instead of reusing (skipping to) an older one; run_all.py passes its 500-token tag
"""
import argparse
import collections
import csv
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
logging.disable(logging.CRITICAL)

import stage4_ontology_lookup as s4  # noqa: E402
import stage5_type_validate as s5    # noqa: E402

SET = ROOT / "data" / "labels" / "entity_resolution_gold.csv"


def n(s):
    return " ".join(re.sub(r"[^\w\s]", " ", str(s or "").lower()).split())


def resolved_ids(res):
    if getattr(res, "drug_list", None):
        return sorted(d.ontology_id for d in res.drug_list if d.ontology_id)
    return [res.ontology_id] if res.ontology_id else []


def pct(a, b):
    return round(100 * a / b, 1) if b else None


def e3_folder_name(tag=None, day=None):
    name = f"{day or date.today().isoformat()}_E3_entity-resolution"
    return f"{name}_{tag}" if tag else name


def main():
    ap = argparse.ArgumentParser()
    from paths import RUNS
    ap.add_argument("--runs-dir", default=str(RUNS))
    ap.add_argument("--tag", help="suffix of the run folder (e.g. the batch tag)")
    args = ap.parse_args()
    rows = list(csv.DictReader(open(SET, newline="")))
    out = []
    for r in rows:
        res = s4.lookup(r["action"], r["entity"])
        v = s5.validate(r["action"], res)
        exp_ids = sorted(x.strip() for x in r["expected_id"].split(";") if x.strip())
        got_ids = resolved_ids(res)
        status_ok = v.status == r["expected_status"]
        type_ok = None if not r["expected_type"] else v.semantic_type == r["expected_type"]
        concept_ok = None
        if r["expected_status"] == "accepted":
            if exp_ids:
                # same concept listed in two ontologies (e.g. pneumonia in DOID and HP) counts as correct:
                # the id differs but the label is the same
                concept_ok = got_ids == exp_ids or (len(exp_ids) == 1 and n(res.canonical) == n(r["expected_concept"]))
            elif r["expected_type"] in ("vital_sign", "lab_result"):
                concept_ok = n(res.canonical) == n(r["expected_concept"])
        if status_ok and concept_ok and r["category"] != "wrong_type" and r["action"] == "diagnosis":
            type_ok = None if v.semantic_type in ("disease", "symptom") else type_ok  # both types allowed for diagnosis
        full_ok = status_ok and type_ok is not False and concept_ok is not False
        if r["expected_status"] != "accepted" and v.status == "accepted":
            err = "incorrect_acceptance"
        elif r["expected_status"] == "accepted" and v.status != "accepted":
            err = "incorrect_rejection"
        elif r["expected_status"] == "accepted" and not full_ok:
            err = "wrong_concept"
        elif not status_ok:
            err = "wrong_status"
        else:
            err = ""
        out.append({**r, "got_status": v.status, "got_type": v.semantic_type, "got_ids": got_ids,
                    "got_concept": res.canonical, "got_method": res.match_method,
                    "got_score": round(res.score, 3), "got_source": res.source,
                    "got_reason": v.reason, "status_ok": status_ok, "type_ok": type_ok,
                    "concept_ok": concept_ok, "full_ok": full_ok, "error": err})

    def block(items):
        acc = [x for x in items if x["expected_status"] == "accepted"]
        return {"n": len(items), "status_acc_pct": pct(sum(x["status_ok"] for x in items), len(items)),
                "full_acc_pct": pct(sum(x["full_ok"] for x in items), len(items)),
                "incorrect_acceptance": sum(x["error"] == "incorrect_acceptance" for x in items),
                "incorrect_rejection": sum(x["error"] == "incorrect_rejection" for x in items),
                "wrong_concept": sum(x["error"] == "wrong_concept" for x in items),
                "wrong_status": sum(x["error"] == "wrong_status" for x in items),
                "coverage_pct": pct(sum(x["got_status"] == "accepted" for x in acc), len(acc)) if acc else None,
                "n_expected_accepted": len(acc)}

    summary = {"overall": block(out),
               "by_category": {c: block([x for x in out if x["category"] == c]) for c in sorted({x["category"] for x in out})},
               "by_action": {a: block([x for x in out if x["action"] == a]) for a in sorted({x["action"] for x in out})}}
    d = Path(args.runs_dir) / e3_folder_name(args.tag)
    if (d / "summary.json").exists():
        print(f"skip {d.name} (already complete; delete the folder to recompute)")
        return
    d.mkdir(parents=True, exist_ok=False)
    (d / "results.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in out) + "\n")
    (d / "summary.json").write_text(json.dumps(summary, indent=2))
    md = ["# E3 entity resolution and type check (Stages 4-5)", "",
          "| group | n | status acc % | full acc % | incorrect acceptance | incorrect rejection | wrong concept | wrong status | coverage % (expected accepted) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for name, b in [("overall", summary["overall"])] + [(f"category: {k}", v) for k, v in summary["by_category"].items()] + \
                   [(f"action: {k}", v) for k, v in summary["by_action"].items()]:
        md.append(f"| {name} | {b['n']} | {b['status_acc_pct']} | {b['full_acc_pct']} | {b['incorrect_acceptance']} | "
                  f"{b['incorrect_rejection']} | {b['wrong_concept']} | {b['wrong_status']} | {b['coverage_pct']} |")
    md += ["", "## Failed rows", "", "| id | category | action | entity | expected | got | error |", "|---|---|---|---|---|---|---|"]
    for x in out:
        if not x["full_ok"]:
            exp = f"{x['expected_status']} {x['expected_type']} {x['expected_id']}".strip()
            got = f"{x['got_status']} {x['got_type']} {','.join(x['got_ids'])} ({x['got_concept']}, {x['got_method']})"
            md.append(f"| {x['id']} | {x['category']} | {x['action']} | {x['entity']} | {exp} | {got} | {x['error']} |")
    (d / "summary.md").write_text("\n".join(md) + "\n")
    print(f"wrote {d}")
    print(json.dumps(summary["overall"]))


if __name__ == "__main__":
    main()
