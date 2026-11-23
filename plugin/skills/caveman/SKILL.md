---
name: caveman
description: Mode de communication ultra-concis Caveman pour réduire les tokens (off, lite, full, ultra, auto/evo), messages commit et compression contexte.
---

# Gravity Workflow Caveman — Concision ROI

*(Skill compagnon `/caveman` ou `/gravity-workflow:caveman`. Exemples FR/EN : `references/style-examples.md`.)*

## 1. Grammaire & CLI

| Commande | Effet (`profile.json`) | Comportement |
| :--- | :--- | :--- |
| **`/caveman`** *(seul)* | Si `full` -> lecture seule ; sinon `set --caveman full` | Active `full` (verrouillé) ou affiche l'état |
| **`/caveman lite\|full\|ultra\|off`** | `set --caveman <niveau>` (`caveman_locked: true`) | Verrouille l'intensité ; confirme en 1 ligne |
| **`/caveman auto`** *(alias `evo`)* | `set --caveman auto` (`caveman_locked: false`) | Rend la main à `evo` (`lite` ou `full` auto) |
| **`/caveman status`** | Lecture seule (`get --json`) | Niveau, verrou, badge et matrice ROI |
| **`/caveman commit`** | Aucune | *Conventional Commits* (`<= 50` car.) sur le **pourquoi** (`git diff`) |
| **`/caveman compress <fichier>`** | Aucune | Condense un Markdown (frontmatter/code/règles intacts), diff + `.original` |

```bash
python3 <plugin_root>/observability/profile_engine.py get --workspace . --json
python3 <plugin_root>/observability/profile_engine.py set --workspace . --caveman <auto|evo|off|lite|full|ultra> --json
```
*(Utiliser `python`/`py -3` sous Windows ; stockage `$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/`.)*

## 2. Intensités, invariants absolus & `evo`

- **Niveaux :** `off` (normal), `lite` *(défaut : zéro politesse/blabla, phrases claires)*, `full` *(télégraphique)*, `ultra` *(densité `X -> Y`, manuel uniquement)*.
- **Intégrité :** code, diffs, commandes, chemins, URLs, JSON et erreurs restent **strictement intacts**.
- **Auto-clarté :** prose complète claire obligatoire pour refus/alerte `PreToolUse` / `01-safety-boundaries.md`, confirmation irréversible et question `/grill-me` / `ask_question`.
- **Adaptation `evo` (`caveman_locked: false`) :** priorité **`lite`** si `session_error`, `guard_deny_recent > 0` ou `eval_pass_rate < 1.0` ; passe en **`full`** si `metrics.available` et (`max_context_tokens >= 150_000` ou `high_output_cost_ratio >= 0.35`) — jamais `ultra`/`off` auto.

## 3. ROI sous-agents & utilitaires

- **Chat :** niveau actif · **`planner` :** plafonné `full` · **`reviewer` :** 1 ligne (`chemin:ligne · preuve · règle · fix`, *Preuve ou Silence*) · **`builder`/`swarm` :** zéro narration · **`evals/` :** `off`.
- **`commit` :** jamais `commit`/`push` sans accord (chef d'orchestre suit `.agents/rules/05-git-versioning.md`).
- **`compress` :** interdit sur `.env`, `secrets/`, `hooks/`, `hooks.json`, `01-safety-boundaries.md` sans accord ; diff + gain avant écriture, sauvegarde `<nom>.original<ext>`.
