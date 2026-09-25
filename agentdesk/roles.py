"""Roles: which agent may do what on the desktop.

Every MCP client gets one role: the one given with `agentdesk mcp --role`, else
the one its client name is assigned to in `agents`, else `default_role`. A role
is a set of permissions plus optional allowlists for the URLs and commands
`open` may start.

Roles guard the MCP tools. They are not a security boundary against an agent
that also has a shell as your user: such an agent can edit the config file or
drive the sandbox through the command line.
"""

import fnmatch
import re
import shlex
from pathlib import Path

PERMISSIONS = {
    "screen": "take screenshots, zoom, read the cursor position, list windows, read the desktop status",
    "mouse": "move the cursor, click, drag and scroll",
    "keyboard": "type text and press keys",
    "open.url": "open URLs in the sandbox browser",
    "open.command": "launch commands and apps in the sandbox (they run as your user)",
    "windows.arrange": "focus, move, resize, maximize, center and fullscreen windows",
    "windows.close": "close windows",
    "desktop.start": "start the desktop, also on first use",
    "desktop.stop": "stop the desktop",
    "desktop.show": "show or hide the desktop window and dock the live preview",
    "desktop.handover": "hand control to you, e.g. for a login",
    "desktop.reclaim": "take control back after a handover",
}

BUILTIN = {
    "admin": {"description": "everything", "allow": ["*"]},
    "operator": {"description": "everything except launching commands", "allow": ["*"], "deny": ["open.command"]},
    "viewer": {"description": "look only", "allow": ["screen"]},
}

_MOUSE = ("left_click", "right_click", "middle_click", "double_click", "triple_click", "mouse_move",
          "left_click_drag", "left_mouse_down", "left_mouse_up", "scroll")

# Permissions each tool action needs.
ACTIONS = {
    "computer": {
        **dict.fromkeys(("screenshot", "cursor_position", "wait", "zoom"), ("screen",)),
        **dict.fromkeys(_MOUSE, ("mouse",)),
        "type": ("keyboard",),
        "key": ("keyboard",),
    },
    "open": {"url": ("open.url",), "command": ("open.command",)},
    "windows": {
        "list": ("screen",),
        **dict.fromkeys(("focus", "move", "resize", "maximize", "center", "fullscreen"), ("windows.arrange",)),
        "close": ("windows.close",),
    },
    "desktop": {
        "status": ("screen",),
        "start": ("desktop.start",),
        "stop": ("desktop.stop",),
        "restart": ("desktop.stop", "desktop.start"),
        "show": ("desktop.show",),
        "hide": ("desktop.show",),
        "preview": ("desktop.show",),
        "handover": ("desktop.handover",),
        "reclaim": ("desktop.reclaim",),
    },
}

ROLE_KEYS = ("description", "allow", "deny", "urls", "commands")
# A command allowlist is meaningless if the command can chain another one.
SHELL_SYNTAX = re.compile(r"[;&|`$<>(){}\n\\]")


class Denied(RuntimeError):
    pass


def expand(patterns):
    """Permission names matching the patterns, e.g. ["open.*"]."""
    return {p for pattern in patterns for p in PERMISSIONS if fnmatch.fnmatchcase(p, pattern)}


def ordered(permissions):
    return [p for p in PERMISSIONS if p in permissions]


def specs(cfg):
    """All roles by name: the built-in ones, overridden or extended by the config."""
    merged = {name: dict(spec, builtin=True) for name, spec in BUILTIN.items()}
    for name, spec in (cfg.get("roles") or {}).items():
        if isinstance(spec, dict):
            merged[name] = dict(spec, builtin=name in BUILTIN, customized=name in BUILTIN)
    return merged


def assigned(cfg, client):
    """Role name the agents table gives this client, or None."""
    agents = cfg.get("agents") or {}
    if client in agents:
        return agents[client]
    for pattern, role in agents.items():
        if fnmatch.fnmatchcase((client or "").lower(), pattern.lower()):
            return role
    return None


def role_for(cfg, client=None, override=None):
    name = override or assigned(cfg, client) or cfg["default_role"]
    spec = specs(cfg).get(name)
    if spec is None:
        raise Denied(f"role {name!r} does not exist; see `agentdesk role list`")
    return Role(name, spec)


class Role:
    def __init__(self, name, spec):
        self.name = name
        self.description = spec.get("description", "")
        self.permissions = expand(spec.get("allow", [])) - expand(spec.get("deny", []))
        # None means anything; a list limits `open` to matching entries.
        self.urls = spec.get("urls")
        self.commands = spec.get("commands")

    def allows(self, *permissions):
        return all(p in self.permissions for p in permissions)

    def allows_action(self, tool, action):
        needed = ACTIONS.get(tool, {}).get(action)
        return needed is not None and self.allows(*needed)

    def check(self, tool, action, args=None):
        needed = ACTIONS.get(tool, {}).get(action)
        if needed is None:
            return  # unknown actions fail in the tool itself
        missing = [p for p in needed if p not in self.permissions]
        if missing:
            raise Denied(
                f"your role {self.name!r} does not allow {tool} {action} (needs {', '.join(missing)}). "
                "Do not work around this; ask the user if the task needs it."
            )
        if tool == "open" and action == "url" and self.urls is not None:
            url = (args or {}).get("url", "")
            if not any(fnmatch.fnmatchcase(url.lower(), p.lower()) for p in self.urls):
                raise Denied(f"your role {self.name!r} may only open these URLs: {', '.join(self.urls) or 'none'}")
        if tool == "open" and action == "command" and self.commands is not None:
            command = (args or {}).get("command", "")
            if not command_allowed(command, self.commands):
                raise Denied(
                    f"your role {self.name!r} may only launch: {', '.join(self.commands) or 'nothing'} "
                    "(plain commands, no shell syntax)"
                )

    def summary(self):
        text = ", ".join(ordered(self.permissions)) or "nothing"
        if self.urls is not None:
            text += f"; URLs: {', '.join(self.urls) or 'none'}"
        if self.commands is not None:
            text += f"; commands: {', '.join(self.commands) or 'none'}"
        return text


def command_allowed(command, patterns):
    if "*" in patterns:
        return True
    if SHELL_SYNTAX.search(command):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    return bool(argv) and any(fnmatch.fnmatchcase(Path(argv[0]).name, p) for p in patterns)


def valid_name(name):
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name))


def problems(cfg):
    """Everything wrong with the role settings, as readable sentences."""
    found = []
    roles = cfg.get("roles") or {}
    for name, spec in roles.items():
        if not valid_name(name):
            found.append(f"role name {name!r} may only use a-z, 0-9, - and _")
        if not isinstance(spec, dict):
            found.append(f"role {name!r} must be an object")
            continue
        for key in spec:
            if key not in ROLE_KEYS:
                found.append(f"role {name!r} has unknown key {key!r} (known: {', '.join(ROLE_KEYS)})")
        for key in ("allow", "deny", "urls", "commands"):
            value = spec.get(key)
            if value is not None and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
                found.append(f"role {name!r}: {key} must be a list of strings")
                continue
            if key in ("allow", "deny"):
                for pattern in value or []:
                    if not expand([pattern]):
                        found.append(f"role {name!r}: {pattern!r} matches no permission")
    known = specs(cfg)
    if cfg.get("default_role") not in known:
        found.append(f"default_role {cfg.get('default_role')!r} is not a role")
    for client, role in (cfg.get("agents") or {}).items():
        if role not in known:
            found.append(f"agent {client!r} is assigned to unknown role {role!r}")
    return found
