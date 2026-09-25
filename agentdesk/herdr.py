"""Dock the live preview next to the agent's chat when running inside Herdr."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as settings

BIN = Path(__file__).resolve().parent.parent / "bin" / "agentdesk"


def available():
    return os.environ.get("HERDR_ENV") == "1" and bool(os.environ.get("HERDR_PANE_ID")) \
        and bool(shutil.which("herdr"))


def _herdr(*args):
    result = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout or "{}").get("result", {})


def open_preview():
    """Split the caller's pane and run the preview there. Returns the pane id."""
    if not available():
        return None
    record = settings.runtime_dir() / "preview-pane"
    if record.exists():
        pane_id = record.read_text().strip()
        try:
            _herdr("pane", "get", pane_id)
            return pane_id
        except (RuntimeError, ValueError, subprocess.SubprocessError):
            record.unlink(missing_ok=True)

    caller = os.environ["HERDR_PANE_ID"]
    try:
        layout = _herdr("pane", "layout", "--pane", caller)["layout"]
        rect = next(p["rect"] for p in layout["panes"] if p["pane_id"] == caller)
        cfg = settings.load()
        direction = cfg["preview_split"]
        # Cells are about twice as tall as wide: split sideways only when the
        # pane is very wide, otherwise put the preview underneath.
        if direction == "auto":
            direction = "right" if rect["width"] > 2.6 * rect["height"] else "down"
        pane = _herdr(
            "pane", "split", "--pane", caller, "--direction", direction,
            "--ratio", f"{cfg['preview_ratio']:g}", "--no-focus", "--cwd", str(Path.home()),
        )["pane"]
        command = f"exec {shlex.quote(sys.executable)} -B {shlex.quote(str(BIN))} watch --auto"
        _herdr("pane", "run", pane["pane_id"], command)
    except (RuntimeError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
        print(f"agentdesk: could not open the Herdr preview: {exc}", file=sys.stderr)
        return None
    record.write_text(pane["pane_id"])
    return pane["pane_id"]
