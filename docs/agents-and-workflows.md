# Sous-agents, Workflows & Routage

## Les 3 sous-agents du plugin (`gravity-plugin/agents/`)

Les agents sont validés strictement par [`validate.py`](../gravity-plugin/scripts/validate.py) (`readonly=True` interdit tout outil d'écriture, d'exécution shell ou `run_workflow`) :

| Agent | Fichier | Modèle manifeste | Permissions & Isolation | Rôle |
|---|---|---|---|---|
| **Planner** | [`planner.md`](../gravity-plugin/agents/planner.md) | `pro` | Lecture seule (`view_file`, `read_file`, `grep_search`, `find_by_name`, `list_dir`, `send_message`), `sandbox` | Analyse le contexte, vérifie `01-safety-boundaries.md`, produit un plan borné (concision Caveman plafonnée à `full`), sans planifier de `git commit`/`push`/`tag` ou release, et refuse précocement toute dérive sécurité |
| **Builder** | [`builder.md`](../gravity-plugin/agents/builder.md) | `flash` | Édition (`write_file`, `edit_file`, `write_to_file`, `replace_file_content`, `multi_replace_file_content`) + `run_command`, `workspace="branch"`, `sandbox` | Implémente strictement le plan ou corrige les constats bloquants dans un worktree isolé, sans jamais commiter/pousser/taguer ni faire de release, et produit `.gravity_verified.patch` |
| **Reviewer** | [`reviewer.md`](../gravity-plugin/agents/reviewer.md) | `pro` | Lecture seule (`view_file`, `read_file`, `grep_search`, `find_by_name`, `list_dir`, `send_message`), **`inheritMcp: false`**, `workspace="inherit"` | Audit indépendant à froid (sans historique de conversation) sur les fichiers produits et le diff (constats 1 ligne sous Caveman, *Preuve ou Silence* intact) |

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

[`review_pipeline.py`](../gravity-plugin/workflows/review_pipeline.py) orchestre la boucle via `run_workflow` et synchronise `.agents/gravity/observability/live_state.json` :

1. **Résolution du preset :** appelle `profile_engine.get_active_preset()` (sauf surcharge explicite via `Args`, incluant `caveman`), puis génère les directives ciblées par ROI via `caveman_protocol.build_subagent_directive()` (`planner` plafonné à `full`, `builder` sec, `reviewer` 1 ligne *Preuve ou Silence*).
2. **Phase 1 — Plan :** exécute `planner` (`plan_model`), ou condense cette étape si `skip_separate_planner=True` (stratégie `fast` — sans modifier les modèles du palier).
3. **Phase 2 — Builder multi-tour isolé :** crée un agent `builder` persistant (`workspace="branch"`, fermé proprement dans `finally: del builder`) qui applique le plan et génère `.gravity_verified.patch` via `git add -N .` puis `git diff --output=.gravity_verified.patch -- . ":(exclude).gravity_verified.patch"` (compatible POSIX et Windows PowerShell 5.1, sortie UTF-8 sans BOM avec décodage auto UTF-16/UTF-8 dans `_read_worktree_patch`).
4. **Court-circuit `unsafe` :** si `skip_verification=True`, sauvegarde le patch dans `.agents/gravity/observability/last_verified.patch` sans lancer le `reviewer`.
5. **Phase 3 & 4 — Revue & remédiation (`safe`) :**
   - Lit `.gravity_verified.patch` en pur Python (troncature prompt à `60 000` caractères avec invitation à inspecter le worktree).
   - Soumet au `reviewer` (`REVIEW_SCHEMA` compatible Gemini, sans `maxItems`). Force `FAIL` dès qu'un `BLOCKER` ou `MAJOR` est présent.
   - Si `FAIL`, relance **le même** `builder` pour corriger uniquement les constats bloquants, jusqu'à `max_remediation_rounds` (`0`/`1`/`2`, plafonné à `1` en `fast`), en contrôlant `build.get("refused")` après chaque remédiation.
   - Si `PASS`, exporte `.agents/gravity/observability/last_verified.patch` ; en cas d'épuisement des tours, passe en `workflow:escalated` avec alerte humaine.

---

## Arbre de décision entre moteurs natifs

Référence détaillée dans [`05-workflows-and-routing.md`](../gravity-plugin/skills/gravity/references/05-workflows-and-routing.md) :

- **Besoin ambigu / décisions de design ouvertes :** explorer le dépôt puis clarifier une question à la fois via `ask_question`, ou proposer `/grill-me` / `/plan /grill-me` à l'utilisateur *(slash command utilisateur uniquement, tous plans)* avant d'engager `builder`, `review_pipeline.py` ou `swarm`.
- **Tâche quotidienne / ciblée :** `Orchestrator` *(Pro†/Ultra/Enterprise)* ou `/plan` / `/goal` en conversation mono-fil (`low` / `medium`).
- **Flux structuré répétable ou évaluations :** outil `run_workflow` appelable directement par l'agent (`phase()`, `parallel()`, `pipeline()`, tous plans).
- **Chantier parallélisable multi-modules :** outil `swarm` *(Preview, tous plans)* sur paliers `high`/`ultra` avec `@verifier` sur modèle distinct en `ultra` (statuts et posts sur le board soumis à la consigne Caveman lorsqu'active ; `git commit`, `git push`, `git tag` et versioning restent strictement réservés au chef d'orchestre, jamais aux workers `swarm`).
- **Point dur algorithmique / concurrence :** proposer `/boost <tâche>` à l'utilisateur *(slash command utilisateur uniquement, plans payants)*.
- **Grand chantier multi-jalons (> 1 jour) :** proposer `/teamwork <objectif>` à l'utilisateur *(slash command utilisateur uniquement, plans payants, dans `~/teamwork_projects/`)*.
