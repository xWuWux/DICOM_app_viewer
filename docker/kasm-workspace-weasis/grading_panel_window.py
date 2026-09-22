#!/usr/bin/env python3
"""
Grading panel window for the Weasis flow -- the real gap found during the
first genuine Kasm click-through test: grading-panel.html (issue #5) was
built and works, but nothing in this workspace ever displayed it. Weasis is
a separate native window, so it needs its own small window beside it,
matching README.md's description of the intended design.

Deliberately NOT a general-purpose browser (no Chromium/Firefox/Epiphany
install): this workspace's whole base image was chosen specifically
*without* one to minimize attack surface (see Dockerfile's own comment),
and issue #7 spent real effort locking down Weasis's own save/export
menus. Installing a full browser here would reopen exactly that surface
(devtools, "save page as", print-to-file, arbitrary navigation) through a
different door. Instead: a single WebKit2GTK WebView, no browser chrome at
all (no address bar, no menu, no tabs -- there is nothing to build one
from since none is created), hard-restricted to the one page it's told to
load.

What's actually locked down, and how:
  - Context menu ("Inspect Element", "View Page Source", "Save Image
    As", etc.) -- suppressed entirely by returning True from the
    "context-menu" signal handler (WebKit2's own documented way to
    veto showing it, not a workaround).
  - Developer tools -- WebKit2.Settings.set_enable_developer_extras(False)
    (the pref that backs the Inspect Element entry in the first place;
    belt-and-suspenders with the context-menu suppression above).
  - New windows/tabs (target="_blank", window.open(), ctrl-click) --
    "create" signal returns None, and JavaScript-initiated popups are
    separately blocked via set_javascript_can_open_windows_automatically.
  - Navigation away from the assigned page -- "decide-policy" for
    WEBKIT_POLICY_DECISION_TYPE_NAVIGATION_ACTION checks the target URI
    against this session's own origin and ignores anything else. Same-
    origin XHR/fetch calls (grading-api via the /api/ proxy) are never
    navigation actions, so this doesn't interfere with the page's own
    logic.
  - Common devtools/save/print keyboard shortcuts -- a "key-press-event"
    handler on the window itself blocks F12, Ctrl+U (view-source),
    Ctrl+S (save), Ctrl+P (print), and Ctrl+Shift+I/J/C (devtools
    variants), independent of whether WebKit itself would have acted on
    them. Defense in depth, not the primary control.
Explicitly NOT claimed as airtight -- same "deterrence, not prevention"
framing as overlay.py and CLAUDE.md's own security note: this narrows the
native-browser-chrome attack surface to effectively nothing, it doesn't
make screen capture or determined tampering impossible.

Positioning: a normal, decorated, user-movable/resizable window pinned to
the right edge of the screen at a fixed width on startup -- not an
always-on-top override-redirect window like overlay.py's watermark (that
would fight with Weasis for focus). If Weasis's own window covers it, the
user can move/resize either window manually via xfwm4's own title bar and
edges, same as any two ordinary windows. Automatically tiling the two
windows against each other was considered and deliberately left out of
this pass -- it needs xdotool/wmctrl (not present in this base image) to
drive Weasis's own window geometry from outside, and the manual-arrange
fallback is a reasonable MVP trade rather than added complexity for a
cosmetic-only improvement.
"""
import os
import sys
import urllib.parse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, Gtk, WebKit2  # noqa: E402

VIEWER_URL = os.environ.get("VIEWER_URL")
if not VIEWER_URL:
    print("grading_panel_window.py: VIEWER_URL is required", file=sys.stderr)
    sys.exit(1)

GRADING_TOKEN = os.environ.get("GRADING_TOKEN")
if not GRADING_TOKEN:
    print("grading_panel_window.py: GRADING_TOKEN is required", file=sys.stderr)
    sys.exit(1)

STUDENT_ID = os.environ.get("STUDENT_ID", "UNKNOWN_STUDENT")
SESSION_ID = os.environ.get("SESSION_ID", "UNKNOWN_SESSION")

PANEL_WIDTH = 420

_query = urllib.parse.urlencode(
    {"token": GRADING_TOKEN, "student_id": STUDENT_ID, "session_id": SESSION_ID}
)
PANEL_URL = urllib.parse.urljoin(VIEWER_URL, "grading-panel.html") + "?" + _query
# Same-origin check for the navigation lockdown below -- anything the page
# itself fetches (grading-api via /api/) is an XHR, not a navigation, so
# this only ever has to match top-level page loads.
_ALLOWED_ORIGIN = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(PANEL_URL))

_BLOCKED_ACCELS = {
    (Gdk.KEY_F12, 0),
    (Gdk.KEY_u, Gdk.ModifierType.CONTROL_MASK),
    (Gdk.KEY_s, Gdk.ModifierType.CONTROL_MASK),
    (Gdk.KEY_p, Gdk.ModifierType.CONTROL_MASK),
    (Gdk.KEY_i, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK),
    (Gdk.KEY_j, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK),
    (Gdk.KEY_c, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK),
}


def on_key_press(_widget, event):
    key = Gdk.keyval_to_lower(event.keyval)
    mods = event.state & (
        Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
    )
    return (key, mods) in _BLOCKED_ACCELS


def on_context_menu(*_args):
    return True  # veto: never show the default context menu


def on_create(*_args):
    return None  # veto: never open a new window/tab for this WebView


def on_decide_policy(_webview, decision, decision_type):
    if decision_type != WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
        return False
    uri = decision.get_navigation_action().get_request().get_uri()
    if uri.startswith(_ALLOWED_ORIGIN):
        decision.use()
    else:
        decision.ignore()
    return True


def main():
    win = Gtk.Window(title="IP_CMC Grading Panel")
    win.set_default_size(PANEL_WIDTH, 900)
    screen = win.get_screen()
    win.move(screen.get_width() - PANEL_WIDTH, 0)
    win.connect("destroy", Gtk.main_quit)
    win.connect("key-press-event", on_key_press)

    webview = WebKit2.WebView()
    settings = webview.get_settings()
    settings.set_enable_developer_extras(False)
    settings.set_javascript_can_open_windows_automatically(False)
    settings.set_allow_file_access_from_file_urls(False)
    webview.connect("context-menu", on_context_menu)
    webview.connect("create", on_create)
    webview.connect("decide-policy", on_decide_policy)
    webview.load_uri(PANEL_URL)

    win.add(webview)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
