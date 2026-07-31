# Ontology Sources — Stage 4 Semantic Lookup

*Master Thesis: Improving Intent Recognition in Grammar-Based Voice Interfaces*
*Anushka Joshi — TU Dresden, Chair of Software Technology*

---

## Why OBO and Not OWL

OBO (Open Biomedical Ontologies) and OWL (Web Ontology Language) are two formats for distributing the same knowledge. The key distinction is:

| | OBO | OWL |
|---|---|---|
| Format | Plain text, human-readable | XML/RDF |
| Parser | Standard Python string parsing | Requires `owlready2` + Java reasoner |
| Semantic reasoning | No — lookup only | Yes — can infer new relationships |
| File size (DOID) | ~55 MB | ~90 MB |
| Use case | Term lookup, synonym matching | Formal inference, consistency checking |

**We do not need OWL because we do not reason — we look up.**

Stage 4 asks exactly one question: *"Is this entity term found in the ontology, and if so, what semantic type is it?"* This is a dictionary lookup, not a logical inference. The OBO files are the **authority** (they were built by domain experts who classified hemolysis as a disease, hemodialysis as a procedure). Our code is the **lookup engine**. No reasoner is needed.

OWL reasoning would be needed if we wanted to ask questions like: *"All subclasses of renal failure that are also genetic disorders"* — we do not need this.

---

## Ontology Files Used

### 1. DOID — Human Disease Ontology
- **File:** `ontologies/doid.obo`
- **Source:** OBO Foundry / disease-ontology.org
- **License:** CC0 1.0 (public domain)
- **Provides semantic types:** `disease`, `condition`, `finding`
- **Why chosen:**
  - Open OBO format — no registration or license required
  - Covers the full spectrum of human diseases with rich synonym sets
  - Directly relevant to the central thesis use case: `diagnosis hemolysis` (DOID:3680) vs `diagnosis hemodialysis` (procedure — not in DOID)
  - Standard ontology used in biomedical NLP research

---

### 2. HPO — Human Phenotype Ontology
- **File:** `ontologies/hp.obo`
- **Source:** hpo.jax.org / OBO Foundry
- **License:** Free for academic and research use
- **Provides semantic types:** `symptom`, `clinical_finding`
- **Why chosen:**
  - Complements DOID: DOID covers disease entities, HPO covers observable symptoms
  - Clinicians often speak in symptom terms ("shortness of breath", "chest pain")
  - Same OBO format as DOID — same parser, no extra implementation cost

---

### 3. RxNorm Prescribable Content
- **File:** `ontologies/RXNCONSO.RRF`
- **Source:** U.S. National Library of Medicine (NLM)
- **License:** Public domain — no license required for Prescribable Content subset
- **Provides semantic types:** `medication`
- **Why chosen:**
  - Gold standard for drug name normalisation in clinical informatics
  - Prescribable Content contains all currently prescribable drugs — exactly the scope needed
  - Contains brand names, generic names, and synonyms under a single RxCUI identifier
  - Downloaded locally — avoids API latency unacceptable for real-time AR glasses
  - No UMLS license required (unlike Full Monthly Release)

---

### 4. NCI Thesaurus — Procedures
- **File:** `ontologies/Thesaurus.txt` (from `Thesaurus.FLAT.zip`)
- **Source:** NCI Enterprise Vocabulary Services (EVS) — evs.nci.nih.gov
- **License:** NCI Thesaurus Terms of Use — free for research and educational use
- **Format:** Tab-delimited flat file (9 columns: code, IRI, parents, synonyms, definition, display_name, concept_status, semantic_type, concept_in_subset)
- **Provides semantic types:** `treatment`, `procedure`
- **Why chosen:**
  - NCIt is the NCI's reference terminology covering 120,000+ biomedical concepts
  - Includes full procedure/intervention hierarchy with concept codes and synonyms
  - Free download, no registration required
  - We filter at load time to rows where `semantic_type` = "Therapeutic or Preventive Procedure" or "Intervention or Procedure" — extracting only the ~5,000 procedure concepts we need
  - Provides citable concept IDs (NCIt codes) for procedures — replaces the need for SNOMED CT
- **Note:** Only the FLAT format is used (not OWL) — see "Why OBO and Not OWL" above

---

## Curated Vocabulary Files

These are not ontologies. They are **controlled vocabularies** — flat lookup tables created for this project covering semantic types where no suitable free ontology exists.

### 5. `vital_signs.json`
- **Provides semantic types:** `vital_sign`, `lab_result`
- **Entries:** 44 terms (12 vital signs, 32 lab results)
- **Structure:** `{label, synonyms[], semantic_type}`
- **Why curated:** NCIt was investigated but does not provide a usable `vital_sign` semantic type — blood pressure is classified as `Finding`, not as a vital sign measurement. LOINC covers this domain but requires registration (rejected). A curated vocabulary of 44 entries covers all vital sign and lab result entities in the evaluation set.
- **Example resolutions:**
  - `"show blood pressure"` → entity=`"blood pressure"` → exact match → `semantic_type=vital_sign` → `show` allows `vital_sign` → **ACCEPTED**
  - `"show BP"` → Stage 1 expands BP → `"blood pressure"` → exact match → `semantic_type=vital_sign` → **ACCEPTED**
  - `"show INR"` → Stage 1 expands INR → `"international normalised ratio"` → exact match → `semantic_type=lab_result` → `show` allows `lab_result` → **ACCEPTED**
  - `"show CBC"` → Stage 1 expands CBC → `"complete blood count"` → exact match → `semantic_type=lab_result` → **ACCEPTED**

### 6. `patient_info_terms.json`
- **Provides semantic types:** `patient_info`, `document`, `messaging`
- **Entries:** 13 terms
- **Structure:** `{label, synonyms[], semantic_type}`
- **Why curated:**
  - No standard ontology covers administrative/record-level terms at this scope
  - "Discharge summary", "medication list", "progress notes" are system-level document categories, not clinical concepts owned by any standards body
  - Scope is small and well-defined — 13 entries cover all record types present in the evaluation command set
- **Example resolutions:**
  - `"show patient history"` → entity=`"patient history"` → synonym match → `"Medical History"` → `semantic_type=patient_info` → `show` allows `patient_info` → **ACCEPTED**
  - `"show discharge summary"` → exact match → `semantic_type=document` → `show` allows `document` → **ACCEPTED**
  - `"show messages"` → exact match → `semantic_type=messaging` → `show` allows `messaging` → **ACCEPTED**
  - `"show movie"` → no match in any ontology → **ENTITY_NOT_FOUND** (Stage 4 rejects non-medical entities)

---

## Ontologies Considered and Rejected

### SNOMED CT
- **Rejected — licensing constraint**
- Full SNOMED CT requires a license from SNOMED International or a National Release Centre (BfArM in Germany), requiring institutional membership even for research use.
- RF2 format (Release Format 2) involves multiple interlinked tables, complex to parse.
- **What we use instead:** NCI Thesaurus FLAT file — free, citable concept IDs, same procedure coverage.

### OPS — Operationen- und Prozedurenschlüssel
- **Rejected — language mismatch**
- OPS labels are in German ("Hämodialyse", "Peritonealdialyse"). The pipeline's LLM outputs entities in English. A translation layer would add complexity and error risk.
- **What we use instead:** NCIt (English labels).

### ICD-10-GM
- **Rejected — wrong purpose**
- ICD-10-GM is a billing/documentation classification. Labels are verbose ("Essential (primary) hypertension") rather than natural speech terms.
- DOID covers the same semantic space with NLP-oriented labels and richer synonyms.

### LOINC
- **Rejected — access friction + scope mismatch**
- Download requires mandatory user registration at loinc.org — not freely downloadable without an account, making automated or reproducible pipeline setup impractical.
- 95,000+ observation codes — far larger than needed for this use case.
- LOINC names are observation-centric and verbose ("2160-0: Creatinine [Mass/volume] in Serum or Plasma") — not matchable against short speech entities like "creatinine" or "INR".
- **What we use instead:** Curated `vital_signs.json` — 44 entries covering all vital sign and lab result entities in the evaluation set. NCIt was also investigated but does not provide a usable vital_sign semantic type (blood pressure appears as "Finding" in NCIt, not as a vital sign measurement).

### Full RxNorm Monthly Release
- **Not used — license and size**
- Requires UMLS license. ~500MB vs ~50MB for Prescribable Content.
- Prescribable Content covers all currently prescribable drugs — full release adds retired and non-prescribable substances not relevant to this use case.

---

## Semantic Type Mapping

| `allowed_slot_types` in domain_config | Ontology source |
|---|---|
| `disease`, `condition`, `finding` | DOID (`doid.obo`) |
| `symptom` | HPO (`hp.obo`) |
| `medication` | RxNorm Prescribable (`RXNCONSO.RRF`) |
| `treatment`, `procedure` | NCI Thesaurus (`Thesaurus.txt`) |
| `vital_sign`, `lab_result` | Curated `vital_signs.json` |
| `patient_info`, `document`, `imaging` | Curated `patient_info_terms.json` |
| `messaging` | Curated `patient_info_terms.json` |
| `free_text` | No lookup — raw text stored as-is |
| `patient_identifier` | No ontology — entity is a proper name |
| `system` | No ontology — entity is optional/empty |
