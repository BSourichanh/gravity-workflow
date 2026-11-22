---
name: gravity-workflow
description: Chef d'orchestre unifié Gravity Workflow à 3 axes : mode (low, medium, high, ultra, evo), workflow (slow, fast) et vérification (safe, unsafe).
---

# Gravity Workflow — Ground your Antigravity agents

*(Skill principal unifié invoqué via `/gravity-workflow` ou `/gravity-workflow:gravity-workflow`, accompagné du skill compagnon `/caveman` pour la concision.)*

## Architecture : Gravity Workflow indépendant du projet + Init vital via `/gravity-workflow`
- **Moteur indépendant dans le plugin (`plugin/`)** : skill principal (`/gravity-workflow`) + skill compagnon (`/caveman`), sous-agents (`agents/planner.md`, `builder.md`, `reviewer.md`), garde-fous & session (`hooks.json` + `hooks/*.py`), workflows (`workflows/review_pipeline.py`), moteur d'évaluations (`evals/run_suite.py`, `evals/eval_checks.py`), profil adaptatif, protocole Caveman & observabilité (`observability/profile_engine.py`, `caveman_protocol.py`, `state_writer.py`, `statusline.py`) et scripts (`scripts/init_workspace.py`, `scripts/validate.py`).
- **Socle vital dans le projet cible (`AGENTS.md` + `.agents/`)** : `/gravity-workflow` initialise et maintient uniquement les fichiers propres au projet (`AGENTS.md`, `.agents/rules/*.md`, `.agents/mcp_config.json` et `.agents/gravity-workflow/{PROGRESS.md, BACKLOG.md, profile.json, evals/cases.json, observability/live_state.json}`), sans créer de dossier visible `gravity-workflow/` à la racine du projet.

## Contraintes absolues d'interaction
- **Garde-fous `PreToolUse` fail-closed toujours ON :** quel que soit le palier (`low`, `medium`, `high`, `ultra`), la protection des secrets, le blocage des commandes destructrices et l'auto-protection du harnais restent actifs sans aucun relâchement.
- **Zéro jargon mécanique :** n'emploie jamais de numérotation artificielle dans tes échanges avec l'utilisateur ; utilise uniquement les noms de paliers et d'étapes métier.
- **Anti-écrasement :** n'écrase jamais un fichier existant sans présenter d'abord un diff explicite et obtenir la validation de l'utilisateur.
- **Conventions Git & versioning (chef d'orchestre uniquement) :** avant tout `git commit`, `git push`, tag ou release (toujours après demande ou accord explicite de l'utilisateur), le chef d'orchestre applique `.agents/rules/05-git-versioning.md` (branches hors `main`/`master`, vérifications/tests selon `safe`/`unsafe`, *Conventional Commits*, SemVer + `CHANGELOG.md`, tags annotés `vX.Y.Z`). Cette règle ne s'applique **jamais** aux sous-agents (`planner`, `builder`, `reviewer`) ni aux workers `swarm`.
- **Budget des règles actives (`defaultRulesBudget`) :** maintiens `AGENTS.md`/`GEMINI.md` + règles globales + règles `trigger: always_on` strictement sous **20 000 tokens agrégés** et chaque fichier de règle sous **24 000 octets**.
- **Slash commands vs outils natifs :** un agent ne peut pas invoquer lui-même de slash commands (`/run-workflow`, `/swarm`, `/boost`, `/teamwork`, `/learn`, `/grill-me`, `/generative_ui`). Appelle directement les outils natifs (`run_workflow`, `swarm`, `ask_question`, `invoke_subagent`, lecture de la skill `generative_ui`) ou recommande le slash command à l'utilisateur lorsqu'une action humaine ou UI dédiée est requise.
- **Exécution des hooks & scripts du plugin :** détecte l'interpréteur Python disponible dans le `PATH` (`python3`, `python` ou `py -3`) sous Windows comme sous POSIX. Ne modifie jamais `.agents/gravity-workflow/profile.json` à la main : passe toujours par `observability/profile_engine.py`.

---

## 1. Grammaire d'invocation & routage unifié (3 axes orthogonaux)

Analyse les **0, 1, 2 ou 3 mots-clés positionnels** après `/gravity-workflow` *(sans flag `--mode`, dans n'importe quel ordre)*, ou une sous-commande utilitaire (`status`, `evals`, `init`) :
1. **Axe 1 — Mode / Réflexion (`mode`) :** `low`, `medium`, `high`, `ultra`, `evo` *(alias `evolutive`)* — défaut : **`evo` au palier `medium`**. Fixe la profondeur de réflexion et les modèles.
2. **Axe 2 — Stratégie Workflow / Temps (`pace`) :** `slow` *(par responsabilité)* ou `fast` *(optimisé temps)* — défaut : **`slow`**. **N'impacte jamais le niveau de réflexion ni les modèles.**
3. **Axe 3 — Vérification & Tests (`verification`) :** `safe` *(actif)* ou `unsafe` *(désactivé)* — défaut : **`safe`**. Active ou désactive toutes les procédures de test et de vérification du workflow.

| Syntaxe | Action sur le profil (`.agents/gravity-workflow/profile.json`) | Comportement exécuté |
| :--- | :--- | :--- |
| **`/gravity-workflow`** *(seul)* | Initialise `evo` (`medium`, `slow`, `safe`) si absent ; sinon conserve le profil | Détection d'état (Init / Audit / Reprise) + micro-carte 3 lignes incluant les 3 axes et le motif `evo` |
| **`/gravity-workflow <tâche>`** | Conserve les 3 axes du profil (défaut **`evo/medium`**, **`slow`**, **`safe`**) | Vérifie/initialise le socle si besoin, puis exécute `<tâche>` |
| **`/gravity-workflow low\|medium\|high\|ultra [<tâche>]`** | `profile_engine.py set --workspace . --mode <palier>` → **verrouille** (`locked: true`) sans changer `pace` ni `verification` | Confirme le verrouillage sur ce palier, puis exécute `<tâche>` (ou micro-carte) |
| **`/gravity-workflow evo [<tâche>]`** *(alias : `evolutive`)* | `profile_engine.py set --workspace . --mode evo` → **déverrouille** (`locked: false`, garde le palier courant) | Confirme la reprise du mode évolutif, puis exécute `<tâche>` |
| **`/gravity-workflow fast\|slow [<tâche>]`** | `profile_engine.py set --workspace . --pace <fast\|slow>` → met à jour uniquement la stratégie temps | `fast` optimise le workflow en temps (parallélisation/raccourcis), réflexion & tests conservés ; `slow` rétablit la stratégie par responsabilité |
| **`/gravity-workflow unsafe\|safe [<tâche>]`** | `profile_engine.py set --workspace . --verify <unsafe\|safe>` → met à jour uniquement la vérification | `unsafe` coupe toute procédure de test et de vérification du workflow ; `safe` les rétablit |
| **`/gravity-workflow <combinaison 2 ou 3 axes> [<tâche>]`** | `profile_engine.py set --workspace . [--mode ...] [--pace ...] [--verify ...]` | Applique atomiquement les axes donnés dans n'importe quel ordre (ex. `/gravity-workflow high fast`, `/gravity-workflow fast unsafe`, `/gravity-workflow high fast unsafe`, `/gravity-workflow evo slow safe`) |
| **`/gravity-workflow status`** | Lecture seule (`profile_engine.py get --workspace . --json`) | Affiche le bilan complet en **4 blocs** (voir §5) |
| **`/gravity-workflow evals [CASE-ID...]`** | Lecture seule | Lance `run_workflow` sur `evals/run_suite.py` (voir §6) |
| **`/gravity-workflow init [quickstart\|full]`** | Initialise `profile.json` (`evo` / `medium` / `slow` / `safe`) avec le socle | Exécute `init_workspace.py --workspace . --mode <quickstart\|full>` sans écraser l'existant |

> **Règle de parsing anti-ambiguïté :**
> - Correspondance **mot entier uniquement** en tête de commande (`fast-path` ou `low-latency` ne sont pas interprétés comme `fast` ou `low`).
> - `status`, `evals` et `init` ne déclenchent une sous-commande que s'ils sont seuls ou suivis uniquement de leurs arguments dédiés (`CASE-ID...` pour `evals`, `quickstart|full` pour `init`) — sinon l'entrée est traitée comme `<tâche>` (ex. `/gravity-workflow status de la PR` ou `/gravity-workflow init le fichier config`).
> - Une invocation explicite `/gravity-workflow evals` s'exécute toujours même si le profil est en `unsafe` (`unsafe` coupe uniquement les vérifications et tests automatiques du workflow).
> - **Concision Caveman (`/caveman`) :** la grammaire `/gravity-workflow` reste strictement à 3 axes (zéro collision sur `ultra`). Le skill compagnon **`/caveman [lite|full|ultra|off|auto]`** (plus `commit`, `compress`, `status`) pilote `.agents/gravity-workflow/profile.json` (`caveman`, défaut `lite`, piloté automatiquement par `evo` tant que `caveman_locked: false`, propagé par ROI aux sous-agents / `swarm`, avec `evals/` strictement exclu).

### Stratégie de workflow : `slow` (par responsabilité) vs `fast` (optimisé temps, réflexion inchangée)
- **`slow` (par défaut) :** sélectionne le moteur par responsabilité (`Orchestrator`/`/plan` sur tâche ciblée, boucle séquentielle complète `Planner → Builder → Reviewer` sur `review_pipeline.py`, `swarm` sur chantiers multi-modules).
- **`fast` :** optimise exclusivement le **temps d'exécution** sans jamais réduire le niveau de réflexion ni les modèles du palier actif — parallélise agressivement (`swarm` / `parallel()`) dès >= 2 sous-tâches indépendantes, condense les étapes séquentielles redondantes (ignore l'étape `Planner` séparée lorsque la consigne est déjà cadrée), et plafonne les boucles de remédiation séquentielles à 1 passe max. **Ne désactive ni les tests ni les vérifications** (qui relèvent exclusivement de `safe`/`unsafe`).

### Vérification & Tests : `safe` (par défaut) vs `unsafe`
- **`safe` (par défaut) :** toutes les procédures de vérification et de test du workflow sont actives selon le palier (revue indépendante `review_pipeline.py`, remédiation, écriture/exécution de tests unitaires, `/gravity-workflow evals`, `validate.py`, auto-formatage `PostToolUse`).
- **`unsafe` :** désactive **toute** procédure de test et de vérification du workflow (aucune revue indépendante, aucune boucle de remédiation, aucun test unitaire écrit ni lancé, aucun `/gravity-workflow evals` ni `validate.py` post-tâche, et formatage `PostToolUse` ignoré). Les garde-fous de sécurité `PreToolUse` fail-closed (`01-safety-boundaries.md`) restent **toujours actifs**.

### Cadrage & arbitrage avant exécution (protocole `/grill-me`)
- Sur toute tâche ambiguë, multi-options ou structurante, **explore d'abord le dépôt** (ne pose jamais une question dont le code détient déjà la réponse) avant toute écriture ou lancement de `review_pipeline.py` / `swarm`.
- S'il reste des décisions de design ou de périmètre ouvertes : clarifie **une question à la fois via `ask_question`** (option recommandée en premier préfixée par `(Recommended)`), ou recommande à l'utilisateur de lancer **`/grill-me`** (souvent combiné en **`/plan /grill-me`**) pour verrouiller le plan avant implémentation.
- Consigne tout arbitrage différé ou idée hors périmètre dans `.agents/gravity-workflow/BACKLOG.md`.
- **Interaction avec les axes :** en stratégie **`fast`**, limite-toi aux seules ambiguïtés strictement bloquantes ; en paliers **`high` / `ultra`** (ou avant un `swarm` / `/teamwork`), recommande explicitement `/plan /grill-me` lorsque l'architecture ou le périmètre restent ouverts.

### Lecture et mise à jour du profil via CLI (`profile_engine.py`)
```bash
# Lire le profil actif, la stratégie temps, la vérification et les modèles résolus :
python3 <plugin_root>/observability/profile_engine.py get --workspace . --json

# Mettre à jour un ou plusieurs axes :
python3 <plugin_root>/observability/profile_engine.py set --workspace . [--mode <mode>] [--pace <slow|fast>] [--verify <safe|unsafe>] --json
```
*(Utiliser `python` ou `py -3` sous Windows si `python3` est absent du `PATH`.)*

---

## 2. Matrice des 4 paliers (`low` / `medium` / `high` / `ultra`) & attribution des modèles

> Le mode **`evo`** démarre par défaut au palier **`medium`**. À la fin de chaque session (`Stop`), `stop_session_logger.py` appelle `profile_engine.evolve_on_stop()` qui recalcule un score déterministe (0–100 : taille projet, tests/CI, règles, alertes `PreToolUse`, évals, erreurs, série propre) et ajuste le palier de **±1 niveau max par session** (avec hystérésis ±5 pts). Lorsqu'un mode fixe est verrouillé, `current_level` reste figé et seul `suggested_level` est recalculé à titre indicatif.

| Dimension / Rôle | `low` | `medium` *(défaut `evo`)* | `high` | `ultra` |
| :--- | :--- | :--- | :--- | :--- |
| **Garde-fous `PreToolUse`** | **ON (fail-closed)** | **ON (fail-closed)** | **ON (fail-closed)** | **ON (fail-closed)** |
| **Orchestration** | Direct mono-fil (économe) | `Orchestrator` / `/plan` (+ `/grill-me` si ambigu) + [`review_pipeline.py`](../../workflows/review_pipeline.py) (`1` tour) sur modifs significatives | `/plan /grill-me` si périmètre ouvert + `review_pipeline.py` complet (`2` tours) ou `swarm` si multi-modules | `/plan /grill-me` si structurant + `swarm` (`@verifier` cross-model), recommandation `/boost` ou `/teamwork` |
| **Session principale** *(conseil UI)* | `3.8 Flash` / `Hydrogen` (`/effort low`) | `Orchestrator` (`/effort medium`) | `Orchestrator` / `Neon` (`/effort high`) | `Orchestrator` `Neon`/`Carbon` (`/effort xhigh`) |
| **`planner`** (`review_pipeline` / sous-agent) | *(non lancé ; `flash_lite` si forcé)* | **`inherit`** | **`pro`** *(repli `inherit` si `allow_pro: false`)* | **`pro`** |
| **`builder`** (`review_pipeline` / sous-agent) | **`flash_lite`** *(si forcé ; sinon direct)* | **`flash`** | **`flash`** | **`pro`** *(ou `flash` sur lots simples)* |
| **`reviewer` indépendant** (`review_pipeline`) | *(non lancé ; `flash` si forcé)* | **`pro`** *(repli `inherit` sur Free/Plus)* | **`pro`** | **`pro`** |
| **`max_remediation_rounds`** | `0` | `1` | `2` | `2` (+ escalade `/boost`) |
| **`evals` (`candidate` / `judge`)** | `flash_lite` / `flash` *(à la demande)* | `flash` / `inherit` *(ciblés si règles modifiées)* | `flash` / `pro` *(ciblés sur cas impactés)* | `flash` / `pro` *(suite complète `12/12` + `/learn`)* |
| **`swarm` (`workers` / `@verifier`)** | *(non utilisé)* | *(non utilisé)* | Workers rapides · `@verifier` frontier (`Neon`) | Workers rapides/frontier · `@verifier` **cross-model** (`swarm(action="models")`) |
| **Init projet vierge** | `quickstart` | `quickstart` ou `full` | `full` | `full` + `validate.py --strict` |

**Règles de compatibilité & surcharge :**
- `review_pipeline.py` et `run_suite.py` lisent automatiquement `profile_engine.get_active_preset()` ; les `Args` explicites restent prioritaires.
- Les stratégies workflow (`slow` / `fast`) n'altèrent jamais les modèles ni la réflexion du palier ; seul `unsafe` désactive la revue indépendante, les tests et les évaluations.
- Un projet peut définir `"allow_pro": false` (plan Free/Plus sans tier `pro`, rabattant tout `pro` vers `inherit`) ou `"model_overrides": {...}` dans `.agents/gravity-workflow/profile.json`.

---

## 3. Détection automatique d'état & Initialisation

> **Déploiement automatique de `.agents/rules/05-git-versioning.md` :** à **chaque** invocation `/gravity-workflow` (Reprise de session, Mode Audit ou Démarrage à froid `quickstart`/`full`), vérifie si `.agents/rules/05-git-versioning.md` existe dans le workspace cible. S'il est absent, crée-le automatiquement sans écraser aucun fichier existant via `python3 <plugin_root>/scripts/init_workspace.py --workspace . --ensure-git-rule` (ou via `--mode <quickstart|full>` lors d'une initialisation/audit).

Inspecte le workspace du projet courant avant toute question :
1. **Reprise de session** (`.agents/gravity-workflow/PROGRESS.md` présent) : lis `PROGRESS.md` et `.agents/gravity-workflow/profile.json`, vérifie la présence de `.agents/rules/05-git-versioning.md` (sinon exécute `init_workspace.py --workspace . --ensure-git-rule`), affiche la **micro-carte 3 lignes** et, en présence d'ambiguïtés structurantes dans `<tâche>`, applique d'abord le protocole `/grill-me` (`ask_question` une question à la fois ou recommandation `/plan /grill-me`) avant d'exécuter la tâche demandée (ou propose l'étape suivante, `/gravity-workflow status` ou `/gravity-workflow evals`).
2. **Mode Audit** (`.agents/` ou `AGENTS.md` présent sans `.agents/gravity-workflow/PROGRESS.md`) : analyse `AGENTS.md`, `.agents/rules/` (y compris présence de `05-git-versioning.md`) et `.agents/mcp_config.json` (validité `trigger:`, budget `< 20 000` tokens), produis une cartographie de conformité et initialise `.agents/gravity-workflow/` + `05-git-versioning.md` si absent via [init_workspace.py](../../scripts/init_workspace.py) sans écraser l'existant.
3. **Démarrage à froid** (ni `.agents/gravity-workflow/` ni `AGENTS.md`) :
   - En palier `low` → initialise directement en `--mode quickstart` (inclut `.agents/rules/05-git-versioning.md`).
   - En palier `high` / `ultra` → initialise directement en `--mode full` (inclut `.agents/rules/05-git-versioning.md`).
   - En palier `medium` → propose **Démarrage rapide (`quickstart`)** (`references/00-quick-start.md`) ou **Parcours Guidé complet (`full`)** en 8 étapes métier avec sauvegarde dans `.agents/gravity-workflow/PROGRESS.md` (tous deux déploient `.agents/rules/05-git-versioning.md`).

---

## 4. Micro-carte obligatoire (3 lignes)

```text
Gravity Workflow [X/8] · Mode : evo → <palier> (ou <palier> [verrouillé]) · Workflow : <slow|fast> · Vérif : <safe|unsafe> · Étape : <Nom métier>
Santé : Règles <OK/WARN> · Guard Fail-Closed <ON/OFF> · Caveman : <niveau> (auto|verrouillé) · Evals <pass/total>
Action / Évolution : <Dernier motif evo si adapté | Prochaine action concrète>
```

---

## 5. Procédure `/gravity-workflow status` (Bilan en 4 blocs)

Lis `.agents/gravity-workflow/PROGRESS.md`, `.agents/gravity-workflow/profile.json`, `.agents/rules/`, `.agents/gravity-workflow/evals/report.json` et `.agents/gravity-workflow/observability/runs.jsonl` (`conversation_id`, `model`, `termination_reason`, `fully_idle`), puis restitue 4 blocs structurés :
- Dans **Antigravity App** : lis d'abord la skill intégrée `generative_ui` et produis un artifact HTML autonome (`ArtifactMetadata` avec `UserFacing: true`, `RequestFeedback: false`, plus `<agent-embed>` si compact `<= 450px`).
- Dans **CLI `agy` / IDE** : affiche directement les 4 blocs en Markdown.

Blocs obligatoires :
1. **Avancement & Mode actif** : étapes cochées sur 8 (hors Bonus), mode/palier courant (`evo → <palier>` ou `<palier> [verrouillé]`), stratégie workflow (`slow` ou `fast`), vérification (`safe` ou `unsafe`), niveau **Caveman** et son verrou (`auto` ou `verrouillé`), `suggested_level` et `last_reason` du profil.
2. **Santé des règles & sécurité** : budget actif estimé (`< 20 000` tokens agrégés), `trigger: always_on` valide et taille `< 2 Ko` pour `01-safety-boundaries.md`, présence de `.agents/rules/05-git-versioning.md` (conventions Git & versioning chef d'orchestre), état des hooks fail-closed du plugin.
3. **Métriques d'évaluations** : dernier score `PASS/TOTAL`, rejets pré-filtre et items `[manual]` restants.
4. **Recommandations** : 1 à 3 actions concrètes prioritaires.

---

## 6. Procédure `/gravity-workflow evals` (Suite d'évaluations parallèle)

1. Appelle directement l'outil natif **`run_workflow`** avec le chemin absolu du script du plugin [run_suite.py](../../evals/run_suite.py) (`Description="Suite d'évaluations Gravity Workflow"` ; optionnellement `Args={"case_ids": [...]}` ou surcharges `candidate_model` / `judge_model` / `level`). Sans surcharge explicite, `run_suite.py` utilise les modèles du palier actif (`profile_engine.get_active_preset`).
2. Vérifie la double sortie générée dans le projet cible (`.agents/gravity-workflow/evals/report.md` et `.agents/gravity-workflow/evals/report.json`).
3. Affiche le score automatique suivi de la checklist `[manual]` à arbitrer par l'utilisateur.
4. En cas de régression ou correction en session, invite l'utilisateur à lancer `/learn` (ou propose un diff de règle ciblée) et ajoute un cas de non-régression dans `.agents/gravity-workflow/evals/cases.json`.

---

## 7. Chargement progressif des fiches de référence (`references/`)

Charge uniquement la fiche correspondant à l'étape ou au besoin en cours :
- Démarrage rapide -> `references/00-quick-start.md`
- Cadrage du projet -> `references/01-project-scoping.md`
- Socle & règles -> `references/02-charter-and-rules.md`
- Équipe & Reviewer indépendant -> `references/03-subagents-architecture.md`
- Outils & connexions -> `references/04-tools-and-mcp.md`
- Routeur & workflows -> `references/05-workflows-and-routing.md`
- Garde-fous automatiques -> `references/06-safety-hooks.md`
- Tests & évaluations -> `references/07-eval-suite.md`
- Suivi, adaptation `evo` & `/learn` -> `references/08-session-learning.md`
- Tableau de bord (Bonus) -> `references/09-observability-dashboard.md`
