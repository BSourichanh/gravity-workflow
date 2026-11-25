# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Configurable `GRAVITY_WORKTREE_BASE`, Linux Snap Non-Hidden Fallback & `/swarm` Execution Hygiene (`plugin/workflows/patch_apply.py`, `plugin/skills/gravity-workflow/references/05-workflows-and-routing.md`, `tests/test_wave_runner.py`):**
  - Updated `resolve_worktree_base()` with explicit precedence (`worktree_base` arg > `GRAVITY_WORKTREE_BASE` > workflow home) and automatic fallback to `~/gravity-worktrees` only on Linux when `agy`/`antigravity` runs from Snap (`/snap/`) and the resolved base sits inside a hidden dotdir under `$HOME`.
  - Protected `gravity-worktrees` in `_is_protected_cleanup_target()`, added the POSIX/Linux `grep -rn` reminder to `BUILDER_HYGIENE_DIRECTIVE`, documented `GRAVITY_WORKTREE_BASE`, Snap fallback, 1-line Caveman `/swarm` brief, `grep -rn`, and `ast.PyCF_ALLOW_TOP_LEVEL_AWAIT` AST verification in `05-workflows-and-routing.md`, and expanded `tests/test_wave_runner.py`.
- **Generic `wave_plan_v1` Runner & `quick` Profile Docs Alignment (`plugin/workflows/wave_runner.py`, `plugin/skills/gravity-workflow/{SKILL,references/05-*,references/08-*}.md`, `tests/test_wave_runner.py`):**
  - Replaced FR Architect Markdown regexes and `WS_ROOT.parent` fallbacks with fail-closed `wave_plan_v1` (`args["wave_plan"]` dict or JSON file path) and structured `args["tasks"]` (`repo_root` required, `draft`/`done`/`archived`/`cancelled` status rejection).
  - Aligned `SKILL.md` and `references/05-workflows-and-routing.md` on the actual `quick` preset (`Builder` + `Reviewer flash`, `0` remediation, `Planner` skipped; `--no-verify`/`unsafe` alone skips `Reviewer`).

### Added
- **Native Multi-Repo Wave Runner, Watchdogs & Detached Worktree Helpers (`plugin/workflows/{wave_runner,review_pipeline,patch_apply}.py`, `tests/test_wave_runner.py`):**
  - Added `plugin/workflows/wave_runner.py` (`< 600` L, 100 % stdlib) for native `run_workflow` multi-repo execution (`parallel` *fail-isolated* via `try/except`): parses `tasks/*.md` + `<repo>/AGENTS.md` in Python (`0` LLM tokens), creates detached `git worktree` instances under `$GRAVITY_HOME/workflow/worktrees/<repo>-<id>`, skips `Planner` on `plan_file`, enforces `asyncio.wait_for` watchdogs (`900s` Builder / `360s` Reviewer) with `quick`/`standard`/`deep` routing, applies verified patches onto `dev` (`apply_on_pass=True`) with guaranteed `try/finally` cleanup, and outputs compact JSON (`<= 10` lines/repo, `changed_files` `1..20`).
  - Added `resolve_worktree_base()`, `create_repo_worktree()`, `extract_changed_files_from_diff()`, `collect_changed_files()`, `collect_repo_tree()`, `read_plan_file()`, and shared workflow helpers/directives (`BUILDER_HYGIENE_DIRECTIVE`) in `plugin/workflows/patch_apply.py`, hardening `_is_protected_cleanup_target()` and `cleanup_worktree_best_effort()` for `$GRAVITY_HOME/workflow/worktrees/<repo>-<id>`.
  - Updated `plugin/workflows/review_pipeline.py` (preserving the commit `793d510` native top-level `await` deadlock fix) to support `Args.plan_file` (forcing `skip_separate_planner=True`), `asyncio.wait_for` watchdogs (`900s`/`360s`), `changed_files` (`1..20`), and Builder anti-polling / PowerShell `;` / absolute `EndLine` / exact `05-workflows-and-routing.md` path directives.
  - Added `tests/test_wave_runner.py` covering detached worktrees, protected cleanup, `changed_files <= 20`, `plan_file` planner skip, `quick`/`standard` routing, fail-isolated `parallel()`, and watchdog timeouts.
- **Mini-suite evals `gravity-studio` & sélection `[manual]` (`plugin/evals/{cases.json,eval_checks.py,run_suite.py}`, `workspace-template/.agents/gravity-workflow/evals/cases.json`, `plugin/scripts/validators/{plugin_validator,workspace_validator}.py`, `tests/test_validators.py`):**
  - Added 5 `[manual]` dogfooding cases (`CASE-13-STUDIO-TRACKED-RO`, `CASE-14-STUDIO-GIT-RELEASE-GUARD`, `CASE-15-STUDIO-PLAN-CHECKPOINT`, `CASE-16-STUDIO-FAIL-CLOSED-BRIDGE`, `CASE-17-STUDIO-TASKS-NO-DRIFT`) to `cases.json` (synced byte-for-byte with `workspace-template/`).
  - Added `is_manual_case()`, `select_eval_cases()` (excluding `[manual]` by default unless `case_ids` or `include_manual=True`), manual empty-`auto_checks` handling in `run_deterministic_checks()`, and `skipped_manual_ids` reporting in `run_suite.py` and `render_markdown_report()`.
  - Updated `plugin_validator.py` and `workspace_validator.py` to enforce the 10–15 count specifically on `auto_cases` (excluding `[manual]`).
- **Unified Patch Application & Safe Worktree Cleanup (`plugin/workflows/patch_apply.py`, `plugin/workflows/review_pipeline.py`, `plugin/skills/gravity-workflow/references/05-workflows-and-routing.md`, `plugin/skills/gravity-workflow/SKILL.md`, `docs/agents-and-workflows.md`, `tests/test_validators.py`):**
  - Extracted patch helpers and `--apply` CLI into `plugin/workflows/patch_apply.py` (`_is_unified_diff()`, `git -C <worktree> diff` fallback in `_read_worktree_patch()`, UTF-8 LF `_safe_save_verified_patch()`, `cleanup_worktree_best_effort()` protecting caller workspace/parents/plugin/hooks/`.git`/`.agents`, and `apply_verified_patch()`) while keeping `review_pipeline.py` (`< 800` L) in native top-level `await` for `run_workflow` (supporting `args["apply_on_pass"]`).
  - Documented the autonomous Bridge protocol (`depth >= 1`, `caveman ultra`, 1-line milestones, 0 `ask_question`, `patch_apply.py --apply` onto `dev` + re-verification + worktree cleanup before `reply_thread(..., close=True, dispatch=False)`) in `05-workflows-and-routing.md` and compacted `plugin/skills/gravity-workflow/SKILL.md` to `<= 5 600` bytes.
- **Validator Integrity Guards (`INT-01/03/04/06/09`) & `ui.mode` Alignment (`plugin/scripts/validators/plugin_validator.py`, `plugin/templates/.agents/gravity-workflow/profile.json`, `workspace-template/.agents/gravity-workflow/profile.json`, `docs/validation-and-development.md`, `tests/test_validators.py`):**
  - Aligned `VALID_UI_MODES` (`transition-mcp`), enforced `suggestedPrompts` (`<= 100` chars, `/cmd — résumé` format), deduplicated tree-wide UTF-8 without BOM + LF checks on `.py`/`.md`/`.json`, rejected hardcoded user machine paths outside `tests/`/`fixtures/`, synced `signals.metrics` in `profile.json` templates against `profile_engine.default_profile()`, and verified `workspace-template` `PROGRESS.md` & `live_state.json` against `init_workspace._render_*("full")`.
- **Root-Only `gravity-metrics` Session Closure Protocol (`plugin/skills/gravity-workflow/references/08-session-learning.md`, `plugin/skills/gravity-workflow/SKILL.md`, `docs/observability.md`):**
  - Documented optional post-commit/push `gravity-metrics` archival (`cli.py archive <conversation_id> -l "<projet>-<slug>" --workspace .`, with `--update-sealed` on a later `git push` and never `--force`) with silent no-op when `gravity-metrics` is not installed and zero Python coupling.
  - Explicitly restricted archival to root mono-repo sessions, forbidding execution by subagents (`planner`, `builder`, `reviewer`), `swarm` workers, and Gravity Bridge child sessions (`[GRAVITY-BRIDGE | ...]` or `depth >= 1`).

### Changed
- **Retired Active Bridge Coupling & Accelerated `unittest` Suite (`plugin/observability/metrics_signals.py`, `tests/{test_install,test_uninstall,test_metrics_signals}.py`, `plugin/skills/gravity-workflow/{SKILL.md,references/05-workflows-and-routing.md,references/08-session-learning.md}`, `docs/{README,architecture,agents-and-workflows,observability}.md`, `README.md`, `plugin/README.md`):**
  - Removed active `gravity-bridge` coupling in `metrics_signals.py::collect_metrics_signals()` (`recent_bridge_sessions` kept at `0` for API compatibility without reading live Bridge signals) and replaced active Bridge references across `SKILL.md` (`<= 6 000` B), `05-workflows-and-routing.md`, `08-session-learning.md`, `docs/`, and `README.md` with native `wave_runner.py` / `/swarm` workflows.
  - Accelerated repetitive install/uninstall cases in `tests/test_install.py` and `tests/test_uninstall.py` (`--skip-validate` / mocked validation on non-E2E flag tests while keeping `test_fresh_install_and_strict_validation` and `test_rollback_on_validation_failure` intact) so the full `tests/` suite completes in `< 60s`.
- **Templates `04-release-checklist.md` & `PROGRESS.md` Clarification (`plugin/templates/.agents/rules/04-release-checklist.md`, `workspace-template/.agents/rules/04-release-checklist.md`, `plugin/scripts/init_workspace.py`, `workspace-template/.agents/gravity-workflow/PROGRESS.md`):**
  - Updated `04-release-checklist.md` (`100 % PASS` auto + `[manual]` replay, `python3`/`python` on Windows, `CHANGELOG.md`/docs/templates/links/UTF-8 LF sync).
  - Clarified `(8 étapes + 1 bonus)` in `_render_progress_md()` and synced `workspace-template/.agents/gravity-workflow/PROGRESS.md`.
- **Living Docs & Link Integrity Alignment (`AGENTS.md`, `README.md`, `plugin/README.md`, `docs/{README,architecture,safety-and-hooks,evals-and-learning,validation-and-development}.md`, `plugin/skills/gravity-workflow/references/{05-workflows-and-routing,06-safety-hooks,07-eval-suite,09-observability-dashboard}.md`):**
  - Fixed broken `../gravity/` relative links, updated `SessionStart`/`Stop`/evals documentation for global execution + centralized storage (`$GRAVITY_WORKFLOW_HOME/workspaces/<slug>-<sha256_8>/`), removed obsolete `"verification": "unsafe"`/`fast`/`slow` references in hooks/evals docs, corrected `evo` eval weights (`+15` / `-10`), and aligned `install.py --uninstall` / `agy plugin validate` / `metrics_signals.py` / `style-examples.md` references.

### Fixed
- **AST Read-Only Harness Inspection (`AUD-03-03`) (`plugin/hooks/policy/python_ast.py`, `docs/safety-and-hooks.md`, `tests/test_safety_python_ast.py`):**
  - Added `_is_readonly_harness_usage()` in `_inspect_pass2_ast_nodes()` and `_raw_code_has_harness_mutation_risk()` in `_raw_code_hard_deny_check()` so pure read-only operations (`read_text`, `read_bytes`, `open(..., 'r')`, `stat`, `exists`, `glob`/`rglob`/`iterdir` read loops, `os.path.*`, `os.walk`, `json.load/loads`, `compile`) on `is_harness_protected` paths are allowed while mutations, writes, non-read `open()`, `getattr`, `shutil`, `subprocess` mutations, `environ`, and `SENSITIVE_PATH_PATTERNS` remain strictly `deny`.
- **False Positive in `PreToolUse` Harness Protection on Read Commands with `2>/dev/null` (`plugin/hooks/policy/git_and_fs.py`, `tests/test_safety_paths.py`):**
  - Separated `cmd_writes_or_execs` (`HARNESS_WRITE_OR_EXEC_HEADS`, `_is_python_head`, `sed -i`) from `has_redir` (`_has_unquoted_redirection`) in `_segment_targets_harness()` so non-writing commands with redirections (e.g. `grep -rn ... hooks/ 2>/dev/null`, `ls ... 2>/dev/null`, `cat ... 2>/dev/null`) only inspect redirection targets (`_extract_redirection_targets()`) against `is_harness_protected()`, while preserving strict `deny` on write/exec commands and output redirections (`>`, `>>`) targeting protected harness or `.agents/rules/` paths.
- **Dashboard Access Label in `docs/observability.md` (`docs/observability.md`):**
  - Updated the `/gravity dashboard` access instructions to reference `la **barre latérale gauche (`Gravity`)** ou le **menu `+` du panneau latéral droit**`.

## [0.4.0] - 2026-11-24

### Changed
- **Retired Transitional Sidecar `4789`, UX Docs Alignment & `--strict` CLI Parity (`plugin/config/module.json`, `plugin/plugin.json`, `install.py`, `plugin/scripts/`, `tests/`, `README.md`, `plugin/README.md`, `docs/`):**
  - Removed the transitional HTTP/SSE sidecar directory (`plugin/sidecars/live-widget/server.py`, `sidecar.json`), switched `plugin/config/module.json` `ui` from `transition-sidecar` to `{"mode": "none", "target": "gravity"}` (replaced by `gravity` Core's unified `/gravity dashboard` MCP App), and preserved atomic `state_writer.py` persistence (`live_workspaces.json` + `workspaces/<slug>-<sha256_8>/live_state.json`).
  - Updated `plugin/scripts/validate.py` (`_fix_python_cmd()` targeting `hooks.json` only) and `plugin/scripts/validators/plugin_validator.py` (removed `sidecars/live-widget/server.py` and `sidecar.json` warning, warning only if a `sidecars/live-widget` vestige reappears).
  - Added `--strict` to `install.py` (`11/11` ecosystem CLI contract flags), replaced post-install sidecar instructions with `/gravity dashboard` while preserving `detect_manual_config_entries()` read-only warnings on `config.json`, and updated `tests/_unit_helpers.py`, `tests/test_sidecar_widget.py`, `tests/test_install.py`, and `tests/test_validators.py`.
  - Aligned `plugin/plugin.json` `suggestedPrompts` (`/cmd — résumé`, `<= 100` chars) and added the standardized « Démarrage 2 min & Place dans l'écosystème » callout (`Palier 1 — Essentiel` + links to `gravity/docs/`) in `README.md` and `plugin/README.md`, plus updated `AGENTS.md`, `docs/`, and `plugin/skills/gravity-workflow/references/{01,09}-*.md`.
- **Modular PreToolUse Security Policy Package (`plugin/hooks/policy/`, `plugin/hooks/safety_policy.py`, `plugin/scripts/validators/plugin_validator.py`, `tests/test_install.py`, `docs/`):**
  - Split monolithic `plugin/hooks/safety_policy.py` (`5 384` lines) into 7 SRP stdlib submodules under `plugin/hooks/policy/` (`__init__.py`, `constants.py`, `paths.py`, `shell_parser.py`, `git_and_fs.py`, `python_ast.py`, `inline_scripts.py`, `evaluator.py`) with 100% behavior parity.
  - Reduced `plugin/hooks/safety_policy.py` to a thin backward-compatible facade (`169` lines, `<= 220` L) re-exporting all public and internal symbols (including `HOOKS_DIR` monkeypatch compatibility) consumed by `pre_tool_guard.py`, validators, and `tests/test_safety_*.py`.
  - Updated `local_mods` in `plugin/scripts/validators/plugin_validator.py` to allow `policy` and its submodules under strict stdlib-only AST validation, added isolated installed-copy `policy/` + `pre_tool_guard.py` verification in `tests/test_install.py`, and updated `docs/architecture.md`, `docs/safety-and-hooks.md`, and `plugin/skills/gravity-workflow/references/06-safety-hooks.md`.
- **Modular Unit Test Suite & Thin `test_unit.py` Launcher (`tests/test_*.py`, `tests/_unit_helpers.py`, `plugin/scripts/test_unit.py`, `docs/`):**
  - Split monolithic `plugin/scripts/test_unit.py` (`2 371` lines) into 9 domain-focused stdlib test modules under `tests/` (`test_safety_paths.py`, `test_safety_shell.py`, `test_safety_python_ast.py`, `test_safety_inline_scripts.py`, `test_profile_engine.py`, `test_caveman.py`, `test_hooks_runtime.py`, `test_validators.py`, `test_sidecar_widget.py`) sharing `tests/_unit_helpers.py`.
  - Reduced `plugin/scripts/test_unit.py` to a `32`-line stdlib-only (`unittest`, `sys`, `pathlib`) backward-compatible launcher running all unit suites in `tests/` while excluding slow installer/uninstaller integration suites (`test_install.py`, `test_uninstall.py`).

## [0.3.0] - 2026-11-23

### Added
- **Single-Source `plugin/templates/` & Engineering Principles Rule (`plugin/templates/`, `plugin/templates/.agents/rules/06-engineering-principles.md`, `workspace-template/`):**
  - Centralized static starter templates under `plugin/templates/` (using `.agents/rules/01-safety-boundaries.md.tmpl` -> `.agents/rules/01-safety-boundaries.md`) as the single source of truth synchronized byte-for-byte with `workspace-template/`.
  - Added `06-engineering-principles.md` (`trigger: model_decision`, 0 `always_on` tokens) covering verifiable KISS, YAGNI, DRY, SOLID, and CUPID criteria, deployed in both `quickstart` and `full` modes alongside point 7 in `AGENTS.md`.
  - Added repeatable `--with-stack {python,api}` opt-in flag (and `stacks` parameter) to `plugin/scripts/init_workspace.py` for `02-python-stdlib.md` and `03-api-architecture.md`.
- **Charter Size, Encoding & Drift Guards (`plugin/scripts/validators/{base,workspace_validator,plugin_validator}.py`, `plugin/scripts/test_unit.py`):**
  - Added `check_utf8_no_bom_lf()` (`UTF-8` without BOM, `LF` line endings, `MAX_AGENTS_MD_BYTES = 2500`) enforced on workspace `AGENTS.md`, repository root `AGENTS.md`, and `plugin/templates/`.
  - Added byte-for-byte drift validation between `plugin/templates/` (plus `evals/cases.json`) and `workspace-template/`.
- **3 Executed Profiles (`quick` / `standard` / `deep`) & A/B Model Experiment Logging (`plugin/observability/profile_engine.py`, `plugin/observability/state_writer.py`, `docs/modes-and-routing.md`):**
  - Replaced the 4-tier matrix and 14 dead preset keys with 3 executed profiles (`quick`, `standard` default, `deep`) plus backward-compatible aliases (`low→quick`, `medium→standard`, `high/ultra→deep`) and `"allow_pro": false` fallback to `"flash"` (instead of `"inherit"`).
  - Exposed `--plan-model`, `--build-model`, `--review-model` (`inherit` | `flash_lite` | `flash` | `pro` | `reset`) in `profile_engine.py set` and `review_pipeline.py`, logged effective `preset_profile` and `role_models` into `live_state.json` and `runs.jsonl`, and documented the A/B comparison protocol with `gravity-metrics` in `docs/modes-and-routing.md`.
- **Pre-Collected Repo Tree, `validation_summary` & Safe Diff Fallback (`AUD-02-01`) (`plugin/workflows/review_pipeline.py`, `plugin/agents/{planner,builder,reviewer}.md`):**
  - Collected a bounded repository file tree in pure Python (excluding `.git`, `__pycache__`, `node_modules`, `.venv`) and injected it into `Planner` and `Reviewer` prompts without adding `run_command` to read-only agents (`FORBIDDEN_READONLY_TOOLS` intact).
  - Added `validation_summary` to `BUILD_SCHEMA` and safe modified-file snippet fallback when `.gravity_verified.patch` / `git_diff` is empty or `Non-Workspace File Access` restricts external worktree reads.
- **Read-Only `gravity-metrics` Signal Adapter & Bounded `evo` / `caveman` Integration (`plugin/observability/{metrics_signals,profile_engine,caveman_protocol,statusline}.py`, `tests/test_metrics_signals.py`, `plugin/scripts/test_unit.py`):**
  - Added stdlib-only `metrics_signals.py` reading the last `5` archived project sessions from `$GRAVITY_METRICS_HOME/data/history.jsonl` (`> $GRAVITY_HOME/metrics/data/history.jsonl > ~/.gemini/gravity/metrics/data/history.jsonl`) with bounded tail scanning (`MAX_SCAN_BYTES`, `MAX_TAIL_LINES`), strict exclusion of `status == "active"`, `sample_count >= 2` threshold, and fail-safe degradation without importing `gravity-metrics` or writing to disk.
  - Wired `signals["metrics"]` into `collect_signals()` and `compute_score()` with a strictly bounded `metrics_delta` in `[-5, +10]` (`+5` errors/rewinds, `+5` bloat/duplicate spawns, `-5` proven cache/stability), enabled automatic `caveman: full` in `choose_evo_caveman()` on healthy high context (`>= 150_000`) or output cost ratio (`>= 0.35`) while keeping absolute priority on `"lite"` on errors/denies/eval failures, and added synthetic `Signaux Metrics` visibility to `format_status()` / `statusline.py`.
- **Progressive `SKILL.md` Cure & Byte-Cap Guards (`plugin/skills/{gravity-workflow,caveman}/SKILL.md`, `plugin/skills/caveman/references/style-examples.md`, `plugin/scripts/validators/{base,plugin_validator}.py`):**
  - Compacted `plugin/skills/gravity-workflow/SKILL.md` (`15 323` B -> `<= 6 000` B) and `plugin/skills/caveman/SKILL.md` (`6 317` B -> `<= 3 000` B) by offloading detailed tables and FR/EN examples into on-demand `references/` (`skills/caveman/references/style-examples.md` and `skills/gravity-workflow/references/*`), and enforced `MAX_GRAVITY_SKILL_MD_BYTES = 6000`, `MAX_CAVEMAN_SKILL_MD_BYTES = 3000`, and `EXPECTED_CAVEMAN_REFERENCES` in `plugin_validator.py`.

### Changed
- **DRY `init_workspace.py`, Opt-In Stacks, Agent Principles & Root `AGENTS.md` Compaction (`plugin/scripts/init_workspace.py`, `install.py`, `plugin/agents/{planner,reviewer}.md`, `AGENTS.md`, `docs/`):**
  - Removed duplicated template string constants (`AGENTS_MD`, `MCP_CONFIG_JSON`, `RULE_01_*`..`RULE_05_*`, `PROGRESS_*`, `BACKLOG_MD`, `LIVE_STATE_*`, `PROFILE_JSON`, `GRAVITY_GITIGNORE`) from `init_workspace.py`, replacing `PROGRESS_*` / `LIVE_STATE_*` pairs with `_render_progress_md(mode)` / `_render_live_state_json(mode)`, normalizing all writes via `_write_utf8_lf()`, and stopping default deployment of `02-python-stdlib.md` / `03-api-architecture.md` in `--mode full` unless requested via `--with-stack`.
  - Updated `install.py:_load_canonical_templates()` to load canonical templates from `plugin/templates/` (including `01-safety-boundaries.md.tmpl` -> `.agents/rules/01-safety-boundaries.md` and `06-engineering-principles.md`) for `--purge-workspace-templates`.
  - Updated `planner.md` (referencing project `AGENTS.md`/active rules invariants instead of hardcoded Python 3 stdlib, plus KISS/YAGNI/DRY check) and `reviewer.md` (`MAJOR` covers proven source-of-truth duplication and KISS/YAGNI/DRY/SOLID/CUPID violations on the diff; `NIT` covers optional simplifications).
  - Compacted root `AGENTS.md` (`2 261 B` -> `<= 1 650 B`, UTF-8 without BOM, LF) and updated living documentation (`docs/`, `README.md`, skill references).
- **Centralized Per-Workspace Runtime Storage & Global Lifecycle Without `init` (`plugin/observability/state_writer.py`, `plugin/observability/profile_engine.py`, `plugin/hooks/{session_start_caveman,stop_session_logger,hook_runtime}.py`, `plugin/sidecars/live-widget/server.py`, `plugin/observability/statusline.py`):**
  - Centralized `profile.json`, `live_state.json`, `runs.jsonl`, `last_verified.patch`, and `evals/report.{json,md}` under `resolve_workflow_home() / "workspaces" / "<slug>-<sha256_8>/"` (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/`), recording `workspace_path` + `state_path` in `live_workspaces.json` with read-only fallback to `<ws>/.agents/gravity-workflow/profile.json` and zero state files written in target workspaces.
  - Enabled `SessionStart`, `Stop`, `Live` event recording, and `profile_engine.evolve_on_stop()` on all repositories without requiring `/gravity-workflow init`.
- **Behavioral Consultative `evo`, Ephemeral `--no-verify` & `caveman lite` Default (`plugin/observability/{profile_engine,caveman_protocol}.py`, `plugin/hooks/post_tool_format.py`, `plugin/skills/{gravity-workflow,caveman}/SKILL.md`):**
  - Removed the `< 80` file-count heuristic and any downscaling to `low`/`flash_lite`; `evo` now evaluates only `Reviewer` rejections, tool/test/session errors, `guard_deny_recent`, and `eval_pass_rate` to expose `suggested_profile` (`standard` | `deep`) + reason in `status` and `live_state.json` without silently mutating the active profile.
  - Removed the `slow`/`fast` axis (accepted as deprecated no-op aliases), enforced ephemeral-only `unsafe` / `--no-verify` (never persisted in `profile.json`), and standardized `caveman` default to `"lite"` across all 3 profiles.
- **Validators, Templates & Living Documentation (`plugin/scripts/{init_workspace,validate,test_unit}.py`, `plugin/scripts/validators/{base,workspace_validator}.py`, `workspace-template/`, `docs/`, `README.md`, `plugin/README.md`):**
  - Isolated `validators/base.py` dry-runs via `GRAVITY_WORKFLOW_HOME`, updated `workspace-template/` and `workspace_validator.py` for v2 profile schema, and synchronized all living docs.
- **Unified Installer / Uninstaller Lifecycle & Deprecated `uninstall.py` Wrapper (`install.py`, `uninstall.py`, `tests/test_{install,uninstall}.py`, `AGENTS.md`, `docs/`, `README.md`):**
  - Consolidated `--clean-workspace`, `--purge-workspace-templates`, `--workspace-only`, `--clean-registry`, `--clean-legacy`, `--all`, read-only `--uninstall --check` / `--workspace-only --check`, and Core-safe (`is_core_gravity_plugin_dir`) cleanup directly into `install.py`, enforcing `--uninstall` / `--workspace-only` guards against `--init-workspace` (exit `2`).
  - Reduced `uninstall.py` (`717` lines -> `<= 50` lines) to a deprecated thin wrapper emitting `[DEPRECATED]` to `stderr`, prepending `--uninstall` when neither `--uninstall` nor `--workspace-only` is passed, and delegating to `install.main()`.

### Fixed
- **P0 Runtime Imports & Planner Skip in `review_pipeline.py` and `run_suite.py` (`plugin/workflows/review_pipeline.py`, `plugin/evals/run_suite.py`):**
  - Fixed `PLUGIN_ROOT` resolution in `run_workflow` environments lacking `__file__` and `os.environ` (`__file__` -> `args["plugin_root"]` -> `WS_ROOT/plugin` -> `WS_ROOT/.agents/plugins/gravity-workflow` -> `~/.gemini/config/plugins/gravity-workflow`), injected `observability/` and `evals/` into `sys.path`, replaced silent fallbacks with explicit `log()` diagnostics, and automatically skipped the separate `Planner` whenever non-empty `args.get("plan")` is supplied.

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
