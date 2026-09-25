"""Builds the agent's cursor theme: an accent-coloured arrow with a name tag.

The arrow is drawn as SVG, rasterised with rsvg-convert and packed into an
XCursor file. Every cursor shape name points at the same image, so the agent
cursor looks the same whether it hovers a link, a text field or a border.
"""

import hashlib
import math
import os
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

THEME_NAME = "AgentDesk"
NOMINAL_SIZES = (24, 32, 48, 64)
BUNDLED = Path(__file__).resolve().parent.parent / "assets" / "cursor-default.xcursor"

# Every name a client might ask for, cursor-shape-v1 names plus legacy X11 ones.
SHAPE_NAMES = """
left_ptr arrow top_left_arrow pointer hand hand1 hand2 pointing_hand text xterm ibeam
crosshair cross tcross wait watch progress left_ptr_watch half-busy grab openhand
grabbing closedhand dnd-move move fleur size_all all-scroll not-allowed crossed_circle
forbidden no-drop help question_arrow whats_this context-menu cell plus col-resize
sb_h_double_arrow split_h row-resize sb_v_double_arrow split_v e-resize right_side
w-resize left_side n-resize top_side s-resize bottom_side ne-resize top_right_corner
nw-resize top_left_corner se-resize bottom_right_corner sw-resize bottom_left_corner
ew-resize h_double_arrow size_hor ns-resize v_double_arrow size_ver nesw-resize
fd_double_arrow size_bdiag nwse-resize bd_double_arrow size_fdiag zoom-in zoom-out
copy dnd-copy alias dnd-link vertical-text dnd-none dnd-ask
""".split()

# Arrow tip in the 32-unit design space; this is the cursor hotspot.
HOT_X, HOT_Y = 5.5, 3.2


def svg(accent, label):
    label = label.strip()
    text_width = 5.9 * len(label)
    pill_width = text_width + 14 if label else 0
    width = max(22, 15 + pill_width + 3)
    height = 40 if label else 28
    pill = ""
    if label:
        safe = label.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        pill = f"""
  <g filter="url(#shadow)">
    <rect x="15" y="22" width="{pill_width:.1f}" height="15" rx="7.5"
          fill="{accent}" stroke="#ffffff" stroke-width="1.4"/>
    <text x="22" y="32.6" font-family="Inter, 'Noto Sans', 'DejaVu Sans', sans-serif"
          font-size="10" font-weight="700" fill="#ffffff"
          textLength="{text_width:.1f}" lengthAdjust="spacingAndGlyphs">{safe}</text>
  </g>"""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width:.1f}" height="{height}"
     viewBox="0 0 {width:.1f} {height}">
  <defs>
    <filter id="shadow" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur in="SourceAlpha" stdDeviation="0.9"/>
      <feOffset dy="1" result="blur"/>
      <feComponentTransfer><feFuncA type="linear" slope="0.4"/></feComponentTransfer>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <path filter="url(#shadow)" d="M{HOT_X} {HOT_Y} L5.5 24.5 L10.8 19.4 L18.8 19.4 Z"
        fill="{accent}" stroke="#ffffff" stroke-width="2" stroke-linejoin="round"/>{pill}
</svg>
"""


def render_png(svg_text, scale, out_path):
    rsvg = shutil.which("rsvg-convert")
    if not rsvg:
        raise RuntimeError("rsvg-convert (librsvg) is needed to draw a custom cursor")
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as handle:
        handle.write(svg_text)
    try:
        subprocess.run([rsvg, "-z", str(scale), "-o", str(out_path), handle.name], check=True)
    finally:
        os.unlink(handle.name)


def decode_png(data):
    """Decode an 8-bit RGBA, non-interlaced PNG (what rsvg-convert writes)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, width = 8, b"", 0
    while pos < len(data):
        (length,) = struct.unpack_from(">I", data, pos)
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or colour != 6 or interlace:
                raise ValueError("expected 8-bit RGBA non-interlaced PNG")
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4
    rows, prev = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind = raw[start]
        row = bytearray(raw[start + 1 : start + 1 + stride])
        for i in range(stride):
            a = row[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if kind == 1:
                row[i] = (row[i] + a) & 0xFF
            elif kind == 2:
                row[i] = (row[i] + b) & 0xFF
            elif kind == 3:
                row[i] = (row[i] + (a + b) // 2) & 0xFF
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[i] = (row[i] + pred) & 0xFF
        rows.append(row)
        prev = row
    return width, height, b"".join(rows)


def xcursor(images):
    """images: list of (nominal, width, height, xhot, yhot, rgba_bytes)."""
    header = struct.pack("<4sIII", b"Xcur", 16, 0x10000, len(images))
    toc, chunks = b"", b""
    offset = 16 + 12 * len(images)
    for nominal, width, height, xhot, yhot, rgba in images:
        pixels = bytearray(width * height * 4)
        for i in range(width * height):
            r, g, b, a = rgba[i * 4 : i * 4 + 4]
            # XCursor wants premultiplied ARGB, stored little-endian (B G R A).
            pixels[i * 4 : i * 4 + 4] = bytes((b * a // 255, g * a // 255, r * a // 255, a))
        chunk = struct.pack("<IIIIIIIII", 36, 0xFFFD0002, nominal, 1, width, height, xhot, yhot, 0)
        chunk += bytes(pixels)
        toc += struct.pack("<III", 0xFFFD0002, nominal, offset)
        chunks += chunk
        offset += len(chunk)
    return header + toc + chunks


def build_xcursor(accent, label):
    svg_text = svg(accent, label)
    images = []
    with tempfile.TemporaryDirectory() as tmp:
        for nominal in NOMINAL_SIZES:
            scale = nominal / 32
            png = Path(tmp) / f"{nominal}.png"
            render_png(svg_text, scale, png)
            width, height, rgba = decode_png(png.read_bytes())
            hot_x = min(width - 1, math.floor(HOT_X * scale))
            hot_y = min(height - 1, math.floor(HOT_Y * scale))
            images.append((nominal, width, height, hot_x, hot_y, rgba))
    return xcursor(images)


def fingerprint(accent, label):
    return hashlib.sha1(f"v1|{accent.lower()}|{label}".encode()).hexdigest()[:12]


def ensure_theme(icons_dir, accent, label, default_accent, default_label):
    """Write the theme under icons_dir unless it is already current."""
    theme = Path(icons_dir) / THEME_NAME
    cursors = theme / "cursors"
    stamp = cursors / ".fingerprint"
    wanted = fingerprint(accent, label)
    if stamp.exists() and stamp.read_text().strip() == wanted:
        return theme

    is_default = accent.lower() == default_accent.lower() and label == default_label
    if is_default and BUNDLED.exists() and not shutil.which("rsvg-convert"):
        data = BUNDLED.read_bytes()
    else:
        data = build_xcursor(accent, label)

    if cursors.exists():
        shutil.rmtree(cursors)
    cursors.mkdir(parents=True)
    (cursors / "default").write_bytes(data)
    for name in SHAPE_NAMES:
        (cursors / name).symlink_to("default")
    (theme / "index.theme").write_text(
        f"[Icon Theme]\nName={THEME_NAME}\nComment=agentdesk agent cursor\n"
    )
    stamp.write_text(wanted)
    return theme


def preview(accent, label, out_path, scale=4):
    render_png(svg(accent, label), scale * 1.0, out_path)
