"""Settings: defaults, overridden by ~/.config/agentdesk/config.json, then env.

`agentdesk config` and `agentdesk role` edit the file. Every key can also be
set as AGENTDESK_<KEY> in the environment; lists and objects as JSON.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Option:
    key: str
    default: object
    help: str
    # Takes effect only after `agentdesk restart`.
    restart: bool = False
    choices: tuple = ()
    minimum: float = None
    maximum: float = None


OPTIONS = [
    # Look of the sandbox
    Option("width", 1280, "Sandbox screen width in pixels. Screenshots map 1:1 to click coordinates",
           restart=True, minimum=320, maximum=7680),
    Option("height", 800, "Sandbox screen height in pixels", restart=True, minimum=240, maximum=4320),
    Option("accent", "#ff7a1a", "Colour of the agent cursor and of the window frame", restart=True),
    Option("takeover_accent", "#3d9bff", "Frame and bar icon colour while you have taken over"),
    Option("label", "Claude", "Name tag next to the agent cursor. Empty for a bare arrow", restart=True),
    Option("cursor_size", 32, "Size of the agent cursor", restart=True, choices=(24, 32, 48, 64)),
    Option("frame_width", 3, "Width of the frame around the sandbox window", minimum=0, maximum=20),
    Option("background", "#1e1f29", "Background colour of the sandbox desktop", restart=True),
    Option("rounding", 8, "Corner radius of windows inside the sandbox", restart=True, minimum=0, maximum=40),
    Option("gaps_in", 4, "Gap between windows inside the sandbox", restart=True, minimum=0, maximum=100),
    Option("gaps_out", 8, "Gap between windows and the sandbox edge", restart=True, minimum=0, maximum=100),
    Option("float_windows", True, "Windows float and open centred like on a desktop. false tiles them",
           restart=True),
    Option("kb_layout", "", "Keyboard layout inside the sandbox, e.g. de. Empty copies yours", restart=True),
    Option("kb_variant", "", "Keyboard layout variant inside the sandbox. Empty copies yours", restart=True),
    # Behaviour
    Option("start_hidden", False, "Park the window off-screen when the desktop starts"),
    Option("workspace", "agentdesk", "Hidden workspace the window is parked on", restart=True),
    Option("browser", "", "Browser for `open` with a URL (Chromium family or Firefox). Empty picks one"),
    Option("browser_args", [], "Extra arguments for the sandbox browser"),
    Option("isolate_dbus", True, "Give sandbox apps their own D-Bus session so they open inside the sandbox",
           restart=True),
    Option("screenshot_cursor", False, "Draw the agent cursor into screenshots sent to the agent"),
    Option("settle_ms", 400, "Pause after an action before the follow-up screenshot, in ms",
           minimum=0, maximum=10000),
    Option("open_wait_ms", 2000, "Pause after `open` before the screenshot, in ms", minimum=0, maximum=30000),
    Option("cursor_glide_ms", 450, "Longest glide of the agent cursor to its target, in ms. 0 jumps",
           minimum=0, maximum=3000),
    Option("terminal_classes", ["foot", "alacritty", "kitty", "ghostty", "wezterm", "xterm", "konsole",
                                "terminal"],
           "Window classes (substrings) that paste with ctrl+shift+v instead of ctrl+v"),
    Option("notify_handover", True, "Send a desktop notification when the agent hands control to you"),
    Option("idle_stop_minutes", 15, "Stop the desktop after this many idle minutes while the agent has "
           "control. 0 keeps it running", minimum=0),
    Option("stop_on_exit", True, "Stop a desktop the agent started itself when its session ends"),
    # Live preview
    Option("preview", True, "Dock a live preview next to the agent's chat when it runs inside Herdr"),
    Option("preview_split", "auto", "Where the preview docks: right, down or auto (by pane shape)",
           choices=("auto", "right", "down")),
    Option("preview_ratio", 0.62, "Split ratio handed to Herdr when the preview docks", minimum=0.1, maximum=0.9),
    # Roles
    Option("default_role", "admin", "Role of agents without an entry in agents"),
    Option("agents", {}, "Role per agent: MCP client name (or glob) -> role"),
    Option("roles", {}, "Custom roles, and overrides of the built-in ones"),
]

BY_KEY = {o.key: o for o in OPTIONS}
DEFAULTS = {o.key: o.default for o in OPTIONS}
COLOURS = ("accent", "takeover_accent", "background")


def _xdg(var, fallback):
    return Path(os.environ.get(var) or Path.home() / fallback)


def config_dir():
    return _xdg("XDG_CONFIG_HOME", ".config") / "agentdesk"


def config_path():
    return config_dir() / "config.json"


def data_dir():
    return _xdg("XDG_DATA_HOME", ".local/share") / "agentdesk"


def runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/agentdesk-{os.getuid()}"
    path = Path(base) / "agentdesk"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def env_name(key):
    return "AGENTDESK_" + key.upper()


def check(key, raw):
    """Turn a value from the file, the environment or the command line into
    the option's type and range. Raises ValueError naming the key."""
    option = BY_KEY[key]
    default = option.default
    try:
        if isinstance(default, bool):
            value = raw if isinstance(raw, bool) else {
                "1": True, "true": True, "yes": True, "on": True,
                "0": False, "false": False, "no": False, "off": False,
            }[str(raw).strip().lower()]
        elif isinstance(default, int):
            if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
                raise ValueError
            value = int(raw)
        elif isinstance(default, float):
            if isinstance(raw, bool):
                raise ValueError
            value = float(raw)
        elif isinstance(default, list):
            if isinstance(raw, str):
                text = raw.strip()
                raw = json.loads(text) if text.startswith("[") else [p.strip() for p in text.split(",") if p.strip()]
            if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
                raise ValueError
            value = list(raw)
        elif isinstance(default, dict):
            if isinstance(raw, str):
                raw = json.loads(raw)
            if not isinstance(raw, dict):
                raise ValueError
            value = raw
        else:
            if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
                raise ValueError
            value = str(raw)
    except (KeyError, TypeError, ValueError):
        kind = {bool: "true or false", int: "a whole number", float: "a number",
                list: "a list of strings", dict: "a JSON object", str: "text"}[type(default)]
        raise ValueError(f"{key} must be {kind}, got {raw!r}") from None

    if option.choices and value not in option.choices:
        raise ValueError(f"{key} must be one of {', '.join(map(str, option.choices))}, got {value!r}")
    if option.minimum is not None and value < option.minimum:
        raise ValueError(f"{key} must be at least {option.minimum:g}, got {value!r}")
    if option.maximum is not None and value > option.maximum:
        raise ValueError(f"{key} must be at most {option.maximum:g}, got {value!r}")
    if key in COLOURS:
        value = normalize_colour(key, value)
    if key == "workspace" and not re.fullmatch(r"[\w.-]+", value):
        raise ValueError(f"workspace must be a plain name, got {value!r}")
    return value


def read_file():
    """The user's config file as written, without defaults."""
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text() or "{}")
    except ValueError as exc:
        raise RuntimeError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return data


def write_file(data):
    """Validate, then replace the config file atomically."""
    resolve(data, os.environ)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def resolve(file_data, environ):
    """Merge defaults, file and environment. Returns (settings, sources)."""
    cfg, sources = dict(DEFAULTS), dict.fromkeys(DEFAULTS, "default")
    for key, value in file_data.items():
        if key in BY_KEY:
            cfg[key] = check(key, value)
            sources[key] = "file"
    for key in DEFAULTS:
        raw = environ.get(env_name(key))
        if raw is not None:
            cfg[key] = check(key, raw)
            sources[key] = env_name(key)
    from . import roles
    problems = roles.problems(cfg)
    if problems:
        raise ValueError("; ".join(problems))
    return cfg, sources


def load_with_sources():
    try:
        return resolve(read_file(), os.environ)
    except ValueError as exc:
        raise RuntimeError(f"{config_path()}: {exc}") from None


def load():
    return load_with_sources()[0]


def normalize_colour(key, value):
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError(f"{key} must be a hex colour like #ff7a1a, got {value!r}")
    return "#" + value.lower()
