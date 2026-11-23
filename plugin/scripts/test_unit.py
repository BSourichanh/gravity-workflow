#!/usr/bin/env python3
"""Lanceur rétrocompatible des suites de tests unitaires (100 % stdlib)."""

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = PLUGIN_ROOT.parent / "tests"
EXCLUDED_FILES = frozenset({"test_install.py", "test_uninstall.py"})


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str) -> unittest.TestSuite:
    """Charge les modules unitaires de tests/ hors tests d'installation/désinstallation."""
    suite = unittest.TestSuite()
    if not TESTS_DIR.is_dir():
        return suite
    tests_str = str(TESTS_DIR)
    if tests_str not in sys.path:
        sys.path.insert(0, tests_str)
    for test_file in sorted(TESTS_DIR.glob("test_*.py")):
        if test_file.name in EXCLUDED_FILES:
            continue
        suite.addTests(loader.loadTestsFromName(test_file.stem))
    return suite


if __name__ == "__main__":
    if not TESTS_DIR.is_dir():
        sys.stderr.write(f"Répertoire tests introuvable : {TESTS_DIR}\n")
        sys.exit(1)
    unittest.main(verbosity=2)
