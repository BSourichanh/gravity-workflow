---
name: gravity-workflow
description: Unified Gravity Workflow orchestrator: 3 profiles (quick, standard, deep), consultative evo mode, ephemeral --no-verify, and /caveman.
---

# Gravity Workflow — Ground your Antigravity agents

*(Primary skill `/gravity-workflow` or `/gravity-workflow:gravity-workflow`, companion `/caveman`.)*

## Absolute Constraints
- **Centralized storage, zero pollution:** hooks, `Live`, and `profile_engine.py` work on any repository without `init` under `$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/workspaces/<slug>-<sha256_8>/` (read-only fallback `.agents/gravity-workflow/profile.json`). `.agents/` remains optional (`init`).
- **`PreToolUse` fail-closed guards always ON:** secret protection, destructive command blocks, and harness self-protection stay active even with `unsafe` / `--no-verify`.
- **Zero internal jargon & anti-overwrite:** never use artificial numbering (`Module XX`); never overwrite an existing file without a validated diff.
- **Git & versioning (orchestrator only):** before `commit`/`push`/tag/release (human approval required), apply `.agents/rules/05-git-versioning.md` (dedicated branch, checks, Conventional Commits, SemVer + `CHANGELOG.md`, `vX.Y.Z` tags); forbidden to subagents and `swarm`.
- **Context budget & native tools:** `AGENTS.md`/`GEMINI.md` + `always_on` rules `< 20 000` tokens (`<= 24 000` B/file). Call `run_workflow`, `swarm`, `ask_question`, `invoke_subagent` directly; suggest `/boost`, `/teamwork`, `/grill-me`, `/learn` to the user.
- **Python & profile:** use `python3`, `python`, or `py -3` per `PATH`; always route profile changes through `observability/profile_engine.py`.

## 1. 3-Axis Grammar & CLI (`profile_engine.py`)

Leading positional keywords (**whole-word** match only) or subcommands (`status`, `evals`, `init` alone or with their dedicated arguments):
- **Mode (`quick` | `standard` | `deep` | `evo`):** `quick`*(low)*, `standard`*(default, medium)*, `deep`*(high/ultra)* lock (`locked: true`); `evo`*(evolutive)* enables consultative mode (`locked: false`, `suggested_profile` on `Stop` with `metrics` signals bounded `[-5, +10]`). `slow`/`fast` accepted as no-op aliases.
- **Ephemeral verification (`unsafe` | `--no-verify`):** disables review/tests for **1 task or 1 `run_workflow` call**, never persisted (`PreToolUse` remains ON).
- **Conciseness (`/caveman`):** default `lite` (`auto` switches to `full` on high context/output cost via `metrics`; `evals/` excluded).
- **Scoping (`/grill-me`):** explore the repository first; clarify one question at a time via `ask_question` (`(Recommended)` first) or recommend `/plan /grill-me`.

```bash
python3 <plugin_root>/observability/profile_engine.py get --workspace . --json
python3 <plugin_root>/observability/profile_engine.py set --workspace . [--mode <quick|standard|deep|evo>] [--caveman <level>] [--plan-model <tier>] [--build-model <tier>] [--review-model <tier>] --json
```

## 2. Profiles, Project State & Micro-Card

- **`quick`:** direct single-thread (`builder` `flash`, `0` remediation, `planner`/`reviewer` skipped).
- **`standard` *(default)*:** direct if trivial; `review_pipeline.py` otherwise (`planner` `flash` skipped if `plan` given, `builder` `flash`, `reviewer` `pro`, `1` remediation).
- **`deep`:** full `review_pipeline.py` (`planner` `pro`, `builder` `flash`, `reviewer` `pro`, `2` remediations); suggest `/plan /grill-me`, `/boost`, or `/swarm`.
- *Overrides:* `"allow_pro": false` falls back `pro` to `flash`; `--plan-model`/`--build-model`/`--review-model` and explicit `Args` take priority (`references/05-workflows-and-routing.md`).
- **State:** initialized (`PROGRESS.md`) -> micro-card + execute; audit (`.agents/` without `PROGRESS.md`) -> check budget/triggers; bare repo -> centralized storage with zero local writes (`init` optional via `init_workspace.py`, `--ensure-git-rule` only if `.agents/` exists or on `init`).

```text
Gravity Workflow [X/8 or centralized] · Profile: <quick|standard|deep> (evo|locked) · Verify: <safe|no-verify ephemeral> · Stage: <Stage Name>
Health: Rules <OK/N-A> · Guard Fail-Closed <ON/OFF> · Caveman: <level> (auto|locked) · Evals <pass/total|N/A>
Suggestion evo: <standard|deep> (<behavioral reason>) · Next action: <Concrete action>
```

## 3. Utilities (`status`, `evals`) & On-Demand References

- **`/gravity-workflow status`:** run `profile_engine.py get --workspace . --json`, read centralized storage (`runs.jsonl`, `evals/report.json`) and `.agents/`, then output **4 blocks** (progress/`role_models`/`suggested_profile`, rules/safety health, evals/`runs.jsonl` + `Signaux Metrics (...)` if available, 1-3 actions). App: `generative_ui` HTML; CLI: Markdown (`references/09-observability-dashboard.md`).
- **`/gravity-workflow evals [CASE-ID...]`:** `run_workflow` on `evals/run_suite.py` (`Args`: `case_ids`, `plugin_root`, `candidate_model`, `judge_model`, `level`), score + `[manual]`, `/learn` + `cases.json` on failure (`references/07-eval-suite.md`).

Load **only** the required reference sheet:
- `references/00-quick-start.md` *(Quick start & init)*
- `references/01-project-scoping.md` *(Scoping)*
- `references/02-charter-and-rules.md` *(Charter & rules)*
- `references/03-subagents-architecture.md` *(Subagents)*
- `references/04-tools-and-mcp.md` *(Tools & MCP)*
- `references/05-workflows-and-routing.md` *(Profile matrix, A/B & router)*
- `references/06-safety-hooks.md` *(Safety guards & hooks)*
- `references/07-eval-suite.md` *(Evaluation suite)*
- `references/08-session-learning.md` *(Adaptation `evo`, Metrics & `/learn`)*
- `references/09-observability-dashboard.md` *(Observability, `status` & `/gravity dashboard`)*
