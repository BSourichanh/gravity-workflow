# Observabilité & Tableau de Bord

Gravity Workflow fournit 4 surfaces complémentaires alimentées par les fichiers `.agents/gravity-workflow/` du projet cible ([`09-observability-dashboard.md`](../plugin/skills/gravity-workflow/references/09-observability-dashboard.md)).

---

## Les 4 surfaces d'observabilité

| Surface | Environnement | Script / Mécanisme | Contenu affiché |
|---|---|---|---|
| **Micro-carte (3 lignes)** | App & CLI (`agy`) | `SKILL.md` | Progression `[X/8]`, badge `mode` (`evo -> <palier>` ou verrouillé), `pace`, `verification`, santé règles/`Guard`, `Caveman : <niveau> (auto\|verrouillé)`, évals, dernier motif `evo` |
| **`/gravity-workflow status`** | App (`generative_ui`) / CLI (Markdown) | `SKILL.md` + `profile_engine.py get --json` | Bilan à la demande en **4 blocs** : avancement, 3 axes & `caveman`, santé règles & sécurité, métriques d'évals, 1–3 recommandations |
| **`/statusline`** | **CLI `agy` uniquement** | [`statusline.py`](../plugin/observability/statusline.py) | Barre compacte sur 1 ligne : `Gravity Workflow X/8 · <badge> · <caveman_badge> · Guard ON/OFF · Evals ... · <modèle> · Ctx % · SBX · $coût` (lecture unique de `profile.json`, vérifie `harness-pre-tool-guard.enabled`, `exit 0` garanti) |
| **Live Widget (Sidecar)** | **Antigravity App uniquement** *(transition)* | [`server.py`](../plugin/sidecars/live-widget/server.py) (`http://127.0.0.1:4789`) | Serveur HTTP + SSE stdlib anti-XSS (`ThreadingHTTPServer`, `daemon_threads=True`), affichant mode, `pace`, `verification` et `caveman`, santé `/health`, sélecteur multi-workspaces (signature SSE surveillant `live_state.json`, `profile.json` et `live_workspaces.json`) |

> **Note de transition UI :** le sidecar Live Widget sur le port `4789` (`"ui": {"mode": "transition-sidecar", "target": "gravity"}` dans `plugin/config/module.json`) est maintenu à titre transitoire et convergera vers l'UI unifiée du plugin Core **`gravity`** (`../gravity`).

Les formats compacts produits par `profile_engine.format_mode_badge()` et `profile_engine.format_caveman_badge()` sont respectivement `evo:<level>/<pace>/<verification>` (ou `<level>[lock]/<pace>/<verification>`) et `caveman:<niveau>` (ou `caveman:<niveau>[lock]`).

---

## Persistance atomique & stockage centralisé ([`state_writer.py`](../plugin/observability/state_writer.py))

- **Résolution centralisée unifiée (`default_workflow_home()` & `resolve_registry_path()`) :** partagée entre `state_writer.py`, `sidecars/live-widget/server.py` et `uninstall.py` selon l'ordre **`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/`** (avec surcharge directe optionnelle `$GRAVITY_WORKFLOW_REGISTRY_PATH` et compatibilité descendante si `state_writer.REGISTRY_PATH` est patché en test).
- **Écriture atomique :** fichier temporaire dans le même dossier (`tempfile.mkstemp` + `os.fsync` + `os.replace` avec retry borné sur `PermissionError` Windows) protégé par `exclusive_file_lock` (`fcntl.flock` sur POSIX, `msvcrt.locking` avec retry sur Windows).
- **Limites bornées :** `MAX_EVENTS = 50` dans `live_state.json` (horodatage `ts` + `ts_iso`), `MAX_WORKSPACES = 20` dans `~/.gemini/gravity/workflow/live_workspaces.json` (désactivable en test via `GRAVITY_WORKFLOW_SKIP_REGISTRY=1` et surchargeable via `GRAVITY_WORKFLOW_STATE_PATH` / `GRAVITY_WORKFLOW_WORKSPACE` / `GRAVITY_WORKFLOW_HOME` / `GRAVITY_HOME` / `GRAVITY_WORKFLOW_REGISTRY_PATH`).
- **Champs clés de `.agents/gravity-workflow/observability/live_state.json` :**
  `stage`, `progress`, `mode`, `level`, `pace`, `verification`, `caveman`, `caveman_locked`, `guard_status`, `nodes` (`planner`/`builder`/`reviewer`), `events`.

---

## Activation utilisateur (App & CLI)

### 1. Sidecar Live Widget (`Antigravity App`)
L'utilisateur active le sidecar manuellement dans `~/.gemini/config/config.json` *(un agent ne doit jamais modifier `config.json`)* :

```json
{
  "sidecars": {
    "gravity-workflow/live-widget": {
      "enabled": true
    }
  }
}
```

### 2. `/statusline` (`CLI agy`)
Configurer dans `~/.gemini/antigravity-cli/settings.json` :

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.gemini/config/plugins/gravity-workflow/observability/statusline.py",
    "stack_with_default": true
  }
}
```

*(Sous Windows sans `python3` réel dans le `PATH`, remplacer `python3` par `python` ou `py -3`, et adapter le sidecar sur la copie installée via `validate.py --fix-python-cmd`.)*
