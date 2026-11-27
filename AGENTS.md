# Gravity Workflow — Instructions Agents

## Périmètre & Architecture
Harnais Antigravity 100 % stdlib Python 3 (zéro dépendance dans `plugin/`, `workspace-template/`, `install.py`, `uninstall.py`, `tests/`) :
- `plugin/` : moteur (`plugin.json` 9 champs, `config/module.json` v1 `id: "workflow"`, `templates/`, skills, agents, hooks fail-closed, workflows, evals).
- `workspace-template/` : référence (14 fichiers alignés sur `plugin/templates/`).
- `install.py` (`--uninstall`, wrapper `uninstall.py`, `$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow`) ; [`docs/`](docs/README.md).
- **Périmètre séparé :** `../gravity-metrics`, hub [`../gravity`](../gravity/).

## Invariants
- **Git & versioning (chef d'orchestre uniquement) :** `dev`/branche dédiée (jamais `main`) ; `commit`/`push`/tag/release (accord requis) : *Conventional Commits*, SemVer (`plugin/plugin.json` + `CHANGELOG.md`), tags `vX.Y.Z`, checks ; sous-agents/`swarm` exclus.
- **Principes & hygiène :** **KISS**, **YAGNI**, **DRY**, **SOLID**, **CUPID** ; **UTF-8 sans BOM**, **LF**.
- **Doc vivante :** mise à jour [`docs/`](docs/README.md) dans le même lot.
- **Intégrité :** `hooks.json` (`"python3"`), préserver `gravity` Core (`id: "core"`), zéro `Module XX` dans `plugin/skills/` et `docs/`.

## Vérifications obligatoires
- `python plugin/scripts/validate.py --strict`
- `python plugin/scripts/validate.py --workspace workspace-template --strict`
- `agy plugin validate ./plugin --strict`
- `python plugin/scripts/test_unit.py`
- `python -m unittest discover -s tests -p "test_*.py" -v`
