# Observabilité & Tableau de Bord

Gravity Workflow fournit **3 surfaces locales** complémentaires + le **cockpit MCP App unifié `/gravity dashboard`** (`gravity` Core), tous alimentés par le stockage centralisé sous **`$GRAVITY_WORKFLOW_HOME/workspaces/<slug>-<sha256_8>/`** (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/`, avec repli lecture seule sur `.agents/gravity-workflow/` si présent — [`09-observability-dashboard.md`](../plugin/skills/gravity-workflow/references/09-observability-dashboard.md)).

---

## Les 3 surfaces locales + Cockpit MCP App unifié

| Surface | Environnement | Script / Mécanisme | Contenu affiché |
|---|---|---|---|
| **Micro-carte (3 lignes)** | App & CLI (`agy`) | `SKILL.md` | Progression `[X/8 ou centralisé]`, profil actif (`quick`\|`standard`\|`deep`), mode (`evo` consultatif ou verrouillé), santé règles/`Guard`, `Caveman`, évals, **`suggested_profile` (`standard`\|`deep`) + motif** |
| **`/gravity-workflow status`** | App (`generative_ui`) / CLI (Markdown) | `SKILL.md` + `profile_engine.py get --json` | Bilan à la demande en **4 blocs** : avancement, profil & `role_models` A/B, **`suggested_profile` + motif**, santé règles & sécurité, métriques d'évals/`runs.jsonl` (+ ligne `Signaux Metrics (N sess.) : cache X %, err Y %, delta Z pts` via `format_status()` si disponible), 1–3 recommandations |
| **`/statusline`** | **CLI `agy` uniquement** | [`statusline.py`](../plugin/observability/statusline.py) | Barre compacte sur 1 ligne : `Gravity Workflow X/8 · <badge> · <caveman_badge> · Guard ON/OFF · Evals ... · [Signaux Metrics ... ·] <modèle> · Ctx % · SBX · $coût` (`exit 0` garanti) |
| **Cockpit `/gravity dashboard`** | **Antigravity App (MCP App)** | `gravity` Core (`workflow_provider.py` + `open_gravity_dashboard`) | Vue Workflow live multi-workspaces consommant `live_workspaces.json` et `live_state.json` (mode, profil actif, `suggested_profile`, `caveman`, santé règles, événements récents) |

> **Migration UI :** l'ancien sidecar HTTP transitoire sur le port `4789` (`sidecars/live-widget/`) est retiré (`"ui": {"mode": "none", "target": "gravity"}` dans `plugin/config/module.json`) et remplacé par le cockpit MCP App unifié **`/gravity dashboard`** du plugin Core **`gravity`** (`../gravity`).

Les formats compacts produits par `profile_engine.format_mode_badge()` et `profile_engine.format_caveman_badge()` sont respectivement `evo:<profil>` (ou `<profil>[lock]`) et `caveman:<niveau>` (ou `caveman:<niveau>[lock]`).

---

## Persistance atomique & stockage centralisé ([`state_writer.py`](../plugin/observability/state_writer.py))

- **Résolution centralisée unifiée (`resolve_workflow_home()` / `default_workflow_home()` & `resolve_registry_path()`) :** partagée entre `state_writer.py`, `profile_engine.py`, `install.py` et le provider `workflow_provider.py` de `gravity` Core selon l'ordre **`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/`** (avec surcharge directe optionnelle `$GRAVITY_WORKFLOW_REGISTRY_PATH` et compatibilité descendante si `state_writer.REGISTRY_PATH` est patché en test).
- **Isolation par dépôt (`workspaces/<slug>-<sha256_8>/`) :** regroupe `profile.json`, `live_state.json`, `runs.jsonl`, `last_verified.patch` et `evals/report.{json,md}` via `resolve_workspace_storage_dir()` sans jamais écrire de fichier d'état dans les workspaces cibles ; chaque entrée de `live_workspaces.json` enregistre `workspace_path` (`workspace`) et `state_path`.
- **Écriture atomique :** fichier temporaire dans le même dossier (`tempfile.mkstemp` + `os.fsync` + `os.replace` avec retry borné sur `PermissionError` Windows) protégé par `exclusive_file_lock` (`fcntl.flock` sur POSIX, `msvcrt.locking` avec retry sur Windows).
- **Limites bornées :** `MAX_EVENTS = 50` dans `live_state.json` (horodatage `ts` + `ts_iso`), `MAX_WORKSPACES = 20` dans `~/.gemini/gravity/workflow/live_workspaces.json` (désactivable en test via `GRAVITY_WORKFLOW_SKIP_REGISTRY=1` et surchargeable via `GRAVITY_WORKFLOW_STATE_PATH` / `GRAVITY_WORKFLOW_WORKSPACE` / `GRAVITY_WORKFLOW_HOME` / `GRAVITY_HOME` / `GRAVITY_WORKFLOW_REGISTRY_PATH`).
- **Adaptateur lecture seule [`metrics_signals.py`](../plugin/observability/metrics_signals.py) :** lit de façon bornée (`MAX_SCAN_BYTES`, `MAX_TAIL_LINES`) les `5` dernières sessions archivées du projet dans `$GRAVITY_METRICS_HOME/data/history.jsonl` (`> $GRAVITY_HOME/metrics/data/history.jsonl > ~/.gemini/gravity/metrics/data/history.jsonl`) sans aucun import de `gravity-metrics` ni écriture disque, en excluant `status == "active"` (`sample_count >= 2` requis).
- **Clôture finale `gravity-metrics` (session racine mono-dépôt uniquement) :** zéro couplage Python (no-op silencieux si `~/.gemini/config/plugins/gravity-metrics/scripts/cli.py` est absent). Documentée dans [`08-session-learning.md`](../plugin/skills/gravity-workflow/references/08-session-learning.md) : exécutée uniquement après vérifications vertes et `git commit`/`git push` via `cli.py archive <conversation_id> -l "<projet>-<slug>" --workspace .` (ou `--update-sealed` sur un `git push` ultérieur, jamais `--force`), et **strictement interdite** aux sous-agents (`planner`, `builder`, `reviewer`), workers `swarm` et conversations enfants Gravity Bridge (`[GRAVITY-BRIDGE | ...]` ou `depth >= 1`).
- **Champs clés de `live_state.json` :**
  `stage`, `progress`, `mode`, `level`, `preset_profile`, `suggested_profile`, `suggested_reason`, `role_models`, `caveman`, `caveman_locked`, `guard_status`, `nodes` (`planner`/`builder`/`reviewer`), `events`.

---

## Activation utilisateur (App & CLI)

### 1. Cockpit unifié `/gravity dashboard` (`Antigravity App`)
Invoquer `/gravity dashboard` depuis le plugin Core `gravity` (ou ouvrir la **barre latérale gauche (`Gravity`)** ou le **menu `+` du panneau latéral droit**). Si une ancienne entrée manuelle `"gravity-workflow/live-widget"` ou `"gravity/live-widget"` subsiste sous `"sidecars"` dans `~/.gemini/config/config.json`, la retirer manuellement *(un agent ne doit jamais modifier `config.json`)*.

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

*(Sous Windows sans `python3` réel dans le `PATH`, remplacer `python3` par `python` ou `py -3`, et adapter `hooks.json` sur la copie installée via `validate.py --fix-python-cmd`.)*
