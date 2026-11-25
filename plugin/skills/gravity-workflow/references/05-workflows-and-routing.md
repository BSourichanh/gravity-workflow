# Routeur & workflows (Multi-orchestration native et profils `/gravity-workflow`)

## Objectif
Piloter l'orchestration, l'attribution des modèles et le workflow `workflows/review_pipeline.py` depuis le skill unifié `/gravity-workflow [quick|standard|deep|evo]`, sans clé morte et avec stockage centralisé sous `$GRAVITY_HOME/workflow/workspaces/<hash>/`.

## Profils `/gravity-workflow` & vérification éphémère
Un projet démarre par défaut en **`mode: "evo"` (consultatif, profil actif `standard`)**. Les alias historiques (`low→quick`, `medium→standard`, `high/ultra→deep`, `slow`/`fast`) restent tolérés sans erreur :

1. **Profil d'exécution (`quick`, `standard`, `deep`, `evo`) :** fixe les modèles (`plan_model`, `build_model`, `review_model`, `evals`), `max_remediation_rounds` et `skip_separate_planner`. Un profil fixe verrouille `profile.json` ; `/gravity-workflow evo` réactive la recommandation consultative (`suggested_profile`: `standard` | `deep`).
2. **Vérification éphémère (`--no-verify` / `unsafe`) :** s'applique uniquement à **1 tâche ou 1 invocation `run_workflow`**, n'est **jamais persistée** dans `profile.json`, et maintient `PreToolUse` actif.

| Profil | Orchestration | `planner` / `builder` / `reviewer` (`review_pipeline.py`) | `max_remediation_rounds` |
| :--- | :--- | :--- | :--- |
| **`quick`** *(alias `low`)* | Exécution rapide (`Builder` + `Reviewer flash` ; `--no-verify`/`unsafe` seul saute `Reviewer`) | `flash` *(sauté)* / `flash` / `flash` *(actif ; sauté seul si `--no-verify`)* | `0` |
| **`standard`** *(défaut, alias `medium`)* | Direct sur tâche triviale ; `review_pipeline.py` sur lot significatif | `flash` *(sauté si `plan` fourni)* / `flash` / `pro` *(repli `flash` si `allow_pro: false`)* | `1` |
| **`deep`** *(alias `high`/`ultra`)* | `review_pipeline.py` complet ; suggestion `/plan /grill-me`, `/boost` ou `/swarm` à l'initiative utilisateur | `pro` *(sauté si `plan` fourni)* / `flash` *(`pro` sur override)* / `pro` *(repli `flash`)* | `2` |

**Règles de surcharge & essais A/B :**
- `review_pipeline.py` et `run_suite.py` lisent automatiquement `profile_engine.get_active_preset()` ; les `Args` explicites (`level`, `plan`, `no_verify`, `plan_model`, `build_model`, `review_model`, `plugin_root`) restent prioritaires.
- Lorsque `args.get("plan")` est fourni non vide, `review_pipeline.py` saute le `Planner` séparé même en `standard` ou `deep` (sauf override `skip_separate_planner=False`).
- Un projet peut définir `"allow_pro": false` (rabattant tout modèle `pro` vers `flash`) ou `"model_overrides"` via `--plan-model`, `--build-model`, `--review-model` (`inherit` | `flash_lite` | `flash` | `pro` | `reset`). Chaque exécution journalise `preset_profile` et `role_models` effectifs dans `runs.jsonl` et `live_state.json`.

## Palier d'entrée : conversation simple, `/grill-me`, `/plan`, `/goal` & mode `Orchestrator`
Avant de lancer plusieurs agents, privilégie les mécanismes mono-fil moins gourmands en quota :
- **`/grill-me`** *(tous plans — slash command utilisateur ; côté agent, utiliser l'outil natif `ask_question` une question à la fois avec `(Recommended)` en premier)* : interview interactive avant implémentation pour lever les ambiguïtés d'architecture, de périmètre ou de comportement après exploration du dépôt. Se combine idéalement en **`/plan /grill-me`** pour verrouiller un plan validé avant d'engager `builder`, `review_pipeline.py` ou `swarm` ; les arbitrages différés sont consignés dans `.agents/gravity-workflow/BACKLOG.md`.
- **`Orchestrator`** *(Pro†, Ultra, Enterprise)* : combine dans une seule conversation un Planner frontier en lecture seule (`Gemini 4 Neon` par défaut, `Carbon`, `Argon`, `Claude Opus 5.5`) et un Executor rapide (`Gemini 4 Hydrogen`, `Claude Sonnet 5.5`), avec retour automatique au planner en cas d'imprévu.
- **`/plan`** *(tous plans)* : exploration sans effet de bord et plan d'implémentation approuvé avant code.
- **`/goal`** *(tous plans)* : exécution autonome jusqu'à vérification finale sans pauses intermédiaires.

## Matrice de routage des 4 moteurs multi-agents

| Moteur | Appel agent vs slash command | Disponibilité plan | Cas d'usage idéal & limites |
| :--- | :--- | :--- | :--- |
| **Dynamic Workflows** (`run_workflow`) | **Outil `run_workflow`** appelable directement par l'agent *(ou `/run-workflow` côté utilisateur)* | **Tous plans** | Flux répétables, traitements par lots, suite d'évaluations, boucle Builder→Reviewer structurée par JSON Schema (`phase()`, `parallel()`, `pipeline()`, `model="flash"` ou `"flash_lite"`). |
| **Swarm** (`swarm`) | **Outil `swarm`** appelable par l'agent *(ou `/swarm` côté utilisateur)* | **Tous plans** *(Preview)* | Tâches larges se découpant proprement par fichiers/packages/pistes avec `@verifier` (cross-model possible) et `@monitor`, coordination sur Swarm Board et veille à 0 token des membres inactifs. **Consomme rapidement le quota** (1 agent par worker). |
| **Boost** (`/boost`) | **Slash command utilisateur uniquement** *(l'agent recommande `/boost <tâche>`)* | **Plans payants uniquement** | Bug retors, race condition, problème algorithmique complexe ou refactorisation serrée : boucle adversariale (`DeepCoder`/`DeepInvestigator`) avec workers d'amélioration et vérification indépendante. |
| **Teamwork** (`/teamwork`) | **Slash command utilisateur uniquement** *(l'agent recommande `/teamwork <objectif>`)* | **Plans payants uniquement** | Grand chantier multi-jalons (> 1 jour) dans `~/teamwork_projects/`. 5 chemins (`General`, `Iterative`, `Document Review`, `Math/Proof`, `Large Team`), 3 modes d'intégrité (`development`, `demo`, `benchmark`) et 4 portes d'audit (`Critic`, `Challenger`, `Auditor`, `Success Auditor`). |

## Arbre de décision pratique (maîtrise des quotas)
- Besoin flou, décisions de design ouvertes ou chantier structurant avant code -> explorer le dépôt puis clarifier une question à la fois via `ask_question` (ou recommander `/grill-me` / `/plan /grill-me` à l'utilisateur) ; en profil `quick`, se limiter aux ambiguïtés strictement bloquantes.
- Tâche quotidienne ou modification ciblée -> `Orchestrator` (ou `/plan` + conversation simple, paliers `low`/`medium`).
- Lots répétitifs, audits systématiques ou evals -> outil `run_workflow` (preset issu de `profile_engine.get_active_preset`).
- Chantier parallélisable sur modules indépendants -> outil `swarm` (paliers `high`/`ultra`, avec `@verifier` sur un modèle distinct ; ni les workers `swarm` ni le `builder` de `review_pipeline.py` ne committent, poussent ou taguent : cette étape reste réservée au chef d'orchestre).
- Point dur algorithmique ou concurrence après échec -> proposer `/boost` à l'utilisateur (plan payant).
- Chantier multi-jalons multi-jours -> proposer `/teamwork` à l'utilisateur (plan payant).

## Composants fournis par le plugin (`plugin/`)
1. **`skills/gravity-workflow/SKILL.md` & `skills/caveman/SKILL.md`** : skill principal `/gravity-workflow` (`quick`/`standard`/`deep`/`evo`, `status`, `evals`, `init`) et skill compagnon `/caveman` (`lite` défaut sur les 3 profils, `commit`, `compress`).
2. **`observability/profile_engine.py` & `observability/caveman_protocol.py`** : résout les presets actifs (`plan_model`, `build_model`, `review_model`, `max_remediation_rounds`, `eval_candidate`, `eval_judge`, `skip_separate_planner`, `skip_verification` éphémère, `caveman`, `caveman_planner`, `caveman_evals="off"`) depuis le profil centralisé (`$GRAVITY_HOME/workflow/workspaces/<hash>/profile.json`, repli lecture seule `.agents/gravity-workflow/profile.json`).
3. **`workflows/patch_apply.py`, `workflows/review_pipeline.py` & `workflows/wave_runner.py`** :
   - `patch_apply.py` fournit `resolve_worktree_base()` (`worktree_base` > `GRAVITY_WORKTREE_BASE` > `$GRAVITY_WORKFLOW_HOME/worktrees` / `$GRAVITY_HOME/workflow/worktrees` / `~/.gemini/gravity/workflow/worktrees`, avec repli Linux Snap vers `~/gravity-worktrees` si `agy`/`antigravity` vient de `/snap/` et que la base résolue est dans un dotdir caché sous `$HOME`), `create_repo_worktree()`, `cleanup_worktree_best_effort()` (protégeant `gravity-worktrees`), `extract_changed_files_from_diff()` (`1..20`), `_safe_save_verified_patch()` (UTF-8 sans BOM, LF), `apply_verified_patch()` / `--apply --workspace ... [--worktree ...]` et `BUILDER_HYGIENE_DIRECTIVE`.
   - `review_pipeline.py` (top-level `await` natif pour `run_workflow`) supporte `Args.plan` et `Args.plan_file` (lecture UTF-8 Python à `0` token forçant `skip_separate_planner=True`), encadre `Builder` (`900s`) et `Reviewer` (`360s`) par `asyncio.wait_for`, collecte `changed_files` (`1..20`) et journalise dans `live_state.json` et `runs.jsonl`.
   - `wave_runner.py` (`< 650` L, 100 % stdlib) orchestre des vagues multi-dépôts via `run_workflow` (`parallel` *fail-isolated* en `try/except`) depuis un manifeste neutre `args["wave_plan"]` (`wave_plan_v1`, dict ou chemin JSON) ou `args["tasks"]` structurés (`repo_root` explicite + `plan_file`/`plan`, rejet *fail-closed* de toute chaîne brute, `schema_version != "wave_plan_v1"`, `repo_root` manquant/invalide ou `status` bloqué), lit `plan_file` + `<repo>/AGENTS.md` en Python (`0` token), crée un `git worktree` détaché par dépôt, exécute `Builder` (`900s`) + `Reviewer` (`quick`=`flash` `0` remédiation, `standard`/`deep`=`pro`, `360s`, sans `Planner`), applique sur `dev` (`apply_on_pass=True`), nettoie en `try/finally` et retourne un JSON compact (`<= 10` lignes/dépôt, `changed_files` `1..20`).

## Protocole natif de vagues (`wave_runner.py`), `/swarm` & hygiène d'exécution
- **Production 100 % native :** utiliser `run_workflow` sur `plugin/workflows/wave_runner.py` (`Args={"wave_plan": "<path_ou_dict_wave_plan_v1>", "apply_on_pass": true}` ou `Args={"tasks": [{"repo_root": "...", "plan_file": "..."}], "apply_on_pass": true}`) pour les lots planifiés multi-dépôts, ou `/swarm` natif pour les chantiers couplés/exploratoires (zéro dépendance active vers `gravity-bridge`). Sur Linux Snap (`/snap/bin/agy`), créer les worktrees `/swarm` hors dotdir caché (via `GRAVITY_WORKTREE_BASE` ou `~/gravity-worktrees`) pour permettre `agy plugin validate ./plugin --strict`.
- **Directives Builder & `/swarm` obligatoires :** préfixer les briefs `/swarm` d'une consigne Caveman sur 1 ligne (`[Caveman: style télégraphique concis, 0 prose superflue]`) ; pour toute commande longue via `run_command`, utiliser `WaitMsBeforeAsync=10000` + `NotificationTimeoutSeconds=120..300` et attendre la notification sans appeler `manage_task(status)` en boucle ; sous PowerShell Windows, enchaîner avec `;` (zéro `&&`) ; sous POSIX/Linux, utiliser `grep -rn` (jamais `rg`) ; pour `read_file`, toujours fournir un `EndLine` absolu `>= StartLine` ; chemin exact de cette fiche : `plugin/skills/gravity-workflow/references/05-workflows-and-routing.md`.
- **Test AST top-level `await` :** vérifier `wave_runner.py` et `review_pipeline.py` en compilation/test unitaire avec `compile(source, path, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)` (sans `ast.parse` nu qui rejette `await` top-level).
- **Limites strictes workers/subagents :** zéro `git commit`, `git push`, `git tag`, zéro montée de version ni archivage `gravity-metrics` dans `wave_runner.py`, `review_pipeline.py` ou `/swarm` (strictement réservés au chef d'orchestre racine).

## Checkpoint déterministe
- Le skill unifié `skills/gravity-workflow/SKILL.md` et `observability/profile_engine.py` couvrent les 3 profils (`quick`/`standard`/`deep`), le mode consultatif `evo` et les surcharges A/B.
- `workflows/review_pipeline.py` et `workflows/wave_runner.py` sautent `Planner` sur `plan`/`plan_file` (`wave_runner.py` consomme `wave_plan_v1` ou `tasks` structurés *fail-closed*), appliquent les watchdogs `900s`/`360s`, protègent `dev` par worktrees isolés (`try/finally`) et retournent `changed_files` (`1..20`).
