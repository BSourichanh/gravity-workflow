---
name: caveman
description: Mode de communication ultra-concis Caveman pour réduire les tokens (off, lite, full, ultra, auto/evo), messages commit et compression contexte.
---

# Gravity Workflow Caveman — Concision ROI & Zéro Blabla

*(Skill compagnon dédié de Gravity Workflow invoqué via `/caveman` ou `/gravity-workflow:caveman` en cas de collision.)*

---

## 1. Grammaire d'invocation

| Commande | Action sur le profil centralisé (`profile.json`) | Comportement |
| :--- | :--- | :--- |
| **`/caveman`** *(seul)* | Si déjà `full` → lecture seule ; sinon `set --caveman full` | Active le mode `full` (verrouillé) ou affiche l'état si déjà `full` |
| **`/caveman lite\|full\|ultra\|off`** | `profile_engine.py set --workspace . --caveman <niveau>` (`caveman_locked: true`) | Verrouille l'intensité choisie et confirme en 1 ligne |
| **`/caveman auto`** *(alias : `evo`)* | `profile_engine.py set --workspace . --caveman auto` (`caveman_locked: false`) | Rend la main au défaut nominal `lite` (`full` auto réservé aux signaux contexte/coût élevé Phase 3) |
| **`/caveman status`** | Lecture seule (`profile_engine.py get --workspace . --json`) | Affiche niveau actif, verrou (`auto/evo` vs `verrouillé`), badge et matrice ROI |
| **`/caveman commit`** | Aucune | Rédige un message *Conventional Commits* (`<= 50` car. sur le titre) centré sur le **pourquoi** depuis `git diff --staged` (ou `git diff`) |
| **`/caveman compress <fichier>`** | Aucune | Condense un fichier Markdown de contexte en gardant règles, frontmatter, code et URLs intacts, avec diff validé + sauvegarde `.original` |

> **Stockage centralisé :** le réglage est persisté sous `$GRAVITY_HOME/workflow/workspaces/<hash>/profile.json` (zéro fichier écrit dans le workspace cible). Si le workspace possède un ancien `.agents/gravity-workflow/profile.json`, il est lu en repli lecture seule.

```bash
# Lire l'état Caveman et le preset résolu :
python3 <plugin_root>/observability/profile_engine.py get --workspace . --json

# Modifier le réglage Caveman :
python3 <plugin_root>/observability/profile_engine.py set --workspace . --caveman <auto|evo|off|lite|full|ultra> --json
```
*(Utiliser `python` ou `py -3` sous Windows si `python3` est absent du `PATH`.)*

---

## 2. Intensités & règles de style (FR / EN)

| Niveau | Règle de rédaction | Usage recommandé |
| :--- | :--- | :--- |
| **`off`** | Prose normale sans restriction | Débogage délicat ou préférence utilisateur |
| **`lite`** *(défaut sur tous profils)* | Supprime politesse, intro/outro, formules de transition et remplissage ; garde phrases complètes claires | Défaut nominal, lisible et sans risque |
| **`full`** | Style télégraphique direct sans articles ni connecteurs superflus ; fragments précis acceptés | Réservé au verrou manuel (`/caveman full`) ou futur signal contexte/coût élevé Phase 3 |
| **`ultra`** | Densité maximale : abréviations techniques (`DB`, `auth`, `fn`, `cfg`, `impl`), causalité `X -> Y`, zéro blabla | Réservé au verrou manuel (`/caveman ultra`) en chat principal |

### Invariants absolus (jamais modifiés quel que soit le niveau)
1. **Intégrité technique :** code source, blocs de code, diffs, commandes shell, chemins de fichiers, URLs, schémas/payloads JSON et messages d'erreur restent **strictement intacts**.
2. **Auto-clarté obligatoire :** repasse immédiatement en **prose complète et explicite** pour :
   - tout refus ou alerte de sécurité `PreToolUse` / `01-safety-boundaries.md` ;
   - toute demande de confirmation avant action destructrice ou irréversible ;
   - toute question d'arbitrage `/grill-me` ou `ask_question`.
3. **Adaptation `evo` (`caveman_locked: false`) :**
   - **`lite`** par défaut sur les 3 profils (`quick`, `standard`, `deep`) et en cas d'instabilité (`session_error`, alerte `deny`, ou `eval_pass_rate < 1.0`).
   - Passage automatique en **`full`** réservé à la Phase 3 sur signaux de contexte/coût élevé sains *(jamais `ultra` ni `off` automatiquement)*.

---

## 3. Propagation ciblée par ROI (sous-agents, `swarm` & `evals`)

| Cible | Comportement Caveman | Raison |
| :--- | :--- | :--- |
| **Chat principal** | Applique le niveau actif (`off`/`lite`/`full`/`ultra`) | Principal levier de réduction des tokens output et d'historique |
| **`planner`** | **Plafonné à `full`** (`ultra` -> `full`) | Évite toute ambiguïté transmise au `builder` |
| **`reviewer` indépendant** | Format compact 1 ligne (`chemin:ligne · preuve · règle · fix`) | Économise des tokens `pro` en gardant *Preuve ou Silence* intact |
| **`builder` & `swarm`** | Zéro narration entre outils ; `summary` et posts Swarm secs | Réduit le bruit partagé sur le board Swarm |
| **`evals/`** | **Strictement exclu (`off`)** | Évite tout biais sur les assertions déterministes et le juge |

---

## 4. Procédures utilitaires (`commit` & `compress`)

### `/caveman commit`
1. Inspecte `git status --short` et `git diff --staged` (ou `git diff` si rien n'est indexé).
2. Propose un titre *Conventional Commits* (`<type>(<scope>): <impératif <= 50 car.>`) centré sur le **pourquoi**, suivi si nécessaire de puces courtes (`<= 72` car./ligne).
3. Ne lance jamais `git commit` ou `git push` sans demande explicite ; côté **chef d'orchestre** uniquement, la préparation complète avant commit/push suit `.agents/rules/05-git-versioning.md` (branches hors `main`, SemVer, `CHANGELOG.md`, tags annotés `vX.Y.Z`).

### `/caveman compress <fichier>`
1. **Sécurité & anti-écrasement :** refuse de modifier `.env`, `secrets/`, `hooks.json`, `hooks/` ou `.agents/rules/01-safety-boundaries.md` sans accord humain explicite.
2. Lit `<fichier>` (cible typique : `AGENTS.md`, règle `.agents/rules/*.md` verbose, doc Markdown) et prépare une version condensée :
   - conserve intact le frontmatter YAML (`trigger:`, `globs:`, `description:`), les blocs de code, les chemins, les URLs et **toutes** les exigences métier/sécurité ;
   - supprime uniquement le verbiage et les répétitions.
3. Affiche le **diff complet** et l'estimation d'économie avant toute écriture ; après validation utilisateur, sauvegarde d'abord l'original en `<nom>.original<ext>` (sans écraser un `.original` existant sans accord) puis applique l'édition.
