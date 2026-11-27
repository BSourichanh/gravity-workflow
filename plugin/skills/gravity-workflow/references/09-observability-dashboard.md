# Tableau de bord & Observabilité (Bonus)

## Objectif
Exploiter les **3 surfaces locales** complémentaires de `plugin/` ainsi que le **cockpit MCP App unifié `/gravity dashboard`** (`gravity` Core) pour offrir une visibilité temps réel et à la demande sur l'équipe d'agents, la santé des règles et les garde-fous du projet.

## Les 3 surfaces locales + Cockpit MCP App unifié `/gravity dashboard`

| Surface | Environnement | Rôle & source de données |
| :--- | :--- | :--- |
| **Micro-carte** (3 lignes) | App & CLI (`agy`) | Affichée automatiquement à chaque appel `/gravity-workflow` depuis le profil centralisé (profil `quick`/`standard`/`deep`, mode consultatif `evo` ou verrouillé, `Caveman`, `suggested_profile` et motif). |
| **`/gravity-workflow status`** | App (artifact `generative_ui`) / CLI (Markdown) | Photo à la demande en **4 blocs** : (1) avancement, mode, profil actif, `role_models` A/B, `Caveman`, `suggested_profile` + motif ; (2) santé des règles (`.agents/`, `< 20 000` tokens, `01-safety-boundaries.md`, `05-git-versioning.md`, garde-fous plugin) ; (3) métriques d'évaluations/`runs.jsonl` + ligne `Signaux Metrics (N sess.) : cache X %, err Y %, delta Z pts` si `signals.metrics.available` vaut `True` ; (4) 1–3 recommandations (en App, lire `generative_ui` et émettre un artifact HTML + `<agent-embed>`). |
| **`/statusline`** | **CLI `agy` uniquement** | Barre persistante générée par `plugin/observability/statusline.py` lisant le payload JSON `stdin` d'Antigravity (`model`, `effort`, `context_window`, `quota`, `sandbox`, `cost`, `workspace.project_dir`), le profil centralisé (`evo:standard · caveman:lite` ou `deep[lock] · caveman:full[lock]`), la ligne synthétique `Signaux Metrics (...)` si disponible et `evals/report.json`. |
| **Cockpit `/gravity dashboard`** | **Antigravity App (MCP App)** | Vue Workflow unifiée fournie par le plugin Core `gravity` (`open_gravity_dashboard`) via le fournisseur `SPI-1` [`scripts/dashboard_provider.py`](../../../scripts/dashboard_provider.py), consommant `~/.gemini/gravity/workflow/live_workspaces.json` (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow`), `workspaces/<slug>-<sha256_8>/{live_state.json,profile.json,runs.jsonl}` (`state_writer.py`) et les signaux `SPI-3` (`metrics_signals.py`). *(Remplace l'ancien sidecar HTTP transitoire sur le port `4789`, définitivement retiré.)* |

## Cockpit unifié `/gravity dashboard` (Antigravity App)
La vue temps réel est intégrée nativement dans le cockpit **`/gravity dashboard`** du plugin Core `gravity` (`open_gravity_dashboard`), sans serveur HTTP externe ni modification de `config.json`.
Si un ancien réglage manuel `"gravity-workflow/live-widget"` ou `"gravity/live-widget"` figure encore sous `"sidecars"` dans `~/.gemini/config/config.json`, demander à l'utilisateur de le retirer manuellement (ne jamais éditer `config.json` via un agent).

## Activation de `/statusline` (Antigravity CLI)
Configurer dans `~/.gemini/antigravity-cli/settings.json` en pointant vers le script `observability/statusline.py` du plugin (timeout CLI = 5 s, désactivation automatique après 30 échecs consécutifs) :
```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.gemini/config/plugins/gravity-workflow/observability/statusline.py",
    "stack_with_default": true
  }
}
```

## Composants & état projet
1. `plugin/observability/state_writer.py` (écrivain atomique avec verrou portable `exclusive_file_lock` + registre `~/.gemini/gravity/workflow/live_workspaces.json`), `plugin/observability/profile_engine.py` et `.agents/gravity-workflow/{profile.json, observability/live_state.json}` initialisés dans le projet.
2. `plugin/skills/gravity-workflow/SKILL.md` (procédure `/gravity-workflow status`).
3. `plugin/observability/statusline.py` exécutable (`chmod 0755` sous POSIX, lecture JSON `stdin` + badge mode/palier + `exit 0` garanti).

## Checkpoint déterministe & validation finale
1. Vérifier la sortie de `python3 <plugin_root>/observability/statusline.py` (avec et sans JSON `stdin`).
2. Vérifier l'actualisation de `live_state.json` / `live_workspaces.json` sous `$GRAVITY_WORKFLOW_HOME` (et son affichage dans `/gravity dashboard` si `gravity` Core est installé).
3. Lancer l'audit final strict :
   - `python3 <plugin_root>/scripts/validate.py --strict` (sur le plugin)
   - `python3 <plugin_root>/scripts/validate.py --workspace . --strict` (sur le projet)
   - `agy plugin validate --strict .` (`agy v1.5+`, sinon `agy plugin validate .`) sur le dépôt plugin
4. Marquer l'étape Bonus comme terminée dans `.agents/gravity-workflow/PROGRESS.md`.
