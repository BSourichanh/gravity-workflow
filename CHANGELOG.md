# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] - 2026-11-22

### Changed
- **Quote-Aware Shell Segmentation (`gravity-plugin/hooks/safety_policy.py`):**
  - Added `split_shell_segments()` (quote-aware splitting preserving Windows backslashes with bounded recursive `$(...)` and backtick subcommand extraction) and `extract_command_head_and_args()` (Windows-safe tokenizer unwrapping `VAR=val`, `&`, `sudo`, `env`, `command`, `nohup`, `time`), separating per-segment evaluation (`_is_destructive_rm`, `_is_git_force_push`, `_shell_targets_harness`, `_check_segment_inline_exec`) from full-command/pipeline rules.
- **Expanded Unit Tests & Living Documentation (`test_unit.py`: 26 tests, `docs/`, `skills/gravity/references/06-safety-hooks.md`):**
  - Added `test_shell_segmentation_and_tokenization` and `test_shell_false_positives_and_anti_bypass` covering the full `allow` / `force_ask` / `deny` matrix, and synchronized `docs/safety-and-hooks.md`, `docs/validation-and-development.md`, `docs/architecture.md`, `gravity-plugin/skills/gravity/references/06-safety-hooks.md`, and `README.md`.

### Fixed
- **Eliminated Shell Guard False Positives Without Weakening Fail-Closed Coverage (`gravity-plugin/hooks/safety_policy.py`):**
  - Stopped `force_ask` false positives on chained commands (`python -m unittest ...; git -C ... status`, `node build.js; grep -E ...`), post-script/-module flags (`python scripts/cli.py -c config.json`, `python -m mypkg.cli -c prod`), and `deny` false positives on read-only commands referencing harness `.py` files (`git diff -- gravity-plugin/hooks/...`, `Get-Content ...`, `2>&1`) or `git push origin dev; rm -f /tmp/cache`, while blocking bypasses via quoted `;`, wrappers, and `$(...)`/backtick subcommands.

## [0.1.1] - 2026-11-22

### Added
- **Orchestrator-only Git & Versioning Rule (`workspace-template/.agents/rules/05-git-versioning.md` — 13-file baseline):**
  - Added `trigger: model_decision` project rule covering branch discipline (`dev` / feature branches, never direct commit/push to `main`/`master`), pre-commit/pre-push checks, Conventional Commits, Semantic Versioning + `CHANGELOG.md` (Keep a Changelog), and annotated `vX.Y.Z` tags — strictly scoped to the main `/gravity` orchestrator and forbidden to subagents (`planner`, `builder`, `reviewer`) and `swarm` workers.
  - Updated `gravity-plugin/scripts/init_workspace.py` to deploy `05-git-versioning.md` in both `quickstart` and `full` modes, plus idempotent `ensure_git_versioning_rule()` / `--ensure-git-rule` automatically invoked by `/gravity` when absent from a project, and synced `uninstall.py` (`--purge-workspace-templates`) and `workspace_validator.py`.
- **Read-only Agent Eval Predicate (`agent_frontmatter_readonly`):**
  - Added `agent_frontmatter_readonly()` in `gravity-plugin/evals/eval_checks.py` and updated `CASE-07` (`gravity-plugin/evals/cases.json` & `workspace-template/.agents/gravity/evals/cases.json`) to inspect YAML `tools:` without false positives from Markdown prose.
- **Expanded Test Coverage (`test_unit.py`: 24 tests, `tests/test_uninstall.py`):**
  - Added unit tests for `agent_frontmatter_readonly`, scoped hooks protection & Windows/PowerShell hardening, ASCII-safe `pre_tool_guard.py` output without non-Gravity pollution, multi-turn `evo` / `TERMINAL_CUSTOM_HOOK` handling, `ensure_git_versioning_rule` / `--ensure-git-rule` / `workspace-template` parity, and symlink uninstall targets.

### Changed
- **Subagent & Workflow Alignment:**
  - Harmonized tool allowlists (`read_file`, `write_file`, `edit_file`) across `agents/planner.md`, `agents/builder.md`, `agents/reviewer.md`, and `03-subagents-architecture.md`.
  - Explicitly restricted `builder`, `planner`, `review_pipeline.py`, and `swarm` workers from running `git commit`, `git push`, `git tag`, or release version bumps.
  - Updated `review_pipeline.py` to generate `.gravity_verified.patch` via `git diff --output=...` (UTF-8 without BOM across POSIX & PowerShell 5.1, with UTF-16/UTF-8 fallback reading) and check `build.get("refused")` during remediation rounds.
- **Observability & Living Documentation:**
  - Updated `live-widget/server.py` SSE signature tracking (`live_state.json`, `profile.json`, `live_workspaces.json`), single-read `statusline.py` checking `harness-pre-tool-guard.enabled`, and `state_writer.py` atomic `os.replace` retry on Windows `PermissionError` with `ts_iso` event timestamps.
  - Synchronized `AGENTS.md`, `README.md`, `gravity-plugin/README.md`, `install.py`, and all living documentation sheets in `docs/`.

### Fixed
- **Safety Guard Scoping & Windows Compatibility (`hooks/` & `scripts/`):**
  - Scoped harness self-protection in `safety_policy.py` to Gravity's actual `hooks.json` / `hooks/` paths (preventing false positives on sibling plugins or `src/hooks/`), hardened `git` global-flag force-push detection, Windows/PowerShell `rd`/`rmdir`/`.exe`/env-dump detection, `run_workflow` `Cwd` resolution + fail-closed missing-script handling, and AST checks for `importlib`/`__import__`/`getattr`.
  - Enforced UTF-8 stdio reconfiguration and ASCII-safe `emit_json()` across hooks and CLIs (`validate.py`, `init_workspace.py`, `statusline.py`, `profile_engine.py`, `hook_runtime.py`) to avoid Windows `cp1252`/`protojson` errors, and prevented `pre_tool_guard.py` / `stop_session_logger.py` from creating `.agents/gravity/` in non-Gravity workspaces.
- **Profile Engine, Evals & Symlink Cleanup:**
  - Fixed `profile_engine.py` multi-turn `(conversation_id, execution_num)` idempotency, `ts_iso` event windowing, and `NORMAL_TERMINATION_REASONS` (`TERMINAL_CUSTOM_HOOK`, `INJECTED_RESPONSE`, `TERMINAL_STEP_TYPE`, `USER_CANCELED`).
  - Failed explicitly in `evals/run_suite.py` when candidate `workspace_path` is empty instead of falling back to `WS_ROOT`.
  - Preserved final symlink paths in `install.py` / `uninstall.py` via `_remove_tree_or_symlink()` so symlinked targets are unlinked safely without deleting real target directories.

## [0.1.0] - 2026-11-21

### Added
- **Standalone Antigravity Plugin (`gravity-plugin/` — 42 files, 100% Python 3 stdlib):**
  - Unified `/gravity` orchestrator skill with 3 orthogonal axes: adaptive reflection mode (`low` / `medium` / `high` / `ultra` / `evo`, defaulting to `evo` at `medium`), workflow time strategy (`slow` / `fast` without reducing reflection), and verification toggle (`safe` / `unsafe`), plus `/gravity status`, `/gravity evals`, `/gravity init`, pre-execution `/grill-me` scoping, and 10 progressive reference guides.
  - Companion `/caveman` skill (`lite` / `full` / `ultra` / `off` / `auto`, plus `commit` and `compress`) backed by `observability/caveman_protocol.py` and `hooks/session_start_caveman.py` (`SessionStart`), defaulting to `lite`, auto-adapted by `evo` between `lite` and `full` unless locked (`caveman_locked`), and scoped by ROI across `planner`, `builder`, `reviewer`, and `swarm` (with `evals/` excluded and mandatory clear prose for safety alerts, confirmations, and `/grill-me`).
  - Role-separated subagents (`agents/planner.md`, `agents/builder.md`, `agents/reviewer.md`) implementing branch-isolated builds and cold-audit independent reviews ("proof or silence").
  - Fail-closed lifecycle hooks (`hooks.json`, `pre_tool_guard.py`, `safety_policy.py`, `post_tool_format.py`, `session_start_caveman.py`, `stop_session_logger.py`) protecting secrets, blocking destructive POSIX/PowerShell/Git/DB commands, and preventing harness tampering.
  - Automated `review_pipeline.py` workflow (`planner` → branch-isolated `builder` → independent `reviewer` with bounded remediation rounds) and parallel double-filter evaluation suite (`evals/cases.json` 12 cases, `evals/eval_checks.py`, `evals/run_suite.py`).
  - 4 observability surfaces: 3-line micro-card, `/gravity status` (artifact `generative_ui` / Markdown), CLI `/statusline` (`observability/statusline.py`), and multi-workspace anti-XSS HTTP/SSE Live Widget sidecar (`sidecars/live-widget/server.py`, port `4789`), backed by atomic `state_writer.py` and deterministic `profile_engine.py`.
  - Strict plugin & workspace validators (`scripts/validate.py`, `scripts/validators/`), project bootstrapper (`scripts/init_workspace.py`), and 19 internal unit tests (`scripts/test_unit.py`).
- **Minimal Project Baseline (`workspace-template/` — 12 files):**
  - `AGENTS.md`, `.agents/mcp_config.json`, 4 scoped rule templates (`01-safety-boundaries.md` under 2 KB `always_on` + `02`..`04`), and `.agents/gravity/` state files.
- **Cross-Platform Installer & Uninstaller (`install.py`, `uninstall.py`):**
  - Staged copy with rollback, automatic Windows `python3` adaptation on installed copies, `agy` CLI delegation, workspace initialization/cleanup without deleting user-modified files, Live Widget registry pruning/cleanup, and full test coverage (`tests/test_install.py`, `tests/test_uninstall.py`).
- **Living Documentation (`docs/`):**
  - Modular French documentation (`architecture.md`, `modes-and-routing.md`, `agents-and-workflows.md`, `safety-and-hooks.md`, `evals-and-learning.md`, `observability.md`, `validation-and-development.md`) kept in sync with the codebase.
