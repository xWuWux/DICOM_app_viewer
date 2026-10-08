#!/usr/bin/env bash
# End-to-end check of the watermark overlay's two modes (issue #150), on a real
# X server (the image's own KasmVNC Xvnc) with the real picom and overlay.py:
#   alpha mode      picom running  -> the ARGB overlay is genuinely transparent
#   fallback mode   picom absent   -> the glyph-shaped window is used
# The failure this guards against is the one seen live: an ARGB overlay WITHOUT a
# compositor renders as an opaque black rectangle over the whole viewer; and the
# one before it: the shaped window flashing the DICOM view. Neither is visible to
# unit tests, only to pixels on a screen.
#
# Method: screenshot the root window (xwd) in four situations and compare the
# share of pixels still equal to the screen's dominant colour:
#   A no overlay, no compositor | B picom only (reference) |
#   C picom + overlay           | D picom killed, overlay still running
# C and D must keep >= 99% of the screen untouched AND draw some watermark pixels
# (> 0), and D must have gone back to A's colour (fallback really engaged).
# Needs Docker; builds the real Weasis workspace image (no Kasm needed).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Not under the project's ipcmc/ namespace: other jobs on a shared Docker daemon
# clean those tags up (seen while building this test).
IMAGE="local/overlay-compositor-test:$$"
trap 'docker rmi -f "$IMAGE" >/dev/null 2>&1 || true' EXIT

echo "--- building the Weasis workspace image ---"
docker build -q -t "$IMAGE" docker/kasm-workspace-weasis >/dev/null

echo "--- running the overlay on a real X server ---"
out=$(docker run --rm -i --entrypoint bash "$IMAGE" -s 2>&1 <<'INNER' || true
set -u
export DISPLAY=:99
Xvnc :99 -geometry 1280x800 -depth 24 -SecurityTypes None -rfbport 5999 -interface 127.0.0.1 >/tmp/xvnc.log 2>&1 &
for i in $(seq 1 30); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.5; done
xdpyinfo >/dev/null 2>&1 || { echo "RESULT X_SERVER_FAILED"; exit 2; }
xsetroot -solid '#336699'
cat > /tmp/px.py <<'PY'
import struct, subprocess, collections
subprocess.run(["xwd","-root","-silent","-out","/tmp/s.xwd"],check=True)
d=open("/tmp/s.xwd","rb").read()
hs=struct.unpack(">I",d[:4])[0]; w,h=struct.unpack(">II",d[16:24]); bpl=struct.unpack(">I",d[48:52])[0]; nc=struct.unpack(">I",d[76:80])[0]; off=hs+nc*12
c=collections.Counter()
for y in range(0,h,3):
    for x in range(0,w,3):
        o=off+y*bpl+x*4; c[(d[o+2],d[o+1],d[o])]+=1
n=sum(c.values()); top,cnt=c.most_common(1)[0]
print(f"{top[0]:02x}{top[1]:02x}{top[2]:02x} {cnt} {n-cnt} {n}")
PY
echo "RESULT A $(python3 /tmp/px.py)"
picom --config /opt/watermark/picom.conf >/tmp/picom.log 2>&1 & PC=$!
sleep 3
echo "RESULT B $(python3 /tmp/px.py)"
STUDENT_ID=E2E_STU SESSION_ID=1 python3 /opt/watermark/overlay.py >/tmp/overlay.log 2>&1 & OV=$!
sleep 4
echo "RESULT C $(python3 /tmp/px.py)"
kill $PC; sleep 4
echo "RESULT D $(python3 /tmp/px.py)"
kill $OV 2>/dev/null
INNER
)

python3 - "$out" <<'PY'
import sys
res = {}
for line in sys.argv[1].splitlines():
    if line.startswith("RESULT "):
        parts = line.split()
        if parts[1] in "ABCD" and len(parts) == 6:
            res[parts[1]] = (parts[2], int(parts[3]), int(parts[4]), int(parts[5]))
        else:
            print("FAIL:", line); sys.exit(1)
if sorted(res) != list("ABCD"):
    print("FAIL: expected measurements A-D, got", sorted(res), "-- the check did not really run")
    print(sys.argv[1][-1500:]); sys.exit(1)
fail = 0
def check(name, ok, detail):
    global fail
    print(("OK   " if ok else "FAIL ") + name + " -- " + detail)
    fail += (not ok)
A, B, C, D = (res[k] for k in "ABCD")
share = lambda r: r[1] / (r[1] + r[2])
check("C: with picom the overlay is transparent (screen colour unchanged)", C[0] == B[0] and share(C) >= 0.99, f"dominant {C[0]} vs reference {B[0]}, {share(C):.2%} untouched")
check("C: watermark pixels are drawn", C[2] > 0, f"{C[2]} watermark pixels")
check("D: without picom the fallback restores the screen (no black rectangle)", D[0] == A[0] and share(D) >= 0.99, f"dominant {D[0]} vs original {A[0]}, {share(D):.2%} untouched")
check("D: fallback still draws the watermark", D[2] > 0, f"{D[2]} watermark pixels")
sys.exit(1 if fail else 0)
PY
