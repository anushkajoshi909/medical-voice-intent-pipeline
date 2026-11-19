"""Display names of the extraction metrics and the label counts behind them (Finding 12).

Only the names shown in reports and figures are defined here. The JSON keys of the
run folders and reports (action_accuracy_pct, entity_accuracy_pct, ...) and the
scoring do not change, so historical results stay readable and comparable.

  Action classification accuracy   valid commands whose extracted action equals the
                                   labelled action; denominator = valid commands
                                   with an action label (130 in the reported runs)
  Entity extraction accuracy       valid commands with an entity label whose extracted
  (normalised exact match)         entity TEXT equals the label after normalisation
                                   (lowercase, punctuation except '/' ignored);
                                   denominator = commands with an entity label (92).
                                   Not entity-type classification, not NER F1, and
                                   not ontology linking.

The denominators are counted from the dataset and the labels (label_counts), not
written into the report text by hand.
"""
import json
from pathlib import Path

ACTION_LABEL = "Action classification accuracy"
ENTITY_LABEL = "Entity extraction accuracy (normalised exact match)"
END_SIGNAL_LABEL = "End-signal F1"

ROOT = Path(__file__).resolve().parent.parent.parent
DATASET = ROOT / "data" / "annotated_commands.json"
GOLD = ROOT / "data" / "labels" / "gold_labels.json"


def label_counts(dataset_path: Path = DATASET, gold_path: Path = GOLD) -> dict:
    """Numbers of commands and labels, counted from the files."""
    items = json.loads(Path(dataset_path).read_text())
    gold = json.loads(Path(gold_path).read_text()) if Path(gold_path).exists() else {}
    valid = [c for c in items if c.get("valid") is True]
    return {
        "commands": len(items),
        "valid": len(valid),
        "invalid": sum(1 for c in items if c.get("valid") is False),
        "action": sum(1 for c in valid if c.get("expected_action")),
        "entity": sum(1 for c in valid if gold.get(c["id"], {}).get("entity") is not None),
        "end_signal": sum(1 for c in valid if gold.get(c["id"], {}).get("end_signal") is not None),
    }
