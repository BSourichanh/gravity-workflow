# Gravity Workflow — Instructions Agents

## Périmètre & Architecture
Harnais multi-agents natif Google Antigravity, 100 % sans dépendance :
- `plugin/` : moteur autonome (`plugin.json` 9 champs, `config/module.json` v1 `id: "workflow"`, skills `/gravity-workflow` & `/caveman`, agents, hooks fail-closed, workflows, evals, observabilité, sidecar transition `4789`).
- `workspace-template/` : empreinte cible (13 fichiers).
- `install.py`, `uninstall.py` : installeur/désinstalleur (`GRAVITY_WORKFLOW_HOME` > `GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/`) ; [`docs/`](docs/README.md) : documentation vivante.
- **Périmètre séparé :** coûts, tokens et historiques gérés dans `../gravity-metrics`, hub [`../gravity`](../gravity/).

## Invariants
- **Branche `dev` obligatoire :** tout développement s'effectue sur `dev` (jamais directement sur `main`).
- **Principes & hygiène :** appliquer **KISS**, **YAGNI**, **DRY**, **SOLID**, **CUPID** ; encodage **UTF-8 sans BOM** et fins de ligne **LF**.
- **Conventions Git & versioning (chef d'orchestre uniquement) :** avant `commit`, `push`, tag ou release (sur accord utilisateur), seul l'agent principal applique les conventions (`dev` / branche dédiée, jamais sur `main`, *Conventional Commits*, SemVer dans `plugin/plugin.json` + `CHANGELOG.md`, tags `vX.Y.Z`, checks avant commit) ; sous-agents et workers `swarm` ne committent/poussent/taguent jamais.
- **100 % stdlib Python 3 :** zéro dépendance tierce dans `plugin/`, `workspace-template/`, `install.py`, `uninstall.py`, `tests/`.
- **Documentation vivante :** toute modification met à jour [`docs/`](docs/README.md) dans le même lot.
- **Intégrité du socle :** garder `hooks.json` et `sidecar.json` portables (`"python3"`), préserver tout plugin `gravity` Core existant (`config/module.json` `id: "core"`), zéro `Module XX` dans `plugin/skills/` et `docs/`.

## Vérifications obligatoires
- `python plugin/scripts/validate.py --strict`
- `python plugin/scripts/validate.py --workspace workspace-template --strict`
- `agy plugin validate ./plugin --strict`
- `python plugin/scripts/test_unit.py`
- `python -m unittest discover -s tests -p "test_*.py" -v`
