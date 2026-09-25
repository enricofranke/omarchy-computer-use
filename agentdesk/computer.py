"""Mouse, keyboard and screen of the sandbox desktop."""

import json
import math
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from . import config as settings
from .desk import Desk, DeskError
from .hypr import HyprError, lua_str, lua_table
from .wayland import AXIS_HORIZONTAL, AXIS_VERTICAL, BUTTONS, VirtualPointer, WaylandError

MODIFIERS = {
    "ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "altgr": "altgr",
    "super": "logo", "meta": "logo", "cmd": "logo", "win": "logo", "logo": "logo",
}

HYPR_MODS = {"ctrl": "CTRL", "shift": "SHIFT", "alt": "ALT", "altgr": "MOD5", "logo": "SUPER"}

KEY_ALIASES = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "backspace": "BackSpace", "delete": "Delete", "del": "Delete",
    "space": "space", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Page_Up", "page_up": "Page_Up",
    "pagedown": "Page_Down", "page_down": "Page_Down", "insert": "Insert",
    "plus": "plus", "minus": "minus", "print": "Print",
}

CHROMIUM_FAMILY = ("chromium", "google-chrome-stable", "google-chrome", "brave", "brave-browser")


def _ease(t):
    return 4 * t**3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def _keysym(name):
    lowered = name.lower()
    if lowered in KEY_ALIASES:
        return KEY_ALIASES[lowered]
    if len(lowered) in (2, 3) and lowered[0] == "f" and lowered[1:].isdigit():
        return name.upper()
    return name


class Computer:
    def __init__(self, desk=None, agent=True):
        self.desk = desk or Desk()
        # A takeover only blocks the agent. The command line is the user's.
        self.agent = agent
        self.cfg = self.desk.cfg
        self._pointer = None
        self._pointer_key = None
        self._last_render_check = 0.0
        # True once this process started the desktop on demand.
        self.autostarted = False
        # False when the agent's role may not start the desktop.
        self.may_start = True

    # --- plumbing ----------------------------------------------------------

    def state(self, autostart=True):
        state = self.desk.state()
        if not state and autostart:
            if not self.may_start:
                raise DeskError("the agent desktop is not running and your role may not start it; "
                                "ask the user to start it")
            # With a live preview docked next to the chat, the big window
            # would only get in the user's way; show it only without one.
            state = self.desk.start(show=False)
            self.autostarted = True
            if not self.open_preview() and not self.cfg["start_hidden"]:
                self.desk.show()
        if not state:
            raise DeskError("the agent desktop is not running")
        return self.desk.require()

    def _env(self, state):
        env = dict(os.environ)
        env["WAYLAND_DISPLAY"] = state["socket"]
        return env

    def pointer(self, state):
        width, height = self.desk.screen_size(state)
        key = (state["instance"], width, height)
        if self._pointer is None or self._pointer_key != key:
            self.close()
            self._pointer = VirtualPointer(state["socket"], width, height)
            self._pointer_key = key
        return self._pointer

    def close(self):
        if self._pointer:
            self._pointer.close()
        self._pointer = None
        self._pointer_key = None

    def require_control(self):
        if self.agent and self.desk.controller() == "user":
            raise DeskError(
                "the user has taken over the desktop. Wait, or ask them to hand control "
                "back (bar icon right-click, 't' in the preview, or `agentdesk release`)."
            )

    def _with_pointer(self, action):
        self.require_control()
        state = self.state()
        try:
            return action(self.pointer(state), state)
        except (OSError, WaylandError):
            # The sandbox may have restarted under us; reconnect once.
            self.close()
            return action(self.pointer(state), state)

    def open_preview(self):
        if self.cfg["preview"]:
            from . import herdr
            return herdr.open_preview()
        return None

    def mark_activity(self, action):
        path = settings.runtime_dir() / "activity.json"
        path.write_text(json.dumps({"action": action, "at": time.time()}))

    # --- screen ------------------------------------------------------------

    def size(self):
        return self.desk.screen_size(self.state())

    def screenshot(self, region=None, scale=None, cursor=None):
        state = self.state()
        if time.time() - self._last_render_check > 15:
            self.desk.ensure_rendering(state)
            self._last_render_check = time.time()
        cmd = ["grim", "-t", "png", "-l", "1"]
        if cursor if cursor is not None else self.cfg["screenshot_cursor"]:
            cmd.append("-c")
        if region:
            x, y, w, h = region
            cmd += ["-g", f"{x},{y} {w}x{h}"]
        if scale:
            cmd += ["-s", f"{scale:g}"]
        cmd.append("-")
        for attempt in range(2):
            try:
                return subprocess.run(
                    cmd, env=self._env(state), capture_output=True, check=True, timeout=4
                ).stdout
            except subprocess.TimeoutExpired:
                # The window sits somewhere the host stopped rendering (e.g. a
                # closed special workspace). Park it where rendering continues.
                if attempt == 0:
                    self.desk.hide()
                    self.desk.style_window(state)
                    time.sleep(0.3)
            except subprocess.CalledProcessError as exc:
                raise DeskError(f"screenshot failed: {exc.stderr.decode(errors='replace')}") from exc
        raise DeskError("screenshot timed out; the agent desktop is not rendering")

    def zoom(self, x1, y1, x2, y2):
        width, height = self.size()
        x1, x2 = sorted((max(0, x1), min(width, x2)))
        y1, y2 = sorted((max(0, y1), min(height, y2)))
        w, h = max(1, x2 - x1), max(1, y2 - y1)
        scale = max(1.0, min(4.0, width / w, height / h))
        return self.screenshot(region=(x1, y1, w, h), scale=scale)

    # --- mouse ---------------------------------------------------------------

    def cursor_position(self):
        state = self.state()
        pos = self.desk.sandbox(state).json("cursorpos")
        return int(pos["x"]), int(pos["y"])

    def move(self, x, y, glide=True):
        def run(pointer, state):
            longest = self.cfg["cursor_glide_ms"] / 1000
            if glide and longest > 0:
                try:
                    sx, sy = self.cursor_position()
                except (DeskError, KeyError, ValueError):
                    sx, sy = x, y
                distance = math.hypot(x - sx, y - sy)
                if distance > 3:
                    duration = min(longest, 0.12 + distance / 3000)
                    steps = max(6, int(duration * 90))
                    for i in range(1, steps):
                        e = _ease(i / steps)
                        pointer.motion(sx + (x - sx) * e, sy + (y - sy) * e, sync=False)
                        time.sleep(duration / steps)
            pointer.motion(x, y)
        self._with_pointer(run)

    def click(self, x, y, button="left", count=1):
        self.move(x, y)
        code = BUTTONS[button]

        def run(pointer, state):
            time.sleep(0.04)
            for i in range(count):
                pointer.button(code, True)
                time.sleep(0.03)
                pointer.button(code, False)
                if i + 1 < count:
                    time.sleep(0.06)
        self._with_pointer(run)

    def mouse_button(self, pressed, button="left"):
        code = BUTTONS[button]
        self._with_pointer(lambda pointer, state: pointer.button(code, pressed))

    def drag(self, x1, y1, x2, y2):
        self.move(x1, y1)
        self.mouse_button(True)
        time.sleep(0.08)
        # Small first step so apps register the drag before the long glide.
        self._with_pointer(lambda pointer, state: pointer.motion(x1 + (4 if x2 >= x1 else -4), y1))
        self.move(x2, y2)
        time.sleep(0.08)
        self.mouse_button(False)

    def scroll(self, x, y, direction, amount=3):
        self.move(x, y)
        axis = AXIS_VERTICAL if direction in ("up", "down") else AXIS_HORIZONTAL
        clicks = amount if direction in ("down", "right") else -amount
        self._with_pointer(lambda pointer, state: pointer.scroll(axis, clicks))

    # --- keyboard ------------------------------------------------------------

    def _wtype(self, args, stdin=None):
        if not shutil.which("wtype"):
            raise DeskError("wtype is not installed")
        self.require_control()
        state = self.state()
        subprocess.run(
            ["wtype"] + args, env=self._env(state), input=stdin, text=True,
            check=True, capture_output=True, timeout=60,
        )

    def type_text(self, text):
        """Enter text through the sandbox's own clipboard; newlines press Return.

        Keystroke typing via wtype maps characters onto keycodes of real keys
        (the 14th distinct character lands on Backspace), and Chromium's
        address bar and GTK dialogs act on those physical codes. Pasting is
        immune to that and handles any Unicode. The clipboard is the sandbox's,
        not the user's.
        """
        self.require_control()
        if not shutil.which("wl-copy"):
            self._wtype(["-d", "4", "-"], stdin=text)
            return
        state = self.state()
        sandbox = self.desk.sandbox(state)
        focused = (sandbox.json("activewindow") or {}).get("class", "").lower()
        terminal = any(t.lower() in focused for t in self.cfg["terminal_classes"] if t)
        mods = "CTRL SHIFT" if terminal else "CTRL"
        for i, line in enumerate(text.split("\n")):
            if i:
                self.key("Return")
            if not line:
                continue
            subprocess.run(
                ["wl-copy"], input=line, text=True, env=self._env(state),
                check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(0.05)
            sandbox.dispatch("hl.dsp.send_shortcut(" + lua_table({"mods": mods, "key": "v"}) + ")")
            time.sleep(0.08)

    def key(self, combos):
        """xdotool-style combos, space separated: 'ctrl+l', 'ctrl+shift+t Return'.

        Sent through the sandbox compositor's own keyboard (send_shortcut), which
        uses the real keymap. wtype's per-call keymap swap makes GTK4 drop keys
        such as Return, so it only serves as a fallback.
        """
        self.require_control()
        state = self.state()
        sandbox = self.desk.sandbox(state)
        for combo in combos.split():
            parts = [p for p in combo.split("+") if p]
            mods = [MODIFIERS[p.lower()] for p in parts if p.lower() in MODIFIERS]
            keys = [_keysym(p) for p in parts if p.lower() not in MODIFIERS]
            if not keys and not mods:
                raise DeskError(f"cannot parse key combo {combo!r}")
            try:
                if len(keys) != 1:
                    raise HyprError("not a single key")
                key = keys[0].lower() if len(keys[0]) == 1 else keys[0]
                sandbox.dispatch("hl.dsp.send_shortcut(" + lua_table({
                    "mods": " ".join(HYPR_MODS[m] for m in mods), "key": key,
                }) + ")")
            except HyprError:
                args = []
                for mod in mods:
                    args += ["-M", mod]
                for key in keys:
                    args += ["-k", key]
                for mod in reversed(mods):
                    args += ["-m", mod]
                self._wtype(args)
            time.sleep(0.05)

    # --- apps & windows --------------------------------------------------------

    def browser(self):
        if self.cfg["browser"]:
            return self.cfg["browser"]
        for candidate in CHROMIUM_FAMILY + ("firefox",):
            if shutil.which(candidate):
                return candidate
        raise DeskError("no browser found; set \"browser\" in ~/.config/agentdesk/config.json")

    def launch(self, command):
        self.require_control()
        state = self.state()
        command = self._isolate_browser(command)
        self.desk.sandbox(state).dispatch(f"hl.dsp.exec_cmd({lua_str(command)})")
        return command

    def open_url(self, url):
        argv = [self.browser(), *self.cfg["browser_args"], url]
        return self.launch(" ".join(shlex.quote(a) for a in argv))

    def _isolate_browser(self, command):
        """Give browsers their own profile so they never attach to your running one."""
        try:
            argv = shlex.split(command)
        except ValueError:
            return command
        if not argv:
            return command
        name = Path(argv[0]).name
        profiles = settings.data_dir() / "profiles"
        if name in CHROMIUM_FAMILY and not any(a.startswith("--user-data-dir") for a in argv):
            extra = [
                f"--user-data-dir={profiles / name}", "--no-first-run",
                "--no-default-browser-check", "--ozone-platform=wayland",
                # The sandbox ends by closing its display, which Chromium
                # counts as a crash; skip the restore prompt next time.
                "--hide-crash-restore-bubble",
            ]
            return " ".join(shlex.quote(a) for a in argv[:1] + extra + argv[1:])
        if name == "firefox" and "--profile" not in argv and "-profile" not in argv:
            (profiles / "firefox").mkdir(parents=True, exist_ok=True)
            extra = ["--no-remote", "--profile", str(profiles / "firefox")]
            return " ".join(shlex.quote(a) for a in argv[:1] + extra + argv[1:])
        return command

    def windows(self):
        state = self.state()
        sandbox = self.desk.sandbox(state)
        active = (sandbox.json("activewindow") or {}).get("address")
        result = []
        for c in sandbox.clients():
            if c.get("hidden"):
                continue
            result.append({
                "id": c["address"],
                "class": c.get("class", ""),
                "title": c.get("title", ""),
                "x": c["at"][0], "y": c["at"][1],
                "width": c["size"][0], "height": c["size"][1],
                "floating": c.get("floating", False),
                "fullscreen": bool(c.get("fullscreen")),
                "focused": c["address"] == active,
            })
        return result

    def find_window(self, query=None):
        wins = self.windows()
        if not wins:
            raise DeskError("no windows are open on the agent desktop")
        if not query:
            focused = [w for w in wins if w["focused"]]
            return focused[0] if focused else wins[0]
        for w in wins:
            if w["id"] == query:
                return w
        q = query.lower()
        matches = [w for w in wins if q in w["title"].lower() or q in w["class"].lower()]
        if not matches:
            raise DeskError(f"no window matches {query!r}")
        return matches[0]

    def window_action(self, action, query=None, x=None, y=None, width=None, height=None):
        self.require_control()
        state = self.state()
        sandbox = self.desk.sandbox(state)
        win = self.find_window(query)
        sel = f"address:{win['id']}"

        def dsp(name, **args):
            sandbox.dispatch(f"hl.dsp.{name}(" + lua_table({"window": sel, **args}) + ")")

        if action == "focus":
            dsp("focus")
        elif action in ("move", "resize", "maximize", "center") and not win["floating"]:
            dsp("window.float", action="enable")
        if action == "move":
            if x is None or y is None:
                raise DeskError("move needs x and y")
            dsp("window.move", x=int(x), y=int(y))
        # Hyprland resizes floating windows around their centre, so move them
        # back afterwards to keep the top-left corner where the caller expects.
        elif action == "resize":
            if width is None or height is None:
                raise DeskError("resize needs width and height")
            dsp("window.resize", x=int(width), y=int(height))
            dsp("window.move", x=win["x"], y=win["y"])
        elif action == "maximize":
            screen_w, screen_h = self.desk.screen_size(state)
            dsp("window.resize", x=screen_w, y=screen_h)
            dsp("window.move", x=0, y=0)
        elif action == "center":
            dsp("window.center")
        elif action == "fullscreen":
            dsp("window.fullscreen", mode="fullscreen", action="toggle")
        elif action == "close":
            dsp("window.close")
        elif action != "focus":
            raise DeskError(f"unknown window action {action!r}")
        return win
