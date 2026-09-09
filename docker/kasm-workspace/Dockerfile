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
#
# REMOTE_HOST defaults to "ipcmc-viewer" -- the container-name DNS entry
# that only resolves when Kasm and the viewer/orthanc containers share the
# same Docker host (docker-compose.yml's kasm_default_network attachment).
#
# Deploying viewer/orthanc on a separate Proxmox VM/LXC instead (see
# docker-compose.remote-host.yml, docs/PROXMOX_DEPLOYMENT.md)? Build with:
#   docker build --build-arg REMOTE_HOST=<that VM's hostname or IP> \
#     -t ipcmc/dicom-viewer:mvp docker/kasm-workspace
# (Same effect, no rebuild needed: set VIEWER_URL/ORTHANC_URL directly in the
# Kasm admin UI's "Docker Run Config Override" for the registered workspace.)
ARG REMOTE_HOST=ipcmc-viewer
ENV VIEWER_URL="http://${REMOTE_HOST}:8080/"
# Port 8043, NOT Orthanc's own 8042 — that's the auth-injecting proxy in
# docker/viewer/default.conf.template. Pointing here directly at Orthanc
# would hit its Basic Auth challenge, which an iframe can't answer.
ENV ORTHANC_URL="http://${REMOTE_HOST}:8043/"

COPY custom_startup.sh /dockerstartup/custom_startup.sh
RUN chmod +x /dockerstartup/custom_startup.sh

USER kasm-user
