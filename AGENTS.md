# Gravity — Instructions Agents

## Périmètre & Architecture
Harnais multi-agents natif Google Antigravity, 100 % sans dépendance :
- `gravity-plugin/` : moteur (skills `/gravity` & `/caveman`, sous-agents `planner`/`builder`/`reviewer`, hooks fail-closed, workflows, evals, observabilité, sidecar).
- `workspace-template/` : empreinte cible (13 fichiers).
- `install.py`, `uninstall.py` : installeur/désinstalleur multiplateformes ; [`docs/`](docs/README.md) : documentation vivante.
- **Périmètre séparé :** coûts, tokens, cache et historiques de sessions Antigravity sont exclus et gérés dans `../gravity-metrics`.

## Invariants
- **Branche `dev` obligatoire :** tout développement et toute modification s'effectuent sur `dev` (jamais directement sur `main`).
- **Conventions Git & versioning (chef d'orchestre uniquement) :** avant tout `git commit`, `git push`, tag ou release (sur demande explicite de l'utilisateur), seul l'agent principal applique les conventions (`dev` ou branche dédiée, jamais de commit/push direct sur `main`, *Conventional Commits*, SemVer synchronisé dans `gravity-plugin/plugin.json` + `CHANGELOG.md` *Keep a Changelog*, tags annotés `vX.Y.Z` lors d'une release, validations/tests complets avant commit & push) ; `planner`, `builder`, `reviewer` et workers `swarm` ne committent, ne poussent et ne taguent jamais.
- **100 % bibliothèque standard Python 3 :** aucune dépendance tierce dans `gravity-plugin/`, `workspace-template/`, `install.py`, `uninstall.py` ni `tests/`.
- **Documentation vivante :** toute modification fonctionnelle ou structurelle dans `gravity-plugin/`, `workspace-template/`, `install.py` ou `uninstall.py` met à jour la fiche correspondante dans [`docs/`](docs/README.md) dans le même lot.
- **Intégrité du socle :** ne modifier `gravity-plugin/` ou `workspace-template/` que sur demande explicite, en conservant `hooks.json` et `sidecar.json` portables (`"python3"`) dans le dépôt source.
- **Zéro jargon interdit :** ne jamais employer `Module XX` dans `gravity-plugin/skills/` ni dans `docs/`.

## Vérifications obligatoires
- `python gravity-plugin/scripts/validate.py --strict`
- `python gravity-plugin/scripts/validate.py --workspace workspace-template --strict`
- `python gravity-plugin/scripts/test_unit.py`
- `python -m unittest discover -s tests -p "test_*.py" -v`
