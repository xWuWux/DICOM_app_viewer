# Dependencies and Licenses (issue #73)

This document tracks third-party dependencies, licenses, owners, and risk assessment. License policy (allow/deny) is TBD pending review.

## Key components
| Component | Source | Purpose | Owner | License (to verify) | Notes |
|---|---|---|---|---|---|
| FastAPI | PyPI | grading-api framework | FastAPI project | MIT (verify) | In requirements.txt |
| Uvicorn | PyPI | ASGI server | Encode | BSD-3-Clause (verify) | In requirements.txt |
| Orthanc | orthancteam/orthanc | DICOM store/DICOMweb | Orthanc project | GPL/AGPL-family (verify) | See README/EULA notes |
| Weasis | Weasis project | DICOM viewer | Weasis project | EPL/GPL-family (verify) | In kasm-workspace-weasis |
| Kasm Workspaces CE | kasmweb/* | Streaming platform | Kasm | EULA §2.2 (non-commercial/personal, 5 concurrent sessions) | See CLAUDE.md/README |
| KasmVNC/TigerVNC/picom | Upstream | Display/remote | Various | GPL-family (verify) | In images |
| Guacamole/guacd/Postgres | Apache/Official | Alternative streaming | Apache/Upstream | Apache-2.0/Postgres/various (verify) | PoC |

## Scan
- `scripts/security-scan.sh` runs Trivy license scans in report-only mode
- Review license inventory before setting allow/deny policy and enabling hard gate

## Action items
- [ ] Decide allow/deny license policy
- [ ] Add `LICENSE`/`NOTICE` if applicable
- [ ] Generate SBOM (syft/trivy cyclonedx) in CI
- [ ] Document vendor exit plan and data portability
