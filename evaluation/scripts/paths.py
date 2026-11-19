"""Where the evaluation results are stored.

  evaluation/final_run/   the reported evaluation (8 Nov 2026, final code). New runs are written here.
  evaluation/old_runs/    earlier runs (3-5 Nov 2026), kept unchanged as a record.

Both folders have the same layout: runs/ (one folder per run), reports/final/ (aggregated
reports), reports/tables/ (overview tables).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
FINAL_RUN = ROOT / "evaluation" / "final_run"
OLD_RUNS = ROOT / "evaluation" / "old_runs"

RUNS = FINAL_RUN / "runs"
REPORTS = FINAL_RUN / "reports"


def find_report(name: str) -> Path:
    """Path of a report file in reports/final/, looked up in final_run/ first, then in old_runs/."""
    for base in (FINAL_RUN, OLD_RUNS):
        p = base / "reports" / "final" / name
        if p.exists():
            return p
    raise FileNotFoundError(f"{name} is in neither {FINAL_RUN} nor {OLD_RUNS}")
