# Tableau de bord & Observabilité (Bonus)

## Objectif
Exploiter les 4 surfaces complémentaires de suivi fournies par `plugin/` pour offrir une visibilité temps réel et à la demande sur l'équipe d'agents, la santé des règles et les garde-fous du projet.

## Les 4 surfaces d'observabilité Gravity Workflow

| Surface | Environnement | Rôle & source de données |
| :--- | :--- | :--- |
| **Micro-carte** (3 lignes) | App & CLI (`agy`) | Affichée automatiquement à chaque appel `/gravity-workflow` depuis le profil centralisé (profil `quick`/`standard`/`deep`, mode consultatif `evo` ou verrouillé, `Caveman`, `suggested_profile` et motif). |
| **Live Widget (Sidecar)** | **Antigravity App uniquement** (`http://127.0.0.1:4789`, transition) | Serveur SSE Python stdlib anti-XSS (`sidecars/live-widget/server.py`, `daemon_threads=True`) affichant profil actif, `suggested_profile` et `caveman` (`auto`/`[lock]`), et découvrant les workspaces via `~/.gemini/gravity/workflow/live_workspaces.json` (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow`) alimenté atomiquement par `state_writer.py`. *(Surface transitoire appelée à converger dans l'UI unifiée du plugin Core `gravity`.)* |
| **`/gravity-workflow status`** | App (artifact `generative_ui`) / CLI (Markdown) | Photo à la demande en **4 blocs** : (1) avancement, mode, profil actif, `role_models` A/B, `Caveman`, `suggested_profile` + motif ; (2) santé des règles (`.agents/`, `< 20 000` tokens, `01-safety-boundaries.md`, `05-git-versioning.md`, garde-fous plugin) ; (3) métriques d'évaluations/`runs.jsonl` + ligne `Signaux Metrics (N sess.) : cache X %, err Y %, delta Z pts` si `signals.metrics.available` vaut `True` ; (4) 1–3 recommandations (en App, lire `generative_ui` et émettre un artifact HTML + `<agent-embed>`). |
| **`/statusline`** | **CLI `agy` uniquement** | Barre persistante générée par `plugin/observability/statusline.py` lisant le payload JSON `stdin` d'Antigravity (`model`, `effort`, `context_window`, `quota`, `sandbox`, `cost`, `workspace.project_dir`), le profil centralisé (`evo:standard · caveman:lite` ou `deep[lock] · caveman:full[lock]`), la ligne synthétique `Signaux Metrics (...)` si disponible et `evals/report.json`. |

## Activation du Sidecar Live Widget (Antigravity App)
Un sidecar est **global à l'application** et n'accepte pas de variables d'éditeur type `${workspaceFolder}`. L'utilisateur l'active une fois dans `~/.gemini/config/config.json` (ne jamais demander à un agent d'éditer ce fichier) :
```json
{
  "sidecars": {
    "gravity-workflow/live-widget": {
      "enabled": true
    }
  }
}
```
*(Adapter `"command": "python"` dans `sidecar.json` si `python3` réel est absent sous Windows. Alternative moderne App `v2.34.0+` : un MCP App `com.google.antigravity/ui` avec `aux_pane`/`global` peut remplacer le serveur HTTP externe si Node.js/JS est autorisé.)*

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
2. Vérifier l'endpoint `http://127.0.0.1:4789/health` (`{"status":"ok", ...}`) lorsque le Sidecar est actif.
3. Lancer l'audit final strict :
   - `python3 <plugin_root>/scripts/validate.py --strict` (sur le plugin)
   - `python3 <plugin_root>/scripts/validate.py --workspace . --strict` (sur le projet)
   - `agy plugin validate --strict .` (`agy v1.5+`, sinon `agy plugin validate .`) sur le dépôt plugin
4. Marquer l'étape Bonus comme terminée dans `.agents/gravity-workflow/PROGRESS.md`.
