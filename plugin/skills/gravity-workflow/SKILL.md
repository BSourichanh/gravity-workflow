---
name: gravity-workflow
description: Chef d'orchestre unifié Gravity Workflow : 3 profils (quick, standard, deep), mode consultatif evo, vérification éphémère (--no-verify) et /caveman.
---

# Gravity Workflow — Ground your Antigravity agents

*(Skill principal unifié invoqué via `/gravity-workflow` ou `/gravity-workflow:gravity-workflow`, accompagné du skill compagnon `/caveman` pour la concision.)*

## Architecture : Stockage centralisé + Init projet optionnel
- **Moteur autonome dans le plugin (`plugin/`)** : skill principal (`/gravity-workflow`) + skill compagnon (`/caveman`), sous-agents (`agents/planner.md`, `builder.md`, `reviewer.md`), garde-fous & session (`hooks.json` + `hooks/*.py`), workflows (`workflows/review_pipeline.py`), moteur d'évaluations (`evals/run_suite.py`, `evals/eval_checks.py`), profil consultatif, protocole Caveman & observabilité (`observability/profile_engine.py`, `caveman_protocol.py`, `state_writer.py`, `statusline.py`) et scripts (`scripts/init_workspace.py`, `scripts/validate.py`).
- **Cycle de vie actif partout, zéro écriture cible par défaut :** `SessionStart`, `Stop`, `Live` et `profile_engine` fonctionnent sur tout dépôt sans nécessiter `/gravity-workflow init`. Tout l'état runtime (`profile.json`, `live_state.json`, `runs.jsonl`, `last_verified.patch`, `evals/report.*`) est centralisé sous **`$GRAVITY_WORKFLOW_HOME`** (`$GRAVITY_HOME/workflow` ou `~/.gemini/gravity/workflow/`) dans `workspaces/<slug>-<sha256_8>/` (repli lecture seule sur `<ws>/.agents/gravity-workflow/profile.json` si présent). `.agents/` reste optionnel pour les règles et `evals/cases.json` déployés sur demande via `/gravity-workflow init`.

## Contraintes absolues d'interaction
- **Garde-fous `PreToolUse` fail-closed toujours ON :** quel que soit le profil (`quick`, `standard`, `deep`) ou l'option éphémère `unsafe` / `--no-verify`, la protection des secrets, le blocage des commandes destructrices et l'auto-protection du harnais restent actifs.
- **Zéro jargon mécanique :** n'emploie jamais de numérotation artificielle dans tes échanges avec l'utilisateur ; utilise uniquement les noms de profils et d'étapes métier.
- **Anti-écrasement :** n'écrase jamais un fichier existant sans présenter d'abord un diff explicite et obtenir la validation de l'utilisateur.
- **Conventions Git & versioning (chef d'orchestre uniquement) :** avant tout `git commit`, `git push`, tag ou release (toujours après demande ou accord explicite de l'utilisateur), le chef d'orchestre applique `.agents/rules/05-git-versioning.md` (branches hors `main`/`master`, vérifications/tests, *Conventional Commits*, SemVer + `CHANGELOG.md`, tags annotés `vX.Y.Z`). Cette règle ne s'applique **jamais** aux sous-agents (`planner`, `builder`, `reviewer`) ni aux workers `swarm`.
- **Budget des règles actives (`defaultRulesBudget`) :** maintiens `AGENTS.md`/`GEMINI.md` + règles globales + règles `trigger: always_on` strictement sous **20 000 tokens agrégés** et chaque fichier de règle sous **24 000 octets**.
- **Slash commands vs outils natifs :** un agent ne peut pas invoquer lui-même de slash commands (`/run-workflow`, `/swarm`, `/boost`, `/teamwork`, `/learn`, `/grill-me`, `/generative_ui`). Appelle directement les outils natifs (`run_workflow`, `swarm`, `ask_question`, `invoke_subagent`, lecture de la skill `generative_ui`) ou recommande le slash command à l'utilisateur lorsqu'une action humaine ou UI dédiée est requise.
- **Exécution des hooks & scripts du plugin :** détecte l'interpréteur Python disponible dans le `PATH` (`python3`, `python` ou `py -3`) sous Windows comme sous POSIX. Ne modifie jamais `profile.json` à la main : passe toujours par `observability/profile_engine.py`.

---

## 1. Grammaire d'invocation & routage unifié

Analyse les mots-clés positionnels en tête après `/gravity-workflow` *(dans n'importe quel ordre)*, ou une sous-commande utilitaire (`status`, `evals`, `init`) :
1. **Profil d'exécution (`mode`) :** `quick`, `standard` *(défaut)*, `deep`, `evo` *(alias `evolutive`, mode consultatif)* — alias rétrocompatibles tolérés : `low→quick`, `medium→standard`, `high/ultra→deep` (les anciens mots-clés `slow`/`fast` sont acceptés sans erreur et ignorés).
2. **Vérification éphémère (`unsafe` / `--no-verify`) :** applicable **uniquement à 1 tâche ou 1 invocation `run_workflow`**, **jamais persistée** dans `profile.json` (`safe` reste toujours l'état de base).

| Syntaxe | Action sur le profil centralisé (`profile.json`) | Comportement exécuté |
| :--- | :--- | :--- |
| **`/gravity-workflow`** *(seul)* | Conserve le profil actif (`evo` / `standard` par défaut) | Détection d'état + micro-carte 3 lignes incluant `suggested_profile` et le motif `evo` |
| **`/gravity-workflow <tâche>`** | Conserve le profil actif (défaut `standard`) | Exécute `<tâche>` selon le profil actif |
| **`/gravity-workflow quick\|standard\|deep [<tâche>]`** | `profile_engine.py set --workspace . --mode <profil>` → **verrouille** (`locked: true`) | Confirme le verrouillage sur ce profil, puis exécute `<tâche>` |
| **`/gravity-workflow evo [<tâche>]`** *(alias : `evolutive`)* | `profile_engine.py set --workspace . --mode evo` → **mode consultatif** (`locked: false`) | Conserve le profil actif et expose `suggested_profile` + motif à chaque `Stop` |
| **`/gravity-workflow unsafe\|--no-verify <tâche>`** | **Aucune écriture dans `profile.json`** (strictement éphémère) | Désactive revue indépendante / tests pour **cette tâche uniquement** (`PreToolUse` reste ON) |
| **`/gravity-workflow status`** | Lecture seule (`profile_engine.py get --workspace . --json`) | Affiche le bilan complet en **4 blocs** (voir §5) |
| **`/gravity-workflow evals [CASE-ID...]`** | Lecture seule | Lance `run_workflow` sur `evals/run_suite.py` (voir §6) |
| **`/gravity-workflow init [quickstart\|full]`** | Optionnel : déploie le socle `.agents/` + `AGENTS.md` | Exécute `init_workspace.py --workspace . --mode <quickstart\|full>` sans écraser l'existant |

> **Règle de parsing anti-ambiguïté :**
> - Correspondance **mot entier uniquement** en tête de commande (`quick-fix` ou `deep-link` ne sont pas interprétés comme `quick` ou `deep`).
> - `status`, `evals` et `init` ne déclenchent une sous-commande que s'ils sont seuls ou suivis uniquement de leurs arguments dédiés (`CASE-ID...` pour `evals`, `quickstart|full` pour `init`) — sinon l'entrée est traitée comme `<tâche>`.
> - **Concision Caveman (`/caveman`) :** le skill compagnon **`/caveman [lite|full|ultra|off|auto]`** (plus `commit`, `compress`, `status`) pilote `caveman` dans le profil centralisé (défaut **`lite`** sur les 3 profils, `evals/` strictement exclu).

### Cadrage & arbitrage avant exécution (protocole `/grill-me`)
- Sur toute tâche ambiguë, multi-options ou structurante, **explore d'abord le dépôt** (ne pose jamais une question dont le code détient déjà la réponse) avant toute écriture ou lancement de `review_pipeline.py` / `swarm`.
- S'il reste des décisions de design ou de périmètre ouvertes : clarifie **une question à la fois via `ask_question`** (option recommandée en premier préfixée par `(Recommended)`), ou recommande à l'utilisateur de lancer **`/plan /grill-me`** pour verrouiller le plan avant implémentation.

### Lecture et mise à jour du profil via CLI (`profile_engine.py`)
```bash
# Lire le profil actif, la suggestion evo et les modèles résolus :
python3 <plugin_root>/observability/profile_engine.py get --workspace . --json

# Mettre à jour le mode, caveman ou des surcharges modèles A/B :
python3 <plugin_root>/observability/profile_engine.py set --workspace . [--mode <quick|standard|deep|evo>] [--caveman <level>] [--plan-model <tier>] [--build-model <tier>] [--review-model <tier>] --json
```
*(Utiliser `python` ou `py -3` sous Windows si `python3` est absent du `PATH`.)*

---

## 2. Matrice des 3 profils (`quick` / `standard` / `deep`) & attribution des modèles

> En mode **`evo` consultatif**, `stop_session_logger.py` appelle `profile_engine.evolve_on_stop()` en fin de session (`Stop`) sur tous les dépôts. Le moteur évalue uniquement les signaux comportementaux (**rejets `Reviewer`, erreurs outils/tests, `guard_deny_recent`, `eval_pass_rate`** — sans comptage de fichiers `< 80` ni rétrogradation vers `flash_lite`) et expose **`suggested_profile` (`standard` | `deep`) + motif** dans `status` et `live_state.json` **sans jamais muter silencieusement le profil actif**.

| Dimension / Rôle | `quick` *(alias `low`)* | `standard` *(défaut, alias `medium`)* | `deep` *(alias `high`/`ultra`)* |
| :--- | :--- | :--- | :--- |
| **Garde-fous `PreToolUse`** | **ON (fail-closed)** | **ON (fail-closed)** | **ON (fail-closed)** |
| **Orchestration** | Exécution directe mono-fil (zéro `review_pipeline` par défaut) | Direct sur tâche triviale ; `review_pipeline.py` sur lot significatif | `review_pipeline.py` complet (`2` remédiations) ; suggestion `/plan /grill-me`, `/boost` ou `/swarm` à l'initiative utilisateur |
| **`planner`** (`review_pipeline`) | *(sauté ; `flash` si forcé)* | **`flash`** *(sauté automatiquement si `args["plan"]` fourni)* | **`pro`** *(repli `flash` si `allow_pro: false` ; sauté si `args["plan"]` fourni)* |
| **`builder`** (`review_pipeline`) | **`flash`** *(plus de `flash_lite` imposé)* | **`flash`** | **`flash`** *(`pro` sur override explicite `--build-model pro`)* |
| **`reviewer` indépendant** | *(sauté ; `flash` si forcé)* | **`pro`** *(repli `flash` si `allow_pro: false`)* | **`pro`** *(repli `flash` si `allow_pro: false`)* |
| **`max_remediation_rounds`** | `0` *(ou `0–1` si forcé)* | `1` | `2` |
| **`evals` (`candidate` / `judge`)** | `flash` / `flash` | `flash` / `pro` *(repli `flash`)* | `flash` / `pro` *(repli `flash`)* |
| **`caveman` défaut** | `lite` | `lite` | `lite` |

**Règles de surcharge & essais A/B :**
- `review_pipeline.py` et `run_suite.py` lisent automatiquement `profile_engine.get_active_preset()` ; les `Args` explicites (`level`, `plan`, `no_verify`, `plan_model`, `build_model`, `review_model`, `plugin_root`) restent prioritaires.
- Lorsque `args.get("plan")` est fourni non vide, `review_pipeline.py` saute le `Planner` séparé même en `standard` ou `deep` (sauf override `skip_separate_planner=False`).
- Un projet peut définir `"allow_pro": false` (rabattant tout modèle `pro` vers `flash`) ou `"model_overrides"` via `--plan-model`, `--build-model`, `--review-model` (`inherit` | `flash_lite` | `flash` | `pro`). Chaque exécution journalise `preset_profile` et `role_models` effectifs dans `runs.jsonl` et `live_state.json`.

---

## 3. Détection d'état & Initialisation optionnelle

> **Déploiement optionnel de `.agents/rules/05-git-versioning.md` :** uniquement si `.agents/` existe déjà ou lors d'un `/gravity-workflow init`, vérifie si `.agents/rules/05-git-versioning.md` est présent et crée-le sans écraser l'existant via `init_workspace.py --workspace . --ensure-git-rule`. Sur un dépôt sans `.agents/`, ne crée aucun fichier dans le workspace.

1. **Reprise sur dépôt équipé** (`.agents/gravity-workflow/PROGRESS.md` présent) : lis `PROGRESS.md` et `profile_engine.py get`, affiche la micro-carte 3 lignes et exécute la tâche.
2. **Mode Audit** (`.agents/` ou `AGENTS.md` présent sans `PROGRESS.md`) : contrôle `trigger:` et le budget `< 20 000` tokens, puis complète uniquement sur demande utilisateur.
3. **Dépôt nu (sans `.agents/`)** : exécute directement la tâche avec le stockage centralisé (`$GRAVITY_HOME/workflow/workspaces/<hash>/`) sans créer de fichiers dans le dépôt cible (propose `/gravity-workflow init` uniquement si l'utilisateur souhaite des règles ou `evals/cases.json` locaux).

---

## 4. Micro-carte obligatoire (3 lignes)

```text
Gravity Workflow [X/8 ou centralisé] · Profil : <quick|standard|deep> (evo|verrouillé) · Vérif : <safe|no-verify éphémère> · Étape : <Nom métier>
Santé : Règles <OK/N-A> · Guard Fail-Closed <ON/OFF> · Caveman : <niveau> (auto|verrouillé) · Evals <pass/total|N/A>
Suggestion evo : <standard|deep> (<motif comportemental>) · Prochaine action : <Action concrète>
```

---

## 5. Procédure `/gravity-workflow status` (Bilan en 4 blocs)

Exécute `profile_engine.py get --workspace . --json` et lis le stockage centralisé (`profile.json`, `live_state.json`, `runs.jsonl`, `evals/report.json`) ainsi que `.agents/` si présent, puis restitue 4 blocs structurés :
- Dans **Antigravity App** : lis d'abord la skill intégrée `generative_ui` et produis un artifact HTML autonome (`ArtifactMetadata` avec `UserFacing: true`, `RequestFeedback: false`, plus `<agent-embed>` si compact `<= 450px`).
- Dans **CLI `agy` / IDE** : affiche directement les 4 blocs en Markdown.

Blocs obligatoires :
1. **Avancement & Profil actif** : mode (`evo` consultatif ou verrouillé), profil actif (`quick` | `standard` | `deep`), `role_models` effectifs (`plan`, `build`, `review`), niveau **Caveman** (`auto` ou `verrouillé`), et **`suggested_profile` (`standard` | `deep`) + motif**.
2. **Santé des règles & sécurité** : si `.agents/` existe, budget actif estimé (`< 20 000` tokens), `01-safety-boundaries.md` (`trigger: always_on`, `< 2 Ko`), `05-git-versioning.md`, état des hooks fail-closed du plugin.
3. **Métriques d'évaluations & runs** : dernier score `PASS/TOTAL`, derniers runs `runs.jsonl` (`preset_profile`, `role_models`, `verdict`).
4. **Recommandations** : 1 à 3 actions concrètes prioritaires (incluant la suggestion `evo` le cas échéant).

---

## 6. Procédure `/gravity-workflow evals` (Suite d'évaluations parallèle)

1. Appelle directement l'outil natif **`run_workflow`** avec le chemin absolu du script du plugin [run_suite.py](../../evals/run_suite.py) (`Description="Suite d'évaluations Gravity Workflow"` ; optionnellement `Args={"case_ids": [...], "plugin_root": "..."}` ou surcharges `candidate_model` / `judge_model` / `level`).
2. Vérifie les rapports consolidés dans le dossier centralisé (`$GRAVITY_HOME/workflow/workspaces/<hash>/evals/report.md` et `report.json`).
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
