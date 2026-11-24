# Suivi, adaptation `evo` & boucle `/learn` (Apprentissage continu)

## Objectif
Transformer chaque incident, blocage ou correction manuelle en amélioration durable et testable du harnais du projet, tout en journalisant automatiquement les métriques de chaque fin de boucle d'exécution et en adaptant le profil `evo` du projet.

## 1. Journalisation & adaptation automatique `evo` via le Hook `Stop`
- Script **`plugin/hooks/stop_session_logger.py`** (permissions `0755`, stdlib Python 3, `exit 0` garanti, verrou portable `exclusive_file_lock` réutilisé via `hook_runtime.py` depuis `state_writer.py`), câblé dans `plugin/hooks.json` sous le groupe `harness-session-metrics` via `hooks/stop_session_logger.py`.
- **Contrat réel Antigravity `Stop` :** ce hook se déclenche à chaque fin de boucle d'exécution (et peut voir `fullyIdle: false` si des tâches de fond tournent encore). Il reçoit les champs camelCase natifs (`conversationId`, `modelName`, `terminationReason`, `executionNum`, `fullyIdle`, `continuationActive`, `transcriptPath`, `error`, `cwd`), résout le workspace du projet depuis `cwd`/`workspacePaths` et émet strictement **`{}`** sur `stdout` pour autoriser l'arrêt.
- Ajoute une entrée JSON par ligne dans **`$GRAVITY_HOME/workflow/workspaces/<hash>/runs.jsonl`** (stockage centralisé, zéro écriture dans le workspace cible) contenant : `timestamp` (UTC ISO-8601), `event`, `conversation_id`, `model`, `preset_profile`, `role_models`, `termination_reason`, `execution_num`, `fully_idle`, `continuation_active`, `error`, `transcript_path` et `cwd`.
- Appelle ensuite **`profile_engine.evolve_on_stop()`** sur **tous les dépôts** sans nécessiter `/gravity-workflow init` (en ignorant `fully_idle: false`, avec idempotence par `(conversation_id, execution_num)`) :
  - Évalue uniquement les signaux comportementaux : rejets `Reviewer`, erreurs outils/tests, `guard_deny_recent` et `eval_pass_rate` (zéro comptage de fichiers `< 80` ni rétrogradation vers `flash_lite`).
  - Complète en lecture seule via **`observability/metrics_signals.py`** (`$GRAVITY_METRICS_HOME/data/history.jsonl` > `$GRAVITY_HOME/metrics/data/history.jsonl` > `~/.gemini/gravity/metrics/data/history.jsonl`, 5 dernières sessions archivées du projet, `sample_count >= 2`, exclusion stricte `status == "active"`). Un `metrics_delta` borné **`[-5, +10]`** ajuste le score (`+5` si `avg_error_rate_pct >= 15.0` ou `recent_rewind_calls >= 3`, `+5` si `recent_duplicate_spawns >= 2` ou `max_context_tokens >= 180_000`, `-5` si zéro friction, `sample_count >= 3`, `avg_error_rate_pct <= 3.0`, `avg_cache_hit_rate_pct >= 75.0`, `recent_duplicate_spawns == 0`, `recent_rewind_calls == 0`).
  - Expose **`suggested_profile` (`standard` | `deep`) + motif** dans `status`, `profile.json` et `live_state.json` sans jamais muter silencieusement le profil actif.
  - Maintient `caveman: lite` par défaut et en cas d'alerte (`session_error`, `guard_deny_recent > 0`, `eval_pass_rate < 1.0`), et passe automatiquement à `caveman: full` lorsque `metrics.available` est `True` et (`max_context_tokens >= 150_000` ou `high_output_cost_ratio >= 0.35`) — jamais `ultra` ni `off` automatiquement.

## 2. Protocole de la Double Boucle `/learn`
Ne jamais « bricoler » un prompt global de façon empirique. Dès qu'une correction manuelle ou un échec de test survient, déclencher les 2 boucles conjointement dans le projet :

1. **Boucle de comportement (`.agents/rules/`)** :
   - L'utilisateur invoque `/learn` (slash command UI/CLI) qui analyse la session et génère un artifact de proposition (`learning_proposal.md`) à valider avant toute écriture, ou l'agent propose un diff ciblé.
   - Choisir un déclencheur conditionnel (`trigger: glob` ou `trigger: model_decision` — éviter `trigger: manual` si le flux cible le CLI `agy`) afin de **ne jamais surcharger le socle `trigger: always_on`**, préserver le **Context Caching Gemini** (préfixe statique) et rester sous 20 000 tokens agrégés.
2. **Boucle de non-régression (`.agents/gravity-workflow/evals/cases.json`)** :
   - Ajouter immédiatement un nouveau cas dans `.agents/gravity-workflow/evals/cases.json` reproduisant l'anomalie corrigée (avec au moins un prédicat `auto_checks`).
   - Rejouer `/gravity-workflow evals` (via l'outil `run_workflow`) pour vérifier l'intégration avant la prochaine release.

## 3. Clôture finale `gravity-metrics` (session racine mono-dépôt uniquement)
Dans une session `/gravity-workflow` autonome (hors atelier `gravity-architect`), la clôture finale de la fenêtre `gravity-metrics` évite de laisser la session dans la file « À archiver ». Ce mécanisme reste **100 % découplé en Python** (zéro import ni écriture depuis le plugin) et doit être un **no-op silencieux** si `~/.gemini/config/plugins/gravity-metrics/scripts/cli.py` n'existe pas.

- **Déclenchement :** uniquement **après** le passage au vert de toutes les vérifications obligatoires, le `git commit` et l'éventuel `git push` demandé par l'utilisateur.
- **Commande en toute fin de séquence :**
  ```bash
  if [ -f "$HOME/.gemini/config/plugins/gravity-metrics/scripts/cli.py" ]; then
    python3 ~/.gemini/config/plugins/gravity-metrics/scripts/cli.py archive <conversation_id> -l "<projet>-<slug>" --workspace .
  fi
  ```
- **Mise à jour post-archivage :** si un `git push` est demandé un tour plus tard dans la même conversation déjà archivée, mettre à jour l'archive scellée via `--update-sealed` (**sans jamais passer `--force`**) :
  ```bash
  python3 ~/.gemini/config/plugins/gravity-metrics/scripts/cli.py archive <conversation_id> --update-sealed --workspace .
  ```
- **Interdiction absolue de périmètre :**
  - Strictement réservé à l'agent **racine** d'une session mono-dépôt.
  - **Interdit** aux sous-agents (`planner`, `builder`, `reviewer`), aux workers `swarm` et aux conversations enfants Gravity Bridge (`[GRAVITY-BRIDGE | ...]` ou `depth >= 1`), afin de ne jamais sceller prématurément une conversation enfant avant son callback vers l'atelier appelant (ces sessions étant déjà agrégées par la session racine).

## Checkpoint déterministe
- Dry-run sur `stop_session_logger.py` (avec un payload `Stop` Antigravity) alimente `runs.jsonl` et met à jour `profile.json` dans le stockage centralisé `$GRAVITY_HOME/workflow/workspaces/<hash>/` (zéro fichier écrit dans le workspace cible), émet `{}` et sort en `exit=0`.
- Au moins un cycle complet de double boucle `/learn` (1 règle conditionnelle + 1 cas dans `.agents/gravity-workflow/evals/cases.json`) est documenté ou vérifié.
- Mettre à jour `.agents/gravity-workflow/PROGRESS.md` (8/8) et afficher la micro-carte.
