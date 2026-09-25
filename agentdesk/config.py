"""Settings: defaults, overridden by ~/.config/agentdesk/config.json, then env."""

import json
import os
from pathlib import Path

DEFAULTS = {
    # Sandbox screen size in pixels; screenshots map 1:1 to click coordinates.
    "width": 1280,
    "height": 800,
    # Colour of the agent cursor and of the frame around the sandbox window.
    "accent": "#ff7a1a",
    # Name tag next to the agent cursor. Empty string for a bare arrow.
    "label": "Claude",
    # Keep the sandbox window parked off-screen when it starts.
    "start_hidden": False,
    # Browser used by `open --url`; empty means auto-detect.
    "browser": "",
    # Give sandbox apps their own D-Bus session so single-instance apps
    # (Nautilus, GNOME apps, ...) open inside the sandbox, not on your desktop.
    "isolate_dbus": True,
    # Draw the agent cursor into screenshots sent to the model.
    "screenshot_cursor": False,
    # Pause after an action before the follow-up screenshot, in milliseconds.
    "settle_ms": 400,
    # Dock a live preview next to the agent's chat when it runs inside Herdr.
    "preview": True,
}


def _xdg(var, fallback):
    return Path(os.environ.get(var) or Path.home() / fallback)


def config_dir():
    return _xdg("XDG_CONFIG_HOME", ".config") / "agentdesk"


def data_dir():
    return _xdg("XDG_DATA_HOME", ".local/share") / "agentdesk"


def runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/agentdesk-{os.getuid()}"
    path = Path(base) / "agentdesk"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _coerce(raw, default):
    if isinstance(default, bool):
        return str(raw).lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    return str(raw)


def load():
    cfg = dict(DEFAULTS)
    path = config_dir() / "config.json"
    if path.exists():
        try:
            user = json.loads(path.read_text())
        except ValueError as exc:
            raise RuntimeError(f"{path} is not valid JSON: {exc}") from exc
        for key, value in user.items():
            if key in DEFAULTS:
                cfg[key] = _coerce(value, DEFAULTS[key])
    for key, default in DEFAULTS.items():
        env = os.environ.get("AGENTDESK_" + key.upper())
        if env is not None:
            cfg[key] = _coerce(env, default)
    cfg["accent"] = normalize_colour(cfg["accent"])
    return cfg


def normalize_colour(value):
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise RuntimeError(f"accent must be a hex colour like #ff7a1a, got {value!r}")
    return "#" + value.lower()
