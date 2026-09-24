#!/usr/bin/env bash
# Container entrypoint: starts TigerVNC in the foreground (-fg) as PID 1's
# child, so the container's lifecycle is tied directly to the VNC server
# itself -- if the student's session ends (xstartup's own final exec'd
# process, launch-session.sh -> Weasis, exits), TigerVNC exits too, and
# the container stops, same ephemeral-session spirit as the Kasm flow
# (see CLAUDE.md) even without Kasm's own agent managing it.
#
# issue #53: real VNC auth, not -SecurityTypes None. Safe *only* because
# this container is never published to the host (reachable exclusively by
# guacd over the internal ipcmc-internal network -- see
# docker-compose.guacamole.yml) was the original justification for zero
# auth here; this closes the gap for real regardless, in case that
# assumption is ever violated (e.g. a stray `ports:`/`-p` during
# debugging). VNC_PASSWORD is minted per-session by
# scripts/provision-guacamole-session.py and handed to Guacamole's own
# connection config as its `password` parameter -- guacd authenticates
# with it like any other VNC client would.
set -euo pipefail

VNC_GEOMETRY="${VNC_GEOMETRY:-1280x800}"
VNC_DISPLAY="${VNC_DISPLAY:-:1}"

mkdir -p "$HOME/.vnc"
# The classic VNC/RFB password scheme (VncAuth) only ever uses the first 8
# bytes of the password -- inherent to the protocol, not something to "fix"
# here. `vncpasswd -f` (from the tightvncpasswd package -- see Dockerfile's
# own comment on why upstream tigervnc's own vncpasswd isn't available in
# this Ubuntu packaging) reads a password from stdin and writes the
# correctly-obfuscated password file, the same universal format every
# RFB-derived server/client reads.
printf '%s\n' "${VNC_PASSWORD:?VNC_PASSWORD must be set -- see scripts/provision-guacamole-session.py}" \
  | vncpasswd -f > "$HOME/.vnc/passwd"
chmod 600 "$HOME/.vnc/passwd"

# No -SecurityTypes flag needed: TigerVNC defaults to VncAuth once a
# password file exists (confirmed against /etc/tigervnc/vncserver-config-
# defaults' own documented default), which is exactly what's wanted here.
exec vncserver "$VNC_DISPLAY" \
    -geometry "$VNC_GEOMETRY" \
    -localhost no \
    -fg
