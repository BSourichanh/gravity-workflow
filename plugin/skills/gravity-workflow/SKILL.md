---
name: gravity-workflow
description: Chef d'orchestre unifié Gravity Workflow : 3 profils (quick, standard, deep), mode consultatif evo, vérification éphémère (--no-verify) et /caveman.
---

# Gravity Workflow — Ground your Antigravity agents

*(Skill `/gravity-workflow` ou `/gravity-workflow:gravity-workflow` ; compagnon `/caveman`.)*

## Contraintes absolues
- **Stockage centralisé :** hooks, `Live`, `profile_engine.py` sous `$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/workspaces/<slug>-<sha256_8>/` (repli RO `.agents/gravity-workflow/profile.json`). `.agents/` optionnel (`init`).
- **`PreToolUse` fail-closed ON :** secrets, destructeurs, auto-protection actifs même avec `unsafe`/`--no-verify`.
- **Zéro jargon & anti-écrasement :** aucun numéro artificiel ; aucun écrasement sans diff validé.
- **Git & versioning (chef d'orchestre seul) :** `commit`/`push`/tag/release (accord requis) via `.agents/rules/05-git-versioning.md` (`dev`, vérifs, *Conventional Commits*, SemVer + `CHANGELOG.md`, tags `vX.Y.Z`) ; interdit sous-agents/`swarm`/`run_workflow`.
- **Clôture `gravity-metrics` (racine mono-dépôt seule) :** post-commit/push via `references/08-session-learning.md` (`--update-sealed` si `push` ultérieur) ; interdit sous-agents/`swarm`/`run_workflow`.
- **Budget & outils :** `AGENTS.md`/`GEMINI.md` + `always_on` `< 20 000` tokens (`<= 24 000` o/fichier). Appelle `run_workflow`, `swarm`, `ask_question`, `invoke_subagent` ; recommande `/boost`, `/teamwork`, `/grill-me`, `/learn`.
- **Python & profil :** `python3`/`python`/`py -3` (`PATH`) ; passer par `observability/profile_engine.py`.

## 1. Grammaire 3 axes & CLI (`profile_engine.py`)

Mots-clés en tête (**mot entier**) ou sous-commandes (`status`, `evals`, `init`) :
- **Mode (`quick|standard|deep|evo`) :** `quick`*(low)*, `standard`*(défaut, medium)*, `deep`*(high/ultra)* verrouillent (`locked: true`) ; `evo`*(evolutive)* consultatif (`locked: false`, `suggested_profile` au `Stop`, `metrics` `[-5, +10]`). `slow`/`fast` sans effet.
- **Vérif (`unsafe|--no-verify`) :** coupe revue/tests pour **1 tâche ou `run_workflow`**, jamais persistée (`PreToolUse` ON).
- **Concision (`/caveman`) :** défaut `lite` (`auto` -> `full` si contexte/coût élevé via `metrics` ; `evals/` exclu).
- **Cadrage `/grill-me` :** explore d'abord ; 1 question/fois via `ask_question` (`(Recommended)` 1er) ou `/plan /grill-me`.
- **Vagues & orchestration :** moteur par défaut **`swarm`** (appeler directement l'outil `swarm` : `workspace="share"` en mono-dépôt ; `workflows/swarm_wave.py` `prepare`->`diff`->`apply`->`cleanup` sur `wave_plan_v1` multi-dépôts, `monitor: false`). Repli opt-in `run_workflow` (`--engine run_workflow` -> `wave_runner.py` / `review_pipeline.py`) et `/gravity-workflow evals` (`references/05-workflows-and-routing.md`).

```bash
python3 <plugin_root>/observability/profile_engine.py get --workspace . --json
python3 <plugin_root>/observability/profile_engine.py set --workspace . [--mode <quick|standard|deep|evo>] [--engine <swarm|run_workflow>] [--caveman <level>] [--plan-model <tier>] [--build-model <tier>] [--review-model <tier>] --json
```

## 2. Profils, état & micro-carte

- **`quick` :** `builder` `flash` + `1` `reviewer` `flash` mutualisé (`0` remédiation, `planner` sauté) ; `--no-verify`/`unsafe` seul saute `reviewer`.
- **`standard` *(défaut)* :** direct si trivial ; sinon `swarm` (`swarm_wave.py` / `workspace="share"`) ou `review_pipeline.py` opt-in (`planner` `flash` sauté si `plan`, `builder` `flash`, `reviewer` `pro` indépendant/tâche, `1` remédiation).
- **`deep` :** `swarm` (`planner` `pro` sauté si `plan`, `builder` `flash`, `reviewer` `pro` indépendant/tâche, `2` remédiations) ; suggérer `/plan /grill-me`, `/boost`.
- *Surcharges :* `"allow_pro": false` -> `pro` sur `flash` ; `--plan-model`/`--build-model`/`--review-model` et `Args` prioritaires (`references/05-workflows-and-routing.md`).
- **État :** `PROGRESS.md` -> micro-carte + action ; `.agents/` sans `PROGRESS.md` -> budget/triggers ; nu -> centralisé (`init` : `init_workspace.py`, `--ensure-git-rule` si `.agents/`/`init`).

```text
Gravity Workflow [X/8 ou centralisé] · Profil : <quick|standard|deep> (evo|verrouillé) · Vérif : <safe|no-verify éphémère> · Étape : <Nom métier>
Santé : Règles <OK/N-A> · Guard Fail-Closed <ON/OFF> · Caveman : <niveau> (auto|verrouillé) · Evals <pass/total|N/A>
Suggestion evo : <standard|deep> (<motif comportemental>) · Prochaine action : <Action concrète>
```

## 3. Utilitaires (`status`, `evals`) & références

- **`/gravity-workflow status` :** `profile_engine.py get --workspace . --json` + centralisé (`runs.jsonl`, `evals/report.json`) + `.agents/`, **4 blocs** (avancement/`role_models`/`suggested_profile`, santé règles/sécurité, évals/`runs.jsonl` + `Signaux Metrics`, 1–3 actions). App : `generative_ui` HTML ; CLI : Markdown (`references/09-observability-dashboard.md`).
- **`/gravity-workflow evals [CASE-ID...]` :** `run_workflow` sur `evals/run_suite.py` (`Args`: `case_ids`, `plugin_root`, `candidate_model`, `judge_model`, `level`), score + `[manual]`, `/learn` + `cases.json` si écart (`references/07-eval-suite.md`).

Charge **uniquement** la fiche requise :
- `references/00-quick-start.md` *(Démarrage & init)*
- `references/01-project-scoping.md` *(Cadrage)*
- `references/02-charter-and-rules.md` *(Charte & règles)*
- `references/03-subagents-architecture.md` *(Sous-agents)*
- `references/04-tools-and-mcp.md` *(Outils & MCP)*
- `references/05-workflows-and-routing.md` *(Profils, A/B, routeur, `swarm` & `swarm_wave.py`)*
- `references/06-safety-hooks.md` *(Garde-fous & hooks)*
- `references/07-eval-suite.md` *(Évals)*
- `references/08-session-learning.md` *(Adaptation `evo`, Metrics & `/learn`)*
- `references/09-observability-dashboard.md` *(Observabilité, `status` & `/gravity dashboard`)*
