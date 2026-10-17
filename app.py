#!/usr/bin/env python3
"""Entry point. All application code lives in the manager/ package.

Restarting: the panel can restart itself (after an update, or from Settings) by exiting with
RESTART_EXIT_CODE. In dev mode the Werkzeug reloader relaunches it. Otherwise this file runs the real
server in a child process and relaunches it, so the terminal window never has to be closed and reopened."""
import atexit
import os
import subprocess
import sys
import threading
import time

from manager import app
from manager.automation import start_background_threads
from manager.config import DEV_MODE, HOST, LOOPBACK_HOSTS, PORT, RESTART_EXIT_CODE
from manager.lifecycle import shutdown_servers
from manager.util import psutil


def is_supervisor_parent() -> bool:
    return __name__ == "__main__" and not DEV_MODE and os.environ.get("MCM_CHILD") != "1"


def owns_background_work() -> bool:
    """Only the process that serves requests runs schedules; the reloader parent and the supervisor must not."""
    if DEV_MODE:
        return os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    return not is_supervisor_parent()


def supervise() -> int:
    env = {**os.environ, "MCM_CHILD": "1", "MCM_PARENT_PID": str(os.getpid())}
    while True:
        child = subprocess.Popen([sys.executable, os.path.abspath(__file__)], env=env)
        try:
            code = child.wait()
        except KeyboardInterrupt:  # Ctrl+C reaches the child too; give it time to shut down
            try:
                child.wait(timeout=30)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                child.kill()
            return 0
        if code != RESTART_EXIT_CODE:
            return code
        print("\n  Restarting the manager...\n", flush=True)


def exit_when_supervisor_dies():
    """If the supervisor is killed (not closed with Ctrl+C), do not linger as an orphan holding the port."""
    parent = int(os.environ.get("MCM_PARENT_PID") or 0)
    if not parent or psutil is None:
        return

    def watch():
        while True:
            time.sleep(5)
            if not psutil.pid_exists(parent):
                os._exit(0)

    threading.Thread(target=watch, daemon=True, name="parent-watch").start()


def serve():
    try:
        from waitress import serve as waitress_serve
        waitress_serve(app, host=HOST, port=PORT, threads=8)
    except ImportError:
        app.run(host=HOST, port=PORT, debug=False, threaded=True)


def lan_addresses() -> list:
    """Addresses a phone on the same network can type in."""
    import socket
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith(("127.", "169.254.")) and address not in found:
                found.append(address)
    except OSError:
        pass
    return found


if HOST not in LOOPBACK_HOSTS and DEV_MODE:
    # Debug mode serves an interactive debugger, which must never be reachable from the network.
    sys.exit("Refusing to listen on the network in debug mode. Start with start-lan.bat (it turns debug mode off).")

if owns_background_work():
    start_background_threads()

if __name__ == "__main__":
    if os.environ.get("MCM_CHILD") != "1":
        print("=" * 55)
        print("  Minecraft All-in-One Server Manager v2")
        print(f"  http://127.0.0.1:{PORT}")
        if HOST not in LOOPBACK_HOSTS:
            for address in lan_addresses():
                print(f"  On your phone (same Wi-Fi): http://{address}:{PORT}")
        print("=" * 55)
    if DEV_MODE:
        app.run(host=HOST, port=PORT, debug=True, use_reloader=True, threaded=True)
    elif is_supervisor_parent():
        sys.exit(supervise())
    else:
        exit_when_supervisor_dies()
        # Not in dev mode: the reloader exits on every file save, and servers should not stop each time.
        atexit.register(shutdown_servers)
        serve()
