CLAUDE.md
Output only what is asked.
If uncertain: say UNKNOWN or omit. Do not guess.
Bullets > prose. Prefer deletion over verbosity.
Do not rewrite whole files; make surgical edits.
Hard Rules
Medical data (DICOM, raw pixels) stays strictly on-premise (Proxmox servers); no cloud storage or AWS integration.
Client receives only HTML5 video stream pixels via Kasm Workspaces (or, in the Apache Guacamole PoC evaluated as a licensing-safe alternative -- see README.md's Guacamole section -- via Guacamole; docker/guacamole-weasis/ is not yet hardened to Kasm-flow parity, do not use it for real patient data).
Grading is categorical Lung-RADS (0, 1, 2, 3, 4A, 4B, 4X); no NLP or free-text grading.
Stratified random sampling assigns 50/50/30 cases per user across Lung-RADS classes. NOT YET BUILT: current grading-api uses one fixed case per stage, no stratification, SQLite not Postgres (deliberate MVP choice, see README.md "Lung-RADS grading").
Forensic watermarking (Student ID + Timestamp) is mandatory for every streamed session via Kasm.
Ephemeral containers: Kasm containers must be destroyed on logout; zero data persistence.
All DICOM files must be anonymized and stripped of PHI before ingestion.
Kasm Workspaces Community Edition is non-commercial/non-profit/personal use only (EULA §2.2) and capped at 5 concurrent sessions -- do not build around working past this cap. Needs a paid tier + legal/procurement review before any real (paid, 10-20+ concurrent) deployment. Apache Guacamole (Apache-licensed, no such cap) is being evaluated as an alternative for exactly this reason -- see README.md's Guacamole section for what's built and what's explicitly still PoC-only.
Links / Authority
README.md -- what's built vs. deferred, verification notes
docs/ARCHITECTURE.md -- current-state diagrams (session flow, deployment topology)
docs/LOCAL_SETUP_GUIDE.md -- full environment setup walkthrough
docs/PROXMOX_DEPLOYMENT.md -- separate-host deployment
Kasm Workspaces Developer API: https://docs.kasm.com/docs/reference/developer-api
Orthanc REST API: https://orthanc.uclouvain.be/book/users/rest.html
Moodle LTI 1.3 Advantage: https://docs.moodle.org/502/en/Publish_as_LTI_tool (not yet integrated)
Setup / Test
cp .env.example .env && set a real ORTHANC_PASSWORD
docker compose up -d --build
./scripts/fetch-public-samples.sh && ./scripts/load-sample-studies.sh
./scripts/lint.sh
./scripts/build-test.sh
./scripts/security-scan.sh
./scripts/test-grading-api.sh
./scripts/test-shell-scripts.sh
Workflow
CI is a 5-stage pipeline (.github/workflows/ci.yml), each stage gating the next: Lint & Format -> Build Test -> Security Scan -> Unit & Shell Tests -> Integration Tests. Every stage has a script runnable identically locally.
Start local infrastructure: docker compose up -d --build
Load sample DICOM data: ./scripts/fetch-public-samples.sh && ./scripts/load-sample-studies.sh
1. Lint & Format (bash syntax + compose config + ruff check/format on grading-api, no infra needed): ./scripts/lint.sh
2. Build Test (builds every image in the repo, does not run any of them): ./scripts/build-test.sh
3. Security Scan (bandit, shellcheck, hadolint, trivy -- see .hadolint.yaml for the threshold/rationale): ./scripts/security-scan.sh
4. Unit test grading-api's state machine (no infra needed, ~0.1s): ./scripts/test-grading-api.sh
4. BATS test the Kasm workspace launcher scripts (no infra needed, requires bats): ./scripts/test-shell-scripts.sh
5. Smoke test (brings the stack up for real, then tears it down -- don't run against data you care about): ./scripts/smoke-test.sh
5. Guacamole PoC end-to-end test (Playwright, docker-in-docker, self-contained/self-tearing-down): ./scripts/test-guacamole-integration.sh
Mint a per-student Kasm link: python3 scripts/create-session.py --student-id ...
Guacamole PoC (see README.md): ./scripts/guacamole-iac.sh, then python3 scripts/provision-guacamole-session.py --student-id ...
Stop Conditions
PRs sending raw DICOM files to client outside Kasm stream → stop
Adding AWS AppStream, AWS S3, or external cloud dependencies → stop
Disabling, bypassing, or removing forensic watermarking → stop
Introducing free-text/NLP grading instead of categorical Lung-RADS → stop
Destructive database operations without explicit confirmation → stop