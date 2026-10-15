"""Attach to (or launch) FTL and read its memory with process_vm_readv.

Linux's Yama setting `kernel.yama.ptrace_scope = 1` (Ubuntu's default) only
lets a process read the memory of its own descendants. The simplest way to
satisfy that is to let this module launch FTL, which makes the bot FTL's
parent. Attaching to an FTL you started from the desktop fails with EPERM.

ftl_host.py builds on this: it launches FTL once and serves reads over a
private Unix socket, so readers (HostClient) can restart without
restarting the game.
"""

import ctypes
import ctypes.util
import errno
import hashlib
import os
import socket
import struct
import subprocess
from pathlib import Path

from observer.memory import layout


class MemoryReadError(Exception):
    pass


class AttachError(Exception):
    pass


class _IoVec(ctypes.Structure):
    _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]


_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_libc.process_vm_readv.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(_IoVec), ctypes.c_ulong,
    ctypes.POINTER(_IoVec), ctypes.c_ulong,
    ctypes.c_ulong,
]
_libc.process_vm_readv.restype = ctypes.c_ssize_t
_libc.process_vm_writev.argtypes = _libc.process_vm_readv.argtypes
_libc.process_vm_writev.restype = ctypes.c_ssize_t

MAX_STRING_LENGTH = 8192
MAX_VECTOR_ITEMS = 1024


class View:
    """A block of remote memory copied in one read, with typed accessors."""

    def __init__(self, address: int, data: bytes):
        self.address = address
        self.data = data

    def u8(self, offset: int) -> int:
        return self.data[offset]

    def bool(self, offset: int) -> bool:
        return self.data[offset] != 0

    def i32(self, offset: int) -> int:
        return struct.unpack_from("<i", self.data, offset)[0]

    def f32(self, offset: int) -> float:
        return struct.unpack_from("<f", self.data, offset)[0]

    def ptr(self, offset: int) -> int:
        return struct.unpack_from("<Q", self.data, offset)[0]

    def pair_i(self, offset: int) -> tuple[int, int]:
        return struct.unpack_from("<ii", self.data, offset)

    def pair_f(self, offset: int) -> tuple[float, float]:
        return struct.unpack_from("<ff", self.data, offset)


class FTLProcess:
    def __init__(self, pid: int, popen: subprocess.Popen | None = None):
        self.pid = pid
        self.popen = popen

    # --- lifecycle ---------------------------------------------------------

    @classmethod
    def launch(cls, data_dir: Path, log_path: Path | None = None, display: str | None = None) -> "FTLProcess":
        """Start FTL.amd64 as a child process, the way the GOG launcher does (on `display`,
        e.g. the Xephyr window of game_display.py, when given)."""
        executable = data_dir / "FTL.amd64"
        env = dict(os.environ)
        if display:
            env["DISPLAY"] = display
        env["LANG"] = env.get("LC_ALL") or env.get("LANG", "")
        env["LC_ALL"] = ""
        env["LC_NUMERIC"] = "C"
        # No input-method server (IBus): it drops keys sent straight to FTL's window while
        # FTL is not focused, which the bot's background mode relies on. Typing in FTL
        # (ship names) still works with X's built-in input handling.
        env["XMODIFIERS"] = "@im=none"

        output = subprocess.DEVNULL
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            output = open(log_path, "ab")

        popen = subprocess.Popen(
            [str(executable)],
            cwd=data_dir,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return cls(popen.pid, popen)

    @classmethod
    def attach(cls, pid: int | None = None) -> "FTLProcess":
        """Attach to a running FTL. Only works for descendants under ptrace_scope=1."""
        if pid is None:
            pid = find_ftl_pid()
            if pid is None:
                raise AttachError("FTL.amd64 is not running.")

        process = cls(pid)
        try:
            process.read(layout.P_THE_APP, 8)
        except MemoryReadError as error:
            if getattr(error, "errno", None) == errno.EPERM:
                raise AttachError(
                    f"Permission denied reading PID {pid}. With ptrace_scope=1 only "
                    "FTL's parent can read it: close FTL and let this tool launch it."
                ) from error
            raise AttachError(str(error)) from error
        return process

    def alive(self) -> bool:
        if self.popen is not None:
            return self.popen.poll() is None
        return Path(f"/proc/{self.pid}").exists()

    def binary_matches(self) -> bool:
        """True when the running executable is the build the offsets belong to."""
        digest = hashlib.sha256()
        with open(f"/proc/{self.pid}/exe", "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest() == layout.BINARY_SHA256

    # --- raw reads ---------------------------------------------------------

    def read(self, address: int, size: int) -> bytes:
        if address == 0:
            raise MemoryReadError("null pointer")

        buffer = ctypes.create_string_buffer(size)
        local = _IoVec(ctypes.cast(buffer, ctypes.c_void_p), size)
        remote = _IoVec(ctypes.c_void_p(address), size)
        count = _libc.process_vm_readv(self.pid, ctypes.byref(local), 1, ctypes.byref(remote), 1, 0)

        if count != size:
            code = ctypes.get_errno() if count < 0 else 0
            error = MemoryReadError(
                f"read of {size} bytes at 0x{address:x} failed: "
                f"{os.strerror(code) if code else f'short read ({count})'}"
            )
            error.errno = code
            raise error

        return buffer.raw

    def set_speed_level(self, level: int):
        """The one write this project makes: FTL's own game-speed setting (CFPS::speedLevel,
        see layout.py). Nothing else in the game's memory is ever changed."""
        low, high = layout.SPEED_LEVEL_RANGE
        if not low <= level <= high:
            raise MemoryReadError(f"speed level {level} outside {low}..{high}")
        data = struct.pack("<i", level)
        buffer = ctypes.create_string_buffer(data, len(data))
        local = _IoVec(ctypes.cast(buffer, ctypes.c_void_p), len(data))
        remote = _IoVec(ctypes.c_void_p(layout.CFPS_SPEED_LEVEL), len(data))
        count = _libc.process_vm_writev(self.pid, ctypes.byref(local), 1, ctypes.byref(remote), 1, 0)
        if count != len(data):
            code = ctypes.get_errno() if count < 0 else 0
            raise MemoryReadError(f"setting the speed level failed: {os.strerror(code) if code else count}")

    def view(self, address: int, size: int) -> View:
        return View(address, self.read(address, size))

    # --- typed reads -------------------------------------------------------

    def ptr(self, address: int) -> int:
        return struct.unpack("<Q", self.read(address, 8))[0]

    def i32(self, address: int) -> int:
        return struct.unpack("<i", self.read(address, 4))[0]

    def bool(self, address: int) -> bool:
        return self.read(address, 1) != b"\x00"

    def string_at(self, data_pointer: int) -> str:
        """Decode a COW std::string given the pointer stored in the string object."""
        if data_pointer == 0:
            return ""
        length = struct.unpack("<Q", self.read(data_pointer - 24, 8))[0]
        if length == 0:
            return ""
        if length > MAX_STRING_LENGTH:
            raise MemoryReadError(f"implausible string length {length} at 0x{data_pointer:x}")
        return self.read(data_pointer, length).decode("utf-8", errors="replace")

    def string(self, address: int) -> str:
        """Decode the std::string object located at `address`."""
        return self.string_at(self.ptr(address))

    def vector_range(self, address: int, item_size: int) -> tuple[int, int]:
        """Return (begin, count) of the std::vector object located at `address`."""
        begin, end = struct.unpack("<QQ", self.read(address, 16))
        if begin == 0 or end <= begin:
            return begin, 0
        span = end - begin
        if span % item_size:
            raise MemoryReadError(f"vector at 0x{address:x} is not a multiple of {item_size}")
        count = span // item_size
        if count > MAX_VECTOR_ITEMS:
            raise MemoryReadError(f"implausible vector size {count} at 0x{address:x}")
        return begin, count

    def pointer_vector(self, address: int) -> list[int]:
        begin, count = self.vector_range(address, 8)
        if count == 0:
            return []
        return list(struct.unpack(f"<{count}Q", self.read(begin, count * 8)))

    def float_vector(self, address: int) -> list[float]:
        begin, count = self.vector_range(address, 4)
        if count == 0:
            return []
        return list(struct.unpack(f"<{count}f", self.read(begin, count * 4)))


# --- ftl_host.py protocol ------------------------------------------------------
# request:  <Q address><I size>
# response: <B ok><I length><length bytes>   data if ok, else a UTF-8 error message
REQUEST = struct.Struct("<QI")
RESPONSE_HEADER = struct.Struct("<BI")
SET_SPEED_LEVEL = 1   # request address meaning "set the speed level" (an older host answers with an error)
MAX_HOST_READ = 1 << 20


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    while size:
        chunk = sock.recv(size)
        if not chunk:
            raise ConnectionError("ftl_host closed the connection")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


class HostClient(FTLProcess):
    """Reads FTL's memory through a running ftl_host.py instead of directly."""

    def __init__(self, socket_path: Path):
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(str(socket_path))
        except OSError as error:
            raise AttachError(
                f"Cannot reach ftl_host at {socket_path} ({error}). Start it in another terminal with: ftl-host"
            ) from error
        # The host answers a read of address 0 with FTL's PID.
        pid = int(self._request(0, 0).decode())
        super().__init__(pid)

    def _request(self, address: int, size: int) -> bytes:
        self.sock.sendall(REQUEST.pack(address, size))
        ok, length = RESPONSE_HEADER.unpack(_recv_exact(self.sock, RESPONSE_HEADER.size))
        payload = _recv_exact(self.sock, length)
        if not ok:
            raise MemoryReadError(payload.decode("utf-8", errors="replace"))
        return payload

    def set_speed_level(self, level: int):
        """Ask ftl_host to set FTL's game speed (request address 1, the level in the size field)."""
        try:
            self._request(SET_SPEED_LEVEL, level & 0xFFFFFFFF)
        except (ConnectionError, OSError) as error:
            raise MemoryReadError(f"ftl_host connection lost: {error}") from error

    def read(self, address: int, size: int) -> bytes:
        if address == 0:
            raise MemoryReadError("null pointer")
        try:
            return self._request(address, size)
        except (ConnectionError, OSError) as error:
            raise MemoryReadError(f"ftl_host connection lost: {error}") from error


def find_ftl_pid() -> int | None:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if os.readlink(entry / "exe").endswith("/FTL.amd64"):
                return int(entry.name)
        except OSError:
            continue
    return None
