#!/usr/bin/env python3
"""Draw the figures of the evaluation chapter from the report of one run (default: the final run, 2026-11-08).

Figures (PDF and PNG) in figures/ next to the report (evaluation/final_run/reports/figures/), copied to latex-template-cgv-2/fig/eval/ if that folder exists:
  confusion_matrices      the decision of B0, Gemma 4, and Llama 3.3 (first repetition)
  metrics_by_model        accuracy, precision, recall, and F1 of the decision for all systems
  extraction_by_model     action classification accuracy, entity extraction accuracy (normalised exact match),
                          and end-signal F1 of the language model (names from metric_labels.py)

All numbers are read from all_results_<date>.json and per_command_<date>.csv in
evaluation/final_run/reports/final/ (or evaluation/old_runs/reports/final/).
Requires matplotlib (pip install matplotlib).
Usage: python evaluation/scripts/make_figures.py [--date 2026-11-08]
"""
import argparse
import collections
import csv
import json
import shutil
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metric_labels import ACTION_LABEL, ENTITY_LABEL, END_SIGNAL_LABEL  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
ORDER = [("meta-llama/Llama-3.3-70B-Instruct", "Llama 3.3"),
         ("google/gemma-4-26B-A4B-it", "Gemma 4"),
         ("openai/gpt-oss-120b", "gpt-oss (500)"),
         ("openai/gpt-oss-120b__maxtok4000", "gpt-oss (4000)"),
         ("deepseek-ai/DeepSeek-V4.1-Flash", "DeepSeek (500)"),
         ("deepseek-ai/DeepSeek-V4.1-Flash__maxtok4000", "DeepSeek (4000)")]
COLORS = ["#E69F00", "#56B4E9", "#009E73", "#F0E442"]      # orange, sky blue, green, yellow
plt.rcParams.update({"font.size": 9, "axes.titlesize": 11, "axes.labelsize": 9, "legend.fontsize": 8,
                     "xtick.labelsize": 8, "ytick.labelsize": 8})


def grouped_bars(ax, labels, series, names, colors):
    n = len(series)
    width = 0.8 / n
    x = np.arange(len(labels))
    for i, (vals, name, col) in enumerate(zip(series, names, colors)):
        ax.bar(x - 0.4 + width * (i + 0.5), vals, width, label=name, color=col)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylim(0, 100)
    ax.set_yticks(np.arange(0, 101, 20))
    ax.set_axisbelow(True)
    ax.grid(True, linestyle=":", color="#bbbbbb", linewidth=0.7)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-11-08")
    ap.add_argument("--out", help="output folder (default: figures/ next to the report)")
    ap.add_argument("--no-copy", action="store_true", help="do not copy the PDFs into the thesis folder")
    args = ap.parse_args()
    from paths import find_report
    rep = find_report(f"all_results_{args.date}.json").parent
    d = json.load(open(rep / f"all_results_{args.date}.json"))
    rows = [r for r in csv.DictReader(l for l in open(rep / f"per_command_{args.date}.csv") if not l.startswith("#"))]
    out = Path(args.out) if args.out else rep.parent / "figures"
    out.mkdir(parents=True, exist_ok=True)
    B = d["baseline_B0_ludyga"]["all_243_items"]

    # ---------- 1. confusion matrices (first repetition)
    def conf(system):
        c = collections.Counter()
        for r in rows:
            if r["system"] == system and int(r["rep"]) == 1:
                c[("v" if r["gold_valid"] == "True" else "i", "a" if r["outcome"] == "ACCEPT" else "n")] += 1
        return np.array([[c["v", "a"], c["v", "n"]], [c["i", "a"], c["i", "n"]]])
    mats = [("B0 (Ludyga's grammar)", np.array([[B["valid_accepted"], B["valid_clarified"] + B["valid_rejected"]],
                                                [B["invalid_accepted"], B["invalid_clarified"] + B["invalid_rejected"]]])),
            ("Gemma 4", conf("google/gemma-4-26B-A4B-it")),
            ("Llama 3.3", conf("meta-llama/Llama-3.3-70B-Instruct"))]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6))
    for ax, (title, m) in zip(axes, mats):
        im = ax.imshow(m, cmap="Blues", vmin=0, vmax=130)
        ax.set_title(f"Confusion Matrix \u2014 {title}", fontsize=9)
        ax.set_xticks([0, 1]); ax.set_xticklabels(["accepted", "not accepted"])
        ax.set_yticks([0, 1]); ax.set_yticklabels(["valid", "invalid"], rotation=90, va="center")
        ax.set_xlabel("Predicted label"); ax.set_ylabel("True label")
        for (i, j), v in np.ndenumerate(m):
            ax.text(j, i, str(v), ha="center", va="center", fontsize=10, color="black")
    fig.subplots_adjust(left=0.05, right=0.9, wspace=0.3, bottom=0.17, top=0.9)
    cax = fig.add_axes([0.92, 0.17, 0.015, 0.73])
    fig.colorbar(im, cax=cax).set_label("count")
    fig.savefig(out / "confusion_matrices.pdf"); fig.savefig(out / "confusion_matrices.png", dpi=200); plt.close(fig)

    # ---------- 2. metrics by model
    names = ["B0"] + [n for _, n in ORDER]
    acc = [B["accuracy_strict_pct"]] + [d["systems"][k]["decisions_full_pipeline"]["accuracy_strict_pct"]["mean"] for k, _ in ORDER]
    pre = [B["precision_pct"]] + [d["systems"][k]["decisions_full_pipeline"]["precision_pct"]["mean"] for k, _ in ORDER]
    rec = [B["valid_recall_pct"]] + [d["systems"][k]["decisions_full_pipeline"]["valid_recall_pct"]["mean"] for k, _ in ORDER]
    f1 = [B["f1_pct"]] + [d["systems"][k]["decisions_full_pipeline"]["f1_pct"]["mean"] for k, _ in ORDER]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    grouped_bars(ax, names, [np.array(v) for v in (acc, pre, rec, f1)], ["Accuracy", "Precision", "Recall", "F1"],
                 COLORS)
    ax.set_ylabel("Score (%)"); ax.set_title("Command decision — overall metrics by model")
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.22), frameon=False)
    fig.tight_layout(); fig.savefig(out / "metrics_by_model.pdf"); fig.savefig(out / "metrics_by_model.png", dpi=200); plt.close(fig)

    # ---------- 3. extraction by model
    act = [d["systems"][k]["extraction"]["action_accuracy_pct"]["mean"] for k, _ in ORDER]
    ent = [d["systems"][k]["extraction"]["entity_accuracy_pct"]["mean"] for k, _ in ORDER]
    sig = [d["systems"][k]["extraction"]["end_signal_f1_pct"]["mean"] for k, _ in ORDER]
    fig, ax = plt.subplots(figsize=(9, 4.0))
    grouped_bars(ax, [n for _, n in ORDER], [act, ent, sig], [ACTION_LABEL, ENTITY_LABEL, END_SIGNAL_LABEL],
                 COLORS[:3])
    ax.set_ylabel("Score (%)"); ax.set_title("Extraction by the language model — metrics by model")
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.22), frameon=False)
    fig.tight_layout(); fig.savefig(out / "extraction_by_model.pdf"); fig.savefig(out / "extraction_by_model.png", dpi=200); plt.close(fig)

    print("figures ->", out)
    dest = ROOT / "latex-template-cgv-2" / "fig" / "eval"
    if not args.no_copy and (ROOT / "latex-template-cgv-2").is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        for f in out.glob("*.pdf"):
            shutil.copy(f, dest / f.name)
        print("copied to", dest)


if __name__ == "__main__":
    main()
