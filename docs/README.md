# Documentation Vivante Gravity Workflow

**Gravity Workflow** — *Ground your Antigravity agents* — est un harnais multi-agents natif pour **Google Antigravity** (App `v2.34.0+`, CLI `agy` et extensions IDE), entièrement implémenté en **bibliothèque standard Python 3** sans dépendance tierce ni étape de build Node.js.

Cette documentation remplace la spécification statique monolithique par des fiches thématiques maintenues en continu au fil des évolutions de [`gravity-workflow-plugin/`](../gravity-workflow-plugin/), [`workspace-template/`](../workspace-template/) et [`install.py`](../install.py). Le code source du dépôt reste l'unique source de vérité ; ces fiches en décrivent l'architecture, les contrats et les points d'entrée.

---

## Index des fiches

| Fiche | Sujet couvert | Composants principaux |
|---|---|---|
| [`architecture.md`](architecture.md) | Séparation plugin / projet cible, empreinte 13 fichiers, initialisation, `install.py` et `uninstall.py` | `gravity-workflow-plugin/`, `workspace-template/`, `init_workspace.py`, `install.py`, `uninstall.py` |
| [`modes-and-routing.md`](modes-and-routing.md) | Skill unifié `/gravity-workflow`, 3 axes orthogonaux, attribution des modèles, moteur `evo` et skill compagnon `/caveman` | `skills/gravity-workflow/SKILL.md`, `skills/caveman/SKILL.md`, `profile_engine.py`, `caveman_protocol.py`, `profile.json` |
| [`agents-and-workflows.md`](agents-and-workflows.md) | Sous-agents (`planner`, `builder`, `reviewer` indépendant), pipeline de revue et moteurs natifs | `agents/*.md`, `workflows/review_pipeline.py` |
| [`safety-and-hooks.md`](safety-and-hooks.md) | Garde-fous `PreToolUse` fail-closed, `SessionStart`, `PostToolUse`, `Stop`, règles YAML et budget contexte | `hooks.json`, `hooks/*.py`, `.agents/rules/*.md` |
| [`evals-and-learning.md`](evals-and-learning.md) | Suite d'évaluations parallèle à double filtre et boucle d'apprentissage `/learn` | `evals/cases.json`, `eval_checks.py`, `run_suite.py` |
| [`observability.md`](observability.md) | Micro-carte, `/gravity-workflow status`, `/statusline` CLI, sidecar Live Widget et écriture atomique | `state_writer.py`, `statusline.py`, `sidecars/live-widget/` |
| [`validation-and-development.md`](validation-and-development.md) | Commandes strictes POSIX / PowerShell, suites de tests et checklist de contribution | `validate.py`, `scripts/test_unit.py`, `tests/test_install.py`, `tests/test_uninstall.py` |

---

## Invariants du dépôt

1. **100 % bibliothèque standard Python 3 :** aucune dépendance tierce dans [`gravity-workflow-plugin/`](../gravity-workflow-plugin/), [`workspace-template/`](../workspace-template/), [`install.py`](../install.py), [`uninstall.py`](../uninstall.py) ni [`tests/`](../tests/).
2. **Séparation stricte moteur vs projet :** toute la logique du harnais vit dans `gravity-workflow-plugin/` ; l'initialisation dans un projet utilisateur ne crée que les 13 fichiers vitaux de `AGENTS.md` + `.agents/`.
3. **Zéro jargon interne :** ne jamais introduire de numérotation artificielle (`Module XX`) dans les skills, fiches de référence ou cette documentation.
4. **Dépôt compagnon métriques :** la collecte et l'analyse des coûts/tokens de sessions Antigravity sont séparées dans le dépôt frère `../gravity-metrics` (écosystème `../gravity`).

---

## Protocole de mise à jour continue

Toute modification apportée au code doit mettre à jour la ou les fiches correspondantes **dans le même lot de travail** :

| Zone modifiée | Fiche(s) à mettre à jour |
|---|---|
| Manifeste `plugin.json`, arborescence, `init_workspace.py`, `workspace-template/`, `install.py`, `uninstall.py` | [`architecture.md`](architecture.md) |
| `skills/gravity-workflow/SKILL.md`, `skills/caveman/SKILL.md`, `observability/profile_engine.py`, `observability/caveman_protocol.py`, `profile.json` | [`modes-and-routing.md`](modes-and-routing.md) |
| `agents/*.md`, `workflows/review_pipeline.py`, références routage | [`agents-and-workflows.md`](agents-and-workflows.md) |
| `hooks.json`, `hooks/*.py`, `.agents/rules/*.md` | [`safety-and-hooks.md`](safety-and-hooks.md) |
| `evals/cases.json`, `evals/eval_checks.py`, `evals/run_suite.py` | [`evals-and-learning.md`](evals-and-learning.md) |
| `observability/state_writer.py`, `statusline.py`, `sidecars/live-widget/` | [`observability.md`](observability.md) |
| `scripts/validate.py`, `scripts/validators/`, `scripts/test_unit.py`, `tests/test_install.py`, `tests/test_uninstall.py` | [`validation-and-development.md`](validation-and-development.md) |
