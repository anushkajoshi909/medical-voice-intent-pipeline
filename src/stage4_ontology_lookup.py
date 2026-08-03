#!/usr/bin/env python3
"""Stage 4: Ontology Entity Lookup and Canonical Resolution

Maps the raw entity string (preserved as-spoken by Stage 2) to a canonical
label, semantic type, and ontology identifier.

Lookup source per action
  show           → vital_signs.json  (vital_sign, lab_result)
                   domain_config canonical_entities (patient_info, document, messaging)
  diagnosis      → doid.obo (disease)  +  hp.obo (symptom / finding)
  start / stop   → Thesaurus.txt NCIt filtered to Therapeutic/Lab Procedure
  set_medication → RXNCONSO.RRF  (medication)
  change_patient → passthrough — entity IS the patient identifier
  set_note /
  send_message   → passthrough — entity is free_text
  emergency / accept_emergency /
  leave_emergency / continue → no entity

Matching order: exact → synonym → fuzzy (difflib, cutoff 0.72) → none
Indices are built lazily on the first lookup call and cached for the session.
"""
import json
import re
import sys
import yaml
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ONTO = ROOT / "ontologies"

# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class OntologyResult:
    found: bool
    raw_entity: str
    canonical: str | None
    semantic_type: str | None
    ontology_id: str | None
    match_method: str   # "exact" | "synonym" | "fuzzy" | "passthrough" | "none"
    score: float        # 1.0=exact, 0.9=synonym, 0.7+=fuzzy, 0.0=none
    source: str | None  # "vital_signs" | "domain_config" | "doid" | "hp" | "rxnorm" | "ncit"


# ── Internal entry type ────────────────────────────────────────────────────────
# (canonical_label, semantic_type, ontology_id, source_name)
_Entry = tuple[str, str, str | None, str]


# ── Normalisation ─────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r" +", " ", s).strip()


# ── Index builder ─────────────────────────────────────────────────────────────

def _build_maps(
    raw_entries: list[tuple[str, str, str | None, str, list[str]]],
) -> tuple[dict[str, _Entry], list[tuple[str, _Entry]]]:
    """Return (exact_map, fuzzy_list).

    exact_map  : normalized string → _Entry  (canonical + all synonyms)
    fuzzy_list : (normalized_canonical, _Entry) — canonical labels only
    """
    exact: dict[str, _Entry] = {}
    fuzzy: list[tuple[str, _Entry]] = []

    for canonical, stype, oid, src, synonyms in raw_entries:
        entry: _Entry = (canonical, stype, oid, src)
        nc = _norm(canonical)

        # Canonical goes into both maps
        if nc not in exact:
            exact[nc] = entry
        fuzzy.append((nc, entry))

        # Synonyms go into exact map only (preferred names are used for fuzzy)
        for syn in synonyms:
            ns = _norm(syn)
            if ns and ns not in exact:
                exact[ns] = entry

    return exact, fuzzy


# ── Individual loaders ────────────────────────────────────────────────────────

def _load_vital_signs() -> list[tuple]:
    data = json.loads((ONTO / "vital_signs.json").read_text())
    return [(item["label"], item["semantic_type"], None, "vital_signs", [])
            for item in data]


def _load_domain_config_show() -> list[tuple]:
    """Load closed-set EHR record types from domain_config for exact-match validation.
    No synonyms — LLM already outputs the canonical name; Stage 4 just validates it."""
    cfg = yaml.safe_load((ROOT / "domain_config.yaml").read_text())
    rows = []
    canon = cfg.get("actions", {}).get("show", {}).get("canonical_entities", {})
    for stype, labels in canon.items():
        for label in labels:
            rows.append((label, stype, None, "domain_config", []))
    return rows



def _load_obo(path: Path, semantic_type: str, source: str) -> list[tuple]:
    rows = []
    current: dict = {}
    in_term = False

    for raw_line in path.open(encoding="utf-8"):
        line = raw_line.rstrip()

        if line == "[Term]":
            if in_term and current.get("id") and current.get("name") \
                    and not current.get("is_obsolete"):
                rows.append((current["name"], semantic_type, current["id"],
                              source, current.get("synonyms", [])))
            current = {}
            in_term = True
        elif line.startswith("[") and line != "[Term]":
            in_term = False
        elif in_term:
            if line.startswith("id:"):
                current["id"] = line[3:].strip()
            elif line.startswith("name:"):
                current["name"] = line[5:].strip()
            elif line.startswith("is_obsolete:"):
                if "true" in line:
                    current["is_obsolete"] = True
            elif line.startswith("synonym:"):
                m = re.match(r'synonym:\s+"([^"]+)"', line)
                if m:
                    current.setdefault("synonyms", []).append(m.group(1))

    # Last term
    if in_term and current.get("id") and current.get("name") \
            and not current.get("is_obsolete"):
        rows.append((current["name"], semantic_type, current["id"],
                     source, current.get("synonyms", [])))
    return rows


_NCIT_PROCEDURE_TYPES = frozenset([
    "Therapeutic or Preventive Procedure",
    "Laboratory Procedure",
    "Diagnostic Procedure",
])

def _load_ncit() -> list[tuple]:
    rows = []
    for raw_line in (ONTO / "Thesaurus.txt").open(encoding="utf-8"):
        parts = raw_line.rstrip("\n").split("\t")
        if len(parts) < 8:
            continue
        code = parts[0].strip()
        names_field = parts[3].strip()
        ncit_type = parts[7].strip()

        if ncit_type not in _NCIT_PROCEDURE_TYPES:
            continue

        name_parts = [p.strip() for p in names_field.split("|") if p.strip()]
        if not name_parts:
            continue
        canonical = name_parts[0]
        # Skip entries where "canonical" is all-caps (display variant only, no real preferred name)
        if canonical.isupper() and len(name_parts) > 1:
            # Use the mixed-case alternative
            for alt in name_parts[1:]:
                if not alt.isupper():
                    canonical = alt
                    break

        synonyms = [p for p in name_parts[1:] if p != canonical]
        # Map NCIt type to internal semantic_type
        stype = "procedure"
        rows.append((canonical, stype, f"NCIt:{code}", "ncit", synonyms))
    return rows


_RXNORM_TTY = frozenset(["IN", "BN", "PIN", "MIN"])

def _load_rxnorm() -> list[tuple]:
    concepts: dict[str, list[tuple[str, str]]] = {}

    for raw_line in (ONTO / "RXNCONSO.RRF").open(encoding="utf-8"):
        parts = raw_line.split("|")
        if len(parts) < 17:
            continue
        rxcui = parts[0]
        lat = parts[1]
        sab = parts[11]
        tty = parts[12]
        string = parts[14]
        suppress = parts[16]

        if lat != "ENG" or sab != "RXNORM" or suppress == "Y":
            continue
        if tty not in _RXNORM_TTY:
            continue

        concepts.setdefault(rxcui, []).append((tty, string))

    rows = []
    for rxcui, names in concepts.items():
        # Prefer generic (IN) as canonical; fall back to brand (BN)
        generics = [n for t, n in names if t == "IN"]
        brands   = [n for t, n in names if t == "BN"]
        others   = [n for t, n in names if t not in ("IN", "BN")]
        all_names = generics or brands or others
        if not all_names:
            continue
        canonical = all_names[0]
        synonyms  = list({n for _, n in names} - {canonical})
        rows.append((canonical, "medication", f"RXCUI:{rxcui}", "rxnorm", synonyms))

    return rows


# ── Singleton cache ───────────────────────────────────────────────────────────

_cache: dict[str, tuple[dict, list]] = {}
_loaded = False


def _ensure_loaded(verbose: bool = False) -> None:
    global _loaded
    if _loaded:
        return

    def _msg(s: str):
        if verbose:
            print(f"  [Stage 4] {s}", file=sys.stderr)

    _msg("Loading ontologies …")

    _msg("vital_signs.json + domain_config canonical_entities …")
    # show index: vital_signs.json (open-set clinical measurements, exact match)
    # + domain_config canonical_entities (closed-set EHR record types, exact match only).
    # LLM already outputs canonical names for EHR record types; Stage 4 validates
    # the entity is known and assigns its semantic_type.
    _cache["show"] = _build_maps(_load_vital_signs() + _load_domain_config_show())

    _msg("doid.obo …")
    doid_rows = _load_obo(ONTO / "doid.obo", "disease", "doid")
    _msg("hp.obo …")
    hp_rows   = _load_obo(ONTO / "hp.obo",   "symptom", "hp")
    _cache["diagnosis"] = _build_maps(doid_rows + hp_rows)

    _msg("Thesaurus.txt (NCIt) …")
    ncit_rows = _load_ncit()
    _cache["procedure"] = _build_maps(ncit_rows)

    _msg("RXNCONSO.RRF …")
    rx_rows = _load_rxnorm()
    _cache["medication"] = _build_maps(rx_rows)

    _loaded = True
    _msg("Ontologies ready.")


# ── Matcher ───────────────────────────────────────────────────────────────────

# Higher cutoff for clinical indexes where false positives are dangerous
_FUZZY_CUTOFF = {
    "show":       0.72,  # display terms — looser is fine
    "diagnosis":  0.90,  # disease/symptom — strict to avoid hemolysis→hemoptysis
    "procedure":  0.86,  # treatments/procedures
    "medication": 0.88,  # drug names
}
_FUZZY_N = 1


_INT_OF_RE = re.compile(r"\bof\b\s+(.+)", re.IGNORECASE)


def _extract_medicine(entity: str) -> str:
    """Extract the medicine name from a grammar-validated medication entity.

    The grammar guarantees exactly two structures:
      medicine INT unit      → "metformin 500 mg"         → "metformin"
      INT unit OF medicine   → "500 mg of metformin"      → "metformin"

    For anything more complex ("metformin 500 mg twice a day"), pattern 1 still
    applies — name is everything before the first digit.
    Raw entity is never modified; this is lookup-only extraction.
    """
    s = entity.strip()
    # Pattern 2: starts with a digit → "INT unit OF medicine"
    if s and s[0].isdigit():
        m = _INT_OF_RE.search(s)
        return m.group(1).strip() if m else s
    # Pattern 1: starts with text → "medicine INT unit [...]"
    # Take everything before the first digit
    idx = next((i for i, c in enumerate(s) if c.isdigit()), len(s))
    return s[:idx].strip() or s


def _match(query_norm: str, index_name: str) -> OntologyResult | None:
    exact_map, fuzzy_list = _cache[index_name]

    entry = exact_map.get(query_norm)
    if entry:
        method = "exact" if query_norm == _norm(entry[0]) else "synonym"
        score  = 1.0 if method == "exact" else 0.9
        return OntologyResult(True, "", entry[0], entry[1], entry[2], method, score, entry[3])

    # Fuzzy over canonical labels only
    cutoff = _FUZZY_CUTOFF.get(index_name, 0.80)
    candidates = [nc for nc, _ in fuzzy_list]
    matches = get_close_matches(query_norm, candidates, n=_FUZZY_N, cutoff=cutoff)
    if matches:
        best_nc = matches[0]
        entry = next((e for nc, e in fuzzy_list if nc == best_nc), None)
        if entry:
            import difflib
            score = difflib.SequenceMatcher(None, query_norm, best_nc).ratio()
            return OntologyResult(True, "", entry[0], entry[1], entry[2], "fuzzy", score, entry[3])

    return None


# ── Public API ────────────────────────────────────────────────────────────────

# Actions where entity has a specific semantic type but no ontology lookup
_PASSTHROUGH_PATIENT    = frozenset(["change_patient"])
_PASSTHROUGH_FREE_TEXT  = frozenset(["set_note", "send_message"])

# Primary ontology index per action
_ACTION_INDEX = {
    "show":           "show",
    "diagnosis":      "diagnosis",
    "start":          "procedure",
    "stop":           "procedure",
    "set_medication": "medication",
}

# Fallback index: tried when primary returns no match
# start/stop allow medication slot type — if not in NCIt try RXNORM
_ACTION_FALLBACK = {
    "start": "medication",
    "stop":  "medication",
}


def _lookup_medication(raw_entity: str) -> OntologyResult | None:
    """Medication lookup using grammar-driven medicine name extraction.

    The grammar guarantees entity is either "medicine INT unit" or
    "INT unit OF medicine". We extract just the medicine name for lookup;
    the raw entity string is never modified.
    """
    # Try full entity first (covers the rare case where entity IS just a drug name)
    result = _match(_norm(raw_entity), "medication")
    if result:
        return result

    # Extract medicine name per grammar structure, then look up
    medicine = _extract_medicine(raw_entity)
    if medicine.lower() != raw_entity.lower():
        result = _match(_norm(medicine), "medication")

    return result


def lookup(action: str, raw_entity: str, verbose: bool = False) -> OntologyResult:
    """Resolve raw_entity for a given action using the appropriate ontology.

    Call this only when Stage 3 passed (frame_valid=True) and action != "UNKNOWN".
    """
    raw_entity = raw_entity.strip()

    # Explicit passthroughs with known semantic types
    if action in _PASSTHROUGH_PATIENT:
        return OntologyResult(True, raw_entity, raw_entity, "patient_identifier",
                              None, "passthrough", 1.0, None)
    if action in _PASSTHROUGH_FREE_TEXT:
        return OntologyResult(True, raw_entity, raw_entity, "free_text",
                              None, "passthrough", 1.0, None)

    # Actions with no defined ontology index → passthrough
    # (system commands, display toggles, emergency actions, etc.)
    index_name = _ACTION_INDEX.get(action)
    if index_name is None:
        return OntologyResult(True, raw_entity, None, None, None, "passthrough", 1.0, None)

    if not raw_entity:
        return OntologyResult(False, raw_entity, None, None, None, "none", 0.0, None)

    _ensure_loaded(verbose=verbose)

    query_norm = _norm(raw_entity)

    # Medication lookup gets dose-stripping; others use plain match
    if index_name == "medication":
        result = _lookup_medication(raw_entity)
    else:
        result = _match(query_norm, index_name)
        # Try fallback index (e.g. start/stop → also check RXNORM for drug entities)
        if not result:
            fallback = _ACTION_FALLBACK.get(action)
            if fallback == "medication":
                result = _lookup_medication(raw_entity)
            elif fallback:
                result = _match(query_norm, fallback)

    if result:
        result.raw_entity = raw_entity
        return result

    return OntologyResult(False, raw_entity, None, None, None, "none", 0.0, None)


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cases = [
        ("show",          "patient history"),
        ("show",          "hemoglobin"),
        ("show",          "discharge letter"),
        ("show",          "CBC"),
        ("show",          "movie"),
        ("diagnosis",     "hemolysis"),
        ("diagnosis",     "hypertension"),
        ("diagnosis",     "chest pain"),
        ("start",         "hemodialysis"),
        ("start",         "vasopressors"),
        ("stop",          "infusion"),
        ("set_medication","paracetamol 100 mg"),
        ("set_medication","ibuprofen"),
        ("change_patient","Peter Parker"),
        ("set_note",      "patient presenting with fever"),
        ("emergency",     ""),
    ]
    for action, entity in cases:
        r = lookup(action, entity, verbose=True)
        sym = "✓" if r.found else "✗"
        print(f"[{sym}] {action:16s} raw={entity!r:30s} → "
              f"canonical={r.canonical!r:30s} type={r.semantic_type!r:18s} "
              f"method={r.match_method:10s} score={r.score:.2f}  src={r.source}")
