# Équipe & Reviewer indépendant (Modèles Gemini & Sous-agents)

## Objectif
Exploiter l'équipe de sous-agents spécialisés embarquée dans `gravity-plugin/agents/` aux permissions minimales (principe du moindre privilège), choisir les modèles et niveaux d'effort adaptés à chaque rôle, et instituer une revue indépendante à froid sans biais de complaisance (sans historique de conversation).

## Catalogue modèles Antigravity & stratégie Gemini (2026)

| Modèle / Tier | Disponibilité | Contexte | Niveaux `/effort` | Usage recommandé Gravity |
| :--- | :--- | :--- | :--- | :--- |
| **`Orchestrator`** *(natif)* | Pro†, Ultra, Enterprise | Selon paire | Fixe (auto) | Conversation principale : Planner (`Neon`/`Carbon`/`Argon`/`Opus 5.5`) + Executor (`Hydrogen`/`Sonnet 5.5`). |
| **Gemini 4 Neon** | Pro†, Ultra, Enterprise | **512K** | `low`, `medium`, `high`, **`xhigh` (Extra)** | Planification, revue indépendante critique, coordination `/swarm`. *(Attention : fenêtre 512K, pas 1M).* |
| **Gemini 4 Carbon** | Ultra, Enterprise | Frontier | `low`, `medium`, `high` | Raisonnement très profond (planner `Orchestrator` en Enterprise). |
| **Gemini 4 Argon** | Pro†, Ultra, Enterprise | **1M** | `low`, `medium`, `high` | Audit multi-fichiers ou gros contextes exigeant > 512K tokens. |
| **Gemini 4 Hydrogen** | Pro (non-trial), Ultra, Enterprise | **1M** | `low`, `medium`, `high`, **`xhigh`** | Exécution rapide (`builder`), refactorisation, tarif plat jusqu'à 1M. |
| **Gemini 3.8 Flash** | **Tous plans (y compris Free & Plus)** | **1M** | `low`, `medium`, `high` | Seul Gemini sur Free/Plus ; exécuteur économique et repli universel pour workflows parallèles. |
| **Gemini 3.7 / 3.6 Flash, 3.1 Pro** | Enterprise uniquement | 1M | `low`..`high` | Flotte Enterprise (`3.1 Pro` a un palier tarifaire > 200K tokens). |
| **Claude Sonnet 5.5 / Opus 5.5 (thinking)** | Pro (non-trial), Ultra, Enterprise | — | `low`, `medium`, `high`, `max` | Alternative cross-model (exécution hybride : des modèles Gemini légers gèrent le contexte en fond). *(Claude 4.6 & GPT-OSS-120b retirés le 30/11/2026).* |
| **Nano Banana 2** | Automatique | — | — | Modèle dédié de l'outil `generate_image`. |

- **Tiers de sous-agents & workflows :** `inherit` (modèle de la session), `flash_lite` (triage/extraction économique), `flash` (exécution rapide), `pro` (planification & audit).
- **Piège de compatibilité :** demander `model: pro` dans un sous-agent ou `run_workflow` échoue si le compte utilisateur n'a pas accès au tier `pro` (ex. plan **Free/Plus** avec `Gemini 3.8 Flash` seul). Prévois un repli documenté sur `inherit` ou `flash`.
- **Coût du raisonnement (`/effort`, `--effort`) & cache :** les tokens de *thinking* sont facturés au tarif **`output`** (~5× l'input). Utilise `low`/`medium` pour les exécutions bornées et evals de routine, et réserve `high`/`xhigh` à la planification complexe ou aux revues bloquantes.
- **Vérification cross-model :** dans `/swarm`, demande explicitement d'épingler `@verifier` sur un autre modèle que les workers (ex. workers sur `Hydrogen`/`3.8 Flash`, vérifieur sur `Neon` ou `Claude Opus 5.5`).

## Conception des sous-agents du plugin (`gravity-plugin/agents/<role>.md`)
Les 3 sous-agents (`planner`, `builder`, `reviewer`) sont fournis par le plugin (sans dupliquer `.agents/agents/` dans le projet cible, sauf si le projet souhaite surcharger un agent localement). Chaque sous-agent utilise un frontmatter YAML précisant :
- `name` et `description` claire du périmètre.
- `model`: **`pro`** (ou `inherit` sur Free/Plus) pour la planification (`planner.md`) et l'audit critique (`reviewer.md`) — en cohérence avec le mode natif `Orchestrator` — ; **`flash`** pour l'exécution en branche isolée (`builder.md`) ; **`flash_lite`** pour les tris haute volumétrie.
- `subagent: true`, `mainAgent: false`, `commandExecutionPolicy: sandbox`.
- `inheritMcp` (`false` recommandé sur les auditeurs stricts) et `inheritCustomizations` (`true`).
- **Périmètre Git & versioning :** `git commit`, `git push`, `git tag`, la montée de version SemVer et l'édition release de `CHANGELOG.md` (`.agents/rules/05-git-versioning.md`) sont strictement réservés au **chef d'orchestre** (interdits à `planner`, `builder`, `reviewer` et aux workers `swarm`).
- `tools`: allowlist stricte limitée aux outils réellement nécessaires.
  > **Avertissement Antigravity (`docs/subagents.md`) :** un nom d'outil mal orthographié ou inexistant dans `tools:` peut faire geler le sous-agent à l'exécution. Vérifie les noms exacts supportés par ta surface (`view_file`, `read_file`, `grep_search`, `find_by_name`, `list_dir`, `write_to_file`, `write_file`, `replace_file_content`, `edit_file`, `multi_replace_file_content`, `run_command`, `send_message`).

## Le Reviewer indépendant (`gravity-plugin/agents/reviewer.md`)
L'auditeur inspecte le livrable **sans aucun historique de conversation**, uniquement à partir des fichiers produits et du `git diff` transmis explicitement par le workflow ou l'appelant (le Reviewer n'ayant pas d'outil shell).
- **Outils autorisés (lecture seule uniquement)** : `view_file`, `read_file`, `grep_search`, `find_by_name`, `list_dir`, `send_message`. Aucun outil d'écriture (`write_to_file`, `write_file`, `replace_file_content`, `edit_file`, `multi_replace_file_content`), d'exécution shell (`run_command`, `execute_command`, `bash`, `shell`) ni `run_workflow`.
- **Modèle** : `model: pro` (repli `inherit` sur Free/Plus).
- **Protocole obligatoire** :
  1. **Comprehension Check** : résumé factuel en 2–3 phrases de ce que fait réellement le livrable.
  2. **Preuve ou silence** : aucun `BLOCKER` ni `MAJOR` sans le triplet exact `Emplacement (chemin:ligne)` + `Preuve (extrait littéral)` + `Règle violée`. Sans preuve matérielle, reclasser en `UNCERTAIN` ou supprimer.
  3. **Grille de sévérité** :
     - `BLOCKER` (ligne rouge / `01-safety-boundaries.md`) -> `FAIL` immédiat.
     - `MAJOR` (écart objectif et prouvable aux specs) -> `FAIL`.
     - `UNCERTAIN` (max 3, ambiguïté contextuelle) -> ne bloque pas seul.
     - `NIT` (max 3, forme/lisibilité) -> `PASS`, optionnel.
     - `[manual]` -> checklist qualitative laissée au jugement humain.

## Checkpoint déterministe
- `agents/reviewer.md`, `agents/planner.md` et `agents/builder.md` sont actifs depuis le plugin avec frontmatter YAML valide et allowlist conforme (vérifié par `validate.py`).
- Mettre à jour `.agents/gravity/PROGRESS.md` et afficher la micro-carte.
