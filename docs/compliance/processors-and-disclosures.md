# Processors, data processing agreements and third-party disclosure: outline

## Who touches the data
| Party | Role (to confirm) | Personal data reaching them | Agreement |
|---|---|---|---|
| GUMed (operator of the Proxmox / host infrastructure) | controller, presumably (**TO DECIDE**) | all stores in [data-inventory.md](data-inventory.md) | internal |
| Kasm Technologies (Kasm Workspaces CE, software run on-prem) | software vendor | none by design; **UNKNOWN** whether CE contacts the vendor (licence check, telemetry); to verify before go-live | Kasm EULA; DPA only if data reaches them |
| Apache Software Foundation (Guacamole, software run on-prem) | software vendor | none | Apache 2.0 licence |
| Orthanc, Weasis (software run on-prem) | software vendors | none | their licences (#73) |
| GitHub (hosts the code, issues, CI) | code hosting | **must stay none**: no real participant ids, study data, keys or links in issues, PRs, CI logs (secret scan: gitleaks, #245; demo ids only so far) | GitHub terms |
| Data provider of the CT studies (radiologist / clinic) | source of anonymised studies | anonymised studies flow **to** us | **TO DECIDE**: data-sharing agreement and anonymisation statement (#87, #162) |
| Any future external host, cloud, scrubbing service | would be a processor | not allowed today (CLAUDE.md: on-premise only, no cloud) | would need a DPA (GDPR art. 28) and legal review |

## Data processing agreement
We found no external processor in the data path of the current on-premise setup. Whether that
removes the need for a DPA is **TO DECIDE by legal**; the finding holds only if:
1. the Kasm CE outbound-traffic question above is answered "no personal data";
2. hosting stays on GUMed infrastructure operated by GUMed staff. If the Proxmox hosts are run by
   another unit or company, that relationship needs to be classified (**TO DECIDE**).

## Third-party disclosure
No disclosure of participant data to third parties by design. Planned disclosures to decide:
- publication of research results (aggregated or pseudonymised: **TO DECIDE**);
- sharing results with the course organisers or the radiologist (**TO DECIDE**).
