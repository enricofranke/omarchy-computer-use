"""hyprctl helpers for the host session and the sandbox instance."""

import json
import os
import subprocess


class HyprError(RuntimeError):
    pass


def lua_str(value):
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def lua_table(mapping):
    parts = []
    for key, value in mapping.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        elif isinstance(value, (list, tuple)):
            rendered = "{" + ", ".join(
                str(v) if isinstance(v, (int, float)) else lua_str(v) for v in value
            ) + "}"
        elif isinstance(value, dict):
            rendered = lua_table(value)
        else:
            rendered = lua_str(value)
        parts.append(f"{key} = {rendered}")
    return "{ " + ", ".join(parts) + " }"


def require_host():
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        raise HyprError("agentdesk needs to run inside a Hyprland session (HYPRLAND_INSTANCE_SIGNATURE is unset)")


class Hypr:
    """One Hyprland instance; instance=None means the session we run in."""

    def __init__(self, instance=None):
        self.instance = instance

    def _cmd(self, *args):
        cmd = ["hyprctl"]
        if self.instance:
            cmd += ["--instance", self.instance]
        return cmd + list(args)

    def raw(self, *args, timeout=5):
        try:
            result = subprocess.run(
                self._cmd(*args), capture_output=True, text=True, timeout=timeout
            )
        except FileNotFoundError as exc:
            raise HyprError("hyprctl not found; is Hyprland installed?") from exc
        except subprocess.TimeoutExpired as exc:
            raise HyprError(f"hyprctl {' '.join(args)} timed out") from exc
        return result.stdout.strip()

    def json(self, *args):
        out = self.raw("-j", *args)
        try:
            return json.loads(out)
        except ValueError as exc:
            raise HyprError(f"hyprctl {' '.join(args)}: {out[:200]}") from exc

    def eval(self, code):
        out = self.raw("eval", code)
        if out != "ok":
            raise HyprError(f"hyprctl eval failed: {out[:300]}")

    def dispatch(self, expr):
        self.eval(f"hl.dispatch({expr})")

    def set_prop(self, window, prop, value):
        self.dispatch(
            "hl.dsp.window.set_prop(" + lua_table({"window": window, "prop": prop, "value": str(value)}) + ")"
        )

    def clients(self):
        return [c for c in self.json("clients") if c.get("mapped", True)]

    def window(self, address):
        for client in self.json("clients"):
            if client.get("address") == address:
                return client
        return None


def instances():
    try:
        return json.loads(subprocess.run(
            ["hyprctl", "-j", "instances"], capture_output=True, text=True, timeout=5
        ).stdout or "[]")
    except (ValueError, subprocess.TimeoutExpired):
        return []
