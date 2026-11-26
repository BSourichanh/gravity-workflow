#!/usr/bin/env python3
"""Deprecated wrapper delegating uninstall/cleanup commands to install.py."""

import sys
from pathlib import Path
from typing import List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import install  # noqa: E402


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print deprecation warning to stderr and delegate to install.main()."""
    print(
        "[DEPRECATED] Use 'python install.py --uninstall ...' instead of uninstall.py.",
        file=sys.stderr,
    )
    raw_args: List[str] = list(sys.argv[1:] if argv is None else argv)
    if "--uninstall" not in raw_args and "--workspace-only" not in raw_args:
        raw_args = ["--uninstall", *raw_args]
    return install.main(raw_args)


if __name__ == "__main__":
    raise SystemExit(main())
