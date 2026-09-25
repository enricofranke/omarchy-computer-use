"""MCP server (stdio, JSON-RPC 2.0) exposing the agent desktop as tools.

Stdlib only. stdout carries protocol messages exclusively; logs go to stderr.
"""

import base64
import copy
import json
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback

from . import __version__
from . import config as settings
from . import roles
from .computer import Computer
from .desk import DeskError
from .hypr import HyprError
from .wayland import WaylandError


def notify(message):
    """Desktop notification on the user's session; best effort."""
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "-a", "agentdesk", "-u", "critical", "Your turn on the agent desktop", message],
            capture_output=True, timeout=5,
        )

INSTRUCTIONS = """\
agentdesk gives you your own desktop: a separate Hyprland session with its own
cursor and keyboard, shown to the user as a window with a coloured frame. Your
clicks and typing only ever reach this desktop; the user's mouse and keyboard
are never touched, so they can keep working while you do.

Workflow: take a screenshot first, act with the `computer` tool (coordinates
are pixels in the latest screenshot), check the returned screenshot, repeat.
Use `open` to start a browser or app inside the desktop and `windows` to list,
focus, move, resize or maximize windows. The desktop starts on first use. The
arrow with your name in screenshots is your own cursor.

Dialogs (file pickers, save dialogs, password prompts of apps) open inside the
desktop like any other window; handle them there. Files live on the user's real
disk, so downloads land in ~/Downloads where your other tools can read them.

When a step needs the human (logging in, entering a password or payment data,
solving a CAPTCHA), call `desktop` with action `handover` and a short message.
The user takes over with their own mouse and keyboard; your input is blocked
until control comes back. Only use `reclaim` when the user said they are done.

When the task no longer needs the desktop, call `desktop` with action `stop`;
the live preview closes with it. Browser logins survive in the sandbox profile.
"""

COORD = {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}

TOOLS = [
    {
        "name": "computer",
        "description": (
            "Use the mouse and keyboard on your own sandbox desktop and take screenshots. "
            "Coordinates are [x, y] pixels from the top-left of the screenshot. Every action "
            "except cursor_position returns a fresh screenshot. key takes xdotool-style combos "
            "such as 'ctrl+l', 'Return', 'ctrl+shift+t'; several combos can be space-separated."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "screenshot", "left_click", "right_click", "middle_click",
                        "double_click", "triple_click", "mouse_move", "left_click_drag",
                        "left_mouse_down", "left_mouse_up", "scroll", "type", "key",
                        "cursor_position", "wait", "zoom",
                    ],
                },
                "coordinate": {**COORD, "description": "Target [x, y] for clicks, moves, scrolls and drag end."},
                "start_coordinate": {**COORD, "description": "Drag start [x, y] for left_click_drag."},
                "text": {"type": "string", "description": "Text for type, key combo(s) for key."},
                "scroll_direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "scroll_amount": {"type": "integer", "minimum": 1, "maximum": 30},
                "duration": {"type": "number", "minimum": 0, "maximum": 30, "description": "Seconds for wait."},
                "region": {
                    "type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4,
                    "description": "[x1, y1, x2, y2] to magnify for zoom.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "open",
        "description": (
            "Open a URL in the sandbox browser or run a shell command that starts an app inside "
            "the sandbox desktop. Browsers get a dedicated profile, separate from the user's."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open in the sandbox browser."},
                "command": {"type": "string", "description": "Shell command to launch, e.g. 'nautilus' or 'foot'."},
            },
        },
    },
    {
        "name": "windows",
        "description": (
            "List windows on the sandbox desktop or arrange one: focus, move (x, y), resize "
            "(width, height), maximize, center, fullscreen (toggle) or close. `window` is an id "
            "from list or a case-insensitive part of the title/class; default is the focused window."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "focus", "move", "resize", "maximize", "center", "fullscreen", "close"],
                },
                "window": {"type": "string"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "desktop",
        "description": (
            "Manage the sandbox desktop itself: status, start, stop, restart, show/hide its window "
            "on the user's screen (it keeps working while hidden), handover (ask the user to take "
            "over, e.g. to log in; pass a message), reclaim (take control back once the user "
            "says they are done) and preview (dock a live view next to the chat in Herdr)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "start", "stop", "restart", "show", "hide", "handover", "reclaim", "preview"],
                },
                "message": {"type": "string", "description": "What the user should do, for handover."},
            },
            "required": ["action"],
        },
    },
]


def _text(message):
    return {"type": "text", "text": message}


def _image(png):
    return {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"}


def _xy(args, key="coordinate"):
    value = args.get(key)
    if not (isinstance(value, list) and len(value) == 2):
        raise DeskError(f"{key} [x, y] is required for this action")
    return int(value[0]), int(value[1])


class Server:
    def __init__(self, role=None):
        self.computer = Computer()
        self.lock = threading.Lock()
        # `agentdesk mcp --role` wins over the agents table in the config.
        self.role_override = role
        self.client = ""
        self.listed = None  # role signature the agent last saw in tools/list
        self.outbox = []  # notifications to send after the current reply

    def role(self):
        return roles.role_for(settings.load(), self.client, self.role_override)

    def remember_client(self, info):
        """Keep a list of connected agents for `agentdesk agents`."""
        self.client = str(info.get("name") or "")
        try:
            role = self.role().name
        except (roles.Denied, RuntimeError) as exc:
            role = f"invalid ({exc})"
        path = settings.data_dir() / "agents.json"
        try:
            seen = json.loads(path.read_text()) if path.exists() else {}
            seen[self.client or "(unnamed)"] = {
                "version": str(info.get("version") or ""), "last_seen": time.time(), "role": role,
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(seen, indent=1))
        except (OSError, ValueError):
            pass

    def tools(self, role):
        """The tool list trimmed to what the role allows."""
        allowed = []
        for tool in TOOLS:
            tool = copy.deepcopy(tool)
            props = tool["inputSchema"]["properties"]
            if tool["name"] == "open":
                for key in ("url", "command"):
                    if not role.allows_action("open", key):
                        props.pop(key)
                if not props:
                    continue
            else:
                enum = [a for a in props["action"]["enum"] if role.allows_action(tool["name"], a)]
                if not enum:
                    continue
                props["action"]["enum"] = enum
            allowed.append(tool)
        return allowed

    def instructions(self):
        try:
            role = self.role()
        except (roles.Denied, RuntimeError) as exc:
            return INSTRUCTIONS + f"\nNo tool will work right now: {exc}\n"
        if role.permissions == set(roles.PERMISSIONS) and role.urls is None and role.commands is None:
            return INSTRUCTIONS
        denied = [p for p in roles.PERMISSIONS if p not in role.permissions]
        note = (
            f"\nYour role on this desktop is {role.name!r}. You may: {role.summary()}. "
            f"Not allowed: {', '.join(roles.PERMISSIONS[p] for p in denied) or 'nothing'}. "
            "Tools and actions outside your role are not offered. If a task needs one, tell "
            "the user instead of working around it.\n"
        )
        return INSTRUCTIONS + note

    def guard(self, name, args):
        """Refuse the call unless the agent's role allows it."""
        # Settings and roles are read fresh, so changes apply to running sessions.
        cfg = settings.load()
        self.computer.cfg = self.computer.desk.cfg = cfg
        role = roles.role_for(cfg, self.client, self.role_override)
        signature = (role.name, tuple(roles.ordered(role.permissions)))
        if self.listed is not None and signature != self.listed:
            self.listed = signature
            self.outbox.append({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        if name == "open":
            action = "url" if args.get("url") else "command" if args.get("command") else None
        else:
            action = args.get("action", {"windows": "list", "desktop": "status"}.get(name))
        if action:
            role.check(name, action, args)
        self.computer.may_start = role.allows("desktop.start")

    def start_desk(self, show=None):
        desk = self.computer.desk
        if not desk.state() and not self.computer.may_start:
            raise roles.Denied("the agent desktop is not running and your role may not start it; "
                               "ask the user to start it")
        return desk.start(show=show)

    def idle_watch(self):
        """Stop the desktop once the agent has left it alone for a while."""
        minutes = self.computer.cfg["idle_stop_minutes"]
        if minutes <= 0:
            return
        while True:
            time.sleep(30)
            desk = self.computer.desk
            with self.lock:
                try:
                    state = desk.state()
                    if not state or desk.controller() != "agent":
                        continue
                    try:
                        last = json.loads((settings.runtime_dir() / "activity.json").read_text())["at"]
                    except (OSError, ValueError, KeyError):
                        last = state["started"]
                    if time.time() - max(last, state["started"]) > minutes * 60:
                        print("agentdesk: stopping the idle desktop", file=sys.stderr)
                        self.computer.close()
                        desk.stop()
                except Exception:  # never let the watchdog kill the server
                    traceback.print_exc(file=sys.stderr)

    def shutdown(self):
        """Session over: stop a desktop this session started, unless the user has it."""
        computer = self.computer
        computer.close()
        try:
            if computer.cfg["stop_on_exit"] and computer.autostarted and computer.desk.state() \
                    and computer.desk.controller() == "agent":
                computer.desk.stop()
        except Exception:
            traceback.print_exc(file=sys.stderr)

    def settle_and_shoot(self, note):
        time.sleep(self.computer.cfg["settle_ms"] / 1000)
        return [_text(note), _image(self.computer.screenshot())]

    # --- tools ---------------------------------------------------------------

    def tool_computer(self, args):
        c = self.computer
        action = args.get("action")
        c.mark_activity(action)
        if action == "screenshot":
            width, height = c.size()
            return [_text(f"Screen {width}x{height}."), _image(c.screenshot())]
        if action == "cursor_position":
            x, y = c.cursor_position()
            return [_text(f"Cursor at [{x}, {y}].")]
        if action == "wait":
            time.sleep(min(30.0, float(args.get("duration", 1))))
            return [_text("Waited."), _image(c.screenshot())]
        if action == "zoom":
            region = args.get("region") or []
            if len(region) != 4:
                raise DeskError("zoom needs region [x1, y1, x2, y2]")
            return [_text(f"Zoomed into {region}."), _image(c.zoom(*map(int, region)))]
        if action in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
            x, y = _xy(args) if args.get("coordinate") else c.cursor_position()
            button = {"right_click": "right", "middle_click": "middle"}.get(action, "left")
            count = {"double_click": 2, "triple_click": 3}.get(action, 1)
            c.click(x, y, button=button, count=count)
            return self.settle_and_shoot(f"{action} at [{x}, {y}].")
        if action == "mouse_move":
            x, y = _xy(args)
            c.move(x, y)
            return self.settle_and_shoot(f"Moved to [{x}, {y}].")
        if action == "left_click_drag":
            x1, y1 = _xy(args, "start_coordinate") if args.get("start_coordinate") else c.cursor_position()
            x2, y2 = _xy(args)
            c.drag(x1, y1, x2, y2)
            return self.settle_and_shoot(f"Dragged [{x1}, {y1}] -> [{x2}, {y2}].")
        if action in ("left_mouse_down", "left_mouse_up"):
            if args.get("coordinate"):
                c.move(*_xy(args))
            c.mouse_button(action == "left_mouse_down")
            return self.settle_and_shoot(action.replace("_", " ") + ".")
        if action == "scroll":
            x, y = _xy(args) if args.get("coordinate") else c.cursor_position()
            direction = args.get("scroll_direction", "down")
            amount = int(args.get("scroll_amount", 3))
            c.scroll(x, y, direction, amount)
            return self.settle_and_shoot(f"Scrolled {direction} {amount} at [{x}, {y}].")
        if action == "type":
            text = args.get("text")
            if not text:
                raise DeskError("type needs text")
            c.type_text(text)
            return self.settle_and_shoot(f"Typed {len(text)} characters.")
        if action == "key":
            combo = args.get("text")
            if not combo:
                raise DeskError("key needs text, e.g. 'ctrl+l'")
            c.key(combo)
            return self.settle_and_shoot(f"Pressed {combo}.")
        raise DeskError(f"unknown action {action!r}")

    def tool_open(self, args):
        c = self.computer
        c.mark_activity("open")
        if args.get("url"):
            command = c.open_url(args["url"])
        elif args.get("command"):
            command = c.launch(args["command"])
        else:
            raise DeskError("open needs url or command")
        time.sleep(c.cfg["open_wait_ms"] / 1000)
        return [_text(f"Launched: {command}"), _image(c.screenshot())]

    def tool_windows(self, args):
        c = self.computer
        action = args.get("action", "list")
        if action == "list":
            wins = c.windows()
            width, height = c.size()
            return [_text(json.dumps({"screen": [width, height], "windows": wins}, indent=1))]
        c.mark_activity("windows")
        win = c.window_action(
            action, args.get("window"), args.get("x"), args.get("y"),
            args.get("width"), args.get("height"),
        )
        return self.settle_and_shoot(f"{action}: {win['title'] or win['class']}")

    def tool_desktop(self, args):
        desk = self.computer.desk
        action = args.get("action", "status")
        if action == "status":
            state = desk.state()
            if not state:
                return [_text("The agent desktop is not running.")]
            width, height = desk.screen_size(state)
            where = "visible on the user's screen" if desk.visible(state) else "hidden (still running)"
            who = "you have control" if desk.controller() == "agent" else "the USER has control"
            return [_text(f"Running, {width}x{height}, {where}, {who}.")]
        if action == "stop":
            self.computer.close()
            desk.stop()
            return [_text("Stopped the agent desktop.")]
        if action == "restart":
            self.computer.close()
            desk.stop()
            desk.start()
            return [_text("Restarted the agent desktop.")]
        if action == "preview":
            pane = self.computer.open_preview()
            return [_text(f"Preview docked in Herdr pane {pane}." if pane else
                          "No preview: this session does not run inside Herdr (or preview is off).")]
        if action == "start":
            desk.start()
            self.computer.open_preview()
        elif action == "show":
            self.start_desk(show=True)
            desk.show()
        elif action == "hide":
            desk.hide()
        elif action == "handover":
            self.start_desk()
            desk.give_to_user()
            note = args.get("message") or f"{self.computer.cfg['label'] or 'The agent'} needs you on its desktop."
            if self.computer.cfg["notify_handover"]:
                notify(note)
            return [_text(
                "Handed control to the user and showed the desktop. Your input is blocked "
                f"until they hand it back. They were told: {note}"
            )]
        elif action == "reclaim":
            desk.give_to_agent()
            return [_text("You have control of the desktop again."), _image(self.computer.screenshot())]
        else:
            raise DeskError(f"unknown desktop action {action!r}")
        return [_text(f"Done: {action}.")]

    # --- protocol ------------------------------------------------------------

    def handle(self, message):
        method = message.get("method")
        params = message.get("params") or {}
        if method == "initialize":
            self.remember_client(params.get("clientInfo") or {})
            return {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "agentdesk", "version": __version__},
                "instructions": self.instructions(),
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            try:
                role = self.role()
            except (roles.Denied, RuntimeError) as exc:
                print(f"agentdesk: {exc}", file=sys.stderr)
                return {"tools": []}
            self.listed = (role.name, tuple(roles.ordered(role.permissions)))
            return {"tools": self.tools(role)}
        if method == "tools/call":
            name = params.get("name", "")
            handler = getattr(self, "tool_" + name, None)
            if not handler:
                return {"content": [_text(f"Unknown tool {name}")], "isError": True}
            args = params.get("arguments") or {}
            try:
                self.guard(name, args)
                return {"content": handler(args)}
            except (DeskError, HyprError, WaylandError, roles.Denied, RuntimeError, ValueError, OSError) as exc:
                return {"content": [_text(f"Error: {exc}")], "isError": True}
            except Exception as exc:  # keep the server alive on bugs
                traceback.print_exc(file=sys.stderr)
                return {"content": [_text(f"Internal error: {exc!r}")], "isError": True}
        raise KeyError(method)


def serve(role=None):
    server = Server(role)
    threading.Thread(target=server.idle_watch, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: (server.shutdown(), sys.exit(0)))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "id" not in message:
            continue  # notifications need no reply
        reply = {"jsonrpc": "2.0", "id": message["id"]}
        try:
            with server.lock:
                reply["result"] = server.handle(message)
        except KeyError:
            reply["error"] = {"code": -32601, "message": f"Method not found: {message.get('method')}"}
        for note in server.outbox:
            sys.stdout.write(json.dumps(note) + "\n")
        server.outbox.clear()
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
    server.shutdown()
