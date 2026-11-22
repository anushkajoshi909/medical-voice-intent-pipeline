"""Small helpers shared by the evaluation scripts (run_eval.py, build_final_report.py, run_e3.py)."""
import dataclasses
import re


def pct(part, whole):
    """part / whole in percent, one decimal; None if whole is 0."""
    return round(100 * part / whole, 1) if whole else None


def norm_entity(s) -> str:
    """Entity comparison ignores case and punctuation: Stage 1 removes punctuation from the
    input, but the LLM sometimes writes it back into its output (o'clock, a comma between two drugs)."""
    return " ".join(re.sub(r"[^\w\s/]", " ", str(s or "").lower()).split())


def json_default(o):
    """json.dumps default: dataclasses (e.g. DrugResult) as dicts, anything else as text."""
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    return str(o)
