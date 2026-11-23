# Validation, Tests & Développement Continu

Toutes les vérifications reposent exclusivement sur **Python 3.10+ (bibliothèque standard uniquement)** sans dépendance externe.

---

## Commandes de validation obligatoire

### Depuis la racine du dépôt `gravity-workflow/` (Windows PowerShell ou POSIX)

```bash
# 1. Valider strictement le plugin autonome (manifeste 9 champs, config/module.json v1, logo, agents, hooks + dry-runs, stdlib AST, zéro jargon)
python plugin/scripts/validate.py --strict

# 2. Valider strictement le socle de référence workspace-template/
python plugin/scripts/validate.py --workspace workspace-template --strict

# 3. Valider via Antigravity CLI (agy)
agy plugin validate ./plugin --strict

# 4. Exécuter la suite de tests unitaires interne du plugin
python plugin/scripts/test_unit.py

# 5. Exécuter les tests d'intégration installateur & désinstalleur
python -m unittest discover -s tests -p "test_*.py" -v
```

### Checklist pré-release (`v0.2.0+`)
- [ ] `validate.py --strict` et `validate.py --workspace workspace-template --strict` retournent `0` erreur et `0` avertissement.
- [ ] `agy plugin validate ./plugin --strict` retourne `Validation passed` (`exit 0`).
- [ ] `test_unit.py` et `tests/test_*.py` (`test_install.py` couvrant installation + désinstallation/nettoyage unifiés `install.py --uninstall` et `test_uninstall.py` couvrant la parité + avertissement `[DEPRECATED]` du wrapper mince `uninstall.py`) passent intégralement.
- [ ] [`CHANGELOG.md`](../CHANGELOG.md), [`LICENSE`](../LICENSE), [`.gitignore`](../.gitignore) et [`.gitattributes`](../.gitattributes) sont à jour et aucun `__pycache__/`, `.agents/` ou secret n'est suivi.

### Vérifications complémentaires

- **Test `/statusline` depuis `workspace-template/` :**
  ```powershell
  Push-Location workspace-template
  python ../plugin/observability/statusline.py
  '{"workspace":{"project_dir":"."},"model":{"display_name":"Gemini 4 Neon"},"context_window":{"used_percentage":14.2},"sandbox":{"enabled":true}}' | python ../plugin/observability/statusline.py
  Pop-Location
  ```
- **Initialisation et audit d'un projet cible :**
  ```bash
  python plugin/scripts/init_workspace.py --workspace <projet> --mode quickstart
  python plugin/scripts/validate.py --workspace <projet> --strict
  ```

---

## Architecture des validateurs (`plugin/scripts/validators/`)

- [`base.py`](../plugin/scripts/validators/base.py) : parseur frontmatter YAML, vérificateur AST `check_stdlib_only` (avec tolérance explicite aux modules locaux déclarés), contrôle `chmod 0755` sous POSIX, validation des agents (`readonly=True` interdit écriture/shell/`run_workflow`), validation structurelle `hooks.json` et exécution complète des dry-runs `PreToolUse` / `PostToolUse` / `SessionStart` / `Stop`.
- [`plugin_validator.py`](../plugin/scripts/validators/plugin_validator.py) : contrôle strict des 9 champs officiels de `plugin.json` (+ `disabled` optionnel, rejet des clés non officielles et du BOM UTF-8), validation du contrat [`config/module.json`](../plugin/config/module.json) v1 (`validate_module_contract`), ratio carré du `logo.svg`, conformité des skills `gravity-workflow/` (`description <= 160` car. + 10 fiches `references/`) et `caveman/` (`description <= 160` car.), rejet de toute ancienne skill héritée (`gravity`, ...), absence de `Module XX`.
- [`workspace_validator.py`](../plugin/scripts/validators/workspace_validator.py) : vérifie `AGENTS.md`, expansion `@[label](path)`, règles (triggers valides, `01-safety-boundaries.md` `trigger: always_on` `<= 2 048` octets, règles `<= 24 000` octets, budget total `< 20 000` tokens), `mcp_config.json`, `evals/cases.json` (10–15 cas), `.agents/gravity-workflow/profile.json` (`mode`, `current_level`, `locked`, `pace`, `verification`, `caveman`, `caveman_locked`) et `.gitignore` (`*.lock`).

---

## Règles de portabilité & contribution

1. **Garder `python3` portable dans le dépôt source :** ne jamais exécuter `validate.py --fix-python-cmd` sur `plugin/` source (réserver ce flag à une copie installée sur Windows ; `install.py` l'applique automatiquement à la copie cible).
2. **Ne jamais copier-coller tout le code dans `docs/` :** documenter les contrats, invariants et liens relatifs vers `plugin/`, `workspace-template/` et `install.py`, puis mettre à jour la fiche `docs/` concernée à chaque changement (table dans [`docs/README.md`](README.md)).
3. **Permissions POSIX :** vérifier les droits d'exécution `0755` pour tout nouveau script ajouté dans `EXEC_SCRIPTS` ([`install.py`](../install.py) et [`plugin_validator.py`](../plugin/scripts/validators/plugin_validator.py)).
