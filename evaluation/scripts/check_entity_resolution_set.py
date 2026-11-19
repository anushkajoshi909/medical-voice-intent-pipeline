#!/usr/bin/env python3
"""Check the entity-resolution gold set (data/labels/entity_resolution_gold.csv) against the
raw ontology files. It does NOT import or run any pipeline stage, so the gold stays independent of
the system under test.

For every row it verifies the claims the labels make:
  exact            the entity is the canonical label of the expected concept
  synonym          the entity is a synonym (not the label) of the expected concept
  misspelling      the concept exists, the misspelled entity is not a term in any ontology
  unseen_synonym   the concept exists, the entity is not a term in any ontology
  not_in_ontology  the entity is not a term in the ontologies the action searches
  wrong_type / collision   the expected concept exists and (for wrong_type) the entity is one of its terms
Dose, unit and timing words are cut off medication entities before the term check
(everything from the first digit).

Usage: python evaluation/scripts/check_entity_resolution_set.py
"""
import csv
import re
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ONT = ROOT / "ontologies"
SET = ROOT / "data" / "labels" / "entity_resolution_gold.csv"
ACTION_SOURCES = {"diagnosis": ["DOID", "HP"], "start": ["NCIt", "RXCUI"], "stop": ["NCIt", "RXCUI"],
                  "set_medication": ["RXCUI"], "show": ["vital_signs"]}


def n(s):
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


terms = collections.defaultdict(lambda: collections.defaultdict(set))   # source -> term -> ids
label = {}                                                              # id -> canonical label


def add(src, cid, canon, syns):
    label[cid] = canon
    for t in [canon, *syns]:
        terms[src][n(t)].add(cid)


def load_obo(path, src):
    cur = None
    for line in open(ONT / path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line == "[Term]":
            cur = {"syn": [], "obs": False}
        elif cur is None:
            continue
        elif line.startswith("id: "):
            cur["id"] = line[4:]
        elif line.startswith("name: "):
            cur["name"] = line[6:]
        elif line.startswith("is_obsolete: true"):
            cur["obs"] = True
        elif line.startswith("synonym: "):
            m = re.match(r'synonym: "(.*)" \w+', line)
            if m:
                cur["syn"].append(m.group(1))
        elif line == "" and "id" in cur:
            if not cur["obs"]:
                add(src, cur["id"], cur["name"], cur["syn"])
            cur = None


def load_ncit():
    types = {"Therapeutic or Preventive Procedure", "Laboratory Procedure", "Diagnostic Procedure"}
    for line in open(ONT / "Thesaurus.txt", encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) < 8 or p[7].strip() not in types:
            continue
        names = [x.strip() for x in p[3].split("|") if x.strip()]
        if not names:
            continue
        canon = names[0]
        if canon.isupper() and len(names) > 1:
            canon = next((a for a in names[1:] if not a.isupper()), canon)
        add("NCIt", f"NCIt:{p[0].strip()}", canon, names)


def load_rxnorm():
    by = collections.defaultdict(list)
    for line in open(ONT / "RXNCONSO.RRF", encoding="utf-8"):
        p = line.split("|")
        if len(p) < 17 or p[1] != "ENG" or p[11] != "RXNORM" or p[16] == "Y" or p[12] not in ("IN", "BN", "PIN", "MIN"):
            continue
        by[p[0]].append((p[12], p[14]))
    for cui, names in by.items():
        pick = [s for t, s in names if t == "IN"] or [s for t, s in names if t == "BN"] or [s for _, s in names]
        add("RXCUI", f"RXCUI:{cui}", pick[0], [s for _, s in names])


def load_vital():
    for x in json.load(open(ONT / "vital_signs.json")):
        add("vital_signs", f"vital_signs:{x['label']}", x["label"], [])


load_obo("doid.obo", "DOID")
load_obo("hp.obo", "HP")
load_ncit()
load_rxnorm()
load_vital()


def name_part(entity, action):
    if action == "set_medication":
        return n(re.split(r"\s\d", " " + entity)[0])
    return n(entity)


def ids_of(cid):
    # vital-sign rows carry no id in the set: look the label up
    return cid


problems = []
rows = list(csv.DictReader(open(SET, newline="")))
for r in rows:
    act, ent, cat = r["action"], name_part(r["entity"], r["action"]), r["category"]
    srcs = ACTION_SOURCES[act]
    in_action = {i for s in srcs for i in terms[s].get(ent, ())}
    in_any = {i for s in terms for i in terms[s].get(ent, ())}
    cids = [c.strip() for c in r["expected_id"].split(";") if c.strip()]
    if not cids and r["expected_concept"] and act == "show":
        cids = [f"vital_signs:{r['expected_concept']}"] if f"vital_signs:{r['expected_concept']}" in label else []
    bad = []
    for c in cids:
        if c not in label:
            bad.append(f"expected id {c} not found in the ontology files")
        elif n(label[c]) != n(r["expected_concept"].split(";")[0]) and len(cids) == 1:
            bad.append(f"label of {c} is {label[c]!r}, set says {r['expected_concept']!r}")
    if cat == "exact" and cids and len(cids) == 1 and cids[0] in label and n(label[cids[0]]) != ent:
        bad.append(f"entity is not the label {label[cids[0]]!r}")
    if cat == "exact" and r["expected_type"] == "ehr_record" and in_action:
        bad.append("record category is in the vital-sign list")
    if cat == "synonym" and cids and len(cids) == 1 and cids[0] in label:
        if cids[0] not in in_any:
            bad.append("entity is not a term of the expected concept")
        elif n(label[cids[0]]) == ent:
            bad.append("entity equals the label, not a synonym")
    if cat in ("misspelling", "unseen_synonym") and in_any:
        bad.append(f"entity is already a term in an ontology: {sorted(in_any)[:3]}")
    if cat == "not_in_ontology" and in_action:
        bad.append(f"entity is a term in the searched ontologies: {sorted(in_action)[:3]}")
    if cat == "wrong_type" and cids and len(cids) == 1 and cids[0] in label and cids[0] not in in_any:
        bad.append("entity is not a term of the expected concept")
    if r["expected_status"] == "entity_not_found" and in_action:
        bad.append(f"expected not found but present in the action's ontologies: {sorted(in_action)[:3]}")
    if cat == "collision" and r["entity"] and len(in_any) < 1 and r["expected_status"] != "entity_not_found":
        bad.append("collision row but the entity is not in any ontology")
    for b in bad:
        problems.append((r["id"], act, r["entity"], b))

print(f"{len(rows)} rows checked, {len(problems)} problems")
for p in problems:
    print("  ", *p)
print(collections.Counter(r["category"] for r in rows))
