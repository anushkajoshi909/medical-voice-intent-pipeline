#!/usr/bin/env python3
"""Generate Section 7.2 figures from the selected final evaluation report.

Run with .venv/bin/python evaluation/scripts/make_validation_comparison.py.
Writes only the confusion matrices and validation comparison, leaving other figures intact.
"""
import argparse
import collections
import csv
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
from paths import find_report
ap = argparse.ArgumentParser()
ap.add_argument('--date', default='2026-11-08')
ap.add_argument('--out', type=Path, default=ROOT/'latex-template-cgv-2/fig/eval')
args = ap.parse_args()
REPORT = find_report(f'all_results_{args.date}.json').parent
OUT = args.out
CONFIGS = [
    ('meta-llama/Llama-3.3-70B-Instruct', 'Llama 3.3 (500)'),
    ('google/gemma-4-26B-A4B-it', 'Gemma 4 (500)'),
    ('openai/gpt-oss-120b', 'gpt-oss (500)'),
    ('openai/gpt-oss-120b__maxtok4000', 'gpt-oss (4000)'),
    ('deepseek-ai/DeepSeek-V4.1-Flash', 'DeepSeek (500)'),
    ('deepseek-ai/DeepSeek-V4.1-Flash__maxtok4000', 'DeepSeek (4000)'),
]
plt.rcParams.update({'font.size': 11, 'axes.titlesize': 12,
                     'axes.labelsize': 11, 'xtick.labelsize': 10,
                     'ytick.labelsize': 11, 'pdf.fonttype': 42})
d = json.loads((REPORT / f'all_results_{args.date}.json').read_text())
with (REPORT / f'per_command_{args.date}.csv').open() as f:
    rows = list(csv.DictReader(line for line in f if not line.startswith('#')))
b = d['baseline_B0_ludyga']['all_243_items']
matrices = [('Baseline', np.array([[b['valid_accepted'], b['valid_clarified'] + b['valid_rejected']],
                                 [b['invalid_accepted'], b['invalid_clarified'] + b['invalid_rejected']]]))]
for key, title in [('google/gemma-4-26B-A4B-it', 'Gemma 4'),
                   ('meta-llama/Llama-3.3-70B-Instruct', 'Llama 3.3')]:
    c = collections.Counter((r['gold_valid'] == 'True', r['outcome'] == 'ACCEPT')
                            for r in rows if r['system'] == key and int(r['rep']) == 1)
    matrices.append((title, np.array([[c[True, True], c[True, False]],
                                     [c[False, True], c[False, False]]])))
fig, axes = plt.subplots(1, 3, figsize=(8.8, 3.0), layout='constrained')
for ax, (title, m) in zip(axes, matrices):
    assert m.sum() == 243
    im = ax.imshow(m, cmap='Blues', vmin=0, vmax=130)
    ax.set_title(title, pad=10)
    ax.set_xticks([0, 1], ['Accepted', 'Not accepted'])
    ax.set_yticks([0, 1], ['Valid', 'Invalid'])
    ax.set_xlabel('Pipeline decision')
    if ax is axes[0]:
        ax.set_ylabel('Reference label')
    for (i, j), v in np.ndenumerate(m):
        ax.text(j, i, str(v), ha='center', va='center', fontsize=14,
                color='white' if v > 80 else 'black')
fig.colorbar(im, ax=axes, fraction=0.025, pad=0.025, label='Commands')
OUT.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT / 'confusion_matrices.pdf', bbox_inches='tight')
fig.savefig(OUT/'confusion_matrices.png', dpi=170, bbox_inches='tight')
plt.close(fig)

fig, axes = plt.subplots(1, 2, figsize=(9.2, 4.8), sharey=True)
y = np.arange(len(CONFIGS))
for ax, metric, title, limit in [
    (axes[0], 'false_acceptance_rate_pct', '(a) False acceptance rate', 16),
    (axes[1], 'valid_recall_pct', '(b) Valid-command recall', 110),
]:
    before = [d['systems'][k]['llm_alone_no_stage3_to_5'][metric]['mean'] for k, _ in CONFIGS]
    after = [d['systems'][k]['decisions_full_pipeline'][metric]['mean'] for k, _ in CONFIGS]
    ax.barh(y - 0.18, before, height=0.32, label='Extraction-only rule',
            color='#d2d6da', edgecolor='#687078', linewidth=0.5)
    ax.barh(y + 0.18, after, height=0.32, label='Full pipeline', color='#206b91')
    for vals, offset in [(before, -0.18), (after, 0.18)]:
        for row, value in enumerate(vals):
            ax.text(value + limit * 0.018, row + offset, f'{value:.1f}',
                    ha='left', va='center', fontsize=9)
    ax.set_xlim(0, limit)
    ax.set_title(title, pad=12)
    ax.set_xlabel('Percent')
    ax.set_axisbelow(True)
    ax.grid(axis='x', color='#dddddd', linewidth=0.6)
    ax.spines[['top', 'right']].set_visible(False)
axes[0].set_yticks(y, [name for _, name in CONFIGS])
axes[0].invert_yaxis()
axes[0].set_xticks([0, 5, 10, 15])
axes[1].set_xticks([0, 25, 50, 75, 100])
handles, labels = axes[0].get_legend_handles_labels()
fig.legend(handles, labels, loc='lower center', ncol=2, frameon=False)
fig.subplots_adjust(left=0.24, right=0.98, top=0.89, bottom=0.15, wspace=0.18)
fig.savefig(OUT / 'validation_comparison.pdf', bbox_inches='tight')
fig.savefig(OUT/'validation_comparison.png', dpi=170, bbox_inches='tight')
plt.close(fig)
print('Generated confusion_matrices.pdf and validation_comparison.pdf from the selected final batch.')
