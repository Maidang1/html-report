---
title: Claude Code 的架构：模型之外的那一层 harness
kicker: 架构解读
deck: 基于 Anthropic 工程博客与官方文档（截至 2026-11-20），拆解 Claude Code 的循环、上下文与缓存、工具与 Code Mode、扩展层、多代理、权限与沙箱和运行形态：循环本身很朴素，复杂度几乎都在循环之外。
meta:
  - Claude Code v2.1.329（2026-11-19）
  - anthropic.com/engineering + code.claude.com/docs
footer: 主要来源：Anthropic 工程博客（Code execution with MCP、Advanced tool use、Making Claude Code safer with sandboxing、How we built Claude Code auto mode、An update on recent Claude Code quality reports、Scaling Managed Agents、How we contain Claude across products、Effective context engineering、Building a C compiler with a team of parallel Claudes）与 code.claude.com/docs（how-claude-code-works、code-mode、prompt-caching、context-window、tools-reference、sub-agents、agent-teams、workflows、hooks、skills、memory、permission-modes、sandboxing、changelog）。未使用任何泄露源码。
index: index.html
updated: 2026-11-20
created: 2026-11-20
---

Claude Code 的“智能”来自模型，但它能在真实仓库里连续工作几个小时，靠的是模型之外的那一层：工具、上下文管理、权限与沙箱、扩展机制和运行形态。Anthropic 把这一层叫作 **agentic harness**。本文只用 Anthropic 工程博客和 Claude Code 官方文档（截至 2026-11-20，对应 v2.1.329），按“循环 → 上下文 → 工具 → Code Mode → 扩展 → 多代理 → 安全 → 运行形态”的顺序拆解这一层是怎么设计的，以及每个设计在防什么问题。

先给结论：循环本身很朴素，复杂度几乎全部在循环外面。Claude Code 的设计可以归结为三条主线：**把上下文当预算**（前缀缓存、按需加载、压缩），**把确定性的事交给代码**（hooks、Code Mode、dynamic workflows），**把安全交给环境而不是提示词**（OS 沙箱、代理、分类器只做减负）。

- **一个循环，多种界面**：终端、桌面、IDE、Web、Slack、CI 和 Agent SDK 跑的是同一个 agentic loop，只是执行位置和交互方式不同
- **上下文是三层前缀**：System prompt → Project context → Conversation，很多功能的形态是为了不打破缓存前缀
- **Code Mode**：模型写 JavaScript 调工具，中间结果留在脚本里；每个嵌套调用仍逐个过权限规则和 hook
- **四档多代理**：subagent、fork、agent teams、dynamic workflows，区别在“谁持有计划”
- **安全分层**：模式与规则定基线，OS 沙箱封边界，auto mode 分类器替人审批，事故经验反复指向“环境层兜底”
- **harness 会过时**：每个组件都编码了“模型做不到什么”的假设，模型变强后要逐个拆掉验证

## 总体：Claude Code 的价值在模型之外的那一层

官方对 agentic loop 的描述只有三步：**收集上下文、采取行动、验证结果**，三步交织、由模型决定每一步做什么，用户可以随时打断或在运行中插话。Claude Code 自己的定位是“围绕模型的那一层”：提供工具、管理模型看到的上下文（右侧原文）。

这层 harness 对所有界面一视同仁。官方列出的执行环境有三种：**Local**（本机，默认）、**Cloud**（Anthropic 管理的 VM，或组织自托管的 self-hosted environments）、**Remote Control**（代码在本机执行，浏览器和手机只是窗口）。界面层包括终端、桌面应用、IDE 插件、claude.ai/code、Slack 和 CI；Agent SDK 则直接打包了原生 Claude Code 二进制，跑的是同一个循环。

会话状态全部落在本地：每条消息、工具调用和结果写进 `~/.claude/projects/` 下的明文 JSONL，所以能 resume（同一 session ID 追加）、fork（复制历史到新 ID）和 rewind。编辑文件前还会给受影响的文件拍快照（checkpoint），双击 Esc 即可回滚；但数据库、API、部署这类远端副作用无法回滚，只能靠权限控制。

```md title="code.claude.com/docs/en/how-claude-code-works.md:23"
The agentic loop is powered by two components: [models](#models) that reason and [tools](#tools) that act. Claude Code is the layer around the model that provides the tools and manages the context the model sees. This surrounding layer is what the term agentic harness refers to.
```

```svg title="图 1：Claude Code 的分层（根据官方文档整理）" tone="light"
<svg viewBox="0 0 760 430" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, 'Segoe UI', 'PingFang SC', 'Noto Sans CJK SC', sans-serif" font-size="13">
  <rect x="0" y="0" width="760" height="430" fill="#f7f8fa"/>
  <text x="20" y="28" fill="#555" font-size="12">界面层</text>
  <g fill="#ffffff" stroke="#c9ced6">
    <rect x="20" y="38" width="108" height="34" rx="6"/><rect x="138" y="38" width="108" height="34" rx="6"/><rect x="256" y="38" width="108" height="34" rx="6"/><rect x="374" y="38" width="108" height="34" rx="6"/><rect x="492" y="38" width="108" height="34" rx="6"/><rect x="610" y="38" width="130" height="34" rx="6"/>
  </g>
  <g fill="#333" text-anchor="middle">
    <text x="74" y="60">终端 CLI</text><text x="192" y="60">桌面应用</text><text x="310" y="60">IDE 插件</text><text x="428" y="60">Web / 手机</text><text x="546" y="60">Slack / CI</text><text x="675" y="60">Agent SDK</text>
  </g>
  <rect x="20" y="92" width="530" height="196" rx="10" fill="#eaf0fd" stroke="#2457d6"/>
  <text x="36" y="116" fill="#2457d6" font-weight="600">harness 核心（各界面共用）</text>
  <g fill="#ffffff" stroke="#2457d6">
    <rect x="40" y="132" width="110" height="40" rx="6"/><rect x="176" y="132" width="110" height="40" rx="6"/><rect x="312" y="132" width="110" height="40" rx="6"/>
  </g>
  <g fill="#1d2b4f" text-anchor="middle">
    <text x="95" y="157">组装上下文</text><text x="231" y="157">模型推理</text><text x="367" y="157">执行工具</text>
  </g>
  <g stroke="#2457d6" fill="none" stroke-width="1.5">
    <path d="M150 152 H172"/><path d="M286 152 H308"/><path d="M367 172 V190 H95 V176"/>
  </g>
  <text x="231" y="204" fill="#555" font-size="12" text-anchor="middle">结果回流，直到模型不再请求工具</text>
  <g fill="#ffffff" stroke="#c9ced6">
    <rect x="40" y="222" width="150" height="50" rx="6"/><rect x="204" y="222" width="150" height="50" rx="6"/><rect x="368" y="222" width="166" height="50" rx="6"/>
  </g>
  <g fill="#333" font-size="12">
    <text x="50" y="243">上下文管理</text><text x="50" y="261" fill="#666">缓存前缀 · 压缩 · 按需加载</text>
    <text x="214" y="243">权限与沙箱</text><text x="214" y="261" fill="#666">模式 · 规则 · auto mode</text>
    <text x="378" y="243">会话持久化</text><text x="378" y="261" fill="#666">JSONL · checkpoint · fork</text>
  </g>
  <rect x="570" y="92" width="170" height="196" rx="10" fill="#ffffff" stroke="#c9ced6"/>
  <text x="584" y="116" fill="#333" font-weight="600">扩展层</text>
  <g fill="#444" font-size="12">
    <text x="584" y="142">CLAUDE.md / rules</text><text x="584" y="164">Skills</text><text x="584" y="186">MCP（tool search）</text><text x="584" y="208">Hooks / Mods</text><text x="584" y="230">Subagents / Workflows</text><text x="584" y="252">Plugins / Marketplace</text><text x="584" y="274">Output styles</text>
  </g>
  <path d="M570 190 H552" stroke="#999" stroke-width="1.5"/>
  <text x="20" y="318" fill="#555" font-size="12">执行环境（代码在哪里跑）</text>
  <g fill="#ffffff" stroke="#c9ced6">
    <rect x="20" y="328" width="230" height="62" rx="6"/><rect x="265" y="328" width="230" height="62" rx="6"/><rect x="510" y="328" width="230" height="62" rx="6"/>
  </g>
  <g fill="#333">
    <text x="34" y="352">Local</text><text x="34" y="373" fill="#666" font-size="12">本机；OS 沙箱包住 shell 命令</text>
    <text x="279" y="352">Cloud</text><text x="279" y="373" fill="#666" font-size="12">托管 VM 或自托管环境；git 代理</text>
    <text x="524" y="352">Remote Control</text><text x="524" y="373" fill="#666" font-size="12">本机执行，网页/手机做窗口</text>
  </g>
  <path d="M285 288 V324" stroke="#999" stroke-width="1.5"/>
  <text x="20" y="418" fill="#888" font-size="11">后台会话由 supervisor 进程托管；Agent SDK 内置同一个二进制</text>
</svg>
```

## 上下文与缓存：每轮都重发全部上下文，所以每个字节都要算账

模型在两次请求之间不记得任何东西，Claude Code 每发一条消息都会**重新发送完整上下文**：system prompt、项目上下文、全部历史消息和工具结果，再加上新消息。成本和延迟能接受，全靠 API 的前缀缓存：只要请求开头与最近处理过的内容逐字一致，就按缓存价计费，只重新计算变化的部分。缓存按前缀精确匹配，前缀里任何一处改动都会让其后的全部内容重算，没有按文件或分段的缓存。

所以 Claude Code 把请求按“变化频率”排成三层（下表）。这个排序解释了许多看似无关的设计：plan mode 和 skill 的指令以**对话消息**追加而不是改 system prompt；开启 tool search 时，会话第一次请求的工具列表在整场会话中固定不变，中途才连上的 MCP server 只以 deferred 形式出现；advisor 工具定义放在缓存断点之后，开关它不破坏前缀；`/fork` 把隔离指令放在复制对话的末尾。

| 层 | 内容 | 何时变化 |
| --- | --- | --- |
| System prompt | 核心指令、工具定义 | 已加载的工具定义集合变化时 |
| Project context | CLAUDE.md、auto memory、无作用域的 rules | 会话开始，或 `/clear`、`/compact` 之后 |
| Conversation | 用户消息、Claude 的回复、工具结果 | 每一轮 |

会让缓存失效的动作：切换模型（每个模型独立缓存）、改 effort（多数模型如此；5.5 代等新模型在订阅或 API key 下保持缓存）、打开 fast mode、在没有 tool search 时增删 MCP server、启停插件、deny 整个工具、压缩、累积大量图片、升级 Claude Code。不会失效的：编辑仓库文件、会话中修改 CLAUDE.md、切权限模式、切 output style、调用 skill、rewind。缓存 TTL 也分桶：订阅额度内主对话是 1 小时，子代理、workflow、teammate、fork、压缩请求都是 5 分钟；API key 和云厂商一律 5 分钟。

### 压缩：摘要之后，哪些东西会被重新注入？

接近上限时，Claude Code 用一个独立请求把历史压缩成摘要。这个请求复用同一前缀（system prompt、工具、历史，末尾加摘要指令），缓存热的时候成本远低于上下文大小；这也是官方建议在任务间隙主动 `/compact`、而不是等到任务中途自动触发的原因。工程博客对这一步的描述见右侧：保留架构决策、未解决的 bug 和实现细节，丢掉冗余的工具输出，再带上最近访问的 5 个文件。

文档给出了压缩后的完整清单：system prompt 与 output style 继续生效；根目录 CLAUDE.md、无作用域 rules、auto memory 从磁盘重新注入；skill 描述重建；git status 重新读取；plan mode 写下的计划从磁盘重注入；最近修改的至多 5 个读过或编辑过的文件被重读（超过 5,000 tokens 的只给路径）；已调用的 skill 正文重注入，每个 ≤5,000 tokens、合计 ≤25,000；后台命令和后台子代理继续运行，并提醒 Claude 哪些还在跑。带 `paths` 的规则和子目录 CLAUDE.md 则随历史一起被摘要掉，用到时再按需加载。如果单个文件或输出大到摘要后立刻又满，几次之后会停止自动压缩并报 thrashing 错误，而不是陷入循环。

```text title="anthropic.com/engineering/effective-context-engineering-for-ai-agents · Compaction"
In Claude Code, for example, we implement this by passing the message history to the model to summarize and compress the most critical details. The model preserves architectural decisions, unresolved bugs, and implementation details while discarding redundant tool outputs or messages. The agent can then continue with this compressed context plus the five most recently accessed files. Users get continuity without worrying about context window limitations.
```

### 检索策略：CLAUDE.md 预先放入，其余 just-in-time

Anthropic 在上下文工程一文里把 Claude Code 作为“混合策略”的例子：CLAUDE.md 直接放进上下文，其余内容由 glob、grep 这类原语在需要时去取，绕开了预建索引过期和语法树复杂的问题。分析大型数据库时也是同样思路：写定向查询、把结果存下来、用 `head`/`tail` 查看，而不是把整份数据读进上下文。auto memory 遵守同样的预算观：只在会话开头加载 `MEMORY.md` 的前 200 行或 25KB，它只是索引，主题文件按需读取。

### 2026 年 4 月的质量事故：harness 的每个细节都在影响智能

2026 年 3–4 月，用户反映 Claude Code “变笨了”。复盘显示 API 和推理层都没问题，问题是三处 harness 改动叠加：

**默认 effort**：3 月 4 日把默认推理强度从 `high` 降到 `medium` 以减少长尾延迟，4 月 7 日回滚；现在 Opus 4.7 默认 `xhigh`，其他模型默认 `high`。effort 值就是发给 Messages API 的 effort 参数。**清理旧 thinking 的缓存优化**：3 月 26 日上线“空闲超过 1 小时的会话恢复时清掉旧 thinking”（反正会 cache miss），用的是 `clear_thinking_20251015` 加 `keep:1`；bug 让它在此后**每一轮**都清，Claude 越来越不记得自己为何这样做，表现为健忘、重复、工具选择怪异，并因持续 cache miss 更快耗尽额度。4 月 10 日在 v2.1.101 修复。**一行 system prompt**：4 月 16 日为抑制 Opus 4.7 的啰嗦加了右侧这句长度限制，逐行消融显示 Opus 4.6 和 4.7 都掉了 3%，4 月 20 日（v2.1.116）回滚。

> 这句话短，却直接压低了“工具调用之间的思考文字”。后续措施包括：每次改 system prompt 都跑分模型的广泛评测和逐行消融，模型特定的改动按模型 gate，可能牺牲智能的改动加 soak 期和灰度。

```prompt title="anthropic.com/engineering/april-23-postmortem · 2026-04-16 加入、04-20 回滚的 system prompt 行"
“Length limits: keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless the task requires more detail.”
```

## 工具面在收缩：更少的专用工具，更强的 Bash

官方把内置工具分为五类：文件操作、搜索、执行、Web、代码智能（需安装 LSP 插件），另有子代理、提问、计划、worktree、定时、消息等编排工具。2026 年的演进方向很清楚：**能交给 shell 的就不单设工具**，单设的工具则把边界条件写死。

第一，**Glob 和 Grep 默认消失**。v2.1.117（2026-04-22）起，macOS、Linux、WSL 的原生构建不再提供这两个工具，Claude 直接在 Bash 里用 `find` 和 `grep`，它们实际调用内嵌的 `bfs` 和 `ugrep`，少一次工具往返；搜索也因此以 `Bash` 调用的身份经过 hooks 和权限规则。只有用 `--tools` 点名、或会话禁用了 Bash 时才会恢复。第二，**新模型默认不给任务清单工具**：TaskCreate 等和 TodoWrite 只在 Opus 4.7、Sonnet 4.6、Haiku 4.5 及更早模型上默认提供，理由是新模型不靠书面清单也能跟踪多步工作，而这些工具的定义和提醒本身占上下文。

保留下来的工具都有细致的护栏：

| 工具 | 关键行为 | 为什么这样设计 |
| --- | --- | --- |
| Bash | 每条命令独立进程；`cd` 只在项目目录内跨命令保持，越界则重置并附 `Shell cwd was reset to <dir>`；环境变量不保留；默认超时 2 分钟、上限 10 分钟 | 每次调用状态可预测，避免模型误以为还在某个目录或环境里 |
| Bash 输出 | 成功时内联约 30,000 字符，超出则存文件并给路径和前 2,000 字符预览；失败时给约 10,000 字符的头尾摘录；grep、rg、find、diff、test、git diff 的退出码 1 视为正常 | 大输出不直接灌进上下文，需要时再读或搜；减少把“无匹配”误判为失败 |
| Read | 单次默认至多 25,000 tokens，整文件超过 256KB 需分页；图片缩放后作为视觉输入；PDF 超过 10 页按 `pages` 每次至多 20 页 | 把读取成本变成显式的分页决策 |
| Edit | 精确字符串替换、要求唯一匹配；先读后改（新模型在读取无需授权时可直接改）；用 cat、head、`sed -n`、grep、rg 看过单个文件也算读过，并触发该目录 CLAUDE.md 与 path 规则加载 | 防止基于过期内容改文件，同时不强迫多一次 Read |
| WebFetch | HTML 转 Markdown 后，用**另一次模型调用**按 prompt 抽取，Claude 拿到的是抽取结果（官方称 “lossy by design”）；拒绝 localhost；每次至多 100,000 字符；缓存 15 分钟；跨主机重定向不自动跟随 | 网页原文不进主上下文；跨域跳转需要重新决策 |

工具也决定了权限规则的写法：规则按工具名和参数匹配，例如 `Bash(npm run *)`、`Read(~/secrets/**)`、`Edit(/src/**)`、`WebFetch(domain:example.com)`、`Agent(Explore)`。Agent SDK 还规定了并行规则：只读工具可以并发，Edit、Write、Bash 这类改状态的工具串行执行。

## Code Mode：让模型写 JavaScript 来调用工具

Code Mode 是 2026-11-12（v2.1.323）加入的实验特性，默认关闭。它改变的是工具调用的形态：平时 Claude 逐个请求 Bash、Read、Edit，读完每个结果再决定下一步；开启后，Claude 写一段 JavaScript，由 Claude Code 在会话里执行，脚本内部调用工具，**只有最后一个表达式的值返回给模型**。承载它的工具叫 `REPL`。

右侧是文档里的示例：`$` 模板标签运行 shell 命令，`edit()` 是编辑工具的函数形态，最后一行的对象就是返回给 Claude 的内容（被改的文件路径和测试输出）。一次推理完成了“搜索、取第一个结果、修改、跑测试”四步，中间的搜索结果没有进入上下文。界面上用户看到的仍是工具调用（例如 diff），代码本身折叠在 `Ctrl+O` 的 transcript 视图里。适用场景是跨多文件的重复修改、需要先过滤再读的搜索、可以并行的独立命令。

脚本之间是 REPL 语义：changelog 里有“Fixed a Code Mode script's `let x;` keeping the value that an earlier script gave `x`”和“top-level variables that an earlier failed script never set”这类条目，说明顶层变量会跨脚本保留；还有 `tools` 对象（“losing access to tools through `tools` in sessions with more than 4,096 tools”）以及 `log`、`preview`、`sleep` 等内置名字。

````js title="code.claude.com/docs/en/code-mode.md · How Code Mode works"
```javascript theme={null}
const files = await $`rg -l "function retryPolicy" src`.text();
const target = files.trim().split("\n")[0];
await edit(target, "maxAttempts: 3", "maxAttempts: 5");
````

```svg title="图 2：Code Mode 中一次 REPL 调用的路径（根据官方文档整理）" tone="light"
<svg viewBox="0 0 640 300" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, 'Segoe UI', 'PingFang SC', 'Noto Sans CJK SC', sans-serif" font-size="13">
  <rect x="0" y="0" width="640" height="300" fill="#f7f8fa"/>
  <rect x="20" y="20" width="130" height="44" rx="6" fill="#ffffff" stroke="#c9ced6"/>
  <text x="85" y="47" text-anchor="middle" fill="#333">模型</text>
  <rect x="200" y="20" width="200" height="44" rx="6" fill="#eaf0fd" stroke="#2457d6"/>
  <text x="300" y="40" text-anchor="middle" fill="#1d2b4f">REPL 工具</text>
  <text x="300" y="56" text-anchor="middle" fill="#555" font-size="11">auto mode 分类器审查整段代码</text>
  <path d="M150 42 H196" stroke="#2457d6" stroke-width="1.5"/>
  <rect x="200" y="96" width="200" height="56" rx="6" fill="#ffffff" stroke="#2457d6"/>
  <text x="300" y="118" text-anchor="middle" fill="#333">JavaScript 脚本</text>
  <text x="300" y="138" text-anchor="middle" fill="#666" font-size="11">变量与中间结果留在这里</text>
  <path d="M300 64 V92" stroke="#2457d6" stroke-width="1.5"/>
  <g fill="#ffffff" stroke="#c9ced6">
    <rect x="40" y="186" width="120" height="40" rx="6"/><rect x="180" y="186" width="120" height="40" rx="6"/><rect x="320" y="186" width="120" height="40" rx="6"/><rect x="460" y="186" width="140" height="40" rx="6"/>
  </g>
  <g text-anchor="middle" fill="#333" font-size="12">
    <text x="100" y="211">Bash（$）</text><text x="240" y="211">Edit / Write</text><text x="380" y="211">Read / Web</text><text x="530" y="211">MCP 工具</text>
  </g>
  <g stroke="#999" stroke-width="1.2" fill="none">
    <path d="M260 152 L100 182"/><path d="M285 152 L240 182"/><path d="M315 152 L380 182"/><path d="M340 152 L530 182"/>
  </g>
  <text x="320" y="252" text-anchor="middle" fill="#555" font-size="12">每个嵌套调用：按原工具名过 deny / ask 规则与 PreToolUse hook</text>
  <text x="320" y="272" text-anchor="middle" fill="#555" font-size="12">仅 shell 命令进 OS 沙箱；JS 本身与文件、Web、MCP 调用在沙箱外</text>
  <path d="M200 124 H90 V68" stroke="#2457d6" stroke-width="1.5" fill="none"/>
  <text x="96" y="100" fill="#2457d6" font-size="11">只返回最后一个表达式</text>
</svg>
```

### 权限：审批粒度变了，但规则仍逐个调用生效

Code Mode 最容易被误解的地方是权限。文档写得很明确：脚本里的**每个工具调用**都以原工具名（`Bash` 或 MCP 工具名）经过 deny/ask 规则和 `PreToolUse` hook。变化在 auto mode：分类器审查的是**整段代码**，不是每次调用，因此看不到运行时才算出来的值（比如从搜索结果里取出的路径）。需要硬性控制的命令要靠规则或 hook，例如给 `Bash(git push *)` 加 ask 规则。

hook 作者还要注意命令形态：Code Mode 下 `git push` 可能以 `cd 'app' && git push` 的形式到达 hook，所以 hook 要像 Bash 规则那样拆开复合命令逐段检查。想先看代码再放行，可以给 `REPL` 本身加 ask 规则（右侧）；审批框里不显示代码，需要按 `Ctrl+O` 查看。运行中弹出的权限提示有两种拒绝：直接 No 或 Esc 会停止整段代码（已完成的修改保留）；带备注的 No 只拒绝这一次调用，脚本可以继续。

```json title="~/.claude/settings.json · 每段代码运行前先审批"
{
  "permissions": {
    "ask": ["REPL"]
  }
}
```

### 沙箱与时长：JS 本身不在沙箱里

沙箱设置只约束脚本发出的 **shell 命令**。JavaScript 本身和它发起的文件、Web、MCP 调用都在沙箱外运行，要把它们也隔离，需要把整个 Claude Code 放进容器或虚拟机。开启 strict sandbox mode 后，脚本请求“以非沙箱方式运行命令”也会被拒绝。

时长分两档：前台等待时脚本最多运行 10 分钟（v2.1.328 起，Manual 和 acceptEdits 模式下停在权限提示上的时间不计入）；以 auto 或 bypassPermissions 模式启动的会话可以让代码转入后台，最长 2 小时，期间用户可以继续和 Claude 对话，用 `/tasks` 停止。另一个细节：在会话运行过代码之后，`PreToolUse` hook 没给出决定（崩溃、超时）就会拒绝该调用，即便没有设置 `onFailure`。

### 输出脱敏：在 PostToolUse 里改写脚本的返回值

脚本的返回值进入上下文前，可以被匹配 `REPL` 的 `PostToolUse` hook 读取和改写。`tool_response` 里有 `code`、`result`、`stdout`、`stderr`、`toolsUsed` 等字段（右上）；hook 返回同形状的 `updatedToolOutput` 即可替换（右下）。形状不对、hook 崩溃或超时，Claude 都会拿到原始输出，所以出错时也要返回带占位符的副本。文档另外提醒：`refusedCalls` 字段会原样重复被拒调用的输入，不能指望靠它脱敏；v2.1.328 起 `UserPromptSubmit` hook 不再收到代码输出，脱敏要统一放在 `PostToolUse`。

````json title="PostToolUse 收到的 tool_response（code-mode.md）"
```json theme={null}
{
  "code": "(await $`cat notes.txt`.text())",
  "result": "deploy token: abc123",
  "stdout": "",
  "stderr": "",
  "toolsUsed": ["Bash"]
}
````

````json title="PostToolUse hook 返回的 updatedToolOutput（code-mode.md）"
```json theme={null}
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "updatedToolOutput": {
      "code": "(await $`cat notes.txt`.text())",
      "result": "deploy token: REDACTED",
      "stdout": "",
      "stderr": "",
      "toolsUsed": ["Bash"]
    }
  }
}
```
````

### 思想来源：从“MCP 即代码”到 Programmatic Tool Calling

Code Mode 不是凭空出现的。2025-11-04 的《Code execution with MCP》指出两个问题：工具定义占满上下文，中间结果在工具之间反复穿过模型。解法是把 MCP server 呈现为文件系统上的代码 API（每个工具一个 `.ts` 文件），模型按需读定义、写代码编排、只打印需要的结果，示例任务从 150,000 tokens 降到 2,000。文章明确提到 Cloudflare 把同一思路称为 “Code Mode”，也列出了代价：要有安全的执行环境、资源限制和监控。

2025-11-24 这个思路被做进 API：**Tool Search Tool**（工具标 `defer_loading: true`，按需搜索加载，token 减少 85%，Opus 4.5 的 MCP 评测从 79.5% 升到 88.1%）和 **Programmatic Tool Calling**（PTC）。PTC 里 Claude 在 API 侧的代码执行沙箱写 Python，脚本调用到工具时暂停，开发者收到带 `caller` 字段的 `tool_use`（右侧），结果回到脚本而不是模型上下文。复杂研究任务的平均 token 从 43,588 降到 27,297。

````json title="anthropic.com/engineering/advanced-tool-use · 由代码发起的工具调用带 caller 字段"
```
{
  "type": "tool_use",
  "id": "toolu_xyz",
  "name": "get_expenses",
  "input": {"user_id": "emp_123", "quarter": "Q3"},
  "caller": {
    "type": "code_execution_20250825",
    "tool_id": "srvtoolu_abc"
  }
}
```
````

|  | 直接工具调用 | Programmatic Tool Calling（API） | Code Mode（Claude Code） |
| --- | --- | --- | --- |
| 谁编排多个调用 | 模型，每次调用一次推理 | 模型写的 Python 脚本 | 模型写的 JavaScript 脚本 |
| 代码在哪运行 | — | API 侧的 code execution 沙箱 | 本机 Claude Code 会话内 |
| 工具结果去哪 | 全部进上下文 | 回到脚本，只有 stdout 进上下文 | 回到脚本，只有最后表达式进上下文 |
| 谁执行工具 | harness | 开发者（收到带 `caller` 的 tool_use） | Claude Code，走原有权限、hook、沙箱链路 |
| 审批粒度 | 每次调用 | 由开发者的工具实现决定 | 规则与 hook 逐调用；auto mode 分类器按整段代码 |

### 使用门槛与限制

要求 v2.1.323 以上，用 Claude 订阅或 Console 账号**直连** Anthropic API（网关、Bedrock、Vertex、Foundry 等都不行），组织未开 HIPAA 或零数据保留，模型为 Opus 5.5、Sonnet 5.5、Haiku 5.5 或 Fable 5.2，只能在交互终端里用（`-p`、Agent SDK、VS Code、桌面应用、云会话都不支持）。开关是 `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1`，项目 settings 里的这个变量会被忽略，避免克隆来的仓库替你打开；managed settings 可以设 `0` 全组织禁用，但不能强制打开。Code Mode 会话只能在开着 Code Mode 的终端里恢复，且不能使用 Remote Control、Claude in Chrome 和 computer use。

## 扩展层：按“上下文成本”和“确定性”给每种扩展定位

官方把扩展分成三类：**常驻上下文**（CLAUDE.md、`.claude/rules/`、output style）、**按需能力**（skills、MCP、LSP、子代理、workflow、跨会话消息）和**事件自动化**（hooks），plugins 和 marketplace 是打包分发层。选哪种，看两件事：它每次请求要占多少上下文，以及它是不是必须每次都发生。

| 扩展 | 何时加载 | 加载什么 | 上下文成本 |
| --- | --- | --- | --- |
| CLAUDE.md | 会话开始 | 每个文件全文 | 每次请求 |
| Output style | 会话开始、切换时 | 当前样式的完整指令 | 每次请求 |
| Skills | 会话开始 + 使用时 | 开始只加载描述，使用时加载正文 | 低（描述每次请求） |
| MCP | 会话开始 | 工具名；schema 按需 | 工具未使用前很低 |
| Code intelligence | 编辑后与查询时 | 诊断、符号位置 | 低，且减少读文件 |
| Subagents | 启动时 | 全新上下文（fork 则复制父对话） | 与主会话隔离 |
| Hooks | 事件触发 | 不加载 | 零，除非返回附加上下文 |

### 指令文件：叠加、懒加载，并且要短

CLAUDE.md 从当前目录向上逐级加载并**拼接**（根目录在前，越靠近工作目录越靠后），子目录里的 CLAUDE.md 等到 Claude 读写该目录文件时才加载；块级 HTML 注释在注入前会被剥掉，可以给人留备注而不花 token。官方建议控制在 200 行以内，因为“更长的指令文件会降低遵循度”。`.claude/rules/` 下的规则可以用 `paths` 限定作用范围（右侧示例），只有处理匹配文件时才加载。v2.1.277 起可以直接读 AGENTS.md：仓库只有 AGENTS.md 时读它，同时有 CLAUDE.md 时默认只读 CLAUDE.md。

```md title=".claude/rules/*.md · 只在处理匹配文件时加载的规则（memory.md）"
---
paths:
  - "src/api/**/*.ts"
---

# API Development Rules

- All API endpoints must include input validation
- Use the standard error response format
- Include OpenAPI documentation comments
```

### Skills：三级渐进披露

Skill 是一个带 `SKILL.md` 的目录。第一级，启动时只把每个 skill 的名字和描述放进上下文；第二级，Claude 判断相关时读入整个 `SKILL.md`；第三级，`SKILL.md` 引用的附加文件和脚本按需读取或直接执行，脚本本身不必进上下文。因为有文件系统和代码执行，一个 skill 能打包的内容“实际上没有上限”。右侧是文档里的最小示例：`description` 决定 Claude 何时自动加载它；`` !`git diff HEAD` `` 这行是动态上下文注入，Claude Code 先执行命令、用输出替换这一行，Claude 拿到的是已经带着 diff 的指令。

有副作用的 skill 建议设 `disable-model-invocation: true`，这样连描述都不进上下文，只能由用户用 `/名字` 调用。skill 可以通过 `context: fork` 在隔离上下文中运行，子代理也可以用 `skills:` 字段预载 skill 全文。Agent Skills 已在 2025-12-18 作为开放标准发布。

```md title="~/.claude/skills/summarize-changes/SKILL.md（skills.md 示例）"
---
description: Summarizes uncommitted changes and flags anything risky. Use when the user asks what changed, wants a commit message, or asks to review their diff.
---

## Current changes

!`git diff HEAD`

## Instructions

Summarize the changes above in two or three bullet points, then list any risks you notice such as missing error handling, hardcoded values, or tests that need updating. If the diff is empty, say there are no uncommitted changes.
```

### MCP：默认只放工具名，定义按需搜索

MCP 工具定义默认 deferred：会话开始只加载工具名和 server instructions，完整 schema 在 Claude 需要时通过 tool search（`ToolSearch` 工具）加载，因此接入更多 server 对上下文影响很小，也没有固定的单 server 工具上限。server instructions 和常驻工具描述的截断上限是 4,096 字符，经搜索加载的工具描述是 16,384 字符，所以关键说明要写在前面。

### Hooks：把“必须发生”的事从提示词里拿出来

Hooks 是 harness 生命周期上的确定性扩展点。事件覆盖会话（SessionStart、SessionEnd）、提示（UserPromptSubmit）、工具（PreToolUse、PostToolUse、PostToolBatch）、权限（PermissionRequest、PermissionDenied）、子代理与任务、Stop、压缩前后、模型切换、worktree、文件变化、MCP elicitation 等三十多个。处理器有五种：**command**（脚本，stdin 收 JSON）、**http**、**mcp_tool**、**prompt**（单轮模型判定）、**agent**（带工具的子代理验证，最多 50 轮）。退出码 2 表示阻断，stderr 作为原因；同一事件的匹配 hook 并行执行。

官方给出的原则是：“Put guardrails in hooks.” 写在 CLAUDE.md 或 skill 里的“不要改 .env”只是请求，`PreToolUse` hook 拦下编辑才是强制。右侧是文档里的配置示例：对所有 memory server 的调用记日志，对任何 MCP server 的写操作跑校验脚本。不同来源的 hooks 是合并后全部触发的，而 skills、子代理、MCP server 则按名字覆盖。

```json title="settings.json · hooks 配置示例（hooks.md）"
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__memory__.*",
        "hooks": [
          {
            "type": "command",
            "command": "echo 'Memory operation initiated' >> ~/mcp-operations.log"
          }
        ]
      },
      {
        "matcher": "mcp__.*__write.*",
        "hooks": [
          {
            "type": "command",
            "command": "/home/user/scripts/validate-mcp-write.py"
          }
        ]
      }
    ]
  }
}
```

更深一层是 **mods**：它也是插件的一种，但由 JavaScript/TypeScript 事件处理器组成，运行在 Claude Code 进程内部，可以观察、修改或接管工具调用、提交的 prompt 和界面绘制；与 settings hooks 不同，它的文件、网络和进程访问不受沙箱约束。Output style 则只管“怎么回答”（角色、语气、格式），与 CLAUDE.md 管“知道什么”分开，同一时间只有一个生效。

## 多代理四档：区别在于谁持有计划

Claude Code 提供四种并行方式，官方用“谁决定下一步做什么、中间结果放在哪”来区分。**Subagent** 是 Claude 派出的工人，有自己的上下文窗口，只把摘要交回；**fork** 是带着完整父对话的子代理；**agent teams** 是一个 lead 会话监督多个对等的 Claude Code 实例；**dynamic workflow** 是 Claude 写一段脚本，由运行时执行，计划和中间结果都在代码里。

|  | Subagents | Agent teams | Dynamic workflows |
| --- | --- | --- | --- |
| 谁决定下一步 | Claude，逐轮决定 | lead agent，逐轮决定 | 脚本 |
| 中间结果在哪 | Claude 的上下文窗口 | 共享任务列表 | 脚本变量 |
| 可复用的是什么 | 工人定义 | 团队定义 | 编排本身 |
| 规模 | 每轮几个委派任务 | 少量长期运行的同伴 | 每次运行几十到几百个 agent |
| 被打断时 | 重启当前轮 | teammate 继续运行 | 同一会话内可恢复 |

### Subagent 与 fork：隔离上下文，限制深度和并发

内置子代理有 Explore（只读，用于搜索代码库，调用时指定 quick、medium 或 very thorough）、Plan（plan mode 下做研究）和 general-purpose。Explore 和 Plan 启动时不加载 CLAUDE.md 和 git status，以求快和省。普通子代理的初始上下文是：自己的 system prompt 加环境信息（不是 Claude Code 的 system prompt）、委派消息、全层级 CLAUDE.md、git status 快照、预载的 skills，以及可以 `SendMessage` 的同伴名单；不继承 output style 和 auto memory。子代理默认在后台运行，它的权限提示会冒泡到主会话。

限额：子代理默认最多嵌套 3 层，到达深度时扣掉 Agent 工具；同时运行的子代理上限 20 个，超过时返回 “Concurrent subagent limit reached” 并告诉 Claude 不要重试；总数不限。fork 则复用父对话的 system prompt、工具、模型和历史，因此首个请求能直接读父会话的缓存；普通子代理前缀不同，要自己预热缓存。

子代理的报告在交给主对话之前会被扫描：模仿 `<system-reminder>`、`Human:` 或 `Assistant:` 的文本会被插入反斜杠，提到 `bypassPermissions` 这类权限设置时会前置一行 `[harness: subagent output matched instruction-shaped pattern(s): …` 标记，报告整体还带一个头，声明其中的指令和授权声明不代表用户。这对应了隔离一文里的担忧：子代理的输出不能因为“来自我们自己”就被当作更高信任。

```md title="code.claude.com/docs/en/sub-agents.md · Subagent output scanning"
* **Backslash insertion**: the scan inserts a backslash into text that imitates Claude Code's own output, such as a `<system-reminder>` tag or a line starting with `Human:` or `Assistant:`, so the imitation reads as ordinary text instead of being mistaken for part of the conversation.
```

### Agent teams：共享任务列表 + 邮箱

agent teams 仍是实验特性（`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`），只在交互会话里可用。结构是 lead、teammates（各自独立的 Claude Code 实例）、共享任务列表和邮箱：每个 agent 的邮箱是 `~/.claude/teams/{team}/inboxes/{agent}.json`，任务有 pending、in progress、completed 三态和依赖关系，认领任务用文件锁防止抢占。teammate 加载项目的 CLAUDE.md、MCP 和 skills，但不继承 lead 的对话。它的原型可以追溯到 2026-02 那篇用 16 个 Claude 写 C 编译器的文章：每个 agent 一个容器，用 `current_tasks/` 下的文本文件当锁，靠 git 同步。

### Dynamic workflows：把编排写成可重放的脚本

当任务需要的 agent 数量超过一段对话能协调的范围，或者希望结果在交给用户前被交叉验证，Claude 会写一个 workflow 脚本（右侧为文档示例）：`agent()` 启动一个子代理并可要求结构化输出，`pipeline()` 和 `parallel()` 扇出，`phase()` 分组显示。在 prompt 里写 `ultracode` 或直接说“use a workflow”即可触发；`/effort ultracode` 让 Claude 对每个实质性任务都规划 workflow。内置的 `/deep-research` 就是一个 workflow：多角度搜索、交叉核验、对每条结论投票，输出带引用的报告。

运行时的约束都是为了**可恢复**：脚本本身不能访问文件和 shell、不能 `import()`；`Date.now()`、`Math.random()` 会抛错，保证重放时发出同样的 `agent()` 调用。恢复时按 agent 启动顺序重放：已完成的直接返回保存的结果，从第一个 prompt 变化或失败的 agent 开始，其后全部重跑。默认 16 个并发（可调到 256），单次 `parallel()` 或 `pipeline()` 不超过 4,096 项，单次运行不超过 1,000 个 agent。扇出时，前缀相同的 agent 会等第一个开始响应后再放行（默认最多等 5 秒），以共享它刚写入的 prompt cache。

```js title="workflow 脚本示例（code.claude.com/docs/en/workflows.md）"
export const meta = {
  name: 'audit-routes',
  description: 'Audit every route handler for missing auth checks',
}

const found = await agent('List every .ts file under src/routes/.', {
  schema: { type: 'object', required: ['files'], properties: { files: { type: 'array', items: { type: 'string' } } } },
})

const audits = await pipeline(found.files, file =>
  agent(`Audit ${file} for missing authentication checks.`, { label: file }),
)

return audits.filter(Boolean)
```

## 安全：环境隔离兜底，分类器减负

Claude Code 的权限系统由四层叠成：**模式**定基线，**规则**（allow/ask/deny，按工具名和参数匹配）做精细控制，**hooks 和 mods** 做自定义判断，**OS 沙箱**封住 shell 命令能触及的范围。六种模式分别是 `default`（Manual）、`acceptEdits`、`plan`、`auto`、`dontAsk`（CI 用，凡是会提示的都拒绝）和 `bypassPermissions`（只该在隔离的容器或 VM 里用）。deny 规则在所有模式下都生效，包括 bypass。

有些动作任何模式都不会自动批准：显式的 ask 规则、组织设为 ask 的 connector 工具、需要用户交互的工具（AskUserQuestion 和标了 `requiresUserInteraction` 的 MCP 工具）、针对关键路径的 `rm`/`rmdir`、跨会话消息的保护，以及开启相应设置后在工作目录之外的读写。

```svg title="图 3：auto mode 下一次工具调用的判定顺序（根据工程博客与 permission-modes 文档整理）" tone="light"
<svg viewBox="0 0 640 400" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, 'Segoe UI', 'PingFang SC', 'Noto Sans CJK SC', sans-serif" font-size="13">
  <rect x="0" y="0" width="640" height="400" fill="#f7f8fa"/>
  <g fill="#ffffff" stroke="#c9ced6">
    <rect x="20" y="20" width="200" height="40" rx="6"/>
    <rect x="20" y="88" width="200" height="52" rx="6"/>
    <rect x="20" y="168" width="200" height="52" rx="6"/>
  </g>
  <rect x="20" y="248" width="200" height="52" rx="6" fill="#eaf0fd" stroke="#2457d6"/>
  <rect x="20" y="328" width="200" height="52" rx="6" fill="#eaf0fd" stroke="#2457d6"/>
  <g fill="#333">
    <text x="120" y="45" text-anchor="middle">模型发出工具调用</text>
    <text x="32" y="110">1 allow / ask / deny 规则</text><text x="32" y="129" fill="#666" font-size="11">进入 auto 时丢弃宽泛的解释器规则</text>
    <text x="32" y="190">2 只读操作、项目内编辑</text><text x="32" y="209" fill="#666" font-size="11">直接放行，不付分类器延迟</text>
    <text x="32" y="270" fill="#1d2b4f">3 阶段一：单 token 过滤</text><text x="32" y="289" fill="#555" font-size="11">指令要求倾向拦截</text>
    <text x="32" y="350" fill="#1d2b4f">4 阶段二：带推理复核</text><text x="32" y="369" fill="#555" font-size="11">只处理阶段一标记的动作</text>
  </g>
  <g stroke="#999" stroke-width="1.5" fill="none">
    <path d="M120 60 V84"/><path d="M120 140 V164"/><path d="M120 220 V244"/><path d="M120 300 V324"/>
  </g>
  <rect x="300" y="168" width="150" height="52" rx="6" fill="#ffffff" stroke="#c9ced6"/>
  <text x="375" y="190" text-anchor="middle" fill="#333">执行</text><text x="375" y="208" text-anchor="middle" fill="#666" font-size="11">shell 命令进 OS 沙箱</text>
  <rect x="300" y="248" width="150" height="52" rx="6" fill="#ffffff" stroke="#c9ced6"/>
  <text x="375" y="270" text-anchor="middle" fill="#333">注入探针</text><text x="375" y="288" text-anchor="middle" fill="#666" font-size="11">扫描工具结果，可疑则加警告</text>
  <rect x="300" y="328" width="150" height="52" rx="6" fill="#ffffff" stroke="#c9ced6"/>
  <text x="375" y="350" text-anchor="middle" fill="#333">拒绝并继续</text><text x="375" y="368" text-anchor="middle" fill="#666" font-size="11">理由作为工具结果返回</text>
  <g stroke="#2457d6" stroke-width="1.5" fill="none">
    <path d="M220 194 H296"/><path d="M220 274 H260 V200 H296"/><path d="M220 354 H296"/><path d="M375 220 V244"/>
  </g>
  <text x="228" y="188" fill="#2457d6" font-size="11">放行</text>
  <text x="228" y="268" fill="#2457d6" font-size="11">未标记</text>
  <text x="228" y="348" fill="#2457d6" font-size="11">确认危险</text>
  <rect x="480" y="168" width="140" height="212" rx="6" fill="#ffffff" stroke="#c9ced6"/>
  <g fill="#444" font-size="11">
    <text x="492" y="190">分类器只看：</text><text x="492" y="208">· 用户消息</text><text x="492" y="226">· 工具调用本身</text>
    <text x="492" y="252">不看：</text><text x="492" y="270">· assistant 文字</text><text x="492" y="288">· 工具输出</text>
    <text x="492" y="318">连续 3 次或累计</text><text x="492" y="336">20 次拒绝后</text><text x="492" y="354">交还给人；-p 下</text><text x="492" y="372">终止或跳过动作</text>
  </g>
</svg>
```

### OS 沙箱：同时隔离文件和网络

2025-10 发布的沙箱用 OS 原语实现：Linux 用 bubblewrap，macOS 用 Seatbelt，限制覆盖命令派生的所有子进程。文件上允许读写当前工作目录、阻止修改其外的文件；网络上只能经 unix domain socket 连到沙箱外的代理，由代理按域名放行，并对新域名向用户确认（右侧原文）。博客强调两者缺一不可：没有网络隔离，被注入的 agent 可以外传 SSH key；没有文件隔离，它可以改配置逃出沙箱。内部使用中，沙箱让权限提示减少了 84%，运行时已开源。

边界在哪里同样重要：沙箱只包 shell 命令。Read、Edit、Write、WebFetch、WebSearch 走权限规则而非沙箱（`denyRead` 不拦 Read 工具，`allowedDomains` 不限 WebFetch），command hooks、本地 MCP server、LSP、mods 也都在沙箱外；用户自己在 `!` 后输入的命令通常也不进沙箱。Claude 可以用 `dangerouslyDisableSandbox` 请求非沙箱重试，strict 模式（`allowUnsandboxedCommands: false`）可以关掉这条路，由管理员设置时仓库里放宽沙箱的配置会被忽略。

```text title="anthropic.com/engineering/claude-code-sandboxing · 两条边界"
- Filesystem isolation, by allowing read and write access to the current working directory, but blocking the modification of any files outside of it.

- Network isolation, by only allowing internet access through a unix domain socket connected to a proxy server running outside the sandbox. This proxy server enforces restrictions on the domains that a process can connect to, and handles user confirmation for newly requested domains. And if you’d like further-increased security, we also support customizing this proxy to enforce arbitrary rules on outgoing traffic.
```

### Auto mode：两阶段分类器替人审批

auto mode 的动机是一个数字：用户实际会批准 93% 的权限提示，审批疲劳让“逐条确认”名存实亡。它用模型分类器替代人，判定顺序见图 3。第一层是内置的安全工具白名单和用户 allow 规则，但**进入 auto mode 时会丢弃已知能任意执行代码的规则**（全量 shell、通配的 python/node/ruby 解释器、包管理器的 run 命令），否则分类器永远看不到最危险的命令；第二层是项目内的文件读写，直接放行；其余动作进入分类器。分类器分两阶段：阶段一只输出一个 token、并被要求倾向拦截；只有被标记的动作才进入阶段二做带推理的复核。两阶段输入相同、只改最后一句指令，所以阶段二几乎全部命中缓存。

分类器是刻意“reasoning-blind”的：只看用户消息和工具调用本身，去掉 assistant 的文字和工具输出（右侧原文）。去掉 assistant 文字，是为了不让 agent 用“用户之前默许过”这类说辞说服分类器；去掉工具输出，是对 prompt injection 的结构性防御，因为恶意内容正是从工具输出进来的。代价是失去来源信息：用户没提过的任务 ID，分类器分不清是查出来的还是编出来的。工具输出那一侧另有一个服务端注入探针，可疑时给 agent 加警告。

```text title="anthropic.com/engineering/claude-code-auto-mode · 为什么去掉 assistant 文字"
We strip assistant text so the agent can't talk the classifier into making a bad call. The agent could generate persuasive rationalizations, such as "this is safe because the user implicitly approved it earlier," or "this target is definitely agent-owned." If the classifier reads those, it can be talked into the wrong decision. Instead, we want it to judge what the agent did, not what the agent said.
```

```text title="anthropic.com/engineering/claude-code-auto-mode · Deny-and-continue"
When the transcript classifier flags an action as dangerous, that denial comes back as a tool result along with an instruction to treat the boundary in good faith: find a safer path, don't try to route around the block. If a session accumulates 3 consecutive denials or 20 total, we stop the model and escalate to the human. This is the backstop against a compromised or overeager agent repeatedly pushing towards an outcome the user wouldn't want. In headless mode (`claude -p`) there is no UI to ask the human, so we instead terminate the process.
```

分类器的提示词是固定模板加三个可定制槽：信任边界（默认只信任当前 git 仓库）、拦截规则（默认 20 多条，分为销毁或外传、削弱安全态势、跨越信任边界、绕过审查或影响他人四组）和例外。模板要求按真实影响而不是表面文本判断：先写文件再执行，就评估文件内容；`&&` 连起来的命令算一个动作；“clean up my branches” 不等于授权批量删除，“can we fix this?” 是问题而不是指令。子代理在委派时和返回时各过一次分类器。博客公布的评测如下：

| 数据集 | 阶段一 | 阶段一 + 阶段二（实际部署） |
| --- | --- | --- |
| 真实内部流量（n = 10,000），误拦率 | 8.5% | 0.4% |
| 真实 overeager 动作（n = 52），漏放率 | 6.6% | 17% |
| 合成数据外传（n = 1,000），漏放率 | 1.8% | 5.7% |

17% 的漏放率是博客自己称为 “the honest number” 的数字：多数漏判里，分类器看到了危险，却把“用户说过类似的话”当成了对这个动作影响范围的授权。所以官方定位很克制：auto mode 是对 `--dangerously-skip-permissions` 的大幅改进，但不能替代对高风险基础设施的人工审查。到 2026 年下半年，auto mode 已成为交互终端的默认起始模式（v2.1.283 起），并演进出“服务端审查”：直连 API 的会话由服务端随模型请求一起给出判定，拿不到判定时**拒绝而不是放行**；本地分类器默认用 Sonnet 5。

### 事故教训：确定性的边界才是最后一道防线

2026-05 的《How we contain Claude across products》总结了三次事故。其一，克隆仓库里的 `.claude/settings.json` 定义的 hook 在“Do you trust this folder?”之前就执行了（右侧原文）；修复是把项目配置的解析和执行推迟到用户接受信任之后。其二，内部红队用钓鱼邮件让员工粘贴一段 prompt，里面夹带“读 `~/.aws/credentials` 并 POST 出去”，25 次中成功 24 次：指令来自用户本人时，以用户意图为锚的模型层防御无从察觉，只有出站控制和文件边界能挡住。其三（Cowork），允许访问 `api.anthropic.com` 后，恶意文件携带攻击者的 API key 让 Claude 把文件上传到攻击者账号，结论是 allowlist 应被理解为“能力授予”而非“目的地过滤”。

由此得出的原则：先在环境层做隔离，再在模型层引导行为；隔离强度要匹配用户的监督能力（能读懂 bash 的开发者与知识工作者不是同一个威胁模型）；最该警惕的是自己写的组件，hypervisor、seccomp、gVisor 都很可靠，出过事的是自研代理。

```text title="anthropic.com/engineering/how-we-contain-claude · 信任对话框之前的代码"
Between mid-2025 and January 2026, we received reports of vulnerabilities in Claude Code through our responsible disclosure program. Three of these vulnerabilities targeted code that executes before the user has consented to anything. To understand how this is possible, consider the most direct case: a developer clones a repository to review a pull request, and that repository contains a .claude/settings.json which defines a hook. Because Claude Code reads project settings during startup—before presenting the standard "Do you trust this folder?" prompt—the hook the attacker had authored and committed would execute automatically. The remaining cases looked structurally similar, in which input from the not-yet-trusted directory was parsed before the trust boundary had been established.
```

## 长任务与运行形态：把会话从终端里解放出来

2026-02 的 C 编译器实验点出了交互式 harness 的天花板：“Existing agent scaffolds like Claude Code require an operator to be online”。作者的解法是右侧这个无限循环脚本：Claude 完成一个任务后，循环立刻起一个新会话接着干。16 个 agent、近 2,000 个会话、约 2 万美元，产出一个能编译 Linux 6.9 的 10 万行 Rust 编译器。此后 Claude Code 逐步把这类外部脚本的能力收进产品。

**/goal** 与 Stop hook 同构：每轮结束后由一个独立模型检查完成条件，未满足就开新的一轮，而不是把控制权还给用户；评估器只看对话里 Claude 已经展示的内容，不自己跑命令，所以条件要写成“能被输出证明”的形式（例如“`npm test` 退出码为 0”），最长 4,000 字符。它与 auto mode 互补：auto mode 只在一轮之内放行工具调用，不会开新的一轮。**/loop** 和会话内 Cron 工具按时间间隔重跑 prompt，`ScheduleWakeup` 让 Claude 自己决定下次何时醒来。**Channels** 是反方向：MCP server 把外部事件（如 Telegram、Discord 消息）主动推进正在运行的会话。

```bash title="anthropic.com/engineering/building-c-compiler · 让 Claude 不停工作的外层循环"
#!/bin/bash

while true; do
    COMMIT=$(git rev-parse --short=6 HEAD)
    LOGFILE="agent_logs/agent_${COMMIT}.log"

    claude --dangerously-skip-permissions \
           -p "$(cat AGENT_PROMPT.md)" \
           --model claude-opus-X-Y &> "$LOGFILE"
done
```

### 会话不再依附于终端

后台会话（`claude agents` 打开 agent view）由独立的 supervisor 进程托管，关掉终端也照常运行；状态落盘，能挺过自动更新、supervisor 重启和睡眠唤醒，修改代码前会被要求先建 worktree。**Remote Control** 让手机和网页成为本机会话的窗口，执行和文件始终留在本机。**云会话**运行在 Anthropic 管理的 VM 或组织自托管环境里：GitHub 凭据加密保存在服务端、从不进入 VM，git 操作经代理，由代理校验后附加凭据（例如只允许 push 到配置的分支）；network secrets 同样在沙箱外附加到匹配的请求上。**云端定时任务**（原 routines）把 prompt、仓库和 connectors 存成配置，由时间表、API 调用或 GitHub 事件触发。会话之间还可以用 cross-session messaging 互发消息。

### Managed Agents：把 harness 本身做成可替换的

2026-04 的《Scaling Managed Agents》把视角再上提一层。早期把 session、harness 和沙箱放在同一个容器里，容器就成了不能丢的“宠物”。拆开后，agent 被抽象成三个接口：只追加的 **session** 事件日志、调用模型并路由工具的 **harness**、执行代码的**沙箱**。容器只是一个工具（右侧原文）：挂了就是一次工具调用错误，交给 Claude 决定是否重建；harness 挂了，用 `wake(sessionId)` 和 `getSession(id)` 从最后一个事件恢复。凭据永不进沙箱：git token 在初始化时写进 remote，MCP 的 OAuth token 存在 vault 里由代理使用。容器按需创建后，p50 首 token 时间下降约 60%，p95 下降超过 90%。

这篇文章开头的一句话可以作为整份报告的注脚：harness 编码了“Claude 自己做不到什么”的假设，而这些假设会随着模型变强而过时。例子是 Sonnet 4.5 临近上下文上限时会提前收尾（“context anxiety”），于是 harness 加了 context reset；换到 Opus 4.5 后这个行为消失，reset 成了累赘。长时运行 harness 的两篇文章也走过同样的路：从 initializer + coding agent，到 planner、generator、evaluator 三代理加 sprint contract，再在 Opus 4.6 上逐个拆掉组件、确认哪些仍然承重。新模型默认不给 TodoWrite、Glob/Grep 并入 Bash，都是同一逻辑的产物。

```text title="anthropic.com/engineering/managed-agents · brain 与 hands 解耦"
The harness leaves the container. Decoupling the brain from the hands meant the harness no longer lived inside the container. It called the container the way it called any other tool: `execute(name, input) → string`. The container became cattle. If the container died, the harness caught the failure as a tool-call error and passed it back to Claude. If Claude decided to retry, a new container could be reinitialized with a standard recipe: `provision({resources})`. We no longer had to nurse failed containers back to health.
```

## 设计取舍总览：每个选择都在付一种代价

把前面的设计放在一起看，可以归纳出下表。左列是选择，中间是官方给出的理由，右列是文档或博客里承认的代价。

| 设计选择 | 理由 | 代价或局限 |
| --- | --- | --- |
| 每轮重发完整上下文 + 三层前缀缓存 | 无状态、实现简单，缓存命中时成本近似线性 | 切模型、改工具集、压缩都会让缓存重建；功能设计受“别破坏前缀”约束 |
| CLAUDE.md 预加载，其余 just-in-time 检索 | 避免预建索引过期，按需取用 | 探索比读预计算结果慢；CLAUDE.md 越长遵循度越低 |
| 自动压缩 + 定向重注入 | 让会话越过上下文上限继续 | 早期细节可能丢失；带 `paths` 的规则会被摘要掉 |
| 精简工具面（Glob/Grep 并入 Bash，新模型去掉 TodoWrite） | 少一次往返，少占上下文 | 更多操作以 `Bash` 身份出现，规则和 hook 要按命令文本写 |
| Code Mode | 多步工具调用一次推理完成，中间结果不进上下文 | auto mode 看不到运行时值；JS 在沙箱外；仅限交互终端和直连 API |
| Hooks 作为强制手段 | 确定执行、零上下文成本 | hook 本身是代码执行面，需要信任边界（事故一） |
| 四档多代理 | 按任务规模选择隔离和编排方式 | token 消耗成倍增加；子代理输出需要防注入扫描 |
| OS 沙箱只包 shell 命令 | 开销低，不用搬到容器，提示减少 84% | 文件/Web 工具、hooks、MCP、mods 在沙箱外，统一边界需容器或 VM |
| Auto mode 两阶段、reasoning-blind 分类器 | 替代审批疲劳下的人工确认，误拦率 0.4% | 对真实 overeager 动作漏放 17%；看不到来源信息 |
| 会话与执行解耦（后台、远程、云、Managed Agents） | 会话不依附终端，执行环境可替换 | 每个新运行形态都有各自的限制，例如 Code Mode 不能转入后台会话 |

几处值得留意的局限。第一，Code Mode 和 agent teams 仍是实验特性，文档明确说可能改变或移除，上面的版本门槛和限额会快速变化。第二，权限体系的复杂度本身就是风险：同一条“禁止”在 CLAUDE.md、规则、hook、沙箱里含义不同，文档专门提醒 Bash 规则按命令文本匹配，换一种写法就可能绕开。第三，Anthropic 自己的展望点名了两个未解决的问题：跨会话保存的上下文（CLAUDE.md、记忆、定时任务的状态目录）会成为持久投毒的载体；多代理系统里，给子代理输出更高信任会打开新的注入路径。

> 如果只记住一件事：Claude Code 的大部分设计都在回答“哪些事情不该交给模型临场判断”。上下文预算交给缓存和按需加载，必须发生的事交给 hooks 和脚本，安全边界交给操作系统和代理，模型负责剩下真正需要判断的部分。
