CLAUDE.md
Output only what is asked.
If uncertain: say UNKNOWN or omit. Do not guess.
Bullets > prose. Prefer deletion over verbosity.
Do not rewrite whole files; make surgical edits.
Hard Rules
Medical data (DICOM, raw pixels) stays strictly on-premise (Proxmox servers); no cloud storage or AWS integration.
Client receives only HTML5 video stream pixels via Kasm Workspaces.
Grading is categorical Lung-RADS (0, 1, 2, 3, 4A, 4B, 4X); no NLP or free-text grading.
Stratified random sampling via PostgreSQL assigns 50/50/30 cases per user across Lung-RADS classes.
Forensic watermarking (Student ID + Timestamp) is mandatory for every streamed session via Kasm.
Ephemeral containers: Kasm containers must be destroyed on logout; zero data persistence.
All DICOM files must be anonymized and stripped of PHI before ingestion.
Links / Authority
docs/ARCHITECTURE.md
docs/SECURITY.md
Kasm Workspaces API: https://kasmweb.com/docs/api.html
Orthanc REST API: https://orthanc.uclouvain.be/book/users/rest.html
Moodle LTI 1.3 Advantage: https://www.moodle.org/en/lti/
Setup / Test
docker-compose up -d
npm install
npm run test
Workflow
Start local infrastructure: docker-compose up -d
Run middleware API: npm run dev
Run test suite: npm test
Sync Orthanc DICOM store: npm run sync-dicom
Stop Conditions
PRs sending raw DICOM files to client outside Kasm stream → stop
Adding AWS AppStream, AWS S3, or external cloud dependencies → stop
Disabling, bypassing, or removing forensic watermarking → stop
Introducing free-text/NLP grading instead of categorical Lung-RADS → stop
Destructive database operations without explicit confirmation → stop