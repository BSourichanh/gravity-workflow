# Architecture & Installation

## Principe fondamental : Plugin autonome vs Socle vital projet

Gravity sépare strictement le moteur du harnais des fichiers propres au projet cible :

1. **Plugin autonome ([`gravity-plugin/`](../gravity-plugin/) — 42 fichiers) :** embarque le manifeste, le skill principal `/gravity` (et ses 10 fiches de référence), le skill compagnon `/caveman`, les 3 sous-agents, les hooks (`hooks.json` + `hooks/*.py`), les workflows, le moteur d'évaluations, le moteur de profil/protocole Caveman/observabilité, le sidecar Live Widget, le validateur, l'initialiseur et les tests unitaires.
2. **Empreinte projet minimale ([`workspace-template/`](../workspace-template/) — 13 fichiers) :** l'initialisation d'un projet cible ne déploie que `AGENTS.md` et `.agents/` (`mcp_config.json`, règles, suivi `PROGRESS.md`/`BACKLOG.md`, `profile.json`, catalogue `evals/cases.json`, `live_state.json` et `.gitignore`). Aucun dossier visible `gravity/` n'est créé à la racine du projet.

---

## Arborescence complète

### 1. Plugin (`gravity-plugin/`)

```text
gravity-plugin/
├── plugin.json                         # Manifeste Antigravity (name="gravity", 3 suggestedPrompts)
├── hooks.json                          # Groupes PreToolUse, PostToolUse, SessionStart et Stop (portables python3)
├── README.md
├── assets/logo.svg                     # SVG carré 1:1
├── agents/
│   ├── planner.md                      # Lecture seule (pro / sandbox)
│   ├── builder.md                      # Branche isolée (flash / sandbox)
│   └── reviewer.md                     # Reviewer indépendant lecture seule (pro / inheritMcp: false)
├── skills/
│   ├── gravity/
│   │   ├── SKILL.md                    # Chef d'orchestre principal /gravity
│   │   └── references/                 # 10 fiches chargées à la demande (00 à 09)
│   └── caveman/
│       └── SKILL.md                    # Skill compagnon /caveman (intensités, commit, compress)
├── hooks/
│   ├── hook_runtime.py                 # Stdin threadé, emit_ok/emit_json, résolution workspace, verrous
│   ├── safety_policy.py                # Règles pures deny / force_ask / allow
│   ├── pre_tool_guard.py               # Hook PreToolUse fail-closed (chmod 0755)
│   ├── post_tool_format.py             # Hook PostToolUse auto-formatage (chmod 0755)
│   ├── session_start_caveman.py        # Hook SessionStart injection Caveman (chmod 0755)
│   └── stop_session_logger.py          # Hook Stop runs.jsonl + evolve_on_stop (chmod 0755)
├── workflows/
│   └── review_pipeline.py              # Pipeline Planner -> Builder (branch) -> Reviewer indépendant
├── evals/
│   ├── cases.json                      # Catalogue starter de 12 cas
│   ├── eval_checks.py                  # Prédicats AST-whitelist et rendu Markdown
│   └── run_suite.py                    # Runner parallèle isolé pour run_workflow
├── observability/
│   ├── caveman_protocol.py             # Règles Caveman, adaptation evo et directives sous-agents
│   ├── profile_engine.py               # Moteur 3 axes + adaptation evo + Caveman (chmod 0755)
│   ├── state_writer.py                 # Écrivain atomique + verrou portable + registre global
│   └── statusline.py                   # Barre /statusline CLI (chmod 0755)
├── sidecars/live-widget/
│   ├── sidecar.json                    # Port 4789, restart_policy: on-failure
│   └── server.py                       # Serveur HTTP/SSE anti-XSS multi-workspace (chmod 0755)
└── scripts/
    ├── init_workspace.py               # Bootstrapper projet full/quickstart (chmod 0755)
    ├── validate.py                     # CLI validation --strict (chmod 0755)
    ├── test_unit.py                    # 24 tests unitaires internes (100 % stdlib)
    └── validators/                     # base.py, plugin_validator.py, workspace_validator.py
```

### 2. Socle projet (`workspace-template/`)

```text
workspace-template/
├── AGENTS.md                           # Charte projet au préfixe statique (Context Caching)
└── .agents/
    ├── mcp_config.json                 # Connecteurs MCP du projet
    ├── rules/
    │   ├── 01-safety-boundaries.md     # trigger: always_on (< 2 Ko)
    │   ├── 02-python-stdlib.md         # trigger: glob ('**/*.py')
    │   ├── 03-api-architecture.md      # trigger: model_decision
    │   ├── 04-release-checklist.md     # trigger: manual
    │   └── 05-git-versioning.md        # trigger: model_decision (chef d'orchestre uniquement)
    └── gravity/
        ├── .gitignore                  # Ignore *.lock, runs.jsonl, last_verified.patch, reports
        ├── PROGRESS.md                 # Suivi des 8 étapes + Bonus
        ├── BACKLOG.md                  # Arbitrages différés & nouveaux cas /learn
        ├── profile.json                # Profil 3 axes (evo/medium, slow, safe) + caveman (lite, auto)
        ├── evals/cases.json            # Copie locale des 12 cas d'évaluation
        └── observability/live_state.json
```

---

## Initialisation & détection d'état

À l'appel de `/gravity` (voir [`SKILL.md`](../gravity-plugin/skills/gravity/SKILL.md)) ou via [`init_workspace.py`](../gravity-plugin/scripts/init_workspace.py) :

- **Détection automatique dans l'ordre :**
  1. **Reprise de session** (`.agents/gravity/PROGRESS.md` présent) : lit `PROGRESS.md` + `profile.json`, vérifie/déploie `.agents/rules/05-git-versioning.md` via `init_workspace.py --ensure-git-rule` s'il est absent, affiche la micro-carte 3 lignes et poursuit la tâche.
  2. **Mode Audit** (`.agents/` ou `AGENTS.md` présent sans `PROGRESS.md`) : contrôle `trigger:` et le budget `< 20 000` tokens, puis complète `.agents/gravity/` et `.agents/rules/05-git-versioning.md` (si absent) sans écraser les fichiers existants.
  3. **Démarrage à froid** (workspace vierge) : `low` lance `--mode quickstart`, `high`/`ultra` lance `--mode full`, `medium` laisse le choix.
- **Modes de `init_workspace.py` :**
  - `full` *(défaut)* : déploie les 13 fichiers (`PROGRESS.md` à `0/8` + les 5 modèles de règles).
  - `quickstart` : déploie le socle minimal viable (`AGENTS.md`, `mcp_config.json`, `01-safety-boundaries.md`, `05-git-versioning.md` + `.agents/gravity/`, `PROGRESS.md` à `Quick Start [~] 3/8`).
  - `--ensure-git-rule` : déploie uniquement `.agents/rules/05-git-versioning.md` s'il est absent d'un projet existant (idempotent).
  - **Anti-écrasement :** aucun fichier existant n'est remplacé sans `--force` après accord explicite.

---

## Installateur cross-plateforme (`install.py`)

[`install.py`](../install.py) installe, met à niveau, valide et désinstalle `gravity-plugin/` (Python 3.8+, 100 % stdlib) :

- **Portées :** `--scope global` (`~/.gemini/config/plugins/gravity/`, défaut) ou `--scope workspace --workspace <dir>` (`<dir>/.agents/plugins/gravity/`).
- **Méthodes :** `--method copy` *(défaut — copie mise en zone de staging avec sauvegarde et rollback automatique en cas d'échec)* ou `--method agy` *(délègue à `agy plugin validate` + `agy plugin install`)*.
- **Adaptation Windows :** sans `--no-fix-python` et en l'absence d'un binaire `python3` réel (l'alias WindowsApps est ignoré), réécrit `python3` vers `python` ou `py -3` uniquement dans la **copie installée** via `validate.py --fix-python-cmd`, laissant le dépôt source portable.
- **Permissions POSIX :** applique les bits d'exécution équivalents à `0755` sur les 9 scripts déclarés dans `EXEC_SCRIPTS`.
- **Commandes utiles :** `--check`, `--dry-run`, `--force` (mise à jour), `--init-workspace <dir> [--mode quickstart|full]`, `--uninstall`.

---

## Désinstalleur cross-plateforme (`uninstall.py`)

[`uninstall.py`](../uninstall.py) complète `install.py` (Python 3.8+, 100 % stdlib) pour retirer Gravity proprement sans toucher aux fichiers personnalisés ni modifier automatiquement `config.json` / `settings.json` :

- **Suppression du plugin :** `--scope global` (`~/.gemini/config/plugins/gravity/`), `--scope workspace --workspace <dir>`, `--target <dir>` ou `--method agy` (`agy plugin uninstall gravity`), avec refus sur toute cible non-Gravity, `$HOME`, racine disque ou répertoire du dépôt source (`gravity-plugin/`, `workspace-template/`).
- **Nettoyage projet (`--clean-workspace <dir>`) :** supprime `.agents/gravity/` (et `.agents/plugins/gravity/` si valide) et retire l'entrée du projet dans `~/.gravity/live_workspaces.json` sans jamais supprimer `AGENTS.md`, `.agents/rules/*` ni `.agents/mcp_config.json`. L'option opt-in `--purge-workspace-templates` ne supprime les fichiers modèles que s'ils sont **strictement identiques** aux originaux d'[`init_workspace.py`](../gravity-plugin/scripts/init_workspace.py).
- **Registre & diagnostic :** `--clean-registry` supprime `~/.gravity/live_workspaces.json` (et `~/.gravity/` s'il est vide) ; `--all` combine plugin + registre (+ `--clean-workspace` si fourni) ; `--check` inspecte en lecture seule (y compris la présence éventuelle de `gravity/live-widget` dans `config.json` et `statusLine` dans `settings.json`) et `--dry-run` simule sans écriture.
