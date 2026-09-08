# Custom Kasm Workspace image for the DICOM viewer MVP.
#
# This is NOT meant to be built/run with plain `docker build` + `docker run` —
# Kasm images must be registered through Kasm Workspaces (which injects its own
# agent, VNC server, audio, etc.). Build this only after Kasm Workspaces itself
# is installed (see README.md "From MVP to the real thing"). Once Kasm is up:
#
#   docker build -t ipcmc/dicom-viewer:mvp docker/kasm-workspace
#   -> register the resulting image in the Kasm admin UI as a new Workspace,
#      or `docker push` it to a registry Kasm can pull from.
#
# Base: Kasm's official Chrome-only workspace (lightest option — we don't need
# a full desktop since the viewer is a web page).
FROM kasmweb/chrome:1.16.0

USER root

# Kiosk launch config: open the watermarked viewer instead of a normal browser
# chrome/session URLs are provided per-session by scripts/create-session.py,
# which appends ?student_id=...&session_id=... to VIEWER_URL below.
# Reachable because docker-compose.yml attaches `viewer`/`orthanc` to
# kasm_default_network — Docker's embedded DNS resolves them by container name.
# Both can still be overridden per-session via the Kasm API's `environment`
# field (see scripts/create-session.py) — these are just sane defaults so the
# workspace also works from a plain "Launch" click in the Kasm admin UI.
ENV VIEWER_URL="http://ipcmc-viewer:8080/"
# Port 8043 on the viewer container, NOT Orthanc's own 8042 — that's the
# auth-injecting proxy in nginx.conf. Pointing here directly at Orthanc
# would hit its Basic Auth challenge, which an iframe can't answer.
ENV ORTHANC_URL="http://ipcmc-viewer:8043/"

COPY custom_startup.sh /dockerstartup/custom_startup.sh
RUN chmod +x /dockerstartup/custom_startup.sh

USER kasm-user
