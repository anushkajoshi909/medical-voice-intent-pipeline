#!/usr/bin/env python3
"""Gold-label worksheet for entity accuracy and end-signal metrics.

Gold label = the correct answer for a command, written by a human (not by the system).
The evaluation compares the system's output against it.

  draft  Write an empty evaluation/dataset/gold_labels_worksheet.csv (id, text, expected_action, entity,
         end_signal) for the valid commands that carry an entity, to be filled in by a human.
         Commands whose action has no entity (emergency, turn on/off, continue) are left out.
         Items that already carry expected_entity / expected_entity_expanded in the dataset are
         pre-filled. No model output is used, so the labels stay independent of the system under test.
         Refuses to overwrite an existing worksheet.
  build  Read the worksheet and write evaluation/dataset/gold_labels.json from the filled columns
         only (run_eval.py joins that file on the item id).

Worksheet columns
  id, text, expected_action
  entity       correct entity ('-' = the command has no entity), after normalisation: lowercase,
               abbreviations expanded, filler words such as 'the' / 'please' removed.
               Leave empty = not labelled.
  end_signal   dictation actions only (set_note, send_message): did the user say a finish signal
               ("finish note", "send it", ...)? TRUE / FALSE

Usage
  python evaluation/scripts/make_gold_labels.py draft
  (edit the CSV: fill the 'entity' / 'end_signal' columns)
  python evaluation/scripts/make_gold_labels.py build
"""
import csv
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "annotated_commands.json"
OUT_DIR = ROOT / "evaluation" / "dataset"
WORKSHEET = OUT_DIR / "gold_labels_worksheet.csv"
GOLD = OUT_DIR / "gold_labels.json"
DICTATION = {"set_note", "send_message"}


def norm(s):
    return " ".join(str(s or "").lower().split())


def draft():
    if WORKSHEET.exists():
        sys.exit(f"{WORKSHEET} already exists (it holds labels); delete it first to start over")
    items = json.loads(DATA.read_text())
    # actions defined without an entity (emergency, turn on/off, continue): nothing to label
    cfg = yaml.safe_load((ROOT / "domain_config.yaml").read_text())["actions"]
    entity_free = {a for a, info in cfg.items() if info.get("entity_slot") is False}

    rows = []
    for it in items:
        if not it["valid"] or it.get("expected_action") in entity_free:
            continue
        given = it.get("expected_entity_expanded") or it.get("expected_entity")
        rows.append({
            "id": it["id"], "text": it["text"], "expected_action": it.get("expected_action"),
            "entity": norm(given) if given else "",
            "end_signal": "",
        })
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(WORKSHEET, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} valid commands -> {WORKSHEET}")
    print(f"  pre-filled from the dataset: {sum(1 for r in rows if r['entity'])}")
    print(f"  to fill: entity for the rest; end_signal for {sum(1 for r in rows if r['expected_action'] in DICTATION)} dictation items")


def build():
    gold = {}
    with open(WORKSHEET, newline="") as f:
        for r in csv.DictReader(f):
            g = {}
            if r["entity"].strip():
                # "-" = the command correctly has NO entity (e.g. bare "stop")
                g["entity"] = "" if r["entity"].strip() == "-" else norm(r["entity"])
            if r["end_signal"].strip().upper() in ("TRUE", "FALSE"):
                g["end_signal"] = r["end_signal"].strip().upper() == "TRUE"
            if g:
                gold[r["id"]] = g
    GOLD.write_text(json.dumps(gold, indent=2, ensure_ascii=False))
    print(f"{len(gold)} items with confirmed labels -> {GOLD}")


if __name__ == "__main__":
    {"draft": draft, "build": build}[sys.argv[1] if len(sys.argv) > 1 else "draft"]()
