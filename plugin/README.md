# Gravity Workflow (`plugin/`)

> **Statut :** Plugin autonome & validé pour Antigravity App, CLI et IDE (indépendant du projet cible).
> **Compatibilité :** Linux, macOS et Windows (100 % bibliothèque standard Python 3.10+).

**Ground your Antigravity agents** — Harnais multi-agents 100 % natif pour **Google Antigravity App (`v2.34.0+`)**, **CLI `agy` (`v1.5+`)** et extensions IDE, sans dépendance tierce (100 % bibliothèque standard Python 3).

## Principe : Gravity Workflow indépendant du projet + Init vital via `/gravity-workflow`

- **Tout le moteur réside dans `plugin/`** : manifeste officiel 9 champs (`plugin.json`), contrat module v1 (`config/module.json`, `id: "workflow"`), skill principal (`/gravity-workflow [low|medium|high|ultra|evo] [slow|fast] [safe|unsafe]`, `/gravity-workflow status`, `/gravity-workflow evals`, `/gravity-workflow init`) + skill compagnon (`/caveman [lite|full|ultra|off|auto]`, `commit`, `compress`, `status`), sous-agents (`planner`, `builder`, `reviewer`), hooks (`hooks.json` + `hooks/*.py` incluant `PreToolUse`, `PostToolUse`, `SessionStart` Caveman et `Stop`), workflows (`workflows/review_pipeline.py`), moteur d'évaluations (`evals/`), profil adaptatif, protocole Caveman & observabilité (`observability/profile_engine.py`, `caveman_protocol.py`, `state_writer.py`, `statusline.py`), sidecar de transition (`sidecars/live-widget/`) et bootstrapper (`scripts/init_workspace.py`).
- **Initialisation légère dans le projet cible** : taper `/gravity-workflow` dans n'importe quel projet lance la session avec 3 axes orthogonaux par défaut (**`mode: evo`** au palier **`medium`**, stratégie workflow **`slow`** par responsabilité, vérification **`safe`** ; `fast` optimise uniquement le temps d'exécution sans réduire la réflexion ni couper les tests, tandis qu'`unsafe` désactive les vérifications et tests du workflow tout en maintenant `PreToolUse` actif) plus la concision **`caveman: lite`** (`caveman_locked: false`, pilotée automatiquement par `evo` entre `lite` et `full` selon le ROI et la stabilité, avec `evals/` toujours exclu) et déploie uniquement les fichiers vitaux propres au projet (`AGENTS.md`, `.agents/rules/`, `.agents/mcp_config.json` et `.agents/gravity-workflow/` incluant `profile.json`), sans polluer la racine du dépôt avec le code interne du plugin.

## Migration `v0.1.x` → `v0.2.0` (`gravity` → `gravity-workflow`)

À partir de `v0.2.0`, le plugin est renommé **`gravity-workflow`** sans shim de compatibilité au runtime (`.agents/gravity/` n'est plus reconnu) :
- **Plugin :** `python install.py` (sous `--method copy`) installe dans `~/.gemini/config/plugins/gravity-workflow/` et supprime l'ancien plugin workflow v0.1.x `~/.gemini/config/plugins/gravity` uniquement s'il ne possède pas `config/module.json` `"id": "core"` ET présente les marqueurs workflow hérités (`hooks/pre_tool_guard.py` ou `skills/gravity/`) — le plugin **Gravity Core (`gravity`)** n'est jamais touché (passer `--keep-legacy` pour conserver un vrai legacy). Sous `--method agy`, lancer `agy plugin uninstall gravity` (legacy uniquement) puis `agy plugin install ./plugin`.
- **Workspaces & registre :** nettoyer l'état hérité avec `python uninstall.py --clean-legacy --workspace-only --clean-workspace <projet>` (qui purge `~/.gravity/live_workspaces.json` et `.agents/gravity/`), puis réinitialiser le projet via `python3 scripts/init_workspace.py --workspace <projet>` ou `/gravity-workflow`.
- **Config App / CLI :** mettre à jour manuellement `~/.gemini/config/config.json` (`gravity-workflow/live-widget`) et `~/.gemini/antigravity-cli/settings.json` (`~/.gemini/config/plugins/gravity-workflow/observability/statusline.py`).

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
Utiliser [`../install.py`](../install.py) pour l'installation/mise à niveau (adaptation `python3` sur Windows, repli transactionnel *in-place* avec rollback sous verrou Windows `sidecars/live-widget` et nettoyage Core-safe du legacy workflow `gravity` sans `--keep-legacy`) et [`../uninstall.py`](../uninstall.py) pour désinstaller proprement le plugin, nettoyer `.agents/gravity-workflow/` sans toucher aux fichiers personnalisés, purger le registre centralisé (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/live_workspaces.json`) et supprimer les artefacts hérités (`--clean-legacy` / `--all`).

### Installation manuelle (Antigravity App & IDE)
Copier le dossier `plugin/` dans `~/.gemini/config/plugins/gravity-workflow/` (portée globale) ou `.agents/plugins/gravity-workflow/` (portée workspace), puis gérer son activation dans **Customizations → Installed**.

## Initialisation d'un projet (via `/gravity-workflow` ou en CLI)

```bash
python3 scripts/init_workspace.py --workspace /chemin/vers/mon-projet
# Ou en mode Démarrage rapide (10 min) :
python3 scripts/init_workspace.py --workspace /chemin/vers/mon-projet --mode quickstart
```

## Activation du Sidecar Live Widget *(surface de transition)*

Les sidecars s'exécutent uniquement dans **Antigravity App** (absents du CLI et de l'IDE). Par sécurité, l'utilisateur doit activer le sidecar lui-même dans `~/.gemini/config/config.json` (un agent ne doit jamais éditer ce fichier `0600` géré atomiquement par le Language Server) :
```json
{
  "sidecars": {
    "gravity-workflow/live-widget": {
      "enabled": true
    }
  }
}
```
Interface temps réel (SSE, anti-XSS) accessible sur `http://127.0.0.1:4789` (endpoint de santé : `/health`). La découverte multi-workspaces s'effectue automatiquement via `~/.gemini/gravity/workflow/live_workspaces.json` (ou `$GRAVITY_WORKFLOW_HOME` / `$GRAVITY_HOME/workflow`, alimenté par `state_writer.py` vers `<projet>/.agents/gravity-workflow/observability/live_state.json`).

> **Note de transition UI :** le sidecar Live Widget sur le port `4789` (`"ui": {"mode": "transition-sidecar", "target": "gravity"}` dans `config/module.json`) est une surface de transition appelée à converger vers l'UI unifiée du plugin Core **`gravity`**.

## Validation & Portabilité

> **Note Windows :** Sur Windows natif sans alias `python3`, lancez `python scripts/validate.py --fix-python-cmd` sur votre **copie installée** pour adapter `hooks.json` et `sidecar.json` vers `python` (ou `py -3`) sans modifier le dépôt source. Les permissions `chmod 0755` s'appliquent aux environnements POSIX (Linux/macOS) et sont gérées de manière transparente sous Windows.
> **Note CLI `agy` :** `agy plugin validate . --strict` requiert `agy v1.5+` ; sur les versions antérieures, passez le chemin seul (`agy plugin validate .`).

```bash
python3 scripts/validate.py --strict
python3 scripts/test_unit.py
agy plugin validate . --strict
python3 scripts/validate.py --workspace /chemin/vers/mon-projet --strict
```
