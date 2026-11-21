# Validation, Tests & Développement Continu

Toutes les vérifications reposent exclusivement sur **Python 3.8+ (bibliothèque standard uniquement)** sans dépendance externe.

---

## Commandes de validation obligatoire

### Depuis la racine du dépôt `Gravity/` (Windows PowerShell ou POSIX)

```bash
# 1. Valider strictement le plugin autonome (manifeste, logo, agents, hooks + 20+ dry-runs, stdlib AST, zéro jargon)
python gravity-plugin/scripts/validate.py --strict

# 2. Valider strictement le socle de référence workspace-template/
python gravity-plugin/scripts/validate.py --workspace workspace-template --strict

# 3. Exécuter la suite de tests unitaires interne du plugin (26 tests)
python gravity-plugin/scripts/test_unit.py

# 4. Exécuter les tests d'intégration installateur & désinstalleur
python -m unittest discover -s tests -p "test_*.py" -v
```

### Checklist pré-release (`v0.1.0+`)
- [ ] `validate.py --strict` et `validate.py --workspace workspace-template --strict` retournent `0` erreur et `0` avertissement.
- [ ] `agy plugin validate ./gravity-plugin` retourne `[ok]` (`exit 0`).
- [ ] `test_unit.py` (26 tests) et `tests/test_*.py` (`test_install.py` + `test_uninstall.py`) passent intégralement.
- [ ] [`CHANGELOG.md`](../CHANGELOG.md), [`LICENSE`](../LICENSE), [`.gitignore`](../.gitignore) et [`.gitattributes`](../.gitattributes) sont à jour et aucun `__pycache__/` ou secret n'est suivi.

### Vérifications complémentaires

- **Test `/statusline` depuis `workspace-template/` :**
  ```powershell
  Push-Location workspace-template
  python ../gravity-plugin/observability/statusline.py
  '{"workspace":{"project_dir":"."},"model":{"display_name":"Gemini 4 Neon"},"context_window":{"used_percentage":14.2},"sandbox":{"enabled":true}}' | python ../gravity-plugin/observability/statusline.py
  Pop-Location
  ```
- **Validation CLI Antigravity (`agy`) :**
  ```bash
  # Sur agy v1.5+, sinon omettre --strict
  agy plugin validate --strict ./gravity-plugin
  ```
- **Initialisation et audit d'un projet cible :**
  ```bash
  python gravity-plugin/scripts/init_workspace.py --workspace <projet> --mode quickstart
  python gravity-plugin/scripts/validate.py --workspace <projet> --strict
  ```

---

## Architecture des validateurs (`gravity-plugin/scripts/validators/`)

- [`base.py`](../gravity-plugin/scripts/validators/base.py) : parseur frontmatter YAML, vérificateur AST `check_stdlib_only` (avec tolérance explicite aux modules locaux déclarés), contrôle `chmod 0755` sous POSIX, validation des agents (`readonly=True` interdit écriture/shell/`run_workflow`), validation structurelle `hooks.json` et exécution complète des dry-runs `PreToolUse` / `PostToolUse` / `SessionStart` / `Stop`.
- [`plugin_validator.py`](../gravity-plugin/scripts/validators/plugin_validator.py) : contrôle `plugin.json` (`name` kebab-case, `description <= 160` car., 3 `suggestedPrompts`), ratio carré du `logo.svg`, présence et conformité des skills `gravity/` (`description <= 160` car. + 10 fiches `references/`) et `caveman/` (`description <= 160` car.), rejet de toute ancienne skill héritée, absence de `Module XX`.
- [`workspace_validator.py`](../gravity-plugin/scripts/validators/workspace_validator.py) : vérifie `AGENTS.md`, expansion `@[label](path)`, règles (triggers valides, `01-safety-boundaries.md` `trigger: always_on` `<= 2 048` octets, règles `<= 24 000` octets, budget total `< 20 000` tokens), `mcp_config.json`, `evals/cases.json` (10–15 cas), `.agents/gravity/profile.json` (`mode`, `current_level`, `locked`, `pace`, `verification`, `caveman`, `caveman_locked`) et `.gitignore` (`*.lock`).

---

## Règles de portabilité & contribution

1. **Garder `python3` portable dans le dépôt source :** ne jamais exécuter `validate.py --fix-python-cmd` sur `gravity-plugin/` source (réserver ce flag à une copie installée sur Windows ; `install.py` l'applique automatiquement à la copie cible).
2. **Ne jamais copier-coller tout le code dans `docs/` :** documenter les contrats, invariants et liens relatifs vers `gravity-plugin/`, `workspace-template/` et `install.py`, puis mettre à jour la fiche `docs/` concernée à chaque changement (table dans [`docs/README.md`](README.md)).
3. **Permissions POSIX :** vérifier les droits d'exécution `0755` pour tout nouveau script ajouté dans `EXEC_SCRIPTS` ([`install.py`](../install.py) et [`plugin_validator.py`](../gravity-plugin/scripts/validators/plugin_validator.py)).
