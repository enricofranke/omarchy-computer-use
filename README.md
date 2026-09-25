# AgentDesk

**Computer use for Hyprland and Omarchy, with its own cursor.**

AgentDesk gives Claude (or any MCP-capable agent) a desktop of its own: a
nested Hyprland session that appears on your screen as a single window with an
orange frame. The agent gets its own cursor (an orange arrow with its name
tag), its own keyboard focus and its own windows. Your mouse and keyboard are
never touched, so you keep working while it clicks around. Hide the window and
the agent keeps working in the background.

<p align="center"><img src="assets/cursor-preview.png" width="240" alt="The agent cursor: an orange arrow with a Claude name tag"></p>

- **Your input stays yours.** The agent drives a separate Wayland seat, not your pointer.
- **Real computer use.** It clicks, drags, scrolls, types (including Unicode), presses shortcuts,
  and moves, resizes, maximizes and closes windows.
- **Watch or ignore it.** Show the desktop over your current workspace, or park it off-screen.
  It keeps rendering while hidden.
- **Its own apps.** Browsers get a separate profile and apps get their own D-Bus session,
  so nothing the agent opens lands on your desktop.
- **Take over any time.** While the agent drives, your mouse and keyboard pass over its window
  without touching anything. Take over with one click (the frame turns blue), hand back when done.
  The agent can also hand over by itself, e.g. for a login.
- **Live preview next to the chat.** Inside [Herdr](https://herdr.dev), a small pane docks next to
  the agent's chat and shows what it sees.
- **No build step.** Plain Python stdlib plus tools Omarchy already ships (`grim`, `wtype`, `wl-clipboard`).

## How it works

```
your Hyprland session
└── window "aquamarine"  (orange frame, 1280×800, parked on a hidden workspace)
    └── nested Hyprland  ← own seat, own cursor, own windows
        ├── virtual pointer  (zwlr_virtual_pointer_v1, spoken directly over the Wayland socket)
        ├── virtual keyboard (wtype)
        └── screenshots      (grim)
```

The nested compositor uses Hyprland's Wayland backend, so it is an ordinary
window on your desktop. Pointer and keyboard events go to the nested
compositor's socket and never reach your session. The host window has
`render_unfocused` set, which keeps the sandbox rendering (and screenshots
working) while it sits on the hidden `agentdesk` workspace.

## Requirements

- Hyprland 0.56 or newer with a Lua config. Omarchy 4 ships this.
- `python3` (3.9+), `grim`, `wtype`
- Optional: `rsvg-convert` to recolour or rename the cursor, and `dbus-run-session`
  to isolate sandbox apps from your session bus

Run `bin/agentdesk doctor` to check.

## Install

### Claude Code plugin

```bash
claude plugin marketplace add OWNER/agentdesk
claude plugin install agentdesk@agentdesk
```

This installs the MCP server and a `computer-use` skill that teaches Claude
the workflow. To wire up only the MCP server:

```bash
git clone https://github.com/OWNER/agentdesk ~/.local/share/agentdesk-src
claude mcp add agentdesk -- python3 ~/.local/share/agentdesk-src/bin/agentdesk mcp
```

Other MCP clients (Codex, opencode, …) can use the same command:
`python3 /path/to/agentdesk/bin/agentdesk mcp`.

### Omarchy bar widget

```bash
omarchy plugin add https://github.com/OWNER/agentdesk.git --enable --yes
```

A cursor icon appears in the bar. Left click shows or hides the agent's
desktop and starts it when it is stopped. Right click stops it. The icon
pulses in the accent colour while the agent is acting.

For a keybinding, add this to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + CTRL + A", "Agent desktop",
  "python3 ~/.config/omarchy/plugins/agentdesk/bin/agentdesk toggle")
```

## Taking over

| | |
|---|---|
| Bar icon | right click takes over / hands back |
| Preview pane | `t` takes over / hands back, `s` shows or hides the window |
| CLI | `agentdesk takeover`, `agentdesk release`, `agentdesk control` (toggle) |

While the agent has control, the sandbox ignores your forwarded mouse and
keyboard and its window does not take your keyboard focus. After a takeover
the agent's input is blocked until you hand back.

## Live preview

`agentdesk watch` draws the desktop in any terminal: a sharp image in Kitty,
Ghostty or WezTerm, coloured half blocks elsewhere. When the agent runs inside
Herdr, the preview opens by itself in a split next to its chat the first time
the desktop starts (`"preview": false` turns that off; `agentdesk preview`
opens it by hand). For a sharp image inside Herdr, enable its experimental
Kitty graphics:

```toml
# ~/.config/herdr/config.toml
[experimental]
kitty_graphics = true
```

## MCP tools

| Tool       | What it does |
|------------|--------------|
| `computer` | `screenshot`, `left_click`, `right_click`, `middle_click`, `double_click`, `triple_click`, `mouse_move`, `left_click_drag`, `left_mouse_down`/`up`, `scroll`, `type`, `key`, `cursor_position`, `wait`, `zoom`. The action set mirrors Anthropic's computer-use tool. Actions return a fresh screenshot. |
| `open`     | Open a URL in the sandbox browser or launch any command inside the sandbox |
| `windows`  | `list`, `focus`, `move`, `resize`, `maximize`, `center`, `fullscreen`, `close` |
| `desktop`  | `status`, `start`, `stop`, `restart`, `show`, `hide`, `handover`, `reclaim`, `preview` |

The desktop starts on first use.

## Command line

```bash
agentdesk start [--show|--hidden]   agentdesk stop | restart
agentdesk show | hide | toggle      agentdesk status [--json]
agentdesk open https://example.com  agentdesk open --cmd nautilus
agentdesk screenshot -o shot.png    agentdesk click 640 400 [--button right] [--count 2]
agentdesk type "hello"              agentdesk key ctrl+l
agentdesk scroll 640 400 down 5     agentdesk windows [list|maximize|move|resize|…] [window]
agentdesk takeover | release        agentdesk watch | preview
agentdesk doctor                    agentdesk cursor-preview -o cursor.png
```

## Configuration

`~/.config/agentdesk/config.json` is optional. Every key can also be set as an
environment variable, e.g. `AGENTDESK_ACCENT=#7aa2f7`.

| Key                 | Default     | Meaning |
|---------------------|-------------|---------|
| `width`, `height`   | `1280`, `800` | Sandbox screen size. Screenshots map 1:1 to click coordinates |
| `accent`            | `#ff7a1a`   | Colour of the agent cursor and the window frame |
| `label`             | `Claude`    | Name tag next to the cursor; `""` for a bare arrow |
| `start_hidden`      | `false`     | Park the window off-screen when the desktop starts |
| `browser`           | auto        | Browser for `open` with a URL (Chromium family or Firefox) |
| `isolate_dbus`      | `true`      | Give sandbox apps their own D-Bus session |
| `settle_ms`         | `400`       | Pause after an action before the follow-up screenshot |
| `preview`           | `true`      | Dock the live preview next to the agent's chat in Herdr |

Changes apply on the next `agentdesk restart`.

## Security

AgentDesk isolates **input and display**. It is not a security sandbox. Apps
inside it run as your user and can read and write your files. The sandbox
browser profile starts empty, so the agent is not logged in anywhere unless you
log in for it.

## Troubleshooting

- **Screenshots time out.** The window is somewhere the host stopped drawing,
  such as a closed special workspace. AgentDesk parks it automatically and
  retries; `agentdesk hide` does the same by hand.
- **The window was closed.** The nested session ends with it. The next action
  or `agentdesk start` brings up a fresh one.
- **Logs.** `$XDG_RUNTIME_DIR/agentdesk/hyprland.log`

## License

MIT
