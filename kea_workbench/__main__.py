import argparse
from pathlib import Path

import uvicorn

from .server import create_app


def main():
    parser = argparse.ArgumentParser(description="启动 Kea2 Web 测试工作台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4320)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    uvicorn.run(create_app(args.root), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
