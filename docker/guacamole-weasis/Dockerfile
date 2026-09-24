# Guacamole-flow Weasis workspace image -- a PoC alternative to
# docker/kasm-workspace-weasis/, evaluated because Kasm Workspaces
# Community Edition is capped at 5 concurrent sessions and restricted to
# non-commercial/non-profit/personal use (see CLAUDE.md, EULA §2.2), which
# blocks any real multi-VM cohort deployment without a paid tier + legal
# review. Apache Guacamole is Apache-licensed with no such cap.
#
# Deliberately basic-functionality only, per explicit scope for this PoC:
# NO watermark overlay, NO Weasis export/import lockdown, NO window
# auto-tiling. This is NOT a security-hardened image the way
# docker/kasm-workspace-weasis/ is -- don't point it at real patient data.
#
# Base: plain ubuntu:24.04, not any Kasm-maintained image -- the whole
# point is a flow that doesn't depend on Kasm at all. TigerVNC standard
# server, not KasmVNC -- confirmed via direct protocol-level testing that
# guacd's VNC client cannot complete an RFB handshake against KasmVNC at
# all (it serves VNC exclusively over its own websocket-wrapped protocol,
# never a raw RFB TCP port); guacd needs a real raw-RFB VNC server.
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

# issue #53: tightvncpasswd generates the password file docker-entrypoint.sh
# uses for real VNC auth. Ubuntu's own tigervnc-standalone-server/
# tigervnc-common packages don't ship a vncpasswd utility at all (confirmed
# empirically -- dpkg -L on both shows no such binary), unlike upstream
# TigerVNC. tightvncpasswd's own vncpasswd writes the exact same universal
# VNC password-file format every RFB-derived server/client reads (TigerVNC,
# TightVNC, RealVNC, x11vnc all descend from the same original codebase) --
# confirmed by installing it and inspecting the output file directly, not
# assumed. Pulls in zero extra dependencies beyond what's already installed.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      tigervnc-standalone-server \
      tigervnc-common \
      tightvncpasswd \
      openbox \
      dbus-x11 \
      epiphany-browser \
      desktop-file-utils \
      xdg-utils \
      libgtk-3-0 \
      gvfs \
      gvfs-daemons \
      python3 \
      curl \
      ca-certificates \
      fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

# Weasis's own .deb release -- same version pin as
# docker/kasm-workspace-weasis/Dockerfile, for the same reason (a specific
# release, not "latest").
#
# mkdir before installing: Weasis's postinst script calls
# xdg-desktop-menu to register a launcher entry, which fails outright
# ("No writable system menu directory found") on a plain Ubuntu base --
# confirmed the hard way, this build failed until these directories
# existed. docker/kasm-workspace-weasis/'s own Kasm base image never hit
# this because its full XFCE stack already provisions them; a minimal
# openbox-only image doesn't. We never actually use the menu entry (Weasis
# is launched directly by absolute path in launch-session.sh), so this is
# just satisfying the postinst script, not something we depend on.
ARG WEASIS_VERSION=4.7.3
RUN mkdir -p /usr/share/desktop-directories /usr/share/applications && \
    curl -fsSL -o /tmp/weasis.deb \
      "https://github.com/nroduit/Weasis/releases/download/v${WEASIS_VERSION}/weasis_${WEASIS_VERSION}-1_amd64.deb" && \
    apt-get update && \
    apt-get install -y /tmp/weasis.deb && \
    rm -f /tmp/weasis.deb && \
    rm -rf /var/lib/apt/lists/*

# Suppresses Weasis's own "not a certified medical device" disclaimer
# dialog -- a usability fix (every session gets a fresh, ephemeral $HOME
# and would otherwise hit it every time), not an anti-cheat/lockdown
# measure, so it stays even though issue #7's full export/import lockdown
# (docker/kasm-workspace-weasis/patch-weasis-config.py) does not apply
# here. Same key/mechanism as that file's own disclaimer patch -- see its
# header comment for how this was confirmed.
RUN python3 -c "\
import json; \
p = '/opt/weasis/lib/app/conf/base.json'; \
d = json.load(open(p)); \
[pref.update(value='false') for pref in d['weasisPreferences'] if pref['code'] == 'weasis.show.disclaimer']; \
json.dump(d, open(p, 'w'), indent=2)"

# Same REMOTE_HOST/VIEWER_URL/ORTHANC_URL convention as
# docker/kasm-workspace-weasis/Dockerfile -- see that file's own comments.
ARG REMOTE_HOST=ipcmc-viewer
ENV VIEWER_URL="http://${REMOTE_HOST}:8080/"
ENV ORTHANC_URL="http://${REMOTE_HOST}:8043/"

RUN useradd -m -s /bin/bash student
ENV HOME=/home/student

COPY xstartup /home/student/.vnc/xstartup
COPY launch-session.sh /opt/startup/launch-session.sh
COPY docker-entrypoint.sh /opt/startup/docker-entrypoint.sh
RUN chmod +x /home/student/.vnc/xstartup \
             /opt/startup/launch-session.sh \
             /opt/startup/docker-entrypoint.sh && \
    chown -R student:student /home/student

USER student
WORKDIR /home/student

ENTRYPOINT ["/opt/startup/docker-entrypoint.sh"]
