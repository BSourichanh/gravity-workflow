# Gravity Workflow (`plugin/`)

> **Statut :** Plugin autonome & validé pour Antigravity App, CLI et IDE (indépendant du projet cible).
> **Compatibilité :** Linux, macOS et Windows (100 % bibliothèque standard Python 3.10+).

> **Démarrage 2 min & Place dans l'écosystème**
>
> - **Palier 1 — Essentiel** *(avec [`gravity` Core](https://github.com/BSourichanh/gravity))* : harnais multi-agents, garde-fous `PreToolUse` fail-closed, routage adaptatif (`quick`/`standard`/`deep`/`evo`), evals et `/caveman`.
> - **Première commande :** `/gravity-workflow` *(et cockpit unifié `/gravity dashboard` dans `gravity` Core)*.
> - **Guides `gravity/docs/` :** [Getting Started](https://github.com/BSourichanh/gravity/blob/main/docs/getting-started.md) · [User Journey](https://github.com/BSourichanh/gravity/blob/main/docs/user-journey.md) · [Command Cheatsheet](https://github.com/BSourichanh/gravity/blob/main/docs/command-cheatsheet.md) · [Troubleshooting & Lifecycle](https://github.com/BSourichanh/gravity/blob/main/docs/troubleshooting-and-lifecycle.md)

**Ground your Antigravity agents** — Harnais multi-agents 100 % natif pour **Google Antigravity App (`v2.34.0+`)**, **CLI `agy` (`v1.5+`)** et extensions IDE, sans dépendance tierce (100 % bibliothèque standard Python 3).

## Principe : Gravity Workflow indépendant du projet + Init vital via `/gravity-workflow`

- **Tout le moteur réside dans `plugin/`** : manifeste officiel 9 champs (`plugin.json`), contrat module v1 (`config/module.json`, `id: "workflow"`), skill principal (`/gravity-workflow [quick|standard|deep|evo]`, `/gravity-workflow status`, `/gravity-workflow evals`, `/gravity-workflow init`, défaut `swarm` + `--engine run_workflow` opt-in) + skill compagnon (`/caveman [lite|full|ultra|off|auto]`, `commit`, `compress`, `status`), sous-agents (`planner`, `builder`, `reviewer`), hooks (`hooks.json` + `hooks/*.py` incluant `PreToolUse`, `PostToolUse`, `SessionStart` Caveman et `Stop`), workflows (`workflows/patch_apply.py`, `workflows/swarm_wave.py`, `workflows/review_pipeline.py`, `workflows/wave_runner.py`), moteur d'évaluations (`evals/`), profil consultatif, protocole Caveman & observabilité (`observability/profile_engine.py`, `metrics_signals.py`, `caveman_protocol.py`, `state_writer.py`, `statusline.py`) et bootstrapper (`scripts/init_workspace.py`).
- **Cycle de vie actif partout & stockage centralisé :** `SessionStart`, `Stop`, `Live` et `profile_engine` s'exécutent sur tout dépôt sans nécessiter `/gravity-workflow init`. Les 3 profils (`quick`, `standard` défaut, `deep`, avec alias `low`/`medium`/`high`/`ultra`), `caveman: lite` par défaut, et `evo` consultatif (`suggested_profile`: `standard`|`deep`) stockent tout l'état runtime sous `$GRAVITY_HOME/workflow/workspaces/<slug>-<sha256_8>/` (zéro fichier écrit dans le workspace cible ; repli lecture seule sur `<ws>/.agents/gravity-workflow/profile.json`). `--no-verify` / `unsafe` reste strictement éphémère par tâche/run.

## Migration `v0.1.x` → `v0.2.0` (`gravity` → `gravity-workflow`)

À partir de `v0.2.0`, le plugin est renommé **`gravity-workflow`** sans shim de compatibilité au runtime (`.agents/gravity/` n'est plus reconnu) :
- **Plugin :** `python install.py` (sous `--method copy`) installe dans `~/.gemini/config/plugins/gravity-workflow/` et supprime l'ancien plugin workflow v0.1.x `~/.gemini/config/plugins/gravity` uniquement s'il ne possède pas `config/module.json` `"id": "core"` ET présente les marqueurs workflow hérités (`hooks/pre_tool_guard.py` ou `skills/gravity/`) — le plugin **Gravity Core (`gravity`)** n'est jamais touché (passer `--keep-legacy` pour conserver un vrai legacy). Sous `--method agy`, lancer `agy plugin uninstall gravity` (legacy uniquement) puis `agy plugin install ./plugin`.
- **Workspaces & registre :** nettoyer l'état hérité avec `python install.py --workspace-only --clean-legacy --clean-workspace <projet>` (qui purge `~/.gravity/live_workspaces.json` et `.agents/gravity/`), puis réinitialiser le projet via `python3 scripts/init_workspace.py --workspace <projet>` ou `/gravity-workflow`.
- **Config App / CLI :** retirer manuellement toute ancienne entrée `gravity/live-widget` ou `gravity-workflow/live-widget` de `~/.gemini/config/config.json` (remplacée par `/gravity dashboard`), et mettre à jour `~/.gemini/antigravity-cli/settings.json` (`~/.gemini/config/plugins/gravity-workflow/observability/statusline.py`).

## Installation

### Locale (développement)
```bash
agy plugin install .
agy plugin list
```

### Depuis le dépôt GitHub / Git
```bash
git clone https://github.com/BSourichanh/gravity-workflow.git
agy plugin install ./gravity-workflow/plugin
```

### Installation & désinstallation automatisées (racine du dépôt)
Utiliser [`../install.py`](../install.py) pour l'installation/mise à niveau (adaptation `python3` sur Windows, repli transactionnel *in-place* avec rollback sous verrou Windows et nettoyage Core-safe du legacy workflow `gravity` sans `--keep-legacy`) et `python install.py --uninstall` / `--workspace-only` (avec [`../uninstall.py`](../uninstall.py) conservé comme wrapper déprécié) pour désinstaller proprement le plugin, nettoyer `.agents/gravity-workflow/` sans toucher aux fichiers personnalisés, purger le registre centralisé (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/live_workspaces.json`) et supprimer les artefacts hérités (`--clean-legacy` / `--all`).

### Installation manuelle (Antigravity App & IDE)
Copier le dossier `plugin/` dans `~/.gemini/config/plugins/gravity-workflow/` (portée globale) ou `.agents/plugins/gravity-workflow/` (portée workspace), puis gérer son activation dans **Customizations → Installed**.

## Initialisation d'un projet (via `/gravity-workflow` ou en CLI)

```bash
python3 scripts/init_workspace.py --workspace /chemin/vers/mon-projet
# Ou en mode Démarrage rapide (10 min) :
python3 scripts/init_workspace.py --workspace /chemin/vers/mon-projet --mode quickstart
```

## Cockpit unifié (`/gravity dashboard`)

Le suivi visuel temps réel s'effectue désormais dans l'hôte UI MCP App unifié **`/gravity dashboard`** du plugin [`gravity` Core](https://github.com/BSourichanh/gravity) (`workflow_provider.py`), alimenté directement par `~/.gemini/gravity/workflow/live_workspaces.json` et `workspaces/<slug>-<sha256_8>/live_state.json` (`state_writer.py`).

> **Migration UI :** l'ancien sidecar HTTP transitoire sur le port `4789` (`sidecars/live-widget/`) est retiré (`"ui": {"mode": "none", "target": "gravity"}` dans `config/module.json`). Si une entrée manuelle `"gravity-workflow/live-widget"` subsiste dans `~/.gemini/config/config.json`, retirez-la manuellement.

## Validation & Portabilité

> **Note Windows :** Sur Windows natif sans alias `python3`, lancez `python scripts/validate.py --fix-python-cmd` sur votre **copie installée** pour adapter `hooks.json` vers `python` (ou `py -3`) sans modifier le dépôt source. Les permissions `chmod 0755` s'appliquent aux environnements POSIX (Linux/macOS) et sont gérées de manière transparente sous Windows.
> **Note CLI `agy` :** `agy plugin validate ./plugin --strict` (depuis la racine) ou `agy plugin validate . --strict` (depuis `plugin/`) requiert `agy v1.5+` ; sur les versions antérieures, passez le chemin seul (`agy plugin validate .`).

```bash
python3 scripts/validate.py --strict
python3 scripts/test_unit.py
agy plugin validate . --strict
python3 scripts/validate.py --workspace /chemin/vers/mon-projet --strict
```
