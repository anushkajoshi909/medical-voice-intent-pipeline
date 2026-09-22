# Pipeline Architecture: UML Diagrams

> Master Thesis · TU Dresden · Software Technology  
> *Improving Intent Recognition in Grammar-Based Voice Interfaces*

Diagrams render in GitLab, GitHub, and VS Code with the Markdown Preview Mermaid Support extension.

---

## Figure 1 · Pipeline Architecture (Component Flow)

Seven-stage pipeline from raw voice input to a confirmed, executable command.
Stages 1-7 implemented.

```mermaid
graph TD
    IN([Voice Input]) --> S1

    S1["Stage 1 · Preprocessing\nstage1_preprocess.py"]:::impl
    S2["Stage 2 · LLM Extraction\nstage2_llm_extract.py"]:::impl
    S3["Stage 3 · ANTLR Validation\nstage3_antlr_validate.py"]:::impl
    S4["Stage 4 · Ontology Lookup\nstage4_ontology_lookup.py"]:::impl
    S5["Stage 5 · Semantic Validation\nstage5_type_validate.py"]:::impl
    S6["Stage 6 · Intent Output\nstage6_intent_output.py"]:::impl
    S7["Stage 7 · User Confirmation\nstage7_confirmation.py"]:::impl

    S1 --> S2 --> S3 --> S4 --> S5 --> S6 --> S7
    S7 -->|confirmed / auto-confirmed| OUT([Command Executed]):::src
    S7 -->|rejected / unclear| RPT([Repeat Command]):::rej
    RPT -.-> S1

    ABB([abbreviations.json]):::cfg --> S1
    DC([domain_config.yaml]):::cfg --> S2
    DC --> S4
    DC -->|confirmation_timeout_sec| S7
    GR([ANTLR Grammar\n.g4 + compiled]):::cfg --> S3
    ONT([Ontologies\nDOID · HP · NCIt · RXNORM]):::cfg --> S4

    classDef impl fill:#3B72B0,color:#fff,stroke:#2A5A90
    classDef cfg fill:#EBF2FA,color:#1C2438,stroke:#3B72B0
    classDef src fill:#1e293b,color:#fff,stroke:#334155
    classDef rej fill:#ef4444,color:#fff,stroke:#b91c1c
```

---

## Figure 2 · Stage 4 Design Patterns (Class Diagram)

Stage 4 applies the **Strategy pattern** (pluggable entity extraction) and the **Registry pattern** (pluggable format loaders).
`domain_config.yaml` is the single source of truth for all routing decisions.

```mermaid
classDiagram
    direction TB

    class OntologyResult {
        <<dataclass>>
        +bool found
        +str raw_entity
        +str canonical
        +str semantic_type
        +str ontology_id
        +str match_method
        +float score
        +str source
    }

    class lookup {
        <<entry point>>
        +lookup(action, entity) OntologyResult
        -_ensure_action_config_loaded()
        -_ensure_loaded()
        -_match(query, index) OntologyResult
        -_norm(s) str
        -_build_maps(entries) tuple
    }

    class _STRATEGIES {
        <<Strategy Registry>>
        +default : _strategy_default
        +medication : _strategy_medication
    }

    class _FORMAT_LOADERS {
        <<Loader Registry>>
        +json_labels : _load_json_labels
        +obo : _load_obo
        +ncit : _load_ncit
        +rxnorm : _load_rxnorm
        +domain_config_canonical : _load_domain_config_show
    }

    class domain_config_yaml {
        <<Single Source of Truth>>
        +actions[].ontology_index
        +actions[].entity_extractor
        +actions[].fallback_index
        +actions[].passthrough_type
        +ontology_sources[].type
        +ontology_sources[].file
        +ontology_sources[].fuzzy_cutoff
    }

    lookup --> OntologyResult : returns
    lookup --> _STRATEGIES : dispatches extraction via
    lookup --> _FORMAT_LOADERS : populates cache via _ensure_loaded
    lookup --> domain_config_yaml : reads action routing from
    _FORMAT_LOADERS --> domain_config_yaml : parameterised by ontology_sources
```

**Patterns applied:**

| Pattern | Where | Extensibility benefit |
|---------|--------|-----------------------|
| Strategy | `_STRATEGIES` dispatch table | New entity extraction logic = new function + one YAML key |
| Registry | `_FORMAT_LOADERS` dict | New ontology file format = new loader function + register |
| Single Source of Truth | `domain_config.yaml` | Action routing, file paths, cutoffs; all in one place; zero hardcoding in routing code |

---

## Figure 3 · Domain Independence Boundary (Component Diagram)

Separates the **domain-independent core** (unchanged for any new domain) from **domain-specific parts** (replaced when adapting to industry, transportation, tourism, etc.).  
Satisfies the *Domain Independence* non-functional requirement.

```mermaid
graph LR
    subgraph GENERIC["Domain-Independent Core: zero changes for a new domain"]
        direction TB
        LU["lookup()"]
        MT["_match() · _norm() · _build_maps()"]
        STR["_STRATEGIES Registry"]
        FLR["_FORMAT_LOADERS Registry"]
        EL["_ensure_loaded(): generic loop"]
        DL["domain_loader.py: validation + prompt builder"]
        S1C["Stage 1 core: preprocess.py"]
        S2C["Stage 2 core: llm_extract.py"]
        S3C["Stage 3 runner: antlr_validate.py"]
    end

    subgraph DOMAIN["Domain-Specific: replaced for a new domain"]
        direction TB
        DC["domain_config.yaml\nactions + ontology_sources"]
        LD["Loader functions\n_load_obo / _load_ncit / _load_rxnorm"]
        ONT["Ontology files\ndoid.obo · hp.obo · Thesaurus.txt · RXNCONSO.RRF"]
        GR["ANTLR grammar\nMedicalSmartGlasses*.g4"]
        AB["abbreviations.json"]
    end

    DC --> EL
    DC --> DL
    ONT --> LD
    LD --> FLR
    FLR --> EL
    EL --> MT
    MT --> LU
    STR --> LU
    GR --> S3C
    AB --> S1C
```

**Adapting to a new domain requires:**
1. New `domain_config.yaml`: actions, descriptions, ontology sources, rules, examples
2. New ANTLR grammar `.g4` + recompile
3. New ontology data files + loader functions for any new file formats (registered in `_FORMAT_LOADERS`)
4. New `abbreviations.json` for domain abbreviations

Everything else reuses unchanged.
