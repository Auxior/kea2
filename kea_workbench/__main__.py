import argparse
import os
from pathlib import Path
import signal
import socket
import sys
import threading

import uvicorn

from .server import create_app
from .storage import write_json


def bind_listener(host, preferred_port):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, preferred_port))
        listener.listen(2048)
        return listener
    except BaseException:
        listener.close()
        raise


def watch_parent(server, finished):
    # Only the launcher owns the pipe's write end. EOF also detects SIGKILL,
    # terminal closure, and forced termination that bypass Python finally blocks.
    descriptor = sys.stdin.fileno()
    while True:
        try:
            # Avoid holding a BufferedReader lock while a daemon thread waits;
            # that lock would make normal Python interpreter shutdown abort.
            command = os.read(descriptor, 1)
        except OSError:
            command = b""
        if command == b"s":
            server.should_exit = True
        elif not command:
            server.should_exit = True
            # A vanished terminal must not leave a bound port indefinitely if
            # task cleanup or a network request prevents graceful shutdown.
            if not finished.wait(5):
                os._exit(0)
            return


def main():
    parser = argparse.ArgumentParser(description="启动 Kea2 Web 测试工作台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4320)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--watch-parent", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ready-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("端口必须为 0–65535")
    root = args.root.resolve()
    listener = bind_listener(args.host, args.port)
    finished = threading.Event()
    try:
        port = listener.getsockname()[1]
        browser_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(args.host, args.host)
        if ":" in browser_host:
            browser_host = f"[{browser_host}]"
        print(f"工作台访问地址：http://{browser_host}:{port}/", flush=True)
        server = uvicorn.Server(uvicorn.Config(create_app(root), host=args.host, port=port))
        if args.ready_file:
            write_json(args.ready_file, {"root": str(root), "port": port, "pid": os.getpid()})
        if args.watch_parent:
            if hasattr(signal, "SIGHUP"):
                signal.signal(signal.SIGHUP, lambda *_: setattr(server, "should_exit", True))
            threading.Thread(target=watch_parent, args=(server, finished), daemon=True).start()
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            pass
    finally:
        listener.close()
        finished.set()


if __name__ == "__main__":
    main()
