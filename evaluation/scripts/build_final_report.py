#!/usr/bin/env python3
"""Aggregate one evaluation batch into three files in evaluation/reports/final/.

A batch = all complete run folders in evaluation/runs/ with the given date and dataset tags
(default: today, tags final500 and final4000), plus one baseline folder and one E3 entity-resolution folder.
The script only READS run folders; it never calls an LLM or a pipeline stage.

Gold labels (data/labels/gold_labels.json) are joined on the command id when the report is built, so
entity accuracy and end-signal metrics can also be computed for runs that were made before the labels existed.

Files written (evaluation/reports/final/)
  all_results_<date>.json   ALL results of all runs in one file: per system (model + token budget) mean, SD and the
                            value of every repetition for extraction, decisions, LLM alone versus full pipeline,
                            latency and explainability coverage, plus the Ludyga baseline B0 and E3. Starts with an
                            "about" block that defines every metric. Not meant to be read as a report.
  main_results_<date>.md    the report: one table with the main metrics per system, and a short E3 table.
  per_command_<date>.csv    one row per command, per system and repetition: extracted entity against the gold entity,
                            outcome, and the latency of every stage. Lines starting with '#' at the top explain the columns.

Usage: python evaluation/scripts/build_final_report.py [--date YYYY-MM-DD] [--also-dates YYYY-MM-DD ...] [--tags final500 final4000]
         [--baseline <B0 run folder name>] [--e3 <E3 run folder name>]
Example (first run, B0 and E3 of 2 Nov):
  python evaluation/scripts/build_final_report.py --date 2026-11-03 --tags keyword-replay \
      --baseline 2026-11-03_B0-ludyga_final500_r1 --e3 2026-11-03_E3_entity-resolution
Example (second run, run over midnight):
  python evaluation/scripts/build_final_report.py --date 2026-11-05 --also-dates 2026-11-04 --tags final500 final4000
"""
import argparse
import collections
import csv
import json
import re
import statistics as st
from datetime import date as _date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
from metric_labels import ACTION_LABEL, ENTITY_LABEL, END_SIGNAL_LABEL, label_counts  # noqa: E402
from paths import RUNS, REPORTS  # noqa: E402  (evaluation/final_run/ by default)
OUT = REPORTS / "final"
STAGES = ["stage1", "stage2", "stage3", "stage4", "stage5", "stage6"]


def norm_entity(s):
    """Same comparison as run_eval.py: lowercase, punctuation ignored."""
    return " ".join(re.sub(r"[^\w\s/]", " ", str(s or "").lower()).split())


def agg(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"mean": round(st.mean(vals), 2), "sd": round(st.stdev(vals), 2) if len(vals) > 1 else 0.0,
            "values": vals}


def pct(a, b):
    return round(100 * a / b, 1) if b else None


GOLD_FILE = ROOT / "data" / "labels" / "gold_labels.json"


def load_gold():
    return json.loads(GOLD_FILE.read_text()) if GOLD_FILE.exists() else {}


def load_batch(day, tags, baseline=None, also_dates=()):
    systems = collections.defaultdict(list)
    for dd in (day, *also_dates):          # a batch that runs over midnight has run folders of two dates
        for d in sorted(RUNS.glob(f"{dd}_*")):
            mf = d / "meta.json"
            if not mf.exists():
                continue
            meta = json.loads(mf.read_text())
            if meta.get("status") != "complete" or meta.get("dataset_tag") not in tags:
                continue
            systems[meta.get("system_label") or meta["system"]].append((d, meta))
    # a label with fresh runs and replays (same model, same token budget): keep the fresh runs only
    for label, runs in list(systems.items()):
        fresh = [r for r in runs if not r[1].get("replay_of")]
        if fresh and len(fresh) != len(runs):
            systems[label] = fresh
    if baseline:
        bd = RUNS / baseline
        bm = json.loads((bd / "meta.json").read_text())
        systems[bm.get("system_label") or bm["system"]].append((bd, bm))
    return systems


def per_command(d, meta, label, gold):
    rows = []
    for line in open(d / "results.jsonl"):
        r = json.loads(line)
        x, g, t = r["result"], gold.get(r["id"], {}), r["timings"]
        ent_ok = None if "entity" not in g or not r["valid"] else norm_entity(x["entity"]) == norm_entity(g["entity"])
        sig_ok = None if "end_signal" not in g else bool(x["end_signal"]) == bool(g["end_signal"])
        rows.append({
            "system": label, "max_tokens": meta["decoding"]["max_tokens"], "rep": meta["rep"], "id": r["id"],
            "category": r["category"], "text": r["text"], "gold_valid": r["valid"],
            "expected_action": r.get("expected_action"), "pred_action": x["action"],
            "action_correct": x.get("action_correct"), "gold_entity": g.get("entity"), "pred_entity": x["entity"],
            "entity_ok": ent_ok, "gold_end_signal": g.get("end_signal"), "pred_end_signal": x["end_signal"],
            "end_signal_ok": sig_ok, "outcome": r["outcome"], "llm_error": x.get("llm_error"),
            **{f"{s}_s": t.get(s) for s in STAGES}, "end_to_end_s": t.get("end_to_end")})
    return rows


COLUMN_NOTES = [
    "per_command_<date>.csv, written by evaluation/scripts/build_final_report.py from results.jsonl of the run folders.",
    "One row = one command in one repetition of one system. system = model name (+ __maxtok4000 for the 4000-token budget).",
    "pred_* = what Stage 2 returned; gold_* = human-checked label (data/labels/gold_labels.json); empty = no gold label.",
    "entity_ok compares entity text ignoring case and punctuation; only valid commands with a gold entity. end_signal_ok likewise.",
    "outcome = ACCEPT / CLARIFY / REJECT of the full pipeline. *_s = seconds spent in that stage; end_to_end_s = whole command.",
    "stage2_s is the LLM call (empty when no LLM call was made, e.g. input rejected in Stage 1).",
]


def extraction_metrics(rows):
    """Entity accuracy and end-signal precision/recall/F1 for ONE repetition (list of per-command rows)."""
    ent = [r for r in rows if r["entity_ok"] is not None]
    sig = [r for r in rows if r["gold_end_signal"] is not None]
    tp = sum(1 for r in sig if r["gold_end_signal"] and r["pred_end_signal"])
    fp = sum(1 for r in sig if not r["gold_end_signal"] and r["pred_end_signal"])
    fn = sum(1 for r in sig if r["gold_end_signal"] and not r["pred_end_signal"])
    return {"entity_accuracy_pct": pct(sum(r["entity_ok"] for r in ent), len(ent)) if ent else None, "entity_gold_n": len(ent),
            "end_signal_precision_pct": pct(tp, tp + fp), "end_signal_recall_pct": pct(tp, tp + fn),
            "end_signal_f1_pct": pct(2 * tp, 2 * tp + fp + fn), "end_signal_n": len(sig)}


def main():
    global RUNS
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=_date.today().isoformat())
    ap.add_argument("--tags", nargs="+", default=["final500", "final4000"])
    ap.add_argument("--also-dates", nargs="*", default=[], help="further dates whose run folders belong to the batch (a batch that ran over midnight)")
    ap.add_argument("--baseline", help="name of the B0 run folder to include (default: the B0 run of the batch)")
    ap.add_argument("--e3", help="name of the E3 run folder (default: the E3 run of the batch date)")
    ap.add_argument("--out", default=str(OUT), help="output folder (default evaluation/final_run/reports/final)")
    ap.add_argument("--runs-dir", default=str(RUNS),
                    help="folder with the run folders (default evaluation/final_run/runs; old runs: evaluation/old_runs/runs)")
    args = ap.parse_args()
    RUNS = Path(args.runs_dir)
    out_dir = Path(args.out)
    systems = load_batch(args.date, args.tags, args.baseline, args.also_dates)
    gold = load_gold()
    if not systems:
        raise SystemExit(f"no complete runs for {args.date} with tags {args.tags}")
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {"systems": {}}
    all_rows = []
    runs_used = []
    for label, runs in sorted(systems.items()):
        if label.startswith("B0"):
            s = json.loads((runs[0][0] / "summary.json").read_text())
            result["baseline_B0_ludyga"] = {"runs": len(runs), "all_243_items": s["all_items"],
                                            "ludyga_198_sentences_only": s["ludyga_198_only"],
                                            "end_to_end_latency_s": s["latency"]["end_to_end"]}
            runs_used += [d.name for d, _ in runs]
            continue
        sums = [json.loads((d / "summary.json").read_text()) for d, _ in runs]
        per_run = [per_command(d, m, label, gold) for d, m in runs]
        rows = [r for pr in per_run for r in pr]
        em = [extraction_metrics(pr) for pr in per_run]
        all_rows += rows
        runs_used += [d.name for d, _ in runs]
        e2e = [r["end_to_end_s"] for r in rows if r["end_to_end_s"] is not None]
        ent = [r for r in rows if r["entity_ok"] is not None]
        h = lambda k: agg([s["hybrid"][k] for s in sums])
        lat = lambda stg, k: agg([s["latency"][stg][k] for s in sums if stg in s["latency"]])
        slow = agg([round(1000 * max(s["latency"][g]["max_s"] for g in STAGES if g != "stage2" and g in s["latency"]), 1) for s in sums])
        result["systems"][label] = {
            "source": ("replay of " + runs[0][1]["replay_of"].rsplit("_r", 1)[0] + ": Stage 2 outputs and Stage 2 time taken from that run, Stages 1 and 3-6 re-run"
                       if runs[0][1].get("replay_of") else "fresh run"),
            "model": runs[0][1].get("model"), "max_tokens": runs[0][1]["decoding"]["max_tokens"], "repetitions": len(runs),
            "extraction": {
                "action_accuracy_pct": agg([s["action_accuracy_pct"] for s in sums]),
                "entity_accuracy_pct": agg([x["entity_accuracy_pct"] for x in em]),
                "entity_gold_n": em[0]["entity_gold_n"],
                "end_signal_precision_pct": agg([x["end_signal_precision_pct"] for x in em]),
                "end_signal_recall_pct": agg([x["end_signal_recall_pct"] for x in em]),
                "end_signal_f1_pct": agg([x["end_signal_f1_pct"] for x in em]),
                "end_signal_n": em[0]["end_signal_n"],
                "llm_error_rate_pct": agg([s["llm_error_rate_pct"] for s in sums])},
            "decisions_full_pipeline": {k: h(k) for k in (
                "valid_recall_pct", "valid_clarification_rate_pct", "false_rejection_rate_pct",
                "false_acceptance_rate_pct", "precision_pct", "f1_pct", "accuracy_strict_pct")},
            "llm_alone_no_stage3_to_5": {k: agg([s["llm_only_B2"][k] for s in sums]) for k in (
                "valid_recall_pct", "false_acceptance_rate_pct", "precision_pct", "f1_pct", "accuracy_strict_pct")},
            "latency_s": {
                "end_to_end_median": lat("end_to_end", "median_s"), "end_to_end_p95": lat("end_to_end", "p95_s"),
                "end_to_end_mean": lat("end_to_end", "mean_s"), "llm_call_median": lat("stage2", "median_s"),
                "slowest_non_llm_stage_max_ms": slow,
                "pooled_over_all_commands_and_repetitions": {
                    "n": len(e2e), "median": round(st.median(e2e), 3),
                    "p95": round(sorted(e2e)[int(0.95 * (len(e2e) - 1))], 3),
                    "share_of_commands_within_1s_pct": pct(sum(v <= 1.0 for v in e2e), len(e2e))},
                "cold_start_ontology_index_build_s": runs[0][1].get("cold_start_first_ontology_lookup_s")},
            "explainability_coverage_pct": {k: agg([s["explainability"][k] for s in sums]) for k in (
                "reason_coverage_pct", "explanation_block_coverage_pct", "provenance_coverage_pct")},
            "first_rejecting_stage_run1": sums[0]["first_rejecting_stage"],
        }

    e3 = [RUNS / args.e3] if args.e3 else sorted(RUNS.glob(f"{args.date}_E3_*"))
    if e3:
        result["entity_resolution_E3"] = json.loads((e3[-1] / "summary.json").read_text())
        runs_used.append(e3[-1].name)
    sample = next(iter(systems.values()))[0][1]
    n = label_counts()
    result = {"_about": {
        "written_by": "evaluation/scripts/build_final_report.py (reads run folders only)",
        "date": args.date, "dataset_tags": args.tags, "run_folders": sorted(runs_used),
        "git_commit": sample.get("git_commit"), "git_dirty": sample.get("git_dirty"),
        "dataset": f"data/annotated_commands.json, {n['commands']} commands ({n['valid']} valid, {n['invalid']} invalid)",
        "gold_labels": "data/labels/gold_labels.json (entity, end_signal) and entity_resolution_gold.csv (E3)",
        "metrics": {
            "valid_recall_pct": f"valid commands accepted by the pipeline, % of {n['valid']}",
            "valid_clarification_rate_pct": f"valid commands sent to clarification (entity could not be grounded), % of {n['valid']}",
            "false_rejection_rate_pct": f"valid commands rejected, % of {n['valid']}",
            "false_acceptance_rate_pct": f"invalid commands accepted, % of {n['invalid']}",
            "precision_pct / f1_pct": "of all accepted commands, share that is valid / F1 of that precision with valid_recall",
            "accuracy_strict_pct": f"(valid accepted + invalid rejected or clarified) / {n['commands']}",
            "action_accuracy_pct": f"{ACTION_LABEL}: valid commands whose extracted action equals expected_action, % of {n['action']}",
            "entity_accuracy_pct": f"{ENTITY_LABEL}: valid commands with a gold entity whose extracted entity text matches it "
                                   f"(case and punctuation ignored), % of {n['entity']}; not entity-type or ontology-linking accuracy",
            "end_signal_*": "dictation commands (set_note, send_message): did the model detect the finish phrase",
            "llm_alone": "decision from Stage 2 only (valid action = accept), without Stages 3 to 5",
            "latency_s": "seconds per command: end to end, LLM call (stage2), slowest other stage; remote endpoint, depends on load",
            "entity_resolution_E3": "Stages 4 and 5 on 98 (action, entity) pairs against expected status, type and concept; no LLM"},
        "reference_latency": "acceptance criterion <= 1 s (Ludyga 2024, Sect. 7.3), as quoted in make_report_tables.py"},
        **result}

    jf = out_dir / f"all_results_{args.date}.json"
    jf.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    cf = out_dir / f"per_command_{args.date}.csv"
    with open(cf, "w", newline="") as f:
        for note in COLUMN_NOTES:
            f.write("# " + note + "\n")
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)

    f1 = lambda a: "n/a" if a is None else f"{a['mean']:.1f} ± {a['sd']:.1f}"
    f2 = lambda a: "n/a" if a is None else f"{a['mean']:.2f} ± {a['sd']:.2f}"
    S = result["systems"]
    md = [f"# Main results ({args.date} runs)", "",
          f"Written by evaluation/scripts/build_final_report.py. {n['commands']} commands ({n['valid']} valid, {n['invalid']} invalid); mean ± SD over 3 repetitions; "
          "percent unless noted. All other numbers and the definition of each metric are in all_results_%s.json." % args.date, "",
          "## 1. Overall result of the pipeline", "",
          f"- **Overall accuracy:** commands decided correctly, out of {n['commands']}: valid commands accepted plus invalid commands rejected or sent to clarification.",
          f"- **Valid accepted:** share of the {n['valid']} valid commands that the pipeline accepts.",
          f"- **Invalid wrongly accepted:** share of the {n['invalid']} invalid commands that the pipeline accepts.",
          "- **Latency:** seconds per command from raw text to the structured result (remote model; depends on load).", "",
          "| system | overall accuracy | valid accepted | invalid wrongly accepted | latency median | latency p95 |",
          "|---|---|---|---|---|---|"]
    for k, v in S.items():
        d, l = v["decisions_full_pipeline"], v["latency_s"]
        md.append(f"| {k} | {f1(d['accuracy_strict_pct'])} | {f1(d['valid_recall_pct'])} | {f1(d['false_acceptance_rate_pct'])} | "
                  f"{f2(l['end_to_end_median'])} | {f2(l['end_to_end_p95'])} |")
    b0 = result.get("baseline_B0_ludyga")
    if b0:
        a0, bl = b0["all_243_items"], b0["end_to_end_latency_s"]
        md.append(f"| B0 Ludyga grammar | {a0['accuracy_strict_pct']} | {a0['valid_recall_pct']} | "
                  f"{a0['false_acceptance_rate_pct']} | {bl['median_s']:.4f} | {bl['p95_s']:.4f} |")
    md += ["", "## 2. What the language model extracts (Stage 2)", "",
           f"Measured against the gold labels: the action of the {n['action']} valid commands with an action label, the entity text of the "
           f"{n['entity']} with an entity label, and the end signal of the {n['end_signal']} dictations.", "",
           f"| system | {ACTION_LABEL} | {ENTITY_LABEL} | {END_SIGNAL_LABEL} |", "|---|---|---|---|"]
    for k, v in S.items():
        e = v["extraction"]
        md.append(f"| {k} | {f1(e['action_accuracy_pct'])} | {f1(e['entity_accuracy_pct'])} | {f1(e['end_signal_f1_pct'])} |")
    replays = [k for k, v in S.items() if v["source"] != "fresh run"]
    if replays:
        md += ["", "Latency of " + ", ".join(replays) + " is the Stage 2 time of the original run; Stages 1 and 3-6 were re-run."]
    e3d = result.get("entity_resolution_E3")
    if e3d:
        md += ["", "## 3. Entity resolution and type check (Stages 4-5, no LLM)", "",
               "98 (action, entity) pairs. Status = accepted, type mismatch, or not found, as expected. Full = also the right type and concept.", "",
               "| group | pairs | status correct | full correct |", "|---|---|---|---|"]
        for name, bl in [("overall", e3d["overall"])] + [(c, x) for c, x in e3d["by_category"].items()]:
            md.append(f"| {name} | {bl['n']} | {bl['status_acc_pct']} | {bl['full_acc_pct']} |")
    (out_dir / f"main_results_{args.date}.md").write_text("\n".join(md) + "\n")
    print(f"-> {jf}\n-> {out_dir / f'main_results_{args.date}.md'}\n-> {cf}  ({len(all_rows)} rows)")


if __name__ == "__main__":
    main()
