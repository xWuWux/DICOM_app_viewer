# Data retention and deletion: outline

## Retention today (technical facts)
See [data-inventory.md](data-inventory.md) for every store. In short: session containers and grading
tokens are short-lived by design; **answers, progress, cases, Orthanc studies, database backups and
result exports have no expiry**, and nothing deletes them automatically. Kasm keeps anonymous users
and session tokens without an expiry date (#251 review); Guacamole keeps connection history (with
client IPs) indefinitely.

## Retention policy (TO DECIDE)
For each category, the owner and DPO decide a period and what happens at its end (delete or
anonymise):
| Category | Proposed trigger | Period |
|---|---|---|
| Answers and progress | end of course / publication of results | **TO DECIDE** |
| Mapping participant id to person (held outside the system) | end of course | **TO DECIDE** |
| Database backups and exports | same as the data they contain | **TO DECIDE** |
| Web and Guacamole connection logs (IP addresses) | security need | **TO DECIDE** |
| Kasm anonymous users and session tokens | end of each session | technical fix pending (#251, #159) |
| Anonymised studies (Orthanc) | end of the course / research project | **TO DECIDE**; agreement with the data provider (#87) |

## Deletion that exists today
- `POST /reset` deletes one participant's progress and answers (off by default, `GRADING_RESET_ENABLED`,
  #98); it is a "start over" feature, not an erasure tool, and it does not touch backups or exports.
- Session teardown: `scripts/end-kasm-session.py` (Kasm, #251) and
  `scripts/teardown-guacamole-session.py` (Guacamole) remove the session container and revoke the
  grading token.
- Expired grading tokens are deleted when a new token is minted.

## Erasure on request
No tool exists. A request today means manual work on: grading database (`sessions`, `progress`,
`submissions` by `student_id`), every backup and export containing them, Guacamole user and history
rows, and the external id-to-person mapping. **To build** (proposal): one operator script that erases a
`student_id` from the live database and reports which backups/exports still contain it, plus a
documented procedure for backups. Note: if results were published in aggregated form, erasure applies
to the identifiable records, not to the publication (to confirm with legal).
