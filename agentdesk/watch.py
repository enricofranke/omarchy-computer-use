"""Live preview of the agent desktop inside a terminal pane.

Meant for a small pane next to the agent's chat, e.g. a Herdr split. Three ways
to draw, picked automatically:

- Herdr's pane graphics API: a sharp image; needs `experimental.kitty_graphics`
  in Herdr's config.
- Kitty graphics escapes: a sharp image straight in Kitty/Ghostty/WezTerm.
- Coloured half blocks: works in any truecolor terminal, thumbnail quality.

Frames are only redrawn when the picture changed. Keys: s show/hide the desktop
window, t take over / hand back, q quit.
"""

import base64
import fcntl
import hashlib
import json
import os
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import time
import tty

from . import config as settings
from .desk import Desk, DeskError
from .hypr import HyprError

ESC = "\x1b"
MOUSE_ON = f"{ESC}[?1000h{ESC}[?1006h"
MOUSE_OFF = f"{ESC}[?1000l{ESC}[?1006l"
SGR_MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")


def _winsize():
    try:
        rows, cols, xpx, ypx = struct.unpack(
            "HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8))
    except OSError:
        cols, rows = shutil.get_terminal_size()
        xpx = ypx = 0
    return cols, rows, xpx, ypx


def _grim(state, scale, fmt):
    env = dict(os.environ, WAYLAND_DISPLAY=state["socket"])
    cmd = ["grim", "-t", fmt] + (["-l", "1"] if fmt == "png" else []) + ["-s", f"{scale:.4f}", "-"]
    return subprocess.run(cmd, env=env, capture_output=True, timeout=3, check=True).stdout


def _fit(sw, sh, cols, rows, cell_w, cell_h):
    """Largest cols x rows box with the screen's aspect ratio."""
    c = cols
    r = max(1, round(c * cell_w * sh / sw / cell_h))
    if r > rows:
        r = rows
        c = max(1, round(r * cell_h * sw / sh / cell_w))
    return c, r


def _ago(seconds):
    return f"{int(seconds)}s ago" if seconds < 60 else f"{int(seconds // 60)}m ago"


# --- renderers ---------------------------------------------------------------

class HerdrRenderer:
    """Pushes PNG frames through Herdr's pane.graphics API."""

    name = "herdr"

    def __init__(self, info):
        self.pane = os.environ["HERDR_PANE_ID"]
        self.cell_w = info["cell_width_px"]
        self.cell_h = info["cell_height_px"]

    @staticmethod
    def call(method, params):
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(5)
            sock.connect(os.environ["HERDR_SOCKET_PATH"])
            sock.sendall((json.dumps({"id": "agentdesk", "method": method, "params": params}) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf or b"{}")

    @classmethod
    def probe(cls):
        """(renderer or None, why not) for the current Herdr pane."""
        if os.environ.get("HERDR_ENV") != "1" or not os.environ.get("HERDR_SOCKET_PATH"):
            return None, ""
        try:
            reply = cls.call("pane.graphics.info", {"pane_id": os.environ.get("HERDR_PANE_ID", "")})
        except (OSError, ValueError):
            return None, ""
        if reply.get("result"):
            return cls(reply["result"]), ""
        code = (reply.get("error") or {}).get("code", "")
        if code == "feature_disabled":
            return None, "sharp preview: set experimental.kitty_graphics = true in herdr"
        if code == "cell_size_unavailable":
            return None, "sharp preview: re-attach herdr once (close and reopen its window)"
        return None, ""

    def frame(self, state, sw, sh, cols, rows, top):
        c, r = _fit(sw, sh, cols, rows, self.cell_w, self.cell_h)
        png = _grim(state, min(1.0, c * self.cell_w / sw), "png")
        width, height = struct.unpack(">II", png[16:24])

        def draw():
            self.call("pane.graphics.set", {
                "pane_id": self.pane, "layer_id": "agentdesk", "format": "png",
                "image_width": width, "image_height": height,
                "data_base64": base64.b64encode(png).decode(),
                "placement": {"grid_cols": c, "grid_rows": r,
                              "viewport_col": max(0, (cols - c) // 2), "viewport_row": top},
            })
            return ""
        return png, draw

    def clear(self):
        try:
            self.call("pane.graphics.clear", {"pane_id": self.pane, "layer_id": "agentdesk"})
        except OSError:
            pass
        return ""


class KittyRenderer:
    """Kitty graphics escapes; alternates two image ids to avoid flicker."""

    name = "kitty"

    def __init__(self):
        self.image_id = 1

    def frame(self, state, sw, sh, cols, rows, top):
        _, _, xpx, ypx = _winsize()
        full_cols, full_rows = shutil.get_terminal_size()
        cell_w = xpx / full_cols if xpx else 8.0
        cell_h = ypx / full_rows if ypx else 16.0
        c, r = _fit(sw, sh, cols, rows, cell_w, cell_h)
        png = _grim(state, min(1.0, c * cell_w / sw), "png")

        def draw():
            new_id = 2 if self.image_id == 1 else 1
            data = base64.b64encode(png).decode()
            parts = [data[i : i + 4096] for i in range(0, len(data), 4096)] or [""]
            out = [f"{ESC}[{top + 1};{max(0, (cols - c) // 2) + 1}H"]
            for i, part in enumerate(parts):
                more = int(i < len(parts) - 1)
                head = f"a=T,f=100,i={new_id},p=1,c={c},r={r},C=1,q=2," if i == 0 else ""
                out.append(f"{ESC}_G{head}m={more};{part}{ESC}\\")
            out.append(f"{ESC}_Ga=d,d=I,i={self.image_id},q=2{ESC}\\")
            self.image_id = new_id
            return "".join(out)
        return png, draw

    def clear(self):
        return f"{ESC}_Ga=d,d=A,q=2{ESC}\\"


class BlockRenderer:
    """Upper-half blocks with truecolor: two pixels per cell, any terminal."""

    name = "blocks"

    def frame(self, state, sw, sh, cols, rows, top):
        c, r = _fit(sw, sh, cols, rows, 1.0, 2.0)
        ppm = _grim(state, c / sw, "ppm")
        # P6 header: magic, width, height, maxval, each separated by whitespace.
        fields, pos = [], 0
        while len(fields) < 4:
            while ppm[pos : pos + 1].isspace():
                pos += 1
            start = pos
            while not ppm[pos : pos + 1].isspace():
                pos += 1
            fields.append(ppm[start:pos])
        width, height = int(fields[1]), int(fields[2])
        pixels = ppm[pos + 1 :]

        def draw():
            left = max(0, (cols - width) // 2) + 1
            out = []
            for y in range(0, min(height, r * 2), 2):
                out.append(f"{ESC}[{top + y // 2 + 1};{left}H")
                last = None
                for x in range(width):
                    i = (y * width + x) * 3
                    j = ((y + 1) * width + x) * 3 if y + 1 < height else i
                    colours = (pixels[i : i + 3], pixels[j : j + 3])
                    if colours != last:
                        (r1, g1, b1), (r2, g2, b2) = colours
                        out.append(f"{ESC}[38;2;{r1};{g1};{b1}m{ESC}[48;2;{r2};{g2};{b2}m")
                        last = colours
                    out.append("▀")
                out.append(f"{ESC}[0m")
            return "".join(out)
        return ppm, draw

    def clear(self):
        return ""


def pick_renderer():
    herdr, why = HerdrRenderer.probe()
    if herdr:
        return herdr, ""
    if os.environ.get("HERDR_ENV") == "1":
        return BlockRenderer(), why
    if os.environ.get("TERM_PROGRAM", "").lower() in ("ghostty", "kitty", "wezterm") \
            or os.environ.get("KITTY_WINDOW_ID"):
        return KittyRenderer(), ""
    return BlockRenderer(), ""


# --- viewer --------------------------------------------------------------------

class Viewer:
    def __init__(self, auto=False):
        # auto: opened by the agent; close once the desktop has gone away.
        self.auto = auto
        self.seen_running = False
        self.gone_since = None
        self.confirm_stop_until = 0.0
        self.buttons = []
        self.desk = Desk()
        self.label = self.desk.cfg["label"] or "Agent"
        self.activity_path = settings.runtime_dir() / "activity.json"
        self.renderer, self.hint = pick_renderer()
        self.last_probe = time.time()
        self.last_hash = None
        self.last_status = None
        self.dirty = True

    def write(self, text):
        sys.stdout.write(text)
        sys.stdout.flush()

    def activity(self):
        try:
            data = json.loads(self.activity_path.read_text())
            return data.get("action", ""), time.time() - float(data.get("at", 0))
        except (OSError, ValueError):
            return "", 1e9

    def status_line(self, state):
        if not state:
            return f"○ {self.label}'s desktop is not running"
        action, age = self.activity()
        if self.desk.controller() == "user":
            return "◆ You have control · t: hand back"
        if age < 6:
            return f"● {self.label} is working · {action} · {_ago(age)}"
        where = "on screen" if self.desk.visible(state) else "in the background"
        return f"○ {self.label} idle {where}" + (f" · last: {action} {_ago(age)}" if action else "")

    def upgrade(self):
        """Switch to sharp Herdr graphics as soon as they become available."""
        if self.renderer.name != "blocks" or not self.hint or time.time() - self.last_probe < 5:
            return
        self.last_probe = time.time()
        herdr, why = HerdrRenderer.probe()
        if herdr:
            self.renderer, self.hint, self.dirty = herdr, "", True
        elif why != self.hint:
            self.hint, self.last_status = why, None

    def footer(self, state, cols):
        """Clickable buttons; also remembers their columns for mouse hits."""
        if not state:
            items = [("Close", "q")]
        else:
            stop = "Stop? click again" if time.time() < self.confirm_stop_until else "Stop"
            items = [
                ("Hand back" if self.desk.controller() == "user" else "Take over", "t"),
                ("Hide" if self.desk.visible(state) else "Show", "s"),
                (stop, "x"),
                ("Close", "q"),
            ]
        text, self.buttons, col = "", [], 1
        for label, key in items:
            chunk = f" {label} [{key}] "
            if col + len(chunk) > cols:
                break
            self.buttons.append((col, col + len(chunk) - 1, key))
            text += f"{ESC}[7m{chunk}{ESC}[0m "
            col += len(chunk) + 1
        if self.hint and col + len(self.hint) + 2 <= cols:
            text += f"{ESC}[2m {self.hint}{ESC}[0m"
        return text

    def draw(self):
        self.upgrade()
        cols, rows = shutil.get_terminal_size()
        state = self.desk.state()
        if state:
            self.seen_running, self.gone_since = True, None
        elif self.gone_since is None:
            self.gone_since = time.time()
        status = self.status_line(state)[:cols]
        footer = self.footer(state, cols)

        frame, render = None, None
        if state:
            try:
                sw, sh = self.desk.screen_size(state)
                frame, render = self.renderer.frame(state, sw, sh, cols, max(1, rows - 2), 1)
            except (subprocess.SubprocessError, DeskError, HyprError, OSError, ValueError):
                frame = None
        digest = hashlib.sha1(frame).hexdigest() if frame else None
        if not self.dirty and digest == self.last_hash and (status, footer) == self.last_status:
            return

        out = [f"{ESC}[1;1H{ESC}[2K{ESC}[1m{status}{ESC}[0m"]
        if frame and (self.dirty or digest != self.last_hash):
            out.append(render() or "")
        elif not frame:
            out.append(self.renderer.clear() + f"{ESC}[2;1H{ESC}[J")
        out.append(f"{ESC}[{rows};1H{ESC}[2K{footer}")
        self.write("".join(out))
        self.last_hash, self.last_status, self.dirty = digest, (status, footer), False

    def should_close(self):
        if not self.auto or self.gone_since is None:
            return False
        # Give a restart a moment; close quickly once a running desktop stopped.
        grace = 3 if self.seen_running else 20
        return time.time() - self.gone_since > grace

    def handle_input(self, data):
        """Keys and SGR mouse clicks (on the button row or the picture)."""
        for match in SGR_MOUSE.finditer(data):
            button, col, row, kind = int(match[1]), int(match[2]), int(match[3]), match[4]
            if kind != "M" or button != 0:
                continue
            _, rows = shutil.get_terminal_size()
            if row == rows:
                for start, end, key in self.buttons:
                    if start <= col <= end:
                        if self.handle_key(key):
                            return True
            elif row > 1:
                self.handle_key("s")
        for key in SGR_MOUSE.sub("", data).lower():
            if self.handle_key(key):
                return True
        return False

    def handle_key(self, key):
        """Returns True when the viewer should exit."""
        if key in ("q", "\x03"):
            return True
        try:
            if key == "x" and self.desk.state():
                if time.time() < self.confirm_stop_until:
                    self.desk.stop()
                else:
                    self.confirm_stop_until = time.time() + 3
            elif key == "s" and self.desk.state():
                self.desk.toggle()
            elif key == "t":
                if self.desk.controller() == "agent":
                    self.desk.start()
                    self.desk.give_to_user()
                elif self.desk.state():
                    self.desk.give_to_agent()
        except (DeskError, HyprError):
            pass
        self.last_status = None
        return False

    def run(self):
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "dirty", True))
        self.write(f"{ESC}]2;agentdesk preview\a{ESC}[?25l{ESC}[2J{MOUSE_ON}")
        try:
            tty.setcbreak(fd)
            while not self.should_close():
                if self.dirty:
                    self.write(self.renderer.clear() + f"{ESC}[2J")
                self.draw()
                _, age = self.activity()
                sharp = self.renderer.name != "blocks"
                # Near-video while the agent acts, relaxed when idle.
                timeout = (0.08 if sharp else 0.3) if age < 6 else (0.5 if sharp else 0.8)
                ready, _, _ = select.select([fd], [], [], timeout)
                if ready and self.handle_input(os.read(fd, 256).decode(errors="ignore")):
                    break
        except KeyboardInterrupt:
            pass
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            self.write(MOUSE_OFF + self.renderer.clear() + f"{ESC}[2J{ESC}[H{ESC}[?25h")


def main(auto=False):
    Viewer(auto=auto).run()
