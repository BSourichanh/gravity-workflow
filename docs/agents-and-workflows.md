# Sous-agents, Workflows & Routage

## Les 3 sous-agents du plugin (`plugin/agents/`)

Les agents sont validés strictement par [`validate.py`](../plugin/scripts/validate.py) (`readonly=True` interdit tout outil d'écriture, d'exécution shell ou `run_workflow`) :

| Agent | Fichier | Modèle manifeste | Permissions & Isolation | Rôle |
|---|---|---|---|---|
| **Planner** | [`planner.md`](../plugin/agents/planner.md) | `pro` | Lecture seule (`read_file`, `grep_search`, `find_by_name`, `list_dir`, `send_message`), `sandbox` | Analyse le contexte, vérifie `01-safety-boundaries.md`, produit un plan borné (concision Caveman plafonnée à `full`), sans planifier de `git commit`/`push`/`tag` ou release, et refuse précocement toute dérive sécurité |
| **Builder** | [`builder.md`](../plugin/agents/builder.md) | `flash` | Édition (`write_file`, `edit_file`) + lecture (`read_file`, `grep_search`, `find_by_name`, `list_dir`) + `run_command`, `send_message`, `workspace="branch"`, `sandbox` | Implémente strictement le plan ou corrige les constats bloquants dans un worktree isolé, sans jamais commiter/pousser/taguer ni faire de release, et produit `.gravity_verified.patch` |
| **Reviewer** | [`reviewer.md`](../plugin/agents/reviewer.md) | `pro` | Lecture seule (`read_file`, `grep_search`, `find_by_name`, `list_dir`, `send_message`), **`inheritMcp: false`**, `workspace="inherit"` | Audit indépendant à froid (sans historique de conversation) sur les fichiers produits et le diff (constats 1 ligne sous Caveman, *Preuve ou Silence* intact) |

### Protocole de revue indépendante du `reviewer`
1. **Comprehension Check :** résume factuellement en 2–3 phrases ce que fait le livrable.
2. **Preuve ou Silence :** aucun `BLOCKER` ou `MAJOR` n'est recevable sans le triplet exact `chemin/fichier:ligne`, extrait littéral constaté, et règle violée. Sans preuve matérielle, le constat passe en `UNCERTAIN` ou est supprimé.
3. **Grille à 4 niveaux + `[manual]` :**
   - `BLOCKER` (ligne rouge / sécurité) → verdict `FAIL`.
   - `MAJOR` (écart objectif aux specs) → verdict `FAIL`.
   - `UNCERTAIN` (tronqué à 3 items max) → non bloquant.
   - `NIT` (tronqué à 3 items max) → non bloquant (`PASS`).
   - `[manual]` → checklist qualitative soumise à l'utilisateur.

---

## Pipeline de revue (`workflows/review_pipeline.py`)

[`review_pipeline.py`](../plugin/workflows/review_pipeline.py) orchestre la boucle via `run_workflow` et synchronise `live_state.json` + `runs.jsonl` centralisés (`$GRAVITY_HOME/workflow/workspaces/<hash>/`) :

1. **Résolution `PLUGIN_ROOT` & preset :** résout `PLUGIN_ROOT` sans `__file__` ni `os.environ` (`__file__` si présent → `args["plugin_root"]` → `WS_ROOT/plugin` → `WS_ROOT/.agents/plugins/gravity-workflow` → `~/.gemini/config/plugins/gravity-workflow`), injecte `observability/` dans `sys.path`, loggue toute erreur explicitement via `log()`, appelle `profile_engine.get_active_preset()` (surcharges `Args` prioritaires) et génère les directives Caveman via `caveman_protocol.build_subagent_directive()`.
2. **Phase 1 — Plan & pré-collecte d'arborescence :** collecte en pur Python une arborescence bornée (`_collect_repo_tree`, hors `.git`, `__pycache__`, `node_modules`, `.venv`), saute automatiquement `planner` dès que `args.get("plan")` est non vide (ou en profil `quick`, sauf override `skip_separate_planner=False`), sinon exécute `planner` (`plan_model`) avec l'arborescence injectée.
3. **Phase 2 — Builder multi-tour isolé (`validation_summary`) :** crée un agent `builder` persistant (`workspace="branch"`, fermé dans `finally: del builder`) qui applique le plan, exécute les vérifications pertinentes dans `validation_summary` et génère `.gravity_verified.patch` via `git add -N .` puis `git diff --output=.gravity_verified.patch -- . ":(exclude).gravity_verified.patch"`.
4. **Court-circuit `--no-verify` / `unsafe` éphémère :** si `no_verify=True` ou `verification="unsafe"` est passé dans `Args` (strictement éphémère, jamais persisté), sauvegarde le patch centralisé (`last_verified.patch`) sans lancer `reviewer`.
5. **Phase 3 & 4 — Revue & remédiation (`safe`) :**
   - Lit `.gravity_verified.patch` (repli `git_diff` puis aperçu borné des `modified_files` si `Non-Workspace File Access` est restreint).
   - Soumet au `reviewer` (`REVIEW_SCHEMA`) avec l'arborescence pré-collectée et `validation_summary`. Force `FAIL` dès qu'un `BLOCKER` ou `MAJOR` est présent.
   - Si `FAIL`, relance **le même** `builder` jusqu'à `max_remediation_rounds` (`0`/`1`/`2`), en contrôlant `build.get("refused")`.
   - Si `PASS`, exporte `last_verified.patch` dans le stockage centralisé ; journalise `preset_profile`, `role_models`, `verdict` et `remediation_rounds` dans `runs.jsonl`.

---

## Arbre de décision entre moteurs natifs

Référence détaillée dans [`05-workflows-and-routing.md`](../plugin/skills/gravity-workflow/references/05-workflows-and-routing.md) :

- **Besoin ambigu / décisions de design ouvertes :** explorer le dépôt puis clarifier une question à la fois via `ask_question`, ou proposer `/grill-me` / `/plan /grill-me` à l'utilisateur *(slash command utilisateur uniquement, tous plans)* avant d'engager `builder`, `review_pipeline.py` ou `swarm`.
- **Tâche quotidienne / ciblée :** `Orchestrator` *(Pro†/Ultra/Enterprise)* ou `/plan` / `/goal` en conversation mono-fil (`quick` / `standard`).
- **Flux structuré répétable ou évaluations :** outil `run_workflow` appelable directement par l'agent (`phase()`, `parallel()`, `pipeline()`, tous plans).
- **Chantier parallélisable multi-modules :** outil `swarm` *(Preview, tous plans)* proposé à l'initiative utilisateur en profil `deep` (`git commit`, `git push`, `git tag` et versioning restent strictement réservés au chef d'orchestre, jamais aux workers `swarm`).
- **Point dur algorithmique / concurrence :** proposer `/boost <tâche>` à l'utilisateur *(slash command utilisateur uniquement, plans payants)*.
- **Grand chantier multi-jalons (> 1 jour) :** proposer `/teamwork <objectif>` à l'utilisateur *(slash command utilisateur uniquement, plans payants, dans `~/teamwork_projects/`)*.
