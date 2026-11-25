# Claude Code 架构调研笔记（截至 2026-11-20）

> 方法：官方工程博客（anthropic.com/engineering）、官方文档（code.claude.com/docs，原始 markdown 通过 llms.txt 下载）、claude.com 博客为一手来源；第三方逆向分析只作旁证，单独标注。不使用 2026-03-31 npm 包泄露的源码。本机安装的 Claude Code 为 v2.1.314。

## 0. 总体：harness = 模型之外的一切

### Takeaway
官方把 Claude Code 定义为"围绕模型的那一层"：提供工具、管理模型看到的上下文。循环本身很朴素（收集上下文 → 行动 → 验证，模型决定下一步），所有界面（终端、桌面、IDE、Web、Slack、CI）共用同一个 agentic loop，差别只在"代码在哪执行、怎么交互"。

### Cited Findings
- "Claude Code is the layer around the model that provides the tools and manages the context the model sees. This surrounding layer is what the term agentic harness refers to." — how-claude-code-works.md
- 工具五类：File operations / Search / Execution / Web / Code intelligence（需要 code intelligence 插件，LSP）；另有 spawn subagent、提问用户等编排工具。
- 三种执行环境：Local / Cloud（Anthropic 管理的 VM 或组织自托管 self-hosted environments）/ Remote Control（本机执行、浏览器控制）。
- 会话持久化：每条消息、工具调用、结果写入 `~/.claude/projects/` 下的明文 JSONL；支持 rewind、resume（同 session ID 追加）、fork（`--fork-session` 或 `/branch` 复制历史为新 ID）。
- 编辑前对受影响文件快照（checkpoint），与 git 无关，Esc 两下回滚；远端副作用（DB、API、部署）无法回滚。
- 自动注入的上下文（system reminder）：CLAUDE.md、output style 指令、"之前编辑过的文件已在磁盘上变化"提示、commit/PR attribution 行。
- auto memory：MEMORY.md 前 200 行或 25KB（先到者）每次会话开头加载。
- 压缩：接近上限时自动 compact 成摘要；CLAUDE.md 里可写 "Compact Instructions" 段，`/compact focus on ...`；单个文件/输出过大导致摘要后立刻又满时，几次后停止自动压缩并报 thrashing 错误，不进入死循环。
- MCP 工具定义默认 deferred，经 tool search 按需加载，未使用前只有工具名和 server instructions 占上下文。
- Skills 按需加载：会话开始只看到描述；`disable-model-invocation: true` 连描述也不放；`skillOverrides` 从 settings 控制。
- 子代理有独立上下文；fork 子代理带一份当前对话拷贝；子代理的工具调用不进主上下文，只回一个摘要。
- 权限模式（Shift+Tab 轮换）：Auto（分类器后台审查，v2.1.283 起为交互终端与 VS Code 的默认起始模式）、Manual、Accept edits、Plan。
- 排队消息：Claude 跑工具时输入的消息会在这批工具调用结束后、同一 turn 内被读到（mid-turn steering）。

## 1. Code Mode（实验特性，code-mode.md）

### Takeaway
Code Mode 把"模型逐个发工具调用、逐个读结果"改成"模型写一段 JavaScript，由 Claude Code 在会话里执行，脚本内部调用 Bash/Read/Edit/MCP 等工具，只把最后一个表达式的值返回给模型"。承载它的工具叫 `REPL`。每个内部工具调用仍按原工具名过 deny/ask 规则和 PreToolUse hook；auto mode 的分类器审查的是整段代码而不是每个调用。

### Cited Findings
- 示例代码（文档原文）：
  ```javascript
  const files = await $`rg -l "function retryPolicy" src`.text();
  const target = files.trim().split("\n")[0];
  await edit(target, "maxAttempts: 3", "maxAttempts: 5");
  ({ edited: target, tests: $`npm test -- retryPolicy`.nothrow() });
  ```
  `$` 模板标签跑 shell，`edit()` 是编辑工具的函数形态，最后一个表达式作为返回值。
- 界面上显示的是工具调用（如 diff），不是代码；Ctrl+O 打开 transcript viewer，代码以折叠的 **Code** 行出现。
- 适用：跨多文件的重复改动；需要先过滤再读的搜索；可并行的独立命令。
- 开关：`CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1`（shell 或用户 settings）；项目 settings 里设置会被忽略（防止克隆的仓库替你打开）；managed settings 设 `0` 可全组织禁用，但不能从 managed settings 强制打开。首次需确认，会话顶部显示 "Code Mode (experimental) is on · this session can be resumed only with Code Mode on"。
- 要求：v2.1.323+；Claude 订阅或 Console 账号，直连 Anthropic API（不支持网关、Bedrock、Vertex、Foundry 等）；非 HIPAA/ZDR；模型 Opus 5.5 / Sonnet 5.5 / Haiku 5.5 / Fable 5.2；仅交互终端（不支持 `-p`、Agent SDK、VS Code、Desktop、云端会话）；native build；与 agent teams 共用需 v2.1.326+（in-process 显示模式下 teammate 也跑 Code Mode）；不能有 apiKeyHelper、OpenTelemetry 导出、DISABLE_TELEMETRY（需要下载 feature flag）。
- 限制：只能在开启 Code Mode 的终端里 resume；不能用 Remote Control、Claude in Chrome、computer use；不能移到 Desktop 或转成后台会话。
- 权限：auto / bypassPermissions 下代码可整段跑完；每个工具调用仍按原名（Bash、MCP 工具名）走 deny/ask 规则与 PreToolUse hook。auto mode 分类器审查"每段代码"而非每次调用，**看不到运行时计算出的值**（如从搜索结果里取的路径），所以关键控制要靠规则或 hook。
- Hook 注意：Code Mode 下 `git push` 可能以 `cd 'app' && git push` 形式到达 hook，hook 要像 Bash 规则一样拆复合命令检查。`PreToolUse` matcher `REPL` 可从 `tool_input.code` 读 JS；`PostToolUse` matcher `REPL` 可读/改写 `tool_response`（字段 code / result / stdout / stderr / toolsUsed / error / images / documents / refusedCalls），返回 `updatedToolOutput` 脱敏。`refusedCalls` 会原样重复被拒调用的输入，不能靠它脱敏。
- `"permissions": {"ask": ["REPL"]}` 可要求每段代码先审批（审批框不含代码，需 Ctrl+O 查看）。
- 运行中的权限提示：答 No/Esc → 代码停止，已完成的改动保留；No + 备注 → 只拒这次调用，代码可继续。
- 沙箱：代码发起的 shell 命令受 sandbox 约束；**JS 本身和它的文件/Web/MCP 调用在沙箱外**，要隔离需容器/VM。strict sandbox mode 可阻止代码请求 unsandboxed 执行。
- 长任务：前台等待最多 10 分钟（v2.1.328 起 Manual/acceptEdits 下等待权限提示的时间不计入）；后台（需以 auto 或 bypassPermissions 启动）最多 2 小时，可继续对话；`/tasks` 里可停。
- PreToolUse hook 在代码调用中没给出决定（失败/超时）就拒绝该调用，即使未设 `onFailure`；"the hook did not finish" 在会话运行过代码后对任何工具调用都生效。
- v2.1.328 起 `UserPromptSubmit` hook 不再收到代码输出（之前后台代码完成的输出以 `source: poll_event` 的 prompt 形式回流）。
- auto mode 下用户在多次分类器拦截后亲自批准的代码，其中的工具调用可能出现带倒计时的提示（无 IDE 时 1:59 后自动拒绝，避免卡住无人值守会话）。

### Inferences
- 后台代码结果以 "poll_event" 形式回流成新的输入，说明 Code Mode 的后台执行复用了会话的异步事件队列（与后台 bash、MCP 自动后台化同一机制，都在 `/tasks` 里）。
- 模型可用性门槛（5.5 代及以上）说明需要模型专门训练来写这种"工具即函数"的 JS。

## 2. 沙箱（engineering/claude-code-sandboxing，2025-10-20）

### Takeaway
沙箱是"预先划好边界、边界内免审批"的思路：OS 级原语（Linux bubblewrap、macOS seatbelt）同时做文件系统隔离和网络隔离，覆盖命令派生的所有子进程。内部使用中权限提示减少 84%。runtime 已开源（sandbox-runtime）。同篇发布 Claude Code on the web：每个会话一个隔离云沙箱，git 凭据不进沙箱，由 git proxy 校验后代为附加 token。

### Cited Findings
- 默认权限模型：只读，修改与执行需批准；echo/cat 等安全命令自动放行。
- "effective sandboxing requires both filesystem and network isolation"：没有网络隔离可外传 SSH key；没有文件隔离可逃出沙箱拿到网络。
- 文件：允许读写 cwd，阻止修改 cwd 之外文件。网络：只能经 unix domain socket 连到沙箱外的代理服务器，代理按域名放行并对新域名请求用户确认，可定制任意出站规则。
- 越界时立即通知用户选择是否允许。
- Web 版：沙箱内 git 客户端用 scoped credential 认证到自研 proxy，proxy 校验凭据和 git 交互内容（如只能 push 到配置的分支），再附上真实 token 发往 GitHub。

## 3. Auto mode（engineering/claude-code-auto-mode，2026-03-25，John Hughes）

### Takeaway
Auto mode 用模型分类器代替人审批，两层防御：输入层是服务端 prompt-injection probe（扫描工具输出，可疑时给 agent 加警告）；输出层是 transcript classifier（Sonnet 4.6），两阶段：单 token 快速过滤（倾向拦截）→ 被标记的再走 CoT 推理。分类器"reasoning-blind"：只看用户消息和工具调用本身，去掉 assistant 文本和工具结果。

### Cited Findings
- 动机：用户实际会批准 93% 的手动提示（approval fatigue）。内部事故：误删远端分支、把 GitHub token 上传到内部集群、对生产库跑 migration——都是 overeager。
- 威胁模型四类：overeager、honest mistakes、prompt injection、misaligned model。分类器针对前两类调优。
- 权限三层：Tier 1 内置安全工具白名单（读、搜索、代码导航、todo、plan 模式切换）+ 用户 allow 规则——但**进入 auto mode 时丢弃已知可任意执行代码的规则**（全量 shell、通配解释器 python/node/ruby、包管理器 run）；Tier 2 项目内文件写/改直接放行（可用版本控制回看）；Tier 3 其余（shell、web fetch、外部工具、子代理派生、项目外文件操作）进分类器。
- 分类器 prompt = 固定模板 + 三个可定制槽：environment（信任边界，默认只信任当前 git repo）、block rules（默认 20+ 条：Destroy or exfiltrate / Degrade security posture / Cross trust boundaries / Bypass review or affect others）、allow exceptions。`claude auto-mode defaults` 查看。
- 固定模板要点：按真实影响而非表面文本评估（写文件再执行就评估 payload；`&&` 链视作一个动作；Python 拼 shell 命令要评估拼出来的命令）；用户意图规则保守："clean up my branches" 不授权批量删除，"can we fix this?" 是问题不是指令。
- 结果：真实流量 n=10,000，全流程 FPR 0.4%（stage1 8.5%）；真实 overeager n=52，FNR 17%（stage1 6.6%）；合成外传 n=1,000，FNR 5.7%。漏判多是"找到像同意的证据但没核对是否覆盖该动作的影响范围"。
- stage 2 与 stage 1 输入相同只改最后一句指令，因此几乎全部命中 prompt cache。
- 子代理：委派时（可拒绝）和返回时（只警告，给发起者前置安全警告）都跑分类器。
- Deny-and-continue：拒绝作为工具结果返回并要求善意对待边界、找更安全路径；连续 3 次或累计 20 次拒绝则停下交给人；`claude -p` 下直接终止进程。

## 4. 隔离全景（engineering/how-we-contain-claude，2026-05-25）

### Takeaway
限制"爆炸半径"有两条路：监督（human-in-the-loop、auto mode）和隔离（环境层）。Anthropic 的结论是"先在环境层做隔离，再在模型层引导行为"；三个产品三种隔离形态：claude.ai 用 gVisor 临时容器，Claude Code 用"人在回路 + OS 沙箱"，Cowork 用本地 VM。

### Cited Findings
- 遥测：用户批准约 93% 的权限提示；老用户自动批准率约为新用户两倍，但更常中途打断（从"逐步审批"转向"偏航时介入"）。
- 防御三个组件：环境（沙箱/VM/egress）、模型（system prompt、分类器、probe、训练）、外部内容（MCP、插件、web）。Opus 4.7 在 Gray Swan ART 上单次注入成功率约 0.1%，100 次自适应攻击后 5–6%；auto mode 拦下约 83% 的 overeager 行为。
- Claude Code 的沙箱：读允许，工作区内写允许，网络默认拒绝；Seatbelt / bubblewrap；提示减少 84%；runtime 开源。
- 事故 1：信任对话框之前的代码。clone 的仓库里 `.claude/settings.json` 定义的 hook 在 "Do you trust this folder?" 之前就被执行（2025 年中到 2026-01 共收到 3 个同类漏洞）。修复：项目本地配置的解析和执行推迟到用户接受信任提示之后。
- 事故 2：用户成为注入向量。2026-02 内部红队钓鱼，让员工粘贴一段 prompt，暗含读 `~/.aws/credentials` 并 POST 出去；25 次中成功 24 次。模型层以用户意图为锚，用户亲自输入时无异常可抓，只有环境层（egress + 文件边界）能挡。
- Cowork：最初 agent loop 跑在 VM 内（full-VM mode），后改为 loop 在宿主、代码执行在 VM 内（host mode），本地 MCP server 也移到 VM 外；挂载模式 read-only / read-write / read-write-no-delete；symlink 先解析再校验路径。
- 事故 3：经批准域名外传。允许 api.anthropic.com，恶意文件带攻击者 API key 让 Claude 调 Files API 上传。修复：VM 内 MITM 代理只放行带 VM 自己 session token 的请求。结论："allowlist 是能力授予，不是目的地过滤"。
- 原则：自己造的组件最弱（hypervisor、seccomp、gVisor 都可靠，自研代理出过事）；隔离强度匹配用户的监督能力。
- 展望：持久记忆投毒（CLAUDE.md、memory、定时 agent 的状态目录）；多 agent 信任升级（子代理输出不能被当作更高信任）；agent 身份。

## 5. Managed Agents（engineering/managed-agents，2026-04-08）

### Takeaway
把 agent 虚拟化成三个接口：session（只追加的事件日志）、harness（调用 Claude、路由工具调用的循环）、sandbox（执行环境）。"brain"（Claude + harness）与"hands"（沙箱和工具）解耦，hand 统一成 `execute(name, input) → string`。这是 Claude Code 这类 harness 的"元 harness"。

### Cited Findings
- 动机：harness 编码了"Claude 做不到什么"的假设，会随模型变强而过时。例：Sonnet 4.5 有 "context anxiety"（感觉快满就提前收尾），加了 context reset；到 Opus 4.5 行为消失，reset 成了累赘。
- 早期把 session、harness、sandbox 放进一个容器 → 容器成了"宠物"，挂了会话就丢；调试要进带用户数据的容器。
- 解耦后：容器挂了 = 工具调用错误，交还 Claude 决定重试，`provision({resources})` 重建；harness 挂了用 `wake(sessionId)` + `getSession(id)` 从最后事件恢复；运行中 `emitEvent(id, event)` 持久化。
- 安全：token 永不进沙箱。Git 用仓库 token 在沙箱初始化时 clone 并写进 remote；MCP 的 OAuth token 存 vault，经专用代理调用，harness 也不知道凭据。
- "The session is not Claude's context window"：session 是在上下文之外的持久上下文对象，`getEvents()` 按位置切片读取；harness 可在送入上下文前任意变换（为 cache 命中率和 context engineering 服务）。把"可恢复的存储"与"任意的上下文管理"分离。
- 性能：容器按需通过工具调用 provision，p50 TTFT 降约 60%，p95 降超 90%。
- many brains / many hands：hand 不绑定 brain，brain 之间可以传递 hand。

## 6. 用代码调用工具：Code execution with MCP（2025-11-04）与 Advanced tool use（2025-11-24）

### Takeaway
这是 Code Mode 的思想源头。问题有二：工具定义占满上下文、中间结果反复穿过模型。解法：把 MCP server 呈现为文件系统上的代码 API，模型按需读定义、写代码编排、只把需要的结果打印回来。随后平台化为三件套：Tool Search Tool、Programmatic Tool Calling（PTC）、Tool Use Examples。

### Cited Findings
- MCP 代码执行：`servers/<server>/<tool>.ts` 文件树，每个工具一个文件包一层 `callMCPTool`；示例任务从 150,000 tokens 降到 2,000（-98.7%）。明确提到 Cloudflare 把同一思路称为 "Code Mode"。
- 收益：渐进披露（或 `search_tools` 带 detail level 参数）、在执行环境里过滤（10,000 行表只看 5 行）、循环/条件/错误处理用代码表达（省掉每次分支都等模型的 TTFT）、隐私（中间结果默认不进上下文，harness 可自动把 PII token 化，下一次工具调用时再还原）、状态持久化与把代码存成 skill（加 SKILL.md）。代价：需要安全执行环境、资源限制、监控。
- Tool Search Tool：工具标 `defer_loading: true`，只加载搜索工具（~500 tokens）；五个 server 58 个工具约 55K tokens，内部见过 134K；85% token 减少；Opus 4 MCP 评测 49%→74%，Opus 4.5 79.5%→88.1%；不破坏 prompt cache（deferred 工具不在初始 prompt 里）。内置 regex 和 BM25 两种搜索。`mcp_toolset` 可整 server 延迟加载、个别工具常驻。
- PTC：`code_execution_20250825` + 工具上 `allowed_callers`；Claude 写 Python（asyncio.gather 并行），脚本运行到工具调用时暂停，API 把带 `caller` 字段的 tool_use 发给开发者，结果回到脚本而非模型上下文；最后只有 stdout 进上下文。复杂研究任务平均 token 43,588→27,297（-37%）；知识检索 25.6%→28.5%；GIA 46.5%→51.2%。Claude for Excel 用 PTC。
- Tool Use Examples：`input_examples` 字段补充 JSON Schema 表达不了的用法约定。

### Inferences
- Claude Code 的 Code Mode（JS、本地执行、`REPL` 工具）是 PTC（Python、API 侧沙箱）在本地 harness 的对应物：两者都让"中间结果留在执行环境、只返回最后表达式/stdout"。区别：Code Mode 的 JS 跑在本地 harness 进程，内部工具调用仍走 Claude Code 自己的权限、hook、沙箱链路。

## 7. Skills（engineering/equipping-agents...，2025-10-16，后更新为开放标准 2025-12-18）

### Takeaway
Skill = 含 `SKILL.md` 的目录。三级渐进披露：① 启动时把所有 skill 的 `name` + `description` 预载入 system prompt；② 判断相关时读整个 SKILL.md；③ SKILL.md 引用的附加文件/脚本按需读取或直接执行（脚本不必进上下文）。因为有文件系统和代码执行，skill 可打包的内容"实际上无上限"。

### Cited Findings
- 触发方式：Claude 用 Bash 工具读 `pdf/SKILL.md`，再按需读 `forms.md`。
- skill 可带确定性脚本（PDF 表单字段提取），Claude 运行脚本而不把脚本或 PDF 读进上下文。
- 安全：恶意 skill 可引入漏洞或指示外传，仅从可信来源安装并审计。
- 2025-12-18 发布为开放标准（agentskills），跨平台可移植。

## 8. 上下文工程（engineering/effective-context-engineering...，2025-09-29）

### Takeaway
上下文是边际收益递减的"注意力预算"。Claude Code 是"混合策略"的样板：CLAUDE.md 预先放入，glob/grep 等原语 just-in-time 取文件，绕开过期索引。长任务三件套：compaction、结构化笔记（todo list / NOTES.md / memory tool）、子代理（返回 1,000–2,000 token 摘要）。

### Cited Findings
- "Claude Code is an agent that employs this hybrid model: CLAUDE.md files are naively dropped into context up front, while primitives like glob and grep allow it to navigate its environment and retrieve files just-in-time"
- Claude Code 的 compaction："passing the message history to the model to summarize and compress the most critical details ... preserves architectural decisions, unresolved bugs, and implementation details while discarding redundant tool outputs ... continue with this compressed context plus the five most recently accessed files."
- 最轻的压缩是 tool result clearing（已作为 Developer Platform 特性发布）。
- Claude Code 分析大数据库时写定向查询、存结果、用 head/tail 看，不把完整数据读进上下文。

## 9. 记忆（engineering/components-of-agent-memory，2026-11-06，Lance Martin）

### Takeaway
记忆分层：transcripts（可搜索事件日志）→ Claude 写的 memory notes → 记忆表示（如 wiki）→ 每次会话注入的上下文。"dreaming"（离线回放 transcript）可纠错、建 wiki、把笔记提升到注入层。Claude Code 的 auto memory 就是"注入层要小"的实例：只加载 MEMORY.md 前 200 行或 25KB，作为索引。

### Cited Findings
- "Claude Code's auto memory keeps its injected layer small ... It loads only the first 200 lines or 25 KB of `MEMORY.md`. This header serves as an index"
- "our documentation on Claude Code warns that longer instruction files reduce adherence"
- 指导原则："tell Claude what memory is for, but not how to use it"；对强模型规定"只记失败"反而降低收益（Opus 5 −0.234）。前沿模型的默认指引是：用户要求或明显值得记时写记忆，交给模型判断。
- CL-Bench：Opus 5.5 0.655→0.838；Sonnet 4.6 0.330→0.364（加引导 0.641）。
- Pokémon：Sonnet 3.7 写 413 个文件、1 个徽章；Fable 5 写 10 个文件 + global.md、4 个徽章。

## 10. Claude Code 质量事故复盘（engineering/april-23-postmortem，2026-04-23）

### Takeaway
三个与模型无关、纯 harness 层的改动叠加导致"变笨"：默认 effort 从 high 改 medium；空闲 >1h 的会话清旧 thinking 的缓存优化 bug（每轮都清）；system prompt 加了长度限制。说明 harness 的上下文管理、默认参数、system prompt 每一行都直接影响智能表现。

### Cited Findings
- 2026-03-04 默认 effort high→medium，04-07 回滚；现默认 Opus 4.7 `xhigh`，其他 `high`；effort 值直接作为 Messages API 的 effort 参数，`/effort` 切换；ultrathink 被带回。
- 2026-03-26 空闲超 1 小时的会话恢复时用 `clear_thinking_20251015` header + `keep:1` 清旧 thinking（反正 cache miss），bug 导致此后每轮都清 → 健忘、重复、怪异工具选择，并且持续 cache miss 导致额度消耗快。04-10 v2.1.101 修复。复现困难是因为内部的消息排队服务端实验和 thinking 展示方式改动掩盖了它。
- 2026-04-16 system prompt 加入 "Length limits: keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless the task requires more detail." 消融实验显示 Opus 4.6/4.7 都降 3%，04-20（v2.1.116）回滚。
- 改进：更多员工用公开构建；每次 system prompt 改动跑分模型的广泛评测和逐行消融；CLAUDE.md 要求模型特定改动按模型 gate；soak 期与灰度。

## 11. 长时运行 harness（effective-harnesses 2025-11-26；harness-design-long-running-apps 2026-03-24；building-c-compiler 2026-02-05）

### Takeaway
三篇共同的结论："harness 的每个组件都编码了'模型自己做不到什么'的假设"，要随模型升级逐个拆除验证。早期用 initializer + coding agent、JSON feature list、progress 文件、git 作为跨上下文窗口的交接；后来 planner/generator/evaluator 三代理 + sprint contract；Opus 4.6 后拆掉 sprint，evaluator 改为末尾一次。agent teams 的原型是 16 个 Claude 在 Docker 里用 git 锁文件协作。

### Cited Findings
- 失败模式：一次做太多（one-shot）、看到有进展就宣布完成、没做端到端测试就标完成。
- feature list 用 JSON（模型比 Markdown 更少乱改），只允许改 `passes` 字段；"It is unacceptable to remove or edit tests"。
- 每个会话开头：`pwd`、读 progress 文件与 git log、读 feature list、跑 init.sh 和基本端到端测试。
- context reset vs compaction：compaction 在原地摘要，延续性好但不给干净的起点，context anxiety 仍在；reset 给干净起点但依赖交接产物。
- 三代理版本跑 6 小时左右 $200 量级，简化后约 4 小时 $124.70。
- C 编译器：16 个 agent，近 2,000 个 Claude Code 会话，$20,000，10 万行 Rust，能编 Linux 6.9（x86/ARM/RISC-V）。harness 是 `while true; do claude --dangerously-skip-permissions -p "$(cat AGENT_PROMPT.md)" ...; done`；每个 agent 一个容器，clone 到 /workspace，push 到裸仓库 /upstream；`current_tasks/*.txt` 作为锁；无编排 agent。"Existing agent scaffolds like Claude Code require an operator to be online"——这正是后来 agent teams、/goal、routines 要解决的。

## 12. 扩展层总览（features-overview.md）与上下文成本

### Takeaway
官方把扩展分成：常驻上下文（CLAUDE.md、output style、`.claude/rules/`）、按需能力（skills、MCP、code intelligence/LSP、subagents、dynamic workflows、cross-session messaging）、事件自动化（hooks），plugins/marketplace 是打包层，artifact 是输出层。核心取舍是上下文成本与确定性：hook 零上下文成本且确定执行；skill 只常驻描述；MCP 只常驻工具名。

### Cited Findings
- 上下文成本表：CLAUDE.md 每次请求全量；output style 每次请求；skills 描述每次请求，正文用时加载；MCP 工具名常驻，schema 按需（tool search 默认开）；code intelligence 编辑后给诊断；subagent 隔离；hooks 零成本（除非返回 additionalContext）。
- 覆盖规则：CLAUDE.md 叠加；skills 按名覆盖（managed > user > project）；subagents（managed > CLI flag > project > user > plugin）；MCP（local > project > user）；hooks 合并全部触发。
- "Put guardrails in hooks. An instruction like 'never edit .env' in CLAUDE.md or a skill is a request, not a guarantee. A PreToolUse hook that blocks the edit is enforcement."
- CLAUDE.md 建议 <200 行；`.claude/rules/` 可用 `paths` frontmatter 只在操作匹配文件时加载。
- skill 与 subagent 可组合：subagent 的 `skills:` 字段预载 skill 全文；skill 的 `context: fork` 在隔离上下文运行。

## 13. 内置工具（tools-reference.md）

### Takeaway
工具集合是 harness 的"手"。2026 年下半年的几个重要变化：macOS/Linux/WSL 默认**不再提供 Glob/Grep 工具**，改由 Bash 里内嵌的 `bfs`、`ugrep` 承担 find/grep；新模型默认**不提供 TodoWrite/Task 工具**（"newer models keep track of multi-step work without a written checklist"）；工具权限规则与工具名一一对应。

### Cited Findings
- 工具清单（节选）：Agent、Artifact、AskUserQuestion、Bash、PowerShell、CronCreate/Delete/List、Edit、Write、Read、NotebookEdit、EnterPlanMode/ExitPlanMode、EnterWorktree/ExitWorktree、Glob、Grep、LSP、ListAgents、SendMessage、Monitor（后台命令每行输出回流，也可开 WebSocket）、PushNotification、RemoteTrigger（云端定时任务，/schedule 背后）、ScheduleWakeup（自定节奏 /loop）、Skill、SubagentHandback（auto mode 下子代理交回报告）、TaskCreate/Get/List/Update/Stop、TodoWrite、ToolSearch、WaitForMcpServers、WebFetch、WebSearch、Workflow、SendFeedback、SendUserFile、ReportFindings、EndConversation、REPL（Code Mode）。
- Bash：每个命令独立进程；`cd` 在项目目录内跨命令保持，越界则重置并在结果后附 `Shell cwd was reset to <dir>`；环境变量不保留（`CLAUDE_ENV_FILE` 或 SessionStart hook 持久化）；默认超时 2 分钟，上限 10 分钟；输出 >30,000 字符时存文件给路径 + 前 2,000 字符预览，失败时 10,000 字符头尾摘录；grep/rg/find/diff/test/git diff 退出码 1 视为正常。
- Read：默认单次 25,000 tokens，整文件 >256KB 拒绝需分页；图片缩放后作为视觉输入；PDF >10 页需 `pages` 分段（每次最多 20 页）；二进制扩展名拒绝并建议 skill 或脚本。
- Edit：精确字符串替换；read-before-edit（新模型在读取无需权限时可直接编辑）；唯一性；文件在磁盘上变化后若 old_string 仍精确唯一匹配仍可编辑；用 cat/head/sed -n/grep/rg 单文件查看也算"读过"，并会加载该路径下的子目录 CLAUDE.md 与 path-scoped rules。
- WebFetch：HTML→Markdown，然后用**独立的模型调用**按 prompt 抽取，Claude 拿到的是抽取结果（"lossy by design"）；拒绝 localhost；每次最多 100,000 字符；缓存 15 分钟；跨 host 重定向不自动跟随。
- Agent 工具：同一工具可启动普通子代理、teammate（带 `name` 且启用 agent teams）、fork（继承完整对话）；`maxTurns` 达到上限返回 partial；子代理默认后台运行；后台子代理的权限提示冒泡到主会话；可 resume（SendMessage）。

## 14. 多代理：subagents / fork / agent teams / dynamic workflows

### Takeaway
Claude Code 有四档并行机制，区别在"谁持有计划"：subagent（Claude 逐轮决定、结果回到上下文）、agent team（lead 监督多个对等会话，共享任务列表 + 邮箱）、dynamic workflow（Claude 写 JS 脚本，运行时执行，中间结果在脚本变量里，最多每次 1,000 个 agent）、以及 fork（带完整对话的子代理）。

### Cited Findings
- 内置子代理：Explore（只读，parent 是 Fable 时订阅用户跑 Opus；指定 quick/medium/very thorough）、Plan（plan mode 研究）、general-purpose。Explore 和 Plan 不加载 CLAUDE.md 和 git status 以节省成本。
- 非 fork 子代理初始上下文：自身 system prompt + 环境信息（不是 Claude Code 的 system prompt）、委派消息、CLAUDE.md 全层级、git status 快照、预载 skills、sibling roster（可 SendMessage 的名单）。不继承 output style、auto memory。上下文窗口按子代理自己的模型。
- 嵌套：默认最多 3 层（`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`），到达深度时扣掉 Agent 工具；并发上限 20（`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`），超过返回 "Concurrent subagent limit reached" 并告诉 Claude 不要重试；总数不限。
- 子代理有自己的 auto-compaction，transcript 里记 `compact_boundary` 事件（含 preTokens）。
- 子代理输出扫描：对模仿 `<system-reminder>`、`Human:`/`Assistant:` 的文本插反斜杠；提到 bypassPermissions 等时前置 `[harness: subagent output matched instruction-shaped pattern(s): ...` 标记；结果外包一个"这是子代理的话，不代表用户授权"的头。
- 子代理持久记忆：`memory: user|project|local`，目录如 `~/.claude/agent-memory/<agent>/`。
- Fork：同 system prompt、工具、模型、历史；默认开（`CLAUDE_CODE_FORK_SUBAGENT`）；`/subtask` 手动 fork，后台运行，结果作为消息回到主会话。
- Agent teams（实验，`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`）：lead + teammates（独立 Claude Code 实例）+ 共享 task list（pending/in progress/completed + 依赖，文件锁防抢）+ mailbox（`~/.claude/teams/{team}/inboxes/{agent}.json`）。显示模式 in-process 或 tmux/iTerm2 split panes。teammate 加载 CLAUDE.md、MCP、skills，但不继承 lead 的对话。只在交互会话里可用（`-p`/SDK 不会 spawn teammate）。
- Dynamic workflows：Claude 写 JS（`export const meta`；`agent(prompt, {schema, label})`、`pipeline()`、`parallel()`、`phase()`、`log()`、`args`），runtime 在隔离环境后台执行；脚本本身无文件/shell 访问、禁止 `import()`；`Date.now()`/`Math.random()` 会抛错，保证重放确定性；默认 16 并发（最多 256），单次 parallel/pipeline ≤4,096 项，单次 run ≤1,000 agent。**可恢复**：按 agent 启动顺序重放，已完成的返回缓存结果，第一个 prompt 变化或失败的 agent 及其后全部重跑（类似 Temporal 式确定性重放）。扇出时同前缀的 agent 先等第一个开始响应再放行（默认最多 5 秒）以共享 prompt cache。触发：prompt 里写 `ultracode` 或"use a workflow"；`/effort ultracode` 让 Claude 对每个实质任务都规划 workflow。内置 `/deep-research`（多角度搜索、交叉核验、投票、带引用报告）。

## 15. 权限、auto mode 现状与沙箱（permission-modes.md / permissions.md / sandboxing.md）

### Takeaway
权限系统 = 模式（基线）+ 规则（allow/ask/deny，按工具名和参数模式匹配）+ hooks/mods + 沙箱（OS 级）。2026 年下半年的 auto mode 已演进为"服务端分类器审查"：在会话的模型请求中由服务端顺带给出 verdict，默认分类器模型 Sonnet 5。沙箱只包 shell 命令，内置文件/Web 工具、hooks、本地 MCP、LSP、mods 都在沙箱外，要统一边界需把整个进程放进容器/VM。

### Cited Findings
- 六种模式：`default`（manual）、`acceptEdits`、`plan`（可用 auto 时也放行分类器批准的命令）、`auto`、`dontAsk`（CI 用，会提示的一律拒绝）、`bypassPermissions`（仅限隔离容器/VM）。deny 规则在所有模式（含 bypass）都生效。
- 任何模式都不自动批准：显式 ask 规则、组织设为 ask 的 connector 工具、AskUserQuestion 与 `requiresUserInteraction` 的 MCP 工具、关键路径 rm/rmdir、跨会话消息保护、开启 block* 设置时的工作区外读写。
- auto mode 判定顺序：先匹配 allow/ask/deny 规则（受保护路径的写、关键路径删除、带域名的命令等例外）→ 工作目录内只读和编辑自动通过 → 其余交给分类器。
- 服务端审查：直连 Anthropic API 的终端/`-p`/SDK/VS Code/Desktop 会话（各有版本门槛，v2.1.271–281），云会话也走服务端；服务端不给 verdict 时**拒绝而不是放行**，连续 10 次无 verdict 可停止 turn；`CLAUDE_CODE_AUTO_MODE_SERVER=0` 关闭。
- 本地分类器默认 Sonnet 5（会话跑 Sonnet 4.6 则用 4.6）。连续 3 次或累计 20 次拦截则暂停 auto、恢复提示（阈值不可配）；`-p` 无提示可回退时动作不执行但 Claude 继续工作。
- 沙箱模式：auto-allow（沙箱内命令免提示）与常规模式；`FOO=bar npm test`、`make CC=clang`、`eval` 这类可能改变实际执行程序的命令仍走权限流程；内容型 ask 规则（`Bash(git push *)`）仍提示。
- 逃生舱：`dangerouslyDisableSandbox` 参数请求非沙箱重试；`allowUnsandboxedCommands: false`（strict）关闭；managed/`--settings` 设置时沙箱变为 admin-required，仓库内放宽沙箱的配置被忽略。
- 沙箱外运行的：Read/Edit/Write/WebFetch/WebSearch（受权限规则约束，`denyRead` 不拦 Read 工具，`allowedDomains` 不限 WebFetch）、command hooks、本地 MCP、plugin monitors、LSP、mods、用户 `!` 输入的命令（后台会话除外）。
- 信任边界：项目本地配置在用户接受 workspace trust 之后才解析执行（呼应 how-we-contain-claude 里的事故）。

## 16. Hooks（hooks.md）

### Takeaway
Hooks 是 harness 生命周期上的确定性扩展点，覆盖会话、prompt、工具、权限、子代理、任务、压缩、模型切换、worktree、MCP elicitation 等约 32 个事件；处理器有五种：command / http / mcp_tool / prompt（单轮模型判定）/ agent（子代理带工具验证，最多 50 轮）。

### Cited Findings
- 事件：SessionStart、Setup、UserPromptSubmit、UserPromptExpansion、PreToolUse、PermissionRequest、PermissionDenied（可 `retry: true`）、PostToolUse、PostToolUseFailure、PostToolBatch（并行工具批次全部完成、下一次模型调用之前）、Notification、MessageDisplay、SubagentStart/Stop、TaskCreated/Completed、Stop、StopFailure、TeammateIdle、InstructionsLoaded、ConfigChange、CwdChanged、DirectoryAdded、FileChanged、WorktreeCreate/Remove、PreCompact/PostCompact、PreModelSwitch/PostModelSwitch、SessionEnd、Elicitation/ElicitationResult。
- 退出码：0 成功（应用 JSON 字段）；2 阻断（stderr 作为原因）；其他为非阻断错误（`onFailure: "block"` 可改）。`if` 字段用权限规则语法过滤（`Bash(git *)`）。默认超时 command/http/mcp_tool 600s，prompt 30s，agent 60s。同一事件的匹配 hook 并行执行，prompt/agent 类最多 12 个并发。
- `/goal` 与 Stop hook 同构：每轮结束后由独立模型评估条件，未满足则开启新 turn；评估器只看对话中 Claude 已呈现的内容，不自己跑命令或读文件；条件最长 4,000 字符。auto mode 只在单 turn 内放行工具调用、不开新 turn，二者互补。

## 17. 记忆与指令（memory.md）

### Cited Findings
- CLAUDE.md 从 cwd 向上每级加载并**拼接**（根在前、cwd 在后，越近越后读）；子目录 CLAUDE.md 在 Claude 读写该目录文件时懒加载；块级 HTML 注释注入前被剥掉；单文件 >4 MiB 跳过。
- AGENTS.md：v2.1.277+ 可直接读取。仓库只有 AGENTS.md 时读它；同时有 CLAUDE.md 时默认只读 CLAUDE.md；可在 CLAUDE.md 里 import AGENTS.md。
- auto memory 存在 `~/.claude/projects/<project>/memory/`；MEMORY.md 前 200 行/25KB 启动加载，写入后若接近上限，Claude Code 提醒 Claude 缩短（一行一条、细节移到主题文件、合并或删除过期项）；主题文件按需读；写入带 frontmatter 的记忆时自动记录 `modified` 时间戳。

## 18. Prompt cache 与上下文布局（prompt-caching.md / context-window.md）

### Takeaway
Claude Code 每轮重发完整上下文，靠前缀缓存保证成本和延迟。请求按变化频率分三层：System prompt（核心指令 + 工具定义）→ Project context（CLAUDE.md、auto memory、无作用域规则）→ Conversation。很多设计（plan mode/skill 以对话消息形式追加、MCP 工具 deferred、advisor 工具定义放在 cache breakpoint 之后、`/fork` 把隔离指令放在复制对话的末尾）都是为了不破坏前缀。

### Cited Findings
- 缓存失效：切模型（每个模型独立缓存）、改 effort（多数模型；5.5 代等新模型在订阅/API key 下保持）、开 fast mode、连接/移除 MCP server（无 tool search 时）、启停插件、deny 整个工具、compaction、大量图片、升级 Claude Code。保持缓存：编辑仓库文件、会话中改 CLAUDE.md（下次 compact 才生效）、切权限模式、切 output style、调用 skill、/recap、rewind。
- tool search 开启时：会话首次请求的工具列表在整个会话中保持不变，之后连上的 server 以 deferred 形式提供。
- TTL：订阅且在额度内，主对话 1 小时，其余（子代理、workflow、teammate、fork、compaction、标题）5 分钟；API key/云厂商一律 5 分钟；超额后主对话降为 5 分钟。`subagentPromptCacheTtl` 可设 1h。
- compaction 用一个独立请求（复用同前缀：system prompt + 工具 + 历史 + 结尾的摘要指令），cache 热时成本远低于上下文大小；空闲后 1h TTL 会话可能被 idle compaction（`idleCompaction`）。
- fork 子代理首个请求能读父缓存；普通子代理不能（前缀不同）。
- compaction 后保留/重建：system prompt 与 output style；根 CLAUDE.md、无作用域规则、auto memory 从磁盘重新注入；skill 描述重建（v2.1.304+）；git status 刷新；plan mode 写的计划从磁盘重注入；最近修改的最多 5 个读过/编辑过的文件被重读（>5,000 tokens 的只给路径）；已调用 skill 正文重注入（每个 ≤5,000 tokens、总 ≤25,000）；后台命令/子代理继续跑并提醒；SessionStart(compact) hook 输出追加。

## 19. 运行形态：后台会话、远程、云端、定时与事件

### Cited Findings
- Agent view（`claude agents`）：后台会话由独立的 supervisor 进程运行，不依赖终端；状态持久化到磁盘，跨自动更新、supervisor 重启、睡眠唤醒；修改代码前被指示建 worktree（`.claude/worktrees/`）。
- Remote Control：执行留在本机，claude.ai/code 和手机 App 只是窗口；多端同步对话、子代理和 workflow 进度；断线自动重连。
- Cloud session：Anthropic 管理的 VM 或组织自托管环境；`claude --cloud` 从终端发起（克隆远端分支而非本地 checkout）；GitHub 凭据加密存服务端，经 GitHub proxy 附加；network secrets 同样在沙箱外附加到匹配请求；环境可配网络级别、环境变量、setup script。
- Cloud scheduled tasks（原 routines）：保存的配置（prompt + 仓库 + connectors），触发器有 Schedule / API（POST + bearer token）/ GitHub 事件，`RemoteTrigger` 工具与 `/schedule` 背后。
- 会话内定时：`/loop`、CronCreate/Delete/List，自定节奏的 `ScheduleWakeup`；Channels：MCP server 主动把事件推进正在运行的会话（Telegram/Discord/iMessage 研究预览），可双向。
- Cross-session messaging：一个会话给另一个会话（本机、其他机器或云端）发文本消息，不传历史和文件。
- Advisor tool：服务端 server tool，Claude 在关键节点（定方案前、反复报错、宣布完成前）咨询更强的模型，advisor 看到完整对话。Fast mode：同模型不同 API 配置，最多快 2.5 倍、单价更高。
- Mods：插件的一种，JS/TS 事件处理器运行在 Claude Code 进程内，可观察/修改/接管工具调用、prompt、UI 绘制；与 settings hooks（外部脚本）区分；不受沙箱约束。

## 20. Agent SDK（agent-sdk/agent-loop.md 等）

### Cited Findings
- SDK（TS/Python）打包原生 Claude Code 二进制，运行与 Claude Code 相同的循环；消息类型 SystemMessage（init、compact_boundary）、AssistantMessage、UserMessage、StreamEvent、ResultMessage（含用量、成本、session ID）；`maxTurns`、`maxBudgetUsd`、effort、permission mode。
- 并行工具执行：只读工具（Read/Glob/Grep、标 readOnly 的 MCP 工具）并发；改状态的（Edit/Write/Bash）串行；自定义工具默认串行，设 `readOnlyHint` 才并发。
- 上下文：核心内置工具（Bash/Read/Edit）定义每次请求都带；MCP 工具与其他内置工具默认被 tool search 延迟。
- 压缩可定制：CLAUDE.md 里写摘要指令（标题任意，按意图匹配）、PreCompact hook、发送 `/compact`。
- MCP tool search：工具名 + server instructions 启动加载；server instructions 与常驻工具描述截断上限 4,096 字符，经搜索加载的工具描述 16,384 字符。
