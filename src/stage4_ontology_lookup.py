#!/usr/bin/env python3
"""Stage 4: Ontology Entity Lookup and Canonical Resolution

Maps the raw entity string (preserved as-spoken by Stage 2) to a canonical
label, semantic type, and ontology identifier.

Routing is driven entirely by domain_config.yaml — each action declares:
  ontology_index    : which cache index to search (null = passthrough)
  fallback_index    : secondary index if primary misses (null = no fallback)
  entity_extractor  : strategy applied before lookup
                        "default"    — use entity as-is
                        "medication" — strip dose/unit, then lookup
  passthrough_type  : semantic_type to assign when ontology_index is null
                      and the entity carries meaning (patient name, free text)

Adding a new action or new entity format:
  1. Add ontology_index / entity_extractor to domain_config.yaml
  2. If a new extractor is needed, add _strategy_X() and register in _STRATEGIES
  3. No changes to this routing logic required

Matching order: exact → synonym → fuzzy (difflib, per-index cutoff) → none
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
    match_method: str         # "exact" | "synonym" | "fuzzy" | "passthrough" | "none"
    score: float              # 1.0=exact, 0.9=synonym, 0.7+=fuzzy, 0.0=none
    source: str | None        # "vital_signs" | "domain_config" | "doid" | "hp" | "rxnorm" | "ncit"
    clarification_type: str | None = None      # set when entity found in a DIFFERENT index
    clarification_canonical: str | None = None # canonical label from that global match


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
# Each loader receives a `src` dict (one entry from ontology_sources in
# domain_config.yaml). All domain knowledge (filters, semantic types, file paths)
# comes from that dict — functions are pure format parsers.
#
# To add a new file format for a new domain:
#   1. Write _load_X(src: dict) -> list[tuple]
#   2. Register it in _FORMAT_LOADERS below

def _load_json_labels(src: dict) -> list[tuple]:
    path   = ROOT / src["file"]
    sname  = src.get("source_name", Path(src["file"]).stem)
    data   = json.loads(path.read_text())
    return [(item["label"], item["semantic_type"], None, sname, [])
            for item in data]


def _load_domain_config_show(src: dict) -> list[tuple]:
    """Read closed-set EHR record types from actions.show.canonical_entities.
    Uses the already-loaded _action_cfg — no second file read."""
    rows = []
    canon = _action_cfg.get("show", {}).get("canonical_entities", {})
    for stype, labels in canon.items():
        for label in labels:
            rows.append((label, stype, None, "domain_config", []))
    return rows


def _load_obo(src: dict) -> list[tuple]:
    path          = ROOT / src["file"]
    semantic_type = src["semantic_type"]
    source        = src.get("source_name", path.stem)
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

    if in_term and current.get("id") and current.get("name") \
            and not current.get("is_obsolete"):
        rows.append((current["name"], semantic_type, current["id"],
                     source, current.get("synonyms", [])))
    return rows


def _load_ncit(src: dict) -> list[tuple]:
    path         = ROOT / src["file"]
    filter_types = frozenset(src["filter_semantic_types"])
    stype        = src.get("semantic_type", "procedure")
    source       = src.get("source_name", "ncit")
    rows = []
    for raw_line in path.open(encoding="utf-8"):
        parts = raw_line.rstrip("\n").split("\t")
        if len(parts) < 8:
            continue
        code        = parts[0].strip()
        names_field = parts[3].strip()
        ncit_type   = parts[7].strip()

        if ncit_type not in filter_types:
            continue

        name_parts = [p.strip() for p in names_field.split("|") if p.strip()]
        if not name_parts:
            continue
        canonical = name_parts[0]
        if canonical.isupper() and len(name_parts) > 1:
            for alt in name_parts[1:]:
                if not alt.isupper():
                    canonical = alt
                    break

        synonyms = [p for p in name_parts[1:] if p != canonical]
        rows.append((canonical, stype, f"NCIt:{code}", source, synonyms))
    return rows


def _load_rxnorm(src: dict) -> list[tuple]:
    path       = ROOT / src["file"]
    tty_filter = frozenset(src.get("term_types", ["IN", "BN", "PIN", "MIN"]))
    stype      = src.get("semantic_type", "medication")
    source     = src.get("source_name", "rxnorm")
    concepts: dict[str, list[tuple[str, str]]] = {}

    for raw_line in path.open(encoding="utf-8"):
        parts = raw_line.split("|")
        if len(parts) < 17:
            continue
        rxcui    = parts[0]
        lat      = parts[1]
        sab      = parts[11]
        tty      = parts[12]
        string   = parts[14]
        suppress = parts[16]

        if lat != "ENG" or sab != "RXNORM" or suppress == "Y":
            continue
        if tty not in tty_filter:
            continue

        concepts.setdefault(rxcui, []).append((tty, string))

    rows = []
    for rxcui, names in concepts.items():
        generics  = [n for t, n in names if t == "IN"]
        brands    = [n for t, n in names if t == "BN"]
        others    = [n for t, n in names if t not in ("IN", "BN")]
        all_names = generics or brands or others
        if not all_names:
            continue
        canonical = all_names[0]
        synonyms  = list({n for _, n in names} - {canonical})
        rows.append((canonical, stype, f"RXCUI:{rxcui}", source, synonyms))

    return rows


# ── Format loader registry ─────────────────────────────────────────────────────
# Maps type names (from ontology_sources in domain_config.yaml) to loader fns.
# New file format for a new domain → add _load_X(src) + register here.

_FORMAT_LOADERS: dict = {
    "json_labels":             _load_json_labels,
    "domain_config_canonical": _load_domain_config_show,
    "obo":                     _load_obo,
    "ncit":                    _load_ncit,
    "rxnorm":                  _load_rxnorm,
}


# ── Singleton cache ───────────────────────────────────────────────────────────

_cache: dict[str, tuple[dict, list]] = {}
_FUZZY_CUTOFF: dict[str, float] = {}   # populated by _ensure_loaded from config
_loaded = False


def _ensure_loaded(verbose: bool = False) -> None:
    """Load all ontology indexes declared in ontology_sources in domain_config.yaml.

    Generic loop — no index names or file paths hardcoded here.
    To add a new index: edit domain_config.yaml (ontology_sources) only.
    """
    global _loaded, _FUZZY_CUTOFF
    if _loaded:
        return

    def _msg(s: str):
        if verbose:
            print(f"  [Stage 4] {s}", file=sys.stderr)

    _msg("Loading ontologies …")

    ont_cfg = yaml.safe_load((ROOT / "domain_config.yaml").read_text()).get("ontology_sources", {})

    for index_name, index_info in ont_cfg.items():
        _msg(f"Building '{index_name}' index …")
        rows: list[tuple] = []
        for src in index_info.get("sources", []):
            loader = _FORMAT_LOADERS.get(src["type"])
            if loader is None:
                raise ValueError(
                    f"Unknown ontology source type: {src['type']!r}. "
                    f"Register a _load_X(src) function in _FORMAT_LOADERS."
                )
            rows.extend(loader(src))
        _cache[index_name] = _build_maps(rows)

    _FUZZY_CUTOFF = {
        name: info.get("fuzzy_cutoff", 0.80)
        for name, info in ont_cfg.items()
    }

    _loaded = True
    _msg("Ontologies ready.")


# ── Matcher ───────────────────────────────────────────────────────────────────

_FUZZY_N = 1  # _FUZZY_CUTOFF is populated at load time from ontology_sources config


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


# ── Lookup strategies ─────────────────────────────────────────────────────────
# Each strategy takes (raw_entity, index_name) and returns OntologyResult|None.
# Register new strategies here — reference them via entity_extractor in
# domain_config.yaml. No changes to the routing logic in lookup() required.

def _strategy_default(raw_entity: str, index_name: str) -> "OntologyResult | None":
    return _match(_norm(raw_entity), index_name)


def _strategy_medication(raw_entity: str, index_name: str) -> "OntologyResult | None":
    return _lookup_medication(raw_entity)


_STRATEGIES: dict = {
    "default":    _strategy_default,
    "medication": _strategy_medication,
}


# ── Action config (loaded lazily from domain_config.yaml) ─────────────────────

_action_cfg: dict = {}
_action_cfg_loaded = False


def _ensure_action_config_loaded() -> None:
    global _action_cfg, _action_cfg_loaded
    if _action_cfg_loaded:
        return
    from domain_loader import load as _domain_load
    _action_cfg = _domain_load()["action_config"]
    _action_cfg_loaded = True


# ── Medication helper (used by _strategy_medication) ──────────────────────────

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


def _global_clarification_search(
    query_norm: str, exclude_indexes: set[str]
) -> "OntologyResult | None":
    """Exact + synonym search across ALL indexes except those already tried.

    Called only when the action-specific (and fallback) search failed.
    Identifies what the entity actually IS so Stage 5 / Stage 6 can produce
    a specific message ("hemodialysis is a procedure, not a diagnosis")
    rather than a generic "entity not found".
    No fuzzy matching — only high-confidence matches are used for clarification.
    """
    for index_name, (exact_map, _) in _cache.items():
        if index_name in exclude_indexes:
            continue
        entry = exact_map.get(query_norm)
        if entry:
            method = "exact" if query_norm == _norm(entry[0]) else "synonym"
            return OntologyResult(
                False, "", entry[0], entry[1], entry[2], method,
                1.0 if method == "exact" else 0.9, entry[3],
            )
    return None


def lookup(action: str, raw_entity: str, verbose: bool = False) -> OntologyResult:
    """Resolve raw_entity for a given action using the appropriate ontology.

    Routing is driven by domain_config.yaml — no action names are hardcoded here.
    Call this only when Stage 3 passed (frame_valid=True) and action != "UNKNOWN".
    """
    raw_entity = raw_entity.strip()
    _ensure_action_config_loaded()

    action_info      = _action_cfg.get(action, {})
    ont_index        = action_info.get("ontology_index")    # None → passthrough
    passthrough_type = action_info.get("passthrough_type")  # semantic type for passthrough

    # Passthrough: skip ontology lookup entirely
    # — if passthrough_type is set, assign that semantic type and return entity as canonical
    # — if not set (system/no-entity commands), canonical is None
    if ont_index is None:
        canonical = raw_entity if passthrough_type else None
        return OntologyResult(True, raw_entity, canonical, passthrough_type,
                              None, "passthrough", 1.0, None)

    if not raw_entity:
        return OntologyResult(False, raw_entity, None, None, None, "none", 0.0, None)

    _ensure_loaded(verbose=verbose)

    # Dispatch to the configured extraction strategy
    strategy_name = action_info.get("entity_extractor", "default")
    strategy      = _STRATEGIES.get(strategy_name, _STRATEGIES["default"])
    result = strategy(raw_entity, ont_index)

    # Try fallback index if primary missed
    fallback = action_info.get("fallback_index")
    if not result and fallback:
        fb_strategy_name = action_info.get("fallback_extractor", strategy_name)
        fb_strategy      = _STRATEGIES.get(fb_strategy_name, _STRATEGIES["default"])
        result = fb_strategy(raw_entity, fallback)

    if result:
        result.raw_entity = raw_entity
        return result

    # Primary and fallback both missed.

    # If action declares passthrough_fallback_type, accept the entity as-is
    # with that semantic type (e.g. show action treats anything not a vital sign
    # as ehr_record — the LLM gates clinical validity via domain rules).
    passthrough_fallback = action_info.get("passthrough_fallback_type")
    if passthrough_fallback:
        return OntologyResult(
            True, raw_entity, raw_entity, passthrough_fallback,
            None, "passthrough_fallback", 1.0, None,
        )

    # Search all OTHER indexes (exact+synonym only) to identify what the entity
    # actually is — gives Stage 5 enough info to produce a specific clarification
    # ("hemodialysis is a procedure, not a diagnosis") rather than a generic miss.
    searched = {ont_index} | ({fallback} if fallback else set())
    clarification = _global_clarification_search(_norm(raw_entity), searched)
    if clarification:
        return OntologyResult(
            False, raw_entity, None, None, None, "none", 0.0, None,
            clarification_type=clarification.semantic_type,
            clarification_canonical=clarification.canonical,
        )

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
