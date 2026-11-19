# E3 entity resolution and type check (Stages 4-5)

| group | n | status acc % | full acc % | incorrect acceptance | incorrect rejection | wrong concept | wrong status | coverage % (expected accepted) |
|---|---|---|---|---|---|---|---|---|
| overall | 98 | 92.9 | 86.7 | 1 | 5 | 6 | 1 | 93.3 |
| category: collision | 7 | 57.1 | 0.0 | 1 | 1 | 4 | 1 | 80.0 |
| category: exact | 39 | 100.0 | 97.4 | 0 | 0 | 1 | 0 | 100.0 |
| category: misspelling | 14 | 100.0 | 100.0 | 0 | 0 | 0 | 0 | 100.0 |
| category: not_in_ontology | 9 | 100.0 | 100.0 | 0 | 0 | 0 | 0 | None |
| category: synonym | 11 | 100.0 | 100.0 | 0 | 0 | 0 | 0 | 100.0 |
| category: unseen_synonym | 6 | 33.3 | 16.7 | 0 | 4 | 1 | 0 | 33.3 |
| category: wrong_type | 12 | 100.0 | 100.0 | 0 | 0 | 0 | 0 | None |
| action: diagnosis | 34 | 88.2 | 85.3 | 0 | 3 | 1 | 1 | 87.5 |
| action: set_medication | 18 | 88.9 | 88.9 | 0 | 2 | 0 | 0 | 86.7 |
| action: show | 16 | 100.0 | 93.8 | 0 | 0 | 1 | 0 | 100.0 |
| action: start | 22 | 95.5 | 81.8 | 1 | 0 | 3 | 0 | 100.0 |
| action: stop | 8 | 100.0 | 87.5 | 0 | 0 | 1 | 0 | 100.0 |

## Failed rows

| id | category | action | entity | expected | got | error |
|---|---|---|---|---|---|---|
| e3_021 | unseen_synonym | diagnosis | stomach ache | accepted symptom HP:0002027 | entity_not_found None  (None, none) | incorrect_rejection |
| e3_022 | unseen_synonym | diagnosis | belly pain | accepted symptom HP:0002027 | entity_not_found None  (None, none) | incorrect_rejection |
| e3_023 | unseen_synonym | diagnosis | covid | accepted disease DOID:0080600 | entity_not_found None  (None, none) | incorrect_rejection |
| e3_027 | collision | diagnosis | cold | accepted disease DOID:10459 | accepted disease DOID:3083 (chronic obstructive pulmonary disease, synonym) | wrong_concept |
| e3_028 | collision | diagnosis | hemolysis | entity_not_found | type_mismatch procedure  (None, none) | wrong_status |
| e3_040 | exact | start | oxygen therapy | accepted procedure NCIt:C94624 | accepted procedure NCIt:C29545 (Warburg Therapy, synonym) | wrong_concept |
| e3_051 | collision | start | heparin | accepted medication RXCUI:5224 | accepted procedure NCIt:C165967 (Heparin Measurement, synonym) | wrong_concept |
| e3_052 | collision | start | acetaminophen | accepted medication RXCUI:161 | accepted procedure NCIt:C135398 (Acetaminophen Measurement, synonym) | wrong_concept |
| e3_053 | collision | start | hemolysis | type_mismatch condition | accepted procedure NCIt:C111208 (Hemolytic Index, synonym) | incorrect_acceptance |
| e3_061 | collision | stop | heparin | accepted medication RXCUI:5224 | accepted procedure NCIt:C165967 (Heparin Measurement, synonym) | wrong_concept |
| e3_074 | unseen_synonym | set_medication | albuterol inhaler 2 puffs | accepted medication RXCUI:435 | entity_not_found None  (None, none) | incorrect_rejection |
| e3_075 | collision | set_medication | paracetamol 500 milligrams | accepted medication RXCUI:161 | entity_not_found None  (None, none) | incorrect_rejection |
| e3_098 | unseen_synonym | show | pulse | accepted vital_sign | accepted ehr_record  (pulse, passthrough_fallback) | wrong_concept |
