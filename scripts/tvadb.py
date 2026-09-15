#!/usr/bin/env python3
"""入口薄壳：python3 scripts/tvadb.py <子命令>。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tvadb.cli import main  # noqa: E402
if __name__ == "__main__":
    main()
