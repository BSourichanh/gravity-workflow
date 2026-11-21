# Gravity (`gravity-plugin`)

> **Statut :** Plugin autonome & validé pour Antigravity App, CLI et IDE (indépendant du projet cible).
> **Compatibilité :** Linux, macOS et Windows (100 % bibliothèque standard Python 3).

**Ground your Antigravity agents** — Harnais multi-agents 100 % natif pour **Google Antigravity App (`v2.34.0+`)**, **CLI `agy` (`v1.5+`)** et extensions IDE, sans dépendance tierce (100 % bibliothèque standard Python 3).

## Principe : Gravity indépendant du projet + Init vital via `/gravity`

- **Tout le moteur réside dans `gravity-plugin/`** : skill principal (`/gravity [low|medium|high|ultra|evo] [slow|fast] [safe|unsafe]`, `/gravity status`, `/gravity evals`, `/gravity init`) + skill compagnon (`/caveman [lite|full|ultra|off|auto]`, `commit`, `compress`, `status`), sous-agents (`planner`, `builder`, `reviewer`), hooks (`hooks.json` + `hooks/*.py` incluant `PreToolUse`, `PostToolUse`, `SessionStart` Caveman et `Stop`), workflows (`workflows/review_pipeline.py`), moteur d'évaluations (`evals/`), profil adaptatif, protocole Caveman & observabilité (`observability/profile_engine.py`, `caveman_protocol.py`, `state_writer.py`, `statusline.py`), sidecar (`sidecars/live-widget/`) et bootstrapper (`scripts/init_workspace.py`).
- **Initialisation légère dans le projet cible** : taper `/gravity` dans n'importe quel projet lance la session avec 3 axes orthogonaux par défaut (**`mode: evo`** au palier **`medium`**, stratégie workflow **`slow`** par responsabilité, vérification **`safe`** ; `fast` optimise uniquement le temps d'exécution sans réduire la réflexion ni couper les tests, tandis qu'`unsafe` désactive les vérifications et tests du workflow tout en maintenant `PreToolUse` actif) plus la concision **`caveman: lite`** (`caveman_locked: false`, pilotée automatiquement par `evo` entre `lite` et `full` selon le ROI et la stabilité, avec `evals/` toujours exclu) et déploie uniquement les fichiers vitaux propres au projet (`AGENTS.md`, `.agents/rules/`, `.agents/mcp_config.json` et `.agents/gravity/` incluant `profile.json`), sans polluer la racine du dépôt avec le code interne de Gravity.

## Installation

### Locale (développement)
```bash
agy plugin install .
agy plugin list
```

### Depuis le dépôt GitHub / Git
```bash
git clone https://github.com/BSourichanh/gravity.git
agy plugin install ./gravity/gravity-plugin
```

### Installation & désinstallation automatisées (racine du dépôt)
Utiliser [`../install.py`](../install.py) pour l'installation/mise à niveau (avec adaptation `python3` sur Windows) et [`../uninstall.py`](../uninstall.py) pour désinstaller proprement le plugin, nettoyer `.agents/gravity/` sans toucher aux fichiers personnalisés et purger `~/.gravity/live_workspaces.json`.

### Installation manuelle (Antigravity App & IDE)
Copier le dossier du plugin dans `~/.gemini/config/plugins/gravity/` (portée globale) ou `.agents/plugins/gravity/` (portée workspace), puis gérer son activation dans **Customizations → Installed**.

## Initialisation d'un projet (via `/gravity` ou en CLI)

```bash
python3 scripts/init_workspace.py --workspace /chemin/vers/mon-projet
# Ou en mode Démarrage rapide (10 min) :
python3 scripts/init_workspace.py --workspace /chemin/vers/mon-projet --mode quickstart
```

## Activation du Sidecar Live Widget

Les sidecars s'exécutent uniquement dans **Antigravity App** (absents du CLI et de l'IDE). Par sécurité, l'utilisateur doit activer le sidecar lui-même dans `~/.gemini/config/config.json` (un agent ne doit jamais éditer ce fichier `0600` géré atomiquement par le Language Server) :
```json
{
  "sidecars": {
    "gravity/live-widget": {
      "enabled": true
    }
  }
}
```
Interface temps réel (SSE, anti-XSS) accessible sur `http://127.0.0.1:4789` (endpoint de santé : `/health`). La découverte multi-workspaces s'effectue automatiquement via `~/.gravity/live_workspaces.json` (alimenté par `state_writer.py` vers `<projet>/.agents/gravity/observability/live_state.json`).

> **Alternative native (`v2.34.0+`) :** Dans l'Antigravity App, un tableau de bord intégré en panneau latéral (`aux_pane` / `global`) peut également être exposé via **MCP Apps (`com.google.antigravity/ui`)** si un bundle JavaScript/Node.js est accepté hors de la contrainte 100 % stdlib Python.

## Validation & Portabilité

> **Note Windows :** Sur Windows natif sans alias `python3`, lancez `python scripts/validate.py --fix-python-cmd` pour adapter automatiquement `hooks.json` et `sidecar.json` vers `python` (ou `py -3`) sans risquer d'erreur JSON. Les permissions `chmod 0755` s'appliquent aux environnements POSIX (Linux/macOS) et sont gérées de manière transparente sous Windows.
> **Note CLI `agy` :** `agy plugin validate --strict .` requiert `agy v1.5+` ; sur les versions antérieures, passez le chemin seul (`agy plugin validate .`).

```bash
python3 scripts/validate.py --strict
python3 scripts/test_unit.py
agy plugin validate --strict .
python3 scripts/validate.py --workspace /chemin/vers/mon-projet --strict
```
