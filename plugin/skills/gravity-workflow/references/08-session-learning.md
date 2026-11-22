# Suivi, adaptation `evo` & boucle `/learn` (Apprentissage continu)

## Objectif
Transformer chaque incident, blocage ou correction manuelle en amélioration durable et testable du harnais du projet, tout en journalisant automatiquement les métriques de chaque fin de boucle d'exécution et en adaptant le profil `evo` du projet.

## 1. Journalisation & adaptation automatique `evo` via le Hook `Stop`
- Script **`plugin/hooks/stop_session_logger.py`** (permissions `0755`, stdlib Python 3, `exit 0` garanti, verrou portable `exclusive_file_lock` réutilisé via `hook_runtime.py` depuis `state_writer.py`), câblé dans `plugin/hooks.json` sous le groupe `harness-session-metrics` via `hooks/stop_session_logger.py`.
- **Contrat réel Antigravity `Stop` :** ce hook se déclenche à chaque fin de boucle d'exécution (et peut voir `fullyIdle: false` si des tâches de fond tournent encore). Il reçoit les champs camelCase natifs (`conversationId`, `modelName`, `terminationReason`, `executionNum`, `fullyIdle`, `continuationActive`, `transcriptPath`, `error`, `cwd`), résout le workspace du projet depuis `cwd`/`workspacePaths` et émet strictement **`{}`** sur `stdout` pour autoriser l'arrêt.
- Ajoute une entrée JSON par ligne dans **`.agents/gravity-workflow/observability/runs.jsonl`** du projet contenant : `timestamp` (UTC ISO-8601), `event`, `conversation_id`, `model`, `termination_reason`, `execution_num`, `fully_idle`, `continuation_active`, `error`, `transcript_path` et `cwd`.
- Appelle ensuite **`profile_engine.evolve_on_stop()`** (uniquement si le projet possède `.agents/gravity-workflow/profile.json` ou `PROGRESS.md`, en ignorant `fully_idle: false`, avec idempotence par `conversation_id`) :
  - Calcule un score déterministe `0..100` (volume fichiers, tests/CI, règles/MCP, alertes `PreToolUse`, évals, erreurs, série propre `clean_streak`).
  - En mode **`evo`**, ajuste `current_level` d'au plus **±1 palier par session** avec une marge d'hystérésis de **±5 pts** et consigne la raison dans `.agents/gravity-workflow/profile.json`, sans jamais modifier les choix orthogonaux `pace` (`slow`/`fast`) ni `verification` (`safe`/`unsafe`) de l'utilisateur.
  - Adapte également `caveman` automatiquement (`lite` par défaut ou si instabilité `session_error`/`deny`/`eval_failing`, `full` si `high`/`ultra` propre ou `clean_streak >= 2`, jamais `ultra`/`off` en auto) tant que `caveman_locked` vaut `false` (`/caveman auto` / `evo` redonne la main au moteur).
  - Si un palier fixe est **verrouillé** (`/gravity-workflow low|medium|high|ultra`), conserve `current_level` inchangé et met uniquement à jour `suggested_level`.

## 2. Protocole de la Double Boucle `/learn`
Ne jamais « bricoler » un prompt global de façon empirique. Dès qu'une correction manuelle ou un échec de test survient, déclencher les 2 boucles conjointement dans le projet :

1. **Boucle de comportement (`.agents/rules/`)** :
   - L'utilisateur invoque `/learn` (slash command UI/CLI) qui analyse la session et génère un artifact de proposition (`learning_proposal.md`) à valider avant toute écriture, ou l'agent propose un diff ciblé.
   - Choisir un déclencheur conditionnel (`trigger: glob` ou `trigger: model_decision` — éviter `trigger: manual` si le flux cible le CLI `agy`) afin de **ne jamais surcharger le socle `trigger: always_on`**, préserver le **Context Caching Gemini** (préfixe statique) et rester sous 20 000 tokens agrégés.
2. **Boucle de non-régression (`.agents/gravity-workflow/evals/cases.json`)** :
   - Ajouter immédiatement un nouveau cas dans `.agents/gravity-workflow/evals/cases.json` reproduisant l'anomalie corrigée (avec au moins un prédicat `auto_checks`).
   - Rejouer `/gravity-workflow evals` (via l'outil `run_workflow`) pour vérifier l'intégration avant la prochaine release.

## Checkpoint déterministe
- Dry-run sur `stop_session_logger.py` (avec un payload `Stop` Antigravity) crée/alimente `.agents/gravity-workflow/observability/runs.jsonl` et met à jour `.agents/gravity-workflow/profile.json` dans le workspace cible, émet `{}` et sort en `exit=0`.
- Au moins un cycle complet de double boucle `/learn` (1 règle conditionnelle + 1 cas dans `.agents/gravity-workflow/evals/cases.json`) est documenté ou vérifié.
- Mettre à jour `.agents/gravity-workflow/PROGRESS.md` (8/8) et afficher la micro-carte.
