# Architecture — current state

**This describes what's actually built and running today**, not the
target/future system. For what's deliberately deferred (Moodle, payments,
real stratified sampling, certificates, admin dashboard), see README.md's
"What's NOT in this MVP" section and CLAUDE.md's hard rules — not
duplicated here to avoid the two documents drifting apart again.

## Session flow

```mermaid
sequenceDiagram
    autonumber
    actor Coordinator as Coordinator (You)
    actor Student as Radiologist / Tester
    participant CreateSession as scripts/create-session.py
    participant KasmAPI as Kasm Public API
    participant KasmSession as Kasm Session Container<br/>(Chrome kiosk)
    participant Nginx as viewer (nginx)<br/>watermark.html + grading panel
    participant GradingAPI as grading-api<br/>(FastAPI + SQLite)
    participant Orthanc as orthanc (DICOM store)

    Coordinator->>CreateSession: Run with --student-id X
    CreateSession->>KasmAPI: POST /api/public/request_kasm<br/>(env: STUDENT_ID, SESSION_ID)
    KasmAPI->>KasmSession: Provision ephemeral container<br/>(ipcmc/dicom-viewer:mvp)
    KasmAPI-->>CreateSession: kasm_id, user_id, session_token, link
    CreateSession-->>Coordinator: Prints the individual link
    Coordinator->>Student: Sends link (out of band, e.g. email)

    Student->>KasmSession: Opens link in browser
    KasmSession->>Nginx: Chrome loads watermark.html<br/>(?student_id=..&session_id=..)
    Nginx->>GradingAPI: GET /api/case?student_id=X
    GradingAPI-->>Nginx: Current stage + case<br/>(never ground truth yet)
    Nginx-->>KasmSession: Renders DICOM viewer + grading panel
    KasmSession->>Orthanc: iframe loads filtered-studies view<br/>(via auth-injecting proxy :8043)
    Orthanc-->>KasmSession: Study list filtered to the one assigned study
    Student->>Orthanc: Clicks the row to open the image viewer
    KasmSession-->>Student: Streams pixels only, watermark tiled over everything

    alt Nauka (learning)
        Student->>Nginx: Submits free-text impression
        Nginx->>GradingAPI: POST /api/submit
        GradingAPI-->>Nginx: Reference report (not graded)
        Nginx-->>Student: Shows student's text + reference report, stacked
    else Ocena (assessment)
        Student->>Nginx: Submits category + S modifier
        Nginx->>GradingAPI: POST /api/submit
        GradingAPI-->>Nginx: correct/incorrect + ground truth
        Nginx-->>Student: Immediate feedback
    else Test (exam)
        Student->>Nginx: Submits category + S modifier
        Nginx->>GradingAPI: POST /api/submit
        GradingAPI-->>Nginx: No reveal
        Nginx-->>Student: "Odpowiedź zapisana"
    end

    Note over Student,GradingAPI: Repeats per case until each stage's case(s) are exhausted
    Student->>Nginx: After all 3 stages: views results
    Nginx->>GradingAPI: GET /api/results
    Student->>Nginx: Optional: "Rozpocznij od początku"
    Nginx->>GradingAPI: POST /api/reset
```

Key differences from the original design discussion in `Dokumentacja/`
worth calling out explicitly: no Moodle launches this (a coordinator runs
`create-session.py` by hand), no payment gate, no LTI grade passback, and
`GET /case` / `POST /submit` enforce that ground truth and the reference
report are never sent to the browser before the stage that's supposed to
reveal them — that rule lives in `grading-api/app/main.py`, not just this
diagram.

## Deployment topology

```mermaid
%%{init: {'flowchart': {'nodeSpacing': 65, 'rankSpacing': 75}}}%%
flowchart TD
    Coordinator(["Coordinator<br/>runs scripts/create-session.py"])
    Student(["Radiologist / Tester<br/>web browser only"])

    subgraph Host["Single Docker host (see docs/PROXMOX_DEPLOYMENT.md for a separate-host variant)"]
        subgraph KasmInfra["Kasm Workspaces (Community Edition)"]
            KasmProxy["Kasm Proxy"]
            KasmAPI["Kasm Manager / Public API"]
            KasmAgent["Kasm Agent"]
            KasmContainer["Session Container<br/>ipcmc/dicom-viewer:mvp<br/>Chrome kiosk + watermark"]
        end

        subgraph AppStack["docker-compose app stack"]
            Viewer["viewer (nginx)<br/>watermark.html + grading panel<br/>/api proxy + auth-injecting Orthanc proxy"]
            GradingAPI[("grading-api<br/>FastAPI + SQLite")]
            Orthanc[("orthanc<br/>DICOM store")]
        end
    end

    Coordinator -->|"1. mint link"| KasmAPI
    KasmAPI -->|"2. provision"| KasmAgent
    KasmAgent -->|"3. spawn"| KasmContainer
    KasmAPI -.->|"4. returns link"| Coordinator
    Coordinator -->|"5. sends link"| Student

    Student -->|"6. opens link"| KasmProxy
    KasmProxy --> KasmContainer
    KasmContainer --> KasmProxy
    KasmContainer -->|"7. loads page"| Viewer
    Viewer -->|"8. /api/*"| GradingAPI
    GradingAPI --> Viewer
    Viewer -->|"9. auth-injected proxy :8043"| Orthanc
    Orthanc --> Viewer

    KasmAgent -.->|"destroys on logout"| KasmContainer
```

"Single Docker host" is currently this dev laptop, not yet anything
reachable from the GUMed/UCK network — see `docs/PROXMOX_DEPLOYMENT.md`
for the separate-host variant. Kasm Workspaces is installed as Community
Edition, which comes with real licensing constraints (non-commercial-use
restriction, 5-concurrent-session cap) — see CLAUDE.md's hard rules, not
this diagram, for the authoritative statement of those.

Notes on what this diagram deliberately does *not* show, because it
doesn't exist yet: Moodle, a payment gateway, LTI Advantage, a
Postgres/stratified-sampling layer, a Proxmox cluster (this is one host).
Each of those is a real, tracked gap — see README.md and CLAUDE.md, not
this diagram, for the authoritative list so the two don't drift apart.

Separately, a **Weasis-based session flow is being built alongside this
one** (milestone "Weasis viewer migration", GitHub issues #3-#8) — a second
Kasm workspace image (`docker/kasm-workspace-weasis/`) exists and is
verified to launch Weasis correctly, but it isn't wired into any live
session yet (no auto-launch against an assigned study, no grading panel,
no watermark overlay for it). Both diagrams above still describe the
Chrome+Orthanc flow because that's the only one actually running
end-to-end today; they'll be redrawn once the Weasis flow reaches the same
point.
