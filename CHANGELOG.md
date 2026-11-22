# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-11-22

### Added
- **Canonical Tool Alias Collision Guard (`plugin/scripts/validators/base.py`, `plugin/scripts/test_unit.py`):**
  - Added `CANONICAL_TOOL_ALIASES` (`view_file` → `read_file`, `write_to_file` → `write_file`, `replace_file_content` / `multi_replace_file_content` → `edit_file`) and collision/exact-duplicate detection in `validate_agent_file()`, with unit regression tests covering exact duplicates, canonical alias collisions, and the 3 plugin agent manifests (`REQUIRED_PRETOOL_MATCHERS` and `plugin/hooks.json` kept intact).

### Fixed
- **Subagent Tool Name Conflict on Executor Construction (`plugin/agents/{planner,builder,reviewer}.md`, `docs/agents-and-workflows.md`):**
  - Removed legacy tool aliases (`view_file`, `write_to_file`, `replace_file_content`, `multi_replace_file_content`) from `planner.md`, `builder.md`, and `reviewer.md` `tools:` frontmatters so each agent declares unique canonical tool names only (fixing `failed to construct executor: tool name conflict: multiple tools resolved with name "read_file"`), and synchronized the agent tools table in `docs/agents-and-workflows.md`.

## [0.2.0] - 2026-11-22

### Added
- **Ecosystem Module Contract (`plugin/config/module.json` v1):**
  - Added `plugin/config/module.json` (`schema_version: 1`, `id: "workflow"`, `plugin_name: "gravity-workflow"`, `storage.subdir: "workflow"`, `env_override: "GRAVITY_WORKFLOW_HOME"`, `commands: ["/gravity-workflow", "/caveman"]`, `ui: {"mode": "transition-sidecar", "target": "gravity"}`, `signals`) for discovery by the `gravity` Core plugin.

### Changed
- **BREAKING — Standardized `plugin/` Layout, Official 9-Field Manifest & Centralized Storage (`gravity` → `gravity-workflow`):**
  - Standardized the plugin package directory to `plugin/` (from `gravity-plugin/` / `gravity-workflow-plugin/`), cleaned `plugin/plugin.json` to strictly the 9 official Antigravity fields (removing `$schema`, `license`, `keywords`; UTF-8 without BOM), set `name` → `"gravity-workflow"`, `displayName` → `"Gravity Workflow"`, `repository`/`homepage` → `https://github.com/BSourichanh/gravity-workflow`, primary skill `/gravity` → `/gravity-workflow` (`skills/gravity-workflow/`), and sidecar identifier → `gravity-workflow/live-widget` (transition UI targeting `gravity` Core).
  - Moved project runtime state from `.agents/gravity/` → `.agents/gravity-workflow/`, unified centralized workspace registry resolution via `default_workflow_home()` and `resolve_registry_path()` across `plugin/observability/state_writer.py`, `plugin/sidecars/live-widget/server.py`, and `uninstall.py` (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/live_workspaces.json`, preserving `state_writer.REGISTRY_PATH` monkeypatch compatibility), and updated environment overrides (`GRAVITY_WORKFLOW_WORKSPACE`, `GRAVITY_WORKFLOW_STATE_PATH`, `GRAVITY_WORKFLOW_HOME`, `GRAVITY_WORKFLOW_REGISTRY_PATH`, `GRAVITY_WORKFLOW_SKIP_REGISTRY`, `GRAVITY_WORKFLOW_WIDGET_PORT`).
  - Removed all runtime legacy shims (`.agents/gravity/` is no longer recognized by hooks, validators, or statusline) and gitignored local `.agents/` at the repository root.
- **Strict 9-Field Manifest & Module Contract Validation (`plugin/scripts/validators/plugin_validator.py`):**
  - Removed `$schema` requirement and enforced strictly the 9 official Antigravity fields (+ optional `disabled`, rejecting unknown keys and UTF-8 BOM) alongside `validate_module_contract()` for `plugin/config/module.json` v1.
- **Core-Safe Installer & Uninstaller Migration (`install.py`, `uninstall.py`, `tests/`):**
  - Updated `install.py` (`PLUGIN_SOURCE = REPO_ROOT / "plugin"`, `--scope global|workspace`, `--method copy|agy`, `--keep-legacy`) to remove a legacy v0.1.x `plugins/gravity` workflow plugin **only** when it has no `config/module.json` `"id": "core"` AND shows legacy workflow markers (`hooks/pre_tool_guard.py`, `hooks/safety_policy.py`, or `skills/gravity/`), ensuring any installed `gravity` Core plugin is never removed.
  - Updated `uninstall.py` (`--clean-legacy`, auto-enabled by `--all`) to detect and remove legacy `~/.gravity/live_workspaces.json`, legacy `.agents/gravity/` workspace state, and legacy workflow `plugins/gravity` while preserving `gravity` Core plugins and using centralized `default_workflow_home()` / `resolve_registry_path()`.
- **Surgical `deny`-Focused Threat Inspector & Workspace-Scoped `force_ask` (`plugin/hooks/safety_policy.py`):**
  - Enforced hard `deny` rules globally across all repositories while scoping heuristic `force_ask` prompts exclusively to Gravity Workflow-initialized workspaces (`is_gravity_workspace()`).
  - Replaced the rigid `python -c` AST allowlist with recursive `deny`-focused inspection across `python -c` (multi-branch sequential AST, `list`/`tuple` variables, mutations, unpacking, `IfExp`, `ListComp`, `Slice`, `NamedExpr`, `os.exec*`/`os.spawn*`), PowerShell here-strings (`@'...'@ | python -`, `$code | python -`), POSIX heredocs (`<< 'EOF'`, `<<<`, multi-heredoc and piped heredocs), `node -e`/`-p` (comments, template literals `${...}`, computed brackets, escapes), `perl -e`/`-E`, `ruby -e`, `sh`/`bash`/`zsh`/`dash -c`, `pwsh`/`powershell -Command`, `cmd /c`/`/k`, `git -c` & `ext::` URLs, `curl -d/-F @<secret>`, `xargs`, `find -exec`/`-execdir`/`-ok`, and `run_workflow`.
  - Added argument-role awareness for search/history/message commands (`git commit -m`, `git log -S`, `git diff -S/-G`, `git grep`, `git merge`/`stash -m`, `rg`, `grep`, `Select-String`, `echo`) and targeted harness self-protection to the resolved active installed plugin (including destructive root deletion) and deployed paths without self-locking the source tree (`plugin/hooks/`).
- **Transactional Windows In-Place Upgrade Fallback (`install.py`, `tests/test_install.py`):**
  - Added atomic `os.rename` swap with two-phase transactional `_sync_directory_in_place()` fallback (full file/mode rollback before stale-entry pruning, plus NTFS junction and directory symlink support) when an active process such as `sidecars/live-widget` locks the target directory on Windows (`WinError 32`).
- **Expanded Tests & Living Documentation (`plugin/scripts/test_unit.py`, `tests/`, `validators/`, `docs/`, `plugin/skills/gravity-workflow/references/06-safety-hooks.md`, `README.md`, `plugin/README.md`, `AGENTS.md`):**
  - Added regression coverage for centralized storage precedence (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow`), `REGISTRY_PATH` monkeypatch compatibility, strict 9-field + `config/module.json` v1 validation, Core-safe legacy cleanup, adversarial multi-language/shell/Git bypasses, benign anti-false-positive commands, non-self-locked source editing, Windows locked-file rollback, and non-shim `.agents/gravity/` rejection, and synchronized `AGENTS.md` (KISS/YAGNI/DRY/SOLID/CUPID) and all living documentation sheets.

### Fixed
- **Eliminated Recurring `force_ask` Prompts & False Positives Across Workspaces (`plugin/hooks/safety_policy.py`):**
  - Eliminated heuristic `force_ask` interruptions outside Gravity Workflow workspaces and on benign `python -c` (`subprocess`, loops, dynamic paths, writes), `node -e`, `perl -e`, `ruby -e`, `sh -c`, `pwsh -Command`, `cmd /c`, `find -exec`, `xargs`, `| base64`, and non-destructive `run_workflow` scripts, as well as false `deny` blocks on search/commit/regex literals (`\.env`, `DROP DATABASE`, `".*"`).
- **Prevented Corrupted Active Plugin Installations on Windows Upgrades (`install.py`):**
  - Prevented `python install.py --force` from partially removing `hooks/pre_tool_guard.py` or pruning stale entries before verifying in-place writes succeed when files or `sidecars/live-widget` are locked.

## [0.1.3] - 2026-11-22

### Changed
- **Fail-Closed AST Allowlist for Inline Python (`gravity-plugin/hooks/safety_policy.py`):**
  - Added `_match_ps_single_herestring()` (PowerShell `@'\n...\n'@` support in `split_shell_segments()`, `_tokenize_shell_segment_rich()`, and `_has_unquoted_redirection()`), `_extract_command_head_args_rich()` (`sudo` tracking & quote metadata), `_is_safe_inline_path()`, and `inspect_inline_python_code()`.
- **Expanded Tests & Living Documentation (`test_unit.py`: 27 tests, `validators/base.py`, `docs/`, `skills/gravity/references/06-safety-hooks.md`, `README.md`):**
  - Added `test_inline_python_ast_allowlist_and_anti_bypass` and synchronized all safety/architecture/validation docs.
- **Repository Agent Charter (`AGENTS.md`):**
  - Aligned root `AGENTS.md` to the canonical 3-section skeleton (`<= 2 500` bytes UTF-8) while preserving all invariants.

### Fixed
- **Eliminated Blanket `force_ask` Prompts on Benign `python -c` One-Liners (`gravity-plugin/hooks/safety_policy.py`):**
  - Auto-allows pure stdlib calculations and workspace-scoped read-only file inspection (including multiline PowerShell `@'...'@` here-strings) while escalating writes, non-allowlisted imports, dynamic/out-of-workspace paths, shell expansion (`$`, backticks, `%VAR%`), `sudo`, and dunder/module-reexport bypass attempts to `force_ask`, and strengthening inline `os.environ` access to `deny`.

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
