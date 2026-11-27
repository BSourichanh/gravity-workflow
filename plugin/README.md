# Gravity Workflow (`plugin/`)

> **Status:** Standalone validated plugin for Antigravity App, CLI, and IDE (decoupled from target repositories).
> **Compatibility:** Linux, macOS, and Windows (100% Python 3.10+ standard library).

> **2-Minute Quickstart & Ecosystem Role**
>
> - **Tier 1 — Core Harness** *(with [`gravity` Core](https://github.com/BSourichanh/gravity))*: multi-agent harness, fail-closed `PreToolUse` guards, adaptive routing (`quick`/`standard`/`deep`/`evo`), evals, and `/caveman`.
> - **First command:** `/gravity-workflow` *(plus unified `/gravity dashboard` cockpit in `gravity` Core)*.
> - **Guides (`gravity/docs/`):** [Getting Started](https://github.com/BSourichanh/gravity/blob/main/docs/getting-started.md) · [User Journey](https://github.com/BSourichanh/gravity/blob/main/docs/user-journey.md) · [Command Cheatsheet](https://github.com/BSourichanh/gravity/blob/main/docs/command-cheatsheet.md) · [Troubleshooting & Lifecycle](https://github.com/BSourichanh/gravity/blob/main/docs/troubleshooting-and-lifecycle.md)

**Ground your Antigravity agents** — 100% native multi-agent harness for **Google Antigravity App (`v2.34.0+`)**, **CLI `agy` (`v1.5+`)**, and IDE extensions, with zero external dependencies (100% Python 3 standard library).

## Principle: Project-Independent Plugin + Optional Project Init

- **All runtime engine logic lives in `plugin/`**: official 9-field manifest (`plugin.json`), module v1 contract (`config/module.json`, `id: "workflow"`, `SPI-1` `dashboard` provider + `SPI-3` `session-metrics` consumer), shared primitives (`_shared/`), primary skill (`/gravity-workflow [quick|standard|deep|evo]`, `/gravity-workflow status`, `/gravity-workflow evals`, `/gravity-workflow init`), companion skill (`/caveman [lite|full|ultra|off|auto]`, `commit`, `compress`, `status`), subagents (`planner`, `builder`, `reviewer`), hooks (`hooks.json` + `hooks/*.py` covering `PreToolUse`, `PostToolUse`, `Stop`, and `SessionStart` with `"enabled": false`), workflows (`workflows/{worktree_ops,patch_apply,swarm_wave,wave_runner,review_pipeline}.py`), evaluation runner (`evals/`), consultative profile & observability (`observability/{profile_engine,evo_engine,metrics_signals,caveman_protocol,state_writer,statusline}.py`), `SPI-1` dashboard provider (`scripts/dashboard_provider.py`), and workspace bootstrapper (`scripts/init_workspace.py`).
- **Centralized storage everywhere:** `Stop`, `Live`, and `profile_engine` run on any repository without requiring `/gravity-workflow init`. All runtime state (`profile.json`, `live_state.json`, `runs.jsonl`, `last_verified.patch`, `evals/report.*`) is stored under `$GRAVITY_HOME/workflow/workspaces/<slug>-<sha256_8>/` (zero state files written into target workspaces; read-only fallback on `<ws>/.agents/gravity-workflow/profile.json`). `--no-verify` / `unsafe` remains strictly ephemeral per task/run.

## Migration `v0.1.x` -> `v0.2.0` (`gravity` -> `gravity-workflow`)

Starting at `v0.2.0`, the plugin is named **`gravity-workflow`** with no runtime compatibility shim (`.agents/gravity/` is no longer recognized):
- **Plugin:** `python install.py` (`--method copy`) installs into `~/.gemini/config/plugins/gravity-workflow/` and removes a legacy v0.1.x workflow `~/.gemini/config/plugins/gravity` only if it has no `config/module.json` `"id": "core"` AND shows legacy workflow markers (`hooks/pre_tool_guard.py`, `hooks/safety_policy.py`, or `skills/gravity/`) — **Gravity Core (`gravity`)** is never removed (pass `--keep-legacy` to retain a genuine legacy plugin). Under `--method agy`, run `agy plugin uninstall gravity` (legacy only) then `agy plugin install ./plugin`.
- **Workspaces & registry:** clean legacy state with `python install.py --workspace-only --clean-legacy --clean-workspace <project>` (purging `~/.gravity/live_workspaces.json` and `.agents/gravity/`), then reinitialize via `python3 scripts/init_workspace.py --workspace <project>` or `/gravity-workflow`.
- **App / CLI config:** manually remove any stale `gravity/live-widget` or `gravity-workflow/live-widget` entry from `~/.gemini/config/config.json` (replaced by `/gravity dashboard`), and update `~/.gemini/antigravity-cli/settings.json` (`~/.gemini/config/plugins/gravity-workflow/observability/statusline.py`).

## Installation

### Local (development)
```bash
agy plugin install .
agy plugin list
```

### From GitHub / Git
```bash
git clone https://github.com/BSourichanh/gravity-workflow.git
agy plugin install ./gravity-workflow/plugin
```

### Automated Installer & Uninstaller (repository root)
Use [`../install.py`](../install.py) for install/upgrade (delegating to `_shared.installer_core`, adapting `python3` on Windows, transactional in-place rollback under Windows locks, and Core-safe legacy `gravity` cleanup unless `--keep-legacy` is passed) and to safely uninstall the plugin, clean `.agents/gravity-workflow/` without touching user-customized files, purge the centralized registry (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/live_workspaces.json`), and clean legacy artifacts (`--clean-legacy` / `--all`).

### Manual Installation (Antigravity App & IDE)
Copy `plugin/` into `~/.gemini/config/plugins/gravity-workflow/` (global scope) or `.agents/plugins/gravity-workflow/` (workspace scope), then manage activation in **Customizations -> Installed**.

## Project Initialization (via `/gravity-workflow` or CLI)

```bash
python3 scripts/init_workspace.py --workspace /path/to/my-project
# Or in Quickstart mode:
python3 scripts/init_workspace.py --workspace /path/to/my-project --mode quickstart
```

## Unified Cockpit (`/gravity dashboard`) & `SPI-1` Provider

Real-time visual tracking is served through the unified **`/gravity dashboard`** MCP App cockpit in [`gravity` Core](https://github.com/BSourichanh/gravity) via the decoupled **`SPI-1`** provider [`scripts/dashboard_provider.py`](scripts/dashboard_provider.py) (`--view workflow --json`), reading `~/.gemini/gravity/workflow/live_workspaces.json`, `workspaces/<slug>-<sha256_8>/{live_state.json,profile.json,runs.jsonl}` (`state_writer.py`), and `SPI-3` `signal_v1` metrics signals (`observability/metrics_signals.py`).

> **UI Migration:** the transitional HTTP sidecar on port `4789` (`sidecars/live-widget/`) is retired (`"ui": {"mode": "none", "target": "gravity"}` in `config/module.json`). If a manual `"gravity-workflow/live-widget"` entry remains in `~/.gemini/config/config.json`, remove it manually.

## Validation & Portability

> **Windows Note:** On native Windows without a real `python3` binary on `PATH`, run `python scripts/validate.py --fix-python-cmd` on your **installed copy** to rewrite `hooks.json` to `python` (or `py -3`) without modifying the source repository. POSIX `chmod 0755` permissions apply on Linux/macOS and are handled transparently on Windows.
> **`agy` CLI Note:** `agy plugin validate . --strict` requires `agy v1.5+`; on older versions pass the path alone (`agy plugin validate .`).

```bash
python3 scripts/validate.py --strict
python3 scripts/test_unit.py
agy plugin validate . --strict
python3 scripts/validate.py --workspace /path/to/my-project --strict
```
