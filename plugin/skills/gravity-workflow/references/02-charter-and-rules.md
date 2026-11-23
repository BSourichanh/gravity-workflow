# Socle & règles (Context Engineering)

## Objectif
Remplacer toute instruction monolithique par un socle minimal permanent complété par des règles modulaires chargées à la demande dans le projet cible, en optimisant le **Context Caching Gemini** et en respectant les limites natives d'Antigravity.

> [!IMPORTANT]
> **Règle d'or frontmatter :** Toute règle `.md` placée dans `.agents/rules/` **DOIT** déclarer une clé **`trigger`** valide (`always_on`, `model_decision`, `glob`, `manual`). Une syntaxe erronée comme `always_on: true` (sans `trigger:`) ou camelCase (`alwaysOn`) est **silencieusement ignorée** par Antigravity.

## Stratégie de découpage en 4 types de règles

| Type de règle | Frontmatter YAML | Usage & contrainte stricte |
| :--- | :--- | :--- |
| Socle permanent | `trigger: always_on` | Interdits absolus uniquement (`01-safety-boundaries.md`). **Doit rester < 2 Ko**. |
| Portée fichiers | `trigger: glob` | `globs: '**/*.ts, **/*.tsx'` — **chaîne séparée par des virgules obligatoire**, jamais de tableau YAML (`glob:` singulier toléré, préférer `globs:`). Citer les motifs commençant par `*` entre guillemets. |
| Décision modèle | `trigger: model_decision` | Champ **`description:` obligatoire** décrivant exactement quand charger la règle (progressive disclosure). |
| Manuel / Audit | `trigger: manual` | Invoquée explicitement par mention `@rule` (App & IDE uniquement — **non supporté par mention `@` dans le CLI `agy`**). |

## Découverte, inclusions et limites natives Antigravity
- **`AGENTS.md` / `GEMINI.md` :** fichiers Markdown sans frontmatter, toujours actifs (`always_on`). Antigravity remonte du répertoire de travail jusqu'à la racine du dépôt (`.git`), plus les fichiers globaux (`~/.gemini/AGENTS.md`, `~/.gemini/GEMINI.md`, `~/.gemini/config/rules/*.md`), sans jamais descendre dans les sous-dossiers en dessous du workspace root.
- **Scan plat à 1 niveau :** `.agents/rules/` ne scanne que les `.md` directs (les fichiers d'état rangés sous `.agents/gravity-workflow/` ne sont jamais injectés comme règles). Pour des sous-dossiers ou des règles partagées hors dépôt, utiliser un manifeste **`.agents/rules.json`** (comme `skills.json`, `agents.json`, `plugins.json`).
- **Inclusions `@` :** `@[label](path)` inline le contenu du fichier cible (en retirant son frontmatter) avant calcul des limites ; `@filename` réécrit vers un chemin canonique sans inliner le contenu.
- **Plafonds réels (séparés des fenêtres modèle 512K–1M) :**
  - **24 000 octets max par fichier** (tronqué en cas de dépassement après expansion `@[label](path)`).
  - **20 000 tokens agrégés (`defaultRulesBudget`)** pour `AGENTS.md`/`GEMINI.md` + règles globales + `trigger: always_on` (séparé du budget skills/MCP). Au-delà, Antigravity rétrograde automatiquement les plus grosses règles en simples pointeurs `- chemin: description`.
- **Context Caching Gemini (`cache_read_tokens` 10×–20× moins cher) :** garde le préfixe (`AGENTS.md` et règles `always_on`) strictement statique ; n'y injecte jamais d'horodatages, compteurs ou états mouvants.

## Livrables produits dans le projet
1. **`AGENTS.md`** à la racine du projet : sans frontmatter, concis (`<= 2 500` octets, LF), préfixe stable, orienté rôle, livrable, sources de vérité et règles de collaboration (anti-écrasement, priorité outils natifs, revue indépendante, double boucle `/learn`, conventions Git/versioning chef d'orchestre et principes KISS/YAGNI/DRY/SOLID/CUPID).
2. **`.agents/rules/01-safety-boundaries.md`** : `trigger: always_on`, traduit les 3 lignes rouges sous 2 Ko.
3. **`.agents/rules/05-git-versioning.md`** : `trigger: model_decision`, conventions Git & versioning avant `commit`/`push` (branches hors `main`, *Conventional Commits*, SemVer, `CHANGELOG.md`, tags annotés `vX.Y.Z`) réservées exclusivement au chef d'orchestre et déployées automatiquement par `/gravity-workflow` si absentes (modes `quickstart`, `full`, Audit et Reprise).
4. **`.agents/rules/06-engineering-principles.md`** : `trigger: model_decision` (`0` token `always_on`), critères KISS, YAGNI, DRY, SOLID et CUPID déployés en `quickstart` et `full`.
5. **Checklist release & règles opt-in par stack :** `.agents/rules/04-release-checklist.md` (`trigger: manual`, déployée en `--mode full`, avec note de lecture manuelle en CLI) et règles de stack ciblées déployées uniquement sur demande via `--with-stack python` (`02-python-stdlib.md`, `trigger: glob`) ou `--with-stack api` (`03-api-architecture.md`, `trigger: model_decision`).

## Règle anti-écrasement
Si `AGENTS.md` ou des règles préexistent (mode Audit), analyse leur volume, détecte les doublons ou syntaxes invalides (`always_on: true` sans `trigger:`, `globs` en liste YAML) et propose un diff avant toute édition.

## Checkpoint déterministe
- `01-safety-boundaries.md` pèse moins de 2 048 octets et déclare `trigger: always_on`.
- Toutes les règles de `.agents/rules/*.md` ont un `trigger` valide, pèsent <= 24 000 octets et utilisent une chaîne `globs` séparée par des virgules si `trigger: glob`.
- Le volume cumulé actif (`AGENTS.md` + `GEMINI.md` + règles `trigger: always_on`) reste strictement inférieur à 20 000 tokens.
- Mettre à jour `.agents/gravity-workflow/PROGRESS.md` et afficher la micro-carte.
