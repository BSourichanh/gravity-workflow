# Gravity Workflow — Instructions Agents

## Périmètre & Architecture
Harnais multi-agents natif Google Antigravity, 100 % sans dépendance :
- `gravity-workflow-plugin/` : moteur (skills `/gravity-workflow` & `/caveman`, agents, hooks fail-closed, workflows, evals, observabilité, sidecar).
- `workspace-template/` : empreinte cible (13 fichiers).
- `install.py`, `uninstall.py` : installeur/désinstalleur multiplateformes ; [`docs/`](docs/README.md) : documentation vivante.
- **Périmètre séparé :** coûts, tokens et historiques exclus (gérés dans `../gravity-metrics`, hub [`../gravity`](../gravity/)).

## Invariants
- **Branche `dev` obligatoire :** tout développement s'effectue sur `dev` (jamais directement sur `main`).
- **Conventions Git & versioning (chef d'orchestre uniquement) :** avant tout `commit`, `push`, tag ou release (sur accord utilisateur), seul l'agent principal applique les conventions (`dev` / branche dédiée, jamais de commit/push direct sur `main`, *Conventional Commits*, SemVer dans `gravity-workflow-plugin/plugin.json` + `CHANGELOG.md`, tags annotés `vX.Y.Z`, validations/tests avant commit) ; sous-agents et workers `swarm` ne committent/poussent/taguent jamais.
- **100 % bibliothèque standard Python 3 :** zéro dépendance tierce dans `gravity-workflow-plugin/`, `workspace-template/`, `install.py`, `uninstall.py`, `tests/`.
- **Documentation vivante :** toute modification dans `gravity-workflow-plugin/`, `workspace-template/`, `install.py` ou `uninstall.py` met à jour [`docs/`](docs/README.md) dans le même lot.
- **Intégrité du socle :** ne modifier `gravity-workflow-plugin/` ou `workspace-template/` que sur demande explicite, en gardant `hooks.json` et `sidecar.json` portables (`"python3"`).
- **Zéro jargon interdit :** jamais `Module XX` dans `gravity-workflow-plugin/skills/` ni `docs/`.

## Vérifications obligatoires
- `python gravity-workflow-plugin/scripts/validate.py --strict`
- `python gravity-workflow-plugin/scripts/validate.py --workspace workspace-template --strict`
- `agy plugin validate ./gravity-workflow-plugin`
- `python gravity-workflow-plugin/scripts/test_unit.py`
- `python -m unittest discover -s tests -p "test_*.py" -v`
