# OpenAI Codex 架构调研笔记（截至 2026-11-20）

> 方法：openai.com 工程博客（直连 403，经 web.archive.org 2026 快照获取原文）、developers.openai.com 博客（llms-full.txt 全文）、Codex 文档（learn.chatgpt.com/docs，llms.txt）为一手来源；本地源码 `~/.cache/goal-research-src/codex`（HEAD `4389a526d51a5a7600e0648436a7142a304f0188`，2026-11-18 11:01 UTC）用于核对，不运行其中代码。第三方分析单独标注。

## 1. Agent loop 与上下文构造（openai.com/index/unrolling-the-codex-agent-loop，2026-01-23，Michael Bolin）

### Takeaway
"Codex harness" 指所有 Codex 产品（CLI、Cloud、VS Code、App）共用的 agent loop 与执行逻辑。它直接调用 Responses API，**每次请求都发送完整 input（不用 `previous_response_id`）**，以保持无状态、支持 ZDR；靠"旧 prompt 是新 prompt 的精确前缀"拿到 prompt cache，把二次方的成本降为线性。配置变化（沙箱、审批、cwd）一律以**追加新消息**的方式表达，而不是改写前面的消息。上下文满时调用 `/responses/compact` 端点，得到带 `encrypted_content` 的 `type=compaction` item 代替原 input。

### Cited Findings
- 端点：ChatGPT 登录走 `https://chatgpt.com/backend-api/codex/responses`；API key 走 `https://api.openai.com/v1/responses`；`--oss` 默认 `http://localhost:11434/v1/responses`（ollama 0.13.4+ / LM Studio 0.3.39+）；也可用 Azure 等托管的 Responses API。
- 请求三个关键字段：`instructions`（`~/.codex/config.toml` 的 `model_instructions_file`，否则模型自带 `base_instructions`，例如 `gpt-5.2-codex_prompt.md` 打包在 CLI 里）、`tools`（Codex 自带工具 + Responses API 内置工具如 `web_search` + MCP 工具，命名 `mcp__weather__get-forecast`）、`input`。
- 当时的默认工具：`shell`（command 数组、workdir、timeout_ms）、`update_plan`（plan、explanation）、`web_search`（`external_web_access: false`）。
- input 前置项顺序：① `role=developer` 的 `<permissions instructions>`（只描述 Codex 自带 shell 工具的沙箱；**MCP 等其他工具不受 Codex 沙箱约束**），由 `workspace_write.md`、`on_request.md` 等片段拼成；② 可选 `role=developer` 的 `developer_instructions`；③ 可选 `role=user` 的用户指令：`$CODEX_HOME` 下 `AGENTS.override.md`/`AGENTS.md`，再从 git/项目根到 cwd 每一级的 `AGENTS.override.md`、`AGENTS.md` 或 `project_doc_fallback_filenames`（默认上限 32 KiB），以及 skills 前言 + 每个 skill 的元数据 + 使用说明；④ `role=user` 的 `<environment_context>`（cwd、shell）。然后才是用户消息。
- prompt 前三项（system、tools、instructions）的顺序由服务端决定，system 内容也由服务端控制。
- SSE 事件：`response.reasoning_summary_text.delta`、`response.output_item.added`、`response.output_text.delta`、`response.completed` 等；`output_item.done` 的 reasoning（含 `encrypted_content`）和 function_call 要原样追加回下一次请求的 input，再加 `function_call_output`。
- 不用 `previous_response_id` 的原因：请求完全无状态；兼容 ZDR（ZDR 客户仍可受益于先前 turn 的推理，因为 `encrypted_content` 可在服务端解密，OpenAI 保存 ZDR 客户的解密密钥但不保存数据）。PR #642、#1641。
- 导致 cache miss 的操作：中途改 tools、换 model、改沙箱配置/审批模式/cwd。早期 MCP 工具枚举顺序不稳定导致 cache miss（已修）；MCP 的 `notifications/tools/list_changed` 中途生效代价高。
- 中途配置变化的处理：沙箱或审批变化→插入新的 `role=developer` `<permissions instructions>`；cwd 变化→插入新的 `role=user` `<environment_context>`。
- 压缩：早期 `/compact` 手动触发，用对话 + 摘要指令请求一次，取 assistant 摘要作为新 input；现在超过 `auto_compact_limit` 自动调用 `/responses/compact`，返回的 item 列表含 `type=compaction` + 不透明 `encrypted_content`，"preserves the model's latent understanding of the original conversation"。

## 2. App Server（openai.com/index/unlocking-the-codex-harness，2026-02-04，Celia Chen）

### Takeaway
所有 Codex 界面（Web、CLI/TUI、VS Code、macOS App、JetBrains、Xcode）共享一个 harness，通过 App Server 暴露：一个长驻进程 + 双向 "JSON-RPC lite"（无 `"jsonrpc":"2.0"` 头，stdio 上的 JSONL）。三层原语：Item（有 started/delta/completed 生命周期）⊂ Turn（一次用户输入引发的工作）⊂ Thread（可创建、恢复、fork、归档的持久会话）。服务端可以反向发请求（如审批），turn 挂起直到客户端回复。

### Cited Findings
- 起源：VS Code 扩展需要复用 TUI 的 agent loop；先试把 Codex 暴露为 MCP server，但 MCP 语义难以承载 IDE 需求（流式进度、diff），于是做了镜像 TUI 循环的 JSON-RPC，后演化为标准协议。
- harness 内容：agent loop；thread 生命周期与持久化；配置与认证（Sign in with ChatGPT）；沙箱中的 shell/文件工具执行；MCP、skills 等扩展在统一策略下接入。这些都在 "Codex core"（既是库，也是运行一个 thread 的 runtime）。
- App Server 进程四部分：stdio reader、Codex message processor、thread manager（每个 thread 起一个 core session）、core threads；message processor 把 core 的低层事件转换成少量稳定的、UI-ready 的通知。
- 握手：客户端先发唯一的 `initialize`（clientInfo），服务端返回 userAgent，协商版本、feature flag、默认值。
- 流程：`thread/start` → `turn/start` → 通知 `thread/started`、`turn/started`、`item/started`、`item/*/delta`、`item/completed`、`turn/completed`；审批为服务端请求 `item/commandExecution/requestApproval`。
- `codex debug app-server send-message-v2 "..."` 可看完整 turn 的 JSON；`codex app-server generate-ts` / `generate-json-schema` 生成绑定；已有 Go/Python/TS/Swift/Kotlin 客户端。
- 集成形态：本地 App/IDE 打包或下载平台二进制作为子进程，版本钉死；Xcode 等可让旧客户端连接新服务端（协议向后兼容）。Codex Web：worker 准备容器、在里面启动 App Server，stdio 隧道过持久网络连接；浏览器经 HTTP+SSE 与后端通信；状态在服务端，标签页关闭任务继续。TUI 历史上直接调用 Rust core 类型，计划重构为 App Server 客户端，从而能连远端 Codex server。
- 其他集成方式：`codex mcp-server`（只暴露 MCP 能表达的部分）、跨厂商 harness 协议（趋于公共子集）、`codex exec`（CI/脚本）、Codex SDK（TS，早于 App Server，表面较小）。

## 3. 沙箱（openai.com/index/inside-the-codex-sandbox，2026-11-18，Michael Bolin）

### Takeaway
Codex 不把计算搬进 VM/容器，而是用各 OS 原生机制约束"在用户现有环境里运行的命令"（理由：iOS 工具链、模拟器等只能在宿主跑）。统一 API `spawn_command_under_sandbox(program_and_args, cwd, env, permission_profile, network_proxy)`，子进程继承约束。配置单位是 **permission profile**（内置 `:workspace`、`:read-only`、`:danger-full-access`，可 `extends`）。网络限制由共享的 `codex-network-proxy`（HTTP + SOCKS5）执行；macOS 用 Seatbelt（`sandbox-exec` + SBPL，`(deny default)` 起步），Linux 用 bubblewrap + seccomp-bpf（`codex-linux-sandbox` 两段式启动），Windows 正迁移到微软的 MXC。

### Cited Findings
- `:workspace` 默认：工作区可写，但 `.git`、`.codex`（以及 `.agents`、`.aws`）只读（防改 config 与 git hooks）；读取范围广（受 OS 权限）；网络关闭。残余风险示例：往被 git 忽略的 `node_modules/prettier` 注入代码，编辑器保存时在沙箱外执行。
- `exec_command` 工具参数（示意）：cmd、workdir、shell、login（默认 `-lc`）、timeout_ms（示意默认 10_000）、`sandbox_permissions`（`use_default` / `with_additional_permissions` / `require_escalated`）、`justification`（只对 escalated，作为面向用户的审批问题）、`additional_permissions`。是否批准由 harness 按用户偏好决定：直接提示用户或交给 **auto-review**。
- harness 也复用沙箱基础设施做本地文件读写；`codex sandbox --permission-profile <p> -- cmd` 可在 agent 会话之外沙箱化任意命令，例如包裹 stdio MCP server（Codex 默认不沙箱化 MCP server 启动命令）。
- profile 配置：`[permissions.<name>]`、`extends = ":workspace"`、`[permissions.<name>.filesystem]` 路径 → `read`/`write`/`deny`，`":workspace_roots"` 子表对每个工作区根应用；`[permissions.<name>.network] enabled`、`[...network.domains]` allow/deny（deny 优先）；`[features] network_proxy = true`。
- 配置分层：多个 config.toml 递归合并，表合并、数组整体替换（所以 profile 用表表达路径和域名）。企业用 `requirements.toml`（系统文件 `/etc/codex/requirements.toml` + 云端策略，云端优先）约束，例如 `allowed_permission_profiles`。
- 代理：未匹配的域名可挂起请求交给 harness 审批（用户或 auto-review）；**先判定允许再做 DNS 解析**，避免主机名经 DNS 泄露；注入 `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` 及 `PIP_PROXY` 等应用专用变量；不遵守代理变量的程序被 OS 沙箱直接挡住。
- macOS：SBPL 用 `(param)` 占位符 + `-D` 传值，例如阻止删除可写根目录本身；为代理端口注入 `(allow network-outbound (remote ip "localhost:3128"))` 等；`sandbox-exec` 已标为 deprecated 但 Chrome 也仍在用。
- Linux：优先用 PATH 上的 bwrap，否则用 vendored 副本；`--bind`/`--ro-bind`/`--perms 000 --tmpfs`/`--ro-bind-data` 构造视图；deny glob 在启动前展开为具体路径（运行中新出现的匹配不受限）；`--unshare-ipc`、`--unshare-pid`、需要时 `--unshare-net`；`no_new_privs` 后用 seccompiler 编译的 BPF 限制网络 syscall（禁网时挡 connect/bind；走代理时允许 IP socket 但默认禁止独立 Unix socket）。代理连接：`codex-linux-sandbox` fork 出宿主侧 relay，用 Unix socketpair 传递沙箱 netns 内 loopback 监听器的 fd，relay 在宿主 netns 连代理。
- Windows：原方案需要管理员权限、ACL 操作慢、在宿主留下持久状态（ACL、防火墙规则、沙箱用户），正迁移到 MXC（Microsoft eXecution Container）：JSON 配置；默认拒绝，Codex 加盘符根只读授权；更具体路径覆盖祖先；别名取更严权限；egress 默认 deny 只放 loopback（代理）。

EOF
## 4. Harness engineering（openai.com/index/harness-engineering，2026-02-11，Ryan Lopopolo）

### Takeaway
不是 harness 内部实现，而是"围绕 Codex 的工程环境"：5 个月、3→7 名工程师、约 1,500 个 PR、约百万行代码、零手写代码。核心经验：AGENTS.md 只做约 100 行的"目录"，知识放在结构化 `docs/`（设计文档、执行计划、质量评分），由 lint/CI 和 doc-gardening agent 保持新鲜；把 UI（Chrome DevTools Protocol）、日志/指标/trace（每个 worktree 一套临时可观测栈，LogQL/PromQL）做成对 agent 可读；用自定义 linter 强制分层依赖（Types → Config → Repo → Service → Runtime → UI，横切关注点只经 Providers），lint 报错信息里直接写修复指令；单次运行常超过 6 小时。

## 5. Symphony（openai.com/index/open-source-codex-orchestration-symphony，2026-04-27）

### Takeaway
"交互式 agent 的天花板是人的注意力（一人同时管 3–5 个会话）"。Symphony 是一份开源规格：把 Linear 看板当控制平面和状态机，每个未完成 issue 对应一个 agent 工作区，挂了自动重启；任务可组成 DAG，只有未阻塞的才开工；agent 可以自己开新 issue；负责盯 CI、rebase、解冲突、重试 flaky。部分团队前三周落地 PR 数 +500%。

## 6. 推理保留 + 压缩（openai.com/index/how-two-settings-tripled-our-arc-agi-3-scores，2026-07-29）

### Cited Findings
- ARC-AGI-3 官方 harness 每步丢弃私有推理、用 175,000 字符滚动截断；换成 Responses API 保留推理 + compaction 后，GPT-5.6 Sol 从 13.3% → 38.3%，输出 token 减少 6 倍。
- "This is how our models are trained, and also how they are deployed in ChatGPT and Codex"：模型训练时就带着保留的私有推理，过长时摘要后继续。对 Codex 架构的意义：harness 必须原样回传 reasoning item（含 encrypted_content），压缩用服务端 compaction 而不是截断。

## 7. 开发者博客（developers.openai.com/blog）

### Cited Findings
- Codex as a platform：开源的是 harness 与集成面（CLI、app-server、SDK），模型与托管服务另算。三种集成：`codex exec`（脚本/CI、结构化输出）、Codex SDK（程序化启动/恢复/流式）、app-server（产品内长会话、事件流、审批）。示例 Relay：应用拥有界面、业务上下文、MCP 工具与审批，Codex 提供循环与沙箱执行。
- Shell + Skills + Compaction（API 侧原语）：skills 对齐 Agent Skills 开放标准，平台向模型暴露每个 skill 的 `name`、`description`、`path`；托管 shell 容器；服务端 compaction（超阈值在流内自动压缩）或独立 `/responses/compact`；网络 allowlist 两层（组织级 + 请求级 `network_policy` 必须是子集）；`domain_secrets` 让模型只看到占位符、sidecar 注入真值；`/mnt/data` 作为产物交接边界。Glean：加 skill 后触发率先降约 20%，补反例后恢复。
- Securing persistent agents（OpenClaw）：可信的确定性逻辑（网络、身份、沙箱）与不可信的 LLM 执行分离；用 Codex 内置沙箱（codex linux sandbox 做进程隔离），`requirements.toml`（`allowed_sandbox_modes`、`allowed_web_search_modes = ["cached"]`、managed 域名白名单/黑名单含 169.254.169.254）+ `config.toml`（`default_permissions`、filesystem `:minimal`、`.ssh` deny、关闭 SOCKS5/上游代理/非 loopback 代理、`shell_environment_policy.exclude` 去掉 GH_TOKEN 等、`[apps._default] enabled=false destructive_enabled=false`）；**Auto-review**：第二个 agent 监控并批准主 agent 的动作，Codex 与 OpenClaw 原生支持；每个 agent 一个 ChatGPT Service Account 身份。
- Run long horizon tasks：GPT-5.3-Codex Extra High 连续约 25 小时、约 13M tokens、约 3 万行；关键是"持久项目记忆"四件套 Prompt.md（规格与 done-when）、Plan.md（里程碑 + 验收命令 + stop-and-fix）、Implement.md（runbook）、Documentation.md（状态与决策日志）；原生 plan mode（`/plan`）。
- Rethinking skills and prompts for GPT-6 Astra：skill 太多时 **Codex 会缩短 skill 描述以塞进上下文**；描述要短且写清何时用；根 SKILL.md 做路由；AGENTS.md 不要要求"每次编辑前读完所有文档"；Astra 会自己跑测试，旧指令会导致多余测试；Astra 更谨慎，旧的强硬边界措辞会让它过早停下。

## 8. Code Mode（源码 + 文档；核心发现）

### Takeaway
Codex 的 Code Mode 把模型可见的工具面换成两个入口：`exec`（freeform 工具，输入是原始 JavaScript，不是 JSON）和 `wait`。脚本在**独立的 code-mode-host 进程**里、每次 `exec` 一个**全新 V8 isolate**中作为 async module 运行；所有嵌套工具挂在全局 `tools` 对象上（`await tools.exec_command(...)`、`tools.mcp__server__tool(...)`），MCP 工具以 TypeScript 声明 + `CallToolResult<T>` 类型呈现。嵌套调用经 gRPC 流回 core，由 core 的 `ToolCallRuntime` 像普通工具调用一样执行，因此审批、沙箱、hooks 全部照常生效。脚本运行超过 `yield_time_ms` 就先把已有输出交还模型并返回 cell ID，模型之后用 `wait` 续读、或 `terminate`。

### Cited Findings
- feature flags（`codex-rs/features/src/lib.rs`）：`CodeMode`（UnderDevelopment，默认关）、`CodeModeOnly`（只暴露 exec/wait，UnderDevelopment）、`CodeModeToolSearch`（UnderDevelopment）、`CodeModeHost` / `CodeModeHostGrpc` / `CodeModeHostOutputTruncation` / `CodeModeInterrupt`（均 Stable 默认开）、`CodeModePrewarm`（UnderDevelopment）；`JsRepl`、`JsReplToolsOnly` 已 Removed（Code Mode 的前身）。启用 `CodeModeOnly` 会自动启用 `CodeMode`。文档配置项：`features.code_mode.enabled`、`excluded_tool_namespaces`、`direct_only_tool_namespaces`；`mcp_servers.<id>.omit_tools_from` / `apps.<id>.omit_tools_from` 可从 `direct` / `deferred` / `code_mode` 三种模型可见面中剔除工具。
- 规模：`code-mode`（~10k 行，gRPC/remote session 客户端）、`code-mode-host`（~12k 行，独立进程，`--listen stdio | grpc+stdio | grpc://IP:PORT`）、`code-mode-protocol`（~5.8k 行，含 `codex.code_mode.v1.proto` 与工具描述生成）、`code-mode-runtime`（~12.8k 行，依赖 `v8`（启用 `v8_enable_sandbox`）与 `deno_core_icudata`），core 适配层 `core/src/tools/code_mode/`（~3.6k 行）。
- gRPC 服务 `CodeModeHost`：OpenSession（流式 SessionEvent）、CloseSession、SubscribeToToolCalls、CompleteToolCall、AcknowledgeNotification、Execute（流式 ExecuteEvent）、Wait、YieldObservation、CancelWait、Terminate、GetProcessMetrics。
- app-server 默认启动本地 Code Mode host；`codex app-server --code-mode-host https://...` 让同进程所有 thread 共享一个远程 host（HTTP/2 gRPC，建议 HTTPS 保护工具参数与结果）；CLI 0.146.0 起支持远程 host。
- `exec` 描述要点（`code-mode-protocol/src/description.rs` 原文）："Evaluates the provided JavaScript code in a fresh V8 isolate as an async module"；"Runs raw JavaScript -- no Node, no file system, no network access, no console"；可选首行 pragma `// @exec: {"yield_time_ms": 10000, "max_output_tokens": 1000}`；`max_output_tokens` 默认 10000；"When the JS code is fully evaluated, the isolate's lifetime ends and unawaited promises are silently discarded"。全局 helper：`exit()`、`text()`、`image()`、`audio()`、`generatedImage()`、`store(key, value)` / `load(key)`（跨 exec 调用持久化）、`notify()`（立即注入额外的 `custom_tool_call_output`）、`setTimeout`/`clearTimeout`、`ALL_TOOLS`、`yield_control()`（立刻把累积输出交给模型，脚本继续跑）。
- yield 默认值：描述模板占位符 `{{ default_exec_yield_time_ms }}` 由 core 配置填充，core 默认 `DEFAULT_CODE_MODE_EXEC_YIELD_TIME_MS = 30_000`（`core/src/config/mod.rs:1262`）；协议层另有常量 `DEFAULT_EXEC_YIELD_TIME_MS = 10_000`（`code-mode-protocol/src/runtime.rs:15`）。`wait` 的 yield 默认 10000 ms。
- deferred 工具：描述中可能省略，但仍在 `tools` 上并列于 `ALL_TOOLS`；开启 CodeModeToolSearch 时用 `await tools.tool_search({query, limit: 8})`（BM25）返回名字与完整 TS 声明。
- `wait` 描述：仅在 exec 返回 "Script running with cell ID ..." 后使用；`cell_id`、`yield_time_ms`（默认 10000）、`max_tokens`（默认 10000）、`terminate: true`；只返回自上次 yield 以来的新输出。
- core 中 `CodeModeDispatchBroker` 接收 host 发来的嵌套调用，`start_turn_worker` 用 `ToolCallRuntime::new(session, step_context, tracker)` 执行，调用来源标记 `ToolCallSource::CodeMode { .. }`；cell 关闭或 turn 取消时嵌套调用被丢弃；host 不可用时：Direct 模式回退到直接工具，CodeMode/CodeModeOnly "fail closed"。
- Hooks 文档："When a model uses code mode to call a tool from JavaScript, hook decisions apply to that nested call"：PreToolUse 阻止 → 工具 promise reject；`updatedInput` → 用改写后的输入执行；PostToolUse block/exit 2 → 工具已执行但 promise reject；`continue: false` → 模型可见结果用 hook 反馈，promise 不 reject。

### Inferences
- 与 Claude Code Code Mode 的差异：Claude Code 用 `REPL` 工具、脚本在 Claude Code 进程内、顶层变量跨脚本保留（REPL 语义，changelog 中有"`let x;` 保留旧值"的修复）、`$` shell 模板标签 + `edit()`/`write()` 等专用 helper；Codex 每次 exec 新 isolate、显式 `store/load` 持久化、所有工具统一在 `tools.*` 上、运行在独立进程（可远程）并用 exec/wait 的 cell 模型处理长任务。两者都让嵌套调用走原有的权限/hook 链路。

## 9. 多代理（docs agent-configuration/subagents + 源码 core/src/agent、tools/handlers/multi_agents*）

### Takeaway
Codex 的子代理是"agent thread"：每个子代理是一个独立线程，有自己的 turn 循环，主线程用一组协作工具管理它们。v2 工具面：`spawn_agent`、`send_message`（投递消息不触发新 turn）、`followup_task`（给空闲子代理派新任务并触发 turn；正在运行则在消息边界投递）、`wait_agent`（等待任一子代理的 mailbox 更新）、`list_agents`、`interrupt_agent`；v1 另有 `send_input`、`resume_agent`、`close_agent`。v1 默认并发 6 个线程（`DEFAULT_AGENT_MAX_THREADS = Some(6)`）、深度 1（`DEFAULT_AGENT_MAX_DEPTH = 1`）；v2 单独设默认每会话 4 个并发线程（`DEFAULT_MULTI_AGENT_V2_MAX_CONCURRENT_THREADS_PER_SESSION = 4`），`wait_agent` 超时默认 30_000 ms、可在 10_000 ms 到 3600*1000 ms 之间（`core/src/config/mod.rs:279-282`）。

### Cited Findings
- 内置角色（`core/src/agent/role.rs`）：`default`（"Default agent."）、`explorer`（"Explorers are fast and authoritative"，鼓励并行多个 explorer、信任其结果、复用 explorer 回答相关问题，配置文件 `explorer.toml`）、`worker`（执行与生产工作；要求主代理明确划分文件/模块所有权，并告诉 worker "not alone in the codebase"、不要回滚别人的修改）；`awaiter` 角色被注释掉（"temp removed"）。
- spawn 参数：v1 有 `message`/`items`、`agent_type`、`fork_context`、`model`、`reasoning_effort`；v2 有 `task_name`、`agent_type`、`fork_turns`、`model`、`reasoning_effort`。
- 文档：子代理继承父 turn 当前的沙箱与审批设置（含 `/permissions`、`--yolo` 的修改）；非交互流程中需要新审批的动作直接失败并把错误回给父代理；CLI 中非活动线程的审批也会弹出；`/subagents` 切换线程。
- 自定义 agent：独立 TOML 文件，必须有 `developer_instructions`，可设 `model`、`model_reasoning_effort`、禁用部分能力；继承父代理的权限、provider、MCP server（不可覆盖）；同名覆盖内置角色。`[agents]` 全局设置：`max_concurrent_threads_per_session`（旧名 `max_threads`）、`interrupt_message`。
- 触发：多数智能档位需用户明确要求（"spawn two agents"）或 AGENTS.md/skill 指示；ChatGPT Work 的 Ultra 档可主动委派。
- `Collab` 特性（旧多代理）已 Deprecated；`MultiAgentV2` Stable 但默认关；`MultiAgentMode` Removed。

## 10. Auto-review（源码代号 guardian；docs sandboxing/auto-review）

### Takeaway
Codex 的"Approve for me"把**原本要问人的审批请求**转给一个独立的 reviewer agent。它不是 Claude Code auto mode 那样的"对所有动作的前置分类器"，而是挂在审批点上：沙箱提权（`require_escalated`）、`decision = "prompt"` 的命令规则、需要审批的网络目的地、可写根之外的文件编辑、按注解或配置需要审批的 MCP/app 调用。reviewer 看到"压缩过的 transcript + 精确的审批请求"，**包括相关工具调用与工具输出**，可以做只读检查（例如删除前先看目录），但看不到隐藏推理。

### Cited Findings
- 配置：`approvals_reviewer = "auto_review"` + 可交互的审批策略（如 `approval_policy = "on-request"`）；`approval_policy = "never"` 时无可审。管理员可强制。
- 拒绝处理：把理由返回主代理并加强指令（不得以变通、间接执行或规避策略达成同一结果；只能走实质更安全的路径，否则停下问用户）。多数模型连续 3 次拒绝或本 turn 最近 50 次审查中 10 次拒绝即中断 turn；网络安全专用模型首次拒绝就中断。超时与拒绝分开报告，并告诉主代理"超时不代表不安全"。
- `/approve` 打开 "Auto-review Denials" 选择器，可对最近（每任务最多 10 条）某一次被拒动作批准**一次重试**，实现为注入一个 developer 级别的批准标记；重试仍经 reviewer，且 reviewer 可按策略再次拒绝。
- 策略模板 `prompts/templates/guardian/policy_template.md` + 默认策略 `policy.md`（可用 `[auto_review].policy` 或 managed `guardian_policy_config` 整体替换）。`policy.md` 四类风险：Data Exfiltration（"Authorization for sensitive egress must specify the payload to be sent as well as the destination"；"Authorization to create or interact with content does not authorize its egress"）、Credential Probing、Persistent Security Weakening、Destructive Actions（"User-provided tasks do not authorize all possible steps for doing that task"；`HOME` 等常见变量被遮蔽视为高风险；要求在执行时完全解析变量与展开）；另有 Low-Risk 段（沙箱重试本身不可疑、工作区外路径本身不算高风险）。
- 相关 feature：`GuardianApproval`（Stable，默认开）、`GuardianReuseParentCompaction`（Stable）、`GuardianV2`/`V3`（UnderDevelopment）。

## 11. Hooks、命令规则、记忆、压缩

### Cited Findings
- Hooks（docs hooks.md）：事件 SessionStart、SessionEnd、SubagentStart、SubagentStop、PreToolUse、PermissionRequest、PostToolUse、PreModelRequest/PostModelRequest（只做诊断，不能改上下文）、PreCompact、PostCompact、UserPromptSubmit、Stop、Interrupt；命名和 JSON 形状与 Claude Code 高度一致（matcher、`updatedInput`、exit 2 阻断）。**信任机制**：非 managed hook 必须先在 `/hooks` 中审阅并信任，信任记录在 hook 定义的哈希上，改动后需重新信任；managed hooks（系统、MDM、云、requirements.toml）自动受信且用户不能禁用。另有 MCP 工具类型 hook、后台 hook、插件自带 hook。`CodexHooks` feature Stable 默认开。
- 命令规则（docs agent-configuration/rules.md，实验）：Starlark 的 `prefix_rule(pattern=[...], decision="prompt"|..., justification=..., match=[...], not_match=[...])`，`match/not_match` 是内联单元测试；从每个活动配置层的 `rules/` 目录加载；TUI 中"加入允许列表"写到 `~/.codex/rules/default.rules`；Smart approvals 会在提权请求时**建议**一个 prefix_rule。
- 记忆（docs customization/memories.md）：本地记忆存在 `$CODEX_HOME/memories_v2/`（旧 `memories/` 兼容双写）；后台生成，跳过活跃和短会话，**等会话空闲足够久才总结**，对生成字段做密钥脱敏；额度剩余低于阈值时跳过后台生成；`/memories` 控制当前对话能否使用/贡献记忆；官方定位"recall layer"，必须遵守的规则仍放 AGENTS.md。
- 压缩：本地回退提示 `prompts/templates/compact/prompt.md`（"You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task." 要求包含进度与决策、约束与偏好、剩余步骤、关键数据）与 `summary_prefix.md`（"Another language model started to solve this problem and produced a summary of its thinking process..."）。主路径是 provider 能力 `RemoteCompactionSupport::V2` 时走远程压缩；压缩有 PreTurn / MidTurn / PostTurn / StandaloneTurn 阶段；`AsyncCompaction`（UnderDevelopment）可在后台预先算好"候选"压缩结果，未到硬阈值时可跳过。另有模型可调用的上下文工具 `get_context_remaining`（"Get the remaining tokens in the current context window."）与 `new_context`（"Start a new context window. Does not clear, reset, or otherwise affect environment state."）。

## 12. 工具面与执行（源码 core/src/tools）

### Cited Findings
- 主工具 `exec_command`（`shell_spec.rs`）："Runs a command in a PTY, returning output or a session ID for ongoing interaction."；参数 `cmd`、`workdir`（默认 turn cwd）、`tty`（是否分配 PTY）、`yield_time_ms`（默认 10000，范围 250–30000；Windows 10000–30000）、`max_output_tokens`（默认 10000）、`shell`、`login`、`environment_id`（来自 `<environment_context>`，省略则用主环境）。配套 `write_stdin`（`session_id`、`chars`，空写入即轮询，空轮询等待 5000–300000 ms）。即 Bolin 文章里早期的 `shell` 工具已被"统一 exec"（`UnifiedExec`、`UnifiedExecTty` Stable 默认开）取代：长命令返回 session，模型像操作终端一样续读/输入。
- `apply_patch`："This is a FREEFORM tool, so do not wrap the patch in JSON."（自定义 patch 语法，文件编辑不走 shell）。
- 其他：`update_plan`、`view_image`、`request_user_input`、`request_permissions`（"Request additional filesystem or network permissions from the user and wait for the client to grant a subset of the requested permission profile ... apply automatically to later shell-like commands in the current turn, or for the rest of the session"）、`tool_search`、`web_search`、`get_context_remaining`、`new_context`、`sleep`、`imagegen`、`request_plugin_install`、`list_available_plugins_to_install`、多代理工具、Code Mode 的 `exec`/`wait`。
- `tool_search`："Searches over deferred tool metadata with BM25 and exposes matching tools for the next model call."，并在描述中列出来源（如 "Google Drive: ..."）；limit 默认 8。配置中工具有三种模型可见面：`direct`（初始工具）、`deferred`（经 tool search）、`code_mode`（Code Mode 脚本）。
- `orchestrator.rs`："Coordinates tool approval, sandbox selection, and retries after sandbox denial. Guardian approval applies to the reviewed authority; escalating a sandboxed attempt requires a fresh review of the retry." —— 先在沙箱内尝试，被沙箱拒绝后申请提权重试，重试必须重新审查。
- exec-server（`codex-rs/exec-server`）：独立的 JSON-RPC 服务，通过 `codex-utils-pty` 启动和控制子进程，承载文件系统/进程处理与平台沙箱分发；向子进程注入 `CODEX_THREAD_ID`、`CODEX_TOOL_CALL_ID`（Code Mode 用嵌套调用 ID）。配合工具的 `environment_id`，harness（"brain"）与执行环境（"hands"）可以分离，一个线程可挂多个环境——与 Anthropic Managed Agents 的 brain/hands 解耦思路相同。

## 13. 指令与扩展

### Cited Findings
- AGENTS.md（docs agents-md）：全局层 `~/.codex/AGENTS.override.md` 否则 `AGENTS.md`（只取第一个非空）；项目层从项目根（通常 git 根）走到 cwd，每级先 `AGENTS.override.md` 再 `AGENTS.md` 再 fallback 文件名；从根到 cwd 拼接，越近越后；累计超过 `project_doc_max_bytes`（默认 32 KiB）停止。会话开始时一次性解析成快照（`agents_md_manager.rs`："Resolves global, thread, and repository instructions into one validated snapshot"，子代理继承快照）——不像 Claude Code 那样在访问子目录时懒加载嵌套指令文件。
- Skills（docs build-skills）：目录 + `SKILL.md`（必须 `name`、`description`）；渐进披露；初始 skill 列表预算为模型上下文窗口的 2%（未知时 8,000 字符），超出先缩短描述，再省略部分 skill 并警告；`skills.max_context_tokens` 可调（≤10,000 tokens）；`$skill-creator` 等以 `$` 调用；`SkillSearch` Stable。
- Plugins：ChatGPT 与 Codex 共用"universal plugin directory"，可打包 skills、MCP servers、hooks（cloud 编排的 ChatGPT Work 不支持插件 hooks）。
- 环境：Local、Worktree、Cloud；Cloud 环境可配置公网访问（全开或白名单）、VPN、network secrets（按允许域名注入）、容器缓存。
- 产品形态：2026-07-09 Codex app 并入 ChatGPT 桌面应用（保留独立的 Codex 编码体验）；远程连接可从手机控制桌面主机或连接 SSH 主机；`codex remote-control`；`codex --remote wss://...` 连远端 app-server。
