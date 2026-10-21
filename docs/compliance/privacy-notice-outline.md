# Privacy notice for doctors: outline

Outline of the information a doctor must receive before using the system (GDPR art. 13). Facts come
from [data-inventory.md](data-inventory.md); every **TO DECIDE** item needs the owner, legal and the
DPO. The final notice is expected in Polish; this outline is English to match the repository.

## 1. Controller and contact
- Controller: **TO DECIDE** (presumably Gdanski Uniwersytet Medyczny; confirm).
- DPO contact: **TO DECIDE.**
- Project contact for questions about the course: **TO DECIDE.**

## 2. What we process
- A pseudonymous participant id assigned by the coordinator; the link between that id and the
  doctor's identity is held by **TO DECIDE (who, where)**.
- Course and exam data: answers (Lung-RADS category, modifier S, free-text impression in the learning
  stage), correctness, time spent per case, stage reached, approval of the end-of-exam review.
- Technical data: client IP address in the web server and Guacamole connection logs; session times.
- The watermark shows the participant id and the time on screen during every session (forensic
  deterrence, mandatory by project rule).
- No patient-identifying data is shown: studies are anonymised before loading.

## 3. Purposes
- Running the course and exam, scoring and feedback.
- Exam integrity (watermark, session logs).
- Research and publication of aggregated results: **TO DECIDE** whether results are published,
  in what form (aggregated, pseudonymised), and under which ethics approval (UNKNOWN).

## Legal basis (TO DECIDE)
Options for the DPO to choose from, per purpose; not a recommendation:
- performance of a task in the public interest / education by a public university (art. 6(1)(e));
- consent (art. 6(1)(a)), e.g. for the research use of results; consent must be as easy to withdraw
  as to give, which affects retention of already-collected results;
- contract (art. 6(1)(b)), if participation is under a course agreement;
- legitimate interest (art. 6(1)(f)) for security logs (IP addresses).
The anonymised study data is intended to fall outside GDPR (recital 26) only if anonymisation is
effective: depends on the radiologist's statement (#162) and the load-time gate (#253).

## 4. Recipients
No external recipients in the data path today; see
[processors-and-disclosures.md](processors-and-disclosures.md). Within GUMed: **TO DECIDE** who sees
named results (course coordinator, radiologist, research team).

## 5. Retention
See [retention-and-deletion.md](retention-and-deletion.md). Today answers and progress have **no
expiry**; a policy is **TO DECIDE.**

## 6. Rights
Access, rectification, erasure, restriction, objection, data portability where applicable, complaint
to the President of the Personal Data Protection Office (UODO). How a request is handled
technically: [retention-and-deletion.md](retention-and-deletion.md#erasure-on-request).

## 7. Automated decisions
Scoring is automatic (category compared with the reference answer). **TO DECIDE** whether a score has
any effect on the doctor beyond feedback (e.g. a course grade); if it does, art. 22 needs review.
