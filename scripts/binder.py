#!/usr/bin/env python3
from __future__ import annotations

from binder.cli import main

if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        import sys
        print(f"tmux-binder: {error}", file=sys.stderr)
        raise SystemExit(1)
