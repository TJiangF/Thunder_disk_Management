"""PyInstaller entry point for the packaged app."""

import sys

from thunder_sweeper.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
