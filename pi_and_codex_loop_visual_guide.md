---
title: Pi、Prime Agent 与 Codex CLI 的定时循环：三种把消息送回对话的方法
kicker: 源码拆解
deck: 对照 Claude Code 的 /loop，拆解 pi-loop 插件、Prime Agent 心跳和 Codex CLI 的 cronex，看它们怎样选择插入时机、防止消息堆积、让会话保持存活。
meta:
  - pi-loop · Prime Agent · cronex
  - 源码与提示词原文
footer: 源码出处：`davidroman0O/pi-loop` v0.4.0、`PrimeIntellect-ai/prime-agent`（`pa-core` / `pa-daemon`）、`stbenjam/cronex`、`stbenjam/skills`。
index: index.html
updated: 2026-11-17
created: 2026-11-17
---

上一篇拆解了 Claude Code 的 `/loop`。这一篇看另外三种实现：它们都要解决同一个问题——**定时任务到期时，怎么把一条消息送进模型正在进行（或刚刚结束）的对话**——但给出的答案很不一样。

文中的源码和提示词都是原文，来自各项目的仓库。三个样本：

- `pi-loop`：Pi Coding Agent 的 TypeScript 插件，跑在 Pi 进程内部
- `rlm_heartbeat`：Prime Agent 内核自带的心跳，Rust 守护进程负责调度
- `cronex`：给 OpenAI Codex CLI 补上的定时能力，用 MCP 加生命周期钩子实现

## 先看结论：定时消息只有两个插入点

模型的一轮工作，是收到一条消息后连续调用若干工具，最后回复完毕。定时消息能插进去的位置只有两处：

- **插入点 A：两次工具调用之间**。模型还在干活，消息插进当前这一轮，马上就能看到。及时，但会改变模型正在做的事。
- **插入点 B：这一轮结束、空闲之后**。作为新的一轮发给模型，不打断当前工作，但要等任务做完。

Claude Code 只用 B。另外三种实现都同时支持 A 和 B，区别在于默认用哪个、靠什么机制插进去、怎么防止消息堆积。

```svg title="图 1：一轮工作里，定时消息可以插入的两个位置" tone="light"
<svg viewBox="0 0 680 400" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif" font-size="12.5">
  <defs>
    <marker id="ins-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 1 L10 5 L0 9 z" fill="#64748b"/>
    </marker>
  </defs>

  <text x="20" y="26" fill="#111827" font-size="14" font-weight="700">模型的一轮工作（Turn）</text>

  <!-- timeline -->
  <line x1="20" y1="78" x2="660" y2="78" stroke="#cbd5e1" stroke-width="2"/>
  <rect x="20" y="58" width="96" height="40" rx="8" fill="#dbeafe"/>
  <text x="68" y="83" text-anchor="middle" fill="#1e40af" font-weight="600">收到消息</text>
  <rect x="132" y="58" width="96" height="40" rx="8" fill="#e5e7eb"/>
  <text x="180" y="83" text-anchor="middle" fill="#111827">工具调用 1</text>
  <rect x="276" y="58" width="96" height="40" rx="8" fill="#e5e7eb"/>
  <text x="324" y="83" text-anchor="middle" fill="#111827">工具调用 2</text>
  <rect x="388" y="58" width="96" height="40" rx="8" fill="#e5e7eb"/>
  <text x="436" y="83" text-anchor="middle" fill="#111827">回复完毕</text>
  <rect x="544" y="58" width="116" height="40" rx="8" fill="#f8fafc" stroke="#cbd5e1" stroke-dasharray="4 3"/>
  <text x="602" y="83" text-anchor="middle" fill="#64748b">空闲等待</text>

  <!-- insertion point A -->
  <line x1="252" y1="44" x2="252" y2="118" stroke="#d97706" stroke-width="2.5"/>
  <circle cx="252" cy="44" r="11" fill="#d97706"/>
  <text x="252" y="48" text-anchor="middle" fill="#fff" font-weight="700">A</text>
  <!-- insertion point B -->
  <line x1="514" y1="44" x2="514" y2="118" stroke="#2457d6" stroke-width="2.5"/>
  <circle cx="514" cy="44" r="11" fill="#2457d6"/>
  <text x="514" y="48" text-anchor="middle" fill="#fff" font-weight="700">B</text>

  <path d="M252 118 V140" stroke="#d97706" stroke-width="1.5" marker-end="url(#ins-arrow)"/>
  <path d="M514 118 V140" stroke="#2457d6" stroke-width="1.5" marker-end="url(#ins-arrow)"/>

  <!-- A box -->
  <rect x="20" y="144" width="316" height="196" rx="12" fill="#fffbeb" stroke="#d97706" stroke-width="1.5"/>
  <text x="36" y="170" fill="#92400e" font-size="14" font-weight="700">插入点 A：两次工具调用之间</text>
  <text x="36" y="192" fill="#78350f">模型还在干活，消息插进这一轮，马上就能看到</text>
  <text x="36" y="224" fill="#111827" font-weight="600">pi-loop</text>
  <text x="36" y="242" fill="#475569">带 ! 的循环：steer 插队，每轮最多一次</text>
  <text x="36" y="268" fill="#111827" font-weight="600">Prime Agent</text>
  <text x="36" y="286" fill="#475569">心跳默认走 Steer 通道</text>
  <text x="36" y="312" fill="#111827" font-weight="600">cronex（Codex CLI）</text>
  <text x="36" y="330" fill="#475569">PostToolUse 钩子把到期任务附在工具结果旁</text>

  <!-- B box -->
  <rect x="344" y="144" width="316" height="196" rx="12" fill="#eff6ff" stroke="#2457d6" stroke-width="1.5"/>
  <text x="360" y="170" fill="#1e3a8a" font-size="14" font-weight="700">插入点 B：这一轮结束、空闲之后</text>
  <text x="360" y="192" fill="#1e40af">不打断当前工作，作为新的一轮发给模型</text>
  <text x="360" y="224" fill="#111827" font-weight="600">Claude Code</text>
  <text x="360" y="242" fill="#475569">唯一的方式：排在你的消息之后</text>
  <text x="360" y="268" fill="#111827" font-weight="600">pi-loop / Prime Agent</text>
  <text x="360" y="286" fill="#475569">默认等 agent_settled / FollowUp 通道</text>
  <text x="360" y="312" fill="#111827" font-weight="600">cronex（Codex CLI）</text>
  <text x="360" y="330" fill="#475569">Stop 钩子的后台进程执行 codex queue</text>

  <text x="20" y="372" fill="#64748b" font-size="12">A 更及时，但会改变模型正在做的事；B 更稳，但要等当前任务做完。</text>
  <text x="20" y="390" fill="#64748b" font-size="12">三种实现的大部分差异，都可以归结为“默认用哪个插入点、怎么防止消息堆积”。</text>
</svg>
```

### 三种实现一览

|  | pi-loop | Prime Agent 心跳 | cronex（Codex CLI） |
| --- | --- | --- | --- |
| 调度器在哪 | 插件内部，和 Pi 同一个进程 | 独立的守护进程 `pa-daemon` | 外部 Go 程序，通过钩子被 Codex 调用 |
| 谁来创建循环 | 你输入 `/loop`，或模型调用 `schedule_loop` 工具 | 模型在 Python REPL 里调用 `rlm_heartbeat.create()` | 模型调用 MCP 工具 `CronCreate` |
| 默认插入点 | B：等模型空闲 | A：Steer 插入当前回合 | 两条都用：忙时 A，闲时 B |
| 防止消息堆积 | 待发区按循环 ID 只留最新一条 | 队列里同一个心跳只留一条 | 租约防重复，漏掉的周期合并成一次 |

### 名词表

| 名词 | 是什么 |
| --- | --- |
| Turn（一轮） | 模型收到一条消息，到它回复完毕的一个来回，中间可能调用多次工具。 |
| Steer（插队） | 把消息插进模型正在进行的这一轮，在下一个工具调用边界生效。对应插入点 A。 |
| FollowUp / agent_settled | 等模型把当前任务彻底做完、空闲下来再发。`agent_settled` 是 Pi 在这个时刻发出的事件。对应插入点 B。 |
| Hook（钩子） | 宿主程序在特定时刻调用的外部命令。Codex 有 7 个：会话开始、子 Agent 开始、提交消息、工具调用后、回合结束、中断、会话结束。 |
| PostToolUse / Stop | Codex 的两个钩子：每次工具调用结束后运行 / 每个回合结束时运行。cronex 靠它们送消息。 |
| `additionalContext` | PostToolUse 钩子可以返回的一段文字，Codex 会把它附在这次工具结果旁边给模型看。 |
| `codex queue` | Codex CLI 的命令，从外部往指定会话（`--thread`）排入一条消息。 |
| MCP | Model Context Protocol，让外部程序以“工具”的形式提供给模型调用。 |
| `_meta.threadId` | Codex 每次调用 MCP 工具时附带的会话 ID，cronex 用它判断任务属于哪个会话。 |
| 租约（lease） | 取出任务时加一把 30 秒的锁，期间别的进程不能再取同一个任务，防止重复投递。 |
| 相位（phase） | 周期任务的“节拍”。每 10 分钟一次、从 00:00 开始，相位就是 00:00、00:10、00:20…… |
| PTC | Programmatic Tool Calling。Prime Agent 只给模型一个 `python_repl` 工具，其他能力都是 REPL 里的 Python 函数。 |
| `convert_to_llm()` | Prime Agent 在发给模型前，把内部消息列表转换成模型能看到的消息，并过滤掉不该发的。 |
| `maxFires` | pi-loop 的参数：最多触发多少次，到了自动删除循环。 |

## pi-loop：插件怎么教模型建循环

Pi 社区早期有三个定时插件，分别因为过度设计、界面浮层吞掉回车键、以及每次触发都重读磁盘 JSON 覆盖了内存里的调度状态（导致循环永久卡死）而被放弃。`davidroman0O/pi-loop`（v0.4.0）用不到 900 行 TypeScript 解决了这三个问题。

除了你在终端输入 `/loop 5m check the build`，插件还给模型注册了三个工具：`schedule_loop`、`list_loops`、`stop_loop`。工具描述里的使用准则很具体：只为用户明确要求的事建循环；同一个目标只建一个循环；轮询必须设 `maxFires`；不需要模型动手时用 `notify` 或 `message`，不要用 `prompt`；只有紧急的触发才设 `force`。

另外，只要存在活跃的循环，插件就在每轮开始前往系统提示词末尾追加一段 `<loop_policy>`；没有循环时什么都不加，不占 token。

> `<loop_policy>` 的 R1–R4 写得很坦白：每条规则都注明是由代码强制执行（`enforced: … (code)`），还是只靠提示词约束（`enforced: none (prose)`），并写明违反后怎么补救。

```ts title="davidroman0O/pi-loop · extensions/loop.ts (Lines 691-715 &amp; 450-467)" raw
<span class="cmt">// 1. 注册给大模型的 schedule_loop 工具描述与 Prompt Guidelines（原版逐字提取）</span>
pi.<span class="fn">registerTool</span>({
  name: <span class="str">"schedule_loop"</span>,
  label: <span class="str">"Schedule Loop"</span>,
  description:
    <span class="str">"Schedule a recurring or one-shot loop in this Pi session that can wake the agent with a prompt, run a shell command, post a message, or notify the user. Scope 'session' (default) fires only in this session; scope 'global' arms in every pi on this project path and survives restarts. Shows in the status bar."</span>,
  promptSnippet: <span class="str">"Schedule a recurring or one-shot loop (prompt/notify/shell/message) on a timer or cron"</span>,
  promptGuidelines: [
    <span class="str">"Use schedule_loop for periodic or deferred work — 'check CI every 5 min', 'remind me at 9am', 'poll until the build passes', 'run npm test hourly'."</span>,
    <span class="str">"Do NOT create schedule_loop calls to test, experiment with, or demonstrate the tool — only create loops for real work the user explicitly asked for. Avoid intervals shorter than ~30s; they fire rapidly and create noise."</span>,
    <span class="str">"Do NOT create multiple schedule_loop calls with the same interval for one goal — they fire in lockstep and flood the session. Instead create ONE interval loop with maxFires."</span>,
    <span class="str">"Do NOT use action='prompt' when no agent action is needed — use 'notify' (a silent reminder) or 'message' (a transcript log line). Use 'shell'+followUpPrompt to run a command on a schedule without waking the agent to run it."</span>,
    <span class="str">"Always set schedule_loop maxFires for polling so it stops itself; a loop without maxFires runs forever. Call stop_loop to remove a loop once its purpose is done."</span>,
    <span class="str">"Set schedule_loop force=true ONLY for time-sensitive triggers that must interrupt a running task; otherwise leave it unset — the default defers until the agent finishes, avoiding disruption."</span>,
    <span class="str">"Name loops you create (schedule_loop name) and call list_loops before creating, to avoid duplicates and so stop_loop can target them."</span>,
  ],
  <span class="cmt">// ...</span>
});

<span class="cmt">// 2. 当存在活跃 Loop 时，在每轮开始前通过 before_agent_start 动态追加到 System Prompt 的 &lt;loop_policy&gt;</span>
<span class="cmt">// 注意：当 store.list().length === 0 时直接 return，消耗 0 Token！</span>
pi.<span class="fn">on</span>(<span class="str">"before_agent_start"</span>, <span class="kw">async</span> (event, ctx) =&gt; {
  <span class="kw">if</span> (store.<span class="fn">list</span>().length === <span class="num">0</span>) <span class="kw">return</span>; <span class="cmt">// nothing to govern</span>
  <span class="kw">const</span> policy = <span class="str">`
&lt;loop_policy authority="schedule_loop / stop_loop / list_loops usage"&gt;
  &lt;bannedBehaviors&gt;
  - Do NOT create multiple loops with the same interval for one goal. Use ONE interval loop with maxFires.
  - Do NOT create a polling loop without maxFires — it runs forever.
  - Do NOT use action='prompt' when notify or message would suffice.
  &lt;/bannedBehaviors&gt;
  &lt;rules&gt;
  R1 lockstep: invariant ≤ one loop per periodic goal | enforced: concurrent fires coalesce (code) | recovery: stop_loop the duplicates, keep one with maxFires.
  R2 bounded: invariant every polling loop ends | enforced: maxFires auto-removes the loop (code) | recovery: set maxFires; stop_loop orphans.
  R3 action-fit: invariant do not wake the agent needlessly | enforced: none (prose) | recovery: switch prompt → notify (reminder) or message (log).
  R4 force: invariant force=true only for time-sensitive triggers | enforced: none (prose) | recovery: leave force unset.
  &lt;/rules&gt;
&lt;/loop_policy&gt;`</span>;
  <span class="kw">return</span> { systemPrompt: <span class="str">`${event.systemPrompt}\n\n${policy}`</span> };
});
```

## pi-loop：到期后消息怎么送到模型面前

定时器到期调用 `fire()` 后，依次经过四步：

1. **先挂好下一次定时器**，再执行这次的动作。这是它和早期插件最大的区别：就算动作报错或卡死，循环也不会断。
2. **用 `<scheduled_trigger>` 包住 prompt**。消息最终以 user 身份发出，不包一层的话，模型会以为是你刚说的话。
3. **放进待发区 `pendingFires`**，以循环 ID 为键。模型忙的时候同一个循环触发 10 次，待发区里也只留最新一条。
4. **决定什么时候发**：带 `!` 的循环在下一个工具调用边界插队（每轮最多一次）；默认的循环等 `agent_settled`，或者在空闲时等 50 毫秒，把同时到期的消息合并成一条发出。

```svg title="图 2：pi-loop 一次触发的四步" tone="light"
<svg viewBox="0 0 680 470" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif" font-size="12.5">
  <defs>
    <marker id="pf-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 1 L10 5 L0 9 z" fill="#64748b"/>
    </marker>
  </defs>

  <rect x="190" y="12" width="300" height="40" rx="10" fill="#1f2937"/>
  <text x="340" y="37" text-anchor="middle" fill="#fff" font-size="14" font-weight="700">定时器到期：fire(id, ctx)</text>
  <line x1="340" y1="52" x2="340" y2="72" stroke="#64748b" stroke-width="2" marker-end="url(#pf-arrow)"/>

  <rect x="20" y="76" width="640" height="56" rx="12" fill="#ecfdf3" stroke="#16a34a" stroke-width="1.5"/>
  <circle cx="46" cy="104" r="13" fill="#16a34a"/><text x="46" y="109" text-anchor="middle" fill="#fff" font-weight="700">1</text>
  <text x="70" y="99" fill="#14532d" font-size="14" font-weight="700">先挂好下一次定时器，再执行动作</text>
  <text x="70" y="120" fill="#166534">就算这次的动作报错或卡死，循环也不会断</text>
  <line x1="340" y1="132" x2="340" y2="152" stroke="#64748b" stroke-width="2" marker-end="url(#pf-arrow)"/>

  <rect x="20" y="156" width="640" height="56" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="46" cy="184" r="13" fill="#2457d6"/><text x="46" y="189" text-anchor="middle" fill="#fff" font-weight="700">2</text>
  <text x="70" y="179" fill="#111827" font-size="14" font-weight="700">用 &lt;scheduled_trigger&gt; 包住 prompt</text>
  <text x="70" y="200" fill="#475569">消息以 user 身份发出，包一层是为了告诉模型“这不是用户说的话”</text>
  <line x1="340" y1="212" x2="340" y2="232" stroke="#64748b" stroke-width="2" marker-end="url(#pf-arrow)"/>

  <rect x="20" y="236" width="640" height="56" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="46" cy="264" r="13" fill="#2457d6"/><text x="46" y="269" text-anchor="middle" fill="#fff" font-weight="700">3</text>
  <text x="70" y="259" fill="#111827" font-size="14" font-weight="700">放进待发区 pendingFires，按 loop.id 去重</text>
  <text x="70" y="280" fill="#475569">模型忙的时候同一个循环触发 10 次，待发区里也只留最新的 1 条</text>

  <!-- branch -->
  <path d="M340 292 V310 H180 V330" fill="none" stroke="#64748b" stroke-width="2" marker-end="url(#pf-arrow)"/>
  <path d="M340 310 H500 V330" fill="none" stroke="#64748b" stroke-width="2" marker-end="url(#pf-arrow)"/>
  <text x="172" y="306" text-anchor="end" fill="#b45309" font-weight="600">带 !（force: true）</text>
  <text x="508" y="306" fill="#1d4ed8" font-weight="600">默认</text>

  <rect x="20" y="334" width="316" height="124" rx="12" fill="#fffbeb" stroke="#d97706" stroke-width="1.5"/>
  <text x="36" y="360" fill="#92400e" font-size="14" font-weight="700">4a 立即插队（steer）</text>
  <text x="36" y="384" fill="#78350f">在下一个工具调用边界插进当前这一轮</text>
  <text x="36" y="406" fill="#78350f">每轮最多插一次，回合结束时重置</text>
  <text x="36" y="436" fill="#92400e" font-size="12" font-family="ui-monospace, Menlo, Consolas, monospace">sendUserMessage(body, {deliverAs: "steer"})</text>

  <rect x="344" y="334" width="316" height="124" rx="12" fill="#eff6ff" stroke="#2457d6" stroke-width="1.5"/>
  <text x="360" y="360" fill="#1e3a8a" font-size="14" font-weight="700">4b 等空下来再发</text>
  <text x="360" y="384" fill="#1e40af">模型忙：等 agent_settled 事件</text>
  <text x="360" y="406" fill="#1e40af">模型闲：等 50ms，把同时到期的合并</text>
  <text x="360" y="436" fill="#1e3a8a" font-size="12" font-family="ui-monospace, Menlo, Consolas, monospace">所有待发消息用 --- 拼成 1 条发出</text>
</svg>
```

```ts title="davidroman0O/pi-loop · extensions/loop.ts (Lines 265-404)" raw
<span class="kw">async function</span> <span class="fn">fire</span>(id: <span class="typ">string</span>, ctx: <span class="typ">ExtensionContext</span>, manual = <span class="kw">false</span>): <span class="typ">Promise</span>&lt;<span class="kw">void</span>&gt; {
  <span class="kw">const</span> loop = store.<span class="fn">get</span>(id);
  <span class="kw">if</span> (!loop || loop.status === <span class="str">"done"</span> || loop.status === <span class="str">"error"</span>) <span class="kw">return</span>;
  <span class="kw">if</span> (!manual &amp;&amp; !loop.enabled) <span class="kw">return</span>;
  <span class="kw">if</span> (firing.<span class="fn">has</span>(id)) <span class="kw">return</span>; <span class="cmt">// 重入保护 (overlap guard)</span>
  firing.<span class="fn">add</span>(id);
  <span class="kw">try</span> {
    <span class="kw">const</span> now = <span class="kw">new</span> <span class="typ">Date</span>();
    <span class="kw">const</span> plan = <span class="fn">planFire</span>(loop, now);

    <span class="cmt">// ★ 阶段 1：在执行动作之前，先把下一次定时器挂好！</span>
    <span class="cmt">// 这样即便后续 executeAction 抛错或卡死，周期性循环也绝不会断掉。</span>
    <span class="kw">if</span> (plan.terminal) {
      <span class="fn">disarm</span>(id);
    } <span class="kw">else if</span> (loop.enabled &amp;&amp; loop.type === <span class="str">"interval"</span> &amp;&amp; plan.nextDelayMs !== <span class="kw">undefined</span>) {
      <span class="kw">const</span> delay = Math.<span class="fn">min</span>(plan.nextDelayMs, MAX_TIMER_DELAY_MS);
      <span class="kw">const</span> timer = <span class="fn">setTimeout</span>(() =&gt; {
        handles.<span class="fn">delete</span>(id);
        <span class="kw">void</span> <span class="fn">fire</span>(id, ctx);
      }, delay);
      timer.unref?.();
      handles.<span class="fn">set</span>(id, { kind: <span class="str">"timeout"</span>, handle: timer });
      store.<span class="fn">update</span>(id, { nextRun: <span class="kw">new</span> <span class="typ">Date</span>(now.<span class="fn">getTime</span>() + delay).<span class="fn">toISOString</span>() });
    }

    store.<span class="fn">update</span>(id, { runCount: plan.runCount, lastRun: now.<span class="fn">toISOString</span>() });
    <span class="kw">await</span> <span class="fn">executeAction</span>(loop, ctx);
    <span class="cmt">// 若达到 maxFires 或 once 已触发，直接从 store 移除</span>
    <span class="kw">if</span> (plan.terminal) { <span class="fn">disarm</span>(id); store.<span class="fn">remove</span>(id); }
    <span class="kw">void</span> store.<span class="fn">persist</span>();
  } <span class="kw">finally</span> {
    firing.<span class="fn">delete</span>(id);
  }
}

<span class="cmt">// ★ 阶段 2：包装身份标签 —— 为什么需要 &lt;scheduled_trigger&gt;？</span>
<span class="cmt">// 因为后续调用的是 pi.sendUserMessage()，在底层 LLM 消息数组中 role 是 "user"。</span>
<span class="cmt">// 如果不加这个 XML 边界，LLM 会以为是人类用户刚刚亲口说了这句话！</span>
<span class="kw">function</span> <span class="fn">wrapScheduled</span>(payload: <span class="typ">string</span>): <span class="typ">string</span> {
  <span class="kw">return</span> <span class="str">`&lt;scheduled_trigger&gt;\nAutomated scheduled trigger — not a message from the user.\n\n${payload}\n&lt;/scheduled_trigger&gt;`</span>;
}

<span class="cmt">// ★ 阶段 3 &amp; 4：缓冲去重 + 区分强制打断 (steer) 与 空闲合并投递 (agent_settled)</span>
<span class="kw">function</span> <span class="fn">sendAgentPrompt</span>(ctx: <span class="typ">ExtensionContext</span>, loop: <span class="typ">Loop</span>, prompt: <span class="typ">string</span>) {
  <span class="cmt">// 永远先写入 pendingFires Map（以 loop.id 为 Key，同一个 Loop 在 Agent 忙时触发多次会自动覆盖只留最新一次！）</span>
  pendingFires.<span class="fn">set</span>(loop.id, { loop, firedAt: <span class="kw">new</span> <span class="typ">Date</span>().<span class="fn">toISOString</span>(), prompt, force: !!loop.force });
  <span class="fn">updateUI</span>(ctx);
  <span class="kw">if</span> (loop.force) {
    <span class="fn">maybeSteer</span>(ctx); <span class="cmt">// 带 ! 的强制循环：在下一个工具调用边界立即插队</span>
    <span class="kw">return</span>;
  }
  <span class="cmt">// 默认温和模式：如果当前空闲，走 50ms 防抖合并；如果正忙，留给 agent_settled 事件一次性排空</span>
  <span class="fn">scheduleIdleFlush</span>(ctx);
}

<span class="cmt">// 强制插队（每轮 Turn 最多允许 Steer 一次，turn_end 时重置 steeredThisTurn = false）</span>
<span class="kw">function</span> <span class="fn">maybeSteer</span>(ctx: <span class="typ">ExtensionContext</span>) {
  <span class="kw">if</span> (steeredThisTurn) <span class="kw">return</span>;
  <span class="kw">const</span> forced = [...pendingFires.<span class="fn">values</span>()].<span class="fn">filter</span>((e) =&gt; e.force);
  <span class="kw">if</span> (forced.length === <span class="num">0</span>) <span class="kw">return</span>;
  <span class="kw">for</span> (<span class="kw">const</span> e <span class="kw">of</span> forced) pendingFires.<span class="fn">delete</span>(e.loop.id);
  steeredThisTurn = <span class="kw">true</span>;
  <span class="fn">updateUI</span>(ctx);
  <span class="kw">const</span> body = forced.<span class="fn">map</span>((e) =&gt; e.prompt).<span class="fn">join</span>(<span class="str">"\n\n---\n\n"</span>);
  pi.<span class="fn">sendUserMessage</span>(body, { deliverAs: <span class="str">"steer"</span> }); <span class="cmt">// ← 插入当前 Turn 的下一个工具边界！</span>
}

<span class="cmt">// 默认温和排空：当 Agent 彻底跑完当前多步任务（agent_settled）或处于 Idle 满 50ms 时触发</span>
<span class="kw">function</span> <span class="fn">drainPending</span>(ctx: <span class="typ">ExtensionContext</span>) {
  <span class="kw">if</span> (pendingFires.size === <span class="num">0</span>) <span class="kw">return</span>;
  <span class="kw">if</span> (!ctx.<span class="fn">isIdle</span>()) <span class="kw">return</span>;
  <span class="kw">const</span> entries = [...pendingFires.<span class="fn">values</span>()];
  pendingFires.<span class="fn">clear</span>();
  <span class="fn">updateUI</span>(ctx);
  <span class="kw">const</span> body = entries.<span class="fn">map</span>((e) =&gt; e.prompt).<span class="fn">join</span>(<span class="str">"\n\n---\n\n"</span>);
  pi.<span class="fn">sendUserMessage</span>(body); <span class="cmt">// ← 作为一条合并后的全新 User Turn 追加到 Transcript 末尾！</span>
}
```

### 只跑命令、不叫醒模型

pi-loop 有一种省 token 的用法：定时在后台跑一条 shell 命令，比如 `npm test` 或 `gh pr checks`。

- **不配 `followUpPrompt`**：只在界面留一行日志，完全不叫醒模型。
- **配了 `followUpPrompt`**：把退出码和截断到 8000 字符的输出拼成一条消息，包进 `<scheduled_trigger>` 发给模型，最后附上你写的后续指令。

右边就是模型收到的消息原文：开头声明“这是自动触发，不是用户发言”，中间是命令结果，最后一行是 `followUpPrompt`。

````text title="实际注入给 LLM 的 User Message 文本结构（shell + followUpPrompt 模式）"
<scheduled_trigger>
Automated scheduled trigger — not a message from the user.

A scheduled shell command completed.

Command: `gh pr checks 42`
CWD: `/Users/dev/repo`
Exit code: 1

STDOUT:
```
build-linux   fail   2m14s   https://github.com/...
```
STDERR:
```
```

If any check failed, inspect the failure log and fix the compilation error.
</scheduled_trigger>
````

## Prime Agent：心跳是 REPL 里的一个函数

Prime Agent 只给模型一个工具 `python_repl`，其他能力都是 REPL 里的 Python 异步函数。心跳也一样：模型执行 `await rlm_heartbeat.create("check training loss", interval="every 5m")` 就建好了一个定时任务。

系统提示词里定义了这几个函数，还约定了一条规则：以 `[kind: qualifier]` 这种方括号开头的 user 消息都是系统自动发的，不是用户说的话。心跳消息正是用这个格式，模型因此能分辨来源。

```md title="crates/pa-core/src/prompts/layers/core.md (Lines 5-7 &amp; 166-173)"
The harness often sends messages to the agent. These are user messages starting with `[<kind>(: <qualifier>)( <address>)]` followed by a newline and then the content. They are not user-generated.

## Heartbeat

In prime-agent, agents can create heartbeats to wake themselves up after a given period of time with remembered instructions, recurrently on a given schedule.

- `rlm_heartbeat.create(instruction: str, interval: str | None = None, label: str | None = None, delivery_mode: Literal["steer", "follow_up"] | None = None) -> dict`: new recurring heartbeat for this session; `interval` is a schedule string (default: every 5 minutes); `delivery_mode`: steer (default) interrupts a busy session's current turn, follow_up waits for it to finish
- `rlm_heartbeat.list(include_inactive: bool = False) -> dict`
- `rlm_heartbeat.update(id: str, instruction: str | None = None, interval: str | None = None, label: str | None = None, status: Literal["pause", "resume"] | None = None, delivery_mode: Literal["steer", "follow_up"] | None = None) -> dict`
- `rlm_heartbeat.delete(id: str) -> dict`
```

### 上下文压缩后也记得自己挂着心跳

长会话会被压缩成摘要。Prime Agent 的压缩提示词专门要求摘要里写明正在运行的心跳和目标，并附上“恢复指针”：调用 `rlm_heartbeat.list()` 就能找回。这样压缩之后，模型不会忘记自己还有定时任务。

```rust title="crates/pa-core/src/session_engine/compaction.rs (Lines 14-35)" kind="prompt" raw
<span class="kw">const</span> SUMMARIZATION_PROMPT: &amp;<span class="typ">str</span> = <span class="str">"The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.\n\nUse this EXACT format:\n\n## Goal\n...\n## Outstanding Work\n- [Running subagents: name / task / status (recovery pointer: rlm.list_subagents())]\n- [Background bash handles: command / status (recovery: the [bash-done] notification; a lost handle cannot be re-awaited)]\n- [Active heartbeats and the goal: schedule / objective (recovery pointers: rlm_heartbeat.list() and goal.get())]\n- [Or \"(none)\" if nothing is in flight]\n..."</span>;
```

### 从调用函数到调度器被唤醒

1. Python 内核向 Rust 会话引擎发送 `rlm_heartbeat.create` 请求。
2. 引擎校验参数，投递方式默认设为 `Steer`。
3. 任务写入 `~/.prime/agent/cron_jobs.json`，会话目录下的 `heartbeat.json` 也存一份。
4. 立即通知守护进程里的调度器重新计算最近的到期时间，不用等下一次轮询。

调度器还有两条规则：任务失败后按 2 分钟起步、每次翻倍、最长 1 小时的间隔重试；不同会话的任务并行执行，同一个会话的任务按顺序一个个来。

> 注意默认值和 pi-loop 正好相反：Prime Agent 的心跳默认插进正在进行的回合，pi-loop 默认等模型空闲。

```rust title="crates/pa-core/src/cron/scheduler.rs (Lines 14-185)" raw
<span class="kw">const</span> MAX_TIMER_DELAY_MS: <span class="typ">u64</span> = <span class="num">60_000</span>;
<span class="kw">const</span> INITIAL_RETRY_BACKOFF_MS: <span class="typ">u64</span> = <span class="num">2</span> * <span class="num">60_000</span>; <span class="cmt">// 2分钟初始退避</span>
<span class="kw">const</span> MAX_RETRY_BACKOFF_MS: <span class="typ">u64</span> = <span class="num">60</span> * <span class="num">60_000</span>;    <span class="cmt">// 1小时最大退避上限</span>

<span class="cmt">/// 指数退避公式：120_000 * 2^(streak - 1)，封顶 3_600_000 ms (1h)</span>
<span class="kw">pub fn</span> <span class="fn">failure_backoff_ms</span>(failure_streak: <span class="typ">u64</span>) -&gt; <span class="typ">u64</span> {
    <span class="kw">let</span> exponent = failure_streak.<span class="fn">saturating_sub</span>(<span class="num">1</span>).<span class="fn">min</span>(<span class="num">16</span>) <span class="kw">as</span> <span class="typ">u32</span>;
    INITIAL_RETRY_BACKOFF_MS
        .<span class="fn">saturating_mul</span>(<span class="num">1u64</span> &lt;&lt; exponent)
        .<span class="fn">min</span>(MAX_RETRY_BACKOFF_MS)
}

<span class="cmt">// 按 Session 分组并发派发：不同 Session 的到期任务并行跑，</span>
<span class="cmt">// 同一个 Session 的多个到期任务在同一个 dispatch_lane 内严格串行（FIFO）！</span>
<span class="kw">let</span> lane_key = <span class="kw">if</span> !job.active_session_id.<span class="fn">is_empty</span>() {
    job.active_session_id.<span class="fn">clone</span>()
} <span class="kw">else</span> {
    job.session_file.<span class="fn">clone</span>()
};
```

## Prime Agent：心跳到期后的五步

调度器认领一个到期心跳后，`run_job()` 依次做五件事：检查要不要发、去重、包装成心跳消息、选通道入队，最后在发给模型前转换格式。

去重是关键：入队前先在 Steer 和 FollowUp 两条通道里，删掉同一个心跳（键为 `heartbeat:<id>`）还没被消费的旧消息。所以模型再忙，同一个心跳在队列里也只有一条。

转换这一步决定模型能看到什么：心跳消息会变成普通的 user 消息发给模型；斜杠命令结果、压缩结果这类记账用的内部消息只在界面显示，直接过滤掉，不会改变发给模型的内容。

```svg title="图 3：Prime Agent 心跳到期后的五步" tone="light"
<svg viewBox="0 0 680 520" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif" font-size="12.5">
  <defs>
    <marker id="pa-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 1 L10 5 L0 9 z" fill="#64748b"/>
    </marker>
  </defs>

  <rect x="170" y="12" width="340" height="40" rx="10" fill="#1f2937"/>
  <text x="340" y="37" text-anchor="middle" fill="#fff" font-size="14" font-weight="700">调度器认领到期心跳：run_job()</text>
  <line x1="340" y1="52" x2="340" y2="70" stroke="#64748b" stroke-width="2" marker-end="url(#pa-arrow)"/>

  <!-- 1 -->
  <rect x="20" y="74" width="640" height="56" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="46" cy="102" r="13" fill="#2457d6"/><text x="46" y="107" text-anchor="middle" fill="#fff" font-weight="700">1</text>
  <text x="70" y="97" fill="#111827" font-size="14" font-weight="700">检查要不要发</text>
  <text x="70" y="118" fill="#475569">会话已删除或归档：取消任务。会话正在压缩、重试：这次跳过。</text>
  <line x1="340" y1="130" x2="340" y2="146" stroke="#64748b" stroke-width="2" marker-end="url(#pa-arrow)"/>

  <!-- 2 -->
  <rect x="20" y="150" width="640" height="56" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="46" cy="178" r="13" fill="#2457d6"/><text x="46" y="183" text-anchor="middle" fill="#fff" font-weight="700">2</text>
  <text x="70" y="173" fill="#111827" font-size="14" font-weight="700">去重：同一个心跳在队列里只留一条</text>
  <text x="70" y="194" fill="#475569">以 heartbeat:&lt;id&gt; 为键，删掉两条通道里还没被消费的旧消息</text>
  <line x1="340" y1="206" x2="340" y2="222" stroke="#64748b" stroke-width="2" marker-end="url(#pa-arrow)"/>

  <!-- 3 -->
  <rect x="20" y="226" width="640" height="56" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="46" cy="254" r="13" fill="#2457d6"/><text x="46" y="259" text-anchor="middle" fill="#fff" font-weight="700">3</text>
  <text x="70" y="249" fill="#111827" font-size="14" font-weight="700">包装成心跳消息</text>
  <text x="70" y="270" fill="#475569"><tspan>正文是 </tspan><tspan font-family="ui-monospace, Menlo, Consolas, monospace" font-size="12" fill="#92400e">[heartbeat: every 5m run#1]</tspan><tspan> 加上原始指令</tspan></text>
  <line x1="340" y1="282" x2="340" y2="298" stroke="#64748b" stroke-width="2" marker-end="url(#pa-arrow)"/>

  <!-- 4 -->
  <rect x="20" y="302" width="640" height="56" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="46" cy="330" r="13" fill="#2457d6"/><text x="46" y="335" text-anchor="middle" fill="#fff" font-weight="700">4</text>
  <text x="70" y="325" fill="#111827" font-size="14" font-weight="700">选通道入队</text>
  <text x="70" y="346" fill="#475569"><tspan fill="#b45309" font-weight="600">Steer（默认）</tspan><tspan>：下一个回合边界就插入　·　</tspan><tspan fill="#1d4ed8" font-weight="600">FollowUp</tspan><tspan>：等会话空闲</tspan></text>
  <line x1="340" y1="358" x2="340" y2="374" stroke="#64748b" stroke-width="2" marker-end="url(#pa-arrow)"/>

  <!-- 5 -->
  <rect x="20" y="378" width="640" height="56" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="46" cy="406" r="13" fill="#2457d6"/><text x="46" y="411" text-anchor="middle" fill="#fff" font-weight="700">5</text>
  <text x="70" y="401" fill="#111827" font-size="14" font-weight="700">发给模型前转换：convert_to_llm()</text>
  <text x="70" y="422" fill="#475569">心跳消息变成普通的 user 消息；记账用的内部消息直接丢掉</text>

  <!-- keep / drop -->
  <rect x="20" y="448" width="316" height="62" rx="10" fill="#ecfdf3" stroke="#86efac"/>
  <text x="34" y="470" fill="#166534" font-weight="700">发给模型</text>
  <text x="34" y="492" fill="#166534" font-size="12">心跳、后台命令完成通知、进度通知</text>
  <rect x="344" y="448" width="316" height="62" rx="10" fill="#fef2f2" stroke="#fecaca"/>
  <text x="358" y="470" fill="#991b1b" font-weight="700">只在界面显示，不发给模型</text>
  <text x="358" y="492" fill="#991b1b" font-size="12">斜杠命令结果、压缩结果、MCP 连接、重试记录</text>
</svg>
```

### 心跳消息长什么样

心跳消息的正文由 `create_heartbeat_prompt_message()` 生成：第一行是方括号头，写明调度规则和这是第几次运行，空一行后是建心跳时记下的指令。以前文的例子来说，模型收到的就是右边这样一条消息。

```text title="示例：rlm_heartbeat 第 1 次触发时模型收到的消息"
[heartbeat: every 5m run#1]

check training loss
```

下面是生成这条消息、以及发给模型前做转换的源码。`convert_to_llm()` 里列出的类型会被跳过，`heartbeat_prompt` 不在其中，所以会被包装成 user 消息。

```rust title="crates/pa-core/src/session_engine/messages.rs (Lines 172-215 &amp; 322-356)" raw
<span class="cmt">/// The durable heartbeat delivery row: the job's prompt under the `[heartbeat:</span>
<span class="cmt">/// &lt;schedule&gt; run#&lt;n&gt;]` header, with the run bookkeeping in details.</span>
#[must_use]
<span class="kw">pub fn</span> <span class="fn">create_heartbeat_prompt_message</span>(
    job: &amp;<span class="typ">AgentCronJob</span>,
    timestamp: <span class="typ">u64</span>,
) -&gt; pa_types::session::<span class="typ">CustomMessage</span> {
    <span class="kw">let</span> schedule = <span class="fn">sanitize_message_header_value</span>(&amp;job.schedule.expression);
    <span class="kw">let</span> content = <span class="fn">format!</span>(
        <span class="str">"[heartbeat: {schedule} run#{}]\n\n{}"</span>,
        job.run_count, job.prompt
    );
    <span class="kw">let mut</span> details = serde_json::Map::<span class="fn">new</span>();
    details.<span class="fn">insert</span>(<span class="str">"jobId"</span>.<span class="fn">to_string</span>(), serde_json::<span class="fn">json!</span>(job.id));
    details.<span class="fn">insert</span>(<span class="str">"schedule"</span>.<span class="fn">to_string</span>(), serde_json::<span class="fn">json!</span>(job.schedule.expression));
    details.<span class="fn">insert</span>(<span class="str">"status"</span>.<span class="fn">to_string</span>(), serde_json::<span class="fn">to_value</span>(job.status).<span class="fn">unwrap_or</span>(serde_json::Value::Null));
    details.<span class="fn">insert</span>(<span class="str">"runCount"</span>.<span class="fn">to_string</span>(), serde_json::<span class="fn">json!</span>(job.run_count));
    <span class="kw">if let</span> <span class="typ">Some</span>(next_run_at) = &amp;job.next_run_at {
        details.<span class="fn">insert</span>(<span class="str">"nextRunAt"</span>.<span class="fn">to_string</span>(), serde_json::<span class="fn">json!</span>(next_run_at));
    }
    <span class="kw">if let</span> <span class="typ">Some</span>(last_run_at) = &amp;job.last_run_at {
        details.<span class="fn">insert</span>(<span class="str">"lastRunAt"</span>.<span class="fn">to_string</span>(), serde_json::<span class="fn">json!</span>(last_run_at));
    }
    pa_types::session::<span class="typ">CustomMessage</span> {
        custom_type: HEARTBEAT_PROMPT_CUSTOM_TYPE.<span class="fn">to_string</span>(), <span class="cmt">// "heartbeat_prompt"</span>
        content: UserContent::<span class="fn">Text</span>(content),
        display: <span class="kw">true</span>,
        details: <span class="typ">Some</span>(serde_json::Value::<span class="fn">Object</span>(details)),
        timestamp,
        rest: serde_json::Map::<span class="fn">default</span>(),
    }
}

<span class="cmt">/// The LLM message view of session messages: session-only roles become user</span>
<span class="cmt">/// text turns; bookkeeping custom types drop out entirely.</span>
#[must_use]
<span class="kw">pub fn</span> <span class="fn">convert_to_llm</span>(messages: &amp;[<span class="typ">AgentMessage</span>]) -&gt; <span class="typ">Vec</span>&lt;<span class="typ">AgentMessage</span>&gt; {
    <span class="kw">let mut</span> out = <span class="typ">Vec</span>::<span class="fn">new</span>();
    <span class="kw">for</span> message <span class="kw">in</span> messages {
        <span class="kw">let</span> converted = <span class="kw">match</span> message {
            <span class="cmt">// ...</span>
            AgentMessage::<span class="fn">Custom</span>(custom) =&gt; {
                <span class="kw">if</span> <span class="fn">matches!</span>(
                    custom.custom_type.<span class="fn">as_str</span>(),
                    SESSION_SLASH_COMMAND_CUSTOM_TYPE
                        | SESSION_SLASH_COMMAND_RESULT_CUSTOM_TYPE
                        | COMPACTION_OUTCOME_CUSTOM_TYPE
                        | MCP_CONNECTION_OUTCOME_CUSTOM_TYPE
                        | REFINEMENT_OUTCOME_CUSTOM_TYPE
                        | PROVIDER_RETRY_OUTCOME_CUSTOM_TYPE
                        | crate::prompts::model_prompts::MODEL_PROMPT_ERROR_CUSTOM_TYPE
                ) {
                    <span class="kw">continue</span>; <span class="cmt">// Bookkeeping 行直接丢弃，不进模型上下文</span>
                }
                <span class="cmt">// "heartbeat_prompt" 不在上方黑名单中，因此被包装成标准 UserMessage Blocks 发送给 LLM！</span>
                <span class="kw">let</span> content = <span class="kw">match</span> custom.content.<span class="fn">clone</span>() {
                    UserContent::<span class="fn">Text</span>(text) =&gt; UserContent::<span class="fn">Blocks</span>(<span class="fn">vec!</span>[<span class="fn">text_block</span>(text)]),
                    blocks @ UserContent::<span class="fn">Blocks</span>(_) =&gt; blocks,
                };
                AgentMessage::<span class="fn">User</span>(<span class="typ">UserMessage</span> {
                    content,
                    timestamp: custom.timestamp,
                    rest: serde_json::Map::<span class="fn">default</span>(),
                })
            }
            <span class="cmt">// ...</span>
        };
        out.<span class="fn">push</span>(converted);
    }
    out
}
```

### 入队与去重的源码

心跳默认走 Steer 通道，显式设了 `FollowUp` 才走另一条；普通 cron 任务走 FollowUp。入队时还会解除会话因中断或压缩而暂停的排队状态。

```rust title="crates/pa-daemon/src/scheduled_jobs.rs (Lines 103-175)" raw
<span class="kw">let</span> heartbeat = <span class="fn">is_heartbeat_cron_job</span>(job); <span class="cmt">// source == "heartbeat" || "rlm_heartbeat"</span>
<span class="kw">let</span> queue_key = heartbeat.<span class="fn">then</span>(|| <span class="fn">format!</span>(<span class="str">"heartbeat:{}"</span>, job.id));
<span class="cmt">// Heartbeat 默认走 steering 车道（除非显式指定 FollowUp）；普通 cron 走 follow_up 车道</span>
<span class="kw">let</span> rides_steering =
    heartbeat &amp;&amp; !<span class="fn">matches!</span>(job.delivery_mode, <span class="typ">Some</span>(DeliveryMode::FollowUp));
{
    <span class="kw">let mut</span> core = <span class="kw">self</span>.core.<span class="fn">lock</span>().<span class="fn">unwrap_or_else</span>(std::sync::PoisonError::into_inner);
    <span class="cmt">// 若会话此前因 abort 或 compact 暂停了排队输入，Cron 触发会将其自动恢复</span>
    core.queued_input_suspended = <span class="kw">false</span>;
    <span class="cmt">// 以 "heartbeat:&lt;id&gt;" 为 Key 在两条车道上原地去重，避免未消费的心跳堆积</span>
    <span class="kw">if let</span> <span class="typ">Some</span>(key) = &amp;queue_key {
        core.steering.<span class="fn">retain</span>(|item| item.queue_key.<span class="fn">as_deref</span>() != <span class="typ">Some</span>(key.<span class="fn">as_str</span>()));
        core.follow_up.<span class="fn">retain</span>(|item| item.queue_key.<span class="fn">as_deref</span>() != <span class="typ">Some</span>(key.<span class="fn">as_str</span>()));
    }
    <span class="kw">let</span> lane = <span class="kw">if</span> rides_steering { &amp;<span class="kw">mut</span> core.steering } <span class="kw">else</span> { &amp;<span class="kw">mut</span> core.follow_up };
    <span class="cmt">// 入队并标记 TurnPolicy::Injected，随后唤醒 Worker 消费并写入恢复日志 checkpoint</span>
    crate::worker::<span class="fn">enqueue_priority</span>(lane, <span class="typ">QueuedItem</span> {
        priority: crate::worker::QueuePriority::Background,
        message,
        preview,
        custom_message,
        queue_key,
        policy: crate::worker::TurnPolicy::Injected,
        <span class="cmt">// ...</span>
    });
}
```

## cronex：给没有调度器的 Codex 补上定时

Codex CLI 本身没有会话内的定时调度器。`stbenjam/cronex` 的思路是：用**同一个 Go 程序**扮演两个角色，通过本地 SQLite（`~/.codex/cronex/crons.sqlite3`）共享状态：

- **MCP 服务器**：给模型提供 `CronCreate`、`CronList`、`CronDelete` 三个工具。
- **7 个生命周期钩子**：在会话开始、提交消息、工具调用后、回合结束等时刻被 Codex 调用，负责记录状态和投递消息。

右边是 `cronex config` 生成的 Codex 配置。其中 `Stop` 和 `UserPromptSubmit` 各挂了两个钩子：一个同步，一个以 `async = true` 启动的后台守望进程，超时设成了几乎无限长。

```toml title="stbenjam/cronex · cmd/cronex/main.go (Lines 145-171 生成的 config.toml)" raw
<span class="cmt"># Generated by cronex config. Merge into your Codex config.toml.</span>
[mcp_servers.cronex]
command = <span class="str">"/usr/local/bin/cronex"</span>
args = [<span class="str">"serve"</span>, <span class="str">"--db"</span>, <span class="str">"/Users/dev/.codex/cronex/crons.sqlite3"</span>]

<span class="cmt"># 1. 会话启动/恢复：在 SQLite 创建或重新激活 sessions(ended=0)</span>
[[hooks.SessionStart]]
[[hooks.SessionStart.hooks]]
type = <span class="str">"command"</span>
command = <span class="str">"'/usr/local/bin/cronex' hook --db '/Users/dev/.codex/cronex/crons.sqlite3' --codex 'codex'"</span>
timeout = <span class="num">5</span>

<span class="cmt"># 2. 子 Agent 拦截：禁止 Subagent 继承定时任务</span>
[[hooks.SubagentStart]]
[[hooks.SubagentStart.hooks]]
type = <span class="str">"command"</span>
command = <span class="str">"'/usr/local/bin/cronex' hook --db ... --codex 'codex'"</span>
timeout = <span class="num">5</span>

<span class="cmt"># 3. 用户提交 Prompt（或 codex queue 注入的 Prompt 开始执行）：</span>
<span class="cmt">#    同步 Hook 设 idle=0 并校验 "Cronex delivery: &lt;uuid&gt;" 令牌推进 NextRunAt；</span>
<span class="cmt">#    异步 Hook (--watch) 启动新一轮后台守望进程！</span>
[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = <span class="str">"command"</span>
command = <span class="str">"'/usr/local/bin/cronex' hook --db ... --codex 'codex'"</span>
timeout = <span class="num">5</span>
[[hooks.UserPromptSubmit.hooks]]
type = <span class="str">"command"</span>
command = <span class="str">"'/usr/local/bin/cronex' hook --db ... --codex 'codex' --watch"</span>
async = <span class="kw">true</span>
timeout = <span class="num">2147483647</span>

<span class="cmt"># 4. 工具调用后置钩子（Busy 态毫秒级顺风车注入！）：</span>
[[hooks.PostToolUse]]
[[hooks.PostToolUse.hooks]]
type = <span class="str">"command"</span>
command = <span class="str">"'/usr/local/bin/cronex' hook --db ... --codex 'codex'"</span>
timeout = <span class="num">5</span>
additionalContextLimit = <span class="num">20000</span>

<span class="cmt"># 5. 回合正常结束（Idle 态唤醒）：同步 Hook 设 idle=1，异步 Hook (--watch) 阻塞等待到期后调用 codex queue！</span>
[[hooks.Stop]]
[[hooks.Stop.hooks]]
type = <span class="str">"command"</span>
command = <span class="str">"'/usr/local/bin/cronex' hook --db ... --codex 'codex'"</span>
timeout = <span class="num">5</span>
[[hooks.Stop.hooks]]
type = <span class="str">"command"</span>
command = <span class="str">"'/usr/local/bin/cronex' hook --db ... --codex 'codex' --watch"</span>
async = <span class="kw">true</span>
timeout = <span class="num">2147483647</span>

<span class="cmt"># 6 &amp; 7. Interrupt (设 idle=1 恢复原先 Watcher 投递) 与 SessionEnd (设 ended=1 暂停投递但保留任务)</span>
```

### 怎么知道任务属于哪个会话

多个 Codex 会话可能共用同一个 MCP 服务器连接。cronex 的做法是读取每次工具调用附带的 `_meta.threadId`，按调用识别会话，而不是按连接。

MCP 握手时，cronex 还会给 Codex 一段说明：建完任务正常结束回合即可，不要自己轮询或 sleep；子 Agent 不能建任务；漏掉的周期会合并成一次；会话关闭时暂停投递，但任务保留。

```go title="stbenjam/cronex · internal/cronex/server.go (Lines 23-41)" kind="prompt" raw
<span class="cmt">// requestSession uses Codex's per-call routing metadata, not the MCP connection</span>
<span class="cmt">// ID. A connection can be shared by threads. bound is for manual clients only.</span>
<span class="kw">func</span> <span class="fn">requestSession</span>(req *mcp.CallToolRequest, bound <span class="typ">string</span>) (<span class="typ">string</span>, <span class="typ">error</span>) {
	session, _ := req.Params.Meta[<span class="str">"threadId"</span>].(<span class="typ">string</span>)
	<span class="kw">if</span> session == <span class="str">""</span> {
		session = bound
	}
	<span class="kw">if</span> strings.<span class="fn">TrimSpace</span>(session) == <span class="str">""</span> {
		<span class="kw">return</span> <span class="str">""</span>, errors.<span class="fn">New</span>(<span class="str">"missing Codex _meta.threadId; use a compatible Codex client or serve --session for manual testing"</span>)
	}
	<span class="kw">return</span> session, <span class="kw">nil</span>
}

<span class="kw">func</span> <span class="fn">NewServer</span>(s *<span class="typ">Store</span>, bound <span class="typ">string</span>) *mcp.Server {
	server := mcp.<span class="fn">NewServer</span>(&amp;mcp.Implementation{Name: <span class="str">"cronex"</span>, Version: <span class="str">"0.1.0"</span>}, &amp;mcp.ServerOptions{
		Instructions: <span class="str">"Cronex schedules prompts in this root Codex session only. Subagent scheduling is unsupported; ask the parent agent to schedule work. Use CronCreate, CronList, and CronDelete for recurring or one-shot tasks. After scheduling, finish your turn normally: the async Stop hook waits and wakes this session. Do not poll or sleep to wait for crons. During active work PostToolUse delivers due prompts. Jobs survive MCP restarts, compaction, and SessionEnd. Delivery pauses while the session is closed; resume the same session and send a message to restart its watcher. Missed intervals coalesce into one run per job. Use CronDelete or expires_at to cancel schedules; SessionEnd does not cancel them. For PR loops use a named dynamic interval plus a separate recurring 28800-second watcher; retain the original start/deadline in the prompt and delete both jobs at termination. These tools schedule work; they do not authorize actions beyond the user's instructions."</span>,
	})
	<span class="cmt">// 注册 CronCreate, CronList, CronDelete ...</span>
}
```

## cronex：两条通道把消息送进会话

外部进程改不了 Codex 内存里的对话，cronex 只能借助钩子和命令，所以分了两条通道：

- **模型正在干活**：每次工具调用结束，`PostToolUse` 钩子查一下有没有到期任务。有就加租约取出，以 `additionalContext` 的形式附在这次工具结果旁边。消息进了正在进行的这一轮，不打断模型，也不用新开一轮。
- **模型空闲**：回合结束时，`Stop` 钩子拉起一个后台进程，休眠到最近的到期时间，醒来确认会话仍然空闲后，执行 `codex queue --thread <id>` 把消息排进会话。

通道 2 发出的消息第一行带一个回执号。新一轮开始时，`UserPromptSubmit` 钩子核对这个回执号，对上了才算送达，并计算下一次触发时间。

```svg title="图 4：cronex 的两条投递通道、回执与保活" tone="light"
<svg viewBox="0 0 680 520" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif" font-size="12.5">
  <defs>
    <marker id="cx-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 1 L10 5 L0 9 z" fill="#64748b"/>
    </marker>
  </defs>

  <!-- shared store -->
  <rect x="160" y="12" width="360" height="56" rx="10" fill="#1f2937"/>
  <text x="340" y="36" text-anchor="middle" fill="#fff" font-size="14" font-weight="700">SQLite：任务、会话忙闲状态、投递回执</text>
  <text x="340" y="56" text-anchor="middle" fill="#cbd5e1" font-size="12">取任务时加 30 秒租约，两条通道不会重复投递同一个任务</text>
  <path d="M220 68 V96" stroke="#64748b" stroke-width="1.5" marker-start="url(#cx-arrow)"/>
  <path d="M460 68 V96" stroke="#64748b" stroke-width="1.5" marker-start="url(#cx-arrow)"/>

  <!-- busy channel -->
  <rect x="20" y="100" width="316" height="250" rx="12" fill="#fffbeb" stroke="#d97706" stroke-width="1.5"/>
  <text x="36" y="126" fill="#92400e" font-size="14" font-weight="700">通道 1：模型正在干活</text>
  <text x="36" y="154" fill="#78350f">① 每次工具调用结束，PostToolUse 钩子运行</text>
  <text x="36" y="178" fill="#78350f">② 先做一次只读查询：有任务到期吗？</text>
  <text x="50" y="196" fill="#a16207" font-size="12">没到期就到此为止，几乎没有开销</text>
  <text x="36" y="220" fill="#78350f">③ 到期：加租约取出任务（最多 8 个）</text>
  <text x="36" y="244" fill="#78350f">④ 输出 additionalContext，附在这次</text>
  <text x="50" y="262" fill="#78350f">工具结果旁边，模型立刻看到</text>
  <rect x="36" y="280" width="284" height="52" rx="8" fill="#ffffff" stroke="#fcd34d"/>
  <text x="48" y="301" fill="#92400e" font-size="12">不打断模型，也不需要新开一轮：</text>
  <text x="48" y="320" fill="#92400e" font-size="12">任务“搭便车”进了正在进行的这一轮</text>

  <!-- idle channel -->
  <rect x="344" y="100" width="316" height="250" rx="12" fill="#eff6ff" stroke="#2457d6" stroke-width="1.5"/>
  <text x="360" y="126" fill="#1e3a8a" font-size="14" font-weight="700">通道 2：模型空闲</text>
  <text x="360" y="154" fill="#1e40af">① 回合结束，Stop 钩子启动后台进程</text>
  <text x="360" y="178" fill="#1e40af">② 进程休眠到最近的到期时间（不耗 token）</text>
  <text x="360" y="202" fill="#1e40af">③ 醒来再确认会话仍然空闲，加租约取任务</text>
  <text x="360" y="226" fill="#1e40af">④ 执行外部命令，把消息排进会话：</text>
  <text x="374" y="246" fill="#1e3a8a" font-size="12" font-family="ui-monospace, Menlo, Consolas, monospace">codex queue --thread &lt;id&gt;</text>
  <text x="374" y="264" fill="#1e3a8a" font-size="12" font-family="ui-monospace, Menlo, Consolas, monospace">  --message "Cronex delivery: …"</text>
  <text x="360" y="290" fill="#1e40af">⑤ 失败就释放租约，1s、2s、4s… 退避重试</text>
  <text x="360" y="318" fill="#64748b" font-size="12">消息第一行带一个回执号，用于下一步确认</text>

  <!-- ack -->
  <path d="M502 350 V378" stroke="#2457d6" stroke-width="1.5" marker-end="url(#cx-arrow)"/>
  <rect x="20" y="382" width="640" height="64" rx="12" fill="#f5f3ff" stroke="#7c3aed" stroke-width="1.5"/>
  <text x="36" y="406" fill="#5b21b6" font-size="14" font-weight="700">回执：UserPromptSubmit 钩子确认送达</text>
  <text x="36" y="428" fill="#5b21b6">新一轮开始时，核对首行的回执号；对上了就记为已送达，并按原相位算出下一次触发时间</text>

  <!-- keepalive -->
  <rect x="20" y="458" width="640" height="52" rx="10" fill="#f1f5f9"/>
  <text x="36" y="480" fill="#334155" font-weight="700">保活：每 27 分钟发一次空转消息</text>
  <text x="36" y="499" fill="#475569" font-size="12">Codex 会回收空闲 30 分钟的会话。没有任务到期时，也要在 27 分钟时“敲一下门”，让会话一直活着</text>
</svg>
```

### 投递的源码

右边四段代码分别是：拼出发给模型的消息文本 `Prompt()`；通道 1 的 `Probe()`，没有到期任务时只做一次索引查询；通道 2 的 `CodexQueue()`，直接执行二进制而不经过 shell，避免消息内容被当成命令注入；以及保活消息的原文。

> 保活消息定在 27 分钟，是为了赶在 Codex 回收空闲 30 分钟的会话之前刷新计时；它明确要求模型什么都不做，立刻结束这一轮。

```go title="stbenjam/cronex · internal/cronex/hooks.go (Lines 27-115) &amp; keepalive.go (Line 13)" raw
<span class="kw">const</span> deliveryPrefix = <span class="str">"Cronex delivery: "</span>

<span class="cmt">// ★ 1. 组装注入给 Codex LLM 的真实 Prompt 文本</span>
<span class="kw">func</span> <span class="fn">Prompt</span>(jobs []<span class="typ">Job</span>) <span class="typ">string</span> {
	<span class="kw">var</span> b strings.Builder
	b.<span class="fn">WriteString</span>(<span class="str">"Scheduled Cronex prompts are due in this session. Perform the tasks below within the user's existing instructions and permissions.\n"</span>)
	<span class="kw">for</span> _, j := <span class="kw">range</span> jobs {
		fmt.<span class="fn">Fprintf</span>(&amp;b, <span class="str">"\nCron %s"</span>, j.ID)
		<span class="kw">if</span> j.Name != <span class="str">""</span> {
			fmt.<span class="fn">Fprintf</span>(&amp;b, <span class="str">" (%s)"</span>, j.Name)
		}
		fmt.<span class="fn">Fprintf</span>(&amp;b, <span class="str">" — scheduled for %s:\n%s\n"</span>, j.NextRunAt.<span class="fn">Format</span>(time.RFC3339), j.Prompt)
	}
	<span class="kw">return</span> b.<span class="fn">String</span>()
}

<span class="cmt">// ★ 2. 通道一（Busy 态）：在每次工具调用结束时的 PostToolUse Hook 中顺风车注入</span>
<span class="cmt">// 绝不休眠、绝不拉起子进程；未到期时仅仅是一条索引 SELECT 查询！</span>
<span class="cmt">// 通过 hookSpecificOutput.additionalContext 附加在工具结果旁，不会覆盖原工具返回值！</span>
<span class="kw">func</span> <span class="fn">Probe</span>(ctx context.Context, s *<span class="typ">Store</span>, session <span class="typ">string</span>, out io.Writer, now time.Time) <span class="typ">error</span> {
	due, err := s.<span class="fn">Due</span>(ctx, session, now)
	<span class="kw">if</span> err != <span class="kw">nil</span> || !due {
		<span class="kw">return</span> err
	}
	d, err := s.<span class="fn">Claim</span>(ctx, session, <span class="str">""</span>, now)
	<span class="kw">if</span> err != <span class="kw">nil</span> || <span class="fn">len</span>(d.Jobs) == <span class="num">0</span> {
		<span class="kw">return</span> err
	}
	result := <span class="kw">map</span>[<span class="typ">string</span>]<span class="typ">any</span>{<span class="str">"hookSpecificOutput"</span>: <span class="kw">map</span>[<span class="typ">string</span>]<span class="typ">string</span>{
		<span class="str">"hookEventName"</span>: <span class="str">"PostToolUse"</span>, <span class="str">"additionalContext"</span>: <span class="fn">Prompt</span>(d.Jobs),
	}}
	<span class="kw">if</span> err := json.<span class="fn">NewEncoder</span>(out).<span class="fn">Encode</span>(result); err != <span class="kw">nil</span> {
		_ = s.<span class="fn">Complete</span>(ctx, d, <span class="kw">false</span>, now)
		<span class="kw">return</span> err
	}
	<span class="kw">return</span> s.<span class="fn">Complete</span>(ctx, d, <span class="kw">true</span>, now)
}

<span class="cmt">// ★ 3. 通道二（Idle 态）：后台 Stop/UserPromptSubmit --watch 进程通过 `codex queue` 唤醒会话</span>
<span class="kw">func</span> <span class="fn">CodexQueue</span>(binary <span class="typ">string</span>) <span class="typ">QueueFunc</span> {
	<span class="kw">return func</span>(ctx context.Context, session, prompt <span class="typ">string</span>) <span class="typ">error</span> {
		ctx, cancel := context.<span class="fn">WithTimeout</span>(ctx, <span class="num">10</span>*time.Second)
		<span class="kw">defer</span> <span class="fn">cancel</span>()
		<span class="cmt">// 直接 exec 二进制，绝不经过 shell，彻底杜绝 Prompt 里的命令注入风险！</span>
		cmd := exec.<span class="fn">CommandContext</span>(ctx, binary, <span class="str">"queue"</span>, <span class="str">"--thread"</span>, session, <span class="str">"--message"</span>, prompt)
		<span class="cmt">// Watch 投递时传入的 prompt 为：deliveryPrefix + d.Token + "\n" + Prompt(d.Jobs)</span>
		<span class="cmt">// ...</span>
	}
}

<span class="cmt">// ★ 4. 保活通道（KeepAlive = "27m"）：防止 Codex 30 分钟空闲回收器杀掉挂着长周期任务的会话</span>
<span class="kw">const</span> keepAlivePrompt = <span class="str">"Cronex keepalive only. No scheduled task is due in this message. Perform no action: do not use tools, check tasks, change files, or create schedules. End this turn immediately with a brief acknowledgement."</span>
```

### 漏掉的周期怎么补

假设一个任务从 00:00 开始每 10 分钟跑一次，而模型从 00:05 起忙了 25 分钟，错过了 00:10 和 00:20 两次。00:25 恢复投递时，cronex 只跑一次，不会连跑两次补齐。

下一次触发时间也不是“现在加 10 分钟”（00:35），而是回到原来的节拍上：从计划时间 00:10 出发，跳过已经错过的步数，得到 00:30。这样投递再晚，任务也不会越跑越偏。

```go title="stbenjam/cronex · internal/cronex/schedule.go (Lines 121-133)" raw
<span class="cmt">// Next coalesces missed runs and retains the original interval's phase.</span>
<span class="kw">func</span> (j <span class="typ">Job</span>) <span class="fn">Next</span>(now time.Time) (time.Time, <span class="typ">error</span>) {
	<span class="kw">if</span> !j.Recurring {
		<span class="kw">return</span> time.Time{}, <span class="kw">nil</span>
	}
	<span class="kw">if</span> j.EverySeconds &gt; <span class="num">0</span> {
		step := time.<span class="fn">Duration</span>(j.EverySeconds) * time.Second
		<span class="cmt">// 注意：不是 now + step，而是 j.NextRunAt + (错过的步数 + 1) * step！</span>
		<span class="cmt">// 严格保留初始创建时的时钟相位，绝不因投递延迟产生累积漂移！</span>
		<span class="kw">return</span> j.NextRunAt.<span class="fn">Add</span>((now.<span class="fn">Sub</span>(j.NextRunAt)/step + <span class="num">1</span>) * step), <span class="kw">nil</span>
	}
	s, err := <span class="fn">schedule</span>(j.Cron, j.Timezone)
	<span class="kw">if</span> err != <span class="kw">nil</span> {
		<span class="kw">return</span> time.Time{}, err
	}
	<span class="kw">return</span> s.<span class="fn">Next</span>(now).<span class="fn">UTC</span>(), <span class="kw">nil</span>
}
```

## pr-loop：一周长的任务怎么排

`stbenjam/skills` 里的 `pr-loop` Skill 展示了怎么用这些工具驱动一个持续数天的任务：守着一个 PR，直到合并或超时。它有两个要点：

- **两个定时任务**：一个动态任务，从每 10 分钟开始，随时间逐步放宽间隔；一个固定的 8 小时守望任务，只在最后才删除。
- **状态写进 prompt**：PR 链接、工作目录、最初的开始时间都写在任务的 prompt 里。下次唤醒时模型从 prompt 里读回来，退避进度和一周上限不会因为重启而清零。

```md title="stbenjam/skills · plugins/loops/skills/pr-loop/references/scheduling.md (Verbatim)"
# PR Loop Scheduling

## Select a backend and create schedules (Step 1.7)

**Prefer native T3 scheduled tasks via MCP.** Look for `schedule_task`, `list_scheduled_tasks`, `update_scheduled_task`, and `delete_scheduled_task` (tool names may have a prefix such as `mcp__t3_code__`). If they are missing from the tool catalog, attempt MCP discovery or one read-only `list_scheduled_tasks` call before concluding they are absent. Use native `CronList`, `CronCreate`, and `CronDelete` **only when T3 scheduling tools are absent**.

Use the selected backend throughout the run, including project overrides. Maintain two recurring schedules:

1. **Dynamic** — initially every 10 minutes; adjust its interval as the backoff schedule progresses (Step 5.3).
2. **Watcher** — every 8 hours, as a safety net if the dynamic schedule fails. Keep it until termination (Step 5.4).

Identify each schedule by the full PR URL and its role, e.g. `pr-loop dynamic: <pr-url>` and `pr-loop watcher: <pr-url>`. List existing schedules first and reuse matching ones; create only missing roles. Include the PR URL, worktree path, original start timestamp (Step 1.5), and instructions to resume this skill in each prompt. On resumed runs, preserve that timestamp so the backoff and one-week limit do not restart.

**Cron* fallback:** use `CronList` to find matching jobs and `CronCreate` to create the missing dynamic/watcher schedules. Retain their job IDs for rescheduling and cleanup.

## Reschedule (Step 5.2)

Adjust only the **dynamic** schedule to the appropriate interval:
- **T3:** call `update_scheduled_task` with its `scheduledTaskId` and `schedule: {"type":"interval","everyMs":<interval-ms>}`.
- **Cron* fallback:** find the dynamic job with `CronList`, delete it with `CronDelete`, and recreate it with `CronCreate` at the new interval; retain the replacement ID.

Leave the 8-hour watcher schedule unchanged.

## Cleanup (Step 5.4)

Delete both the dynamic and watcher schedules using the selected backend: T3 `delete_scheduled_task` with each `scheduledTaskId`, or fallback `CronDelete` with each job ID.
```

间隔按距开始的时间放宽：第 1 小时每 10 分钟查一次，到 6 小时内每 30 分钟，到 24 小时内每 4 小时，之后每 8 小时；24 小时和 48 小时各通知你一次，满一周结束。

```md title="stbenjam/skills · plugins/loops/skills/pr-loop/SKILL.md (Step 5.3 指数退避表 · Verbatim)"
#### Step 5.3: Exponential backoff

Interval based on time since Step 1.5. Project references may override.

| Elapsed        | Interval | Action                    |
|----------------|----------|---------------------------|
| 0 – 1 hr       | 10 min   | Quick feedback loops      |
| 1 – 6 hr       | 30 min   | Waiting on CI / reviewers |
| 6 – 24 hr      | 4 hr     | Longer wait               |
| 24 hr          | —        | Notify user               |
| 24 – 48 hr     | 8 hr     | Low-frequency check-ins   |
| 48 hr          | —        | Notify user again         |
| 48 hr – 1 week | 8 hr     | Maintenance mode          |
| 1 week         | —        | **Terminate**             |
```

### 为什么要多一个 8 小时的守望任务

用 `CronCreate` / `CronDelete` 调整间隔时，必须先删掉旧任务、再建新任务。如果在这两步之间会话中断、模型漏建了新任务，或者某次触发异常退出，唯一的定时任务就断了，再也不会被叫醒。

那个永远不改的 8 小时任务就是看门狗：动态任务就算断了，最多 8 小时后它也会叫醒 Agent，把动态任务修好。

## 四种实现放在一起对比

| 对比项 | Claude Code v2.1.325 | pi-loop | Prime Agent | cronex（Codex CLI） |
| --- | --- | --- | --- | --- |
| 调度在哪里运行 | CLI 进程内，每秒检查一次 | Pi 进程内的插件，内存里的状态为准 | 独立守护进程 + Worker 进程 | 独立 Go 程序：MCP 服务器 + 7 个钩子 + SQLite |
| 谁来创建 | `/loop` Skill 展开指令，模型调用 `CronCreate` / `ScheduleWakeup` | 你输入 `/loop`，或模型调用 `schedule_loop` | 你输入 `/heartbeat`，或模型调用 `rlm_heartbeat.create()` | 模型调用 MCP 工具 `CronCreate` |
| 到期后怎么进对话 | 会话记录加系统标记，prompt 作为元消息排在你的消息之后 | 包进 `<scheduled_trigger>` 用 `sendUserMessage` 发出 | 心跳消息经 `convert_to_llm()` 变成 user 消息 | 忙时 `additionalContext`，闲时 `codex queue` |
| 模型忙时 | 等空闲，不打断 | 默认等空闲；带 `!` 时插队 | 默认插队（Steer）；可改为等空闲 | 不打断，在下一次工具调用后附上 |
| 防止堆积 | 同一任务已在队列就跳过；连续空转折叠 | 待发区按循环 ID 覆盖；空闲时 50ms 合并 | 队列里按心跳 ID 替换旧消息 | 30 秒租约 + 投递回执 + 漏掉的周期按原相位合并 |
| 省 token 的设计 | 赶在 5 分钟缓存过期前触发；长指令只发一次 | 没有循环时不加 `<loop_policy>`；`shell` / `notify` 不叫醒模型 | 内部记账消息不发给模型，不扰动请求前缀 | 等待期间进程休眠；没到期时只做一次索引查询 |
| 生命周期与保活 | 硬编码 3 天自动过期；20 分钟 keepalive 防容器回收 | 没有固定过期时间，靠 `maxFires` 自停；会话级循环随会话结束，全局循环跨重启保留 | 目标会话删除或归档时自动取消；失败后指数退避（最长 1 小时） | 可选 `expires_at`；每 27 分钟保活，防止 Codex 30 分钟回收 |

> 表中 Claude Code 一列的“3 天自动过期”和“20 分钟 keepalive 防容器回收”沿用原报告的写法，但和上一篇的源码不一致：上一篇里 `CronCreate` 提示词和 `PQ.recurringMaxAgeMs` 都是 7 天；1200 秒的 keepalive（`X1i()`）是在模型忘记续期时兜底的。以哪个为准需要再核实。

## 小结：选插入点，就是在及时和稳定之间取舍

- **插入点决定体验**：插进当前回合（Steer、PostToolUse）更及时，但会改变模型正在做的事；等空闲（Claude Code、FollowUp、`codex queue`）更稳，但要等当前任务做完。
- **都要防堆积**：pi-loop 按循环 ID 覆盖，Prime Agent 按心跳 ID 替换，cronex 用租约和回执，Claude Code 折叠空转记录。思路一致：同一个任务在等待中只保留一份。
- **都要告诉模型“这不是用户说的”**：`<scheduled_trigger>`、`[heartbeat: …]` 消息头、`isMeta` 元消息、cronex 消息的固定开场白，作用相同。
- **外部工具也能做定时**：cronex 证明了，宿主没有调度器时，靠 MCP 加钩子加外部队列命令也能拼出完整能力，代价是要自己处理租约、回执和保活。
