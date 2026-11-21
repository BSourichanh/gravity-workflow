# Modes `/gravity`, Routage & Profil Adaptatif

Le plugin expose le skill principal `/gravity` ([`SKILL.md`](../gravity-plugin/skills/gravity/SKILL.md)) et son skill compagnon `/caveman` ([`caveman/SKILL.md`](../gravity-plugin/skills/caveman/SKILL.md)), pilotés par [`profile_engine.py`](../gravity-plugin/observability/profile_engine.py) et [`caveman_protocol.py`](../gravity-plugin/observability/caveman_protocol.py), puis persistés dans `.agents/gravity/profile.json`.

---

## Grammaire d'invocation (3 axes orthogonaux + utilitaires)

`/gravity` accepte **0 à 3 mots-clés positionnels** en tête de commande (dans n'importe quel ordre, sans flag `--mode` dans le chat), ou l'une des 3 sous-commandes utilitaires :

| Axe / Commande | Mots-clés valides | Défaut | Effet |
|---|---|---|---|
| **Axe 1 — Réflexion (`mode`)** | `evo` *(alias `evolutive`)*, `low`, `medium`, `high`, `ultra` | **`evo` (`medium`)** | Fixe la profondeur de réflexion et les modèles ; `low`/`medium`/`high`/`ultra` verrouillent (`locked: true`) jusqu'à `/gravity evo` |
| **Axe 2 — Stratégie Workflow (`pace`)** | `slow`, `fast` | **`slow`** | `slow` : par responsabilité (`Planner -> Builder -> Reviewer`). `fast` : optimisé temps (parallélisation agressive, `Planner` séparé condensé, max 1 passe séquentielle) **sans réduire les modèles ni couper les tests** |
| **Axe 3 — Vérification (`verification`)** | `safe`, `unsafe` | **`safe`** | `unsafe` désactive revue indépendante, remédiation, tests unitaires, évals post-tâche, `validate.py` et auto-formatage `PostToolUse`. Les hooks `PreToolUse` restent **toujours actifs** |
| **Utilitaires** | `status`, `evals [CASE-ID...]`, `init [quickstart\|full]` | — | `status` (bilan 4 blocs), `evals` (lance la suite même en profil `unsafe` si demandé explicitement), `init` (initialise le socle sans écraser) |

### Règles de parsing anti-ambiguïté
- Correspondance **mot entier uniquement** en tête de commande (`fast-path` ou `low-latency` ne sont pas interprétés comme `fast` ou `low`).
- `status`, `evals` et `init` ne déclenchent une sous-commande que s'ils sont seuls ou suivis uniquement de leurs arguments dédiés (`CASE-ID...` pour `evals`, `quickstart|full` pour `init`) ; sinon l'entrée est exécutée comme `<tâche>` (ex. `/gravity status de la PR`).
- À chaque invocation `/gravity` (Reprise de session, Audit ou Initialisation), si `.agents/rules/05-git-versioning.md` est absent du projet cible, il est créé automatiquement sans écraser l'existant (`init_workspace.py --ensure-git-rule`). Cette règle Git & versioning s'applique **exclusivement au chef d'orchestre**, jamais aux sous-agents ni aux workers `swarm`.
- En CLI hors chat, [`profile_engine.py`](../gravity-plugin/observability/profile_engine.py) expose `get`, `set` (`--mode`, `--pace`, `--verify`/`--verification`, `--caveman`, `--allow-pro`, `--reason`, `--json`) et `evolve`.

---

## Matrice des 4 paliers & attribution des modèles

Les modèles résolus par `get_active_preset()` dans `profile_engine.py` dépendent **uniquement du palier** (jamais altérés par `pace`) :

| Rôle / Paramètre | `low` | `medium` *(défaut `evo`)* | `high` | `ultra` |
|---|---|---|---|---|
| **Orchestration** | Direct mono-fil | `Orchestrator` / `/plan` (+ `/grill-me` si ambigu) + `review_pipeline.py` si modifs significatives | `/plan /grill-me` si périmètre ouvert + `review_pipeline.py` complet ou `swarm` multi-modules | `/plan /grill-me` si structurant + `swarm` cross-model, `/boost` ou `/teamwork` |
| **`plan_model`** | `flash_lite` | `inherit` | `pro` | `pro` |
| **`build_model`** | `flash_lite` | `flash` | `flash` | `pro` |
| **`review_model`** | `flash` | `pro` | `pro` | `pro` |
| **`max_remediation_rounds`** | `0` | `1` *(cap `1` en `fast`)* | `2` *(cap `1` en `fast`)* | `2` *(cap `1` en `fast`)* |
| **`eval_candidate` / `eval_judge`** | `flash_lite` / `flash` | `flash` / `inherit` | `flash` / `pro` | `flash` / `pro` |
| **`use_swarm` / `swarm_cross_model`** | `False` / `False` | `False` / `False` | `True` / `False` | `True` / `True` |
| **`init_mode`** | `quickstart` | `choice` | `full` | `full` |

**Règles de surcharge :**
- **`"allow_pro": false`** dans `profile.json` rabat automatiquement tout modèle `pro` vers `inherit` (plans Free/Plus).
- **`"model_overrides"`** permet de surcharger individuellement `plan_model`, `build_model`, `review_model`, `eval_candidate` ou `eval_judge` parmi `{"inherit", "flash_lite", "flash", "pro"}`.
- Les `Args` passés explicitement à `review_pipeline.py` ou `run_suite.py` restent prioritaires sur le preset.

---

## Cadrage avant exécution (protocole `/grill-me`)

Avant de modifier le dépôt ou de lancer `review_pipeline.py` / `swarm` sur une tâche ambiguë ou structurante :

1. **Explorer d'abord le dépôt :** ne jamais poser une question dont le code ou les sources de vérité détiennent déjà la réponse.
2. **Interview itérative (`ask_question` vs slash command) :** l'agent ne peut pas invoquer `/grill-me` lui-même ; il pose **une question à la fois via `ask_question`** (option recommandée en premier préfixée par `(Recommended)`), ou recommande à l'utilisateur de lancer **`/grill-me`** / **`/plan /grill-me`** *(slash command utilisateur uniquement)* pour verrouiller un plan avant code.
3. **Adaptation aux axes & traçabilité :** en stratégie `fast`, limiter le cadrage aux seules ambiguïtés strictement bloquantes ; en paliers `high` / `ultra` (ou avant un `swarm` / `/teamwork`), recommander explicitement `/plan /grill-me` si le périmètre reste ouvert. Tout arbitrage différé est consigné dans `.agents/gravity/BACKLOG.md`.

---

## Profil `.agents/gravity/profile.json` & adaptation `evo`

Le schéma par défaut (`schema_version: 1`, modèle dans [`workspace-template/.agents/gravity/profile.json`](../workspace-template/.agents/gravity/profile.json)) démarre avec `mode: "evo"`, `current_level: "medium"`, `locked: false`, `pace: "slow"`, `verification: "safe"`, `caveman: "lite"`, `caveman_locked: false`, `last_score: 40`.

En fin de session (`Stop`), [`stop_session_logger.py`](../gravity-plugin/hooks/stop_session_logger.py) appelle `evolve_on_stop()` *(idempotent par couple `(conversation_id, execution_num)`, réévaluant les tours `2..N` d'une conversation tout en plafonnant la variation à ±1 palier par rapport au niveau initial `last_conversation_start_level`, ignoré si `fully_idle: false` ou hors projet Gravity)* :

1. **Collecte bornée des signaux** (`MAX_SCAN_FILES = 500`) : nombre de fichiers sources, présence de tests/CI, nombre de règles `.agents/rules/*.md`, serveurs MCP, étapes cochées dans `PROGRESS.md`, alertes `deny` / `force_ask` dans les 15 derniers événements `live_state.json` postérieurs à `last_updated` (filtrage par `ts_iso`), taux de réussite `evals/report.json`, et erreurs de session (`NORMAL_TERMINATION_REASONS` inclut `TERMINAL_CUSTOM_HOOK`, `INJECTED_RESPONSE`, `TERMINAL_STEP_TYPE` et `USER_CANCELED` afin d'éviter les faux positifs avec les hooks `PreInvocation` comme `gravity-metrics`).
2. **Calcul déterministe du score `0..100`** : inclut un bonus d'allègement (`-5` si `eval_pass_rate == 100 %`, `-10` si `clean_streak >= 3` sans erreur ni `deny`).
3. **Seuils & hystérésis (`HYSTERESIS_MARGIN = 5`) :**
   - Plages cibles : `low` `[0, 25)`, `medium` `[25, 55)`, `high` `[55, 80)`, `ultra` `[80, 101)`.
   - Pour monter ou descendre, le score doit dépasser la frontière de **±5 points** ; la transition est plafonnée à **±1 palier par session**.
   - Si `locked: true`, `current_level` reste figé et seul `suggested_level` est mis à jour ; `pace` et `verification` ne sont **jamais modifiés** par `evo`.

---

## Skill compagnon `/caveman` & couplage ROI

La concision est pilotée séparément par [`/caveman`](../gravity-plugin/skills/caveman/SKILL.md) sans collision avec `/gravity ultra` :

- **Commandes `/caveman` :** `/caveman` *(passe en `full` ou affiche l'état si déjà `full`)*, `/caveman lite|full|ultra|off` *(verrouille `caveman_locked: true`)*, `/caveman auto` *(alias `evo`, déverrouille `caveman_locked: false` et recalcule immédiatement)*, `/caveman status`, `/caveman commit` *(Conventional Commits `<= 50` car.)*, `/caveman compress <fichier>` *(condensation Markdown avec diff validé + sauvegarde `.original`)*.
- **Adaptation automatique `evo` (`caveman_locked: false`) :** gérée par `choose_evo_caveman()` dans [`caveman_protocol.py`](../gravity-plugin/observability/caveman_protocol.py) :
  - Instabilité (`session_error`, `guard_deny_recent > 0` ou `eval_pass_rate < 1.0`) → **`lite`** *(clarté prioritaire)* ;
  - Sinon palier `high` ou `ultra` → **`full`** *(ROI modèles `pro`/frontier)* ;
  - Sinon `clean_streak >= 2` → **`full`** *(régime stable)* ;
  - Sinon → **`lite`** *(défaut nominal)*. Jamais `ultra` ni `off` automatiquement.
- **Verrou indépendant :** `caveman_locked` est indépendant de `locked` (Axe 1).
- **Matrice ROI par cible (`get_active_preset()`) :**
  - **Session principale :** niveau actif (`off`/`lite`/`full`/`ultra`), avec **auto-clarté obligatoire** sur alertes/refus `PreToolUse`, confirmations irréversibles et `/grill-me` (`ask_question`).
  - **`planner` (`caveman_planner`) :** plafonné à `lite`/`full` (`ultra` rabattu vers `full`).
  - **`reviewer` indépendant (`caveman_reviewer`) :** format compact sur 1 ligne conservant strictement *Preuve ou Silence*.
  - **`builder` (`caveman_builder`) & `swarm` :** zéro narration entre outils, statuts secs.
  - **`evals/` (`caveman_evals`) :** toujours `"off"` *(exclu pour préserver les assertions déterministes)*.
