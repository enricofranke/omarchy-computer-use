"""`agentdesk config`, `agentdesk role` and `agentdesk agents`: manage settings,
roles and which agent gets which role."""

import json
import os
import shutil
import subprocess
import sys
import time

from . import config as settings
from . import roles


def add_parsers(sub):
    p = sub.add_parser("config", help="show or change settings")
    cs = p.add_subparsers(dest="action")
    q = cs.add_parser("show", help="every setting with its value and where it comes from")
    q.add_argument("--json", action="store_true")
    cs.add_parser("keys", help="every setting with its default and what it does")
    q = cs.add_parser("get", help="print one setting")
    q.add_argument("key")
    q = cs.add_parser("set", help="change a setting (lists: several values or comma separated)")
    q.add_argument("key")
    q.add_argument("value", nargs="+")
    q = cs.add_parser("unset", help="back to the default")
    q.add_argument("key")
    cs.add_parser("check", help="validate the config file")
    cs.add_parser("edit", help="open the config file in $EDITOR, then validate it")
    cs.add_parser("path", help="print the config file path")

    p = sub.add_parser("role", help="manage roles: what an agent may do")
    rs = p.add_subparsers(dest="action")
    rs.add_parser("list", help="all roles, the default role and the assignments")
    q = rs.add_parser("show", help="one role in detail")
    q.add_argument("name")
    rs.add_parser("permissions", help="every permission a role can grant")
    q = rs.add_parser("set", help="create a role or replace parts of one")
    q.add_argument("name")
    q.add_argument("--from", dest="base", help="copy this role when creating")
    q.add_argument("--description")
    q.add_argument("--allow", nargs="+", metavar="PERMISSION", help="permissions or globs such as open.*")
    q.add_argument("--deny", nargs="*", metavar="PERMISSION")
    q.add_argument("--urls", nargs="*", metavar="GLOB", help="limit open to these URLs")
    q.add_argument("--any-url", action="store_true", help="drop the URL limit")
    q.add_argument("--commands", nargs="*", metavar="GLOB", help="limit open to these programs")
    q.add_argument("--any-command", action="store_true", help="drop the command limit")
    for verb, what in (("allow", "grant"), ("deny", "take away")):
        q = rs.add_parser(verb, help=f"{what} permissions")
        q.add_argument("name")
        q.add_argument("permissions", nargs="+")
    q = rs.add_parser("remove", help="delete a custom role or reset a built-in one")
    q.add_argument("name")
    q = rs.add_parser("assign", help="give an agent (MCP client name or glob) a role")
    q.add_argument("agent")
    q.add_argument("role")
    q = rs.add_parser("unassign", help="agent falls back to the default role")
    q.add_argument("agent")
    q = rs.add_parser("default", help="role for agents without an assignment")
    q.add_argument("role")

    sub.add_parser("agents", help="agents that connected, and the role each one gets")


def run(args):
    try:
        if args.cmd == "config":
            return config_command(args)
        if args.cmd == "role":
            return role_command(args)
        return agents_command()
    except (ValueError, RuntimeError, KeyError) as exc:
        message = exc.args[0] if isinstance(exc, KeyError) else exc
        print(f"agentdesk: {message}", file=sys.stderr)
        return 1


# --- config ------------------------------------------------------------------


def _show(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value) if value != "" else "\"\""


def _known(key):
    if key not in settings.BY_KEY:
        raise KeyError(f"unknown setting {key!r}; see `agentdesk config keys`")
    return key


def _restart_hint(key):
    if settings.BY_KEY[key].restart:
        print("applies after `agentdesk restart`")
    if os.environ.get(settings.env_name(key)) is not None:
        print(f"note: {settings.env_name(key)} is set and overrides the file")


def config_command(args):
    action = args.action or "show"
    if action == "path":
        print(settings.config_path())
    elif action == "show":
        cfg, sources = settings.load_with_sources()
        if args.json:
            print(json.dumps(cfg, indent=2, ensure_ascii=False))
            return 0
        width = max(map(len, cfg))
        for key, value in cfg.items():
            source = "" if sources[key] == "default" else f"   ({sources[key]})"
            print(f"{key:<{width}}  {_show(value)}{source}")
    elif action == "keys":
        for option in settings.OPTIONS:
            flags = f" [{'|'.join(map(str, option.choices))}]" if option.choices else ""
            restart = "  (needs restart)" if option.restart else ""
            print(f"{option.key} = {_show(option.default)}{flags}{restart}\n    {option.help}")
    elif action == "get":
        print(_show(settings.load()[_known(args.key)]))
    elif action == "set":
        key = _known(args.key)
        raw = args.value if isinstance(settings.DEFAULTS[key], list) and len(args.value) > 1 else " ".join(args.value)
        value = settings.check(key, raw)
        data = settings.read_file()
        data[key] = value
        settings.write_file(data)
        print(f"{key} = {_show(value)}")
        _restart_hint(key)
    elif action == "unset":
        key = _known(args.key)
        data = settings.read_file()
        if data.pop(key, None) is None:
            print(f"{key} was not set; default is {_show(settings.DEFAULTS[key])}")
            return 0
        settings.write_file(data)
        print(f"{key} = {_show(settings.DEFAULTS[key])} (default)")
        _restart_hint(key)
    elif action == "check":
        data = settings.read_file()
        unknown = [k for k in data if k not in settings.BY_KEY]
        for key in unknown:
            print(f"warning: unknown setting {key!r} is ignored")
        settings.load()
        print(f"ok: {settings.config_path()}" if settings.config_path().exists() else "ok: no config file, all defaults")
    elif action == "edit":
        path = settings.config_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{\n}\n")
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or next(
            (e for e in ("nvim", "vim", "nano", "vi") if shutil.which(e)), None)
        if not editor:
            raise RuntimeError(f"no editor found; set $EDITOR or edit {path} by hand")
        subprocess.run([*editor.split(), str(path)], check=False)
        settings.load()
        print(f"ok: {path}")
    return 0


# --- roles -------------------------------------------------------------------


def _role_spec(data, name):
    """The stored spec of a role, or a copy of the built-in one to customize."""
    stored = (data.get("roles") or {}).get(name)
    if stored is not None:
        return dict(stored)
    if name in roles.BUILTIN:
        return json.loads(json.dumps(roles.BUILTIN[name]))
    return None


def _save_role(data, name, spec):
    data.setdefault("roles", {})[name] = {k: spec[k] for k in roles.ROLE_KEYS if k in spec}
    settings.write_file(data)


def _check_permissions(patterns):
    for pattern in patterns:
        if not roles.expand([pattern]):
            raise ValueError(f"{pattern!r} matches no permission; see `agentdesk role permissions`")


def _assignments(cfg, name):
    return [agent for agent, role in (cfg.get("agents") or {}).items() if role == name]


def role_command(args):
    action = args.action or "list"
    cfg = settings.load()
    data = settings.read_file()
    specs = roles.specs(cfg)

    if action == "permissions":
        width = max(map(len, roles.PERMISSIONS))
        for name, text in roles.PERMISSIONS.items():
            print(f"{name:<{width}}  {text}")
    elif action == "list":
        width = max(map(len, specs))
        for name, spec in specs.items():
            kind = "built-in, customized" if spec.get("customized") else "built-in" if spec["builtin"] else "custom"
            role = roles.Role(name, spec)
            print(f"{name:<{width}}  {role.description or '-'} ({kind})\n{'':<{width}}  {role.summary()}")
        print(f"\ndefault role: {cfg['default_role']}")
        for agent, role in (cfg.get("agents") or {}).items():
            print(f"agent {agent} -> {role}")
    elif action == "show":
        spec = specs.get(args.name)
        if spec is None:
            raise KeyError(f"no role {args.name!r}; see `agentdesk role list`")
        role = roles.Role(args.name, spec)
        print(f"{role.name}: {role.description or '-'}")
        for name, text in roles.PERMISSIONS.items():
            print(f"  {'yes' if name in role.permissions else ' no'}  {name:<17} {text}")
        print(f"  URLs:     {'any' if role.urls is None else ', '.join(role.urls) or 'none'}")
        print(f"  commands: {'any' if role.commands is None else ', '.join(role.commands) or 'none'}")
        agents = _assignments(cfg, role.name)
        if cfg["default_role"] == role.name:
            agents.append("every agent without an assignment")
        print(f"  used by:  {', '.join(agents) or 'nobody'}")
    elif action == "set":
        if not roles.valid_name(args.name):
            raise ValueError("role names may only use a-z, 0-9, - and _")
        spec = _role_spec(data, args.name)
        if spec is None:
            base = args.base or None
            if base and base not in specs:
                raise KeyError(f"no role {base!r} to copy")
            spec = _role_spec(data, base) if base else {"allow": []}
        elif args.base:
            raise ValueError(f"role {args.name!r} exists; --from only applies when creating one")
        if args.description is not None:
            spec["description"] = args.description
        if args.allow is not None:
            _check_permissions(args.allow)
            spec["allow"] = args.allow
        if args.deny is not None:
            _check_permissions(args.deny)
            spec["deny"] = args.deny
        if args.any_url:
            spec.pop("urls", None)
        elif args.urls is not None:
            spec["urls"] = args.urls
        if args.any_command:
            spec.pop("commands", None)
        elif args.commands is not None:
            spec["commands"] = args.commands
        _save_role(data, args.name, spec)
        print(f"{args.name}: {roles.Role(args.name, spec).summary()}")
    elif action in ("allow", "deny"):
        spec = _role_spec(data, args.name)
        if spec is None:
            raise KeyError(f"no role {args.name!r}; create it with `agentdesk role set {args.name}`")
        _check_permissions(args.permissions)
        wanted = roles.expand(args.permissions)
        allow, deny = list(spec.get("allow", [])), list(spec.get("deny", []))
        if action == "allow":
            if not wanted <= roles.expand(allow):
                allow += [p for p in args.permissions if p not in allow]
            if roles.expand(deny) & wanted:
                deny = roles.ordered(roles.expand(deny) - wanted)
        else:
            current = roles.expand(allow) - roles.expand(deny)
            deny += [p for p in roles.ordered(wanted & current) if p not in deny]
        spec["allow"], spec["deny"] = allow, deny
        if not deny:
            spec.pop("deny")
        _save_role(data, args.name, spec)
        print(f"{args.name}: {roles.Role(args.name, spec).summary()}")
    elif action == "remove":
        stored = (data.get("roles") or {})
        if args.name not in stored:
            raise ValueError(f"built-in role {args.name!r} cannot be removed" if args.name in roles.BUILTIN
                             else f"no custom role {args.name!r}")
        if args.name not in roles.BUILTIN:
            users = _assignments(cfg, args.name) + (["default_role"] if cfg["default_role"] == args.name else [])
            if users:
                raise ValueError(f"role {args.name!r} is still used by {', '.join(users)}")
        del stored[args.name]
        if not stored:
            data.pop("roles")
        settings.write_file(data)
        print(f"{args.name}: reset to built-in" if args.name in roles.BUILTIN else f"removed {args.name}")
    elif action == "assign":
        if args.role not in specs:
            raise KeyError(f"no role {args.role!r}; see `agentdesk role list`")
        data.setdefault("agents", {})[args.agent] = args.role
        settings.write_file(data)
        print(f"agent {args.agent} -> {args.role}")
    elif action == "unassign":
        agents = data.get("agents") or {}
        if agents.pop(args.agent, None) is None:
            raise KeyError(f"agent {args.agent!r} has no assignment")
        if not agents:
            data.pop("agents")
        settings.write_file(data)
        print(f"agent {args.agent} -> {cfg['default_role']} (default)")
    elif action == "default":
        if args.role not in specs:
            raise KeyError(f"no role {args.role!r}; see `agentdesk role list`")
        data["default_role"] = args.role
        settings.write_file(data)
        print(f"default role: {args.role}")
    return 0


# --- agents ------------------------------------------------------------------


def _ago(seconds):
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


def agents_command():
    cfg = settings.load()
    path = settings.data_dir() / "agents.json"
    try:
        seen = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        seen = {}
    if not seen:
        print("no agent has connected yet")
    else:
        width = max(map(len, seen))
        for name, info in sorted(seen.items(), key=lambda item: -item[1].get("last_seen", 0)):
            client = "" if name == "(unnamed)" else name
            via = "assigned" if roles.assigned(cfg, client) else "default"
            role = roles.assigned(cfg, client) or cfg["default_role"]
            print(f"{name:<{width}}  {info.get('version') or '-':<10} {_ago(time.time() - info.get('last_seen', 0)):<10}"
                  f"  role {role} ({via})")
    print("\nAssign one with `agentdesk role assign <name> <role>`. "
          "An `agentdesk mcp --role` argument overrides this.")
    return 0
