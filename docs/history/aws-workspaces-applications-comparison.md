# Comparison record -- app state vs Amazon WorkSpaces Applications (2026-10-14)

Scope note: this is a **features-only comparison** between this project's
streaming/grading stack and Amazon WorkSpaces Applications (the service
formerly named Amazon AppStream 2.0), with a focus on the On-Demand fleet
model. It borrows *feature ideas* to implement self-hosted and adopts **no AWS
dependency of any kind** -- CLAUDE.md's stop conditions keep every cloud
service (AppStream, S3, anything else) out of the architecture, and nothing
below proposes adding one. Sources: the current AWS developer-guide pages for
the rebranded service (features overview, fleet types, fleet auto scaling,
Home Folders), fetched 2026-10-14, plus five corrections from the p2 co-agent
review that carry owner/PO decisions of 2026-10-14 and measured facts
recorded earlier in this repo.

---

## Where this stack already matches or exceeds the service

| Capability | WorkSpaces Applications | This project |
| --- | --- | --- |
| Pixels-only to the client | encrypted stream to HTML5 browser or native client | Kasm / Guacamole HTML5 stream (CLAUDE.md hard rule) |
| Disposable sessions | non-persistent by design | stricter: containers destroyed on logout, zero data persistence (hard rule) |
| Clipboard / DLP policy | selectable clipboard direction, file-transfer toggle, print off | stricter: full copy-lockdown suite (`scripts/test-copy-lockdown.sh`), Weasis native export/import/send/Q-R disabled, KasmVNC rich-clipboard DLP |
| Idle / disconnect reaping | idle and disconnect timeouts per stack | Guacamole reaper (#181) + shared teardown library; Kasm group timers in the admin console |
| Per-user isolation | one streaming instance per user (non-multi-session fleets) | one container per doctor |
| Any-device client | HTML5 browser, tablets, native client | same, through Kasm or the Guacamole PoC |
| Forensic watermark | **no equivalent feature** | mandatory Student ID + Timestamp overlay on every streamed session (hard rule) |

## Resume across pauses -- "we already have it", not a gap

The service's pitch of keeping pre-disconnect state (roaming app settings,
Home Folders) is **functionally covered here server-side**, and framing it as
a gap would be wrong. Owner/PO decision of 2026-10-14: the course runs up to
a month with pauses; doctors close the PC and resume where they stopped. The
mechanism is grading-api's `progress` table keyed by `student_id` -- the
containers stay intentionally stateless, which is exactly the CLAUDE.md
zero-persistence rule.

Measured facts behind the reaping timers (not assumptions): the Kasm group
settings were recorded in issue #122 as `keepalive_expiration=3600 s` with
action delete, `keepalive_interval=300 s`, `session_time_limit=0`; the
Guacamole reaper defaults to idle 180 min and max age 48 h
(`scripts/reap-guacamole-sessions.py`). Every login is a fresh container plus
server-side progress, so these values remain valid for a month-long course;
the open follow-ups on our side are re-entry for a returning doctor (token
TTL defaults to 8 h with a hard maximum of 7 days in
`docker/grading-api/app/config.py`, which a month-long course outlives),
time-on-task semantics across pauses, and data retention (#85, #87, #98).

App-navigation parity, current as of 2026-10-14: the exam timer is merged
(#232, API 1.3.0, server clock), and study browsing in the learning and
assessment stages sits on branch `feat/study-browser` (API 1.4.0, not merged
at the time of writing). Relevant whenever "application launch and
navigation" features are compared.

## Borrow candidates (self-hosted implementations only)

1. **Session history / admin overview.** Their console shows per-user session
   start, end, duration and resource use. We have structured logging, the
   reaper/teardown audit lines and `scripts/export-results.py`, but no
   session-level admin view. A join over grading-api session rows and the
   teardown/audit log ("who streamed when, how long, how it ended") is cheap
   and pairs naturally with the forensic watermark for proctoring.
2. **Admission control / honest queue at capacity.** Fleet auto scaling
   manages desired-session capacity and users wait when it is exhausted. Our
   binding constraint is the Kasm Community Edition five-concurrent-session
   cap, which is a **licensing limit (EULA section 2.2), not a technical**
   one. The borrowable part is the UX: answer "all seats busy, try again" at
   mint time instead of surfacing a raw Kasm API error. `docs/DEGRADED_MODES.md`
   already documents what saturates first; a dedicated queue/waitlist answer
   does not exist yet.
3. **Metrics and alarms (CloudWatch analogue).** Prometheus + cAdvisor +
   Grafana on the Proxmox hosts, to keep the "what saturates first at 50/300
   users" claims of `docs/DEGRADED_MODES.md` and `docs/HOSTING_SIZING.md`
   verified by live curves and alerts instead of one-off measurements.
4. **Post-session hook.** The service runs pre/post session scripts. We have
   launch-side hooks (`custom_startup.sh`, the provisioners) but the end side
   is plain teardown; a formal post-session event (audit line, timer summary,
   wipe verification) would pin the ephemerality guarantee with something to
   alert on.

## Not applicable or do-not-borrow

* Billing models (stopped-instance fees, per-second Elastic fleets):
  meaningless on owned hardware, and a "stopped but kept" container state
  would violate destroy-on-logout. Provisioning per login is already the
  functional equivalent of an on-demand fleet.
* Multi-session instances and GPU instance types (Weasis runs fine on the
  CPU instances we have), global regions and edge latency (on-premise by
  design; HA is the `docs/PROXMOX_DEPLOYMENT.md` path), S3-backed Home
  Folders (PHI rule), AD/SAML federation (the planned identity seam is Moodle
  LTI 1.3, not an AWS directory integration).
* Per-student Weasis settings/roaming store: **N/A by policy**. It is
  persistence inside the session layer and conflicts with the zero-persistence
  hard rule; the server-side `progress` table is the sanctioned mechanism for
  everything that must survive a session.
* The rebranded service's new AI-agent access (MCP tools driving the streamed
  desktop application): kept as not-applicable -- agents acting inside a
  medical exam stream is an exam-integrity and PHI posture conflict, whatever
  its merits elsewhere.

---

Prepared by the p1 co-agent from the fetched AWS pages listed above;
corrected per the p2 review of 2026-10-14 (server-side resume framing,
settings-store demotion, measured timer facts from #122 and the reaper
defaults, the #232 / `feat/study-browser` currency note, and the licensing
framing of the Kasm CE cap). No repo behaviour depends on this file; it is a
record, not a contract.
