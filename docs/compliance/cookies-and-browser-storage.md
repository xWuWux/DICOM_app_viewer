# Cookies and browser storage

What the system stores in the doctor's browser, as found in the code on `master` (540354a). No
analytics, advertising or third-party scripts were found: the panel pages load no external URLs.

| Component | What | Purpose | Lifetime |
|---|---|---|---|
| Grading panel (`grading-panel.html`, `watermark.html`) | `sessionStorage`: the grading session token (#145) | keeps the session across a reload of the tab | until the tab is closed; removed on session end or expiry |
| Kasm (vendor) | Kasm's own session cookie / storage for the streamed session | authentication of the streaming session | Kasm-managed: **UNKNOWN in detail, to inventory from a live session** |
| Guacamole (Apache) | Guacamole stores its authentication token in the browser | authentication of the streaming session | Guacamole-managed: **UNKNOWN in detail, to inventory from a live session** |

## Consent
All storage found so far is needed to provide the service the user asked for. Whether strictly
necessary storage is exempt from consent under the applicable Polish and EU ePrivacy rules, and
whether an information notice (no consent banner) is then enough, is **TO DECIDE by legal**. A consent banner becomes necessary if analytics or any non-essential storage is ever added.

## To do before go-live
- Inventory the Kasm and Guacamole cookies/storage from a live session (names, lifetime) and fill in
  the two UNKNOWN rows.
