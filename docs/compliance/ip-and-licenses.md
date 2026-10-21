# Intellectual property and open source licences: outline

## Intellectual property (TO DECIDE)
| Asset | Owner | Status |
|---|---|---|
| This repository's code and documentation | **TO DECIDE** (GUMed? authors?) | no `LICENSE` file; decision deferred by the owner (#108). Until decided, no licence is granted to others. |
| CT studies and reference reports (cases, answer key) | **TO DECIDE** (radiologist / clinic / GUMed) | kept local only (`local/cases.json`, git-ignored); terms of use for the course and for research **TO DECIDE** (#87) |
| Exam results and any publication | **TO DECIDE** | depends on the research plan and the legal basis (privacy notice) |
| Contributions by collaborators | **TO DECIDE** | no contributor agreement; relevant once a project licence is chosen |

## Third-party software
| Component | Licence | Source of the statement |
|---|---|---|
| Kasm Workspaces Community Edition | proprietary EULA: non-commercial, 5 concurrent sessions (section 2.2) | CLAUDE.md |
| Apache Guacamole | Apache License 2.0 | CLAUDE.md, Apache project |
| Orthanc, Weasis, KasmVNC, TigerVNC, picom, Python packages, base images | **to verify from upstream** | #73 |

## Next steps
- #73: a report-only licence inventory (trivy `--scanners license`) and a verified table of the main
  components, then an allow/deny policy decided with legal before any CI gate.
- Decide the project licence (#108), considering the copyleft terms of components that are
  distributed, if the project is ever distributed outside GUMed.
