# Cadrage du projet (Interview `/grill-me`)

## Objectif
Ancrer le harnais sur un cas d'usage concret, un livrable mesurable, des limites non négociables et le contexte exact du compte Antigravity avant de personnaliser les règles du projet.

## Protocole d'interview (inspiré de `/grill-me`)
1. **Explorer d'abord le dépôt :** lis les fichiers existants (`AGENTS.md`, `.agents/`, `pyproject.toml`, `package.json`, structure Git) au lieu de poser des questions dont le code détient déjà la réponse.
2. **Une question à la fois (via `ask_question`) :** place l'option recommandée en premier préfixée par `(Recommended)`. L'utilisateur peut aussi lancer directement `/plan /grill-me` pour verrouiller un plan d'implémentation avant toute écriture.
3. **Usage continu hors initialisation :** ce même protocole s'applique aussi en cours de projet avant toute nouvelle tâche, fonctionnalité ou refactorisation ambiguë (voir `references/05-workflows-and-routing.md`).

Conduis l'échange autour de 5 axes obligatoires :
1. **Domaine & cas d'usage** : quel processus répétitif ou critique les agents doivent-ils exécuter ?
2. **Livrable principal** : quel artefact concret est attendu en sortie (Pull Request, patch de code, rapport Markdown, audit JSON, migration) ?
3. **Sources de vérité** : quels documents, schémas, tests ou référentiels font foi en cas de doute ?
4. **3 Lignes rouges absolues** : quels sont les 3 interdits formels qu'aucun agent ne doit jamais franchir (ex. accès aux secrets, commandes destructrices, modification non autorisée de production) ?
5. **Plan, modèles & surfaces cibles** : l'équipe utilise-t-elle **Free/Plus** (`Gemini 3.8 Flash` uniquement côté Gemini), **Google AI Pro/Ultra** (`Gemini 4 Neon`, `Carbon`, `Argon`, `Hydrogen`, `Orchestrator`, `/boost`, `/teamwork`), **Gemini Enterprise / Pay As You Go**, ou **`GEMINI_API_KEY`** ? Quelles surfaces sont visées (App, CLI `agy`, IDE) ? Ce choix conditionne l'usage de `model: pro` vs `inherit`, du sidecar (App uniquement) et de `trigger: manual` (absent du CLI).

## Règle de gestion des ambiguïtés
Toute question ouverte, idée hors périmètre ou arbitrage différé durant l'interview est immédiatement consigné dans `.agents/gravity-workflow/BACKLOG.md` afin de ne pas bloquer l'avancement.

## Livrables produits dans le projet
1. **`.agents/gravity-workflow/PROGRESS.md`** : consigne les réponses du cadrage, coche l'étape *Cadrage du projet* et affiche la progression.
2. **`.agents/gravity-workflow/BACKLOG.md`** : centralise les questions ouvertes et améliorations futures.
3. **`.agents/gravity-workflow/.gitignore`** (créé par `init_workspace.py`) : ignore automatiquement les fichiers temporaires d'exécution (`observability/*.lock`, `observability/runs.jsonl`, `observability/last_verified.patch`, `evals/report.*`).
4. **`.gitignore` racine** : vérifie (via diff validé si le fichier existe) que les secrets (`.env`, `secrets/`, `*.pem`, `*.key`) ne seront jamais versionnés.

## Checkpoint déterministe
- Les sections Domaine, Livrable, Sources de vérité, 3 Lignes rouges et Profil Antigravity sont renseignées sans champ vide dans `.agents/gravity-workflow/PROGRESS.md`.
- `.agents/gravity-workflow/BACKLOG.md` et `.agents/gravity-workflow/.gitignore` existent.
- `.gitignore` racine protège les fichiers sensibles.
- Afficher la micro-carte 3 lignes et proposer l'étape suivante (*Socle & règles*).
