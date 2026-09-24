#!/usr/bin/env bash
# Container entrypoint: starts TigerVNC in the foreground (-fg) as PID 1's
# child, so the container's lifecycle is tied directly to the VNC server
# itself -- if the student's session ends (xstartup's own final exec'd
# process, launch-session.sh -> Weasis, exits), TigerVNC exits too, and
# the container stops, same ephemeral-session spirit as the Kasm flow
# (see CLAUDE.md) even without Kasm's own agent managing it.
#
# -SecurityTypes None: no VNC password. Deliberate simplification for this
# basic-functionality PoC (explicit scope: no anti-cheat/hardening yet) --
# safe *only* because this container is never published to the host and
# is reachable exclusively by guacd over the internal ipcmc-internal
# network (see docker-compose.guacamole.yml). Do not carry this forward
# into any hardened/production version of this flow.
set -euo pipefail

VNC_GEOMETRY="${VNC_GEOMETRY:-1280x800}"
VNC_DISPLAY="${VNC_DISPLAY:-:1}"

mkdir -p "$HOME/.vnc"
# --I-KNOW-THIS-IS-INSECURE: TigerVNC itself refuses -SecurityTypes None
# + -localhost no without this exact flag, as a safety guard against
# accidentally exposing an unauthenticated VNC server -- which is exactly
# what this is, deliberately, for this PoC (see this file's own header
# comment on why that's an acceptable, scoped trade-off here and not
# something to carry forward).
exec vncserver "$VNC_DISPLAY" \
    -geometry "$VNC_GEOMETRY" \
    -SecurityTypes None \
    -localhost no \
    --I-KNOW-THIS-IS-INSECURE \
    -fg
