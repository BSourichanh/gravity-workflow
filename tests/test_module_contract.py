#!/usr/bin/env python3
"""Tests unitaires du contrat plugin/config/module.json v1 et SPI-2 capture_policy."""

import copy
import json
import unittest

from _unit_helpers import PLUGIN_ROOT, ensure_plugin_paths

ensure_plugin_paths()

from _shared.validator_core import Report, validate_module_contract  # noqa: E402


class TestModuleContractSPI2(unittest.TestCase):
    """Vérifie le contrat module.json v1 et la déclaration SPI-2 capture_policy."""

    def test_module_json_encoding_and_capture_policy_spi2(self) -> None:
        mod_path = PLUGIN_ROOT / "config" / "module.json"
        raw = mod_path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "module.json ne doit pas contenir de BOM UTF-8")
        self.assertNotIn(b"\r", raw, "module.json doit utiliser uniquement LF")

        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["id"], "workflow")
        self.assertEqual(data["plugin_name"], "gravity-workflow")
        self.assertEqual(data["commands"], ["/gravity-workflow", "/caveman"])

        report = Report()
        validated = validate_module_contract(
            PLUGIN_ROOT,
            report,
            expected_plugin_name="gravity-workflow",
            expected_module_id="workflow",
        )
        self.assertIsNotNone(validated)
        self.assertTrue(report.ok(strict=True), f"Erreurs/avertissements contrat: {report.errors} {report.warnings}")

        cp = data.get("capture_policy")
        self.assertIsInstance(cp, dict)
        self.assertEqual(cp["commands"], ["/gravity-workflow"])
        self.assertEqual(cp["exclude_subcommands"], ["status", "evals", "init", "help"])
        self.assertIs(cp["consultative_bypass"], True)
        self.assertEqual(cp["label_prefix"], "workflow")

    def test_validate_module_contract_rejects_invalid_capture_policy(self) -> None:
        base = json.loads((PLUGIN_ROOT / "config" / "module.json").read_text(encoding="utf-8"))

        bad_type = copy.deepcopy(base)
        bad_type["capture_policy"] = "invalid"
        rep_type = Report()
        validate_module_contract(bad_type, rep_type)
        self.assertFalse(rep_type.ok(strict=True))

        bad_bypass = copy.deepcopy(base)
        bad_bypass["capture_policy"]["consultative_bypass"] = "yes"
        rep_bypass = Report()
        validate_module_contract(bad_bypass, rep_bypass)
        self.assertFalse(rep_bypass.ok(strict=True))

        bad_prefix = copy.deepcopy(base)
        bad_prefix["capture_policy"]["label_prefix"] = ""
        rep_prefix = Report()
        validate_module_contract(bad_prefix, rep_prefix)
        self.assertFalse(rep_prefix.ok(strict=True))


if __name__ == "__main__":
    unittest.main()
