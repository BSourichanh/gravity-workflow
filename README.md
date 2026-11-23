# Gravity Workflow

> **Ground your Antigravity agents** — A zero-dependency multi-agent harness, fail-closed safety guard, and parallel evaluation suite built natively for Google Antigravity (App `v2.34.0+`, `agy` CLI, and IDE extensions).

> **Démarrage 2 min & Place dans l'écosystème**
>
> - **Palier 1 — Essentiel** *(avec [`gravity` Core](https://github.com/BSourichanh/gravity))* : harnais multi-agents, garde-fous `PreToolUse` fail-closed, routage adaptatif (`quick`/`standard`/`deep`/`evo`), evals et `/caveman`.
> - **Première commande :** `/gravity-workflow` *(et cockpit unifié `/gravity dashboard` dans `gravity` Core)*.
> - **Guides `gravity/docs/` :** [Getting Started](https://github.com/BSourichanh/gravity/blob/main/docs/getting-started.md) · [User Journey](https://github.com/BSourichanh/gravity/blob/main/docs/user-journey.md) · [Command Cheatsheet](https://github.com/BSourichanh/gravity/blob/main/docs/command-cheatsheet.md) · [Troubleshooting & Lifecycle](https://github.com/BSourichanh/gravity/blob/main/docs/troubleshooting-and-lifecycle.md)

Gravity Workflow is implemented using **100% Python 3 standard library** across the plugin engine, workspace template, and cross-platform installer—with zero external packages or Node.js build steps required.

---

## Key Capabilities

### 1. Plugin-First Architecture ([`plugin/`](plugin/))
All runtime machinery lives in [`plugin/`](plugin/) and stays decoupled from target projects:

- **Module Contract ([`plugin/config/module.json`](plugin/config/module.json)):** Declares schema v1 metadata (`id: "workflow"`, `storage.subdir: "workflow"`, `env_override: "GRAVITY_WORKFLOW_HOME"`, `ui.mode: "none"`, `ui.target: "gravity"`, `signals`) for discovery and live dashboard rendering by [`gravity`](https://github.com/BSourichanh/gravity) Core (`../gravity`).
- **Unified Interactive Skills (`/gravity-workflow` & `/caveman`)**:
  - [`/gravity-workflow`](plugin/skills/gravity-workflow/SKILL.md) — Unified entry point with 3 executed profiles (`quick`, `standard` default, `deep`, plus backward-compatible aliases `low`/`medium`/`high`/`ultra` and consultative `evo` exposing `suggested_profile` without silent mutation), ephemeral `--no-verify` / `unsafe` (single task/run only, never persisted), A/B model overrides (`--plan-model`, `--build-model`, `--review-model`), pre-execution `/grill-me` scoping, `/gravity-workflow status`, `/gravity-workflow evals`, `/gravity-workflow init`, and 10 progressive reference guides in [`plugin/skills/gravity-workflow/references/`](plugin/skills/gravity-workflow/references/).
  - [`/caveman`](plugin/skills/caveman/SKILL.md) — Companion conciseness skill (`/caveman [lite|full|ultra|off|auto]`, `commit`, `compress`, `status`) backed by [`plugin/observability/caveman_protocol.py`](plugin/observability/caveman_protocol.py) (`lite` default across all 3 profiles, propagated by ROI to `planner` [capped at `full`], `reviewer` [1-line independent], `builder` and `swarm`, with `evals/` strictly excluded and mandatory clear prose for security alerts, confirmations, and `/grill-me`).
- **Role-Separated Subagents**:
  - [`planner`](plugin/agents/planner.md) — Read-only architecture and task planner (`pro` model tier, sandboxed), surfacing blocking ambiguities before implementation.
  - [`builder`](plugin/agents/builder.md) — Branch-isolated implementer (`flash` model tier, `workspace="branch"`).
  - [`reviewer`](plugin/agents/reviewer.md) — Independent cold-audit reviewer (`pro` tier, read-only, `inheritMcp: false`) enforcing a strict "proof or silence" protocol (`BLOCKER` / `MAJOR` / `UNCERTAIN` / `NIT`).
- **Fail-Closed Safety Hooks** ([`plugin/hooks.json`](plugin/hooks.json)):
  - [`plugin/hooks/pre_tool_guard.py`](plugin/hooks/pre_tool_guard.py) (`PreToolUse`) — Evaluates shell commands (via quote-aware pipe-chain segmentation, PowerShell here-string support, argument-role awareness for search/Git literals, and recursive `deny`-focused inspection of `python -c`, `node`/`perl`/`ruby`, `sh`/`pwsh`/`cmd`, `xargs`, and `find -exec`), file reads/writes, MCP calls, and `run_workflow` scripts via [`plugin/hooks/safety_policy.py`](plugin/hooks/safety_policy.py), enforcing hard `deny` rules globally while scoping heuristic `force_ask` prompts strictly to Gravity Workflow-initialized workspaces and protecting the active installed plugin without self-locking the source tree.
  - [`plugin/hooks/session_start_caveman.py`](plugin/hooks/session_start_caveman.py) (`SessionStart`), [`plugin/hooks/post_tool_format.py`](plugin/hooks/post_tool_format.py) (`PostToolUse`), and [`plugin/hooks/stop_session_logger.py`](plugin/hooks/stop_session_logger.py) (`Stop`) — One-time `SessionStart` Caveman injection in Gravity Workflow workspaces (preserving Gemini Context Caching), post-edit formatting checks, and session telemetry / `evo` adaptation logging, dynamically resolving the target workspace from `stdin`.
- **Automated Review & Parallel Evals**:
  - [`plugin/workflows/review_pipeline.py`](plugin/workflows/review_pipeline.py) — Resolves `PLUGIN_ROOT` without `__file__` or `os.environ`, pre-collects a bounded repository file tree for `Planner`/`Reviewer`, skips `Planner` when `plan` is provided, captures `validation_summary` in `BUILD_SCHEMA`, handles safe diff fallback if external worktree access is restricted, and logs `preset_profile` + `role_models` for A/B experiments.
  - [`plugin/evals/run_suite.py`](plugin/evals/run_suite.py) and [`plugin/evals/eval_checks.py`](plugin/evals/eval_checks.py) — Runs the 12-case starter suite in [`plugin/evals/cases.json`](plugin/evals/cases.json) using deterministic assertions followed by an independent judge, writing reports to centralized storage.
- **Live Observability, Consultative Profile & Centralized Storage**:
  - [`plugin/observability/profile_engine.py`](plugin/observability/profile_engine.py) and [`plugin/observability/caveman_protocol.py`](plugin/observability/caveman_protocol.py) — 3-profile engine (`quick`/`standard`/`deep` with legacy aliases, ephemeral `no_verify`, consultative behavioral `evo` recommending `standard`|`deep` without silent mutation, and A/B model overrides) active globally on all repositories without requiring `/gravity-workflow init`.
  - [`plugin/observability/state_writer.py`](plugin/observability/state_writer.py) and [`plugin/observability/statusline.py`](plugin/observability/statusline.py) — Stores all runtime state (`profile.json`, `live_state.json`, `runs.jsonl`, `last_verified.patch`, `evals/report.*`) centrally under `resolve_workflow_home() / "workspaces" / "<slug>-<sha256_8>/"` (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/`) for consumption by `/gravity dashboard` (`gravity` Core), with read-only `.agents/gravity-workflow/profile.json` fallback and zero state files written into target workspaces.
  - [`plugin/scripts/init_workspace.py`](plugin/scripts/init_workspace.py) (non-destructive project initialization from [`plugin/templates/`](plugin/templates/) in `full` or `quickstart` mode with opt-in `--with-stack python|api`, UTF-8 LF) and [`plugin/scripts/validate.py`](plugin/scripts/validate.py) (strict plugin, 9-field manifest + `config/module.json` v1, template parity, `AGENTS.md <= 2 500 B`, and workspace validator).

### 2. Minimal Project Footprint ([`workspace-template/`](workspace-template/))
Initializing Gravity Workflow in a repository deploys only the minimal project-specific baseline (`11` files in `quickstart`, `12` in `full`, plus opt-in `--with-stack python|api`) exemplified by the 14 reference files in [`workspace-template/`](workspace-template/):
- [`workspace-template/AGENTS.md`](workspace-template/AGENTS.md) — Project charter and working rules (including KISS/YAGNI/DRY/SOLID/CUPID).
- [`workspace-template/.agents/mcp_config.json`](workspace-template/.agents/mcp_config.json) — Project MCP configuration.
- [`workspace-template/.agents/rules/01-safety-boundaries.md`](workspace-template/.agents/rules/01-safety-boundaries.md) (plus `05-git-versioning.md`, `06-engineering-principles.md`, `04-release-checklist.md` in `full`, and opt-in stack rules `02-python-stdlib.md` / `03-api-architecture.md`) — `trigger: always_on` safety boundaries under 2 KB.
- [`workspace-template/.agents/gravity-workflow/`](workspace-template/.agents/gravity-workflow/) — Milestone progress ([`workspace-template/.agents/gravity-workflow/PROGRESS.md`](workspace-template/.agents/gravity-workflow/PROGRESS.md)), backlog ([`workspace-template/.agents/gravity-workflow/BACKLOG.md`](workspace-template/.agents/gravity-workflow/BACKLOG.md)), adaptive mode profile ([`workspace-template/.agents/gravity-workflow/profile.json`](workspace-template/.agents/gravity-workflow/profile.json)), project evals ([`workspace-template/.agents/gravity-workflow/evals/cases.json`](workspace-template/.agents/gravity-workflow/evals/cases.json)), and live state ([`workspace-template/.agents/gravity-workflow/observability/live_state.json`](workspace-template/.agents/gravity-workflow/observability/live_state.json)).

### 3. Living Documentation ([`docs/`](docs/README.md))
Architecture, modes, workflows, safety contracts, evals, observability, and contributor checks are maintained continuously in modular French documentation under [`docs/`](docs/README.md):
- [`docs/README.md`](docs/README.md) — Index, repository invariants, and continuous update mapping.
- [`docs/architecture.md`](docs/architecture.md) — Plugin vs project footprint separation, directory trees, initialization, `install.py`, and `uninstall.py`.
- [`docs/modes-and-routing.md`](docs/modes-and-routing.md) — Unified `/gravity-workflow` 3-axis grammar, model allocation, `evo` engine, and companion `/caveman` skill.
- [`docs/agents-and-workflows.md`](docs/agents-and-workflows.md) — Subagents (`planner`, `builder`, `reviewer`), independent review protocol, `review_pipeline.py`, and native routing.
- [`docs/safety-and-hooks.md`](docs/safety-and-hooks.md) — Fail-closed `PreToolUse`, `SessionStart`, `PostToolUse`, `Stop`, rule triggers, and context budget.
- [`docs/evals-and-learning.md`](docs/evals-and-learning.md) — Starter evaluation suite, double-filter runner, and `/learn` non-regression loop.
- [`docs/observability.md`](docs/observability.md) — Micro-card, `/gravity-workflow status`, `/statusline`, unified `/gravity dashboard` (`gravity` Core), and atomic state writer.
- [`docs/validation-and-development.md`](docs/validation-and-development.md) — Strict validators, test suites, portability, and contributor checklist.

### 4. Companion Ecosystem ([`gravity`](https://github.com/BSourichanh/gravity))
Session token, cache, and reliability metrics live in [`BSourichanh/gravity-metrics`](https://github.com/BSourichanh/gravity-metrics) (`../gravity-metrics`), coordinated alongside `gravity-architect` and `gravity-bridge` from the ecosystem hub [`BSourichanh/gravity`](https://github.com/BSourichanh/gravity) (`../gravity`).

---

## Installation

### Prerequisites
- **Python 3.10+** (standard library only — no `pip` install or Node.js build required)
- **Google Antigravity** (App `v2.34.0+`, IDE extension, or `agy` CLI)

### Option 1 — Automated Installer (Recommended, Linux / macOS / Windows)

```bash
git clone https://github.com/BSourichanh/gravity-workflow.git
cd gravity-workflow
python install.py
```

[`install.py`](install.py) stages and copies [`plugin/`](plugin/) into `~/.gemini/config/plugins/gravity-workflow/` (using atomic `os.rename` with backup rollback, plus a two-phase transactional in-place upgrade fallback with rollback when a running process locks the target directory on Windows), automatically removes a legacy `~/.gemini/config/plugins/gravity` v0.1.x workflow copy (only when it has no `config/module.json` `"id": "core"` AND shows legacy workflow markers — always leaving an installed [`gravity`](https://github.com/BSourichanh/gravity) Core plugin untouched) unless `--keep-legacy` is passed, adapts `python3` references to `python` or `py -3` on Windows in the **installed copy** (leaving the repository untouched), sets executable bits on POSIX scripts, and runs strict validation before committing changes.

Common options:

```bash
# Inspect prerequisites, install status, and any legacy gravity v0.1.x plugin
python install.py --check
python install.py --dry-run

# Upgrade or reinstall an existing Gravity Workflow plugin copy (pass --keep-legacy to retain v0.1.x)
python install.py --force

# Install scoped to a single project (<target-project-dir>/.agents/plugins/gravity-workflow/)
python install.py --scope workspace --workspace <target-project-dir>

# Install the plugin and initialize a project workspace in one command
python install.py --init-workspace <target-project-dir>
python install.py --init-workspace <target-project-dir> --mode quickstart

# Safely remove the installed Gravity Workflow plugin
python install.py --uninstall
```

### Option 2 — Antigravity CLI (`agy`)

```bash
agy plugin validate ./plugin --strict
agy plugin install ./plugin
```

You can also invoke this path through the installer via `python install.py --method agy`. On `agy v1.5+`, `agy plugin validate ./plugin --strict` is supported; on Windows without a real `python3` binary on `PATH`, run `python plugin/scripts/validate.py --plugin-root <installed-dir> --fix-python-cmd` against the installed copy if hook invocations fail.

### Option 3 — Manual Installation (Antigravity App & IDE)

Copy [`plugin/`](plugin/) into `~/.gemini/config/plugins/gravity-workflow/` (global scope) or `<target-project-dir>/.agents/plugins/gravity-workflow/` (workspace scope), then manage activation in **Customizations → Installed**. On Windows without `python3` on `PATH`, run:

```bash
python plugin/scripts/validate.py --plugin-root ~/.gemini/config/plugins/gravity-workflow --fix-python-cmd
```

### After Installing

1. Open any project in Antigravity and invoke `/gravity-workflow` (default `evo` at `standard`, `safe`), `/gravity-workflow quick|standard|deep|evo [--no-verify] <task>`, `/caveman [lite|full|ultra|off|auto]` (defaults to `lite`), `/gravity-workflow status`, or `/gravity-workflow evals` to start a session (or bootstrap project files via CLI with `init_workspace.py`).
2. **Unified Cockpit (`/gravity dashboard` in `gravity` Core):** Invoke `/gravity dashboard` (provided by [`gravity`](https://github.com/BSourichanh/gravity) Core) to inspect live workflow state across workspaces. *Migration note: the transitional HTTP sidecar on port `4789` is retired; if an older `"gravity-workflow/live-widget"` entry remains in `~/.gemini/config/config.json`, remove it manually.*

See [`plugin/README.md`](plugin/README.md) for the plugin's full French documentation.

### Migrating from `v0.1.x` (`gravity`) to `v0.2.0` (`gravity-workflow`)

`v0.2.0` removes all runtime legacy shims (`.agents/gravity/` is no longer recognized):
1. **Plugin:** `python install.py` (`--method copy`) installs `~/.gemini/config/plugins/gravity-workflow/` and removes a legacy v0.1.x workflow `~/.gemini/config/plugins/gravity` unless `--keep-legacy` is passed (while preserving any [`gravity`](https://github.com/BSourichanh/gravity) Core plugin having `config/module.json` `"id": "core"`). If you installed via CLI, run `agy plugin uninstall gravity` (only for v0.1.x legacy) and `agy plugin install ./plugin`.
2. **Registry & Workspace State:** Clean legacy `.agents/gravity/` and `~/.gravity/live_workspaces.json` via `python install.py --workspace-only --clean-legacy --clean-workspace <target-project-dir>`, then re-initialize the project with `/gravity-workflow` or `init_workspace.py`.
3. **App / CLI Configs:** Remove any legacy `gravity/live-widget` or `gravity-workflow/live-widget` entry from `~/.gemini/config/config.json` (replaced by `/gravity dashboard`), and update `~/.gemini/antigravity-cli/settings.json` (`~/.gemini/config/plugins/gravity-workflow/observability/statusline.py`).

---

## Uninstallation

[`install.py`](install.py) (`--uninstall` / `--workspace-only`) safely removes the Gravity Workflow plugin, optional workspace runtime state (`.agents/gravity-workflow/`), and the centralized workflow registry (`~/.gemini/gravity/workflow/live_workspaces.json`, resolved via `$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/`) while refusing unsafe targets, preserving any installed `gravity` Core plugin, and keeping user-customized files (`uninstall.py` remains as a deprecated wrapper printing `[DEPRECATED]` to `stderr` and forwarding to `install.py --uninstall`):

```bash
# Inspect plugin, workspace, registry, legacy artifacts, and manual config entries (read-only)
python install.py --uninstall --check

# Preview full cleanup (including legacy ~/.gravity and .agents/gravity/) without modifying disk
python install.py --uninstall --dry-run --all --clean-workspace <target-project-dir>

# Uninstall the global plugin only
python install.py --uninstall

# Clean Gravity Workflow runtime state (.agents/gravity-workflow/) in a project without touching AGENTS.md or rules
python install.py --workspace-only --clean-workspace <target-project-dir>

# Also remove unmodified template files (keeps any file edited by the user)
python install.py --workspace-only --clean-workspace <target-project-dir> --purge-workspace-templates

# Clean legacy v0.1.x artifacts (~/.gravity/live_workspaces.json and .agents/gravity/)
python install.py --workspace-only --clean-legacy --clean-workspace <target-project-dir>

# Uninstall plugin + global workflow registry + legacy artifacts (+ optional project cleanup)
python install.py --uninstall --all --clean-workspace <target-project-dir>
python install.py --workspace-only --clean-registry

# Uninstall via Antigravity CLI
python install.py --uninstall --method agy
```

> **Note:** `install.py --uninstall` never edits `~/.gemini/config/config.json` or `~/.gemini/antigravity-cli/settings.json` directly; if `gravity-workflow/live-widget`, legacy `gravity/live-widget`, or `statusLine` entries are detected, it reminds you to remove them manually.

---

## Repository Structure

| Path | Purpose |
|---|---|
| [`install.py`](install.py) | Unified cross-platform installer and uninstaller for `plugin/` with Windows adaptation, Core-safe legacy cleanup, workspace/registry cleanup, and strict validation |
| [`uninstall.py`](uninstall.py) | Deprecated thin wrapper (`[DEPRECATED]` on `stderr`) delegating to `install.py --uninstall` |
| [`tests/test_install.py`](tests/test_install.py) | Unit tests for `install.py` (install, upgrade, rollback, unified uninstall & cleanup) |
| [`tests/test_uninstall.py`](tests/test_uninstall.py) | Unit tests for the deprecated `uninstall.py` wrapper and CLI parity |
| [`plugin/`](plugin/) | Standalone Antigravity plugin ([manifest](plugin/plugin.json), [module contract](plugin/config/module.json), skills, subagents, hooks, workflows, evals, observability, scripts) |
| [`workspace-template/`](workspace-template/) | Reference 14-file project workspace synchronized with [`plugin/templates/`](plugin/templates/) and [`plugin/scripts/init_workspace.py`](plugin/scripts/init_workspace.py) |
| [`docs/`](docs/README.md) | Modular living documentation kept in sync with the codebase (French) |
| [`AGENTS.md`](AGENTS.md) | Repository-level agent instructions and Python stdlib / KISS-YAGNI-DRY-SOLID-CUPID / living docs / integrity invariants |
| [`CHANGELOG.md`](CHANGELOG.md) | Release notes (`v0.3.0`) |
| [`LICENSE`](LICENSE) | MIT License |

---

## Quick Start

All commands below run from the repository root with Python 3 (standard library only):

### 1. Validate the Plugin & Workspace Template

```bash
python plugin/scripts/validate.py --strict
python plugin/scripts/validate.py --workspace workspace-template --strict
agy plugin validate ./plugin --strict
```

### 2. Run the Unit Test Suites

```bash
python plugin/scripts/test_unit.py
python -m unittest discover -s tests -p "test_*.py" -v
```

### 3. Bootstrap a Target Project Workspace via CLI

```bash
python plugin/scripts/init_workspace.py --workspace <target-project-dir>
# Or initialize the minimal quick-start baseline:
python plugin/scripts/init_workspace.py --workspace <target-project-dir> --mode quickstart
# Validate the initialized workspace:
python plugin/scripts/validate.py --workspace <target-project-dir> --strict
```
