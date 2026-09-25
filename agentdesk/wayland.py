"""Tiny Wayland wire-protocol client, just enough for zwlr_virtual_pointer_v1.

Pure stdlib so nothing needs compiling. Only the requests and events the
virtual pointer needs are implemented; unknown events are skipped.
"""

import os
import socket
import struct
import time

WL_DISPLAY_ID = 1

# wl_display requests / events
_DISPLAY_SYNC = 0
_DISPLAY_GET_REGISTRY = 1
_DISPLAY_EV_ERROR = 0
_DISPLAY_EV_DELETE_ID = 1

# wl_registry
_REGISTRY_BIND = 0
_REGISTRY_EV_GLOBAL = 0

# wl_callback
_CALLBACK_EV_DONE = 0

# zwlr_virtual_pointer_manager_v1
_MANAGER_CREATE_VIRTUAL_POINTER = 0

# zwlr_virtual_pointer_v1
_VP_MOTION_ABSOLUTE = 1
_VP_BUTTON = 2
_VP_FRAME = 4
_VP_AXIS_SOURCE = 5
_VP_AXIS_DISCRETE = 7
_VP_DESTROY = 8

BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112
BUTTONS = {"left": BTN_LEFT, "right": BTN_RIGHT, "middle": BTN_MIDDLE}

AXIS_VERTICAL = 0
AXIS_HORIZONTAL = 1
AXIS_SOURCE_WHEEL = 0


class WaylandError(RuntimeError):
    pass


def _string(value):
    data = value.encode() + b"\0"
    padded = data + b"\0" * (-len(data) % 4)
    return struct.pack("<I", len(data)) + padded


def _fixed(value):
    return struct.pack("<i", int(round(value * 256)))


def _uint(*values):
    return struct.pack("<" + "I" * len(values), *values)


def _now_ms():
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


class Connection:
    def __init__(self, display):
        runtime = os.environ.get("XDG_RUNTIME_DIR", "")
        path = display if display.startswith("/") else os.path.join(runtime, display)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        try:
            self.sock.connect(path)
        except OSError as exc:
            raise WaylandError(f"cannot connect to Wayland display {path}: {exc}") from exc
        self._next_id = 2
        self._buf = b""
        self.globals = {}
        self._registry = self._new_id()
        self.send(WL_DISPLAY_ID, _DISPLAY_GET_REGISTRY, _uint(self._registry))
        self.roundtrip()

    def _new_id(self):
        new = self._next_id
        self._next_id += 1
        return new

    def send(self, obj, opcode, payload=b""):
        header = struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode)
        self.sock.sendall(header + payload)

    def bind(self, interface, version):
        if interface not in self.globals:
            raise WaylandError(f"compositor does not offer {interface}")
        name, offered = self.globals[interface]
        new = self._new_id()
        payload = _uint(name) + _string(interface) + _uint(min(version, offered), new)
        self.send(self._registry, _REGISTRY_BIND, payload)
        return new

    def roundtrip(self):
        callback = self._new_id()
        self.send(WL_DISPLAY_ID, _DISPLAY_SYNC, _uint(callback))
        while True:
            obj, opcode, body = self._read_event()
            if obj == callback and opcode == _CALLBACK_EV_DONE:
                return
            self._dispatch(obj, opcode, body)

    def _read_event(self):
        while True:
            if len(self._buf) >= 8:
                obj, word = struct.unpack_from("<II", self._buf)
                size = word >> 16
                if len(self._buf) >= size:
                    body = self._buf[8:size]
                    self._buf = self._buf[size:]
                    return obj, word & 0xFFFF, body
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WaylandError("Wayland connection closed")
            self._buf += chunk

    def _dispatch(self, obj, opcode, body):
        if obj == WL_DISPLAY_ID and opcode == _DISPLAY_EV_ERROR:
            _, code, length = struct.unpack_from("<III", body)
            message = body[12 : 12 + length - 1].decode(errors="replace")
            raise WaylandError(f"protocol error {code}: {message}")
        if obj == self._registry and opcode == _REGISTRY_EV_GLOBAL:
            name, length = struct.unpack_from("<II", body)
            interface = body[8 : 8 + length - 1].decode()
            offset = 8 + length + (-length % 4)
            (version,) = struct.unpack_from("<I", body, offset)
            self.globals[interface] = (name, version)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class VirtualPointer:
    """A pointer device inside one compositor. Coordinates are output pixels."""

    def __init__(self, display, width, height):
        self.width = width
        self.height = height
        self.conn = Connection(display)
        manager = self.conn.bind("zwlr_virtual_pointer_manager_v1", 1)
        self.obj = self.conn._new_id()
        # seat = null lets the compositor pick its default seat.
        self.conn.send(manager, _MANAGER_CREATE_VIRTUAL_POINTER, _uint(0, self.obj))
        self.conn.roundtrip()

    def motion(self, x, y, sync=True):
        x = max(0, min(self.width - 1, int(round(x))))
        y = max(0, min(self.height - 1, int(round(y))))
        self.conn.send(self.obj, _VP_MOTION_ABSOLUTE, _uint(_now_ms(), x, y, self.width, self.height))
        self._frame(sync)

    def button(self, button, pressed, sync=True):
        self.conn.send(self.obj, _VP_BUTTON, _uint(_now_ms(), button, 1 if pressed else 0))
        self._frame(sync)

    def scroll(self, axis, clicks):
        step = 1 if clicks > 0 else -1
        for _ in range(abs(clicks)):
            self.conn.send(self.obj, _VP_AXIS_SOURCE, _uint(AXIS_SOURCE_WHEEL))
            self.conn.send(
                self.obj,
                _VP_AXIS_DISCRETE,
                _uint(_now_ms(), axis) + _fixed(15 * step) + struct.pack("<i", step),
            )
            self._frame(True)
            time.sleep(0.03)

    def _frame(self, sync):
        self.conn.send(self.obj, _VP_FRAME)
        if sync:
            self.conn.roundtrip()

    def close(self):
        try:
            self.conn.send(self.obj, _VP_DESTROY)
            self.conn.roundtrip()
        except (OSError, WaylandError):
            pass
        self.conn.close()
