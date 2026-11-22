# Tests & évaluations (Suite parallèle à double filtre)

## Objectif
Mesurer objectivement la fiabilité du harnais sur une batterie représentative de **10 à 15 cas** dans `.agents/gravity-workflow/evals/cases.json` (12 cas initiaux fournis par `/gravity-workflow`, dont cas adversariaux et test anti-faux positif), en isolant chaque exécution dans une branche Git temporaire et en séparant strictement contrôles déterministes (`auto_checks`) et jugement humain (`[manual]`).

## Format du catalogue (`.agents/gravity-workflow/evals/cases.json`)
Tableau JSON à la racine où chaque objet contient :
- `id` : identifiant unique (ex. `CASE-01-SCHEMA-INTEGRITY`).
- `description` : objectif métier testé.
- `setup_files` *(optionnel)* : dictionnaire `{chemin: contenu}` initialisant les fixtures attendues dans la branche du candidat.
- `input` : consigne fournie à l'agent candidat.
- `auto_checks` : liste de prédicats déterministes évalués sans LLM dans le worktree réel (`workspace_path`) du candidat (`file_exists(...)`, `not_exists(...)`, `json_valid(...)`, `schema_contains(...)`, `file_contains(...)`, `not_contains(...)`, `python_syntax_valid(...)`, `rule_frontmatter_valid(...)`, `agent_frontmatter_readonly(...)`, `expect_refusal()`).
- `manual_checks` : liste de critères qualitatifs humains préfixés par `[manual]`.

## Architecture à double filtre (`gravity-workflow-plugin/evals/eval_checks.py` + `run_suite.py`)
Exécutée par l'agent via `/gravity-workflow evals` qui appelle l'outil **`run_workflow(ScriptPath="<plugin_root>/evals/run_suite.py", Description="...")`** (tous plans ; `Args` optionnels : `candidate_model`, `judge_model`, `level`, `case_ids` ; par défaut alignés sur `profile_engine.get_active_preset`) :
1. **Candidats isolés (`type_name="builder"`, `model=candidate_model`, défaut issu du palier actif)** : exécution via `await parallel(cases, evaluate_case)` avec **capture d'exception `try/except` par cas** (évite qu'un échec isolé n'annule tout le lot dans `parallel()`). Chaque candidat utilise un agent multi-tour (`cand = agent(..., workspace="branch")`) maintenu vivant dans un bloc `finally: del cand` jusqu'après l'audit, afin que son worktree ne soit pas nettoyé prématurément.
2. **Pré-filtre Python déterministe (`eval_checks.run_deterministic_checks`)** : évaluation sûre par AST whitelist (sans `eval()`) sur le `workspace_path` réel du candidat. Tout échec rejette immédiatement le cas à **0 token de juge**. Module pur importable sans `run_workflow` pour tests unitaires.
3. **Juge critique indépendant (`type_name="reviewer"`, `workspace="inherit"`, `model=judge_model`, défaut issu du palier actif)** : sollicité uniquement si le pré-filtre passe, sans créer de clone `branch` inutile pour le juge en lecture seule, avec `workspace_path` + `git_diff` et schéma JSON compatible Gemini (sans `maxItems` bloquant ; troncature Python à 3 items).
4. **Double sortie consolidée dans le projet** : écriture via `WORKSPACES[0]` de `.agents/gravity-workflow/evals/report.md` (tableaux échappés + checklist `[manual]` via `eval_checks.render_markdown_report`) et `.agents/gravity-workflow/evals/report.json`.

> **Quotas & CI/CD :** 12 cas parallèles lancent jusqu'à 24 sessions d'agents (attention aux fenêtres de quota 5h/hebdo ; filtrer avec `Args={"case_ids": [...]}` en développement et préférer `effort` modéré). Pour l'automatisation CI headless sans UI, combiner avec **`agy -p --json-schema <schema> --output-format json`** ou le **SDK Python Antigravity**.

## Composants & livrables
1. `.agents/gravity-workflow/evals/cases.json` dans le projet (initialisé depuis `gravity-workflow-plugin/evals/cases.json`).
2. `gravity-workflow-plugin/evals/eval_checks.py` (prédicats AST whitelist et rendu Markdown 100 % stdlib).
3. `gravity-workflow-plugin/evals/run_suite.py` (orchestrateur `run_workflow` 100 % stdlib Python 3).
4. `gravity-workflow-plugin/skills/gravity-workflow/SKILL.md` (commande `/gravity-workflow evals` appelant directement l'outil `run_workflow`).

## Checkpoint déterministe
- `.agents/gravity-workflow/evals/cases.json` est un tableau JSON valide de 10 à 15 cas séparant `auto_checks` et `manual_checks`.
- L'exécution via `run_workflow` produit simultanément `.agents/gravity-workflow/evals/report.md` et `.agents/gravity-workflow/evals/report.json` et met à jour `.agents/gravity-workflow/observability/live_state.json`.
- Mettre à jour `.agents/gravity-workflow/PROGRESS.md` et afficher la micro-carte.
