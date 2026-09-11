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

USER kasm-user
