#!/usr/bin/env bash
# Shape-complexity budget for the forensic watermark overlay (overlay.py).
#
# Why this exists: the overlay is made "transparent" with an X bounding shape
# built from the glyph pixels (no compositor is available, see overlay.py's
# docstring). The X server clips every repaint of the windows beneath against
# that shape. ~4,500 rectangles (the old fixed 260 px gap at 2294x830) made the
# DICOM view flicker whenever the mouse moved; ~1,700 was flicker-free in a
# live Kasm session. The shape size grows with SCREEN AREA, so this test renders
# the REAL overlay drawing at laptop ... 4K resolutions and fails if any exceeds
# the budget -- a regression here is invisible to every other test and only
# shows up as a flickering viewer on a student's large display.
#
# Builds the real Weasis workspace image and runs the check inside it (needs
# Docker; no Kasm). Budget override: SHAPE_RECT_BUDGET=<int>.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

BUDGET="${SHAPE_RECT_BUDGET:-2500}"
IMAGE="ipcmc/dicom-viewer-weasis:overlay-shape-test"

echo "--- building the Weasis workspace image ---"
docker build -q -t "$IMAGE" docker/kasm-workspace-weasis >/dev/null

echo "--- shape rectangles per screen size (budget ${BUDGET}) ---"
out=$(docker run --rm -i --entrypoint python3 \
  -e STUDENT_ID=STU_SHAPE_TEST -e SESSION_ID=1700000000 -e BUDGET="$BUDGET" \
  -v "$(pwd)/docker/kasm-workspace-weasis/overlay.py:/opt/watermark/overlay.py:ro" \
  "$IMAGE" - 2>&1 <<'PY'
import importlib.util, os, sys
import cairo
import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

spec = importlib.util.spec_from_file_location("overlay", "/opt/watermark/overlay.py")
ov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ov)

probe = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
probe.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
probe.set_font_size(ov.FONT_SIZE)
ext = probe.text_extents(f"{ov.STUDENT_ID} | {ov.SESSION_ID} | 0000-00-00T00:00:00Z")
tw, th = int(ext.width), int(ext.height)

budget = int(os.environ["BUDGET"])
fail = 0
for w, h in [(1366, 768), (1440, 900), (1920, 1080), (2294, 830), (2560, 1440), (2880, 1800), (3840, 2160)]:
    gap = ov.tile_gap(w, h, tw, th)
    surface = ov.render_watermark(w, h, tw + gap, th + gap)
    n = Gdk.cairo_region_create_from_surface(surface).num_rectangles()
    ok = n <= budget
    fail += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {w}x{h}: gap {gap:4d}px -> {n:5d} rectangles")
sys.exit(1 if fail else 0)
PY
) && status=0 || status=$?
printf '%s\n' "$out" | grep -E '^(OK  |FAIL)|Traceback|Error' || true
# Non-vacuity guard: a script that silently did nothing (empty stdin, import
# error swallowed) must not count as a pass.
checked=$(printf '%s\n' "$out" | grep -c -E '^(OK  |FAIL) ' || true)
if [ "$checked" -lt 7 ]; then
  echo "FAIL: expected 7 screen-size checks, saw ${checked} -- the check did not really run"
  printf '%s\n' "$out" | tail -15
  exit 1
fi
if [ "$status" -eq 0 ]; then echo "overlay shape budget: all ${checked} screen sizes within ${BUDGET}"; else echo "overlay shape budget EXCEEDED (see FAIL lines above)"; fi
exit "$status"
