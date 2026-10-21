# Personal data breach procedure: first version (outline)

For incidents involving participant data or study data. Organisational parts (who decides, who
notifies) are **TO DECIDE** with the DPO and must match GUMed's own incident procedure, which takes
precedence where it exists (UNKNOWN whether one exists; to check).

## Contacts (TO DECIDE)
| Role | Name / contact |
|---|---|
| Incident owner (technical) | TO DECIDE |
| Data protection officer (DPO) | TO DECIDE |
| Controller's decision maker | TO DECIDE |
| Campus network / infrastructure team | TO DECIDE |

## Steps
1. **Detect and report.** Anyone who suspects a breach (leaked link, screenshot of a study or answer
   key found outside, lost export file, unexpected access in logs) reports it to the incident owner
   immediately. Write down the time of discovery: legal deadlines run from it.
2. **Contain** (technical, as applicable):
   - end the affected sessions: `scripts/end-kasm-session.py`, `scripts/teardown-guacamole-session.py`;
   - revoke grading tokens: `POST /api/session/revoke` with the coordinator key;
   - rotate secrets that may be exposed: `GRADING_COORDINATOR_KEY`, `ORTHANC_PASSWORD`, the Kasm API
     key, the Guacamole admin password;
   - stop the affected service if needed (`docker compose stop <service>`, never `down -v`);
   - preserve evidence: copy container logs and the Guacamole connection history before rotation.
3. **Assess** with the DPO: what data, how many people, whether patients could be re-identified,
   likelihood and severity of risk to the people concerned. The watermark on a leaked image
   identifies the session it came from.
4. **Notify**, as decided by the controller with the DPO:
   - supervisory authority (President of UODO) without undue delay and, where feasible, within 72
     hours of becoming aware, unless the breach is unlikely to result in a risk (GDPR art. 33);
   - the affected people without undue delay if the risk is high (GDPR art. 34);
   - the data provider of the studies if study data is involved (per the agreement, #87).
5. **Record** every breach, including those not notified: facts, effects, actions taken (GDPR
   art. 33(5)). Where: **TO DECIDE** (a private register, not this public repository).
6. **Review** after closing: root cause, fix, issue in this repository without personal data.

## Not in this repository
Incident records themselves contain personal data and must not be filed as GitHub issues.
