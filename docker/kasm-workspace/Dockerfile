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
ENV VIEWER_URL="http://CHANGE_ME_TO_YOUR_HOST:8080/"

COPY custom_startup.sh /dockerstartup/custom_startup.sh
RUN chmod +x /dockerstartup/custom_startup.sh

USER kasm-user
