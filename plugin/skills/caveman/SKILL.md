---
name: caveman
description: Ultra-concise Caveman communication mode to cut token use (off, lite, full, ultra, auto/evo), commit messages, and context compression.
---

# Gravity Workflow Caveman — ROI Conciseness

*(Companion skill `/caveman` or `/gravity-workflow:caveman`. FR/EN examples: `references/style-examples.md`.)*

## 1. Grammar & CLI

| Command | Effect (`profile.json`) | Behavior |
| :--- | :--- | :--- |
| **`/caveman`** *(bare)* | If `full` -> read-only; else `set --caveman full` | Enables `full` (locked) or prints status |
| **`/caveman lite\|full\|ultra\|off`** | `set --caveman <level>` (`caveman_locked: true`) | Locks intensity; confirms in 1 line |
| **`/caveman auto`** *(alias `evo`)* | `set --caveman auto` (`caveman_locked: false`) | Hands control back to `evo` (`lite` or `full` auto) |
| **`/caveman status`** | Read-only (`get --json`) | Level, lock state, badge, and ROI matrix |
| **`/caveman commit`** | None | Conventional Commits (`<= 50` chars) on the **why** (`git diff`) |
| **`/caveman compress <file>`** | None | Condenses Markdown (frontmatter/code/rules intact), diff + `.original` |

```bash
python3 <plugin_root>/observability/profile_engine.py get --workspace . --json
python3 <plugin_root>/observability/profile_engine.py set --workspace . --caveman <auto|evo|off|lite|full|ultra> --json
```
*(Use `python`/`py -3` on Windows; storage `$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/`.)*

## 2. Intensities, Absolute Invariants & `evo`

- **Levels:** `off` (normal), `lite` *(default: zero filler/fluff, clear sentences)*, `full` *(telegraphic)*, `ultra` *(dense `X -> Y`, manual only)*.
- **Integrity:** code, diffs, commands, paths, URLs, JSON, and error messages stay **strictly intact**.
- **Auto-clarity:** full clear prose mandatory for `PreToolUse` / `01-safety-boundaries.md` blocks or alerts, irreversible confirmations, and `/grill-me` / `ask_question` prompts.
- **`evo` adaptation (`caveman_locked: false`):** **`lite`** priority on `session_error`, `guard_deny_recent > 0`, or `eval_pass_rate < 1.0`; auto-switches to **`full`** when `metrics.available` and (`max_context_tokens >= 150_000` or `high_output_cost_ratio >= 0.35`) — never auto `ultra`/`off`.

## 3. Subagent ROI & Utilities

- **Chat:** active level · **`planner`:** capped at `full` · **`reviewer`:** 1-line (`path:line · proof · rule · fix`, Proof or Silence) · **`builder`/`swarm`:** zero narration · **`evals/`:** `off`.
- **`commit`:** never `commit`/`push` without approval (orchestrator follows `.agents/rules/05-git-versioning.md`).
- **`compress`:** forbidden on `.env`, `secrets/`, `hooks/`, `hooks.json`, `01-safety-boundaries.md` without approval; show diff + savings before write, back up `<name>.original<ext>`.
