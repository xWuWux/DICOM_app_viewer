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

# Issue #4 ("Automatic Launch Without Extra Clicks"): Weasis's own "not a
# certified medical device" disclaimer blocks the main window on first
# launch until clicked through. Its "shown once, remembered after" default
# behavior is useless here -- Kasm containers are ephemeral (destroyed on
# logout, zero persistence, see CLAUDE.md), so every session is a genuinely
# fresh $HOME and would hit this dialog every single time. Patched into the
# base config at build time (not a launch flag) so it's baked into every
# container regardless of how it's started. Verified: the key is
# weasisPreferences[].code == "weasis.show.disclaimer" in this exact file.
RUN python3 -c "\
import json; \
p = '/opt/weasis/lib/app/conf/base.json'; \
d = json.load(open(p)); \
[pref.update(value='false') for pref in d['weasisPreferences'] if pref['code'] == 'weasis.show.disclaimer']; \
json.dump(d, open(p, 'w'), indent=2)"

# Same REMOTE_HOST/VIEWER_URL/ORTHANC_URL convention as
# docker/kasm-workspace/Dockerfile -- see that file's own comments for why
# the defaults are what they are (container-name DNS on the shared
# kasm_default_network; :8043 not Orthanc's own :8042, since that's the
# auth-injecting proxy -- Weasis is never taught Orthanc's real credentials).
ARG REMOTE_HOST=ipcmc-viewer
ENV VIEWER_URL="http://${REMOTE_HOST}:8080/"
ENV ORTHANC_URL="http://${REMOTE_HOST}:8043/"

COPY custom_startup.sh /dockerstartup/custom_startup.sh
RUN chmod +x /dockerstartup/custom_startup.sh

USER kasm-user
