#!/usr/bin/env python3
"""Helpers partagés pour les suites de tests unitaires (100 % stdlib)."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterable, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugin"


def ensure_plugin_paths() -> Path:
    """Ajoute les sous-dossiers moteur du plugin à sys.path (idempotent) et retourne PLUGIN_ROOT."""
    for sub in ("scripts", "evals", "observability", "hooks"):
        path_str = str(PLUGIN_ROOT / sub)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
    return PLUGIN_ROOT


def load_live_widget_server():
    """Charge dynamiquement plugin/sidecars/live-widget/server.py."""
    server_path = PLUGIN_ROOT / "sidecars/live-widget/server.py"
    spec = importlib.util.spec_from_file_location("live_widget_server", server_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_gravity_workspace(base: Path, name: str = "gravity_ws") -> Path:
    """Crée un workspace minimal marqué Gravity Workflow (.agents/gravity-workflow/profile.json)."""
    ws = base / name
    (ws / ".agents/gravity-workflow").mkdir(parents=True, exist_ok=True)
    (ws / ".agents/gravity-workflow/profile.json").write_text("{}", encoding="utf-8")
    return ws


def assert_run_commands_decision(
    case: unittest.TestCase,
    safety_policy_mod,
    workspaces: Iterable[Path],
    commands: Iterable[str],
    expected: str,
    label: str = "commande",
) -> None:
    """Vérifie qu'une série de commandes run_command obtient la décision attendue sur chaque workspace."""
    for ws in workspaces:
        for cmd in commands:
            dec, reason = safety_policy_mod.evaluate_tool_call(
                {"toolCall": {"name": "run_command", "args": {"CommandLine": cmd}}},
                ws,
            )
            case.assertEqual(
                dec,
                expected,
                f"Décision inattendue ({ws.name}, {label}) sur {cmd!r}: attendu {expected}, obtenu {dec} ({reason})",
            )


class WorkflowEnvTestCase(unittest.TestCase):
    """TestCase isolant GRAVITY_WORKFLOW_HOME, GRAVITY_HOME et le registre dans un répertoire temporaire."""

    _ENV_KEYS: Tuple[str, ...] = (
        "GRAVITY_WORKFLOW_HOME",
        "GRAVITY_HOME",
        "GRAVITY_WORKFLOW_SKIP_REGISTRY",
        "GRAVITY_WORKFLOW_STATE_PATH",
    )

    def setUp(self) -> None:
        self._tmp_home = tempfile.TemporaryDirectory()
        self._saved_env = {k: os.environ.get(k) for k in self._ENV_KEYS}
        os.environ["GRAVITY_WORKFLOW_HOME"] = str(Path(self._tmp_home.name) / "wf_home")
        os.environ["GRAVITY_WORKFLOW_SKIP_REGISTRY"] = "1"
        os.environ.pop("GRAVITY_WORKFLOW_STATE_PATH", None)
        os.environ.pop("GRAVITY_HOME", None)

    def tearDown(self) -> None:
        for k, val in self._saved_env.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val
        self._tmp_home.cleanup()
