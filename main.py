#!/usr/bin/env python3
"""Entry point for the organisers' harness:  python3 main.py <ingest|brief|ask|forget> ...  (see assist/cli.py)"""
import sys

from assist.cli import main

if __name__ == "__main__":
    sys.exit(main())
