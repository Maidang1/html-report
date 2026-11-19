# Other open-source agents: `/goal` and persistent-goal autonomous loops (as of 2026-11-18)

Scope: Hermes Agent, OpenHands (software-agent-sdk + frontend), Goose, pi-mono, Aider, Ralph (snarktank/ralph and Goose's Ralph recipe), plus two more /goal implementations found in the survey: HKUDS/DeepCode and KristjanPikhof/Pi-Agent-Goal (a pi extension). Codex CLI, Claude Code plugins, OpenCode, Gemini CLI, Qwen Code, Cline and Roo are out of scope.

Method notes and caveats:
- Sources were read on 2026-11-18. snarktank/ralph and badlogic/pi-mono were shallow-cloned (ralph HEAD `6c53cb0`, 2026-02-01; pi-mono HEAD `51abb2d`, 2026-11-18). goose (`aaif-goose/goose` HEAD `927005e`, 2026-11-18), aider and Pi-Agent-Goal (HEAD `1425824`, 2026-09-26) were later shallow-cloned into `~/.cache/goal-research-src/` and searched in full. That full-text search corrected an earlier path-only finding: Goose does have `/goal` and `/grind`. Hermes, OpenHands and DeepCode were read as single files over raw.githubusercontent.com / the GitHub trees API. Hermes files are pinned to commit `09ded63c4fedcc8fe975d37f9765797d904fc75d` (2026-11-18T10:49Z). OpenHands, Goose, DeepCode and Pi-Agent-Goal were read at `HEAD` on 2026-11-18, so line numbers may drift.
- `gh` was not installed, so the "/goal" survey used web search plus path-level tree listings (`git/trees?recursive=1`), not full-text code search. A repo with goal logic in files whose names don't contain "goal" could be missed. This affected Goose: the path-only scan missed its `/goal`, which a later full clone corrected. Aider was confirmed by a full-text grep of the clone (no `/goal`, no `cmd_goal`).
- Block's Goose repo has moved: `block/goose` redirects to `aaif-goose/goose`. OpenHands has also moved: `All-Hands-AI/OpenHands` redirects to `OpenHands/OpenHands`, and the agent runtime now lives in `OpenHands/software-agent-sdk`.

---

## Q1. Which open-source agents have `/goal` or a goal-named feature?

### Takeaway
Among the projects in scope, two have a first-class built-in `/goal` driven by a judge model:
- **Hermes Agent** has the richest one: a per-turn judge, a completion contract, quality gates, wait barriers and subgoals.
- **OpenHands** has a `/goal` that runs on its agent server as a REST endpoint plus a judge loop.

The others differ:
- **DeepCode** (HKUDS) also ships a built-in `/goal`, with agent-called `update_goal` tools and a verifier.
- **pi-mono** core has no `/goal`. A third-party pi extension, **Pi-Agent-Goal**, adds one.
- **Goose** has built-in `/goal` and `/grind` (`crates/goose/src/agents/state_machine/ops_retry.rs`). Neither uses a judge model: `/goal` injects one hidden "check whether the goal has been fully met" nudge before the turn finishes, and `/grind` re-injects a "keep working" nudge at every turn end until `GOOSE_MAX_TURNS` (default 1000) stops it. Goose also documents a separate "Ralph Loop" recipe, an outer bash loop with worker and reviewer phases.
- **Aider** has no goal feature.
- **snarktank/ralph** is the reference outer bash-loop harness. It stops on a `<promise>COMPLETE</promise>` sentinel string.

### Cited Findings
- **Hermes Agent**: a built-in `/goal` described as "Persistent session goals — the Ralph loop for Hermes", in `hermes_cli/goals.py`, `hermes_cli/goal_command.py`, `gateway/run_goals.py` and `gateway/slash_commands_goals.py`. The desktop app also has goal UI (`apps/desktop/src/store/goals.ts`, `.../session-control-goal.tsx`), and there is an eval named `evals/goal_command_parity.py` — [goals.py](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/hermes_cli/goals.py), [repo tree](https://github.com/NousResearch/hermes-agent)
- **OpenHands**: a `/goal [--max N] <objective>` command intercepted in the web UI (`src/hooks/chat/use-goal-interceptor.ts` in OpenHands/OpenHands). It calls `POST /api/conversations/{id}/goal`, with `/goal/stop` and `/goal/resume` alongside. The SDK kernel lives in `openhands-sdk/openhands/sdk/conversation/goal/{controller,judge,prompts,runner}.py` — [use-goal-interceptor.ts](https://github.com/OpenHands/OpenHands/blob/main/src/hooks/chat/use-goal-interceptor.ts), [goal package](https://github.com/OpenHands/software-agent-sdk/tree/main/openhands-sdk/openhands/sdk/conversation/goal), [F11-goals feature map](https://github.com/OpenHands/software-agent-sdk/blob/main/.agents/skills/verify-agent-server/references/feature-map/F11-goals.md)
- **DeepCode (HKUDS/DeepCode)**: a `/goal <objective>` in its TUI and a Goal panel in its desktop and web apps. Relevant files:
  - goal state: `core/domain/thread_goal.py`
  - agent tools: `core/harness/tools/goal.py`
  - continuation prompt: `core/application/goal_prompts/continuation.md`
  - verifier and reviewer: `core/agent_runtime/goal_verifier.py`, `core/harness/agents/goal_reviewer.py`
  - docs: `docs/guide/goals-and-headless.md`

  Sources: [DeepCode repo](https://github.com/HKUDS/DeepCode), [continuation.md](https://github.com/HKUDS/DeepCode/blob/main/core/application/goal_prompts/continuation.md)
- **Pi-Agent-Goal (KristjanPikhof)**: "Pi extension that gives coding agents persistent /goal workflows with branch-aware state, source imports, progress tools, and explicit start handoffs" — [repo](https://github.com/KristjanPikhof/Pi-Agent-Goal)
- **pi-mono core**: a `grep -ri goal` over `packages/` at HEAD `51abb2d` (2026-11-18) found no goal command. The bundled `packages/coding-agent/examples/extensions/` directory has no goal or loop extension; it does include `plan-mode`, `todo.ts`, `handoff.ts` and `subagent`. "goal" appears only in design docs under `apps/p2/docs/`. Source: local clone of [badlogic/pi-mono](https://github.com/badlogic/pi-mono)
- **Goose `/goal` and `/grind`**: `crates/goose/src/agents/execute_commands.rs` L41-49 registers `CommandDef { name: "goal", description: "Set a goal the agent must satisfy before finishing, or clear with /goal off" }` and `CommandDef { name: "grind", description: "Set a goal the agent pursues relentlessly until max_turns, or clear with /grind off" }`. The logic lives in `crates/goose/src/agents/state_machine/ops_retry.rs` (`RetryOperation`, the same operation that handles recipe retry/success checks). The state is `GoalState { goal, grind }` in `crates/goose/src/session/extension_data.rs` L101-115 — [execute_commands.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/execute_commands.rs), [ops_retry.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/state_machine/ops_retry.rs), [extension_data.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/session/extension_data.rs)
- **Goose Ralph recipe**: the earlier path-level search found only the Ralph tutorial and recipes as goal/loop paths. Those files are:
  - `documentation/docs/tutorials/ralph-loop.md`
  - `documentation/src/pages/recipes/data/recipes/ralph-loop.sh`
  - `documentation/src/pages/recipes/data/recipes/ralph-work.yaml`
  - `documentation/src/pages/recipes/data/recipes/ralph-review.yaml`

  Source: [ralph-loop tutorial](https://github.com/aaif-goose/goose/blob/main/documentation/docs/tutorials/ralph-loop.md)
- **Aider**: `aider/commands.py` defines these `cmd_*` commands: model, editor_model, weak_model, chat_mode, models, web, commit, lint, clear, reset, tokens, undo, diff, add, drop, git, test, run, exit, quit, ls, help, ask, code, architect, context, ok, voice, paste, read_only, map, map_refresh, settings, load, save, multiline_mode, copy, report, editor, edit, think_tokens, reasoning_effort, copy_context. None is goal-related, and no tree path contains "goal". The latest commit seen was 2026-05-22 — [commands.py](https://github.com/Aider-AI/aider/blob/main/aider/commands.py)
- **snarktank/ralph**: `ralph.sh` ("Ralph Wiggum - Long-running AI agent loop") plus `prompt.md` (for Amp), `CLAUDE.md` (for Claude Code) and `prd.json` — [repo](https://github.com/snarktank/ralph)
- Web survey: many small `/goal` implementations exist as plugins or skills rather than one standard. A GitHub topic `goal-mode` exists. OpenCode plugins (prevalentWare/opencode-goal-plugin, william-ricchiuti/OpenCode-goal-plugin) are now superseded by OpenCode's built-in `/goal` in 2.2.1+, which is out of scope here — [goal-mode topic](https://github.com/topics/goal-mode), [william-ricchiuti/OpenCode-goal-plugin](https://github.com/william-ricchiuti/OpenCode-goal-plugin)

### Inferences
- Hermes explicitly calls its design the Ralph loop, and its draft-contract feature is labelled "after Codex's 'draft the goal'". The Codex `/goal` design appears to be the reference that Hermes and DeepCode borrow from: DeepCode's `get_goal`/`update_goal` tool names match it, and so does its `budget_limited` status.
- OpenHands chose a different architecture. Its loop is driven by the server around `conversation.run()`. Hermes, by contrast, uses post-turn hooks inside a chat gateway, and Ralph uses an external bash loop.

### Gaps
- Goose's `/goal` and `/grind` do not appear on its docs site beyond one line in `documentation/docs/gdk/concepts/operations.md`: "`RetryOperation` | Evaluate recipe success checks and goal/grind continuation or retry behavior." The release that introduced them was not identified.
- DeepCode's verifier and reviewer code (`goal_verifier.py`, `goal_runtime.py`) was not read in full, so its runtime continuation driver is only partially documented below.

---

## Q2. Per project: files, line ranges, key code (verbatim)

### Takeaway
All the implementations share one skeleton: persist the objective → run a turn → judge (an LLM judge, an agent tool call, or a sentinel) → either inject a continuation user message or stop. They differ in where state lives and who decides "done":
- **Hermes**: state in a SQLite `state_meta` key; an auxiliary-model judge decides.
- **OpenHands**: state in event-log `ConversationStateUpdateEvent` rows; a judge LLM over the full transcript decides.
- **DeepCode / Pi-Agent-Goal**: the agent itself calls a completion tool, with verification.
- **Ralph / Goose Ralph recipe**: state in files on disk; a sentinel string or a reviewer-written file decides.
- **Goose `/goal`/`/grind`**: state in the session `extension_data` key `goal` (v0). The agent decides by itself; there is no judge, only a nudge message injected by a state-machine operation at turn end.

### Cited Findings

#### Hermes Agent (NousResearch/hermes-agent @ 09ded63)

The state and judge logic is in `hermes_cli/goals.py` (1768 lines) — [source](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/hermes_cli/goals.py).

**Module docstring (L1-7):**
  > "Persistent session goals — the Ralph loop for Hermes. A goal is a free-form objective that stays active across turns; after each turn an auxiliary-model judge decides whether it is satisfied. The continuation prompt is a normal user message appended via `run_conversation` (no system-prompt mutation or toolset swap — prompt caching stays intact). Judge failures are fail-OPEN (`continue`); the turn budget is the backstop."

**Constants (L32-57):**
  ```python
  DEFAULT_MAX_TURNS = 20
  DEFAULT_JUDGE_TIMEOUT = 30.0
  DEFAULT_JUDGE_MAX_TOKENS = 4096
  _JUDGE_RESPONSE_SNIPPET_CHARS = 4000
  DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES = 3
  DEFAULT_MAX_CONSECUTIVE_TRANSPORT_FAILURES = 5
  DEFAULT_GATE_TIMEOUT_SECONDS = 300
  DEFAULT_GATE_MAX_RETRIES = 3
  _MAX_BARRIER_WAIT_S = 30 * 60
  _GATE_OUTPUT_TAIL_CHARS = 3000
  ```

**`GoalState` dataclass (L420-453):**
  ```python
  goal: str
  status: str = "active"          # active | paused | done | cleared
  turns_used: int = 0
  max_turns: int = DEFAULT_MAX_TURNS
  created_at: float = 0.0
  last_turn_at: float = 0.0
  last_verdict: Optional[str] = None        # "done" | "blocked" | "continue" | "wait" | "skipped"
  last_reason: Optional[str] = None
  paused_reason: Optional[str] = None       # why we auto-paused (budget, etc.)
  consecutive_parse_failures: int = 0
  consecutive_transport_failures: int = 0
  subgoals: list[str]
  waiting_on_pid: Optional[int] = None
  waiting_on_session: Optional[str] = None
  waiting_until: float = 0.0
  waiting_on_delegations: int = 0
  waiting_reason: Optional[str] = None
  waiting_since: float = 0.0
  contract: GoalContract   # outcome / verification / constraints / boundaries / stop_when
  gates: list[GoalGate]
  ```

**Persistence (L501-502, L622-664):**
- The key is `goal:{session_id}` in `db.state_meta`, the session SQLite DB (`state.db` per the gateway comments). The value is the JSON from `GoalState.to_json()`.
- `clear_goal` sets `status="cleared'`. Its docstring says the row is "preserved for audit, status=cleared".
- `migrate_goal_to_session(old, new)` moves a goal when the session id changes (L666).

**`GoalManager.set` (L1160-1169):** creates a fresh state with `status="active", turns_used=0`, and `max_turns` set from the argument or from config.

**`resume(reset_budget=True)` (L1186-1194):** sets `turns_used = 0` and clears any wait barrier.

**`evaluate_after_turn` (L1472-1556):** the core decision. Excerpt:
  ```python
  state = self._state
  if state is None or state.status != "active":
      return _decision(state.status if state else None, False, None, "inactive", "no active goal", "")
  # Parked on a live process or an unexpired deadline: quiesce without burning a turn.
  if self.is_waiting():
      return self._waiting_decision(state)
  state.turns_used += 1
  state.last_turn_at = time.time()
  # Gates run BEFORE the judge ...
  gate_decision = self._check_gates()
  if gate_decision is not None:
      if gate_decision.get("should_continue") and state.turns_used >= state.max_turns:
          return self._budget_pause(state, "gate_failed", gate_decision.get("reason", ""), note=" (a quality gate is still failing)")
      return gate_decision
  verdict, reason, parse_failed, wait_directive, transport_failed = judge_goal(...)
  ...
  if verdict == "wait" and wait_directive: parked = self._apply_wait_directive(...)
  if verdict == "blocked":
      return self._pause_decision(f"judged unachievable: {reason}", "blocked", reason,
          f"🚫 Goal judged unachievable — paused: {reason} Re-scope with /goal set, or override with /goal resume.")
  if verdict == "done":
      state.status = "done"; self._save()
      return _decision("done", False, None, "done", reason, f"✓ Goal achieved: {reason}")
  if n_tx >= DEFAULT_MAX_CONSECUTIVE_TRANSPORT_FAILURES: return self._pause_decision(...)
  if n_parse >= DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES: return self._pause_decision(...)
  if state.turns_used >= state.max_turns:
      return self._budget_pause(state, "continue", reason)
  self._save()
  return _decision("active", True, self.next_continuation_prompt(), "continue", reason,
      f"↻ Continuing toward goal ({state.turns_used}/{state.max_turns}): {reason}")
  ```

**`judge_goal` (L880-946):**
- It calls `agent.auxiliary_client.call_llm`.
- The goal is truncated to 2000 chars and the last response to 4000.
- Prompt priority is contract > subgoals > plain.
- Errors fail open to `"continue"`, with `transport_failed=True`.

**Quality gates, `_check_gates` (L1294-1349):**
- User-added shell commands (`/goal gate add <cmd>`) run before the judge.
- A failure becomes the continuation prompt.
- Once `attempts > max_retries`, the goal pauses.

**Kanban variant (L1584-1700):** `run_kanban_goal_loop` uses the same judge, but the worker must end with `kanban_complete` / `kanban_request_review` / `kanban_block` tool calls.

**`hermes_cli/goal_command.py` (218 lines)** — [source](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/hermes_cli/goal_command.py):
- Docstring: "The CLI's /goal semantics, shared by every goal command surface. Adapters supply authorization and schedule the returned prompt; this module alone parses subcommands and mutates goal state. It never changes conversation history."
- Subcommand table (L97-108):
  - `""`/`status` → status line
  - `show` → status plus contract
  - `pause`
  - `resume`/`continue`/`unpause`
  - `clear`/`stop`/`done`
  - `unwait`
  - `wait <pid> [reason]`
  - `gate [list|add <cmd>|remove <N>|clear]`
  - `draft <objective>`
  - `-- <text>`, a verbatim goal for text starting with a control word
  - any other text → set the goal
- `_set` (L137-171) parses inline `field: value` contract lines through `goals.parse_contract`, or drafts a contract with the auxiliary LLM (`draft`). It returns `GoalCommandResult(output, goals.goal_kick_prompt(state.goal, last_user_message), kickoff=True)`. In other words, the first turn's prompt is the goal text itself.
- User-facing set message (L156-170): "⊙ Goal set ({budget}-turn budget): {goal}" … "After each turn, a judge model checks if the goal is done{against}. Hermes keeps working until it is, you pause/clear it, or the budget is exhausted. Use /goal status, /goal show, /goal pause, /goal resume, /goal clear."

**`gateway/run_goals.py` (598 lines), class `GatewayGoalsMixin`** — [source](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/gateway/run_goals.py):
- L32: the section header comment reads `# ── /goal — persistent cross-turn goals (Ralph-style loop)`.
- L33-48: `_goal_max_turns_from_config()` reads `goals.max_turns` from `hermes_cli.config.load_config()`, default 20.
- L312-368: `_post_turn_goal_continuation`. Its docstring: "Run the goal judge after a gateway turn (AFTER delivery) and, if still active, enqueue a continuation through the adapter FIFO so a simultaneous real user message takes priority." The judge runs in `asyncio.to_thread`, with the comment "judge_goal() is a synchronous aux-LLM HTTP call (10-40 s; would block Discord heartbeats)". The continuation is enqueued as a synthetic TEXT `MessageEvent` (`self._enqueue_fifo(_quick_key, self._synthetic_prompt_event(source, prompt), adapter)`). L361 comments: "Enqueue via the adapter's FIFO so a user message already in flight preempts naturally."
- L370-392: `_run_post_turn_hooks`. Its comments say empty or interrupted/errored responses must not drive `/goal`, and that the classic CLI viewer instead "judges and continues through `session.automation`".

#### OpenHands (OpenHands/OpenHands frontend + OpenHands/software-agent-sdk)

**Frontend `src/hooks/chat/use-goal-interceptor.ts` (L1-65)** — [source](https://github.com/OpenHands/OpenHands/blob/main/src/hooks/chat/use-goal-interceptor.ts):
  ```ts
  /** Optional leading "--max <n>" (or "--max=<n>") flag that caps the audit rounds. */
  const MAX_FLAG = /^--max(?:=|\s+)(\d+)\s*/;
  /**
   * Intercepts "/goal [--max N] <objective>" submissions and starts a goal loop
   * on the agent server: it pursues the objective, judging completion after each
   * run until done or the cap is reached. Live progress streams back as goal
   * ConversationStateUpdateEvents (rendered by GoalStatusBanner). ...
   */
  ...
  const request: StartGoalRequest = { objective };
  if (maxIterations && maxIterations >= 1) { request.max_iterations = maxIterations; }
  startGoal(conversationId, request)
  ```
  A bare `/goal` shows the toast `GOAL$OBJECTIVE_REQUIRED`. `src/stores/goal-store.ts` is a zustand store, `statusByConversation: Record<string, GoalStatus>`.

**`openhands-sdk/.../goal/controller.py` (153 lines)** — [source](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/goal/controller.py):
- `GoalOutcome.status: Literal["complete","capped"]`
- `GoalStatusName = Literal["running","complete","capped","interrupted"]`
- `GoalStatus{active, status, iteration, max_iterations, objective, verdict}`
- `GoalContinue{followup, verdict}`, `GoalDone{outcome}`, `GoalAsked{ask_id, question}`
- `GoalController.__init__(objective, judge_llm, *, max_iterations=10)`
- `on_run_finished` (L117-153):
  ```python
  pending = find_pending_ask(events)
  if pending is not None:
      return GoalAsked(ask_id=pending.ask_id, question=pending.question)
  verdict = judge_goal(self.judge_llm, self.objective, events)
  self.iteration += 1
  if verdict.complete:
      return GoalDone(outcome=GoalOutcome(status="complete", iterations=self.iteration, verdict=verdict))
  if self.iteration >= self.max_iterations:
      return GoalDone(outcome=GoalOutcome(status="capped", iterations=self.iteration, verdict=verdict))
  missing = verdict.missing or "Some requirements are not yet verified."
  followup = FOLLOWUP_PROMPT.format(iteration=self.iteration, missing=missing)
  return GoalContinue(followup=followup, verdict=verdict)
  ```

**`judge.py` (130 lines)** — [source](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/goal/judge.py):
- `GoalVerdict{score: float 0..1, complete: bool, missing: str}`.
- The transcript is every `LLMConvertibleEvent` rendered as `role: text`, excluding the system prompt.
- The judge call is forced non-streaming.
- An unparseable reply becomes `GoalVerdict(score=0.0, complete=False, missing="Judge verdict could not be parsed.")`, i.e. it fails toward continuing.
- `complete` defaults to `score >= 1.0` when the reply omits it.

**`runner.py`, `run_goal()` (L32-65), the synchronous SDK driver** — [source](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/goal/runner.py):
  ```python
  controller = GoalController(objective, judge_llm, max_iterations=max_iterations)
  pending = find_pending_ask(conversation.state.events)
  if pending is not None: return GoalAsked(...)
  conversation.send_message(controller.start(), sender="goal")
  while True:
      conversation.run()
      step = controller.on_run_finished(conversation.state.events)
      if isinstance(step, GoalDone): return step.outcome
      if isinstance(step, GoalAsked): return step
      conversation.send_message(step.followup, sender="goal")
  ```
  Its docstring: "Unlike a critic (which the run loop consults *inside* one `run()`), this drives the conversation from the outside, so it composes with whatever critic the agent already has."

**`openhands-agent-server/.../event_service.py` (2671 lines)** — [source](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-agent-server/openhands/agent_server/event_service.py):
- L332-334: `_goal_loop_task: asyncio.Task | None` and `_goal_loop_outcome`.
- L953-967 (`send_message`): "A normal user message supersedes any active /goal loop in this conversation. The goal loop's own messages pass _from_goal_loop=True." → `await self.stop_goal_loop()`.
- L1873-1925 (`start_goal_loop`): `judge_llm` defaults to the agent's LLM (`getattr(self._conversation.agent, "llm", None)`). Under `_run_lock` it raises `goal_already_running` if a loop task is live.
- L1957-~2085 (`_run_goal_loop`):
  - emits `running`
  - sends `RESUME_PROMPT if resume else controller.start()`
  - loops `await self.run()`, waits for the run task, checks the execution status
  - stops with `interrupted` on PAUSED or ERROR
  - continues past STUCK (comment: "The stuck detector is a heuristic that often fires during legitimate iteration ... The goal loop already has an authoritative judge")
  - judges in an executor
  - on `GoalAsked`, sets `interrupted` and returns
  - on `GoalDone`, emits complete/capped
  - otherwise emits `running` with the verdict and sends the followup
  - `CancelledError` produces `interrupted`, unless the service is closing

**F11-goals feature map** — [source](https://github.com/OpenHands/software-agent-sdk/blob/main/.agents/skills/verify-agent-server/references/feature-map/F11-goals.md):
- Routes: `POST /api/conversations/{conversation_id}/goal`, `/goal/stop`, `/goal/resume`.
- `max_iterations` defaults to 10, minimum 1.
- Status is persisted only as `ConversationStateUpdateEvent` rows with `key: "goal"`, and "that is what `/goal/resume` continues from, also after a restart".
- A loop that was active at server restart is reported `interrupted`.
- `/goal` returns 409 while a run or goal is in flight, and 429 when `max_concurrent_runs` (`OH_MAX_CONCURRENT_RUNS`, default 10) is full.
- `/goal/resume` after complete/capped returns 400 `no_resumable_goal`.

#### snarktank/ralph (HEAD 6c53cb0, 2026-02-01)

**`ralph.sh` (113 lines)** — [source](https://github.com/snarktank/ralph/blob/main/ralph.sh):
- Usage: `./ralph.sh [--tool amp|claude] [max_iterations]`, with `MAX_ITERATIONS=10` as the default.
- L42-65: archives `prd.json` and `progress.txt` to `archive/<date>-<branch>` when `prd.json.branchName` differs from `.last-branch`.
- L84-113, the core loop:
  ```bash
  for i in $(seq 1 $MAX_ITERATIONS); do
    if [[ "$TOOL" == "amp" ]]; then
      OUTPUT=$(cat "$SCRIPT_DIR/prompt.md" | amp --dangerously-allow-all 2>&1 | tee /dev/stderr) || true
    else
      OUTPUT=$(claude --dangerously-skip-permissions --print < "$SCRIPT_DIR/CLAUDE.md" 2>&1 | tee /dev/stderr) || true
    fi
    if echo "$OUTPUT" | grep -q "<promise>COMPLETE</promise>"; then
      echo "Ralph completed all tasks!"
      echo "Completed at iteration $i of $MAX_ITERATIONS"
      exit 0
    fi
    echo "Iteration $i complete. Continuing..."
    sleep 2
  done
  echo "Ralph reached max iterations ($MAX_ITERATIONS) without completing all tasks."
  echo "Check $PROGRESS_FILE for status."
  exit 1
  ```

**`prd.json.example`:**
- Top-level fields: `{project, branchName, description, userStories[]}`.
- Each story has `{id, title, description, acceptanceCriteria[], priority, passes:false, notes}`.

Source: [prd.json.example](https://github.com/snarktank/ralph/blob/main/prd.json.example)

#### Goose (aaif-goose/goose @ 927005e): built-in `/goal` and `/grind`

The state is in `crates/goose/src/session/extension_data.rs` L101-115:
```rust
pub struct GoalState {
    pub goal: Option<String>,
    pub grind: Option<String>,
}
impl ExtensionState for GoalState {
    const EXTENSION_NAME: &'static str = "goal";
    const VERSION: &'static str = "v0";
}
```
It is persisted in the session's `extension_data`, written through `GooseEffect::extension_state(&state)`.

**`execute_commands.rs`:**
- L94-108: `is_clear_goal_param` accepts `"off" | "clear" | "none"`.
- `command_starts_turn()`: "Setting a `/goal` or `/grind` (with a description, not the query or `off` forms) makes the agent start pursuing it immediately."

**`ops_retry.rs`, `RetryOperation::goal_command` (L80-161):**
- A bare `/goal` shows `"Current goal: …"` or `"No goal set. Use `/goal <description>` to set one."`.
- `off|clear|none` → `"Goal cleared. The agent will finish normally."`.
- Otherwise:
  - set the field
  - reply `"Goal set. The agent will verify this goal is met before finishing:\n\n> {params}"` (grind: `"Grind goal set. The agent will keep working until max_turns is reached:\n\n> {params}"`)
  - mark the command message as visible to the user but hidden from the agent
  - push the kickoff user message below, visible to the agent but hidden from the user (`with_visibility(false, true)`):
    ```rust
    format!("Start working toward this goal now:\n\n**Goal:** {params}")
    ```

**`RetryOperation::run` (L201-250)**, called at every turn boundary:
```rust
let messages = messages_since_kickoff(conversation)?;
if !ends_turn(messages) { return not_applicable(); }
let state = GoalState::of(session);
if !self.goal_was_nudged(messages) {
    if let Some(goal) = &state.goal {
        let nudge = format!(
            "Before finishing, check whether the following goal has been fully met:\n\n\
             **Goal:** {goal}\n\n\
             If not, continue working toward it."
        );
        let mut message = Message::user().with_text(&nudge).with_visibility(false, true);
        self.set_message_meta(&mut message, NUDGED, serde_json::json!(true));
        emit.message(Message::assistant().with_system_notification(
            SystemNotificationType::InlineMessage, format!("Goal: {goal}")));
        return applied([message.into()]);
    }
}
if let Some(grind) = &state.grind {
    let nudge = format!(
        "Keep working. The grind goal is not yet complete:\n\n\
         **Goal:** {grind}\n\n\
         Continue until it is fully done."
    );
    let message = Message::user().with_text(&nudge).with_visibility(false, true);
    emit.message(Message::assistant().with_system_notification(
        SystemNotificationType::InlineMessage, format!("Grind: {grind}")));
    return applied([message.into()]);
}
if state.goal.is_some() {
    return applied([GooseEffect::extension_state(&GoalState::default())?]);
}
// ... then recipe `retry` success checks (max_retries, on_failure command, conversation reset)
```
- `ends_turn` (`crates/goose-agent/src/operation.rs` L72-83) is true when the last message is an assistant message with no error, no ToolRequest and no ActionRequired.
- `goal_was_nudged` scans the messages since the kickoff for the `nudged` meta marker. `/goal` therefore nudges at most once per user kickoff.
- After the nudged turn ends, the `state.goal.is_some()` branch resets `GoalState` to its default. Because both fields reset, this also clears any grind; the goal is consumed.

**Budget, `crates/goose-agent/src/max_turns.rs`:**
- `MaxTurnsOperation` yields with a `ResourceExhausted` error message once `assistant_turn_count(messages) >= max_turns`: `MAX_TURNS_MESSAGE = "I've reached the maximum number of actions I can do without user input. Would you like me to continue?"`.
- From half the budget onward it adds `<turn-budget>{turns_taken}/{max_turns} used</turn-budget>` to the request.
- The default is `GOOSE_MAX_TURNS` = 1000 (`crates/goose/src/config/base.rs` L1359: `get_param::<u32>("GOOSE_MAX_TURNS").unwrap_or(1000)`).
- In the builder, `MaxTurnsOperation` is ordered after `RetryOperation` and `StopHookOperation` (`state_machine_builder.rs` L181-196).

Sources: [ops_retry.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/state_machine/ops_retry.rs), [execute_commands.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/execute_commands.rs), [max_turns.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose-agent/src/max_turns.rs), [config/base.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/config/base.rs), [state_machine_builder.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/state_machine_builder.rs)

#### Goose (aaif-goose/goose): Ralph Loop recipe

**`ralph-loop.sh` (219 lines)** — [source](https://github.com/aaif-goose/goose/blob/main/documentation/src/pages/recipes/data/recipes/ralph-loop.sh):
- `MAX_ITERATIONS="${RALPH_MAX_ITERATIONS:-10}"`, which it asks for interactively with a cost warning.
- L134: `STATE_DIR=".goose/ralph"`.
- At start it copies the task into `task.md` and runs `rm -f` on `review-result.txt review-feedback.txt work-complete.txt work-summary.txt`.
- The loop (L161-216):
  ```bash
  for i in $(seq 1 "$MAX_ITERATIONS"); do
      echo "$i" > "$STATE_DIR/iteration.txt"
      GOOSE_PROVIDER="$WORKER_PROVIDER" GOOSE_MODEL="$WORKER_MODEL" goose run --recipe "$RECIPE_DIR/ralph-work.yaml" || { ...; exit 1; }
      if [ -f "$STATE_DIR/RALPH-BLOCKED.md" ]; then echo "✗ BLOCKED"; cat "$STATE_DIR/RALPH-BLOCKED.md"; exit 1; fi
      GOOSE_PROVIDER="$REVIEWER_PROVIDER" GOOSE_MODEL="$REVIEWER_MODEL" goose run --recipe "$RECIPE_DIR/ralph-review.yaml" || { ...; exit 1; }
      if [ -f "$STATE_DIR/review-result.txt" ]; then
          RESULT=$(cat "$STATE_DIR/review-result.txt" | tr -d '[:space:]')
          if [ "$RESULT" = "SHIP" ]; then ... echo "COMPLETE: $(date)" > "$STATE_DIR/.ralph-complete"; exit 0
          else  # REVISE: print review-feedback.txt
      ...
      rm -f "$STATE_DIR/work-complete.txt"; rm -f "$STATE_DIR/review-result.txt"
  done
  echo "✗ Max iterations ($MAX_ITERATIONS) reached"; exit 1
  ```
- The script prints "For best results, use different models for cross-model review."

#### DeepCode (HKUDS/DeepCode)

**`core/domain/thread_goal.py`** — [source](https://github.com/HKUDS/DeepCode/blob/main/core/domain/thread_goal.py):
- `class ThreadGoalStatus(StrEnum)`: `ACTIVE="active"`, `PAUSED="paused"`, `BLOCKED="blocked"`, `BUDGET_LIMITED="budget_limited"`, `COMPLETE="complete"`.
- `GoalDecisionSource`: `user`, `agent`, `runtime`, `migration`.
- `ThreadGoal` fields include `token_budget: int | None`, `tokens_used`, and `time_budget_seconds` ("Active time the Goal may use, like `token_budget` for tokens").
- `budget_spent` is true when `tokens_used >= token_budget` or active time ≥ `time_budget_seconds`.
- Editing an ACTIVE goal whose budget is spent → `BUDGET_LIMITED`.

**`core/harness/tools/goal.py` (180 lines)**, three tools — [source](https://github.com/HKUDS/DeepCode/blob/main/core/harness/tools/goal.py):
- `get_goal`: "Read the latest Thread Goal, status, usage, and whether this Turn may update it."
- `update_goal(status: enum["complete","blocked"], reason: str ≤2000)`. Description verbatim: "Update the current Thread Goal. Use 'complete' only when current evidence (code, commands you ran, tests you checked cover it) proves every requirement of the latest Goal; the claim may be checked independently. The reason names only evidence from this conversation (commands you ran and what they showed), never a check you did not run. Use 'blocked' only for a persistent external blocker with no safe remaining action; it is accepted from the Goal's third Turn on. Goal identity comes from this Turn; do not guess IDs."
- `propose_goal_checks`. Description verbatim: "Propose the Goal's checklist: one entry per requirement of the Goal, each with the command that proves that requirement where one exists (a focused test rather than the whole suite when you can). The user confirms the list; after that, DeepCode itself runs the commands whenever the Goal is claimed complete, and a command that does not exit 0 turns the claim back. Proposing replaces an earlier proposal; it never changes a confirmed checklist."

#### Pi-Agent-Goal (KristjanPikhof/Pi-Agent-Goal), a pi-mono extension

**`src/runtime.ts` (407 lines)** — [source](https://github.com/KristjanPikhof/Pi-Agent-Goal/blob/main/src/runtime.ts):
- L17: `export const DEFAULT_GOAL_CONTINUATION_MAX_TURNS = 3;`
- L249-262:
  ```ts
  const maxTurns = getMaxContinuationTurns(api);
  if (turnCount >= maxTurns) return stopDecision("max-turns", goal.goalId);
  api.sendUserMessage(renderContinuationPrompt(goal), { deliverAs: "followUp", ... })
  ```
  The continuation is a pi `sendUserMessage` with `deliverAs: "followUp"`.
- L362: `api.appendEntry(GOAL_CONTINUATION_CUSTOM_TYPE, record)` stores continuation state as a custom session entry.

### Inferences
- Hermes `turns_used` counts both user turns and continuation turns. Its docstring says: "Both real user prompts and our own continuations increment `turns_used` — both consume model budget". OpenHands counts only judged rounds, and a judge exception does not count.
- Pi-Agent-Goal's continuation is per goal: `state.turnCounts.get(goal.goalId)`, and the cap comes from the `goal-continuation-max-turns` flag, default 3 (`runtime.ts` L381-386). Exactly when the counts reset across sessions was not traced.

### Gaps
- `gateway/slash_commands_goals.py` (348 lines) was downloaded but not excerpted. It wires the slash command into chat platforms.
- Pi-Agent-Goal (cloned later): `src/commands.ts` L91 `pi.registerCommand("goal", { description: "Set or view the goal for a long-running task", ... })`. Subcommands are `show|status|import|create|start|edit|pause|resume|clear|complete`, with flags `--yes/-y --replace --start`. `src/tools.ts` registers the tools `get_goal`, `create_goal`, `propose_goal_draft`, `complete_goal` and `update_goal_progress`. State is stored as pi session custom entries (`GOAL_CUSTOM_TYPE = "goal-state"` in `src/state.ts` L11, written via `pi.appendEntry`). It is rebuilt from the current branch's entries (`createGoalStateSnapshot(branchEntries)`), which is why the state is "branch-aware". The `complete_goal` tool's schema was not excerpted.
- DeepCode's runtime driver, which decides when to inject `continuation.md` and how the verifier and reviewer run, was not read.

---

## Q3. Exact start / continuation / completion prompts (verbatim)

### Takeaway
How each tool starts and continues:
- **Start**: Hermes and OpenHands send the raw objective as the first user message. Ralph and Goose instead re-send a fixed instruction file every iteration in a fresh context.
- **Continuation**: Hermes uses a templated `[Continuing toward your standing goal]` message. OpenHands sends the judge's `missing` text inside `FOLLOWUP_PROMPT`.

How each detects completion:
- **Hermes**: a strict 4-verdict JSON judge (done/blocked/continue/wait).
- **OpenHands**: a `{score, complete, missing}` JSON judge.
- **Ralph**: the agent prints `<promise>COMPLETE</promise>`.
- **Goose**: a reviewer model writes `SHIP` to a file.
- **DeepCode / Pi-Agent-Goal**: the agent calls a tool (`update_goal(status="complete")` / `complete_goal`).

### Cited Findings

#### Hermes — `hermes_cli/goals.py` @ 09ded63 ([source](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/hermes_cli/goals.py))

**Start / kick (L965-983):**
- The first turn sends the goal text itself.
- If the goal is at least 400 chars and makes up at least 80% of the last user message, i.e. the user just pasted it, the first turn sends this instead:
  `GOAL_ALREADY_SEEN_KICK = "[Goal set] Continue with the goal you were just given; there is no need to re-read it."`

**`CONTINUATION_PROMPT_TEMPLATE` (L60-66):**
  ```
  [Continuing toward your standing goal]
  Goal: {goal}

  Continue working toward this goal. Take the next concrete step. If you believe the goal is complete, state so explicitly and stop. If you are blocked and need input from the user, say so clearly and stop.
  ```

**`CONTINUATION_PROMPT_WITH_CONTRACT_TEMPLATE` (L70-81):**
  ```
  [Continuing toward your standing goal]
  Goal: {goal}

  Completion contract:
  {contract_block}

  Continue working toward the outcome above. Take the next concrete step. Stay within the stated boundaries and do not violate the constraints. Before claiming the goal is done, satisfy the Verification criterion and show the concrete evidence (command output, file contents, test result). If you hit the stated stop condition or are otherwise blocked and need user input, say so clearly and stop.
  ```

**`CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE` (L84-94):**
  ```
  [Continuing toward your standing goal]
  Goal: {goal}

  Additional criteria the user added mid-loop:
  {subgoals_block}

  Continue working toward the goal AND all additional criteria. Take the next concrete step. If you believe the goal and every additional criterion are complete, state so explicitly and stop. If you are blocked and need input from the user, say so clearly and stop.
  ```

**`CONTINUATION_PROMPT_GATE_FAILED_TEMPLATE` (L97-111):**
  ````
  [Continuing toward your standing goal — a quality gate failed]
  Goal: {goal}

  The quality gate command below must pass before this goal can be declared done, and it just failed (attempt {attempt}/{max_retries}):
    $ {command}
  Exit code: {exit_code}
  Output (tail):
  ```
  {output}
  ```

  Fix the underlying problem so this gate passes, then re-run it to confirm. Do not declare the goal complete while any gate fails. If the gate itself is wrong or cannot pass, say so clearly and stop.
  ````

**`JUDGE_SYSTEM_PROMPT` (L113-172), verbatim:**
  ```
  You are a strict judge evaluating whether an autonomous agent has achieved a user's stated goal. You receive the goal text, the agent's most recent response, and — when present — a list of background processes the agent has running. Decide one of four verdicts.

  DONE — the goal is fully satisfied:
  - The response explicitly confirms the goal was completed, OR
  - The response clearly shows the final deliverable was produced.
  DONE requires the deliverable to actually exist. If the response only explains why the goal cannot be reached, the verdict is BLOCKED, not DONE.

  BLOCKED — the goal cannot be satisfied as stated:
  - The response explains the goal is genuinely unachievable (impossible, out of scope, no valid path to the deliverable), or refuses to fabricate a deliverable that cannot exist, OR
  - The response explains progress is blocked and the next step needs user input to proceed.
  Return BLOCKED with the reason describing what is blocking. BLOCKED is a refusal, not a completion — never return BLOCKED for a goal that was achieved.
  When the block is an error the agent hit (an HTTP status, an API, sign-in or token failure), quote the error text verbatim in the reason and attribute it only to a provider, service or credential the response itself names. Never infer one the response does not name — an unnamed 401 belongs to the model provider the agent was calling, not to some other service's token.

  WAIT — the goal is NOT done, but the next step is to wait for async work to finish rather than act again. Choose this ONLY when the agent's progress is genuinely gated on something running on its own:
  - A background process listed below is still running AND the response shows the agent is waiting on its result (e.g. a CI poller, build, test run, deploy). If the process has a session id, return it in ``wait_on_session`` — that releases when the process exits OR its watch_patterns trigger fires (use this for a long-lived watcher that signals mid-run and may never exit). Otherwise return its pid in ``wait_on_pid`` (releases on exit only).
  - The agent says it is rate-limited / backing off / must wait a fixed period — return seconds in ``wait_for_seconds``.
  - The agent has delegated subagents still running (stated below as active delegations) and the response says it is waiting on them with nothing else dispatchable — return ``wait_for_seconds`` between 600 and 1800. Their results wake the agent on their own; re-poking it now only produces a status recap.
  Picking WAIT parks the loop without burning a turn; it resumes automatically when the pid exits or the time elapses. Do NOT pick WAIT just because work remains — only when re-poking now would be pure busy-work because the agent can't progress until the async thing finishes.

  CONTINUE — not done, and there is a concrete next step the agent can take right now. This is the default when in doubt.

  Reply ONLY with a single JSON object on one line. Shapes:
  {"verdict": "done", "reason": "<one sentence>"}
  {"verdict": "blocked", "reason": "<one sentence>"}
  {"verdict": "continue", "reason": "<one sentence>"}
  {"verdict": "wait", "wait_on_session": "<id>", "reason": "<one sentence>"}
  {"verdict": "wait", "wait_on_pid": <int>, "reason": "<one sentence>"}
  {"verdict": "wait", "wait_for_seconds": <int>, "reason": "<one sentence>"}
  The legacy shape {"done": <true|false>, "reason": "..."} is still accepted (true=done, false=continue).
  ```

**`JUDGE_USER_PROMPT_TEMPLATE` (L186-192):**
  ```
  Goal:
  {goal}

  Agent's most recent response:
  {response}

  {background_block}Current time: {current_time}

  Is the goal satisfied — done, blocked, continue, or wait?
  ```

**Background-process and delegation blocks (L175-184):**
- `"Active delegations: the agent has {count} delegated subagent batch(es) still running; their results are delivered to it automatically when they finish.\n\n"`
- `"Background processes the agent currently has running (it may be waiting on one of these):\n{background_lines}\n\n"`

**`JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE` (L195-211)** ends with:
  > "Decision: For each numbered criterion above, find concrete evidence in the agent's response that the criterion is satisfied. Do not accept generic phrases like 'all requirements met' or 'implying it was done' — require specific evidence (a file contents excerpt, an output line, a command result). If ANY criterion lacks specific evidence in the response, the goal is NOT done — return CONTINUE (or WAIT if blocked on a listed background process).\n\nIs the goal AND every additional criterion satisfied?"

**`JUDGE_USER_PROMPT_WITH_CONTRACT_TEMPLATE` (L214-236)**, decision rules verbatim:
  ```
  Decision rules:
  - The goal is DONE only when the Verification criterion is satisfied AND the response shows concrete evidence of it (a command result, file contents excerpt, test/benchmark output) — not a claim like 'done' or 'all tests pass' without evidence.
  - If any stated Constraint was violated, the goal is NOT done — CONTINUE.
  - If the response shows the agent is waiting on a listed background process to satisfy the Verification criterion (e.g. CI is the verification and it's still running), return WAIT on that process instead of re-poking — re-poking now would be pure busy-work.
  - If the response explains the work is genuinely unachievable or hits the stated Stop condition and needs user input, the goal is NOT done — return BLOCKED with the reason describing the block.
  - Otherwise the goal is NOT done — CONTINUE.

  Is the goal satisfied per its completion contract — done, blocked, continue, or wait?
  ```

**`DRAFT_CONTRACT_SYSTEM_PROMPT` (L239-261)**, used by `/goal draft`:
  ```
  You turn a user's plain-language objective into a structured completion contract for an autonomous coding agent. The contract has five fields:
  - outcome: the single end state that must be true when done
  - verification: the specific test / command / artifact that PROVES the outcome (must be concrete and checkable)
  - constraints: what must NOT change or regress
  - boundaries: which files, dirs, tools, or systems are in scope
  - stop_when: the condition under which the agent should stop and ask for human input instead of pushing on

  Infer sensible, specific values from the objective and any project context implied by it. Prefer concrete verification (a named test command, a build, a benchmark) over vague phrases. Keep each field to one or two sentences. If a field genuinely cannot be inferred, use an empty string for it.

  Reply ONLY with a single JSON object on one line:
  {"outcome": "...", "verification": "...", "constraints": "...", "boundaries": "...", "stop_when": "..."}
  ```

**Kanban continuation and finalize prompts (L1584-1607):**
- `KANBAN_GOAL_CONTINUATION_TEMPLATE`:
  > "[Continuing toward this kanban task — judge says it is not done yet]\nReason: {reason}\n\nTake the next concrete step toward completing the task. When the work is genuinely finished, call kanban_complete with a summary. If it is a code change that needs same-card review before counting as done, call kanban_request_review with a summary instead. If you are blocked and need human input, call kanban_block with a reason. Do not stop without calling one of them."
- `KANBAN_GOAL_FINALIZE_TEMPLATE`:
  > "[The work looks complete, but the task is still open]\nReason: {reason}\n\nIf the task is genuinely done, call kanban_complete now with a short summary of what you did. If it is a code change awaiting same-card review, call kanban_request_review with that summary instead. If something still blocks completion, call kanban_block with the reason instead."

#### OpenHands — `openhands-sdk/openhands/sdk/conversation/goal/prompts.py` (59 lines; [source](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/goal/prompts.py))

**Start:** `GoalController.start()` returns `self.objective` unchanged. It is sent as a user message with `sender="goal"`.

**`JUDGE_SYSTEM_PROMPT` (L6-18).** The missing space in "JSONand" is in the source:
  ```
  You are auditing whether a long-running GOAL has been COMPLETED by an AI software agent.

  Derive the concrete requirements implied by the objective. For EACH requirement,
  look for authoritative evidence in the transcript: file contents, command
  output, or test results produced by the agent. Treat missing, uncertain, or
  merely-claimed-but-unverified evidence as NOT satisfied.

  Respond with STRICT JSONand nothing else, in exactly this shape:
  {"score": <float 0.0-1.0, probability the FULL objective is provably done>, "complete": <true|false>, "missing": "<concise description of what remains, or an empty string if complete>"}
  ```

**`JUDGE_USER_PROMPT` (L21-28):**
  ```
  <objective>
  {objective}
  </objective>

  <transcript>
  {transcript}
  </transcript>
  ```

**`FOLLOWUP_PROMPT` (L46-53):**
  ```
  The goal is NOT yet complete (audit iteration {iteration}).
  Outstanding: {missing}

  Inspect the real current state of the workspace (do not rely on memory). For each remaining requirement, make concrete progress and gather authoritative evidence by running the relevant tests/commands. Keep the full objective intact and finish only once every requirement is provably satisfied.
  ```

**`RESUME_PROMPT` (L56-59):**
  ```
  Resuming a goal that was paused or interrupted. Re-check the real current state of the workspace (do not rely on memory) and continue making concrete, verified progress toward the original objective. Finish only once every requirement is provably satisfied.
  ```

#### snarktank/ralph — `prompt.md` (Amp) / `CLAUDE.md` (Claude Code), 108 lines ([source](https://github.com/snarktank/ralph/blob/main/prompt.md))

The whole file is the per-iteration prompt, piped fresh each iteration. Key parts verbatim, starting with L1-16:
  ```
  # Ralph Agent Instructions

  You are an autonomous coding agent working on a software project.

  ## Your Task

  1. Read the PRD at `prd.json` (in the same directory as this file)
  2. Read the progress log at `progress.txt` (check Codebase Patterns section first)
  3. Check you're on the correct branch from PRD `branchName`. If not, check it out or create from main.
  4. Pick the **highest priority** user story where `passes: false`
  5. Implement that single user story
  6. Run quality checks (e.g., typecheck, lint, test - use whatever your project requires)
  7. Update AGENTS.md files if you discover reusable patterns (see below)
  8. If checks pass, commit ALL changes with message: `feat: [Story ID] - [Story Title]`
  9. Update the PRD to set `passes: true` for the completed story
  10. Append your progress to `progress.txt`
  ```

The completion section (L94-108):
  ```
  ## Stop Condition

  After completing a user story, check if ALL stories have `passes: true`.

  If ALL stories are complete and passing, reply with:
  <promise>COMPLETE</promise>

  If there are still stories with `passes: false`, end your response normally (another iteration will pick up the next story).

  ## Important

  - Work on ONE story per iteration
  - Commit frequently
  - Keep CI green
  - Read the Codebase Patterns section in progress.txt before starting
  ```

The Amp prompt also asks for a progress line `Thread: https://ampcode.com/threads/$AMP_CURRENT_THREAD_ID` "so future iterations can use the `read_thread` tool". `CLAUDE.md` is identical except that it says CLAUDE.md instead of AGENTS.md and drops the Amp thread lines (from `diff prompt.md CLAUDE.md` on the clone).

#### Goose Ralph recipes ([ralph-work.yaml](https://github.com/aaif-goose/goose/blob/main/documentation/src/pages/recipes/data/recipes/ralph-work.yaml), [ralph-review.yaml](https://github.com/aaif-goose/goose/blob/main/documentation/src/pages/recipes/data/recipes/ralph-review.yaml))

**Work-phase `instructions` (ralph-work.yaml L5-30):**
  ```
  You are in a RALPH LOOP - one iteration of work.

  Your work persists through FILES ONLY. You will NOT remember previous iterations.

  STATE FILES (in .goose/ralph/):
  - task.md = The task you need to accomplish (READ THIS FIRST)
  - iteration.txt = Current iteration number
  - review-feedback.txt = Feedback from last review (if any)
  - work-complete.txt = Create when task is DONE (reviewer will verify)

  FIRST: Check your state
  1. cat .goose/ralph/task.md (YOUR TASK)
  2. cat .goose/ralph/iteration.txt 2>/dev/null || echo "1"
  3. cat .goose/ralph/review-feedback.txt 2>/dev/null
  4. ls -la to see existing work

  THEN: Make progress
  - If review-feedback.txt exists, ADDRESS THAT FEEDBACK FIRST
  - Read existing code/files before modifying
  - Make meaningful incremental progress
  - Run tests/verification if applicable

  FINALLY: Signal status
  - If task is complete: echo "done" > .goose/ralph/work-complete.txt
  - Always write a summary: echo "what I did" > .goose/ralph/work-summary.txt
  ```

**Review-phase `instructions` (ralph-review.yaml L5-33):**
  ```
  You are a CODE REVIEWER in a Ralph Loop.

  Your job: Review the work done and decide SHIP or REVISE.

  You are a DIFFERENT MODEL than the worker. Your fresh perspective catches mistakes.

  STATE FILES (in .goose/ralph/):
  - task.md = The original task (READ THIS FIRST)
  - work-summary.txt = What the worker claims to have done
  - work-complete.txt = Exists if worker claims task is complete

  REVIEW CRITERIA:
  1. Does the code/work actually accomplish the task?
  2. Does it run without errors?
  3. Is it reasonably complete, not half-done?
  4. Are there obvious bugs or issues?

  BE STRICT but FAIR:
  - Don't nitpick style if functionality is correct
  - DO reject incomplete work
  - DO reject code that doesn't run
  - DO reject if tests fail

  OUTPUT:
  If approved: echo "SHIP" > .goose/ralph/review-result.txt
  If needs work:
    echo "REVISE" > .goose/ralph/review-result.txt
    echo "specific feedback" > .goose/ralph/review-feedback.txt
  ```

#### DeepCode — `core/application/goal_prompts/continuation.md` (31 lines; [source](https://github.com/HKUDS/DeepCode/blob/main/core/application/goal_prompts/continuation.md))
  ```
  Continue working toward the active Thread Goal.

  Goal:
  {{OBJECTIVE}}

  Use the ordinary Session history, tools, permissions, Skills, and execution
  environment. Inspect the current workspace and make concrete progress; do not
  merely restate a plan. Keep the Goal's full scope: do not swap in a narrower or
  easier version because it is simpler to build or to test.

  Before calling update_goal(status="complete"), audit the work against the
  workspace as it is now. Treat completion as unproven until the audit proves it:
  - List every requirement of the Goal, including the rules of any document it
    points to, such as a spec or an issue.
  - For each one, look at the evidence that would prove it: the code, a command
    you run, a test you have checked actually covers it. Passing tests prove only
    what they test.
  - Classify each as proved, contradicted, incomplete, weakly supported, or
    missing. Only proved counts.
  - When a check's result matters, do not pipe it into another command: in
    `pytest | tail` the exit code is tail's, not the tests'.
  Call update_goal(status="complete") only when every requirement is proved;
  otherwise keep working. In its reason, and whatever you tell the user, report
  only what the tool results in this conversation show; say plainly what you did
  not verify, and never state a result you did not observe.

  Call get_goal whenever you need the latest objective or status. Call
  update_goal(status="blocked") only when the same external blocker has stopped
  all progress across at least three turns and no safe action remains; hard, slow or
  uncertain work is not blocked. The runtime supplies Goal identity and
  concurrency context automatically.
  ```

#### Pi-Agent-Goal — `src/prompts.ts` L86-96 ([source](https://github.com/KristjanPikhof/Pi-Agent-Goal/blob/main/src/prompts.ts))
  ```ts
  export function renderContinuationPrompt(goal: GoalState): string {
  	return [
  		`Continue working toward active goal ${escapeXml(goal.goalId)}.`,
  		"Use the goal system section as the source of truth and make one concrete, evidence-backed step.",
  		"Update progress only when the state has meaningfully changed.",
  		"If all required work is complete, call complete_goal with evidence.",
  		"Do not mark the goal complete unless current evidence proves every requirement is satisfied.",
  	]
  ```
The comment line "the goal system section" implies the goal is also rendered into the system prompt (unverified).

### Inferences
- Hermes and OpenHands both keep the agent's system prompt untouched and inject continuation as an ordinary user message. Hermes states this is for prompt-cache stability.
- OpenHands' judge sees the whole transcript, minus the system prompt. Hermes' judge sees only the last response, capped at 4000 chars, plus a background-process list. Hermes therefore depends on the agent's final message showing evidence, which is why its contract and subgoal judge prompts demand "specific evidence" in the response.

### Gaps
- Pi-Agent-Goal's start prompt (`renderGoalStartPrompt`) and drafting prompt were not fetched.
- DeepCode's verifier and reviewer prompts (`goal_reviewer.py`) were not fetched.

---

## Q4. State machines (triggers, guards) with diagrams

### Takeaway
- **Hermes**: active ⇄ paused, plus done and cleared. It also has a non-turn-burning "waiting" sub-state inside active. Five different guards cause auto-pause.
- **OpenHands**: running → complete | capped | interrupted. Only `interrupted` is resumable.
- **Goose `/goal`**: a one-shot "self-check before finishing" nudge. **Goose `/grind`**: an unconditional re-prompt at every turn end, up to max_turns. Neither has a judge.
- **Ralph / Goose Ralph recipe**: plain bounded `for` loops whose state lives in files and whose exit codes signal the result (0 = complete/SHIP, 1 = max iterations / blocked / error).
- **DeepCode**: active / paused / blocked / budget_limited / complete, where the agent's tool calls drive complete and blocked, and the runtime drives budget_limited.

### Cited Findings
- Hermes transitions, from `GoalManager` methods and `evaluate_after_turn` ([goals.py L1151-1212, L1472-1556](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/hermes_cli/goals.py)):
  ```
                /goal <text> | /goal draft <obj>   (set(): turns_used=0, status=active; kick = goal text)
                               │
                               ▼
     ┌──────────────────── ACTIVE ◄──────────── /goal resume (turns_used=0, clear wait) ──┐
     │   after each turn (gateway post-turn hook, after delivery):                         │
     │   is_waiting()? ── yes ──► WAITING (no turn burned; released on pid exit /          │
     │        │                    session trigger / deadline / delegation count drop /    │
     │        │                    /goal unwait; max 30 min for pid/session barriers)      │
     │        no                                                                           │
     │   turns_used += 1                                                                   │
     │   gates fail (attempt ≤ max_retries) ──► continue with GATE_FAILED prompt           │
     │   gates fail (attempts > max_retries) ─────────────────────────────► PAUSED ────────┤
     │   judge: wait ──► WAITING                                                           │
     │   judge: blocked ──────────────────────────────────────────────────► PAUSED ────────┤
     │   judge: done ──► DONE (terminal; /goal resume not meaningful)                      │
     │   ≥5 consecutive transport failures ───────────────────────────────► PAUSED ────────┤
     │   ≥3 consecutive parse failures ───────────────────────────────────► PAUSED ────────┤
     │   turns_used ≥ max_turns ──────────────────────────────────────────► PAUSED ────────┘
     │   else: continue → enqueue CONTINUATION prompt via adapter FIFO
     └── /goal pause ──► PAUSED (reason "user-paused")     /goal clear|stop|done ──► CLEARED (row kept)
  ```
- Hermes continuation is driven by the gateway post-turn hook (`_run_post_turn_hooks` → `_post_turn_goal_continuation`). It enqueues a synthetic user message into the adapter FIFO, so a real user message already queued runs first. The classic CLI viewer uses `session.automation` instead — [run_goals.py L312-392](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/gateway/run_goals.py)
- OpenHands transitions ([event_service.py `_run_goal_loop`](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-agent-server/openhands/agent_server/event_service.py), [F11-goals.md](https://github.com/OpenHands/software-agent-sdk/blob/main/.agents/skills/verify-agent-server/references/feature-map/F11-goals.md)):
  ```mermaid
  stateDiagram-v2
    [*] --> running: POST /goal {objective,max_iterations=10}\n(409 if run/goal active, 429 if capacity full)
    running --> running: run() ends FINISHED or STUCK → judge → not complete & iteration<max\n→ send FOLLOWUP_PROMPT (sender="goal")
    running --> complete: judge.complete == true
    running --> capped: iteration >= max_iterations
    running --> interrupted: exec status PAUSED/ERROR | ask_user pending | POST /goal/stop\n| user message via /events or socket (supersede) | judge exception | server restart
    interrupted --> running: POST /goal/resume → send RESUME_PROMPT (keeps objective, cap, iteration)
    complete --> [*]
    capped --> [*]: /goal/resume → 400 no_resumable_goal
  ```
- Ralph: `for i in 1..MAX_ITERATIONS { fresh agent process <- prompt file; if stdout contains "<promise>COMPLETE</promise>" → exit 0 }; exit 1`. The per-story state is `prd.json` `passes:false→true`, which the agent sets itself, plus `progress.txt` as an append-only learnings log and git commits. Branch change → the previous run is archived — [ralph.sh](https://github.com/snarktank/ralph/blob/main/ralph.sh), [prompt.md](https://github.com/snarktank/ralph/blob/main/prompt.md)
- Goose Ralph: `for i in 1..MAX { worker(model A, fresh) → RALPH-BLOCKED.md? exit 1 → reviewer(model B, fresh) → review-result.txt == SHIP ? exit 0 : (REVISE → feedback file for next worker) ; rm work-complete/review-result }; exit 1` — [ralph-loop.sh](https://github.com/aaif-goose/goose/blob/main/documentation/src/pages/recipes/data/recipes/ralph-loop.sh)
- Goose `/goal` and `/grind`, inside its operation-based agent state machine (`RetryOperation.run` at each turn end) ([ops_retry.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/state_machine/ops_retry.rs)):
  ```
  /goal <text> → GoalState.goal = text; inject hidden "Start working toward this goal now" → agent runs
  turn ends (assistant msg, no tool request) ─┬─ goal set & not yet nudged → inject hidden "Before finishing, check whether…" (meta nudged=true) → agent runs again
                                              ├─ grind set → inject hidden "Keep working. The grind goal is not yet complete…" → agent runs again (every turn)
                                              ├─ goal set & already nudged → reset GoalState to default (goal AND grind cleared) → finish
                                              └─ neither → recipe retry checks / finish
  any time: assistant_turn_count ≥ GOOSE_MAX_TURNS → "I've reached the maximum number of actions…" (yield to user)
  /goal off|clear|none, /grind off → field = None
  ```
- DeepCode states: `active | paused | blocked | budget_limited | complete`. Decisions are tagged with a source (`user | agent | runtime | migration`). `update_goal(blocked)` is accepted only "from the Goal's third Turn on". A completion claim is "turned back" if any confirmed checklist command exits non-zero — [thread_goal.py](https://github.com/HKUDS/DeepCode/blob/main/core/domain/thread_goal.py), [goal.py](https://github.com/HKUDS/DeepCode/blob/main/core/harness/tools/goal.py)

### Inferences
- The tools split by whether a goal can be resumed after the budget runs out:
  - **Hermes**: yes. Budget exhaustion is a resumable pause, and `/goal resume` resets `turns_used` to 0.
  - **OpenHands**: no. A capped goal is terminal; the user has to start a new `/goal`.
  - **Ralph**: no, though re-running the script continues naturally because state is on disk.
- A done verdict means something different in each system: a judge of the last response (Hermes), a judge of the transcript (OpenHands), the agent's self-declaration (Ralph), a second model (Goose), or the agent's claim plus deterministic checks (DeepCode, and Hermes gates).

### Gaps
- Hermes's exact `_waiting_decision` / `is_waiting` release logic (L1401-1463) was summarized from field comments, not read line by line.

---

## Q5. Safeguards: iteration/token/time budgets, stuck detection, user interrupt

### Takeaway
- **Hermes**: the most defensive. It has:
  - a turn budget (20)
  - judge-failure auto-pauses (3 parse / 5 transport)
  - gate retry caps (3) and a gate timeout (300 s)
  - a wait-barrier cap (30 min)
  - a BLOCKED verdict that pauses instead of spinning
  - user-message priority via the FIFO
- **OpenHands**:
  - an iteration cap (10)
  - supersede-on-user-message
  - `/goal/stop`
  - pause/error halts
  - deliberately ignores the STUCK detector, deferring to the judge
- **DeepCode**: has token and time budgets.
- **Ralph / Goose Ralph recipe**: rely on a max-iteration count (10) and Ctrl-C only. They run with permission bypass flags.
- **Goose `/grind`**: bounded only by `GOOSE_MAX_TURNS` (default 1000 assistant turns per kickoff). Goose `/goal` is self-limiting (one nudge).
- **Pi-Agent-Goal**:
  - 3 continuation turns by default (flag `goal-continuation-max-turns`)
  - no-progress detection (goal `updatedAt` unchanged → stop)
  - user typing stops the chain

### Cited Findings
- Hermes budgets and guards:
  - `DEFAULT_MAX_TURNS = 20`, overridable by `goals.max_turns` in config.
  - Judge timeout 30 s; judge max tokens 4096. The comment says the original 200 "reliably truncated it and tripped the auto-pause".
  - 3 consecutive parse failures → pause, with a hint to route the judge to a stricter model (example `google/gemini-3-flash-preview`).
  - 5 consecutive transport failures → pause.
  - Gate timeout 300 s and 3 retries; gate output tail 3000 chars.
  - Wait barrier at most 30 min for pid/session waits.

  Sources: [goals.py L32-57, L1472-1556](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/hermes_cli/goals.py), [run_goals.py L33-48](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/gateway/run_goals.py)
- Hermes budget-pause message: "⏸ Goal paused — {turns_used}/{max_turns} turns used{note}. Use /goal resume to keep going, or /goal clear to stop." — [goals.py L1465-1470](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/hermes_cli/goals.py)
- Hermes gate workspace refusal: if gates can't run in the workspace, the goal pauses with "⏸ Goal paused — quality gates not run: {refusal}…". Gate commands need explicit authorization: `dispatch_goal_command(..., authorize_gate=...)` "returns a denial or None (explicit approval)" — [goals.py L1294-1349](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/hermes_cli/goals.py), [goal_command.py L174-185](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/hermes_cli/goal_command.py)
- Hermes user interrupt and priority:
  - The continuation is enqueued via the adapter FIFO, "so a simultaneous real user message takes priority".
  - Interrupted or errored empty responses do not drive `/goal`.
  - `hermes pause` leaves heartbeat ticks unclaimed (heartbeat is a sibling feature).

  Source: [run_goals.py L169-176, L312-392](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/gateway/run_goals.py)
- OpenHands:
  - `max_iterations` defaults to 10, minimum 1 (UI flag `--max N`).
  - A user message cancels the loop (`stop_goal_loop()` in `send_message` unless `_from_goal_loop`).
  - `/goal/stop` cancels only the loop; the in-flight round continues until `/interrupt`.
  - A PAUSED or ERROR execution status → `interrupted`.
  - The STUCK status is deliberately ignored: "a STUCK run is not a reason to halt the whole goal -- proceed to the judge".
  - A pending `ask_user` → stop without judging.
  - A failed judge call does not count as a round.
  - The run-slot limit is `OH_MAX_CONCURRENT_RUNS` (default 10).
  - Resuming after a failed judge "never reports more rounds than `max_iterations`".

  Sources: [event_service.py](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-agent-server/openhands/agent_server/event_service.py), [controller.py](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/goal/controller.py), [F11-goals.md](https://github.com/OpenHands/software-agent-sdk/blob/main/.agents/skills/verify-agent-server/references/feature-map/F11-goals.md)
- OpenHands has no token or time budget in the goal module; only the iteration cap was found — [controller.py](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/goal/controller.py)
- DeepCode budgets: `token_budget`, `tokens_used`, `time_budget_seconds`; `budget_spent` → `BUDGET_LIMITED`. The `blocked` claim is only accepted from turn 3, and the continuation prompt says blocked requires "the same external blocker has stopped all progress across at least three turns" — [thread_goal.py](https://github.com/HKUDS/DeepCode/blob/main/core/domain/thread_goal.py), [continuation.md](https://github.com/HKUDS/DeepCode/blob/main/core/application/goal_prompts/continuation.md)
- Pi-Agent-Goal: `DEFAULT_GOAL_CONTINUATION_MAX_TURNS = 3`, then `stopDecision("max-turns", …)` — [runtime.ts](https://github.com/KristjanPikhof/Pi-Agent-Goal/blob/main/src/runtime.ts)
- Pi-Agent-Goal continuation driver (`src/runtime.ts`), hooked onto pi lifecycle events:
  - `agent_settled` → `finishRunningGoalContinuation` then `maybeQueueGoalContinuation`
  - `agent_start` → `startQueuedGoalContinuation`
  - `input` from a non-extension source while a continuation is queued or running → `stopGoalContinuation(..., "user-interrupt")` (L121-128)

  `maybeQueueGoalContinuation` (L240-264) refuses to queue in any of these cases:
  - the agent is not idle (`busy`)
  - messages are pending
  - the goal is not active
  - it was previously stopped for `no-progress` or `user-interrupt`
  - `turnCount >= maxTurns`
  - the goal is stale

  Otherwise it calls `api.sendUserMessage(renderContinuationPrompt(goal), { deliverAs: "followUp", expandPromptTemplates: false })`. In `finishRunningGoalContinuation` (L305-325), if the goal's `updatedAt` did not advance during the continuation turn, the stop reason is `"no-progress"`. In other words, the agent must call a progress or complete tool each turn, or the loop stops — [runtime.ts](https://github.com/KristjanPikhof/Pi-Agent-Goal/blob/main/src/runtime.ts)
- Goose: `MaxTurnsOperation` stops at `GOOSE_MAX_TURNS` (default 1000) with "I've reached the maximum number of actions I can do without user input. Would you like me to continue?". This is the only bound on `/grind`. `/goal` adds at most one nudge per kickoff (the `nudged` meta flag) and then clears itself — [max_turns.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose-agent/src/max_turns.rs), [ops_retry.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/state_machine/ops_retry.rs)
- Ralph:
  - The only budget is `MAX_ITERATIONS` (default 10).
  - Each iteration is a fresh process (`amp --dangerously-allow-all` or `claude --dangerously-skip-permissions --print`).
  - The agent's exit status is swallowed (`|| true`), so a crashing agent just burns an iteration.
  - There is a 2 s sleep between iterations.
  - There is no stuck detection.

  Source: [ralph.sh](https://github.com/snarktank/ralph/blob/main/ralph.sh)
- Goose Ralph:
  - `RALPH_MAX_ITERATIONS` (default 10), with an interactive cost warning.
  - A worker or reviewer `goose run` failure → `exit 1`.
  - `RALPH-BLOCKED.md` → `exit 1`.
  - The developer extension has a 600 s timeout for the worker and 300 s for the reviewer (recipe `extensions.timeout`).

  Sources: [ralph-loop.sh](https://github.com/aaif-goose/goose/blob/main/documentation/src/pages/recipes/data/recipes/ralph-loop.sh), [ralph-work.yaml](https://github.com/aaif-goose/goose/blob/main/documentation/src/pages/recipes/data/recipes/ralph-work.yaml)

### Inferences
- Both judge-based designs fail open to "continue" when the judge breaks: Hermes on transport errors, OpenHands on parse errors (score 0). Each therefore needs a hard cap. Hermes adds the counters for consecutive judge failures; OpenHands instead treats a judge exception as `interrupted`.
- Ralph-style loops trade safety for simplicity. Correctness rests on the agent honestly flipping `passes` and printing the sentinel. Goose's variant adds a cross-model reviewer specifically to counter worker self-certification.

### Gaps
- Hermes may enforce a wall-clock or token budget elsewhere (e.g. the `/loop` or heartbeat modules `hermes_cli/loops.py`), but none was found in the goal module itself.
- How Ralph-style harnesses in the wider ecosystem (other "Ralph Wiggum" bash scripts beyond snarktank and Goose) differ was not surveyed, because of tool-call limits.
