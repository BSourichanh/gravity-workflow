# Gravity

> **Ground your Antigravity agents** — A zero-dependency multi-agent harness, fail-closed safety guard, and parallel evaluation suite built natively for Google Antigravity (App `v2.34.0+`, `agy` CLI, and IDE extensions).

Gravity is implemented using **100% Python 3 standard library** across the plugin engine, workspace template, and cross-platform installer—with zero external packages or Node.js build steps required.

---

## Key Capabilities

### 1. Plugin-First Architecture ([`gravity-plugin/`](gravity-plugin/))
All runtime machinery lives in [`gravity-plugin/`](gravity-plugin/) and stays decoupled from target projects:

- **Unified Interactive Skills (`/gravity` & `/caveman`)**:
  - [`/gravity`](gravity-plugin/skills/gravity/SKILL.md) — Main unified entry point with 3 orthogonal axes: adaptive reflection mode (`/gravity low|medium|high|ultra|evo`, defaulting to `evo` at `medium`), workflow time strategy (`slow` default by responsibility vs `fast` time-optimized without reducing reflection), and workflow verification (`safe` default vs `unsafe` disabling workflow tests/reviews/formatting while keeping `PreToolUse` safety guards ON), plus pre-execution ambiguity scoping via `/grill-me` (`ask_question` / `/plan /grill-me`), `/gravity status`, `/gravity evals`, `/gravity init`, and 10 progressive reference guides in [`gravity-plugin/skills/gravity/references/`](gravity-plugin/skills/gravity/references/).
  - [`/caveman`](gravity-plugin/skills/caveman/SKILL.md) — Companion conciseness skill (`/caveman [lite|full|ultra|off|auto]`, `commit`, `compress`, `status`) backed by [`gravity-plugin/observability/caveman_protocol.py`](gravity-plugin/observability/caveman_protocol.py) (defaults to `lite`, auto-adapted by `evo` between `lite` and `full` unless manually locked, propagated by ROI to `planner` [capped at `full`], `reviewer` [1-line independent], `builder` and `swarm`, with `evals/` strictly excluded and mandatory clear prose for security alerts, confirmations, and `/grill-me`).
- **Role-Separated Subagents**:
  - [`planner`](gravity-plugin/agents/planner.md) — Read-only architecture and task planner (`pro` model tier, sandboxed), surfacing blocking ambiguities before implementation.
  - [`builder`](gravity-plugin/agents/builder.md) — Branch-isolated implementer (`flash` model tier, `workspace="branch"`).
  - [`reviewer`](gravity-plugin/agents/reviewer.md) — Independent cold-audit reviewer (`pro` tier, read-only, `inheritMcp: false`) enforcing a strict "proof or silence" protocol (`BLOCKER` / `MAJOR` / `UNCERTAIN` / `NIT`).
- **Fail-Closed Safety Hooks** ([`gravity-plugin/hooks.json`](gravity-plugin/hooks.json)):
  - [`gravity-plugin/hooks/pre_tool_guard.py`](gravity-plugin/hooks/pre_tool_guard.py) (`PreToolUse`) — Evaluates shell commands (via quote-aware per-segment inspection, PowerShell `@'...'@` here-string support, and fail-closed AST-allowlisted `python -c` verification), file reads/writes, MCP calls, and `run_workflow` scripts via [`gravity-plugin/hooks/safety_policy.py`](gravity-plugin/hooks/safety_policy.py), returning `deny`, `force_ask`, or `allow` while protecting secrets, blocking destructive POSIX/PowerShell/Git/DB actions, and preventing harness tampering.
  - [`gravity-plugin/hooks/session_start_caveman.py`](gravity-plugin/hooks/session_start_caveman.py) (`SessionStart`), [`gravity-plugin/hooks/post_tool_format.py`](gravity-plugin/hooks/post_tool_format.py) (`PostToolUse`), and [`gravity-plugin/hooks/stop_session_logger.py`](gravity-plugin/hooks/stop_session_logger.py) (`Stop`) — One-time `SessionStart` Caveman injection in Gravity workspaces (preserving Gemini Context Caching), post-edit formatting checks, and session telemetry / `evo` adaptation logging, dynamically resolving the target workspace from `stdin`.
- **Automated Review & Parallel Evals**:
  - [`gravity-plugin/workflows/review_pipeline.py`](gravity-plugin/workflows/review_pipeline.py) — End-to-end `plan -> isolated branch build -> independent review` loop with up to two targeted remediation rounds.
  - [`gravity-plugin/evals/run_suite.py`](gravity-plugin/evals/run_suite.py) and [`gravity-plugin/evals/eval_checks.py`](gravity-plugin/evals/eval_checks.py) — Runs the 12-case starter suite in [`gravity-plugin/evals/cases.json`](gravity-plugin/evals/cases.json) using deterministic assertions followed by an independent judge.
- **Live Observability, Adaptive Profile & Strict Validation**:
  - [`gravity-plugin/observability/profile_engine.py`](gravity-plugin/observability/profile_engine.py) and [`gravity-plugin/observability/caveman_protocol.py`](gravity-plugin/observability/caveman_protocol.py) — Deterministic 3-axis profile engine (`evo`/`low`/`medium`/`high`/`ultra` with ±1 tier/session cap and hysteresis, `slow`/`fast` workflow topology, `safe`/`unsafe` verification switch) plus automatic Caveman ROI adaptation (`lite`/`full` unless locked) evaluated on session `Stop` and powering model/workflow presets.
  - [`gravity-plugin/observability/state_writer.py`](gravity-plugin/observability/state_writer.py), [`gravity-plugin/observability/statusline.py`](gravity-plugin/observability/statusline.py), and the HTTP/SSE sidecar [`gravity-plugin/sidecars/live-widget/server.py`](gravity-plugin/sidecars/live-widget/server.py) (configured in [`gravity-plugin/sidecars/live-widget/sidecar.json`](gravity-plugin/sidecars/live-widget/sidecar.json), default port `4789`).
  - [`gravity-plugin/scripts/init_workspace.py`](gravity-plugin/scripts/init_workspace.py) (non-destructive project initialization in `full` or `quickstart` mode) and [`gravity-plugin/scripts/validate.py`](gravity-plugin/scripts/validate.py) (strict plugin and workspace validator).

### 2. Minimal Project Footprint ([`workspace-template/`](workspace-template/))
Initializing Gravity in a repository deploys only the 13 project-specific files exemplified in [`workspace-template/`](workspace-template/):
- [`workspace-template/AGENTS.md`](workspace-template/AGENTS.md) — Project charter and working rules.
- [`workspace-template/.agents/mcp_config.json`](workspace-template/.agents/mcp_config.json) — Project MCP configuration.
- [`workspace-template/.agents/rules/01-safety-boundaries.md`](workspace-template/.agents/rules/01-safety-boundaries.md) (plus four scoped rule templates in `workspace-template/.agents/rules/`, including orchestrator-only Git & versioning conventions) — `trigger: always_on` safety boundaries under 2 KB.
- [`workspace-template/.agents/gravity/`](workspace-template/.agents/gravity/) — Milestone progress ([`workspace-template/.agents/gravity/PROGRESS.md`](workspace-template/.agents/gravity/PROGRESS.md)), backlog ([`workspace-template/.agents/gravity/BACKLOG.md`](workspace-template/.agents/gravity/BACKLOG.md)), adaptive mode profile ([`workspace-template/.agents/gravity/profile.json`](workspace-template/.agents/gravity/profile.json)), project evals ([`workspace-template/.agents/gravity/evals/cases.json`](workspace-template/.agents/gravity/evals/cases.json)), and live state ([`workspace-template/.agents/gravity/observability/live_state.json`](workspace-template/.agents/gravity/observability/live_state.json)).

### 3. Living Documentation ([`docs/`](docs/README.md))
Architecture, modes, workflows, safety contracts, evals, observability, and contributor checks are maintained continuously in modular French documentation under [`docs/`](docs/README.md):
- [`docs/README.md`](docs/README.md) — Index, repository invariants, and continuous update mapping.
- [`docs/architecture.md`](docs/architecture.md) — Plugin vs project footprint separation, directory trees, initialization, `install.py`, and `uninstall.py`.
- [`docs/modes-and-routing.md`](docs/modes-and-routing.md) — Unified `/gravity` 3-axis grammar, model allocation, `evo` engine, and companion `/caveman` skill.
- [`docs/agents-and-workflows.md`](docs/agents-and-workflows.md) — Subagents (`planner`, `builder`, `reviewer`), independent review protocol, `review_pipeline.py`, and native routing.
- [`docs/safety-and-hooks.md`](docs/safety-and-hooks.md) — Fail-closed `PreToolUse`, `SessionStart`, `PostToolUse`, `Stop`, rule triggers, and context budget.
- [`docs/evals-and-learning.md`](docs/evals-and-learning.md) — Starter evaluation suite, double-filter runner, and `/learn` non-regression loop.
- [`docs/observability.md`](docs/observability.md) — Micro-card, `/gravity status`, `/statusline`, Live Widget sidecar, and atomic state writer.
- [`docs/validation-and-development.md`](docs/validation-and-development.md) — Strict validators, test suites, portability, and contributor checklist.

### 4. Companion Project ([`gravity-metrics`](https://github.com/BSourichanh/gravity-metrics))
Session token, cache, cost, and reliability metrics collection lives in the separate companion repository [`BSourichanh/gravity-metrics`](https://github.com/BSourichanh/gravity-metrics) (`../gravity-metrics` locally).

---

## Installation

### Prerequisites
- **Python 3.8+** (standard library only — no `pip` install or Node.js build required)
- **Google Antigravity** (App `v2.34.0+`, IDE extension, or `agy` CLI)

### Option 1 — Automated Installer (Recommended, Linux / macOS / Windows)

```bash
git clone https://github.com/BSourichanh/gravity.git
cd gravity
python install.py
```

[`install.py`](install.py) stages and copies [`gravity-plugin/`](gravity-plugin/) into `~/.gemini/config/plugins/gravity/`, adapts `python3` references to `python` or `py -3` on Windows in the **installed copy** (leaving the repository untouched), sets executable bits on POSIX scripts, and runs strict validation before committing changes.

Common options:

```bash
# Inspect prerequisites and install status without making changes
python install.py --check
python install.py --dry-run

# Upgrade or reinstall an existing Gravity plugin copy
python install.py --force

# Install scoped to a single project (<target-project-dir>/.agents/plugins/gravity/)
python install.py --scope workspace --workspace <target-project-dir>

# Install the plugin and initialize a project workspace in one command
python install.py --init-workspace <target-project-dir>
python install.py --init-workspace <target-project-dir> --mode quickstart

# Safely remove the installed Gravity plugin
python install.py --uninstall
```

### Option 2 — Antigravity CLI (`agy`)

```bash
agy plugin validate ./gravity-plugin
agy plugin install ./gravity-plugin
```

You can also invoke this path through the installer via `python install.py --method agy`. On `agy v1.5+`, `agy plugin validate --strict ./gravity-plugin` is supported; on Windows without a real `python3` binary on `PATH`, run `python gravity-plugin/scripts/validate.py --plugin-root <installed-dir> --fix-python-cmd` against the installed copy if hook invocations fail.

### Option 3 — Manual Installation (Antigravity App & IDE)

Copy [`gravity-plugin/`](gravity-plugin/) into `~/.gemini/config/plugins/gravity/` (global scope) or `<target-project-dir>/.agents/plugins/gravity/` (workspace scope), then manage activation in **Customizations → Installed**. On Windows without `python3` on `PATH`, run:

```bash
python gravity-plugin/scripts/validate.py --plugin-root ~/.gemini/config/plugins/gravity --fix-python-cmd
```

### After Installing

1. Open any project in Antigravity and invoke `/gravity` (default `evo` at `medium`, `slow`, `safe`), `/gravity low|medium|high|ultra|evo [slow|fast] [safe|unsafe] <task>`, `/caveman [lite|full|ultra|off|auto]` (defaults to `lite`), `/gravity status`, or `/gravity evals` to start a session and initialize the minimal 13-file project footprint (or bootstrap via CLI with `init_workspace.py`).
2. **Optional — Enable the Live Widget Sidecar (Antigravity App only):** Manually add the block below to `~/.gemini/config/config.json` (never let an agent edit `config.json` directly), then visit `http://127.0.0.1:4789` (health endpoint: `/health`):

```json
{
  "sidecars": {
    "gravity/live-widget": {
      "enabled": true
    }
  }
}
```

See [`gravity-plugin/README.md`](gravity-plugin/README.md) for the plugin's full French documentation.

---

## Uninstallation

[`uninstall.py`](uninstall.py) safely removes the Gravity plugin, optional workspace runtime state (`.agents/gravity/`), and the global Live Widget registry (`~/.gravity/live_workspaces.json`) while refusing unsafe targets and preserving user-customized files (`python install.py --uninstall` also remains supported for plugin-only removal):

```bash
# Inspect plugin, workspace, registry, and manual config entries (read-only)
python uninstall.py --check

# Preview full cleanup without modifying disk
python uninstall.py --dry-run --all --clean-workspace <target-project-dir>

# Uninstall the global plugin only
python uninstall.py

# Clean Gravity runtime state (.agents/gravity/) in a project without touching AGENTS.md or rules
python uninstall.py --workspace-only --clean-workspace <target-project-dir>

# Also remove unmodified template files (keeps any file edited by the user)
python uninstall.py --workspace-only --clean-workspace <target-project-dir> --purge-workspace-templates

# Uninstall plugin + global Live Widget registry (+ optional project cleanup)
python uninstall.py --all --clean-workspace <target-project-dir>
python uninstall.py --clean-registry --workspace-only

# Uninstall via Antigravity CLI
python uninstall.py --method agy
```

> **Note:** `uninstall.py` never edits `~/.gemini/config/config.json` or `~/.gemini/antigravity-cli/settings.json` directly; if `gravity/live-widget` or `statusLine` entries are detected, it reminds you to remove them manually.

---

## Repository Structure

| Path | Purpose |
|---|---|
| [`install.py`](install.py) | Cross-platform installer for `gravity-plugin/` with Windows adaptation, strict validation, and optional workspace bootstrapping |
| [`uninstall.py`](uninstall.py) | Cross-platform uninstaller for `gravity-plugin/`, workspace `.agents/gravity/` cleanup, and Live Widget registry pruning |
| [`tests/test_install.py`](tests/test_install.py) | Unit tests for `install.py` |
| [`tests/test_uninstall.py`](tests/test_uninstall.py) | Unit tests for `uninstall.py` |
| [`gravity-plugin/`](gravity-plugin/) | Standalone Antigravity plugin ([manifest](gravity-plugin/plugin.json), skills, subagents, hooks, workflows, evals, observability, sidecar, scripts) |
| [`workspace-template/`](workspace-template/) | Reference 13-file project workspace generated by [`gravity-plugin/scripts/init_workspace.py`](gravity-plugin/scripts/init_workspace.py) |
| [`docs/`](docs/README.md) | Modular living documentation kept in sync with the codebase (French) |
| [`AGENTS.md`](AGENTS.md) | Repository-level agent instructions and Python stdlib / living docs / integrity invariants |
| [`CHANGELOG.md`](CHANGELOG.md) | Release notes (`v0.1.2`) |
| [`LICENSE`](LICENSE) | MIT License |

---

## Quick Start

All commands below run from the repository root with Python 3 (standard library only):

### 1. Validate the Plugin & Workspace Template

```bash
python gravity-plugin/scripts/validate.py --strict
python gravity-plugin/scripts/validate.py --workspace workspace-template --strict
agy plugin validate ./gravity-plugin
```

### 2. Run the Unit Test Suites

```bash
python gravity-plugin/scripts/test_unit.py
python -m unittest discover -s tests -p "test_*.py" -v
```

### 3. Bootstrap a Target Project Workspace via CLI

```bash
python gravity-plugin/scripts/init_workspace.py --workspace <target-project-dir>
# Or initialize the minimal quick-start baseline:
python gravity-plugin/scripts/init_workspace.py --workspace <target-project-dir> --mode quickstart
# Validate the initialized workspace:
python gravity-plugin/scripts/validate.py --workspace <target-project-dir> --strict
```
