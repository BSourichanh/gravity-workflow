# Évaluations (`/gravity-workflow evals`) & Apprentissage (`/learn`)

## Rôle des évaluations

Là où `test_unit.py` et `tests/test_install.py` testent le code Python du plugin, **`/gravity-workflow evals` teste automatiquement le comportement réel des agents** face aux conventions du projet et aux requêtes pièges.

- **Déclenchement :** `/gravity-workflow evals` ou `/gravity-workflow evals <CASE-ID...>` appelle `run_workflow` sur [`run_suite.py`](../gravity-workflow-plugin/evals/run_suite.py).
- **Priorité sur `unsafe` :** une invocation explicite `/gravity-workflow evals` s'exécute même si le profil actif est en `verification: "unsafe"` (`unsafe` coupe uniquement les vérifications automatiques en fin de tâche).
- **Exclusion Caveman (`caveman_evals: "off"`) :** la suite d'évaluations est strictement exclue des directives Caveman afin de ne jamais biaiser les prédicats déterministes ni l'audit indépendant.
- **Signal pour `evo` :** le taux de réussite dans `.agents/gravity-workflow/evals/report.json` alimente `evolve_on_stop()` (`-5` pts si `100 %`, `+15` pts si `< 100 %`).

---

## Catalogue starter (`evals/cases.json`)

Copié à l'initialisation vers `.agents/gravity-workflow/evals/cases.json` ([`cases.json`](../gravity-workflow-plugin/evals/cases.json)), il comprend **12 cas** couvrant :

- **Conventions & intégrité :** migration de schéma sans régression (`CASE-01`), absence de secret en dur (`CASE-02`), préservation des garde-fous lors de l'ajout d'outils (`CASE-03`), manifeste JSON (`CASE-04`), respect strict Python stdlib (`CASE-05`), syntaxe `globs` chaîne pour règles conditionnelles (`CASE-06`), sous-agent lecture seule (`CASE-07`), connecteur MCP HTTP sans fuite de jeton (`CASE-08`).
- **Anti-faux positif (`CASE-09-BENIGN-KEYBOARD-FILE`) :** vérifie qu'un fichier légitime contenant `key` dans son nom (`src/ui/keyboard.py`) reste éditable.
- **Tests adversariaux (`CASE-10` à `CASE-12`) :** exige `expect_refusal()` contre l'exfiltration de `.env`/`printenv`, la désactivation de `hooks.json`/`01-safety-boundaries.md`, et les commandes destructrices (`rm --recursive --force /tmp/..`, `DROP DATABASE production;`, `git push --force`).

---

## Architecture à double filtre

1. **Candidat isolé (`builder`, `workspace="branch"`) :** chaque cas s'exécute en parallèle dans une branche Git jetable (capture d'exception `try/except` par cas et fermeture garantie dans `finally: del cand`). Un `workspace_path` vide échoue explicitement au pré-filtre sans repli sur `WS_ROOT`.
2. **Pré-filtre Python déterministe à 0 token ([`eval_checks.py`](../gravity-workflow-plugin/evals/eval_checks.py)) :** évalue par AST whitelist (sans `eval()`) les prédicats `auto_checks` : `expect_refusal()`, `file_exists()`, `not_exists()`, `json_valid()`, `schema_contains()`, `file_contains()`, `not_contains()`, `python_syntax_valid()`, `rule_frontmatter_valid()`, `agent_frontmatter_readonly()` (utilisé sur `CASE-07` pour n'inspecter que `tools:` YAML sans faux positif sur le corps Markdown). Tout échec rejette le cas avant le juge.
3. **Juge indépendant (`reviewer`, `workspace="inherit"`) :** n'audite que les cas ayant passé le pré-filtre (`UNCERTAIN`/`NIT` tronqués à 3, `FAIL` si `BLOCKER` ou `MAJOR`).
4. **Double sortie projet :** écrit `.agents/gravity-workflow/evals/report.json` et `.agents/gravity-workflow/evals/report.md` (table échappée + checklist `[manual]` à valider humainement) et met à jour `live_state.json`.

---

## Double boucle d'apprentissage (`/learn`)

Dès qu'une correction manuelle ou une anomalie survient, appliquer conjointement les deux boucles ([`08-session-learning.md`](../gravity-workflow-plugin/skills/gravity-workflow/references/08-session-learning.md)) :

1. **Comportement (`.agents/rules/`) :** invoquer `/learn` ou proposer un diff créant une règle conditionnelle (`trigger: glob` ou `trigger: model_decision`) afin de ne jamais surcharger le socle `trigger: always_on` ni le Context Caching Gemini.
2. **Non-régression (`.agents/gravity-workflow/evals/cases.json`) :** ajouter un nouveau cas reproduisant l'erreur corrigée avec ses `auto_checks`, puis rejouer `/gravity-workflow evals <CASE-ID>`.
