# Observabilité & Tableau de Bord

Gravity fournit 4 surfaces complémentaires alimentées par les fichiers `.agents/gravity/` du projet cible ([`09-observability-dashboard.md`](../gravity-plugin/skills/gravity/references/09-observability-dashboard.md)).

---

## Les 4 surfaces d'observabilité

| Surface | Environnement | Script / Mécanisme | Contenu affiché |
|---|---|---|---|
| **Micro-carte (3 lignes)** | App & CLI (`agy`) | `SKILL.md` | Progression `[X/8]`, badge `mode` (`evo -> <palier>` ou verrouillé), `pace`, `verification`, santé règles/`Guard`, `Caveman : <niveau> (auto\|verrouillé)`, évals, dernier motif `evo` |
| **`/gravity status`** | App (`generative_ui`) / CLI (Markdown) | `SKILL.md` + `profile_engine.py get --json` | Bilan à la demande en **4 blocs** : avancement, 3 axes & `caveman`, santé règles & sécurité, métriques d'évals, 1–3 recommandations |
| **`/statusline`** | **CLI `agy` uniquement** | [`statusline.py`](../gravity-plugin/observability/statusline.py) | Barre compacte sur 1 ligne : `Gravity X/8 · <badge> · <caveman_badge> · Guard ON/OFF · Evals ... · <modèle> · Ctx % · SBX · $coût` (lecture unique de `profile.json`, vérifie `harness-pre-tool-guard.enabled`, `exit 0` garanti) |
| **Live Widget (Sidecar)** | **Antigravity App uniquement** | [`server.py`](../gravity-plugin/sidecars/live-widget/server.py) (`http://127.0.0.1:4789`) | Serveur HTTP + SSE stdlib anti-XSS (`ThreadingHTTPServer`, `daemon_threads=True`), affichant mode, `pace`, `verification` et `caveman`, santé `/health`, sélecteur multi-workspaces (signature SSE surveillant `live_state.json`, `profile.json` et `live_workspaces.json`) |

Les formats compacts produits par `profile_engine.format_mode_badge()` et `profile_engine.format_caveman_badge()` sont respectivement `evo:<level>/<pace>/<verification>` (ou `<level>[lock]/<pace>/<verification>`) et `caveman:<niveau>` (ou `caveman:<niveau>[lock]`).

---

## Persistance atomique & multi-workspaces ([`state_writer.py`](../gravity-plugin/observability/state_writer.py))

- **Écriture atomique :** fichier temporaire dans le même dossier (`tempfile.mkstemp` + `os.fsync` + `os.replace` avec retry borné sur `PermissionError` Windows) protégé par `exclusive_file_lock` (`fcntl.flock` sur POSIX, `msvcrt.locking` avec retry sur Windows).
- **Limites bornées :** `MAX_EVENTS = 50` dans `live_state.json` (horodatage `ts` + `ts_iso`), `MAX_WORKSPACES = 20` dans `~/.gravity/live_workspaces.json` (désactivable en test via `GRAVITY_SKIP_REGISTRY=1` et surchargeable via `GRAVITY_STATE_PATH` / `GRAVITY_WORKSPACE`).
- **Champs clés de `.agents/gravity/observability/live_state.json` :**
  `stage`, `progress`, `mode`, `level`, `pace`, `verification`, `caveman`, `caveman_locked`, `guard_status`, `nodes` (`planner`/`builder`/`reviewer`), `events`.

---

## Activation utilisateur (App & CLI)

### 1. Sidecar Live Widget (`Antigravity App`)
L'utilisateur active le sidecar manuellement dans `~/.gemini/config/config.json` *(un agent ne doit jamais modifier `config.json`)* :

```json
{
  "sidecars": {
    "gravity/live-widget": {
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
    "command": "python3 ~/.gemini/config/plugins/gravity/observability/statusline.py",
    "stack_with_default": true
  }
}
```

*(Sous Windows sans `python3` réel dans le `PATH`, remplacer `python3` par `python` ou `py -3`, et adapter le sidecar sur la copie installée via `validate.py --fix-python-cmd`.)*
