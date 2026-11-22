# Architecture & Installation

## Principe fondamental : Plugin autonome vs Socle vital projet

Gravity Workflow sépare strictement le moteur du harnais des fichiers propres au projet cible :

1. **Plugin autonome ([`plugin/`](../plugin/)) :** embarque le manifeste `plugin.json` (9 champs officiels Antigravity), le contrat [`config/module.json`](../plugin/config/module.json) (`id="workflow"`), le skill principal `/gravity-workflow` (et ses 10 fiches de référence), le skill compagnon `/caveman`, les 3 sous-agents, les hooks (`hooks.json` + `hooks/*.py`), les workflows, le moteur d'évaluations, le moteur de profil/protocole Caveman/observabilité, le sidecar Live Widget (`4789`, transitoire), le validateur, l'initialiseur et les tests unitaires.
2. **Empreinte projet minimale ([`workspace-template/`](../workspace-template/) — 13 fichiers) :** l'initialisation d'un projet cible ne déploie que `AGENTS.md` et `.agents/` (`mcp_config.json`, règles, suivi `PROGRESS.md`/`BACKLOG.md`, `profile.json`, catalogue `evals/cases.json`, `live_state.json` et `.gitignore`). Aucun dossier visible `gravity-workflow/` n'est créé à la racine du projet.

---

## Arborescence complète

### 1. Plugin (`plugin/`)

```text
plugin/
├── plugin.json                         # Manifeste Antigravity (9 champs officiels, UTF-8 sans BOM)
├── config/
│   └── module.json                     # Contrat module v1 (id="workflow", storage="workflow", ui="transition-sidecar")
├── hooks.json                          # Groupes PreToolUse, PostToolUse, SessionStart et Stop (portables python3)
├── README.md
├── assets/logo.svg                     # SVG carré 1:1
├── agents/
│   ├── planner.md                      # Lecture seule (pro / sandbox)
│   ├── builder.md                      # Branche isolée (flash / sandbox)
│   └── reviewer.md                     # Reviewer indépendant lecture seule (pro / inheritMcp: false)
├── skills/
│   ├── gravity-workflow/
│   │   ├── SKILL.md                    # Chef d'orchestre principal /gravity-workflow
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
│   ├── state_writer.py                 # Écrivain atomique + default_workflow_home() + registre global
│   └── statusline.py                   # Barre /statusline CLI (chmod 0755)
├── sidecars/live-widget/
│   ├── sidecar.json                    # Port 4789, restart_policy: on-failure (transition vers gravity Core UI)
│   └── server.py                       # Serveur HTTP/SSE anti-XSS multi-workspace (chmod 0755)
└── scripts/
    ├── init_workspace.py               # Bootstrapper projet full/quickstart (chmod 0755)
    ├── validate.py                     # CLI validation --strict (chmod 0755)
    ├── test_unit.py                    # Suite de tests unitaires internes (100 % stdlib)
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
    └── gravity-workflow/
        ├── .gitignore                  # Ignore *.lock, runs.jsonl, last_verified.patch, reports
        ├── PROGRESS.md                 # Suivi des 8 étapes + Bonus
        ├── BACKLOG.md                  # Arbitrages différés & nouveaux cas /learn
        ├── profile.json                # Profil 3 axes (evo/medium, slow, safe) + caveman (lite, auto)
        ├── evals/cases.json            # Copie locale des 12 cas d'évaluation
        └── observability/live_state.json
```

---

## Initialisation & détection d'état

À l'appel de `/gravity-workflow` (voir [`SKILL.md`](../plugin/skills/gravity-workflow/SKILL.md)) ou via [`init_workspace.py`](../plugin/scripts/init_workspace.py) :

- **Détection automatique dans l'ordre :**
  1. **Reprise de session** (`.agents/gravity-workflow/PROGRESS.md` présent) : lit `PROGRESS.md` + `profile.json`, vérifie/déploie `.agents/rules/05-git-versioning.md` via `init_workspace.py --ensure-git-rule` s'il est absent, affiche la micro-carte 3 lignes et poursuit la tâche. *(Zéro shim hérité : l'ancien dossier `.agents/gravity/` n'est plus reconnu au runtime.)*
  2. **Mode Audit** (`.agents/` ou `AGENTS.md` présent sans `PROGRESS.md`) : contrôle `trigger:` et le budget `< 20 000` tokens, puis complète `.agents/gravity-workflow/` et `.agents/rules/05-git-versioning.md` (si absent) sans écraser les fichiers existants.
  3. **Démarrage à froid** (workspace vierge) : `low` lance `--mode quickstart`, `high`/`ultra` lance `--mode full`, `medium` laisse le choix.
- **Modes de `init_workspace.py` :**
  - `full` *(défaut)* : déploie les 13 fichiers (`PROGRESS.md` à `0/8` + les 5 modèles de règles).
  - `quickstart` : déploie le socle minimal viable (`AGENTS.md`, `mcp_config.json`, `01-safety-boundaries.md`, `05-git-versioning.md` + `.agents/gravity-workflow/`, `PROGRESS.md` à `Quick Start [~] 3/8`).
  - `--ensure-git-rule` : déploie uniquement `.agents/rules/05-git-versioning.md` s'il est absent d'un projet existant (idempotent).
  - **Anti-écrasement :** aucun fichier existant n'est remplacé sans `--force` après accord explicite.

---

## Installateur cross-plateforme (`install.py`)

[`install.py`](../install.py) installe, met à niveau, valide et désinstalle `plugin/` (Python 3.10+, 100 % stdlib) :

- **Portées :** `--scope global` (`~/.gemini/config/plugins/gravity-workflow/`, défaut) ou `--scope workspace --workspace <dir>` (`<dir>/.agents/plugins/gravity-workflow/`).
- **Méthodes :** `--method copy` *(défaut — copie mise en zone de staging, swap atomique par `os.rename` avec sauvegarde et rollback automatique en cas d'échec, et repli de synchronisation in-place avec rollback en cas de fichier modifié verrouillé puis purge des fichiers obsolètes si un sous-dossier comme `sidecars/live-widget` est verrouillé sous Windows par un processus actif)* ou `--method agy` *(délègue à `agy plugin validate` + `agy plugin install`)*.
- **Nettoyage de migration (`v0.1.x` → `v0.2.0`) :** lors d'une installation `--scope global --method copy` (ou `--scope workspace`), supprime l'ancien plugin workflow `plugins/gravity` **uniquement** s'il ne possède pas `config/module.json` avec `"id": "core"` ET présente des marqueurs workflow hérités (`hooks/pre_tool_guard.py`, `hooks/safety_policy.py` ou `skills/gravity/`). Le plugin **Gravity Core (`gravity`)** n'est **jamais** supprimé (`--keep-legacy` désactive aussi le nettoyage des vrais legacies).
- **Adaptation Windows :** sans `--no-fix-python` et en l'absence d'un binaire `python3` réel (l'alias WindowsApps est ignoré), réécrit `python3` vers `python` ou `py -3` uniquement dans la **copie installée** via `validate.py --fix-python-cmd`, laissant le dépôt source portable.
- **Permissions POSIX :** applique les bits d'exécution équivalents à `0755` sur les 9 scripts déclarés dans `EXEC_SCRIPTS`.
- **Commandes utiles :** `--check`, `--dry-run`, `--force` (mise à jour), `--keep-legacy`, `--init-workspace <dir> [--mode quickstart|full]`, `--uninstall`.

---

## Désinstalleur cross-plateforme (`uninstall.py`)

[`uninstall.py`](../uninstall.py) complète `install.py` (Python 3.10+, 100 % stdlib) pour retirer Gravity Workflow proprement sans toucher aux fichiers personnalisés ni au plugin `gravity` Core, ni modifier automatiquement `config.json` / `settings.json` :

- **Suppression du plugin :** `--scope global` (`~/.gemini/config/plugins/gravity-workflow/`), `--scope workspace --workspace <dir>`, `--target <dir>` ou `--method agy` (`agy plugin uninstall gravity-workflow`), avec refus sur toute cible non-Gravity Workflow, `$HOME`, racine disque ou répertoire du dépôt source (`plugin/`, `workspace-template/`).
- **Nettoyage projet (`--clean-workspace <dir>`) :** supprime `.agents/gravity-workflow/` (et `.agents/plugins/gravity-workflow/` si valide) et retire l'entrée du projet dans le registre centralisé (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/live_workspaces.json`) sans jamais supprimer `AGENTS.md`, `.agents/rules/*` ni `.agents/mcp_config.json`. L'option opt-in `--purge-workspace-templates` ne supprime les fichiers modèles que s'ils sont **strictement identiques** aux originaux d'[`init_workspace.py`](../plugin/scripts/init_workspace.py).
- **Nettoyage hérité (`--clean-legacy`, activé automatiquement par `--all`) :** détecte et supprime l'ancien registre `~/.gravity/live_workspaces.json`, les dossiers hérités `.agents/gravity/` et un ancien plugin workflow `.agents/plugins/gravity/` (sans jamais supprimer un plugin `gravity` Core portant `config/module.json` `"id": "core"`).
- **Registre & diagnostic :** `--clean-registry` supprime `~/.gemini/gravity/workflow/live_workspaces.json` (ou `$GRAVITY_WORKFLOW_HOME` / `$GRAVITY_HOME/workflow`, et le dossier `workflow/` s'il est vide) ; `--all` combine plugin + registre + artefacts hérités (+ `--clean-workspace` si fourni) ; `--check` inspecte en lecture seule (y compris la présence éventuelle de `gravity-workflow/live-widget`, du legacy `gravity/live-widget` dans `config.json` et de `statusLine` dans `settings.json`) et `--dry-run` simule sans écriture.
- **Note de transition UI :** le sidecar `live-widget` (`http://127.0.0.1:4789`, `"ui": {"mode": "transition-sidecar", "target": "gravity"}` dans `plugin/config/module.json`) est une surface de transition destinée à converger dans l'UI unifiée du plugin Core `gravity`.
