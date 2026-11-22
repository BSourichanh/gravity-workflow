# Routeur & workflows (Multi-orchestration native et paliers `/gravity-workflow`)

## Objectif
Piloter l'orchestration, l'attribution des modèles et le workflow `workflows/review_pipeline.py` depuis le skill unifié `/gravity-workflow [low|medium|high|ultra|evo]`, en adaptant automatiquement la profondeur de revue aux enjeux du projet, à l'éligibilité du plan Antigravity et aux quotas (fenêtre 5h / hebdomadaire).

## Modes `/gravity-workflow`, stratégie workflow & vérification (3 axes orthogonaux)
Un projet démarre par défaut en **`mode: "evo"` (palier `medium`)**, **`pace: "slow"`** (par responsabilité) et **`verification: "safe"`**. Les 3 axes se combinent librement dans n'importe quel ordre (`/gravity-workflow high fast`, `/gravity-workflow fast unsafe`, `/gravity-workflow evo slow safe`) :

1. **Mode / Réflexion (`low`, `medium`, `high`, `ultra`, `evo`) :** fixe uniquement la profondeur de réflexion et les modèles (`plan_model`, `build_model`, `review_model`, `evals`). Un palier fixe verrouille `.agents/gravity-workflow/profile.json` ; `/gravity-workflow evo` réactive l'adaptation en fin de session.
2. **Stratégie Workflow / Temps (`slow` vs `fast`) — sans impact sur la réflexion :**
   - **`slow` *(par défaut)* :** choisit le moteur par responsabilité (`Orchestrator`/`/plan`, boucle séquentielle complète `Planner → Builder → Reviewer`, `swarm` si multi-modules).
   - **`fast` :** optimise le **temps d'exécution** sans toucher aux modèles ni à la réflexion du palier — parallélise agressivement (`swarm` / `parallel()`), condense la planification séparée lorsque la tâche est cadrée et limite les remédiations séquentielles à 1 passe max. **Laisse tests et vérifications actifs.**
3. **Vérification & Tests (`safe` vs `unsafe`) :**
   - **`safe` *(par défaut)* :** exécute la revue indépendante (`review_pipeline.py`), les boucles de remédiation, les tests unitaires, `/gravity-workflow evals`, `validate.py` et `PostToolUse`.
   - **`unsafe` :** désactive **toute** procédure de test et de vérification du workflow (`review_pipeline.py` livre directement après le Builder, aucun test ni formatage `PostToolUse` exécuté). Les garde-fous `PreToolUse` restent **toujours actifs** sur tous les axes.

| Palier | Orchestration | `planner` / `builder` / `reviewer` (`review_pipeline.py`) | `max_remediation_rounds` | Modèles `swarm` / recommandations |
| :--- | :--- | :--- | :--- | :--- |
| **`low`** | Conversation directe mono-fil (économe) | `flash_lite` / `flash_lite` / `flash` *(uniquement si forcé)* | `0` | Pas de `swarm` ; UI conseillée `3.8 Flash` / `Hydrogen` (`/effort low`) |
| **`medium`** *(défaut `evo`)* | `Orchestrator` / `/plan` (+ `/grill-me` si ambigu) + `review_pipeline.py` sur modifs significatives | `inherit` / `flash` / `pro` *(repli `inherit` si `allow_pro: false`)* | `1` | Pas de `swarm` par défaut ; UI conseillée `Orchestrator` (`/effort medium`) |
| **`high`** | `/plan /grill-me` si périmètre ouvert + `review_pipeline.py` complet ou `swarm` si multi-modules | `pro` / `flash` / `pro` *(repli `inherit` sur Free/Plus)* | `2` | `swarm` avec workers rapides et `@verifier` frontier (`Neon`) |
| **`ultra`** | `/plan /grill-me` si structurant + `swarm` cross-model, `/boost` ou `/teamwork` | `pro` / `pro` / `pro` | `2` (+ escalade `/boost`) | `@verifier` **cross-model obligatoire** via `swarm(action="models")`, UI `Neon`/`Carbon` (`/effort xhigh`) |

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
- Besoin flou, décisions de design ouvertes ou chantier structurant avant code -> explorer le dépôt puis clarifier une question à la fois via `ask_question` (ou recommander `/grill-me` / `/plan /grill-me` à l'utilisateur) ; en stratégie `fast`, se limiter aux ambiguïtés strictement bloquantes.
- Tâche quotidienne ou modification ciblée -> `Orchestrator` (ou `/plan` + conversation simple, paliers `low`/`medium`).
- Lots répétitifs, audits systématiques ou evals -> outil `run_workflow` (preset issu de `profile_engine.get_active_preset`).
- Chantier parallélisable sur modules indépendants -> outil `swarm` (paliers `high`/`ultra`, avec `@verifier` sur un modèle distinct ; ni les workers `swarm` ni le `builder` de `review_pipeline.py` ne committent, poussent ou taguent : cette étape reste réservée au chef d'orchestre).
- Point dur algorithmique ou concurrence après échec -> proposer `/boost` à l'utilisateur (plan payant).
- Chantier multi-jalons multi-jours -> proposer `/teamwork` à l'utilisateur (plan payant).

## Composants fournis par le plugin (`gravity-workflow-plugin/`)
1. **`skills/gravity-workflow/SKILL.md` & `skills/caveman/SKILL.md`** : skill principal `/gravity-workflow` (3 axes orthogonaux, paliers, `status`, `evals`, `init`) et skill compagnon `/caveman` (concision ciblée par ROI sans collision sur `ultra`, avec `commit` et `compress`).
2. **`observability/profile_engine.py` & `observability/caveman_protocol.py`** : résout les presets actifs (`plan_model`, `build_model`, `review_model`, `max_remediation_rounds`, `eval_candidate`, `eval_judge`, `pace`, `workflow_strategy`, `parallelize_aggressively`, `skip_separate_planner`, `verification`, `skip_verification`, `skip_tests`, `caveman`, `caveman_planner`, `caveman_builder`, `caveman_reviewer`, `caveman_evals="off"`) et gère `.agents/gravity-workflow/profile.json`.
3. **`workflows/review_pipeline.py`** : workflow `planner` (condensé si `skip_separate_planner=True` en stratégie `fast`, tout en conservant les modèles du palier ; concision Caveman plafonnée à `full`) → `builder` multi-tour (unique worktree `workspace="branch"`, produisant de façon déterministe `.gravity_verified.patch` via `git add -N .` puis `git diff --output=.gravity_verified.patch -- . ":(exclude).gravity_verified.patch"`) → en mode **`unsafe`** (`skip_verification=True`), exporte directement le patch et clôture sans revue ni remédiation ; sinon (`safe`) → `reviewer` indépendant (`workspace="inherit"` lecture seule + diff lu en pur Python sans `subprocess`, constats sur 1 ligne sous Caveman avec *Preuve ou Silence* intact, JSON Schema compatible Gemini sans `maxItems`) → remédiation ciblée bornée par `max_remediation_rounds` (`0`/`1`/`2` selon le palier et plafonné à `1` en `fast`, dans **le même** builder multi-tour avec contrôle `build.get("refused")` et `finally: del builder`), verrou déterministe sur `blockers`/`majors`, export du patch vérifié complet dans le projet (`.agents/gravity-workflow/observability/last_verified.patch`) et mise à jour de `.agents/gravity-workflow/observability/live_state.json`.

## Checkpoint déterministe
- Le skill unifié `skills/gravity-workflow/SKILL.md` et `observability/profile_engine.py` couvrent les 4 paliers, le mode `evo`, l'attribution des modèles et la distinction outil vs slash command.
- `workflows/review_pipeline.py` lit le preset actif depuis `profile_engine`, garde le même `builder` multi-tour sur `workspace="branch"`, exécute `reviewer` en `workspace="inherit"` avec le `git_diff`, et ferme l'agent dans un `finally`.
- Mettre à jour `.agents/gravity-workflow/PROGRESS.md` et afficher la micro-carte.
