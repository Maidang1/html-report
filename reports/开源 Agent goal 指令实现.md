# 拆解开源 Agent 让模型持续工作的五种方法

截至 2026-11-18，开源 Agent 里的 `/goal`（以及 ralph loop、grind、boulder 等同类机制）在实现上收敛到同一个骨架：**把目标持久化 → 跑一轮 → 判定是否完成 → 没完成就注入一条续跑消息再跑一轮**。各家的差别集中在三处：下一轮由谁触发（Stop hook 拦截、空闲事件、内核调度器、外部 bash 循环），完成由谁判定（模型自己调工具、独立裁判模型、确定性的命令或文件检查），以及死循环靠什么兜底。做得最完整的是 **Codex CLI** 和 **Qwen Code**。两者都在内核里实现了带 5～6 个状态的目标状态机，给模型提供 `get_goal` / `update_goal` 工具，并用「连续 3 轮无进展就停」加 token 预算防止空转。Qwen 更进一步：模型只能「提议」完成，由一个 temperature 0、只看按来源分类的证据的独立 Verifier 拍板。**Hermes、OpenHands、opencode 和 Claude Code 内置 `/goal`** 走「裁判模型」路线，每轮结束后让另一个 LLM 输出 JSON 判决。**官方 ralph-loop、snarktank/ralph** 则是最朴素的一端，只靠模型输出 `<promise>…</promise>` 哨兵字符串加最大迭代数。Gemini CLI、Roo Code、Aider、pi-mono 核心和 Crush 没有 `/goal`。对想自己实现 `/goal` 的人，最值得借鉴的是：完成判定要和干活的模型分开，空转检测要用程序判断（不能只靠 prompt 劝），用户中断与外部修改要能在状态写入层面抢占续跑。

## 五种续跑模式决定了下一轮从哪里来

各项目可以按「续跑触发点」分成五类，触发点又基本决定了状态存在哪里、能加哪些保护。

**Stop hook 回灌**是 Claude Code 生态的做法。模型结束一轮时 Stop hook 触发，hook 返回 `{"decision":"block","reason":<prompt>}`，Claude Code 就把 `reason` 当作下一条输入继续跑。官方 ralph-loop 插件只有一个 191 行的 bash 脚本 `hooks/stop-hook.sh`，状态是一个 markdown 文件 `.claude/ralph-loop.local.md`（[stop-hook.sh](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/ralph-loop/hooks/stop-hook.sh)）。oh-my-claudecode 在同一机制上搭了一个约 2.7k 行的多模式调度器（[persistent-mode/index.ts](https://github.com/Yeachan-Heo/oh-my-claudecode/blob/main/src/hooks/persistent-mode/index.ts)）。Claude Code 内置 `/goal` 的文档也说它是「a wrapper around a session-scoped prompt-based Stop hook」（[docs](https://code.claude.com/docs/en/goal)）。

**空闲事件续跑**是 OpenCode 家族的做法。opencode 核心在 session runner 即将进入空闲时触发 `experimental.idle` hook，插件在此「admit input」，同一个 execution 就继续跑下去。源码注释原话是「Stop-hook style loop」（[goal.ts](https://github.com/anomalyco/opencode/blob/79bd2c6/packages/core/src/plugin/goal.ts)）。oh-my-openagent 则监听已经发生的 `session.idle` 事件，再异步投递一条新 prompt（[goal/index.ts](https://github.com/code-yeongyu/oh-my-openagent/blob/544c0a4/packages/omo-opencode/src/hooks/goal/index.ts)）。Codex CLI 也属于这一类：线程空闲时由 `on_thread_idle → continue_if_idle()` 开启一个 `turn_trigger="goal"` 的新 turn（[runtime.rs#L434](https://github.com/openai/codex/blob/4389a52/codex-rs/ext/goal/src/runtime.rs#L434)）。

**内核状态机加模型工具**是 Codex CLI、Qwen Code、DeepCode 的做法。目标是一等实体，存在 SQLite 或 transcript 日志里，有明确的状态枚举；模型通过 `get_goal` / `update_goal` 读写，调度器在空闲时铸造一个新 turn。Kilo Code 也在内核里，但用的是专门 fork 出的 fiber 循环，每轮之间 sleep 5 秒（[runner.ts](https://github.com/Kilo-Org/kilocode/blob/46e4dd4/packages/opencode/src/kilocode/session/goal/runner.ts)）。Goose 把 `/goal`、`/grind` 实现为 agent 状态机里的 `RetryOperation`，在每个 turn 边界检查（[ops_retry.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/state_machine/ops_retry.rs)）。

**外部 bash 循环**完全不依赖 agent 内部的钩子。snarktank/ralph 用 `for i in $(seq 1 $MAX_ITERATIONS)` 每轮启动一个全新的 `claude --print` 或 `amp` 进程，用 `grep -q "<promise>COMPLETE</promise>"` 判断结束（[ralph.sh](https://github.com/snarktank/ralph/blob/main/ralph.sh)）。Goose 的 Ralph recipe 和 frankbria/ralph-claude-code 是它的变体。

**裁判模型**与触发方式是正交的，它回答的是「谁判定完成」。Hermes、OpenHands、opencode、Claude Code 内置 `/goal` 和 Qwen 都用一个单独的 LLM 调用输出结构化判决；Codex、oh-my-openagent、Kilo、DeepCode、Cline 插件则让干活的模型自己调用完成工具。

没有 `/goal` 的项目各有替代：**Gemini CLI** 只有 `next_speaker` 检查（默认关闭，`skipNextSpeakerCheck ?? true`）、带 LLM 二次确认的循环检测、`AfterAgent` hook 的 block→continue 路径和 `MAX_TURNS = 100`（[client.ts](https://github.com/google-gemini/gemini-cli/blob/4f343cabc7b59ab990d7411e920094108524abff/packages/core/src/core/client.ts)）；**Roo Code** 已于 2026-05-15 停止维护，只有 `attempt_completion` 和「没用工具就重提示」的 `noToolsUsed`（[Task.ts](https://github.com/RooCodeInc/Roo-Code/blob/b867ec9145750d0ae1ff7f02d35406e9bf2a0b16/src/core/task/Task.ts)）；**Aider** 的命令表里没有任何 goal 相关命令（[commands.py](https://github.com/Aider-AI/aider/blob/main/aider/commands.py)）；**pi-mono** 核心没有，由第三方扩展 Pi-Agent-Goal 补上（[runtime.ts](https://github.com/KristjanPikhof/Pi-Agent-Goal/blob/main/src/runtime.ts)）；**Crush** 的 `TurnEnd` hook 结果被丢弃，无法续跑，只有一个未合并的社区 PR #2948（[turn_hooks.go](https://github.com/charmbracelet/crush/blob/d05fb53/internal/agent/turn_hooks.go)、[PR #2948](https://github.com/charmbracelet/crush/pull/2948)）。

## Codex 与 Qwen 把目标做成了内核里的状态机

### Codex CLI：六个状态、三个工具、三条硬阈值

Codex 的实现是独立 crate `codex-rs/ext/goal/`（11 个 `.rs` 文件共 3445 行，外加三个模板），feature `goals` 目前 **Stable 且默认开启**（[features/src/lib.rs#L2285](https://github.com/openai/codex/blob/4389a52/codex-rs/features/src/lib.rs#L2285)）。TUI 里的 `/goal <objective>` 不会作为 prompt 发给模型，而是经 app-server 的 `thread/goal/set` 写入独立的 SQLite 库 `goals_1.sqlite`。`thread_goals` 表的 `status` 列被 CHECK 约束为 **`active, paused, blocked, usage_limited, budget_limited, complete`** 六个值（[0001_thread_goals.sql](https://github.com/openai/codex/blob/4389a52/codex-rs/state/goals_migrations/0001_thread_goals.sql)）。初版（2026-04-25 合入的 PR #18073–#18077）只有四个状态，`blocked` 和 `usage_limited` 是后加的（[PR #18076](https://github.com/openai/codex/pull/18076)）。

模型只有三个工具。`update_goal` 的 `status` 枚举只有 `complete / blocked / paused`，handler 拒绝其他值，并返回「resume, budget-limited, and usage-limited status changes are controlled by the user or system」。也就是说，**模型不能自己恢复目标，也不能把自己标成预算耗尽**（[spec.rs](https://github.com/openai/codex/blob/4389a52/codex-rs/ext/goal/src/spec.rs)、[tool.rs](https://github.com/openai/codex/blob/4389a52/codex-rs/ext/goal/src/tool.rs)）。工具描述里把「blocked」的门槛写死在文本中：

> Set status to `blocked` only when the same blocking condition has repeated for at least three consecutive goal turns, counting the original/user-triggered turn and any automatic continuations, and the agent cannot make meaningful progress without user input or an external-state change.

每次自动续跑时注入的 `continuation.md` 是目前所有项目里最长、最细的续跑 prompt。它要求模型先把上一轮归类为「progress / verified wait / no progress」，并给出一套逐条举证的「Completion audit」（[continuation.md](https://github.com/openai/codex/blob/4389a52/codex-rs/ext/goal/templates/goals/continuation.md)）。核心段落原文：

```text
Completion audit:
Before deciding that the goal is achieved, treat completion as unproven and verify it against the actual current state:
- Derive concrete requirements from the objective and any referenced files, plans, specifications, issues, or user instructions.
- Preserve the original scope; do not redefine success around the work that already exists.
...
- Treat uncertain or indirect evidence as not achieved; gather stronger evidence or continue the work.
- The audit must prove completion, not merely fail to find obvious remaining work.
```

objective 本身被当作不可信数据：续跑时以 key `goal_objective` 作为 `AdditionalContextKind::Untrusted` 附加上下文传入，默认渲染成一个 tool output（feature `untrusted_as_tool_output` 默认开启），并按 1000 token 截断，所以模板里才有「If the objective is missing or truncated, retrieve the full objective with get_goal」（[steering.rs](https://github.com/openai/codex/blob/4389a52/codex-rs/ext/goal/src/steering.rs)、[turn_input.rs#L814](https://github.com/openai/codex/blob/4389a52/codex-rs/core/src/session/turn_input.rs#L814)）。另外两个模板只在特定事件时注入一次：首次触达 token 预算时向**当前 turn** 注入 `budget_limit.md`（「do not start new substantive work… Wrap up this turn soon」），用户编辑 objective 时注入 `objective_updated.md`。

防死循环在 `accounting.rs` 里用程序实现，有三条硬阈值：**连续 3 个「空」的自动续跑**（无活动且空回复或被中断）、**连续 3 次执行失败**、**任何不可重试的 turn 错误**，三者都把目标置为 `Blocked`。源码注释说明了第三条的来由：「Block the goal to prevent automatic continuation from looping and consuming tokens, as can happen with compaction errors」（[accounting.rs#L229](https://github.com/openai/codex/blob/4389a52/codex-rs/ext/goal/src/accounting.rs#L229)、[extension.rs#L423](https://github.com/openai/codex/blob/4389a52/codex-rs/ext/goal/src/extension.rs#L423)）。token 预算是软停止：触达后只注入一次 wrap-up 提示，turn 不中止。配置项 `max_goal_token_budget` 既是 `create_goal` 的上限，也是未指定预算时的默认值（[config_toml.rs#L802](https://github.com/openai/codex/blob/4389a52/codex-rs/config/src/config_toml.rs#L802)）。

有一个容易误解的细节：**中断暂停目标是 TUI 做的，不是内核做的**。core 扩展的 `on_turn_abort` 只把被中断的续跑记为一次可能的「空续跑」；TUI 在 Esc/Ctrl+C 时主动发送 `SetThreadGoalStatus { status: Paused }`。因此只发 `turn/interrupt` 的其他 app-server 客户端，目标仍是 Active，会继续续跑直到触发空续跑阈值（[interaction.rs#L821](https://github.com/openai/codex/blob/4389a52/codex-rs/tui/src/chatwidget/interaction.rs#L821)）。线程恢复时也只重新激活 `Active` 的目标，`paused` 不会自动恢复，这和初版「paused goals are automatically reactivated」的描述不同（[runtime.rs#L410](https://github.com/openai/codex/blob/4389a52/codex-rs/ext/goal/src/runtime.rs#L410)）。笔记中仍未核实的一点：提高预算时 `BudgetLimited` 是否自动回到 `Active`。

### Qwen Code：模型只能提议，Verifier 才能拍板

Qwen Code 的 `/goal` 是约 7k 行的子系统，位于 `packages/core/src/goals/`（[goals dir](https://github.com/QwenLM/qwen-code/tree/3d75b8ee605777fd754778def4d270e5e4ce362b/packages/core/src/goals)）。它把「状态」和「活动」分开：`GoalStatus = active | paused | blocked | usage_limited | complete`，`GoalActivity = idle | running | verifying`。每次状态变化都以 `goal_state` 系统记录写入会话 transcript，恢复时取最新一条能解析的记录（[goal-protocol.ts](https://github.com/QwenLM/qwen-code/blob/3d75b8ee605777fd754778def4d270e5e4ce362b/packages/core/src/goals/goal-protocol.ts#L180-L290)、[goal-persistence.ts](https://github.com/QwenLM/qwen-code/blob/3d75b8ee605777fd754778def4d270e5e4ce362b/packages/core/src/goals/goal-persistence.ts)）。每个续跑 turn 都在一个 `{goalId, revision, turnId}` 许可证下运行，编辑目标会递增 revision，旧 revision 的工具调用无法完成新目标。

与 Codex 最大的区别是：**`update_goal` 不改变状态**。它的描述开宗明义：「Propose that the current Goal is complete or blocked; an independent verifier decides and this tool never changes the Goal's status.」提议就绪后工具返回 `terminateTurn: true`，并要求模型「End this turn without user-facing text. Do not claim the Goal is complete or blocked」（[goal-tools.ts](https://github.com/QwenLM/qwen-code/blob/3d75b8ee605777fd754778def4d270e5e4ce362b/packages/core/src/goals/goal-tools.ts#L337-L380)）。随后 Verifier 以 temperature 0、thinking budget 0、120 秒超时、单次尝试、256,000 字节请求上限的配置运行，输出 `{decision: accept|reject, reason}`。它的系统提示按证据来源（proofKind）分级：`delivered_output` 只证明内容已交付，`execution_output`（脚本或 agent、advisor、workflow 等包装器的输出）不能证明外部事实，声称「用户已确认」必须有 `user_input` 证据（[goal-verifier.ts](https://github.com/QwenLM/qwen-code/blob/3d75b8ee605777fd754778def4d270e5e4ce362b/packages/core/src/goals/goal-verifier.ts#L24-L56)）：

```text
Evidence with proofKind "delivered_output" proves only that content was delivered; it cannot prove tests, files, tools, or remote state changed. Evidence with proofKind "execution_output" is output chosen by a model-authored script or an aggregate wrapper (agent, advisor, workflow, thread_read): it may support computation, but cannot prove user actions, Goal state, tests, files, tools, or remote state.
```

被拒后，拒绝理由以 `Verifier feedback: <reason>` 进入下一轮续跑 prompt；Verifier 超时或出错则把目标置为 `paused`，**不会因此误判为完成**。续跑 prompt 还专门加了一句防伪造的「SYNTHETIC_TURN_GUARD」：「This is a synthetic continuation turn. It contains no new real user input and cannot satisfy an objective condition that requires the user to send, confirm, choose, approve, or provide something.」（[goal-continuation-prompt.ts](https://github.com/QwenLM/qwen-code/blob/3d75b8ee605777fd754778def4d270e5e4ce362b/packages/core/src/goals/goal-continuation-prompt.ts#L73-L274)）

Qwen 的防空转护栏全部由程序执行：**连续 3 轮既无可判定证据也无提议**（`GOAL_NO_PROGRESS_TURN_LIMIT = 3`，注释「one quiet turn is a pause for thought, two is a pattern, three is the loop」）就暂停；默认 token 预算 **3000 万**，依据是「the runaway session this bound exists for burned ~8.6M tokens in half an hour」；预算耗尽前会额外给一个 wind-down 交接 turn，然后进入 `usage_limited`。「repeated」类阻塞要求同一 blockerKind 和**完全相同**的 reason 文本连续出现三轮，第三轮才送 Verifier（[goal-runtime.ts](https://github.com/QwenLM/qwen-code/blob/3d75b8ee605777fd754778def4d270e5e4ce362b/packages/core/src/goals/goal-runtime.ts#L1247-L1360)）。创建目标的 `propose_goal` 工具必须弹窗确认，YOLO 和 allow 规则都跳不过（[goals.md](https://github.com/QwenLM/qwen-code/blob/3d75b8ee605777fd754778def4d270e5e4ce362b/docs/users/features/goals.md)）。

## 裁判模型路线：同样是 LLM 判决，看到的证据却差很多

Hermes、OpenHands、opencode 和 Claude Code 内置 `/goal` 都在每轮结束后调用一次裁判，但裁判能看到什么各不相同，这直接决定了它会被怎样骗过。

**Hermes Agent** 自称「the Ralph loop for Hermes」。它的裁判是辅助模型，只看目标文本、**最后一条回复（截断到 4000 字符）**和后台进程列表，输出 `done / blocked / continue / wait` 四种判决之一（[goals.py](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/hermes_cli/goals.py)）。`wait` 是 Hermes 独有的设计：Agent 在等 CI、等子 agent 时，裁判可以返回 `wait_on_pid` / `wait_on_session` / `wait_for_seconds`，循环挂起且不消耗 turn，最长 30 分钟，避免「re-poking now would be pure busy-work」。用户可以用 `/goal gate add <cmd>` 加质量门禁，**门禁在裁判之前运行**，失败的命令输出直接成为下一轮续跑 prompt。裁判调用失败时 fail-open 为 `continue`，靠 20 轮默认预算、连续 3 次解析失败、连续 5 次传输失败这几条计数兜底。续跑消息通过网关的适配器 FIFO 入队，所以真实用户消息自然优先（[run_goals.py](https://github.com/NousResearch/hermes-agent/blob/09ded63c4fedcc8fe975d37f9765797d904fc75d/gateway/run_goals.py)）。

**OpenHands** 把循环放在 agent server 上：Web UI 拦截 `/goal [--max N] <objective>`，调用 `POST /api/conversations/{id}/goal`。`run_goal()` 在 `conversation.run()` 外面套一层 while 循环，裁判看的是**除系统提示外的完整 transcript**，输出 `{score, complete, missing}`；未完成时把 `missing` 填进 `FOLLOWUP_PROMPT` 再跑（[runner.py](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/goal/runner.py)、[prompts.py](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/goal/prompts.py)）。状态只有 `running / complete / capped / interrupted`，只有 `interrupted` 能 resume，默认上限 10 轮。一个反直觉的选择是**故意忽略 STUCK 检测器**，注释说「a STUCK run is not a reason to halt the whole goal -- proceed to the judge」；任何普通用户消息都会直接取消 goal 循环（[event_service.py](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-agent-server/openhands/agent_server/event_service.py)）。

**opencode** 的裁判 `judge.txt` 用 `ctx.session.generate` 在**同一会话历史、同一模型**上运行，开头是「Stop acting as the assistant. You are now the reviewer」，规则包括「The assistant claiming the goal is met is not evidence」和「When in doubt, answer "continue"」（[judge.txt](https://github.com/anomalyco/opencode/blob/79bd2c6/packages/core/src/plugin/goal/judge.txt)）。护栏全部内置：`MAX_ROUNDS = 50`、`MAX_STALLS = 3`（判决理由与上轮相同或本轮没用工具即算一次 stall）、`WAIT_LIMIT = 30 分钟`、服务进程重启（boot id 变化）即暂停，写入前重读做 compare-and-set，保证用户的 `/goal pause` 总能赢过循环（[goal.ts](https://github.com/anomalyco/opencode/blob/79bd2c6/packages/core/src/plugin/goal.ts)）。「When in doubt, continue」也带来了反面问题：未合并的 PR #61526 报告 GPT-5.4-nano 作裁判时，规格第一次评审就已全部通过，目标却「still ran 33 more rounds」（[PR #61526](https://github.com/anomalyco/opencode/pull/61526)）。

**Claude Code 内置 `/goal`** 闭源，只能依据文档：每轮结束时把条件和对话发给配置的小快模型，判决有「Not yet met / Met / Impossible」三种，「not yet met」的理由作为下一轮的指引；评估器「does not call tools」，因此文档建议把条件写成「Claude's own output can demonstrate」的形式（[docs](https://code.claude.com/docs/en/goal)）。文档还描述了若干护栏：瞬时错误最多自动重试 3 次后暂停，连续多轮不用工具则停止循环，后台任务运行时跳过评估。具体的评估 prompt 和注入文本**没有公开**。截至 2026-11-18 仍有多个未关闭 issue 指向 Stop hook 型实现的典型缺陷，例如 #100634「/goal clear typed while a goal is running is queued forever」和 #106516「/goal loop consumes quota without progress or stop condition」（[issues](https://github.com/anthropics/claude-code/issues?q=%22%2Fgoal%22)）。

## 自报完成与 Stop hook 派：简单、可移植，但只能靠 prompt 防作弊

**官方 ralph-loop** 是 Stop hook 模式的参考实现。`/ralph-loop PROMPT [--max-iterations N] [--completion-promise TEXT]` 运行一个 setup 脚本，写入带 YAML frontmatter 的状态文件；Stop hook 依次检查状态文件是否存在、session 是否一致、是否达到 `max_iterations`、最后一个文本块里第一个 `<promise>` 标签是否与设定字符串**完全相等**，都不满足就 `iteration++` 并把**原始 prompt 原样**作为 `reason` 回灌（[stop-hook.sh](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/ralph-loop/hooks/stop-hook.sh)）。默认 `MAX_ITERATIONS=0`（无限），没有 promise 时 setup 脚本会警告「This loop cannot be stopped manually!」。防作弊完全靠 prompt：「Do NOT output false statements to exit the loop… Trust the process.」（[setup-ralph-loop.sh](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/ralph-loop/scripts/setup-ralph-loop.sh)）。命令把 `$ARGUMENTS` 不加引号地传进 bash 块、heredoc 也未加引号，已有 issue #80495、#52418 指出 prompt 可被当作 shell 代码执行。

**oh-my-claudecode** 在同一机制上叠了四层：PRD（`prd.json` 中每个 story 必须 `passes: true` 并绑定 criteria revision）、确定性的 `omc ralph verify`（对比基线，exit 0 才算过）、由 reviewer 子 agent 产出且带 `request-id` 的 `<ralph-approved …>VERIFIED_COMPLETE</ralph-approved>` 标签（Stop hook 在 transcript 里核对这个 id）、以及迭代上限。迭代上限分软硬两档：软上限达到后自动 `+10`，硬上限默认 500（[verifier.ts](https://github.com/Yeachan-Heo/oh-my-claudecode/blob/main/src/hooks/ralph/verifier.ts)、[security-config.ts](https://github.com/Yeachan-Heo/oh-my-claudecode/blob/main/src/lib/security-config.ts)）。它还显式放行几类 stop，其中 context-limit 一类在代码里写作「CRITICAL: Never block context-limit/critical-context stops. Blocking these causes a deadlock」（issue #213），另外放行限流、用户中止和显式 `/cancel`。OMC 的 Claude Code goal 适配设计文档甚至规定「`evaluator_passed` is not `complete`」，即不信任内置 `/goal` 的评估器结论（[CLAUDE_CODE_GOAL_ADAPTER.md](https://github.com/Yeachan-Heo/oh-my-claudecode/blob/main/docs/design/CLAUDE_CODE_GOAL_ADAPTER.md)）。

**oh-my-openagent**（原 oh-my-opencode）用 `/goal` 取代了旧的 ralph-loop（PR #6184）。它的续跑 prompt 几乎逐字沿用 Codex 的「completion audit」措辞（源码未注明出处，这是基于文本相似的推断），但 `update_goal(complete)` 没有任何程序校验；配置里的 `default_max_iterations`（默认 100）在 goal hook 代码中**找不到执行点**，只在已废弃的 ralph-loop 里用到（基于代码搜索的推断，可能在共享的 prompt-async gate 中另有限制）（[prompt.ts](https://github.com/code-yeongyu/oh-my-openagent/blob/544c0a4/packages/omo-opencode/src/hooks/goal/prompt.ts)、[tools.ts](https://github.com/code-yeongyu/oh-my-openagent/blob/544c0a4/packages/omo-opencode/src/hooks/goal/tools.ts)）。真正厚重的护栏在它的 todo-continuation-enforcer 里：停滞 3 次、失败 5 次加指数冷却、2 秒倒计时 toast、用户插话即 `user-interruption` 停止重新装填，一共二十多个跳过条件（[idle-event.ts](https://github.com/code-yeongyu/oh-my-openagent/blob/544c0a4/packages/omo-opencode/src/hooks/todo-continuation-enforcer/idle-event.ts)）。

**Kilo Code** 坦率承认不做独立验证：启动提示是「completion is not independently verified」，完成状态在 UI 上标为「Complete (model-reported)」。它用程序判定进度来弥补：`todowrite`、`question`、`goal_report` 不算进度，**任何一轮没有成功的动作也没有报告就立即暂停**；它也没有轮数上限。Kilo 还有一等的 `waiting` 状态，`schedule_wakeup`、cron、后台进程会挂起目标而不是轮询（[instructions.ts](https://github.com/Kilo-Org/kilocode/blob/46e4dd4/packages/opencode/src/kilocode/session/goal/instructions.ts)、[goals.md](https://github.com/Kilo-Org/kilocode/blob/46e4dd4/packages/kilo-docs/pages/code-with-ai/agents/goals.md)）。

**Goose** 的两个命令语义截然不同。`/goal` 在 turn 结束前**只注入一次**隐藏提示「Before finishing, check whether the following goal has been fully met」，之后清空目标；`/grind` 则在**每次** turn 结束都注入「Keep working. The grind goal is not yet complete」，唯一的边界是 `GOOSE_MAX_TURNS`（默认 1000）（[ops_retry.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/state_machine/ops_retry.rs)、[max_turns.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose-agent/src/max_turns.rs)）。**Cline 的官方 goal 插件**更极简：run 完成后排队一条「Are you sure you've completed the goal: ${goal}」，并且只有在这条确认提示出现之后才允许调用 `mark_goal_complete`。插件没有计数字段，模型若一直不调用就会无限追问（这是对代码的推断）（[index.ts](https://github.com/cline/plugins/blob/main/plugins/goal/index.ts)）。

外循环这一端，**snarktank/ralph** 每轮新开进程、只靠文件（`prd.json`、`progress.txt`、git）传递记忆，最大 10 轮，agent 的退出码被 `|| true` 吞掉（[ralph.sh](https://github.com/snarktank/ralph/blob/main/ralph.sh)）。**Goose Ralph recipe** 加入不同模型的 reviewer，由 reviewer 向文件写入 `SHIP` 或 `REVISE`（[ralph-loop.sh](https://github.com/aaif-goose/goose/blob/main/documentation/src/pages/recipes/data/recipes/ralph-loop.sh)）。**frankbria/ralph-claude-code** 要求启发式完成信号至少 2 次，**并且**模型显式输出 `EXIT_SIGNAL: true`，两者同时满足才退出（[ralph_loop.sh](https://github.com/frankbria/ralph-claude-code/blob/main/ralph_loop.sh)）。

## 横向对比：状态、判定者与护栏

| 项目 | 状态集合 | 谁判定完成 | 存储 | 续跑触发 | 预算 / 护栏 |
|---|---|---|---|---|---|
| Codex CLI | active, paused, blocked, usage_limited, budget_limited, complete | 模型 `update_goal(complete)` + prompt 内审计 | `goals_1.sqlite` | 线程空闲 `continue_if_idle` | token 预算（软）；3 空续跑 / 3 执行失败 / turn 错误 → blocked |
| Qwen Code | active, paused, blocked, usage_limited, complete（+ idle/running/verifying） | 独立 Verifier（temp 0，按 proofKind） | transcript `goal_state` 记录 | 调度器 `flushContinuation` | 3000 万 token、3 轮无进展暂停、可选轮数/分钟、wind-down |
| Hermes | active, paused, done, cleared（+ waiting） | 辅助模型裁判（看最后回复）+ shell 门禁 | SQLite `state_meta` | 网关 post-turn hook，FIFO 入队 | 20 轮、3 解析 / 5 传输失败、门禁 3 次、等待 30 分钟 |
| OpenHands | running, complete, capped, interrupted | 裁判（看完整 transcript） | `ConversationStateUpdateEvent` | 服务端 while 循环 | 10 轮；用户消息取消；忽略 STUCK |
| opencode | active, paused, complete | 同模型同历史的 reviewer | 插件存储 `session:<id>` | `experimental.idle` hook | 50 轮、3 stall、30 分钟、重启暂停、CAS 写 |
| oh-my-openagent | active, paused, complete | 模型 `update_goal` | `.omo/goal/<sid>.json` | `session.idle` 事件 | goal hook 内未见上限（推断） |
| Kilo Code | active, complete, blocked, paused, waiting | 模型 `goal_report`（明示不验证） | 会话元数据 `kilo.goal` | fiber 循环 + 5 秒间隔 | 无轮数上限；无成功动作即暂停 |
| Claude Code ralph-loop | 状态文件存在与否 + iteration | `<promise>` 精确匹配 | `.claude/ralph-loop.local.md` | Stop hook `decision: block` | `max_iterations`（默认无限） |
| Claude Code `/goal`（闭源） | 文档：active, achieved, failed, paused, cleared | 小快模型评估器 | 会话级 | prompt 型 Stop hook | 文档：3 次重试、无工具多轮即停 |
| oh-my-claudecode ralph | executing, verifying, complete 等 | PRD + verify 命令 + reviewer 标签 | `.omc/state/sessions/<sid>/` | Stop hook | 软上限 +10、硬上限 500、验证 3 次 |
| Goose `/goal` / `/grind` | `GoalState{goal, grind}` | 模型自查（无裁判） | session `extension_data` | `RetryOperation` turn 边界 | `/goal` 只提示 1 次；`/grind` 仅 1000 turns |
| Cline goal 插件 | pending, active, awaitingVerification | 模型回答「Are you sure」 | `goals.json` | afterRun hook 排队消息 | 无计数（推断无限追问） |
| snarktank/ralph | 文件 + 退出码 | `<promise>COMPLETE</promise>` | `prd.json`、`progress.txt` | 外部 bash `for` | 10 轮 |

表里能读出两个规律。其一，**判定越可信，系统越复杂**：自报（Kilo、omo）只需几十行，独立 Verifier（Qwen）需要证据窗口、来源分类、许可证和 revision 机制。其二，**几乎所有成熟实现都收敛到「3」这个数字**：Codex 的 3 次空续跑和 3 轮阻塞审计、Qwen 的 3 轮无进展、opencode 的 3 次 stall、Hermes 的 3 次解析失败、DeepCode「从第三轮起才接受 blocked」。这说明各家对「一次是偶然、两次是模式、三次是循环」的经验判断一致。

## Conclusion

把这些实现放在一起看，`/goal` 的难点不在「怎么让模型继续跑」（一条 Stop hook 或一个 idle 回调就够了），而在**怎么让它在该停的时候停下**，包括真正完成时、空转时、被阻塞时、用户想接管时。目前可靠的做法分三层：一是把完成判定从干活的模型手里拿走，或至少要求可检查的证据（Qwen 的 proofKind、Hermes 的 shell 门禁、DeepCode 由运行时执行的 checklist 命令、OMC 带 request-id 的审批标签）；二是用程序计数而非 prompt 劝说来识别空转（工具是否成功调用、判决理由是否重复、状态是否推进）；三是在状态写入层面让用户操作抢占续跑（opencode 的 compare-and-set、Codex 的 goal_state_permit、Hermes 的 FIFO 入队、Qwen 的 revision 许可证）。

反过来看，裁判模型并不是银弹。它看得太少会被模型的自述骗过（Hermes 只看最后回复，Claude Code 的评估器不调用工具），裁判模型太弱又会无休止地 continue（opencode #61526 多跑了 33 轮）。而 fail-open 的裁判（Hermes、OpenHands 解析失败时）必须配合硬上限。如果要自己实现一个可靠的 `/goal`，比较稳的组合是：Codex 式内核状态机和不可信 objective 包装，Qwen 式「提议—验证」分离和证据来源分级，opencode 式 CAS 写入和 stall 计数，再加上 Hermes 式的 `wait` 状态，避免在等待 CI 时空耗 token。
