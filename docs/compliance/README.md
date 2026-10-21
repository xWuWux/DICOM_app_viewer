# Legal and compliance outline

Status: **draft outline, not legal advice.** Written from what the code on `master` (540354a,
2026-10-21) actually does. Every legal or organisational choice is marked **TO DECIDE** and needs the
owner, GUMed's legal office and the data protection officer (DPO; Polish: IOD) before any text here
is shown to users or treated as policy. Nothing in this folder changes system behaviour.

Facts the whole folder rests on: [data-inventory.md](data-inventory.md).

| # | Topic | Status | Where |
|---|---|---|---|
| 1 | Privacy policy (information for doctors) | outline | [privacy-notice-outline.md](privacy-notice-outline.md) |
| 2 | Terms and conditions | outline | [terms-of-use-outline.md](terms-of-use-outline.md) |
| 3 | GDPR compliance | outline: inventory of processing + open decisions | [data-inventory.md](data-inventory.md), this table |
| 4 | Cookie consent | outline: only strictly necessary browser storage found | [cookies-and-browser-storage.md](cookies-and-browser-storage.md) |
| 5 | Legal basis | TO DECIDE (options listed) | [privacy-notice-outline.md](privacy-notice-outline.md#legal-basis-to-decide) |
| 6 | Marketing consent | not applicable today (no marketing, no newsletter, no contact collection) | this table |
| 7 | Data deletion | outline: what can be deleted today and what cannot | [retention-and-deletion.md](retention-and-deletion.md) |
| 8 | Data processing agreement | outline: no external processor found in the data path; to confirm | [processors-and-disclosures.md](processors-and-disclosures.md) |
| 9 | Data retention | outline: current technical lifetimes; policy TO DECIDE | [retention-and-deletion.md](retention-and-deletion.md) |
| 10 | Third-party disclosure | outline | [processors-and-disclosures.md](processors-and-disclosures.md) |
| 11 | Data security | outline: measures in place and known gaps | [security-measures.md](security-measures.md) |
| 12 | Data breach procedure | outline: first version of the procedure, contacts TO DECIDE | [breach-procedure.md](breach-procedure.md) |
| 13 | Intellectual property | outline, mostly TO DECIDE | [ip-and-licenses.md](ip-and-licenses.md) |
| 14 | Open source licenses | outline; inventory work tracked in #73 | [ip-and-licenses.md](ip-and-licenses.md) |
| 15 | Payment and subscription | not applicable today (no payments, no subscriptions) | this table |

**6. Marketing consent.** The system sends no messages, collects no e-mail addresses or contact
details, and has no newsletter or analytics. Becomes relevant only if any of that is added.

**15. Payment and subscription.** Users pay nothing and nothing is sold. The only commercial question
is on the operator side: Kasm Workspaces Community Edition is licensed for non-commercial use and 5
concurrent sessions (EULA section 2.2, CLAUDE.md); a paid Kasm tier would need procurement and legal
review (#117, #122). That is a licence/procurement matter, not a user-facing payment.

## Related issues
#87 (data classification, retention, anonymisation), #98 (deletion without archive), #85 (backups),
#107 (encryption at rest, TLS), #73 (licence scanning), #108 (`LICENSE` deferred), #106 (`SECURITY.md`),
#162 (anonymisation statement for the radiologist's data), #117 (vendor exit plan).
