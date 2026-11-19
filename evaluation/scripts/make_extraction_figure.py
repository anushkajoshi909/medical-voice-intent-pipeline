"""Regenerate the Section 7.3 figure from the selected final evaluation report."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from metric_labels import ACTION_LABEL, ENTITY_LABEL, END_SIGNAL_LABEL
ROOT = Path(__file__).resolve().parents[2]
from paths import find_report
ap = argparse.ArgumentParser()
ap.add_argument('--date', default='2026-11-08')
ap.add_argument('--out', type=Path, default=ROOT/'latex-template-cgv-2/fig/eval')
args = ap.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
d = json.loads(find_report(f'all_results_{args.date}.json').read_text())
configs = [
 ('meta-llama/Llama-3.3-70B-Instruct', 'Llama 3.3 (500)'),
 ('google/gemma-4-26B-A4B-it', 'Gemma 4 (500)'),
 ('openai/gpt-oss-120b', 'gpt-oss (500)'),
 ('openai/gpt-oss-120b__maxtok4000', 'gpt-oss (4000)'),
 ('deepseek-ai/DeepSeek-V4.1-Flash', 'DeepSeek (500)'),
 ('deepseek-ai/DeepSeek-V4.1-Flash__maxtok4000', 'DeepSeek (4000)')]
plt.rcParams.update({'font.size': 11, 'pdf.fonttype': 42})
fig, ax = plt.subplots(figsize=(10, 4.5))
x = np.arange(len(configs))
for offset, metric, label, color in [(-.26,'action_accuracy_pct',ACTION_LABEL,'#e69f00'),(0,'entity_accuracy_pct',ENTITY_LABEL,'#56b4e9'),(.26,'end_signal_f1_pct',END_SIGNAL_LABEL,'#009e73')]:
 vals = [d['systems'][key]['extraction'][metric]['mean'] for key,_ in configs]
 bars = ax.bar(x+offset,vals,.24,label=label,color=color)
 ax.bar_label(bars,labels=[f'{v:.1f}' for v in vals],padding=3,fontsize=8,rotation=90)
ax.set_xticks(x,[name for _,name in configs],rotation=20,ha='right')
ax.set_ylim(0,114); ax.set_yticks([0,20,40,60,80,100]); ax.set_ylabel('Score (%)')
ax.set_axisbelow(True); ax.grid(axis='y',color='#dddddd',linewidth=.6)
ax.spines[['top','right']].set_visible(False)
fig.legend(*ax.get_legend_handles_labels(),loc='lower center',ncol=3,frameon=False)
fig.subplots_adjust(bottom=.25,left=.07,right=.99,top=.98)
fig.savefig(args.out/'extraction_by_model.pdf',bbox_inches='tight')
fig.savefig(args.out/'extraction_by_model.png',dpi=150,bbox_inches='tight')
