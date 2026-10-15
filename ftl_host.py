"""Launch FTL and serve read-only access to its memory over a Unix socket.

Ubuntu's ptrace_scope=1 only lets FTL's parent read its memory. ftl-host
becomes that parent and lives in the background for as long as FTL runs, so
the bot and dashboard can be restarted freely:

    ftl-host                 start FTL + host in the background (returns at once)
    ftl-host --foreground    same, but stay attached to this terminal

You rarely need to call it yourself: ftl-bot, ftl-live and ftl-calibrate
start it when FTL is not running yet. It stops on its own when FTL closes.

The socket (data/ftl_host.sock) is created with mode 0600 and answers memory
reads. The only write it makes is FTL's own game-speed setting (CFPS::speedLevel,
range-checked), used by the dashboard's speed control.
"""

import argparse
import os
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import game_display
from config import FTL_DATA_DIR, LOG_DIR, NESTED_DISPLAY, PROJECT_DIR
from observer.memory.process import (
    MAX_HOST_READ, REQUEST, RESPONSE_HEADER, SET_SPEED_LEVEL, AttachError, FTLProcess, MemoryReadError,
    _recv_exact, find_ftl_pid,
)

SOCKET_PATH = PROJECT_DIR / "data" / "ftl_host.sock"
HOST_LOG = LOG_DIR / "ftl_host.log"
UNREADABLE_FTL = ("FTL is already running (PID {pid}) without ftl-host, so the bot cannot read it. "
                  "In FTL: Esc -> Save and Quit, close the game, then try again.")


def make_handler(proc: FTLProcess):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            while True:
                try:
                    address, size = REQUEST.unpack(_recv_exact(self.request, REQUEST.size))
                except ConnectionError:
                    return

                if address == 0 and size == 0:
                    ok, payload = 1, str(proc.pid).encode()
                elif address == SET_SPEED_LEVEL:
                    level = struct.unpack("<i", struct.pack("<I", size))[0]
                    try:
                        proc.set_speed_level(level)
                        ok, payload = 1, b""
                    except MemoryReadError as error:
                        ok, payload = 0, str(error).encode()
                elif size > MAX_HOST_READ:
                    ok, payload = 0, f"read of {size} bytes exceeds {MAX_HOST_READ}".encode()
                else:
                    try:
                        ok, payload = 1, proc.read(address, size)
                    except MemoryReadError as error:
                        ok, payload = 0, str(error).encode()

                self.request.sendall(RESPONSE_HEADER.pack(ok, len(payload)) + payload)

    return Handler


def host_is_up() -> bool:
    """True when a live ftl-host answers on SOCKET_PATH; removes a stale socket file."""
    if not SOCKET_PATH.exists():
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(SOCKET_PATH))
        return True
    except OSError:
        SOCKET_PATH.unlink(missing_ok=True)
        return False
    finally:
        probe.close()


def start_in_background(timeout: float = 15.0) -> bool:
    HOST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(HOST_LOG, "ab") as log:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--foreground"],
            cwd=PROJECT_DIR, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if host_is_up():
            return True
        time.sleep(0.2)
    return False


def ensure_host():
    """Make sure an ftl-host is serving, starting FTL through it if needed."""
    if host_is_up():
        return
    running = find_ftl_pid()
    if running is not None:
        raise AttachError(UNREADABLE_FTL.format(pid=running))
    print("[ftl] Starting FTL through ftl-host in the background...")
    if not start_in_background():
        raise AttachError(f"ftl-host did not come up; see {HOST_LOG}")


def serve():
    running = find_ftl_pid()
    if running is not None:
        print(f"[ftl_host] {UNREADABLE_FTL.format(pid=running)}")
        sys.exit(1)

    SOCKET_PATH.unlink(missing_ok=True)
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)

    display, xephyr = None, None
    if NESTED_DISPLAY:
        try:
            display, xephyr = game_display.start()
            print(f"[ftl_host] FTL's own window: Xephyr display {display}", flush=True)
        except (OSError, RuntimeError, StopIteration) as error:
            print(f"[ftl_host] no Xephyr window ({error}); FTL runs on the desktop", flush=True)
    proc = FTLProcess.launch(FTL_DATA_DIR, LOG_DIR / "ftl_stdout.log", display=display)
    print(f"[ftl_host] Launched FTL (PID {proc.pid}). Serving reads on {SOCKET_PATH}", flush=True)

    old_umask = os.umask(0o177)
    try:
        server = socketserver.ThreadingUnixStreamServer(str(SOCKET_PATH), make_handler(proc))
    finally:
        os.umask(old_umask)
    server.daemon_threads = True

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proc.popen.wait()
        print("[ftl_host] FTL exited.", flush=True)
    except KeyboardInterrupt:
        print("\n[ftl_host] Stopped. FTL keeps running but can no longer be read; "
              "Save and Quit it and start again.")
    finally:
        server.shutdown()
        server.server_close()
        SOCKET_PATH.unlink(missing_ok=True)
        game_display.stop(xephyr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--foreground", action="store_true", help="stay attached to this terminal")
    args = parser.parse_args()

    if args.foreground:
        serve()
        return

    if host_is_up():
        print("[ftl_host] Already running.")
        return
    try:
        ensure_host()
    except AttachError as error:
        print(f"[ftl_host] {error}")
        sys.exit(1)
    print(f"[ftl_host] Running in the background; it stops when FTL closes. Log: {HOST_LOG}")


if __name__ == "__main__":
    main()
