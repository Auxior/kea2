"""Translate Windows console breaks into Kea2's normal cleanup path."""

import signal
import sys


def main():
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    from kea2.cli import main as kea_main

    kea_main()


if __name__ == "__main__":
    main()
