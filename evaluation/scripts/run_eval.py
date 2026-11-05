#!/usr/bin/env python3
"""Run the whole evaluation and save it in a structured, append-only layout.

For every system it writes one run folder:

  evaluation/runs/<date>_<system>_<dataset>_r<k>/
      results.jsonl   one line per command: gold fields, system outcome, all stage
                      outputs, raw LLM response, per-stage timings
      meta.json       everything needed to reproduce / audit the run
      summary.json    metrics computed from results.jsonl (see score())

and one overview table over all runs:

  evaluation/reports/tables/overview.csv

Systems
  B0-ludyga        Ludyga's original grammar (baseline_ludyga/commandToJson.py)
  <model slug>     the hybrid pipeline (run_pipeline.run_command) with that LLM;
                   its summary also contains the LLM-only baseline (B2), i.e. the
                   decision "Stage 2 produced a valid action" with no Stage 3-5.

The pipeline code is NOT modified: stage functions are wrapped for timing from
here. The model is always passed explicitly (.selected_model is never read or
written by this script).

Usage
  python evaluation/scripts/run_eval.py                     # 4 models x 3 reps + B0
  python evaluation/scripts/run_eval.py --reps 1 --sample 12 --tag smoke --runs-dir /tmp/x   # smoke test
  python evaluation/scripts/run_eval.py --models openai/gpt-oss-120b --reps 1
  python evaluation/scripts/run_eval.py --no-baseline
  python evaluation/scripts/run_eval.py --models openai/gpt-oss-120b --max-tokens 4000 --no-baseline
  python evaluation/scripts/run_eval.py --replay evaluation/runs/<old run folder> ... --no-baseline
      re-runs Stages 1,3-6 on the STORED Stage 2 outputs of old runs (no LLM calls), e.g. to measure
      a pipeline fix exactly; the old run folders stay untouched, a new *_replay folder is written.
"""
import argparse
import csv
import dataclasses
import hashlib
import json
import logging
import platform
import random
import re
import statistics
import subprocess
import sys
import time
from datetime import date, datetime
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "baseline_ludyga"))
logging.disable(logging.CRITICAL)  # silence Ludyga's internal logging

import run_pipeline as rp  # noqa: E402
from stage2_llm_extract import get_client  # noqa: E402

DEFAULT_MODELS = [
    "meta-llama/Llama-3.3-70B-Instruct",
    "google/gemma-4-26B-A4B-it",
    "openai/gpt-oss-120b",
    "deepseek-ai/DeepSeek-V4.1-Flash",
]
ENDPOINT = "https://llm.scads.ai/v1"
STAGES = ["stage1", "stage2", "stage3", "stage4", "stage5", "stage6"]
OUTCOME_OF_STATUS = {
    "accepted": "ACCEPT", "accepted_partial": "ACCEPT",
    "needs_clarification": "CLARIFY", "rejected": "REJECT",
}


# ── small helpers ────────────────────────────────────────────────────────────

def slug(name: str) -> str:
    return name.replace("/", "_").replace(" ", "-")


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return "unavailable"


def json_default(o):
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    return str(o)


def pct(x, n):
    return round(100 * x / n, 1) if n else None


def percentile(values, q):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


# ── timing wrappers around the existing stage functions ──────────────────────

CUR: dict = {}


def _timed(name, fn, capture=None):
    def wrapper(*a, **k):
        t0 = time.perf_counter()
        res = fn(*a, **k)
        CUR[name] = time.perf_counter() - t0
        if capture:
            capture(res)
        return res
    return wrapper


def install_timers(max_tokens=500):
    def cap_llm(res):
        CUR["raw_llm_response"] = res.raw_response
        CUR["llm_called"] = True

    rp.preprocess = _timed("stage1", rp.preprocess)
    llm_extract = rp.extract
    rp.extract = _timed("stage2", lambda *a, **k: llm_extract(*a, max_tokens=max_tokens, **k), cap_llm)
    rp.frame_validate = _timed("stage3", rp.frame_validate)
    rp.ontology_lookup = _timed("stage4", rp.ontology_lookup)
    rp.type_validate = _timed("stage5", rp.type_validate)
    rp.assemble_intent = _timed("stage6", rp.assemble_intent)


# ── dataset ──────────────────────────────────────────────────────────────────

def load_dataset(paths, limit, sample=None):
    items = []
    for p in paths:
        items += json.loads(Path(p).read_text())
    gold_path = ROOT / "evaluation" / "dataset" / "gold_labels.json"
    gold = json.loads(gold_path.read_text()) if gold_path.exists() else {}
    for it in items:
        it["gold"] = gold.get(it["id"], {})
    if sample:
        items = random.Random(0).sample(items, min(sample, len(items)))
    return items[:limit] if limit else items


# ── scoring (counting rules: evaluation/evaluation.md section 4) ────────────────────

def first_rejecting_stage(r):
    """First stage that did not let the command through ('none' if accepted)."""
    if r["format_invalid"]:
        return "stage1_format"
    if r["action"] in ("UNKNOWN", "") or r.get("llm_error"):
        return "llm"
    if not r["frame_valid"]:
        return "stage3_antlr"
    if r["s5_status"] in ("type_mismatch", "entity_not_found"):
        return "stage5_" + r["s5_status"]
    return "none"


def confusion(rows, outcome_key):
    """Counts for gold-valid / gold-invalid items by system outcome."""
    c = {"valid": {"ACCEPT": 0, "CLARIFY": 0, "REJECT": 0},
         "invalid": {"ACCEPT": 0, "CLARIFY": 0, "REJECT": 0}}
    for x in rows:
        c["valid" if x["gold_valid"] else "invalid"][x[outcome_key]] += 1
    return c


def metrics_from(c):
    v, i = c["valid"], c["invalid"]
    nv, ni = sum(v.values()), sum(i.values())
    tp, fp = v["ACCEPT"], i["ACCEPT"]
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / nv if nv else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else None)
    n = nv + ni
    return {
        "valid_n": nv, "invalid_n": ni,
        "valid_accepted": v["ACCEPT"], "valid_clarified": v["CLARIFY"],
        "valid_rejected": v["REJECT"],
        "invalid_accepted": i["ACCEPT"], "invalid_clarified": i["CLARIFY"],
        "invalid_rejected": i["REJECT"],
        "valid_recall_pct": pct(v["ACCEPT"], nv),
        "valid_clarification_rate_pct": pct(v["CLARIFY"], nv),
        "false_rejection_rate_pct": pct(v["REJECT"], nv),
        "false_acceptance_rate_pct": pct(i["ACCEPT"], ni),
        "invalid_not_accepted_pct": pct(i["CLARIFY"] + i["REJECT"], ni),
        "precision_pct": round(100 * precision, 1) if precision is not None else None,
        "f1_pct": round(100 * f1, 1) if f1 is not None else None,
        # strict: a valid command ending in CLARIFY is not a success
        "accuracy_strict_pct": pct(v["ACCEPT"] + i["CLARIFY"] + i["REJECT"], n),
        # lenient: valid + CLARIFY counted as understood (older scorer convention)
        "accuracy_lenient_pct": pct(v["ACCEPT"] + v["CLARIFY"] + i["CLARIFY"] + i["REJECT"], n),
    }


def norm(s):
    return " ".join(str(s or "").lower().split())


def norm_entity(s):
    """Entity comparison ignores punctuation: Stage 1 removes it from the input, but the LLM
    sometimes writes it back into its output (o'clock, a comma between two drugs)."""
    return " ".join(re.sub(r"[^\w\s/]", " ", str(s or "").lower()).split())


def explanation_metrics(rows, action_cfg):
    non_accept = [x for x in rows if x["outcome"] != "ACCEPT"]
    with_reason = sum(1 for x in non_accept if x["result"]["s6_payload"].get("reason"))
    with_expl = sum(1 for x in rows
                    if x["result"]["s6_payload"].get("explanation"))
    prov_n = prov_ok = 0
    for x in rows:
        r = x["result"]
        if x["outcome"] != "ACCEPT" or action_cfg.get(r["action"], {}).get("ontology_index") is None:
            continue
        prov_n += 1
        for slot in r["s6_payload"].get("slots", {}).values():
            if isinstance(slot, list):
                ok = bool(slot) and all(d.get("rxcui") for d in slot)
            else:
                ok = bool(slot.get("source"))
            prov_ok += ok
            break
    return {
        "reason_coverage_pct": pct(with_reason, len(non_accept)),
        "explanation_block_coverage_pct": pct(with_expl, len(rows)),
        "provenance_coverage_pct": pct(prov_ok, prov_n),
        "provenance_n": prov_n,
    }


def score_pipeline(rows, action_cfg):
    for x in rows:
        x["gold_valid"] = x["valid"]
        x["outcome_llm_only"] = ("ACCEPT" if x["result"]["action"] in action_cfg
                                 and not x["result"]["format_invalid"] else "REJECT")
    hybrid = metrics_from(confusion(rows, "outcome"))
    llm_only = metrics_from(confusion(rows, "outcome_llm_only"))

    valid = [x for x in rows if x["valid"]]
    with_act = [x for x in valid if x["expected_action"]]
    act_ok = sum(1 for x in with_act if x["result"]["action"] == x["expected_action"])

    ent = [x for x in valid if x["gold"].get("entity") is not None]
    ent_ok = sum(1 for x in ent if norm_entity(x["result"]["entity"]) == norm_entity(x["gold"]["entity"]))
    sig = [x for x in rows if x["gold"].get("end_signal") is not None]
    tp = sum(1 for x in sig if x["gold"]["end_signal"] and x["result"]["end_signal"])
    fp = sum(1 for x in sig if not x["gold"]["end_signal"] and x["result"]["end_signal"])
    fn = sum(1 for x in sig if x["gold"]["end_signal"] and not x["result"]["end_signal"])

    attribution = {}
    for x in rows:
        stage = first_rejecting_stage(x["result"])
        key = f"gold_{'valid' if x['valid'] else 'invalid'}"
        attribution.setdefault(key, {}).setdefault(stage, 0)
        attribution[key][stage] += 1

    per_cat = {}
    for x in rows:
        d = per_cat.setdefault(x["category"], {"n": 0, "ACCEPT": 0, "CLARIFY": 0, "REJECT": 0,
                                               "gold_valid": x["valid"]})
        d["n"] += 1
        d[x["outcome"]] += 1

    errs = [x for x in rows if x["result"]["llm_error"] and not x["result"]["format_invalid"]]
    lat = {}
    for name in STAGES + ["end_to_end"]:
        vals = [x["timings"][name] for x in rows if name in x["timings"]]
        if vals:
            lat[name] = {"n": len(vals), "median_s": round(statistics.median(vals), 4),
                         "p95_s": round(percentile(vals, 0.95), 4),
                         "mean_s": round(statistics.mean(vals), 4),
                         "max_s": round(max(vals), 4)}
    return {
        "hybrid": hybrid,
        "llm_only_B2": llm_only,
        "action_accuracy_pct": pct(act_ok, len(with_act)),
        "action_n": len(with_act),
        "entity_accuracy_pct": pct(ent_ok, len(ent)) if ent else None,
        "entity_gold_n": len(ent),
        "end_signal": ({"n": len(sig), "precision_pct": pct(tp, tp + fp), "recall_pct": pct(tp, tp + fn),
                        "f1_pct": pct(2 * tp, 2 * tp + fp + fn)} if sig else None),
        "llm_error_rate_pct": pct(len(errs), sum(1 for x in rows if not x["result"]["format_invalid"])),
        "llm_calls_total": sum(1 for x in rows if x["llm_called"]),
        "first_rejecting_stage": attribution,
        "per_category": per_cat,
        "latency": lat,
        "explainability": explanation_metrics(rows, action_cfg),
    }


# ── running ──────────────────────────────────────────────────────────────────

def environment():
    def ver(pkg):
        try:
            return metadata.version(pkg)
        except Exception:
            return None
    return {"python": sys.version.split()[0], "platform": platform.platform(),
            "machine": platform.machine(), "openai": ver("openai"),
            "antlr4": ver("antlr4-python3-runtime"), "pyyaml": ver("PyYAML")}


def fingerprints(dataset_paths, hash_ontologies):
    files = {
        "domain_config.yaml": ROOT / "domain_config.yaml",
        "llm_system_prompt.txt": ROOT / "llm_system_prompt.txt",
        "MedicalSmartGlassesLexer.g4": ROOT / "MedicalSmartGlassesLexer.g4",
        "MedicalSmartGlassesParser.g4": ROOT / "MedicalSmartGlassesParser.g4",
        "abbreviations.json": ROOT / "abbreviations.json",
    }
    for p in dataset_paths:
        files[str(Path(p))] = Path(p)
    out = {k: sha256(v) for k, v in files.items() if v.exists()}
    onto = {}
    for f in sorted((ROOT / "ontologies").glob("*")):
        if f.is_file():
            st = f.stat()
            onto[f.name] = {"bytes": st.st_size, "mtime": datetime.fromtimestamp(st.st_mtime).isoformat()}
            if hash_ontologies:
                onto[f.name]["sha256"] = sha256(f)
    return {"sha256": out, "ontologies": onto}


def write_meta(run_dir, meta):
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=json_default))


def run_hybrid(client, model, items, rep, run_dir, meta, replay=False):
    cfg = rp._action_cfg()
    order = list(range(len(items)))
    random.Random(rep).shuffle(order)          # order differs per repetition, reproducible
    meta["shuffle_seed"] = rep
    rows = []
    t_run = time.perf_counter()
    with open(run_dir / "results.jsonl", "w") as out:
        for pos, idx in enumerate(order, 1):
            it = items[idx]
            CUR.clear()
            CUR["current_id"] = it["id"]              # used by the replay stand-in for Stage 2
            t0 = time.perf_counter()
            r = rp.run_command(client, it, model)
            # replay: Stage 2 was not executed now, add the time it took in the original run
            CUR["end_to_end"] = time.perf_counter() - t0 + (CUR.get("stage2", 0.0) if replay else 0.0)
            row = {
                "id": it["id"], "category": it.get("category"), "valid": it["valid"],
                "expected_action": it.get("expected_action"), "text": it["text"],
                "gold": it["gold"], "position": pos, "rep": rep, "model": model,
                "outcome": OUTCOME_OF_STATUS[r["s6_status"]],
                "llm_called": bool(CUR.get("llm_called")),
                "raw_llm_response": CUR.get("raw_llm_response", ""),
                "timings": {k: v for k, v in CUR.items() if isinstance(v, float)},
                "result": r,
            }
            rows.append(row)
            out.write(json.dumps(row, default=json_default) + "\n")
            out.flush()
            if pos % 25 == 0 or pos == len(order):
                print(f"    {pos}/{len(order)}  ({time.perf_counter() - t_run:.0f}s)", flush=True)
    return rows, rp._action_cfg()


def run_baseline(items, run_dir):
    from commandToJson import get_json
    rows = []
    with open(run_dir / "results.jsonl", "w") as out:
        for pos, it in enumerate(items, 1):
            t0 = time.perf_counter()
            try:
                output, err = get_json(f"ok glasses {it['text']}"), None
            except Exception as e:                  # grammar/visitor crash = reject
                output, err = {}, f"{type(e).__name__}: {e}"
            dt = time.perf_counter() - t0
            row = {"id": it["id"], "category": it.get("category"), "valid": it["valid"],
                   "expected_action": it.get("expected_action"), "text": it["text"],
                   "gold": it["gold"], "position": pos,
                   "outcome": "ACCEPT" if output else "REJECT",
                   "grammar_output": output, "error": err, "timings": {"end_to_end": dt}}
            rows.append(row)
            out.write(json.dumps(row, default=json_default) + "\n")
    return rows


def score_baseline(rows):
    for x in rows:
        x["gold_valid"] = x["valid"]
    lat = [x["timings"]["end_to_end"] for x in rows]
    base_ids = {x["id"] for x in rows if not x["id"].startswith("ext_")}
    base = [x for x in rows if x["id"] in base_ids]
    return {
        "all_items": metrics_from(confusion(rows, "outcome")),
        "ludyga_198_only": metrics_from(confusion(base, "outcome")),
        "crashes": sum(1 for x in rows if x["error"]),
        "latency": {"end_to_end": {"n": len(lat), "median_s": round(statistics.median(lat), 5),
                                   "p95_s": round(percentile(lat, 0.95), 5),
                                   "mean_s": round(statistics.mean(lat), 5)}},
    }


def overview_row(run_name, system, rep, s, n):
    h = s.get("hybrid") or s.get("all_items")
    lat = (s.get("latency") or {}).get("end_to_end", {})
    row = {"run": run_name, "system": system, "rep": rep, "n": n}
    row.update({k: h[k] for k in ("valid_recall_pct", "valid_clarification_rate_pct",
                                  "false_rejection_rate_pct", "false_acceptance_rate_pct",
                                  "precision_pct", "f1_pct", "accuracy_strict_pct",
                                  "accuracy_lenient_pct")})
    row["action_accuracy_pct"] = s.get("action_accuracy_pct")
    row["llm_only_far_pct"] = (s.get("llm_only_B2") or {}).get("false_acceptance_rate_pct")
    row["llm_only_recall_pct"] = (s.get("llm_only_B2") or {}).get("valid_recall_pct")
    row["llm_error_rate_pct"] = s.get("llm_error_rate_pct")
    row["latency_median_s"] = lat.get("median_s")
    row["latency_p95_s"] = lat.get("p95_s")
    row["latency_mean_s"] = lat.get("mean_s")
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--dataset", nargs="+", default=[str(ROOT / "data" / "annotated_commands.json")])
    ap.add_argument("--tag", default="full", help="dataset label used in run names")
    ap.add_argument("--limit", type=int, help="only the first N items (smoke test)")
    ap.add_argument("--sample", type=int, help="N random items, fixed seed (smoke test across categories)")
    ap.add_argument("--max-tokens", type=int, default=500,
                    help="completion budget for Stage 2 (default 500 = original setting; reasoning models need more)")
    ap.add_argument("--replay", nargs="+", metavar="RUN_DIR",
                    help="re-run Stages 1,3-6 on the stored Stage 2 outputs of these run folders (no LLM calls)")
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument("--runs-dir", default=str(ROOT / "evaluation" / "runs"))
    ap.add_argument("--reports-dir", default=str(ROOT / "evaluation" / "reports"))
    ap.add_argument("--hash-ontologies", action="store_true", help="sha256 of the big ontology files (slow)")
    args = ap.parse_args()

    runs_dir, reports_dir = Path(args.runs_dir), Path(args.reports_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "tables").mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    items = load_dataset(args.dataset, args.limit, args.sample)
    print(f"{len(items)} items from {len(args.dataset)} file(s)")

    base_meta = {
        "date": today, "dataset_files": args.dataset, "dataset_tag": args.tag, "n_items": len(items),
        "git_commit": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain")),
        "environment": environment(), "endpoint": ENDPOINT,
        "fingerprints": fingerprints(args.dataset, args.hash_ontologies),
        "decoding": {"temperature": 0, "max_tokens": args.max_tokens, "response_format": "json_object"},
        "counting_rules": "evaluation/evaluation.md section 4",
    }

    overview = []

    def finish(run_dir, run_name, system, rep, meta, rows, summary):
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=json_default))
        meta["status"] = "complete"
        meta["finished"] = datetime.now().isoformat(timespec="seconds")
        write_meta(run_dir, meta)
        overview.append(overview_row(run_name, system, rep, summary, len(rows)))

    def prepare(system, rep, tag=None):
        name = f"{today}_{slug(system)}_{tag or args.tag}_r{rep}"
        d = runs_dir / name
        meta_file = d / "meta.json"
        if meta_file.exists() and json.loads(meta_file.read_text()).get("status") == "complete":
            print(f"  skip {name} (already complete)")
            return name, None, None
        d.mkdir(parents=True, exist_ok=True)
        meta = dict(base_meta, system=system, rep=rep, status="running",
                    started=datetime.now().isoformat(timespec="seconds"))
        write_meta(d, meta)
        return name, d, meta

    if args.replay:
        from stage2_llm_extract import LLMResult
        install_timers()
        stored = {}

        def replay_extract(client, clean_text, model=None, **k):
            row = stored[CUR["current_id"]]
            x = row["result"]
            CUR["raw_llm_response"] = row["raw_llm_response"]
            CUR["llm_called"] = row["llm_called"]
            CUR["stage2"] = row["timings"].get("stage2", 0.0)
            return LLMResult(action=x["action"], entity=x["entity"], end_signal=x["end_signal"],
                             confidence=x["confidence"], raw_response=row["raw_llm_response"],
                             model=x["model"], recipient=x["recipient"], error=x["llm_error"])

        rp.extract = replay_extract
        rp.ontology_lookup("show", "blood pressure")        # warm the indices (replay timings are not reported)
        for src in map(Path, args.replay):
            smeta = json.loads((src / "meta.json").read_text())
            if smeta["system"].startswith("B0"):
                continue
            stored.clear()
            stored.update({r["id"]: r for r in (json.loads(l) for l in open(src / "results.jsonl"))})
            label = smeta.get("system_label") or smeta["system"]
            tag = args.tag if "replay" in args.tag else args.tag + "-replay"
            print(f"== replay of {src.name}")
            name, d, meta = prepare(label, smeta["rep"], tag)
            if not d:
                continue
            meta.update(model=smeta.get("model"), replay_of=src.name, system_label=label,
                        replay_note="Stage 2 outputs reused; Stages 1,3-6 re-executed with the current code. "
                                    "Latency is NOT comparable (Stage 2 time copied from the original run).")
            meta["decoding"] = smeta.get("decoding", meta["decoding"])
            rows, cfg = run_hybrid(None, smeta.get("model"), items, smeta["rep"], d, meta, replay=True)
            finish(d, name, label, smeta["rep"], meta, rows, score_pipeline(rows, cfg))
            o = overview[-1]
            print(f"  {name}: valid recall {o['valid_recall_pct']}%  FAR {o['false_acceptance_rate_pct']}%  "
                  f"clarif {o['valid_clarification_rate_pct']}%  FRR {o['false_rejection_rate_pct']}%")

    if not args.no_baseline and not args.replay:
        print("== B0 Ludyga baseline")
        name, d, meta = prepare("B0-ludyga", 1)
        if d:
            rows = run_baseline(items, d)
            finish(d, name, "B0-ludyga", 1, meta, rows, score_baseline(rows))
            print(f"  {name}: valid recall {overview[-1]['valid_recall_pct']}%  "
                  f"FAR {overview[-1]['false_acceptance_rate_pct']}%")

    if args.models and args.reps > 0 and not args.replay:
        client = get_client()
        install_timers(args.max_tokens)
        # cold start: first ontology lookup of the process builds all indices lazily
        t0 = time.perf_counter()
        rp.ontology_lookup("show", "blood pressure")
        base_meta["cold_start_first_ontology_lookup_s"] = round(time.perf_counter() - t0, 3)
        print(f"cold-start ontology index build: {base_meta['cold_start_first_ontology_lookup_s']}s")

        for model in args.models:
            for rep in range(1, args.reps + 1):
                label = model if args.max_tokens == 500 else f"{model}__maxtok{args.max_tokens}"
                print(f"== {label}  rep {rep}/{args.reps}")
                name, d, meta = prepare(label, rep)
                if not d:
                    continue
                meta["model"] = model
                meta["system_label"] = label
                meta["system_prompt_sha256"] = hashlib.sha256(
                    rp._load_domain()["system_prompt"].encode()).hexdigest()
                rows, cfg = run_hybrid(client, model, items, rep, d, meta)
                finish(d, name, label, rep, meta, rows, score_pipeline(rows, cfg))
                o = overview[-1]
                print(f"  {name}: valid recall {o['valid_recall_pct']}%  FAR {o['false_acceptance_rate_pct']}%  "
                      f"clarif {o['valid_clarification_rate_pct']}%  median {o['latency_median_s']}s  "
                      f"p95 {o['latency_p95_s']}s")

    if overview:
        out = reports_dir / "tables" / f"overview_{today}_{args.tag}.csv"
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(overview[0].keys()))
            w.writeheader()
            w.writerows(overview)
        print(f"overview -> {out}")


if __name__ == "__main__":
    main()
