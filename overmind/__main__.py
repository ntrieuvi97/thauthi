#!/usr/bin/env python3
"""Entrypoint for `python3 -m overmind`."""

import sys
from overmind.cli import main

if __name__ == "__main__":
    sys.exit(main())
