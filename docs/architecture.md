# Architecture & Installation

## Principe fondamental : Plugin autonome vs Socle vital projet

Gravity Workflow sépare strictement le moteur du harnais des fichiers propres au projet cible :

1. **Plugin autonome ([`plugin/`](../plugin/)) :** embarque le manifeste `plugin.json` (9 champs officiels Antigravity), le contrat [`config/module.json`](../plugin/config/module.json) (`id="workflow"`), le skill principal `/gravity-workflow` (et ses 10 fiches de référence), le skill compagnon `/caveman`, les 3 sous-agents, les hooks (`hooks.json` + `hooks/*.py`), les workflows, le moteur d'évaluations, le moteur de profil/protocole Caveman/observabilité, le validateur, l'initialiseur et les tests unitaires.
2. **Empreinte projet minimale ([`workspace-template/`](../workspace-template/) — 14 fichiers de référence) :** l'initialisation d'un projet cible ne déploie que `AGENTS.md` et `.agents/` (`mcp_config.json`, règles socle + stacks opt-in via `--with-stack`, suivi `PROGRESS.md`/`BACKLOG.md`, `profile.json`, catalogue `evals/cases.json`, `live_state.json` et `.gitignore`). Aucun dossier visible `gravity-workflow/` n'est créé à la racine du projet.

---

## Arborescence complète

### 1. Plugin (`plugin/`)

```text
plugin/
├── plugin.json                         # Manifeste Antigravity (9 champs officiels, UTF-8 sans BOM)
├── config/
│   └── module.json                     # Contrat module v1 (id="workflow", storage="workflow", hôte UI MCP App unifié /gravity dashboard)
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
│       ├── SKILL.md                    # Skill compagnon /caveman (intensités, commit, compress)
│       └── references/
│           └── style-examples.md       # Exemples comparatifs FR/EN (lite / full / ultra / commit)
├── hooks/
│   ├── hook_runtime.py                 # Stdin threadé, emit_ok/emit_json, résolution workspace, verrous
│   ├── safety_policy.py                # Façade rétrocompatible (<= 220 L) vers policy/
│   ├── policy/                         # 7 sous-modules SRP deny / force_ask / allow
│   │   ├── __init__.py
│   │   ├── constants.py                # Tables statiques, regex, secrets, destructeurs
│   │   ├── paths.py                    # Normalisation chemins, protections plugin/harnais/secrets
│   │   ├── shell_parser.py             # Tokenisation PowerShell/POSIX, sous-expressions, redirections
│   │   ├── git_and_fs.py               # Règles Git (force-push/config -c) et rm/secrets/DB
│   │   ├── python_ast.py               # Inspection AST Python découpée en helpers par famille
│   │   ├── inline_scripts.py           # Extraction/inspection python -c, node -e, perl/ruby, stdin
│   │   └── evaluator.py                # Orchestration _evaluate_shell_command et décisions fail-closed
│   ├── pre_tool_guard.py               # Hook PreToolUse fail-closed (chmod 0755)
│   ├── post_tool_format.py             # Hook PostToolUse auto-formatage (chmod 0755)
│   ├── session_start_caveman.py        # Hook SessionStart injection Caveman (chmod 0755)
│   └── stop_session_logger.py          # Hook Stop runs.jsonl + evolve_on_stop (chmod 0755)
├── workflows/
│   ├── patch_apply.py                  # Helpers git worktree, extraction diff & CLI --apply
│   ├── review_pipeline.py              # Pipeline Planner -> Builder -> Reviewer + plan_file/watchdogs
│   └── wave_runner.py                  # Runner parallèle fail-isolated multi-dépôts (< 600 L)
├── evals/
│   ├── cases.json                      # Catalogue starter de 12 cas auto + 5 [manual]
│   ├── eval_checks.py                  # Prédicats AST-whitelist et rendu Markdown
│   └── run_suite.py                    # Runner parallèle isolé pour run_workflow
├── observability/
│   ├── caveman_protocol.py             # Règles Caveman, adaptation evo et directives sous-agents
│   ├── metrics_signals.py              # Adaptateur RO gravity-metrics (delta [-5, +10])
│   ├── profile_engine.py               # Moteur 3 axes + adaptation evo + Caveman (chmod 0755)
│   ├── state_writer.py                 # Écrivain atomique + default_workflow_home() + registre global
│   └── statusline.py                   # Barre /statusline CLI (chmod 0755)
├── templates/                          # Source de vérité unique des templates statiques (UTF-8 sans BOM, LF)
│   ├── AGENTS.md
│   └── .agents/
│       ├── mcp_config.json
│       ├── rules/
│       │   ├── 01-safety-boundaries.md.tmpl
│       │   ├── 02-python-stdlib.md     # Opt-in --with-stack python
│       │   ├── 03-api-architecture.md  # Opt-in --with-stack api
│       │   ├── 04-release-checklist.md
│       │   ├── 05-git-versioning.md
│       │   └── 06-engineering-principles.md
│       └── gravity-workflow/{.gitignore,BACKLOG.md,profile.json}
└── scripts/
    ├── init_workspace.py               # Bootstrapper projet full/quickstart + --with-stack (chmod 0755)
    ├── validate.py                     # CLI validation --strict (chmod 0755)
    ├── test_unit.py                    # Lanceur rétrocompatible vers tests/test_*.py (<= 50 L, 100 % stdlib)
    └── validators/                     # base.py, plugin_validator.py, workspace_validator.py
```

### 2. Socle projet (`workspace-template/`)

```text
workspace-template/
├── AGENTS.md                           # Charte projet au préfixe statique (<= 2 500 o, LF)
└── .agents/
    ├── mcp_config.json                 # Connecteurs MCP du projet
    ├── rules/
    │   ├── 01-safety-boundaries.md     # trigger: always_on (< 2 Ko)
    │   ├── 02-python-stdlib.md         # trigger: glob ('**/*.py') — opt-in --with-stack python
    │   ├── 03-api-architecture.md      # trigger: model_decision — opt-in --with-stack api
    │   ├── 04-release-checklist.md     # trigger: manual
    │   ├── 05-git-versioning.md        # trigger: model_decision (chef d'orchestre uniquement)
    │   └── 06-engineering-principles.md # trigger: model_decision (KISS, YAGNI, DRY, SOLID, CUPID)
    └── gravity-workflow/
        ├── .gitignore                  # Ignore *.lock, runs.jsonl, last_verified.patch, reports
        ├── PROGRESS.md                 # Suivi (8 étapes + 1 bonus, 9 cases)
        ├── BACKLOG.md                  # Arbitrages différés & nouveaux cas /learn
        ├── profile.json                # Profil v2 (evo consultatif, standard, caveman: lite)
        ├── evals/cases.json            # Copie locale des 12 cas auto + 5 [manual] d'évaluation
        └── observability/live_state.json
```

---

## Initialisation, cycle de vie global & stockage centralisé

- **Cycle de vie actif partout :** `SessionStart`, `Stop`, `Live` et `profile_engine` s'exécutent sur tous les dépôts sans nécessiter `/gravity-workflow init`.
- **Zéro écriture cible par défaut :** tout l'état runtime (`profile.json`, `live_state.json`, `runs.jsonl`, `last_verified.patch`, `evals/report.*`) est centralisé sous `$GRAVITY_WORKFLOW_HOME/workspaces/<slug>-<sha256_8>/` (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/`), avec repli lecture seule sur `<ws>/.agents/gravity-workflow/profile.json` si présent.

À l'appel explicite de `/gravity-workflow init` ou via [`init_workspace.py`](../plugin/scripts/init_workspace.py) :

- **Détection automatique dans l'ordre :**
  1. **Reprise de session** (`.agents/gravity-workflow/PROGRESS.md` présent) : lit `PROGRESS.md` + profil centralisé, vérifie/déploie `.agents/rules/05-git-versioning.md` via `init_workspace.py --ensure-git-rule` s'il est absent, affiche la micro-carte 3 lignes et poursuit la tâche. *(Zéro shim hérité : l'ancien dossier `.agents/gravity/` n'est plus reconnu au runtime.)*
  2. **Mode Audit** (`.agents/` ou `AGENTS.md` présent sans `PROGRESS.md`) : contrôle `trigger:` et le budget `< 20 000` tokens, puis complète `.agents/gravity-workflow/` et `.agents/rules/05-git-versioning.md` (si absent) sans écraser les fichiers existants.
  3. **Démarrage à froid** (workspace vierge) : `quick` lance `--mode quickstart`, `deep` lance `--mode full`, `standard` laisse le choix.
- **Modes de `init_workspace.py` :** *(source unique [`plugin/templates/`](../plugin/templates/), écriture UTF-8 sans BOM en LF ; `PROGRESS.md` et `live_state.json` générés dynamiquement)*
  - `full` *(défaut)* : déploie le socle complet hors stacks spécifiques (12 fichiers : `PROGRESS.md` à `0/8` + `01-safety-boundaries.md`, `04-release-checklist.md`, `05-git-versioning.md`, `06-engineering-principles.md`).
  - `quickstart` : déploie le socle minimal viable (11 fichiers : `AGENTS.md`, `mcp_config.json`, `01-safety-boundaries.md`, `05-git-versioning.md`, `06-engineering-principles.md` + `.agents/gravity-workflow/`, `PROGRESS.md` à `Quick Start [~] 3/8 (8 étapes + 1 bonus)`).
  - `--with-stack {python,api}` *(répétable, opt-in YAGNI)* : ajoute à la demande `02-python-stdlib.md` (`python`) et/ou `03-api-architecture.md` (`api`).
  - `--ensure-git-rule` : déploie uniquement `.agents/rules/05-git-versioning.md` s'il est absent d'un projet existant (idempotent).
  - **Anti-écrasement :** aucun fichier existant n'est remplacé sans `--force` après accord explicite.

---

## Installateur & désinstalleur unifiés (`install.py`, wrapper `uninstall.py`)

[`install.py`](../install.py) installe, met à niveau, valide et désinstalle `plugin/` ainsi que l'état projet/registre/hérité (Python 3.10+, 100 % stdlib) sans toucher aux fichiers personnalisés, au plugin `gravity` Core ni aux fichiers `config.json` / `settings.json` :

- **Portées :** `--scope global` (`~/.gemini/config/plugins/gravity-workflow/`, défaut) ou `--scope workspace --workspace <dir>` (`<dir>/.agents/plugins/gravity-workflow/`).
- **Méthodes :** `--method copy` *(défaut — copie mise en zone de staging, swap atomique par `os.rename` avec sauvegarde et rollback automatique en cas d'échec, et repli de synchronisation in-place avec rollback en cas de fichier modifié verrouillé puis purge des fichiers obsolètes si un sous-dossier comme `hooks/` est verrouillé sous Windows par un processus actif)* ou `--method agy` *(délègue à `agy plugin validate|install|uninstall`)*.
- **Nettoyage de migration (`v0.1.x` → `v0.2.0`) :** lors d'une installation `--scope global --method copy` (ou `--scope workspace`), supprime l'ancien plugin workflow `plugins/gravity` **uniquement** s'il ne possède pas `config/module.json` avec `"id": "core"` ET présente des marqueurs workflow hérités (`hooks/pre_tool_guard.py`, `hooks/safety_policy.py` ou `skills/gravity/`). Le plugin **Gravity Core (`gravity`)** n'est **jamais** supprimé (`--keep-legacy` désactive aussi le nettoyage des vrais legacies).
- **Adaptation Windows :** sans `--no-fix-python` et en l'absence d'un binaire `python3` réel (l'alias WindowsApps est ignoré), réécrit `python3` vers `python` ou `py -3` uniquement dans la **copie installée** via `validate.py --fix-python-cmd`, laissant le dépôt source portable.
- **Permissions POSIX :** applique les bits d'exécution équivalents à `0755` sur les 8 scripts déclarés dans `EXEC_SCRIPTS`.
- **Installation & diagnostic :** `--check`, `--dry-run`, `--force` (mise à jour), `--strict` (validation stricte active par défaut, vérifie aussi la source sous `--check`), `--keep-legacy`, `--init-workspace <dir> [--mode quickstart|full]`.
- **Désinstallation & nettoyage unifiés (`--uninstall` / `--workspace-only`) :**
  - **Suppression du plugin (`--uninstall`) :** `--scope global|workspace`, `--target <dir>` ou `--method agy` (`agy plugin uninstall gravity-workflow`), avec refus sur toute cible non-Gravity Workflow, `$HOME`, racine disque ou répertoire du dépôt source (`plugin/`, `workspace-template/`).
  - **Nettoyage projet (`--clean-workspace <dir>`) :** supprime `.agents/gravity-workflow/` (et `.agents/plugins/gravity-workflow/` si valide) et retire l'entrée du projet dans le registre centralisé (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/live_workspaces.json`) sans jamais supprimer `AGENTS.md`, `.agents/rules/*` ni `.agents/mcp_config.json`. L'option opt-in `--purge-workspace-templates` ne supprime les fichiers modèles que s'ils sont **strictement identiques** aux originaux de [`plugin/templates/`](../plugin/templates/).
  - **Nettoyage hérité (`--clean-legacy`, activé automatiquement par `--all`) :** détecte et supprime l'ancien registre `~/.gravity/live_workspaces.json`, les dossiers hérités `.agents/gravity/` et un ancien plugin workflow `plugins/gravity/` (sans jamais supprimer un plugin `gravity` Core portant `config/module.json` `"id": "core"`).
  - **Registre & diagnostic :** `--clean-registry` supprime `~/.gemini/gravity/workflow/live_workspaces.json` (ou `$GRAVITY_WORKFLOW_HOME` / `$GRAVITY_HOME/workflow`, et le dossier `workflow/` s'il est vide) ; `--all` combine plugin + registre + artefacts hérités (+ `--clean-workspace` si fourni) ; `--uninstall --check` / `--workspace-only --check` inspecte en lecture seule (y compris les anciens vestiges `gravity-workflow/live-widget` ou `gravity/live-widget` dans `config.json` et `statusLine` dans `settings.json`) et `--dry-run` simule sans écriture.
  - **Wrapper déprécié [`uninstall.py`](../uninstall.py) (`<= 50 L`) :** affiche `[DEPRECATED]` sur `stderr`, injecte `--uninstall` si ni `--uninstall` ni `--workspace-only` n'est présent, puis délègue à `install.main()`.
- **Migration UI :** l'ancien sidecar transitoire `live-widget` (port `4789`, retiré) a convergé vers l'hôte UI MCP App unifié `/gravity dashboard` du plugin Core `gravity` (`"ui": {"mode": "none", "target": "gravity"}` dans `plugin/config/module.json`).
