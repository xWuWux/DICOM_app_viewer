# Kasm workspace image for the Weasis-based viewer (issue #3, milestone
# "Weasis viewer migration" -- see that milestone for the full rationale:
# PM feedback wanted a familiar desktop-viewer feel, not Orthanc's web UI).
#
# This is a NEW, separate workspace image -- it does not replace
# docker/kasm-workspace/ (the Chrome-based one). Both can be registered in
# Kasm side by side while this is being validated.
#
# Base: Kasm's own maintained "core-ubuntu-noble" image (XFCE + KasmVNC,
# nothing else) -- deliberately NOT their "desktop" bundle (which layers on
# Chrome, Firefox, VS Code, GIMP, Zoom, Slack, etc.). None of that belongs
# in a locked-down exam kiosk; every extra app is extra attack surface.
# Tag 1.19.0 pinned to match the actual installed Kasm server version
# (confirmed available via `docker manifest inspect` before using it).
FROM kasmweb/core-ubuntu-noble:1.19.0

USER root

# Weasis's own .deb release. Pinned to a specific release, not "latest",
# matching how the Chrome-based image pins kasmweb/chrome:1.16.0.
ARG WEASIS_VERSION=4.7.3
RUN apt-get update && \
    curl -fsSL -o /tmp/weasis.deb \
      "https://github.com/nroduit/Weasis/releases/download/v${WEASIS_VERSION}/weasis_${WEASIS_VERSION}-1_amd64.deb" && \
    apt-get install -y /tmp/weasis.deb && \
    rm -f /tmp/weasis.deb && \
    rm -rf /var/lib/apt/lists/*

# Patches Weasis's own base.json: the disclaimer dialog (issue #4) and the
# native export/import/send/Q-R lockdown (issue #7). See
# patch-weasis-config.py's own header comment for the full rationale
# behind every key it touches -- not repeated here to avoid the two
# drifting apart.
COPY patch-weasis-config.py /tmp/patch-weasis-config.py
RUN python3 /tmp/patch-weasis-config.py && rm /tmp/patch-weasis-config.py

# Same REMOTE_HOST/VIEWER_URL/ORTHANC_URL convention as
# docker/kasm-workspace/Dockerfile -- see that file's own comments for why
# the defaults are what they are (container-name DNS on the shared
# kasm_default_network; :8043 not Orthanc's own :8042, since that's the
# auth-injecting proxy -- Weasis is never taught Orthanc's real credentials).
ARG REMOTE_HOST=ipcmc-viewer
ENV VIEWER_URL="http://${REMOTE_HOST}:8080/"
ENV ORTHANC_URL="http://${REMOTE_HOST}:8043/"

# Issue #6 (Watermark Overlay): python3-gi and python3-cairo are already
# part of this XFCE base image (it's a GTK desktop), but the actual GI<->
# Cairo bridge PyGObject needs to hand a native Cairo context to a "draw"
# signal handler ships in a separate package -- confirmed the hard way
# ("Couldn't find foreign struct converter for 'cairo.Context'") before
# adding this.
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3-gi-cairo && \
    rm -rf /var/lib/apt/lists/*

COPY overlay.py watchdog.sh /opt/watermark/
RUN chmod +x /opt/watermark/watchdog.sh

COPY custom_startup.sh /dockerstartup/custom_startup.sh
RUN chmod +x /dockerstartup/custom_startup.sh

USER kasm-user
