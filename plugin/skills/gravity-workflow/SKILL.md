---
name: gravity-workflow
description: Chef d'orchestre unifié Gravity Workflow : 3 profils (quick, standard, deep), mode consultatif evo, vérification éphémère (--no-verify) et /caveman.
---

# Gravity Workflow — Ground your Antigravity agents

*(Skill principal `/gravity-workflow` ou `/gravity-workflow:gravity-workflow`, compagnon `/caveman`.)*

## Contraintes absolues
- **Stockage centralisé, zéro pollution :** hooks, `Live` et `profile_engine.py` fonctionnent sur tout dépôt sans `init` sous `$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/workspaces/<slug>-<sha256_8>/` (repli lecture seule `.agents/gravity-workflow/profile.json`). `.agents/` reste optionnel (`init`).
- **Garde-fous `PreToolUse` fail-closed toujours ON :** secrets, commandes destructrices et auto-protection restent actifs même avec `unsafe` / `--no-verify`.
- **Zéro jargon mécanique & anti-écrasement :** n'utilise jamais de numérotation artificielle ; n'écrase jamais un fichier existant sans diff validé.
- **Git & versioning (chef d'orchestre uniquement) :** avant `commit`/`push`/tag/release (accord requis), appliquer `.agents/rules/05-git-versioning.md` (`dev`/branche dédiée, vérifs, *Conventional Commits*, SemVer + `CHANGELOG.md`, tags `vX.Y.Z`) ; interdit aux sous-agents et `swarm`.
- **Budget & outils natifs :** `AGENTS.md`/`GEMINI.md` + règles `always_on` `< 20 000` tokens (`<= 24 000` o/fichier). Appelle directement `run_workflow`, `swarm`, `ask_question`, `invoke_subagent` ; recommande `/boost`, `/teamwork`, `/grill-me`, `/learn` à l'utilisateur.
- **Python & profil :** utilise `python3`, `python` ou `py -3` selon `PATH` ; passe toujours par `observability/profile_engine.py`.

## 1. Grammaire 3 axes & CLI (`profile_engine.py`)

Mots-clés positionnels en tête (**mot entier** uniquement) ou sous-commandes (`status`, `evals`, `init` seuls ou avec leurs arguments dédiés) :
- **Mode (`quick` | `standard` | `deep` | `evo`) :** `quick`*(low)*, `standard`*(défaut, medium)*, `deep`*(high/ultra)* verrouillent (`locked: true`) ; `evo`*(evolutive)* active le mode consultatif (`locked: false`, `suggested_profile` au `Stop` avec signaux `metrics` bornés `[-5, +10]`). `slow`/`fast` tolérés sans effet.
- **Vérif éphémère (`unsafe` | `--no-verify`) :** désactive revue/tests pour **1 tâche ou 1 `run_workflow`**, jamais persistée (`PreToolUse` reste ON).
- **Concision (`/caveman`) :** défaut `lite` (`auto` passe à `full` si contexte/coût output élevé via `metrics` ; `evals/` exclu).
- **Cadrage `/grill-me` :** explore d'abord le dépôt ; clarifie ensuite une question à la fois via `ask_question` (`(Recommended)` en premier) ou recommande `/plan /grill-me`.

```bash
python3 <plugin_root>/observability/profile_engine.py get --workspace . --json
python3 <plugin_root>/observability/profile_engine.py set --workspace . [--mode <quick|standard|deep|evo>] [--caveman <level>] [--plan-model <tier>] [--build-model <tier>] [--review-model <tier>] --json
```

## 2. Profils, état projet & micro-carte

- **`quick` :** mono-fil direct (`builder` `flash`, `0` remédiation, `planner`/`reviewer` sautés).
- **`standard` *(défaut)* :** direct si trivial ; `review_pipeline.py` sinon (`planner` `flash` sauté si `plan` fourni, `builder` `flash`, `reviewer` `pro`, `1` remédiation).
- **`deep` :** `review_pipeline.py` complet (`planner` `pro`, `builder` `flash`, `reviewer` `pro`, `2` remédiations) ; suggérer `/plan /grill-me`, `/boost` ou `/swarm`.
- *Surcharges :* `"allow_pro": false` -> `pro` rabattu sur `flash` ; `--plan-model`/`--build-model`/`--review-model` et `Args` explicites prioritaires (`references/05-workflows-and-routing.md`).
- **État :** équipé (`PROGRESS.md`) -> micro-carte + exécution ; audit (`.agents/` sans `PROGRESS.md`) -> contrôle budget/triggers ; dépôt nu -> centralisé sans écriture locale (`init` optionnel via `init_workspace.py`, `--ensure-git-rule` uniquement si `.agents/` existe ou `init`).

```text
Gravity Workflow [X/8 ou centralisé] · Profil : <quick|standard|deep> (evo|verrouillé) · Vérif : <safe|no-verify éphémère> · Étape : <Nom métier>
Santé : Règles <OK/N-A> · Guard Fail-Closed <ON/OFF> · Caveman : <niveau> (auto|verrouillé) · Evals <pass/total|N/A>
Suggestion evo : <standard|deep> (<motif comportemental>) · Prochaine action : <Action concrète>
```

## 3. Utilitaires (`status`, `evals`) & références à la demande

- **`/gravity-workflow status` :** exécute `profile_engine.py get --workspace . --json`, lis stockage centralisé (`runs.jsonl`, `evals/report.json`) et `.agents/`, puis restitue **4 blocs** (avancement/`role_models`/`suggested_profile`, santé règles/sécurité, évals/`runs.jsonl` + `Signaux Metrics (...)` si disponible, 1–3 actions). App : `generative_ui` HTML ; CLI : Markdown (`references/09-observability-dashboard.md`).
- **`/gravity-workflow evals [CASE-ID...]` :** `run_workflow` sur `evals/run_suite.py` (`Args`: `case_ids`, `plugin_root`, `candidate_model`, `judge_model`, `level`), score + `[manual]`, `/learn` + `cases.json` si écart (`references/07-eval-suite.md`).

Charge **uniquement** la fiche requise :
- `references/00-quick-start.md` *(Démarrage & init)*
- `references/01-project-scoping.md` *(Cadrage)*
- `references/02-charter-and-rules.md` *(Charte & règles)*
- `references/03-subagents-architecture.md` *(Sous-agents)*
- `references/04-tools-and-mcp.md` *(Outils & MCP)*
- `references/05-workflows-and-routing.md` *(Matrice profils, A/B & routeur)*
- `references/06-safety-hooks.md` *(Garde-fous & hooks)*
- `references/07-eval-suite.md` *(Suite d'évaluations)*
- `references/08-session-learning.md` *(Adaptation `evo`, Metrics & `/learn`)*
- `references/09-observability-dashboard.md` *(Observabilité, `status` & Live Widget)*
