"""Share the clipboard between your desktop and the sandbox while you have control.

The sandbox is its own Wayland session with its own clipboard. During a
takeover two `wl-paste --watch` processes copy every change across, in both
directions; a hash of the last shared content stops the echo. When the agent
gets control back they stop and the sandbox clipboard is cleared, so nothing
you copied (a password, say) stays within the agent's reach. While the agent
has control nothing is shared.
"""

import hashlib
import os
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from . import config as settings

BIN = Path(__file__).resolve().parent.parent / "bin" / "agentdesk"
# Offered types in order of preference; everything else stays where it is.
PREFERRED = ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "TEXT", "STRING", "image/png")


def available():
    return bool(shutil.which("wl-paste") and shutil.which("wl-copy"))


def _pid_file():
    return settings.runtime_dir() / "clipboard.pid"


def _last_file():
    return settings.runtime_dir() / "clipboard.last"


def _env(display):
    return dict(os.environ, WAYLAND_DISPLAY=display)


def share(host_display, sandbox_display):
    """Start sharing. Returns False when wl-clipboard is missing."""
    stop()
    if not (available() and host_display and sandbox_display):
        return False
    # What you copied before taking over is there right away.
    push(host_display, sandbox_display)
    python, agentdesk = shlex.quote(sys.executable), shlex.quote(str(BIN))

    def watch(src, dst):
        return (f"WAYLAND_DISPLAY={shlex.quote(src)} wl-paste --watch "
                f"{python} -B {agentdesk} clip-push {shlex.quote(src)} {shlex.quote(dst)}")

    proc = subprocess.Popen(
        ["sh", "-c", f"{watch(host_display, sandbox_display)} & {watch(sandbox_display, host_display)} & wait"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _pid_file().write_text(str(proc.pid))
    return True


def stop(sandbox_display=None):
    """Stop sharing; with sandbox_display also empty the sandbox clipboard."""
    try:
        pgid = int(_pid_file().read_text())
    except (OSError, ValueError):
        pgid = None
    _pid_file().unlink(missing_ok=True)
    _last_file().unlink(missing_ok=True)
    if pgid:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if sandbox_display and available():
        subprocess.run(["wl-copy", "--clear"], env=_env(sandbox_display), capture_output=True, timeout=5)


def push(src, dst):
    """Copy the clipboard of display src to display dst, unless it came from there."""
    try:
        types = subprocess.run(["wl-paste", "--list-types"], env=_env(src), capture_output=True,
                               text=True, timeout=5).stdout.splitlines()
        mime = next((t for t in PREFERRED if t in types), None)
        if not mime:
            return
        data = subprocess.run(["wl-paste", "--no-newline", "--type", mime], env=_env(src),
                              capture_output=True, timeout=5).stdout
    except subprocess.TimeoutExpired:
        return
    digest = hashlib.sha1(mime.encode() + b"\0" + data).hexdigest()
    last = _last_file()
    if last.exists() and last.read_text() == digest:
        return
    last.write_text(digest)
    # wl-copy stays around to serve the content. Its own session keeps it
    # alive when the watchers stop, so what you copied survives a hand back.
    subprocess.run(["wl-copy", "--type", mime], input=data, env=_env(dst), stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=5, start_new_session=True)
