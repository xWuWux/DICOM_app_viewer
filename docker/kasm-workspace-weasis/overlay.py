#!/usr/bin/env python3
"""
Forensic watermark overlay (issue #6, milestone "Weasis viewer migration").

A transparent, always-on-top, click-through native window tiled with
"STUDENT_ID | SESSION_ID | timestamp" text, covering the whole screen --
the native-desktop equivalent of watermark.html's/grading-panel.html's own
DOM watermark, needed here because Weasis is a separate native window that
a browser-page watermark can never cover.

How this actually works, verified empirically (not assumed from docs):
- No compositor (e.g. picom) runs in this XFCE/KasmVNC session by default,
  so CSS-style mix-blend-mode:difference has no real X11 equivalent here
  -- that trick only works within one browser's own single rendering
  pipeline, not across independently-rendered X11 windows. The practical
  substitute real overlay/subtitle tools use instead: a dark stroke +
  light fill "halo" around each glyph, so at least one of the two stays
  legible against any background, light or dark. Documented here rather
  than silently claiming the same guarantee the web watermark has.
- "Transparent" is achieved with the X Shape extension (GDK's
  shape_combine_region), not alpha compositing -- only the actual glyph
  pixels are part of the window at all, so no compositor is needed for
  the rest of the screen to show through genuinely unobstructed.
  Confirmed with a screenshot against a solid-color test background.
- Click-through uses input_shape_combine_region() with an *empty* region,
  independent of the bounding shape above -- every click, including one
  aimed directly at rendered watermark text, must reach whatever is
  underneath (Weasis, the grading panel). Confirmed with a real click
  landing on a test button placed underneath, at a point directly on top
  of rendered glyph pixels.
- Always-on-top uses Gtk.WindowType.POPUP (override-redirect -- outside
  window manager stacking control entirely, the strongest guarantee
  available on X11) plus set_keep_above()/stick(). Confirmed it stays
  above a window mapped after it, under a real xfwm4 session (not just
  bare Xvfb, which has no window manager and so nothing to test the
  stacking guarantee against).

Explicit "deterrence, not prevention" framing, matching CLAUDE.md and the
web watermark's own README section: this does not, and cannot, make the
overlay literally unkillable -- nothing running as an ordinary process in
a Linux container can be. What it does is make killing it pointless: see
watchdog.sh, which relaunches it within about a second every time it dies.
"""
import datetime
import math
import os

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

STUDENT_ID = os.environ.get("STUDENT_ID", "UNKNOWN_STUDENT")
SESSION_ID = os.environ.get("SESSION_ID", "UNKNOWN_SESSION")

FONT_SIZE = 15
ANGLE_DEG = -15
TILE_GAP = 70  # sparse tiling on purpose -- a mark, not a wall of text


def watermark_text():
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{STUDENT_ID} | {SESSION_ID} | {ts}"


class Overlay(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        screen = self.get_screen()
        self.width = screen.get_width()
        self.height = screen.get_height()
        self.set_default_size(self.width, self.height)
        self.move(0, 0)
        self.set_decorated(False)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_app_paintable(True)

        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        # Tile spacing is derived from the actual rendered width of a
        # representative string, not guessed -- a fixed guess that's
        # narrower than the real text overlaps adjacent copies and garbles
        # legibility (student/session ID length varies; only the timestamp
        # portion has a fixed, known width, so a placeholder of the same
        # length stands in for it here).
        probe_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        probe_ctx = cairo.Context(probe_surface)
        probe_ctx.select_font_face(
            "monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD
        )
        probe_ctx.set_font_size(FONT_SIZE)
        probe_text = f"{STUDENT_ID} | {SESSION_ID} | 0000-00-00T00:00:00Z"
        extents = probe_ctx.text_extents(probe_text)
        self.cell_w = int(extents.width) + TILE_GAP
        self.cell_h = int(extents.height) + TILE_GAP

        self.connect("draw", self.on_draw)
        self.show_all()

        gdk_window = self.get_window()
        gdk_window.set_override_redirect(True)
        self.stick()
        self.set_keep_above(True)

        GLib.timeout_add(1000, self.tick)

    def tick(self):
        self.queue_draw()
        return True

    def on_draw(self, widget, cr):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.width, self.height)
        ctx = cairo.Context(surface)
        ctx.set_operator(cairo.OPERATOR_CLEAR)
        ctx.paint()
        ctx.set_operator(cairo.OPERATOR_OVER)

        ctx.save()
        # Rotate around the canvas center, same -15deg the web watermark
        # uses, then tile across an area larger than the screen so corners
        # stay covered after rotation.
        ctx.translate(self.width / 2, self.height / 2)
        ctx.rotate(ANGLE_DEG * math.pi / 180)
        ctx.translate(-self.width / 2, -self.height / 2)

        text = watermark_text()
        ctx.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(FONT_SIZE)

        span = int(max(self.width, self.height) * 1.6)
        start = -span // 2
        y = start
        while y < span:
            x = start
            while x < span:
                ctx.move_to(x, y)
                ctx.text_path(text)
                ctx.set_source_rgba(0, 0, 0, 0.9)
                ctx.set_line_width(2.2)
                ctx.stroke_preserve()
                ctx.set_source_rgba(1, 1, 1, 0.85)
                ctx.fill()
                x += self.cell_w
            y += self.cell_h
        ctx.restore()

        cr.set_source_surface(surface, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()

        gdk_window = self.get_window()
        region = Gdk.cairo_region_create_from_surface(surface)
        gdk_window.shape_combine_region(region, 0, 0)
        gdk_window.input_shape_combine_region(cairo.Region(), 0, 0)
        return False


if __name__ == "__main__":
    win = Overlay()
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()
