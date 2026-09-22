#!/usr/bin/env python3
"""
Build-time patch for /opt/weasis/lib/app/conf/base.json -- run once during
`docker build` (see the Dockerfile), never at container start. Every change
here is a preference Weasis itself already exposes (verified against
nroduit/Weasis's own weasis-distributions/etc/config/base.json, the
upstream source of this same file's documented defaults), not a hack.

weasis.show.disclaimer (issue #4): Weasis's own "not a certified medical
device" dialog blocks the main window on first launch until clicked
through. Its "shown once, remembered after" default behavior does nothing
useful here -- Kasm containers are ephemeral (destroyed on logout, zero
persistence, see CLAUDE.md), so every session gets a genuinely fresh
$HOME and would otherwise hit it every single time.

The rest (issue #7, "Lock Down Weasis Native Save/Copy/Export" -- a direct
answer to the context-menu/export concern raised on a reference
screenshot): confirmed via a real running session (File menu, right-click
on the viewport) exactly what's actually reachable before deciding what to
disable, rather than guessing from the menu labels alone.
  - File > Export offered two items: "Exporting view" (a screenshot/image
    save of the current view) and "DICOM" (raw DICOM export to local
    disk/CD/ISO). weasis.export.dicom=false hides the second entirely.
  - weasis.export.dicom.send / weasis.import.dicom / weasis.import.images
    / weasis.import.dicom.qr hide the remaining import/send-related
    menus and dialog pages the same way.
  - felix.auto.start.110 auto-starts three bundles as one group: DICOM
    send (network C-STORE to an arbitrary PACS -- a real exfiltration
    path Kasm's own session-level DLP doesn't cover, since it's a direct
    outbound connection Weasis makes on its own, not a browser
    download/clipboard/upload Kasm can intercept), DICOM Q/R (could let a
    student browse/pull studies beyond the one assigned, breaking "Single
    DICOM Study Isolation"), and an ISO writer (burn-to-disc export).
    Blanked out here, not just hidden -- Felix never loads those bundles
    at all, so the underlying Java code doesn't exist at runtime, not
    merely a menu item made invisible.

One config item this deliberately does NOT claim to close: "Exporting
view" (File > Export > "Exporting view", a screenshot of the current
view) has no matching preference anywhere in Weasis's own base.json --
confirmed by checking its actual source (ActionW.java's EXPORT_VIEW is an
unconditional, always-registered core action, not gated by any property).
Accepted as a residual risk, not silently ignored: any file it saves
stays trapped inside this ephemeral container (destroyed on logout, see
CLAUDE.md) and can't leave the session regardless, since Kasm's own DLP
settings already disable downloads, clipboard-out, uploads, sharing, and
printing at the session level (see README.md's DLP section) -- the same
reasoning that already covers Weasis's own unconditional Print menu,
which has no dedicated preference to hide it either.
"""
import json

PATH = "/opt/weasis/lib/app/conf/base.json"

FALSE_KEYS = {
    "weasis.show.disclaimer",
    "weasis.export.dicom",
    "weasis.export.dicom.send",
    "weasis.import.dicom",
    "weasis.import.images",
    "weasis.import.dicom.qr",
}

with open(PATH) as f:
    config = json.load(f)

for pref in config["weasisPreferences"]:
    if pref["code"] in FALSE_KEYS:
        pref["value"] = "false"
    elif pref["code"] == "felix.auto.start.110":
        pref["value"] = ""

with open(PATH, "w") as f:
    json.dump(config, f, indent=2)
