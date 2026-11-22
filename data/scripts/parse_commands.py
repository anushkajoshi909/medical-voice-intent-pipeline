#!/usr/bin/env python3
"""Parse the 200-command test set from Ludyga's wiki (Language-samples.md)
into structured JSON: [{id, category, valid, text}, ...]
"""
import json
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "baseline_ludyga_wiki" / "Language-samples.md"
OUT = Path(__file__).resolve().parents[2] / "data" / "commands_200.json"

CATEGORY_MAP = {
    "40 clearly invalid strings": ("invalid_garbage", False),
    "30 ambiguous commands": ("invalid_ambiguous", False),
    "30 commands out of medical domain": ("invalid_out_of_domain", False),
    "20x u01-u04": ("valid_u01_u04", True),
    "20x u09-u12": ("valid_u09_u12", True),
    "20x u13-u14": ("valid_u13_u14", True),
    "10x u15": ("valid_u15", True),
    "10x u16-u17": ("valid_u16_u17", True),
    "20x u18-u20": ("valid_u18_u20", True),
}

def main():
    lines = SRC.read_text(encoding="utf-8").splitlines()
    current_category = None
    commands = []
    idx = 1
    for line in lines:
        h2 = re.match(r"^## (.+)$", line.strip())
        if h2:
            current_category = h2.group(1).strip()
            continue
        item = re.match(r"^\* (.+)$", line.strip())
        if item and current_category in CATEGORY_MAP:
            cat_slug, valid = CATEGORY_MAP[current_category]
            text = item.group(1).strip()
            commands.append({
                "id": f"c{idx:03d}",
                "category": cat_slug,
                "valid": valid,
                "text": text,
            })
            idx += 1

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(commands, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Parsed {len(commands)} commands -> {OUT}")
    valid_n = sum(1 for c in commands if c["valid"])
    print(f"  valid: {valid_n}, invalid: {len(commands) - valid_n}")

if __name__ == "__main__":
    main()
