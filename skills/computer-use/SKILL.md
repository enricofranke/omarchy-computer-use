---
name: computer-use
description: Operate graphical apps and websites on the user's Linux (Hyprland/Omarchy) machine through your own sandbox desktop — clicking, typing, reading the screen, arranging windows. Use when a task needs a GUI or a real browser session (testing a web UI, filling a form, checking how a page renders, using a desktop app) and no API or CLI does the job.
---

# Computer use with agentdesk

You have your own desktop: a separate Hyprland session with its own cursor
(an orange arrow with your name) and its own keyboard focus. The user sees it
as a window with an orange frame and can keep using their own mouse and
keyboard while you work. Nothing you do there reaches their desktop.

## Loop

1. `computer` → `screenshot` to see the current state. The screen is small
   (1280×800 by default); coordinates are pixels in the latest screenshot.
2. Act once: click, type, press a key, scroll.
3. Look at the screenshot that comes back before acting again. If something
   did not change, find out why instead of repeating the same click.

## Getting things open

- `open` with `url` starts the sandbox browser. It uses its own profile, so
  the user's logins are not there; if a site needs a login, ask the user to
  log in once by showing the desktop (`desktop` → `show`) and clicking into it.
- `open` with `command` starts any installed app inside the sandbox.
- `windows` → `maximize` gives an app the whole screen, which makes small UI
  easier to hit. `list` returns window ids, positions and sizes.

## Tips

- Prefer keyboard shortcuts when they are reliable: `ctrl+l` then typing a URL
  and `Return` beats hunting for the address bar.
- Click into a text field before `type`. Use `key` with `ctrl+a` to replace text.
- Use `zoom` with a region to read small text instead of guessing.
- `scroll` needs a coordinate over the element that should scroll.
- After an action that loads something, `wait` a second or two, then look again.

## Boundaries

- Do not enter passwords, payment details or other secrets yourself, and do not
  solve CAPTCHAs. Call `desktop` → `handover` with a short message; the user takes
  over with their own mouse and keyboard. Continue with `reclaim` only after they
  tell you they are done.
- Dialogs such as file pickers open inside your desktop; handle them there.
  Downloads land in the user's real ~/Downloads, where your file tools can read them.
- The user decides with a role what you may do here. Actions outside it are not
  offered or get refused. Then tell the user what you need; never work around a
  refusal, e.g. by typing into a terminal or using the `agentdesk` command line.
- Confirm with the user before irreversible actions (sending, buying, deleting,
  publishing), exactly as you would outside the sandbox.
- The sandbox isolates input and display, not files: apps inside it run as the
  user and can read and write their files.
- When done, leave the desktop running unless the user asks to stop it
  (`desktop` → `stop`); `hide` gets it out of their way.
