# Hosting & scaling proposal (DRAFT, 2026-10-06)

Status: DRAFT by Claude, needs human review. Every number marked **[PLACEHOLDER]** is a planning
parameter, NOT a measurement. No prices are quoted: none were verified. Replace placeholders with
results of the load test in section 6 before any purchase.

## 1. Requirements (from owner, 2026-10-06)
- 50 concurrent sessions; each lives 24-48 h and stays running, student/doctor reconnects.
- 10 waves in sequence, 500 users total (hardware sized for 50, reused per wave; nothing scales to 500).
- All 50 can be active at the same moment (scheduled class).
- Session model (owner, 2026-10-06): one slot = one long-lived container/machine. One doctor uses it and their responses are stored; the next doctor logs in via a NEW link on the SAME machine/container and their responses are stored. 50 slots x 10 doctors in sequence = 500. Container is wiped/recreated between doctors (owner decision 2026-10-06); all of a doctor's actions are stored centrally in the database (see section 9).
- Viewer: Weasis, mostly 2D; MPR/3D only in some stages/cases (owner). Which ones: UNKNOWN.
- No GPU: CPU rendering only, measure first.
- On-prem Proxmox; refurbished enterprise servers with warranty acceptable; rack variant (not mini-PCs) preferred, final choice open.
- Wanted topology: access/routing server (weaker) + session server; HA if the price stays near the mini-PC option. One host may fail and sessions must restart quickly.
- Budget: owner will allocate what is needed but prefers lower-cost options; no figure given. Give a minimal option plus a minimal-overhead backup option.
- Locations (owner): one rack on site and two racks in the data center. The on-site rack holds only 2 x NVIDIA GB10 stacked on one shelf (placeholder, ~5-6U); the rest is empty. Depth, free U, power, UPS, switch ports/speed and which location hosts the new servers: UNKNOWN.
- Users reach the service over the public internet from their own PCs/Macs (no VPN/campus restriction). Uplink capacity UNKNOWN.
- Operations: owner's team administers Proxmox and the whole infrastructure and handles issues.
- Streaming layer undecided: Kasm paid tier vs Apache Guacamole. Size for both.

## 2. Project constraints that shape the design
- CLAUDE.md: data only on-prem; watermark mandatory (Student ID + timestamp per session); containers destroyed on logout, zero persistence. Reusing one container for 10 doctors in sequence conflicts with this rule and with per-user isolation: residual Weasis cache/history/clipboard/files of doctor N could be visible to doctor N+1 and the watermark identity must switch. Owner decision: wipe at every hand-over. Cleanest reading that keeps the hard rule unchanged: a 'slot' is a unit of CAPACITY, not a container. Each doctor gets a freshly created container from the clean image (Kasm already destroys a session container on end); nothing is reused. ADR still to be written (use template from #90).
- Internet exposure: sessions are reachable from the public internet, so TLS, rate limiting, ingress hardening and token handling (#63, #94, #103, #107) are prerequisites, not nice-to-haves.
- The 2 GB10 systems on site are only placeholders (owner), not session hosts. Treat the rack as effectively empty (minus ~5-6U).
- Kasm CE: 5 concurrent sessions, non-commercial (EULA 2.2). 50 sessions needs a paid tier + legal/procurement, or Guacamole (PoC only, not hardened; see README).
- Candidate rack Lanberg FF01-8837-23BL (37U): 800x800 mm outer, **600 mm mounting depth**. Server chassis depth must be checked against the actual rack (many 2U/4U servers are 700+ mm). The product link supplied is the 47U model; text said 37U: confirm. Shelf AK-1005-B is 500 mm deep: fits mini-PCs/desktops, not rack servers.
- Always-on sessions reserve RAM 24/7: RAM is the primary sizing axis, not CPU.
- Rate limits, resource limits, healthchecks (#68, #69), backups (#85) must exist before production.

## 3. Roles (what runs where)
- **Access/edge node** (weaker): reverse proxy/TLS, Kasm manager+proxy+DB or Guacamole+Postgres, grading-api (SQLite, single writer, WAL), Orthanc + DICOM storage, backup target agent, monitoring. Moderate RAM, fast disk, mirrored.
- **Session hosts**: Kasm agents or Guacamole session containers. Only these scale with user count.

## 4. Sizing model (replace placeholders by measurements)
Per-session cost: `C` vCPU and `R` GB RAM, plus stream bandwidth `B` Mbit/s.
Totals for N=50: vCPU = 50*C, RAM = 50*R (no RAM overcommit: sessions are long-lived), bandwidth = 50*B.

| Profile | C (vCPU) | R (GB) | 50 sessions: vCPU | 50 sessions: RAM | Basis |
|---|---|---|---|---|---|
| Light 2D | 2 | 3 | 100 | 150 | **[PLACEHOLDER]** |
| MPR | 4 | 6 | 200 | 300 | **[PLACEHOLDER]** |
| 3D volume, CPU-rendered | 8 | 12 | 400 | 600 | **[PLACEHOLDER]** |

Mixed load (owner: mostly 2D, 3D/MPR only in some stages): total = 50 x (f2d*Light + fMPR*MPR + f3d*3D) with f2d+fMPR+f3d = 1. The fractions are UNKNOWN until the radiologist defines the stages; size for the worst realistic stage mix, not the average, because all 50 can be in the heavy stage at once.

HA rule (one host may fail): provision N+1, i.e. surviving hosts must carry all 50 sessions. With H session hosts the
usable capacity is (H-1)/H of installed, so each host must hold >= total/(H-1).
Example logic only: if total RAM = T, then H=2 needs T per host (no real saving vs one big host + cold spare),
H=3 needs T/2 per host, H=4 needs T/3 per host. More, smaller hosts make HA cheaper but cost rack U and ports.
CPU cannot be overcommitted much if all 50 are active simultaneously with 3D rendering.

## 5. Candidate topologies (no prices; prices need budget + depth + sourcing)
- **A. Minimal (2 servers, no HA):** access node + one big session host. Backup = cold restore from #85 backups; failure = sessions lost until restore (hours). Cheapest, does NOT meet "one host may fail".
- **B. Minimal HA (3 servers):** 1 access node + 2 session hosts, each able to run all 50 (RAM T each), VMs/containers restart on the survivor. Proxmox cluster needs a quorum device (QDevice) with 2+1 nodes. Sessions are lost and re-launched on failover (progress persists in grading-api); live "continue without drop" is NOT achievable for long-lived desktop sessions.
- **C. N+1 (4-5 servers):** access node + 3-4 session hosts at T/2..T/3 each. Better price/performance per host, more ports/U/power.
- **D. Mini-PC fleet (reference):** many small nodes, easiest to buy, usually no ECC/remote management, rack-mount/cabling/PSU overhead; used as price baseline only.
HA storage: shared Ceph needs 3+ nodes and 10 GbE; otherwise use local NVMe + replication (ZFS) + backups. Decision pending real RTO.

## 6. Measurement plan (first deliverable, before buying)
1. Build the Weasis workspace image and run 1, 5, 10, 20 parallel sessions on a test box with a representative CT (real size from radiologist; today only public samples).
2. For each profile (2D, MPR, 3D) record: RSS per session, CPU cores busy at peak and idle, stream bandwidth, time to open study, frame latency. Record in docs/NFR.md (#89).
3. Run the same with both streaming layers (Kasm agent, Guacamole) as they differ in overhead.
4. Extrapolate to 50, add 25-30% headroom [policy to confirm], derive per-host specs for topologies A-C.
5. Decide GPU question again with measured 3D numbers (owner said CPU only, measure first).

## 7. Open questions (blocking sizing/purchase)
ANSWERED: budget (no cap, lower-cost preferred), 3D/MPR scope (only some, mostly 2D), access (internet), administration (owner's team), session model (see section 1).
STILL OPEN:
1. Which rack hosts the new servers (on-site vs data center) and total U of that rack: usable depth, free U, power kW/circuits, UPS, switch ports and speed, cooling.
2. Typical CT size (slices, MB/study) and total Orthanc storage for 500 studies: ask the radiologist.
3. Which stages/cases need MPR/3D (drives the share of heavy sessions).
4. Uplink capacity at the hosting location for 50 simultaneous streams.
5. Acceptable failover: restart in minutes vs hours (RTO/RPO, ties to #85, #89).
6. ANSWERED: wipe/recreate at hand-over; data stored centrally (section 9).
7. Which of Kasm paid / Guacamole is the target and when it is decided.
8. Sourcing of refurbished hardware (vendor, warranty, spare parts) and spare-parts policy.

## 8. Next steps
- Owner answers section 7; load test (section 6); then price-able spec per topology with market research.
- Related issues: #68, #69, #85, #89 (NFR/capacity), #105, #107, NEW-D #115, NEW-T #113.

## 9. Data architecture for 500 doctors (owner question, 2026-10-06)
Answer: ALL responses of ALL 500 doctors go into ONE central database on the access node, never per VM/container/slot.
- Containers are disposable and hold no results; they only call grading-api with a per-doctor token. Wiping a container loses nothing. This is already how the code works (grading-api + its own volume, separate from the Kasm containers).
- Per-slot or per-VM storage is rejected: data would be destroyed with the wipe, could not be analysed together (research publication), and would multiply backup/restore work (#85).
- Volume is small: with the planned 130 cases per user (50/50/30, not built yet) = about 65 000 submission rows for 500 doctors, plus audit rows. Size is not the constraint; integrity, concurrency and backup are.
- Engine: SQLite (single writer, WAL) is acceptable for the 50-user pilot (db.py comment, #70, #101). For 50 concurrent doctors on a production cohort with audit log, versioned ground truth (#96) and backups, plan a move to PostgreSQL (CLAUDE.md already names Postgres for the stratified design). Decision for an ADR; do not migrate before #96/#98/#85.
- Needed schema additions for the hand-over model (proposals, not implemented): slot_id and host/container id on the session record (forensics: who sat on which slot when); doctor identity separate from slot; token bound to doctor and revoked at hand-over (today GRADING_TOKEN_TTL_SECONDS default is 8 h, window is 24-48 h: TTL must be set deliberately); audit log of session start/end/wipe (#98); a verified-clean check before a slot is handed to the next doctor.
- Watermark: Student ID + timestamp must come from the new doctor's session, minted per link (create-session.py), so no stale identity survives the wipe.
- Residual-data test (acceptance): after hand-over, no file, Weasis cache, history, clipboard or browser profile from the previous doctor is present in the new container.
