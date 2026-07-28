# Ludyga Baseline — Failure Catalogue

Test set: 198 commands (100 invalid, 98 valid).  Total failures: 49 (false rejects: 49, false accepts: 0).

## Taxonomy

| Code | Meaning |
|---|---|
| `vocabulary_gap` | Synonym / paraphrase not in grammar token list |
| `type_mismatch` | Entity is the wrong semantic type for the action |
| `abbreviation_gap` | Abbreviated medical term not expanded by grammar |
| `syntax_gap` | Correct vocabulary, but word order / filler doesn't match grammar rule |

## False rejects — valid commands the grammar failed to parse

| ID | Category | Command | Failure type |
|---|---|---|---|
| c103 | valid_u01_u04 | Freeze frame | `vocabulary_gap` |
| c104 | valid_u01_u04 | Halt animation | `vocabulary_gap` |
| c105 | valid_u01_u04 | Stop now frame | `syntax_gap` |
| c109 | valid_u01_u04 | Continue frame, start now | `syntax_gap` |
| c112 | valid_u01_u04 | Activate display | `vocabulary_gap` |
| c113 | valid_u01_u04 | Display on | `vocabulary_gap` |
| c115 | valid_u01_u04 | turn the display on | `vocabulary_gap` |
| c117 | valid_u01_u04 | disable the screen | `vocabulary_gap` |
| c118 | valid_u01_u04 | turn it off | `syntax_gap` |
| c126 | valid_u09_u12 | I need help! Start emergency | `syntax_gap` |
| c127 | valid_u09_u12 | Emergency in the kitchen! Start emergency | `syntax_gap` |
| c128 | valid_u09_u12 | Code red, initiate immediately | `vocabulary_gap` |
| c129 | valid_u09_u12 | Begin emergency | `vocabulary_gap` |
| c130 | valid_u09_u12 | Trigger emergency situation | `vocabulary_gap` |
| c131 | valid_u09_u12 | Deactivate emergency mode | `vocabulary_gap` |
| c132 | valid_u09_u12 | Disable emergency mode | `vocabulary_gap` |
| c133 | valid_u09_u12 | Stop emergency | `syntax_gap` |
| c134 | valid_u09_u12 | Terminate the emergency | `vocabulary_gap` |
| c136 | valid_u09_u12 | leave the emergency mode now | `syntax_gap` |
| c138 | valid_u09_u12 | Cancel emergency mode | `vocabulary_gap` |
| c139 | valid_u09_u12 | Leave crisis mode | `vocabulary_gap` |
| c140 | valid_u09_u12 | accept catastrophe accident mode | `vocabulary_gap` |
| c142 | valid_u13_u14 | Display blood pressure | `vocabulary_gap` |
| c146 | valid_u13_u14 | Display diagnostic information | `vocabulary_gap` |
| c148 | valid_u13_u14 | Illustrate the insights | `vocabulary_gap` |
| c149 | valid_u13_u14 | Illustrate the patient's medical history | `vocabulary_gap` |
| c152 | valid_u13_u14 | Bring up the medication history of the past week | `vocabulary_gap` |
| c153 | valid_u13_u14 | Exhibit the surgical procedure details from the last three days | `vocabulary_gap` |
| c154 | valid_u13_u14 | Demonstrate the pathology reports for the last 24 hours | `vocabulary_gap` |
| c156 | valid_u13_u14 | Bring up the chemotherapy regimen recorded in the past six hours | `vocabulary_gap` |
| c158 | valid_u13_u14 | Reveal the immunization history of the last 10 days | `vocabulary_gap` |
| c160 | valid_u15 | Note: Upon examination, there was tenderness in the lower abdomen and a palpable mass | `syntax_gap` |
| c161 | valid_u15 | Record note: Patient's blood pressure was elevated at 160/90 mmHg during today's visit | `vocabulary_gap` |
| c162 | valid_u15 | Add note: Patient reports experiencing shortness of breath and chest pain upon exertion | `vocabulary_gap` |
| c163 | valid_u15 | Log note: Radiology report confirms the presence of a 3 cm mass in the left lung with possible metastasis, finish note | `vocabulary_gap` |
| c169 | valid_u16_u17 | Set medication regimen for patient John Doe: Begin with 10 milligrams of Lisinopril once daily, finish medication | `vocabulary_gap` |
| c170 | valid_u16_u17 | Set medication protocol: Commence treatment with 500 milligrams of Metformin twice a day, finish medication | `vocabulary_gap` |
| c171 | valid_u16_u17 | Start medication plan for Mrs. Smith: Begin with 20 milligrams of Atorvastatin every evening, finish medication | `vocabulary_gap` |
| c172 | valid_u16_u17 | Activate drug regimen for Mr. Johnson: Start with 50 micrograms of Levothyroxine in the morning | `vocabulary_gap` |
| c177 | valid_u16_u17 | Set medication albuterol inhaler 2 puffs every 4 hours as needed, finish medication | `syntax_gap` |
| c180 | valid_u18_u20 | Switch patient to Mary Johnson | `vocabulary_gap` |
| c181 | valid_u18_u20 | Update patient to Sarah Davis | `vocabulary_gap` |
| c183 | valid_u18_u20 | Switch patient to Mary Smith | `vocabulary_gap` |
| c187 | valid_u18_u20 | Display messages | `vocabulary_gap` |
| c190 | valid_u18_u20 | Start message: Remind Mr. Johnson to take his medication as prescribed. Finish and send it to Mr. Johnson | `vocabulary_gap` |
| c192 | valid_u18_u20 | Begin message: Advise Mr. Patel to schedule a follow-up consultation. Complete the message and send it to Mr. Patel | `vocabulary_gap` |
| c195 | valid_u18_u20 | Start message: Update Mr. Nguyen on his upcoming surgery details. Finish and send it to Mr. Nguyen | `vocabulary_gap` |
| c196 | valid_u18_u20 | Begin new message: Record Ms. Garcia's recent symptoms for review. Complete the message and send it to Ms. Garcia | `vocabulary_gap` |
| c197 | valid_u18_u20 | Start message: Inform Dr. Patel about the patient's discharge instructions. Finish and send it to Dr. Patel | `vocabulary_gap` |

## False accepts

None — all 100 invalid commands correctly rejected.


## Failure type breakdown (false rejects only)

| Failure type | Count | % of false rejects |
|---|---|---|
| `vocabulary_gap` | 40 | 82% |
| `syntax_gap` | 9 | 18% |

## Coverage gaps in the 198-command test set

The current test set has **zero** commands using medical abbreviations (e.g. `show BP`, `show SpO2`) and **zero** explicit type-mismatch commands (e.g. `diagnosis hemodialysis`). Both are in-scope failure modes for the new pipeline (Stage 4c abbreviation expansion, Stage 5 semantic validation) and require dedicated test cases — see `data/extended_test_cases.json`.
