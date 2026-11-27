#!/usr/bin/env python3
"""Validateur officiel Gravity Workflow (délègue à `_shared.validator_core` + `workspace_validator`)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT_DEFAULT = SCRIPT_DIR.parent
for _p in (str(PLUGIN_ROOT_DEFAULT), str(SCRIPT_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _shared import validator_core  # noqa: E402
from validators import Report, validate_plugin, validate_workspace  # noqa: E402
from validators.plugin_validator import WORKFLOW_LOCAL_MODULES, build_validator_config  # noqa: E402

WORKFLOW_ALLOWED_MODULES: frozenset[str] = WORKFLOW_LOCAL_MODULES

_BASE_CFG = build_validator_config(
    PLUGIN_ROOT_DEFAULT,
    allowed_local_modules=WORKFLOW_ALLOWED_MODULES,
    workspace_validator_fn=validate_workspace,
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Point d'entrée CLI unifié (--strict, --plugin-root, --fix-python-cmd, --json, --workspace)."""
    return validator_core.run_cli(_BASE_CFG, argv, description="Validateur Gravity Workflow")


if __name__ == "__main__":
    raise SystemExit(main())
