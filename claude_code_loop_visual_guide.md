---
title: Claude Code `/loop` 是怎么工作的：从一条命令到每次定时唤醒
kicker: 源码拆解
deck: 基于 Claude Code v2.1.325 二进制包还原的源码和提示词原文，讲清 /loop 如何解析参数、如何预约下一次、唤醒时消息怎么回到对话，以及这些设计如何配合 5 分钟的 Prompt Cache。
meta:
  - Claude Code v2.1.325
  - 源码与提示词原文
footer: 源码出处：Claude Code v2.1.325 生产环境二进制包（`/$bunfs/root/chunk-*.js`）、Prime Agent `crates/pa-daemon`；调度器与队列处理器两小节来自本机 v2.1.314。代码中的关键符号名为还原所得，提示词为原文。
index: index.html
updated: 2026-11-17
created: 2026-11-17
---

`/loop` 让 Claude Code 定时重复做一件事：盯着 CI、守着一个 PR、隔一阵检查一次部署。它看起来像个定时器，实际上是一个 Skill 加两个调度工具，再加上一套把消息塞回对话的机制。

本文基于本机安装的 Claude Code v2.1.325 二进制包。代码里的关键符号名是从压缩代码中还原的，提示词是原文。我们从你敲下 `/loop` 开始，一路讲到每次唤醒时消息怎么回到对话、为什么这样设计能省钱，最后和其他 Agent 的做法对照。三种常见写法：

- `/loop 5m check CI`：每 5 分钟检查一次，间隔固定
- `/loop check CI`：不写间隔，由模型根据情况自己决定多久后再查
- `/loop`：什么都不写，按 loop.md 或内置的自主巡检指令工作

## 先看全貌：一条命令如何变成反复执行的循环

整个过程分两段。**前三步只发生一次**：你输入命令，Skill 把它展开成一份详细指令，模型照着指令马上执行一次，并且自己预约下一次。

**后两步反复发生**：定时器到期，Claude Code 把本轮要做的事作为新消息放回对话；模型再执行一次，然后决定继续预约还是停止。

值得注意的是，“预约下一次”这件事是模型通过调用工具完成的，不是 Claude Code 自己默默重复。所以模型可以根据看到的情况调整节奏，也可以主动结束循环。

```svg title="图 1：/loop 的完整生命周期" tone="light"
<svg viewBox="0 0 680 470" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif">
  <defs>
    <marker id="life-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 1 L10 5 L0 9 z" fill="#64748b"/>
    </marker>
  </defs>

  <!-- 1 -->
  <rect x="20" y="12" width="600" height="64" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="48" cy="44" r="14" fill="#2457d6"/>
  <text x="48" y="49" text-anchor="middle" fill="#fff" font-size="14" font-weight="700">1</text>
  <text x="74" y="38" fill="#111827" font-size="15" font-weight="700">你输入 /loop 命令</text>
  <text x="74" y="60" fill="#475569" font-size="12.5">例如 /loop 5m check CI、/loop check CI，或者只输入 /loop</text>
  <line x1="320" y1="76" x2="320" y2="96" stroke="#64748b" stroke-width="2" marker-end="url(#life-arrow)"/>

  <!-- 2 -->
  <rect x="20" y="100" width="600" height="64" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="48" cy="132" r="14" fill="#2457d6"/>
  <text x="48" y="137" text-anchor="middle" fill="#fff" font-size="14" font-weight="700">2</text>
  <text x="74" y="126" fill="#111827" font-size="15" font-weight="700">Skill 把命令展开成一整份指令</text>
  <text x="74" y="148" fill="#475569" font-size="12.5">getPromptForCommand() 生成指令文本，作为你的 user 消息发给模型</text>
  <line x1="320" y1="164" x2="320" y2="184" stroke="#64748b" stroke-width="2" marker-end="url(#life-arrow)"/>

  <!-- 3 -->
  <rect x="20" y="188" width="600" height="64" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="48" cy="220" r="14" fill="#2457d6"/>
  <text x="48" y="225" text-anchor="middle" fill="#fff" font-size="14" font-weight="700">3</text>
  <text x="74" y="214" fill="#111827" font-size="15" font-weight="700">模型马上执行一次，并自己预约下一次</text>
  <text x="74" y="236" fill="#475569" font-size="12.5">固定间隔：调用 CronCreate 注册周期任务　·　动态：调用 ScheduleWakeup 约一次唤醒</text>
  <line x1="320" y1="252" x2="320" y2="272" stroke="#64748b" stroke-width="2" marker-end="url(#life-arrow)"/>

  <!-- 4 -->
  <rect x="20" y="276" width="600" height="64" rx="12" fill="#f8fafc" stroke="#94a3b8" stroke-width="1.5"/>
  <circle cx="48" cy="308" r="14" fill="#1f2937"/>
  <text x="48" y="313" text-anchor="middle" fill="#fff" font-size="14" font-weight="700">4</text>
  <text x="74" y="302" fill="#111827" font-size="15" font-weight="700">定时器到期，消息回到对话里</text>
  <text x="74" y="324" fill="#475569" font-size="12.5">会话记录末尾追加一行系统标记，本轮 prompt 进入消息队列</text>
  <line x1="320" y1="340" x2="320" y2="360" stroke="#64748b" stroke-width="2" marker-end="url(#life-arrow)"/>

  <!-- 5 -->
  <rect x="20" y="364" width="600" height="64" rx="12" fill="#f8fafc" stroke="#94a3b8" stroke-width="1.5"/>
  <circle cx="48" cy="396" r="14" fill="#1f2937"/>
  <text x="48" y="401" text-anchor="middle" fill="#fff" font-size="14" font-weight="700">5</text>
  <text x="74" y="390" fill="#111827" font-size="15" font-weight="700">Claude 空闲时再执行一次，然后决定继续还是停</text>
  <text x="74" y="412" fill="#475569" font-size="12.5">继续：再预约一次　·　停止：ScheduleWakeup(stop: true)、CronDelete，或 7 天后自动过期</text>

  <!-- repeat loop 5 → 4 -->
  <path d="M620 396 H652 V308 H626" fill="none" stroke="#2457d6" stroke-width="1.8" stroke-dasharray="5 4" marker-end="url(#life-arrow)"/>
  <text x="660" y="356" fill="#2457d6" font-size="12" font-weight="600" writing-mode="tb">重复</text>

  <text x="20" y="458" fill="#64748b" font-size="12">第 1–3 步只发生一次；第 4、5 步随定时器反复执行，直到任务完成或被停止。</text>
</svg>
```

### 名词表

后文会反复用到下面这些名词，先在这里统一解释。

| 名词 | 是什么 | 在 /loop 里起什么作用 |
| --- | --- | --- |
| Skill | Claude Code 里可以用斜杠命令调用的一段预置能力，本质是一份写给模型的指令。 | `/loop` 就是一个 Skill，名字叫 `loop`，别名 `proactive`。 |
| Transcript（会话记录） | 当前会话按时间排列的全部消息：你说的话、模型的回复、工具调用和结果、系统记录行。可以理解成聊天记录。 | 每次唤醒都是往它的**末尾追加**新消息，前面的内容不动。 |
| Turn（一轮） | 模型收到一条 user 消息，到它回复完毕的一个来回，中间可能调用多次工具。 | 每次唤醒都会开启新的一轮。 |
| Tick / Wakeup（一次唤醒） | 定时器到期、Loop 执行一次。 | 循环就是一次次唤醒串起来的。 |
| `CronCreate` | 一个工具：按 cron 表达式注册定时任务，可以是周期任务，也可以只触发一次。 | 固定间隔模式用它。周期任务 7 天后自动过期。 |
| `ScheduleWakeup` | 一个工具：约定多少秒后叫醒自己一次，只叫一次。 | 动态模式用它。每轮结束前要再调用一次，循环才能继续。 |
| `Monitor` | 一个工具：盯住日志、进程或命令输出，有事件就立刻通知模型。 | 等待“CI 跑完”这类事件时用它，定时唤醒退为兜底。 |
| 系统标记行 | 会话记录里 `type: "system"`、`subtype: "scheduled_task_fire"` 的一行，终端显示为 “Claude resuming /loop wakeup (时间)”。 | 标出“从这里开始是一次定时唤醒”。 |
| 元消息（`isMeta`） | 以 user 身份发给模型、但由程序生成的消息，不是你亲手输入的。 | 每次唤醒带来的 prompt 都是元消息。 |
| 消息队列 `ae` | 等待发给模型的消息排队的地方，Claude 空闲时按顺序取出。`enqueue` 就是“放进队列”。 | 定时 prompt 先进队列，不会打断正在进行的回复。 |
| 空转（`noop: true`） | 模型调用 `ScheduleWakeup` 时报告“这一轮检查完什么都没做”。 | 连续空转的记录会被折叠。 |
| 折叠（Fold） | 把上一次空转唤醒的记录标为已折叠，用一句总结代替。`noOpStreak` 是连续空转次数。 | 避免“检查了、没变化”的记录越积越多。 |
| 哨兵（Sentinel） | 形如 `<<loop.md>>` 的短标记，到点时才被替换成真正的指令内容。 | 避免每次唤醒都重发一遍长指令。 |
| Prompt Cache（前缀缓存） | Anthropic API 会缓存请求开头没变的部分，默认 5 分钟有效。命中时更快、更便宜。 | 很多数字（270 秒、285 秒、1200 秒）都是围绕这 5 分钟设计的。 |
| Jitter（抖动） | 在触发时间上加一个偏移量，让大量任务不在同一秒触发。 | 周期任务最多延后半个周期，上限 30 分钟。 |
| Keepalive（兜底唤醒） | 模型忘记预约下一次时，系统替它约一次 20 分钟后的唤醒。 | 只兜底一次，再忘就结束循环。 |

## 三种用法：/loop 后面写什么，决定它怎么跑

`/loop` 的参数写法决定了模式。Skill 入口只看参数的形态，选一份对应的指令发给模型：

| 你输入的 | 模式 | 模型用什么预约下一次 | 生成指令的函数 |
| --- | --- | --- | --- |
| `/loop 5m check CI`<br>`/loop check CI every 20m` | 固定间隔 | `CronCreate` 注册周期任务 | `M()` |
| `/loop check CI` | 动态：模型自己决定间隔 | 每轮调用 `ScheduleWakeup` | `M()` |
| `/loop` | 自主巡检，动态间隔 | `ScheduleWakeup` + 哨兵 | `w(p, true)` |
| `/loop 15m` | 自主巡检，固定间隔 | `CronCreate` + 哨兵 | `w(p, false)` |

### 入口函数怎么判断

入口是 `registerLoopSkill()` 注册的 `getPromptForCommand(e, t)`。它只做形态判断：`o` 是去掉首尾空白的参数，`c = !o` 表示什么都没写，`m` 表示只写了时间间隔。

`c` 或 `m` 为真时，先读取 `.claude/loop.md` 或 `~/.claude/loop.md`，再交给 `w()` 生成自主巡检指令；否则交给 `M()`，在指令里让模型自己判断是固定间隔还是动态。返回值是一条 `role: "user"` 的文本消息：你敲的一行 `/loop`，在模型眼里变成了一整页说明书。

另外两个细节。第一，模型也能通过 Skill 工具主动调用 `/loop`，`whenToUse` 写明了适用场景，比如“每 5 分钟检查一次部署”。第二，如果这次调用带着 `modelScheduledOrigin` 标记，说明是定时唤醒重新进入，就跳过遥测上报和 `bms()` 状态重置。

```js title="真实源码：/$bunfs/root/chunk-vsbr50qd.js :: registerLoopSkill()" raw
<span class="cm">// 提取自 Claude Code v2.1.325 二进制包 (已还原关键符号名)</span>
<span class="kw">function</span> <span class="fn">registerLoopSkill</span>() {
  <span class="fn">registerBundledSkill</span>({
    name: <span class="str">"loop"</span>,
    menuDescription: <span class="str">"Repeat a prompt or command on an interval (e.g. /loop 5m /foo)"</span>,
    aliases: [<span class="str">"proactive"</span>],
    description: <span class="str">"Run a prompt or slash command on a recurring interval (e.g. /loop 5m /foo). Omit the interval to let the model self-pace."</span>,
<span class="hl-line">    whenToUse: <span class="str">'When the user wants to set up a recurring task, poll for status, or run something repeatedly on an interval (e.g. "check the deploy every 5 minutes", "keep running /babysit-prs"). Do NOT invoke for one-off tasks.'</span>,</span>
    argumentHint: <span class="str">"[interval] [prompt]"</span>,
    userInvocable: <span class="num">true</span>,
    argsMayContainSlashCommands: <span class="num">true</span>,
    isEnabled: isKairosCronEnabled, <span class="cm">// AL() -> checks tengu_kairos_cron</span>

    <span class="kw">async</span> <span class="fn">getPromptForCommand</span>(e, t) {
      <span class="kw">let</span> o = e.<span class="fn">trim</span>();
      <span class="kw">let</span> r = t.options?.mainLoopModel;
      <span class="kw">let</span> s = r !== <span class="kw">undefined</span> &amp;&amp; <span class="fn">needsPreArmStatus</span>(<span class="fn">resolveModel</span>(r), r);
      <span class="kw">let</span> n = <span class="fn">isCloudSession</span>() &amp;&amp; !t.options?.modelScheduledOrigin &amp;&amp; !t.options?.isSkillPreload
        ? <span class="str">`\n## This is a cloud session\nA timer set here lives in this session's container...`</span> : <span class="str">""</span>;

      <span class="cm">// 记录遥测事件 tengu_loop_command</span>
      <span class="kw">if</span> (!t.options?.isSkillPreload &amp;&amp; !t.options?.modelScheduledOrigin) {
        <span class="fn">logTelemetry</span>(<span class="str">"tengu_loop_command"</span>, {
          has_args: o.length &gt; <span class="num">0</span>,
          is_interval_only: INTERVAL_RE.<span class="fn">test</span>(o) || EVERY_CLAUSE_RE.<span class="fn">test</span>(o)
        });
      }

      <span class="kw">let</span> d = o.<span class="fn">match</span>(EVERY_CLAUSE_RE);
      <span class="kw">let</span> c = !o;                       <span class="cm">// 裸 /loop（无任何参数）</span>
      <span class="kw">let</span> m = INTERVAL_RE.<span class="fn">test</span>(o) || d !== <span class="num">null</span>; <span class="cm">// 仅传了时间间隔，如 /loop 15m</span>

      <span class="kw">if</span> (c || m) {
        <span class="kw">let</span> u = d ? <span class="fn">extractEveryInterval</span>(d) : (o || DEFAULT_INTERVAL);
        <span class="kw">let</span> p = <span class="kw">await</span> loopFile.<span class="fn">readLoopFileAsync</span>(t.storageV5); <span class="cm">// 读取 loop.md</span>
        <span class="kw">if</span> (!t.options?.isSkillPreload &amp;&amp; !t.options?.modelScheduledOrigin) {
          <span class="fn">bms</span>(); <span class="cm">// 重置 loop 终止位与 keepalive 计数器</span>
        }
<span class="hl-line">        <span class="cm">// c == true 走动态自调速 w(p, true, ...)，否则走固定 Cron w(p, false, ...)</span></span>
        <span class="kw">return</span> [{ type: <span class="str">"text"</span>, text: <span class="fn">w</span>(p, c, u, s, n) }];
      }

      <span class="kw">if</span> (!t.options?.isSkillPreload &amp;&amp; !t.options?.modelScheduledOrigin) <span class="fn">bms</span>();
<span class="hl-line">      <span class="cm">// 带有用户自定义 prompt：调用 M(o, s, n+h) 构造完整指令</span></span>
      <span class="kw">return</span> [{ type: <span class="str">"text"</span>, text: <span class="fn">M</span>(o, s, n + h) }];
    }
  });
}
```

## 模型收到的指令：四份提示词原文

下面四份提示词都是二进制包里的原文，一字未改。每份先用几句话说明它让模型做什么，右边是原文。原文里蓝色底的行是关键的行为约束，橙色底的是让循环能续上的那一行。

### 带任务的 /loop：M() 生成的指令

输入 `/loop 5m check deploy` 或 `/loop check deploy` 时，模型收到这份指令。它要求模型：

1. **先解析输入**：开头是 `5m` 这样的间隔，或者结尾是 `every 20m`，就是固定间隔；否则进入动态模式。
2. **固定间隔**：把间隔换算成 cron 表达式，调用 `CronCreate`，告诉你任务 ID 和 7 天自动过期，然后**马上执行一次**，不等第一次定时触发。
3. **动态模式**：先执行一次。如果下一次要等某个事件（CI 跑完、日志出现某行），挂一个 `Monitor`。需要继续就调用 `ScheduleWakeup`，并且 `prompt` 必须是 `/loop ` 加上原始输入，这样下次唤醒才会重新进入这个 Skill。这一轮没做事就标 `noop: true`；任务完成就用 `stop: true` 结束。

```prompt title="函数 M(e, t, o) 生成的真实 Skill Prompt（当用户输入 `/loop 5m check deploy` 或 `/loop check deploy` 时注入）" raw
<span class="pr-h"># /loop — schedule a recurring or self-paced prompt</span>

Parse the input below into `[interval] &lt;prompt…&gt;` and schedule it.

<span class="pr-h">## Parsing (in priority order)</span>

1. <span class="pr-b">**Leading token**</span>: if the first whitespace-delimited token matches `^\d+[smhd]$` (e.g. `5m`, `2h`), that's the interval; the rest is the prompt.
2. <span class="pr-b">**Trailing "every" clause**</span>: otherwise, if the input ends with `every &lt;N&gt;&lt;unit&gt;` or `every &lt;N&gt; &lt;unit-word&gt;` (e.g. `every 20m`, `every 5 minutes`, `every 2 hours`), extract that as the interval and strip it from the prompt. Only match when what follows "every" is a time expression — `check every PR` has no interval.
3. <span class="pr-b">**No interval**</span>: otherwise, the entire input is the prompt and you'll self-pace dynamically (see "Dynamic mode" below).

If the resulting prompt is empty, show usage `/loop [interval] &lt;prompt&gt;` and stop.

Examples:
- `5m /babysit-prs` → interval `5m`, prompt `/babysit-prs` (rule 1)
- `check the deploy every 20m` → interval `20m`, prompt `check the deploy` (rule 2)
- `run tests every 5 minutes` → interval `5m`, prompt `run tests` (rule 2)
- `check the deploy` → no interval → dynamic mode, prompt `check the deploy` (rule 3)
- `check every PR` → no interval → dynamic mode, prompt `check every PR` (rule 3 — "every" not followed by time)
- `5m` → empty prompt → show usage

<span class="pr-h">## Fixed-interval mode (rules 1 and 2)</span>

Convert the interval to a cron expression:
- Supported suffixes: `s` → ceil to nearest minute, `m` (minutes), `h` (hours), `d` (days).
- Examples: `5m` → `*/5 * * * *`, `1h` → `0 * * * *`, `1d` → `0 0 * * *`. If the interval doesn't cleanly divide its unit, round to the nearest clean interval and tell the user what you rounded to.

Then:
<span class="hl-line">1. Call `CronCreate` with: `cron` (the expression above), `prompt` (the parsed prompt verbatim), `recurring: true`.</span>
2. Briefly confirm: what's scheduled, the cron expression, the human-readable cadence, that recurring tasks auto-expire after 7 days, and that the user can cancel sooner with `CronDelete` (include the job ID).
<span class="hl-line">3. **Then immediately execute the parsed prompt now** — don't wait for the first cron fire. If it's a slash command, invoke it via the Skill tool; otherwise act on it directly.</span>

<span class="pr-h">## Dynamic mode (rule 3 — no interval)</span>

The user wants you to self-pace. Decide what makes the next iteration worth running — a passage of time, or an observable event.

1. <span class="pr-b">**Run the parsed prompt now.**</span> If it's a slash command, invoke it via the Skill tool; otherwise act on it directly.
2. <span class="pr-b">**If the next run is gated on an event**</span> (CI finishing, a log line matching, a file changing, a PR comment) and no `Monitor` is already running for it: arm a `Monitor`. Its events arrive as `&lt;task-notification&gt;` messages and wake this loop immediately — you do not wait for the `ScheduleWakeup` deadline. Arm once; on later iterations call `TaskList` first and skip this step if a monitor is already running.
3. <span class="pr-b">**Decide whether the loop continues.**</span> If the task needs another iteration, call `ScheduleWakeup` with:
   - `delaySeconds`: with a `Monitor` armed this is the **fallback heartbeat** — how long to wait if no event fires (lean 1200–1800s; idle ticks more frequent than the task needs are pure overhead). Without a `Monitor` this is the cadence — pick based on what you observed. Read the tool's own description for cache-aware delay guidance.
   - `reason`: one short sentence on why you picked that delay.
<span class="hl-amber">   - `prompt`: the full original /loop input verbatim, prefixed with `/loop ` so the next firing re-enters this skill and continues the loop. For example, if the user typed `/loop check the deploy`, pass `/loop check the deploy` as the prompt.</span>
<span class="hl-line">   - `noop`: `true` if this tick changed nothing ("still waiting", "quiet hold"); `false` if it did something worth keeping. Consecutive `noop: true` ticks collapse in the terminal.</span>
   If it doesn't need another iteration, stop instead (step 6) — re-arming is a per-turn choice, not a default.
4. <span class="pr-b">**After the wakeup is armed, briefly confirm**</span>: that you're self-pacing, whether a `Monitor` is the primary wake signal, that you ran the task now, and what fallback delay you picked. This must be ordinary visible response text — the user cannot see your thinking/reasoning, so an update written only there is invisible to them. Make it the last thing in the turn, then end the turn.
5. <span class="pr-b">**If you were woken by a `&lt;task-notification&gt;`**</span> rather than this prompt: handle the event in the context of the loop task, then make the same decision. If the loop should continue, call `ScheduleWakeup` again with the same `prompt` and the same 1200–1800s `delaySeconds` from the schedule step above (the `Monitor` remains the wake signal; the new wakeup is only the fallback heartbeat), then write the same brief update as visible text. If the event means the work is finished, stop (step 6).
6. <span class="pr-b">**To stop the loop**</span> — the task is complete, further iterations can't make progress, or the user asked you to stop — call `ScheduleWakeup` with `stop: true` (no other fields) and `TaskStop` any `Monitor` you armed (use `TaskList` to find the task ID if it is no longer in context). Stopping is the loop's normal ending — the user can restart it anytime with /loop.

<span class="pr-h">## Input</span>

${user_raw_input}
```

### 只输入 /loop：动态自主巡检

没有任务也没有间隔时，模型立刻按自主巡检指令检查一次，再根据观察到的情况决定下次间隔：事情多就短一点，安静就长一点。

和上一份最大的区别在 `ScheduleWakeup` 的 `prompt`：这里填的是哨兵 `<<autonomous-loop-dynamic>>`（有 loop.md 时是 `<<loop.md-dynamic>>`），明确要求**不要**填全文，展开由运行时负责。原文末尾的占位处会内联 loop.md 或内置巡检指令。

```prompt title="函数 w(loopFile, isDynamic=true) 生成的真实 Prompt（当用户直接输入裸 `/loop` 时注入）" raw
<span class="pr-h"># /loop — autonomous default with dynamic pacing</span>
<span class="cm">(若存在 .claude/loop.md 则标题为: # /loop — loop.md tasks with dynamic pacing)</span>

The user invoked `/loop` with no prompt and no interval. Run the autonomous check now, then self-pace the next iteration via `ScheduleWakeup` — no cron.

<span class="pr-h">## Action</span>

1. <span class="pr-b">**Run the autonomous check now**</span>, following the instructions inlined below.
2. <span class="pr-b">**If the next tick is gated on an event**</span> (CI finishing, a PR comment, a log line) and no `Monitor` is already running for it: arm a `Monitor`. Its events wake this loop immediately — you do not wait for the `ScheduleWakeup` deadline. Arm once; on later ticks call `TaskList` first and skip if a monitor is already running.
3. <span class="pr-b">**Decide whether the loop continues.**</span> If the next check is worth running, call `ScheduleWakeup` with:
   - `delaySeconds`: with a `Monitor` armed this is the fallback heartbeat (lean 1200–1800s). Without one, pick based on what you observed this turn — quiet branch? wait longer. Lots in flight? wait shorter. Read the tool's own description for cache-aware delay guidance.
   - `reason`: one short sentence on why you picked that delay.
<span class="hl-amber">   - `prompt`: the literal string `&lt;&lt;autonomous-loop-dynamic&gt;&gt;` (或 `&lt;&lt;loop.md-dynamic&gt;&gt;`) — the dynamic-mode sentinel expands at fire time to the full instructions (first fire / first fire post-compact / loop.md edited) or a dynamic-pacing-specific short reminder (subsequent fires). Do not pass the full instructions; that is handled automatically.</span>
   - `noop`: `true` if this tick changed nothing ("still waiting", "quiet hold"); `false` if it did something worth keeping. Consecutive `noop: true` ticks collapse in the terminal.
   If it isn't, stop instead (step 6) — re-arming is a per-turn choice, not a default.
4. <span class="pr-b">**After the wakeup is armed, briefly confirm**</span>: that this is the autonomous default in dynamic-pacing mode, that you ran the check now, whether a `Monitor` is the primary wake signal, and what fallback delay you picked.
5. <span class="pr-b">**If woken by a `&lt;task-notification&gt;`**</span> rather than this prompt: handle the event, then make the same decision.
6. <span class="pr-b">**To stop the loop**</span> — the task is complete, further iterations can't make progress, or the user asked you to stop — call `ScheduleWakeup` with `stop: true` (no other fields) and `TaskStop` any `Monitor` you armed.

<span class="pr-h">## Autonomous-loop instructions (for the immediate execution and every fire)</span>

${loop.md 内容 或 loopAutonomousPreamble-07qcyhv4.md 全文}
```

### 只输入 /loop 15m：固定间隔的自主巡检

只给了间隔时，模型把间隔换算成 cron，调用 `CronCreate`，`prompt` 填哨兵 `<<autonomous-loop>>` 或 `<<loop.md>>`，确认后立刻执行第一次检查。

```prompt title="函数 w(loopFile, isDynamic=false) 生成的真实 Prompt（当用户输入 `/loop 15m` 只带时间不带描述时注入）" raw
<span class="pr-h"># /loop — schedule the autonomous default</span>

The user invoked `/loop` with no prompt (input was empty or just the interval `${interval}`). Schedule the autonomous-loop default and then run the first autonomous check immediately.

<span class="pr-h">## Action</span>

1. Convert `${interval}` to a 5-field cron expression. Supported suffixes: `s` → ceil to nearest minute, `m` (minutes), `h` (hours), `d` (days). Examples: `5m` → `*/5 * * * *`, `1h` → `0 * * * *`, `1d` → `0 0 * * *`. If the interval doesn't cleanly divide its unit, round to the nearest clean interval and tell the user what you rounded to.
2. Call `CronCreate` with:
   - `cron`: the expression from step 1
<span class="hl-amber">   - `prompt`: the literal string `&lt;&lt;autonomous-loop&gt;&gt;` (或 `&lt;&lt;loop.md&gt;&gt;`) — it expands at fire time to the full autonomous-loop instructions on first delivery, and to a short reminder on subsequent fires (the long instructions stay in the cached message-prefix).</span>
   - `recurring`: `true`
3. Briefly confirm: what's scheduled, the cron expression, the human-readable cadence, that recurring tasks auto-expire after 7 days, and that they can cancel sooner with `CronDelete` (include the job ID). Mention this is the autonomous default and that the autonomous-loop instructions are baked in.
4. <span class="pr-b">**Then immediately run the autonomous check now**</span>, following the instructions inlined below. Don't wait for the first cron fire.

<span class="pr-h">## Autonomous-loop instructions (for the immediate execution and every fire)</span>

${loop.md 内容 或 loopAutonomousPreamble-07qcyhv4.md 全文}
```

### 内置的自主巡检指令

没有 loop.md 时，裸 `/loop` 用的就是这份内置指令。它给自主巡检划了清楚的边界：

- **角色**：你是管家，不是发起人。只推进用户已经开始的工作，不自己找新活。
- **先看什么**：对话里没做完的事，尤其是一起在做的 PR（评审意见、失败的 CI、合并冲突）；然后是做了一半的实现和说过“接下来我会……”的承诺；最后才去看当前分支 PR 的状态。
- **能不能动手**：可逆的操作（本地修改、跑测试）可以直接做；不可逆的（push、删除、发送）要等用户确认。
- **没事可做时**：一句话说明后结束，不要罗列检查过什么。连续三次都没事，就只做一次快速 CI 检查。

```prompt title="内置自主巡检指令原文：/$bunfs/root/loopAutonomousPreamble-07qcyhv4.md" raw
<span class="pr-h"># Autonomous loop check</span>

You're being invoked on a timer while the user is away or occupied. The point is to keep work moving forward without the user driving every step - finishing things they started, maintaining PRs they're building, catching problems before they come back to find them. You're a steward, not an initiator. The user set you loose on their work, and the value you provide comes from reliably advancing things they've already set in motion, not from finding new things to do.

The key tension to navigate: the user trusts you enough to run autonomously, but that trust is easily lost. Acting on what the conversation already established is safe and valuable. Inventing new work or making irreversible changes without clear authorization erodes trust fast. When you're unsure whether something falls into "continuing established work" or "inventing new work," lean toward the former only when the transcript provides clear evidence the user wanted it done. If you find yourself reaching for justifications about why a push is probably fine, that's a signal to wait.

<span class="pr-h">## What to act on</span>

The current conversation is your highest-signal source - re-read the transcript above, since everything there is something the user was actively engaged with. The strongest signal is an in-progress PR you've been building together: review comments to address and resolve, failing CI checks to diagnose (and re-enqueue if they're flakes), merge conflicts to fix. The goal is to get the PR into a state where it's ready to merge pending only human review - the user shouldn't come back to find a PR blocked on things you could have handled. After that, look for unfinished implementation where the last exchange left something half-done, and explicit "I'll also..." or "next I'll..." commitments the conversation made and didn't honor.

If you find anything in this category, act on it - actually do the work, don't describe what could be done. Run the tests, don't say "you could run the tests." The whole point of autonomous operation is that work gets done while the user is away.

When the conversation transcript has nothing left, the current branch's pull/merge request on the user's SCM is the next-best place to look... Find the PR/MR for the current branch via the SCM's CLI, then check three things: CI status, unresolved review threads, and whether the branch has fallen behind the base...

<span class="hl-line">If everything is genuinely quiet - no conversation work, no PR maintenance - say so in one sentence and stop. No summary of what you checked, no list of what you might do later. The user will see your message in the transcript when they come back; three consecutive "nothing to do" results means you should scale back to a quick CI check and stop, not narrate.</span>

<span class="pr-h">## Repeated invocations</span>

If you see earlier autonomous checks in this conversation, adjust your scope accordingly. If a previous check left a question the user hasn't answered, the cost of acting depends on reversibility: for reversible actions (local edits, running tests), make your best call and proceed; for irreversible ones (pushing, deleting, sending), keep waiting... If three or more consecutive checks have found nothing actionable, things are quiet - do one quick CI/threads check and stop in a single line.
```

## 定时器到期后：消息怎么回到对话里

一句话概括：**Claude Code 不改动已有的对话，只在会话记录末尾追加几条消息，再把本轮要执行的 prompt 排进队列，等 Claude 空闲时当作新一轮对话发给模型。**

### 一次触发分三步

定时器到期（`onFireTask` / `onFire`）后，`useScheduledTasks()` 依次做三件事：

1. **写一行系统标记**：往会话记录末尾追加 `scheduled_task_fire`。如果上一次唤醒是空转，同时把那次的记录标为已折叠，并追加一条元消息总结。
2. **把 prompt 放进队列**：调用 `ae.enqueue`，优先级 `later`（排在你自己输入的消息后面），标记为元消息和定时来源。
3. **空闲时发给模型**：Claude 当前没在回复时，从队列取出这条消息，作为新一轮的 user 消息发出。模型执行后，再决定是否调用 `ScheduleWakeup` 预约下一次。谁来取、什么时候取，见本节后面的“两层循环”。

```svg title="图 2：定时器到期后，Claude Code 依次做的三件事" tone="light"
<svg viewBox="0 0 680 540" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif">
  <defs>
    <marker id="flow-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 1 L10 5 L0 9 z" fill="#64748b"/>
    </marker>
  </defs>

  <!-- timer -->
  <rect x="150" y="16" width="380" height="52" rx="10" fill="#1f2937"/>
  <text x="340" y="38" text-anchor="middle" fill="#ffffff" font-size="14" font-weight="700">定时器到期</text>
  <text x="340" y="58" text-anchor="middle" fill="#cbd5e1" font-size="12">CronCreate 的周期任务，或上一轮模型调用的 ScheduleWakeup</text>
  <line x1="340" y1="68" x2="340" y2="94" stroke="#64748b" stroke-width="2" marker-end="url(#flow-arrow)"/>

  <!-- step 1 -->
  <rect x="20" y="98" width="640" height="168" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="48" cy="126" r="14" fill="#2457d6"/>
  <text x="48" y="131" text-anchor="middle" fill="#ffffff" font-size="14" font-weight="700">1</text>
  <text x="72" y="131" fill="#111827" font-size="15" font-weight="700">在 transcript（会话记录）末尾追加一行系统标记</text>
  <text x="72" y="156" fill="#92400e" font-size="12" font-family="ui-monospace, Menlo, Consolas, monospace">type: "system", subtype: "scheduled_task_fire"</text>
  <text x="72" y="178" fill="#475569" font-size="13">终端里显示为 “Claude resuming /loop wakeup (15:42)”，标出这是一次定时唤醒</text>
  <rect x="60" y="192" width="584" height="60" rx="8" fill="#f5f3ff" stroke="#7c3aed" stroke-width="1.2" stroke-dasharray="5 3"/>
  <text x="74" y="215" fill="#5b21b6" font-size="13" font-weight="700">如果上一轮是空转（noop: true），这一步还会顺带：</text>
  <text x="74" y="238" fill="#5b21b6" font-size="13">把上一轮那几行标为“已折叠”，再追加一句给模型看的总结</text>

  <line x1="340" y1="266" x2="340" y2="292" stroke="#64748b" stroke-width="2" marker-end="url(#flow-arrow)"/>

  <!-- step 2 -->
  <rect x="20" y="296" width="640" height="110" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="48" cy="324" r="14" fill="#2457d6"/>
  <text x="48" y="329" text-anchor="middle" fill="#ffffff" font-size="14" font-weight="700">2</text>
  <text x="72" y="329" fill="#111827" font-size="15" font-weight="700">把本轮要执行的 prompt 放进消息队列 ae</text>
  <text x="72" y="356" font-size="13" fill="#475569"><tspan fill="#166534" font-size="12" font-family="ui-monospace, Menlo, Consolas, monospace">priority: "later"</tspan><tspan dx="10">排在你自己输入的消息后面，不会抢在你前面</tspan></text>
  <text x="72" y="384" font-size="13" fill="#475569"><tspan fill="#166534" font-size="12" font-family="ui-monospace, Menlo, Consolas, monospace">isMeta: true</tspan><tspan dx="10">标记为程序生成的消息，不算你亲手打的字</tspan></text>

  <line x1="340" y1="406" x2="340" y2="432" stroke="#64748b" stroke-width="2" marker-end="url(#flow-arrow)"/>

  <!-- step 3 -->
  <rect x="20" y="436" width="640" height="88" rx="12" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <circle cx="48" cy="464" r="14" fill="#2457d6"/>
  <text x="48" y="469" text-anchor="middle" fill="#ffffff" font-size="14" font-weight="700">3</text>
  <text x="72" y="469" fill="#111827" font-size="15" font-weight="700">Claude 空闲时取出这条消息，作为新一轮的 user 消息发给模型</text>
  <text x="72" y="496" fill="#475569" font-size="13">模型执行检查，再决定是否调用 ScheduleWakeup 安排下一次唤醒</text>

  <!-- loop back -->
  <path d="M660 480 H672 V42 H536" fill="none" stroke="#94a3b8" stroke-width="1.6" stroke-dasharray="5 4" marker-end="url(#flow-arrow)"/>
  <text x="604" y="34" text-anchor="middle" fill="#64748b" font-size="12">下一次唤醒</text>
</svg>
```

### 会话记录在触发前后长什么样

以裸 `/loop` 为例：你输入 `/loop` 后，Claude 检查 PR、做了修改并预约下一次；15:32 唤醒时检查没有发现变化，报告 `noop: true`；15:42 定时器再次到期。

右图从上到下就是 15:42 触发后的会话记录。分割线以上的第 1–5 行原样保留；分割线以下是这次新增的三行：

- **第 6 行**：本次唤醒的系统标记。因为 15:32 那次是空转，它同时记下 `noOpStreak: 1`，并用 `foldedUuids` 指向第 3–5 行，表示这几行被折叠。
- **第 7 行**：一条元消息，告诉模型“之前 1 次唤醒什么都没发现，循环正常”。
- **第 8 行**：从队列里取出的本轮 prompt，模型据此开始新的一轮。

```svg title="图 3：15:42 触发后的会话记录——旧记录不动，只在末尾追加" tone="light"
<svg viewBox="0 0 680 500" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif" font-size="13">
  <!-- section: existing history -->
  <rect x="8" y="10" width="664" height="30" rx="6" fill="#f1f5f9"/>
  <text x="20" y="30" fill="#334155" font-weight="700">已有历史：触发前就在 transcript 里，这次一个字都不改</text>

  <!-- 15:32 round that gets folded -->
  <rect x="8" y="122" width="664" height="112" rx="8" fill="#fffbeb" stroke="#d97706" stroke-width="1.2" stroke-dasharray="5 3"/>
  <rect x="470" y="113" width="196" height="20" rx="10" fill="#ffffff" stroke="#d97706" stroke-width="1"/>
  <text x="568" y="127" text-anchor="middle" fill="#b45309" font-size="11.5">15:32 这一轮空转 → 本次被折叠</text>

  <!-- row 1 -->
  <text x="18" y="70" fill="#94a3b8" font-size="11">1</text>
  <rect x="34" y="53" width="76" height="26" rx="13" fill="#dbeafe"/>
  <text x="72" y="71" text-anchor="middle" fill="#1e40af" font-size="12" font-weight="600">你</text>
  <text x="124" y="71" fill="#1f2937"><tspan font-family="ui-monospace, Menlo, Consolas, monospace" font-size="12">/loop</tspan><tspan dx="6">→ 展开成 /loop 指令全文（几 KB），作为你的消息发出</tspan></text>

  <!-- row 2 -->
  <text x="18" y="106" fill="#94a3b8" font-size="11">2</text>
  <rect x="34" y="89" width="76" height="26" rx="13" fill="#e5e7eb"/>
  <text x="72" y="107" text-anchor="middle" fill="#111827" font-size="12" font-weight="600">Claude</text>
  <text x="124" y="107" fill="#1f2937">检查 PR，做了修改 → 调用 ScheduleWakeup 安排下一次</text>

  <!-- row 3 -->
  <text x="18" y="148" fill="#94a3b8" font-size="11">3</text>
  <rect x="34" y="131" width="76" height="26" rx="13" fill="#fef3c7"/>
  <text x="72" y="149" text-anchor="middle" fill="#92400e" font-size="12" font-weight="600">系统标记</text>
  <text x="124" y="149" fill="#92400e" font-family="ui-monospace, Menlo, Consolas, monospace" font-size="12">Claude resuming /loop wakeup (15:32)</text>

  <!-- row 4 -->
  <text x="18" y="184" fill="#94a3b8" font-size="11">4</text>
  <rect x="34" y="167" width="76" height="26" rx="13" fill="#ede9fe"/>
  <text x="72" y="185" text-anchor="middle" fill="#5b21b6" font-size="12" font-weight="600">元消息</text>
  <text x="124" y="185" fill="#1f2937">15:32 这一轮的 prompt（从消息队列取出）</text>

  <!-- row 5 -->
  <text x="18" y="220" fill="#94a3b8" font-size="11">5</text>
  <rect x="34" y="203" width="76" height="26" rx="13" fill="#e5e7eb"/>
  <text x="72" y="221" text-anchor="middle" fill="#111827" font-size="12" font-weight="600">Claude</text>
  <text x="124" y="221" fill="#1f2937"><tspan font-family="ui-monospace, Menlo, Consolas, monospace" font-size="12">gh pr checks</tspan><tspan dx="6">→ 没有变化 →</tspan><tspan dx="6" font-family="ui-monospace, Menlo, Consolas, monospace" font-size="12">ScheduleWakeup(noop: true)</tspan></text>

  <!-- timer divider -->
  <line x1="8" y1="256" x2="672" y2="256" stroke="#2457d6" stroke-width="1.2" stroke-dasharray="6 4"/>
  <rect x="255" y="244" width="170" height="24" rx="12" fill="#2457d6"/>
  <text x="340" y="261" text-anchor="middle" fill="#ffffff" font-size="12.5" font-weight="600">15:42 定时器到期</text>

  <!-- section: appended -->
  <rect x="8" y="280" width="664" height="30" rx="6" fill="#ecfdf3"/>
  <text x="20" y="300" fill="#166534" font-weight="700">本次触发新增：只追加在末尾</text>

  <!-- row 6 -->
  <text x="18" y="340" fill="#94a3b8" font-size="11">6</text>
  <rect x="34" y="323" width="76" height="26" rx="13" fill="#fef3c7"/>
  <text x="72" y="341" text-anchor="middle" fill="#92400e" font-size="12" font-weight="600">系统标记</text>
  <text x="124" y="341" fill="#92400e"><tspan font-family="ui-monospace, Menlo, Consolas, monospace" font-size="12">Claude resuming /loop wakeup (15:42)</tspan><tspan dx="8" fill="#b45309" font-size="11.5">noOpStreak: 1，foldedUuids → 第 3–5 行</tspan></text>

  <!-- row 7 -->
  <text x="18" y="376" fill="#94a3b8" font-size="11">7</text>
  <rect x="34" y="359" width="76" height="26" rx="13" fill="#ede9fe"/>
  <text x="72" y="377" text-anchor="middle" fill="#5b21b6" font-size="12" font-weight="600">元消息</text>
  <text x="124" y="377" fill="#5b21b6" font-family="ui-monospace, Menlo, Consolas, monospace" font-size="12">[1 prior /loop wakeup found nothing actionable; loop is healthy.]</text>

  <!-- row 8 -->
  <text x="18" y="412" fill="#94a3b8" font-size="11">8</text>
  <rect x="34" y="395" width="76" height="26" rx="13" fill="#ede9fe"/>
  <text x="72" y="413" text-anchor="middle" fill="#5b21b6" font-size="12" font-weight="600">元消息</text>
  <text x="124" y="413" fill="#1f2937">15:42 这一轮的 prompt（从消息队列 ae 取出）</text>

  <!-- footer -->
  <rect x="8" y="436" width="664" height="54" rx="8" fill="#eff6ff" stroke="#bfdbfe"/>
  <text x="20" y="458" fill="#1e3a8a" font-weight="600">第 1–5 行原样保留 → 发给 API 的请求开头不变 → Prompt Cache 命中，更快更省</text>
  <text x="20" y="479" fill="#475569" font-size="12">第 3–5 行在终端里收起，第 7 行用一句话告诉模型：之前那次唤醒什么都没发现，循环正常</text>
</svg>
```

### 什么时候折叠，什么时候不折叠

折叠是有条件的。`U(transcript)` 会扫描上一次触发之后的那段记录，只有确认期间没有人插话、没有工具出错、并且模型报告了 `noop: true`，才判定为可以折叠。出现下面任何一种情况都不折叠，旧记录原样保留：

- `model_reported_work`：模型上一轮做了实事（`noop` 不是 `true`）。
- `foreign_user_input`：期间你或其他真实用户发过新消息。
- `tool_abort` / `tool_denial` / `split_tool_pair` / `queued_command`：工具调用被拒绝、被中断，或者有排队中的命令。
- `blocking_system_in_span`：期间执行过 `/compact` 上下文压缩。

> 从右侧 `C()` 的源码看，折叠是追加一行带 `foldedUuids` 的标记，旧消息并没有从数组里删除；`ScheduleWakeup` 的提示词原文也只说连续空转会“在终端里折叠显示”。被折叠的记录之后还会不会发给模型，这段源码里看不出来。

```js title="真实源码：useScheduledTasks() 消息入队与 No-Op 折叠 (v2.1.325)" raw
<span class="cm">// 1. 构造 scheduled_task_fire 系统消息节点 (SPr)</span>
<span class="kw">function</span> <span class="fn">SPr</span>(content, meta) {
  <span class="kw">return</span> {
    type: <span class="str">"system"</span>,
    subtype: <span class="str">"scheduled_task_fire"</span>,
    content,
    isMeta: <span class="num">false</span>,
    timestamp: <span class="kw">new</span> <span class="ty">Date</span>().<span class="fn">toISOString</span>(),
    uuid: meta.uuid ?? <span class="fn">randomUUID</span>(),
    taskId: meta.task.id,
    cron: meta.task.cron,
    prompt: <span class="fn">truncate</span>(meta.task.prompt, <span class="num">200</span>),
    ...(meta.task.kind === <span class="str">"loop"</span> &amp;&amp; { taskKind: <span class="str">"loop"</span> }),
    ...(meta.cronKind &amp;&amp; { cronKind: meta.cronKind }),
    ...(meta.noOpStreak &gt; <span class="num">0</span> &amp;&amp; {
      noOpStreak: meta.noOpStreak,
      streakStartedAt: meta.streakStartedAt
    }),
    ...(meta.foldedUuids?.length &amp;&amp; { foldedUuids: meta.foldedUuids })
  };
}

<span class="cm">// 2. 连续 No-Op 唤醒折叠器：避免空转轮询撑爆上下文</span>
<span class="kw">function</span> <span class="fn">w</span>(streakCount) {
  <span class="kw">return</span> {
    ...<span class="fn">createUserMetaMessage</span>({
<span class="hl-line">      content: <span class="str">`[${streakCount} prior /loop ${<span class="fn">plural</span>(streakCount, "wakeup")} found nothing actionable; loop is healthy.]`</span>,</span>
      isMeta: <span class="num">true</span>
    }),
    turnCompanion: <span class="num">true</span>
  };
}

<span class="kw">function</span> <span class="fn">C</span>(transcript, isIdle, task, fireUuid) {
  <span class="kw">let</span> label = <span class="str">`Claude resuming /loop wakeup (${<span class="fn">formatTime</span>(<span class="kw">new</span> <span class="ty">Date</span>())})`</span>;
  <span class="kw">let</span> check = isIdle ? <span class="fn">U</span>(transcript) : { kind: <span class="str">"none"</span> }; <span class="cm">// U() 检查上一轮是否为 noop:true</span>
  <span class="kw">if</span> (check.kind !== <span class="str">"fold"</span>) {
    <span class="kw">return</span> [...transcript, <span class="fn">SPr</span>(label, { task, uuid: fireUuid, ...(isIdle &amp;&amp; { cronKind: <span class="str">"loop"</span> }) })];
  }
  <span class="kw">let</span> streak = check.priorStreak + <span class="num">1</span>;
  <span class="kw">let</span> sinceStr = <span class="fn">formatTime</span>(<span class="kw">new</span> <span class="ty">Date</span>(check.since));
  <span class="kw">let</span> foldedUuids = transcript.<span class="fn">slice</span>(check.fireIdx).<span class="fn">map</span>((m) =&gt; m.uuid);
<span class="hl-line">  <span class="kw">return</span> [</span>
<span class="hl-line">    ...transcript,</span>
<span class="hl-line">    <span class="fn">SPr</span>(<span class="str">`... · ${streak} no-op ticks since ${sinceStr}`</span>, { task, uuid: fireUuid, cronKind: <span class="str">"loop"</span>, noOpStreak: streak, streakStartedAt: check.since, foldedUuids }),</span>
<span class="hl-line">    <span class="fn">w</span>(streak)</span>
<span class="hl-line">  ];</span>
}

<span class="cm">// 3. 将解析后的 Prompt 推入主会话消息队列 (ae.enqueue)</span>
<span class="kw">let</span> <span class="fn">buildQueuedPrompt</span> = (resolvedText, wakeupSource, meta) =&gt; ({
  value: resolvedText,
  mode: <span class="str">"prompt"</span>,
  agentId: <span class="fn">getCurrentAgentId</span>(),
<span class="hl-amber">  priority: <span class="str">"later"</span>,            <span class="cm">// 低优先级：排在人类实时输入之后</span></span>
<span class="hl-amber">  isMeta: <span class="num">true</span>,                 <span class="cm">// 标记为系统元消息，不在 UI 伪装成人类打字</span></span>
<span class="hl-amber">  skipSlashCommands: <span class="num">true</span>,      <span class="cm">// 防止二次触发 CLI 斜杠命令解析器</span></span>
<span class="hl-amber">  modelScheduledOrigin: <span class="num">true</span>,   <span class="cm">// 告诉 Skill 这是定时唤醒，跳过重复遥测与重置</span></span>
  skipAttachments: <span class="num">true</span>,
  wakeupSource,                 <span class="cm">// "schedule_wakeup" | "cron"</span>
  scheduledTaskId: meta?.taskId,
  scheduledFireId: meta?.fireId,
  workload: <span class="str">"cron"</span>
});
```

### 模型停下之后，谁来接着跑：两层循环

读到这里容易冒出一个疑问：模型每一轮的工作是一个循环，调模型、执行工具、再调模型，直到不再调用工具为止。这个循环一结束就停了，下一次是谁把它“叫起来”的？

答案是有两层循环。**停下的是内层**，也就是一轮 `query()`，每轮结束都会停，这是正常的。**外层是 Claude Code 进程本身**（Bun 事件循环加 Ink/React 终端界面），只要终端开着就一直运行。下一次唤醒靠的是外层的三样东西：每秒执行一次的调度器、一轮结束时立即触发的检查，以及一个监听命令队列的 React effect。

模型在两轮之间没有任何状态在运行。所谓“继续执行”，就是外层再发起一次 `query()`：把完整的会话记录加上一条新的 user 消息发给 API。模型读完历史，知道自己在跑循环、上次做到哪，于是接着做。

这一部分源码读自本机安装的 v2.1.314（比前文的 v2.1.325 略早）。代码是压缩过的，下面两段按含义还原了变量名，并删掉了与定时无关的分支。

```svg title="图 4：两层循环——内层每轮都会停，外层负责把下一条消息送进去" tone="light"
<svg viewBox="0 0 680 584" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif" font-size="13">
  <defs>
    <marker id="tl-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 1 L10 5 L0 9 z" fill="#64748b"/>
    </marker>
    <marker id="tl-arrow-blue" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 1 L10 5 L0 9 z" fill="#2457d6"/>
    </marker>
  </defs>

  <!-- outer loop -->
  <rect x="8" y="8" width="664" height="568" rx="14" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1.5"/>
  <text x="24" y="32" fill="#334155" font-size="13" font-weight="700">外层：Claude Code 进程，终端开着就一直运行</text>

  <!-- triggers -->
  <rect x="28" y="56" width="196" height="50" rx="10" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.2"/>
  <text x="126" y="77" text-anchor="middle" fill="#111827" font-weight="700">每秒一次</text>
  <text x="126" y="96" text-anchor="middle" fill="#475569" font-size="11.5" font-family="ui-monospace, Menlo, Consolas, monospace">setInterval(ae, 1000)</text>
  <rect x="242" y="56" width="196" height="50" rx="10" fill="#ffffff" stroke="#2457d6" stroke-width="1.2"/>
  <text x="340" y="77" text-anchor="middle" fill="#111827" font-weight="700">一轮刚结束</text>
  <text x="340" y="96" text-anchor="middle" fill="#475569" font-size="11.5" font-family="ui-monospace, Menlo, Consolas, monospace">checkNow()</text>
  <rect x="456" y="56" width="196" height="50" rx="10" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.2"/>
  <text x="554" y="77" text-anchor="middle" fill="#111827" font-weight="700">任务文件被改动</text>
  <text x="554" y="96" text-anchor="middle" fill="#475569" font-size="11.5">监听 scheduled_tasks.json</text>
  <line x1="126" y1="106" x2="126" y2="132" stroke="#64748b" stroke-width="1.6" marker-end="url(#tl-arrow)"/>
  <line x1="340" y1="106" x2="340" y2="132" stroke="#64748b" stroke-width="1.6" marker-end="url(#tl-arrow)"/>
  <line x1="554" y1="106" x2="554" y2="132" stroke="#64748b" stroke-width="1.6" marker-end="url(#tl-arrow)"/>

  <!-- scheduler -->
  <rect x="28" y="136" width="624" height="62" rx="10" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <text x="44" y="160" fill="#111827" font-size="14" font-weight="700">调度器 ae()：模型正在跑这一轮，就直接跳过</text>
  <text x="44" y="184" fill="#475569">有任务到期 → onFireTask：追加 scheduled_task_fire 标记，把 prompt 放进队列</text>
  <line x1="340" y1="198" x2="340" y2="226" stroke="#64748b" stroke-width="1.6" marker-end="url(#tl-arrow)"/>

  <!-- queue -->
  <rect x="28" y="230" width="624" height="66" rx="10" fill="#fffbeb" stroke="#d97706" stroke-width="1.5"/>
  <text x="44" y="253" fill="#92400e" font-size="14" font-weight="700">命令队列</text>
  <rect x="44" y="264" width="150" height="22" rx="6" fill="#ffffff" stroke="#fcd34d"/>
  <text x="119" y="279" text-anchor="middle" fill="#78350f" font-size="12">你刚输入的消息</text>
  <rect x="204" y="264" width="300" height="22" rx="6" fill="#ffffff" stroke="#fcd34d"/>
  <text x="354" y="279" text-anchor="middle" fill="#78350f" font-size="12">定时 prompt · priority: later · isMeta</text>
  <line x1="340" y1="296" x2="340" y2="322" stroke="#64748b" stroke-width="1.6" marker-end="url(#tl-arrow)"/>

  <!-- queue processor -->
  <rect x="28" y="326" width="624" height="62" rx="10" fill="#ffffff" stroke="#d0d5dd" stroke-width="1.5"/>
  <text x="44" y="350" fill="#111827" font-size="14" font-weight="700">队列处理器（useEffect）：队列或回合状态一变就重新检查</text>
  <text x="44" y="374" fill="#475569">没在跑、没有弹窗 → iCt() 取出消息 → executeQueuedInput() 发起新一轮</text>
  <line x1="340" y1="388" x2="340" y2="416" stroke="#2457d6" stroke-width="1.6" marker-end="url(#tl-arrow-blue)"/>

  <!-- inner loop -->
  <rect x="28" y="420" width="624" height="140" rx="12" fill="#eff6ff" stroke="#2457d6" stroke-width="1.5"/>
  <text x="44" y="444" fill="#1e3a8a" font-size="14" font-weight="700">内层：一轮 agent 循环 query()，每轮结束都会停</text>
  <path d="M129 472 V460 H549 V468" fill="none" stroke="#2457d6" stroke-width="1.4" marker-end="url(#tl-arrow-blue)"/>
  <rect x="44" y="472" width="170" height="46" rx="8" fill="#ffffff" stroke="#93c5fd"/>
  <text x="129" y="492" text-anchor="middle" fill="#1e3a8a" font-weight="700">调模型</text>
  <text x="129" y="510" text-anchor="middle" fill="#475569" font-size="11.5">完整会话记录 + 新消息</text>
  <line x1="214" y1="495" x2="240" y2="495" stroke="#2457d6" stroke-width="1.4" marker-end="url(#tl-arrow-blue)"/>
  <rect x="244" y="472" width="196" height="46" rx="8" fill="#ffffff" stroke="#93c5fd"/>
  <text x="342" y="492" text-anchor="middle" fill="#1e3a8a" font-weight="700">有 tool_use：执行工具</text>
  <text x="342" y="510" text-anchor="middle" fill="#475569" font-size="11.5">结果追加到会话记录</text>
  <path d="M342 518 V534 H129 V522" fill="none" stroke="#2457d6" stroke-width="1.4" marker-end="url(#tl-arrow-blue)"/>
  <text x="236" y="550" text-anchor="middle" fill="#1e40af" font-size="11.5">再调模型</text>
  <rect x="464" y="472" width="172" height="46" rx="8" fill="#ffffff" stroke="#93c5fd"/>
  <text x="550" y="492" text-anchor="middle" fill="#1e3a8a" font-weight="700">没有 tool_use：结束</text>
  <text x="550" y="510" text-anchor="middle" fill="#475569" font-size="11.5">结束前已预约下一次</text>

  <!-- back to outer -->
  <path d="M636 495 H662 V44 H340 V52" fill="none" stroke="#94a3b8" stroke-width="1.6" stroke-dasharray="5 4" marker-end="url(#tl-arrow)"/>
  <text x="654" y="39" text-anchor="end" fill="#64748b" font-size="11.5">isLoading 变为 false</text>
</svg>
```

### 调度器：每秒查一次，一轮结束再查一次

调度器在 `useScheduledTasks()` 挂载时创建，`start()` 注册了一个每秒执行的定时器和一个任务文件监听。核心是 `ae()`：

- **模型正在跑这一轮就直接返回**（`isLoading()` 为 true）。所以定时任务永远不会打断模型，到期了也只是等着。
- **到期就调用 `onFireTask`**，也就是前面三步里的前两步：追加系统标记、把 prompt 放进队列。
- **一轮刚结束就立即查一次**：另一个 effect 监听 `isLoading`，一旦变为 false 就调用 `checkNow()`，不用等下一秒。

这些定时器都调用了 `.unref()`，进程不靠它们维持，而是靠监听终端输入。`durable: true` 的任务写在 `.claude/scheduled_tasks.json` 里，文件一变就重新读取；同时开着多个 Claude Code 时，只有抢到锁的那个实例会执行文件里的任务。

```js title="调度器 Iuo() 与 useScheduledTasks()（v2.1.314，按压缩代码整理）"
// useScheduledTasks() 挂载时创建调度器并调用 start()
function start() {
  J = setInterval(ae, 1000);                 // 每秒检查一次到期任务
  z = chokidar.watch(".claude/scheduled_tasks.json");   // durable 任务文件变了就重新读
  if (!lockAcquired) A = setInterval(tryAcquireLock, 5000);  // 没抢到锁就每 5 秒重试
  // 这些定时器都调用了 .unref()：进程靠终端输入监听维持，不靠它们
}

function ae() {
  if (isKilled()) return;
  if (isLoading() && !assistantMode) return;  // 模型正在跑这一轮：跳过，下一秒再看
  const now = Date.now();
  for (const task of tasks) {
    if (now < nextFireAt.get(task.id)) continue;
    onFireTask(task);                        // 追加 scheduled_task_fire 标记 + 入队
    if (task.recurring) nextFireAt.set(task.id, nextCronTime(task, now));
    else removeTask(task.id);                // 一次性任务触发后删除
  }
}

// useScheduledTasks() 里的另一个 effect：一轮刚结束就立即检查
useEffect(() => {
  if (isLoading) return;
  scheduler.checkNow();                      // checkNow() { ae() }
}, [isLoading]);
```

### 队列处理器：空闲时取出消息，开始新的一轮

消息进了队列之后，由 REPL 里的队列处理器取出。它不轮询，而是用 `useSyncExternalStore` 订阅命令队列和当前回合的状态。队列里多了一条，或者一轮刚结束，effect 都会重新执行一次。

满足三个条件才会取：没有正在跑的一轮、没有权限确认之类的对话框开着、队列不空。条件满足就调用 `iCt()`：取出下一条消息，把同类的连续 prompt 合并成一批，交给 `executeQueuedInput()`。

`executeQueuedInput()` 最后调用的入口（压缩名 `B1`）和你按回车提交消息时是同一个：带上当前完整的会话记录和这批新消息，发起新一轮 `query()`。模型执行完、预约好下一次，`isLoading` 变回 false，又回到图 4 的顶部。

> “和手动提交是同一个入口”是根据 `B1` 的参数判断的，从 `B1` 到 `query()` 的那一段没有继续展开。`1000` 毫秒是按变量对应找到的常量，没有在运行时验证。

```js title="队列处理器与 iCt()（v2.1.314，按压缩代码整理）"
// REPL 里的队列处理器（压缩名 KIe）
// 通过 useSyncExternalStore 订阅命令队列、当前回合、对话框状态
useEffect(() => {
  if (turn.isActive) return;          // 正在跑一轮：不取
  if (dialogOpen()) return;           // 权限确认等对话框开着：等你先处理
  if (queue.length === 0) return;
  processQueue();                     // → iCt()
}, [queue, turn, dialogs /* … */]);

function iCt({ queue, executeInput }) {
  const first = q1(queue);            // 下一条要执行的消息
  if (!first) return { processed: false };
  const batch = queue.dequeueAllMatching(/* 和 first 同类的连续 prompt */);
  queue.setInFlightDrainBatch(batch);
  executeInput(batch)                 // = executeQueuedInput
    .finally(() => queue.clearInFlightDrainBatch(batch));
  return { processed: true };
}

// executeQueuedInput() 的最后一步（节选）
await B1({
  messages: transcript.getSnapshot(), // 当前完整的会话记录
  queuedCommands: batch,              // 这批新的 user 消息
  mainLoopModel, canUseTool, onBeforeQuery /* … */
});
```

### 如果整个 Claude Code 进程退出了

外层循环也就没了。Claude Code 没有后台守护进程，没有任何东西会替你唤醒模型。

- **只存在于当前会话的任务**（默认的 `CronCreate` 任务和 `ScheduleWakeup` 预约）随进程一起消失。
- **写进 `.claude/scheduled_tasks.json` 的任务**（`durable: true`）在下次启动时由调度器重新读取。
- **错过的一次性任务不会自动执行**。调度器会插入右边这条消息，要求模型先用 `AskUserQuestion` 问你要不要补跑。

```prompt title="错过的一次性任务提示（gkr()，v2.1.314）"
The following one-shot scheduled task was missed while Claude was not running. It has already been removed from .claude/scheduled_tasks.json.
Do NOT execute this prompt yet. First use the AskUserQuestion tool to ask whether to run it now. Only execute if the user confirms.
```

## 为什么不每次都重发完整指令：哨兵与前缀缓存

设想 loop.md 或自主巡检指令有 20,000 字节，每 5 分钟触发一次。如果每次唤醒都把全文塞进队列，跑 20 轮，会话记录里就有 20 份一模一样的长文，白白占掉约 400KB 上下文。

Claude Code 的做法是让模型预约时只传一个**哨兵**，比如 `<<loop.md-dynamic>>`。到点入队前，`resolveLoopDefaultFireAsync` 把哨兵换成真正的内容：

- **第 1 次触发，或 loop.md 被你改过**：发全文加一段短提醒，并记下这次发出的内容（`lastLoopFileDelivered`）。按原报告的说明，刚执行过 `/compact` 后也会重发全文。
- **之后内容没变**：只发三行左右的短提醒，让模型按前面那份全文继续做。

loop.md 每次触发都会从磁盘重新读取，超过 25,000 字节会按行截断并附加警告。没有 loop.md 时，内置自主巡检指令也一样只在第一次发全文。

```svg title="图 5：哨兵在第一次和之后的唤醒里被替换成什么" tone="light"
<svg viewBox="0 0 680 400" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif">
  <defs>
    <marker id="sent-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 1 L10 5 L0 9 z" fill="#64748b"/>
    </marker>
  </defs>

  <!-- what the model scheduled -->
  <rect x="150" y="12" width="380" height="52" rx="10" fill="#1f2937"/>
  <text x="340" y="34" text-anchor="middle" fill="#fff" font-size="13.5" font-weight="700">模型预约时传的 prompt 只是一个短标记</text>
  <text x="340" y="54" text-anchor="middle" fill="#93c5fd" font-size="12" font-family="ui-monospace, Menlo, Consolas, monospace">&lt;&lt;loop.md-dynamic&gt;&gt;</text>
  <line x1="340" y1="64" x2="340" y2="88" stroke="#64748b" stroke-width="2" marker-end="url(#sent-arrow)"/>

  <!-- check -->
  <rect x="110" y="92" width="460" height="54" rx="10" fill="#fffbeb" stroke="#d97706" stroke-width="1.5"/>
  <text x="340" y="114" text-anchor="middle" fill="#92400e" font-size="13.5" font-weight="700">到点入队前，resolveLoopDefaultFireAsync 检查：</text>
  <text x="340" y="134" text-anchor="middle" fill="#92400e" font-size="12.5">这份内容之前发过吗？发过之后改过吗？</text>

  <path d="M200 146 V176" fill="none" stroke="#64748b" stroke-width="2" marker-end="url(#sent-arrow)"/>
  <path d="M480 146 V176" fill="none" stroke="#64748b" stroke-width="2" marker-end="url(#sent-arrow)"/>
  <text x="208" y="166" fill="#1d4ed8" font-size="12" font-weight="600">没发过 / 改过</text>
  <text x="488" y="166" fill="#15803d" font-size="12" font-weight="600">发过，且没改</text>

  <!-- left: full -->
  <text x="40" y="198" fill="#111827" font-size="13.5" font-weight="700">第 1 次触发：发全文</text>
  <rect x="40" y="208" width="290" height="140" rx="8" fill="#eff6ff" stroke="#2457d6" stroke-width="1.5"/>
  <rect x="56" y="222" width="258" height="8" rx="4" fill="#93c5fd"/>
  <rect x="56" y="238" width="230" height="8" rx="4" fill="#93c5fd"/>
  <rect x="56" y="254" width="248" height="8" rx="4" fill="#93c5fd"/>
  <rect x="56" y="270" width="210" height="8" rx="4" fill="#93c5fd"/>
  <rect x="56" y="286" width="240" height="8" rx="4" fill="#93c5fd"/>
  <rect x="56" y="302" width="180" height="8" rx="4" fill="#93c5fd"/>
  <text x="56" y="334" fill="#1e3a8a" font-size="12">loop.md 全文（最多 25,000 字节）+ 短提醒</text>

  <!-- right: short -->
  <text x="370" y="198" fill="#111827" font-size="13.5" font-weight="700">之后每次触发：只发短提醒</text>
  <rect x="370" y="208" width="270" height="56" rx="8" fill="#ecfdf3" stroke="#16a34a" stroke-width="1.5"/>
  <rect x="386" y="222" width="200" height="8" rx="4" fill="#86efac"/>
  <rect x="386" y="238" width="150" height="8" rx="4" fill="#86efac"/>
  <text x="370" y="286" fill="#166534" font-size="12">约 3 行：“按前面那份 loop.md 继续做”</text>

  <!-- footer -->
  <rect x="20" y="360" width="640" height="32" rx="8" fill="#f1f5f9"/>
  <text x="34" y="381" fill="#334155" font-size="12.5">长指令在会话记录里只出现一次，后面的唤醒都引用它，前缀保持不变</text>
</svg>
```

```js title="真实源码：/$bunfs/root/chunk-ed05p846.js :: 哨兵展开与短提醒" raw
<span class="kw">const</span> LOOP_FILE_SENTINEL = <span class="str">"&lt;&lt;loop.md&gt;&gt;"</span>;
<span class="kw">const</span> LOOP_FILE_DYNAMIC_SENTINEL = <span class="str">"&lt;&lt;loop.md-dynamic&gt;&gt;"</span>;
<span class="kw">const</span> AUTONOMOUS_LOOP_SENTINEL = <span class="str">"&lt;&lt;autonomous-loop&gt;&gt;"</span>;
<span class="kw">const</span> AUTONOMOUS_DYNAMIC_SENTINEL = <span class="str">"&lt;&lt;autonomous-loop-dynamic&gt;&gt;"</span>;

<span class="cm">// 后续唤醒时注入的极简短提醒 (Cron 模式 vs Dynamic 模式)</span>
<span class="kw">function</span> <span class="fn">D</span>() {
  <span class="kw">return</span> <span class="str">`# /loop tick — loop.md tasks\n\nWork the tasks from the loop.md contents established earlier in this conversation. If you cannot find them, treat this as a no-op tick. The recurring cron will fire the next tick automatically — do not call ScheduleWakeup from this tick.`</span>;
}

<span class="kw">function</span> <span class="fn">q</span>() {
  <span class="kw">return</span> <span class="str">`# /loop tick — loop.md tasks (dynamic pacing)\n\nWork the tasks from the loop.md contents established earlier in this conversation. If you cannot find them, treat this as a no-op tick.\n\nYou scheduled this tick via the ScheduleWakeup tool (not a recurring cron). To keep the loop alive, call ScheduleWakeup again this turn with \`prompt\` set to the literal sentinel \`&lt;&lt;loop.md-dynamic&gt;&gt;\` and \`noop\` set to \`true\` if this tick changed nothing (or \`false\` if it did) — otherwise the loop ends after this tick.`</span>;
}

<span class="cm">// 核心按需展开函数 R(sessionState, sentinel, loopFileObj)</span>
<span class="kw">function</span> <span class="fn">R</span>(e, t, o) {
  <span class="kw">let</span> isDynamic = (t === LOOP_FILE_DYNAMIC_SENTINEL);
  <span class="kw">if</span> (o) {
    <span class="kw">let</span> shortReminder = isDynamic ? <span class="fn">q</span>() : <span class="fn">D</span>();
<span class="hl-line">    <span class="cm">// 如果上一次投递的 loop.md 内容与当前磁盘内容完全一致，直接返回短提醒！</span></span>
<span class="hl-line">    <span class="kw">if</span> (e.lastLoopFileDelivered === o.content) <span class="kw">return</span> shortReminder;</span>

    <span class="cm">// 否则（首次触发、或用户刚修改了 loop.md），投递完整文件内容并记录快照</span>
    e.lastLoopFileDelivered = o.content;
    <span class="kw">return</span> <span class="str">`# /loop tick — tasks from ${o.path}\n\nThe user configured a loop-tasks file. Work through the tasks defined below; these are the instructions for this tick and every subsequent tick (the reminder on later fires refers back to this message).\n\n---\n\n${o.content}\n\n---\n\n${shortReminder}`</span>;
  }
  <span class="cm">// 若无 loop.md，同样仅在首次触发时投递 getAutonomousLoopPreamble() 全文</span>
  <span class="kw">let</span> shortAuto = isDynamic ? <span class="fn">M</span>() : <span class="fn">S</span>();
  <span class="kw">if</span> (e.autonomousPreambleDelivered) <span class="kw">return</span> shortAuto;
  e.autonomousPreambleDelivered = <span class="num">true</span>;
  <span class="kw">return</span> <span class="str">`${<span class="fn">getAutonomousLoopPreamble</span>()}\n\n---\n\n${shortAuto}`</span>;
}
```

## 等多久再叫醒：缓存窗口、抖动与兜底

唤醒间隔不是随便定的。Prompt Cache 默认只保留 5 分钟：5 分钟内再次请求，前面的对话直接命中缓存；超过 5 分钟，整个上下文要重新处理一遍。所以两个调度工具的提示词和源码，都在围绕这 5 分钟做文章。

右图把几个关键数字放在同一条时间轴上：270 秒还在缓存内，300 秒刚好错过，周期任务会提前到 285 秒触发，空闲时则干脆等 20–30 分钟。

```svg title="图 6：唤醒间隔与 5 分钟缓存窗口" tone="light"
<svg viewBox="0 0 680 250" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif" font-size="12">
  <!-- scale: 0–360s → x 40–440 ; break ; 1200–1800s → x 480–650 -->
  <rect x="40" y="96" width="333" height="30" rx="4" fill="#dcfce7"/>
  <text x="56" y="116" fill="#166534" font-weight="600">缓存有效（5 分钟内）</text>
  <rect x="373" y="96" width="67" height="30" fill="#e5e7eb"/>
  <rect x="480" y="96" width="170" height="30" rx="4" fill="#e5e7eb"/>
  <text x="565" y="116" text-anchor="middle" fill="#475569">缓存已过期</text>

  <!-- axis break -->
  <path d="M446 90 l8 8 l-8 8 l8 8 l-8 8 l8 8 l-8 8" fill="none" stroke="#94a3b8" stroke-width="1.5"/>
  <path d="M466 90 l8 8 l-8 8 l8 8 l-8 8 l8 8 l-8 8" fill="none" stroke="#94a3b8" stroke-width="1.5"/>

  <!-- ticks -->
  <line x1="40" y1="88" x2="40" y2="134" stroke="#334155" stroke-width="1.5"/>
  <text x="40" y="150" text-anchor="middle" fill="#334155">0</text>
  <text x="40" y="166" text-anchor="middle" fill="#64748b" font-size="11">上次请求</text>

  <line x1="340" y1="70" x2="340" y2="134" stroke="#16a34a" stroke-width="2"/>
  <text x="334" y="66" text-anchor="end" fill="#15803d" font-weight="600">270s：仍在缓存内</text>

  <line x1="357" y1="96" x2="357" y2="160" stroke="#2457d6" stroke-width="2"/>
  <text x="357" y="176" text-anchor="middle" fill="#1d4ed8" font-weight="600">285s：*/5 周期任务实际触发</text>
  <text x="357" y="192" text-anchor="middle" fill="#1d4ed8" font-size="11">（比 300s 提前 15 秒）</text>

  <line x1="373" y1="44" x2="373" y2="134" stroke="#dc2626" stroke-width="2"/>
  <text x="380" y="40" fill="#b91c1c" font-weight="600">300s：最差选择，刚好错过缓存</text>

  <line x1="480" y1="134" x2="480" y2="146" stroke="#334155"/>
  <line x1="650" y1="134" x2="650" y2="146" stroke="#334155"/>
  <path d="M480 146 H650" stroke="#64748b" stroke-width="1.5"/>
  <text x="480" y="162" text-anchor="middle" fill="#334155">1200s</text>
  <text x="650" y="162" text-anchor="middle" fill="#334155">1800s</text>
  <text x="565" y="182" text-anchor="middle" fill="#475569" font-weight="600">空闲时的默认间隔</text>

  <text x="40" y="222" fill="#475569">唤醒时缓存还在：模型只需处理新增的内容。缓存过期：整个上下文要重新读一遍，更慢也更贵。</text>
  <text x="40" y="240" fill="#94a3b8" font-size="11">横轴不等比：0–360s 放大显示，360–1200s 省略。</text>
</svg>
```

### CronCreate：固定间隔与一次性提醒

`CronCreate` 的提示词交代了几条规则：

- 近似时间要**避开整点和半点**。所有人说“9 点”都会得到 `0 9`，请求会在同一瞬间打到 API；所以“大约 9 点”应该写成 `57 8` 或 `3 9`。
- 默认任务只活在当前会话里；传 `durable: true` 才写入 `.claude/scheduled_tasks.json`，重启后仍在。
- 要实时盯日志或进程输出，应该用 `Monitor`，不要用 cron 轮询。
- 任务只在 Claude 空闲时触发，周期任务 7 天后自动过期。

```prompt title="CronCreate 工具的系统提示词原文 (buildCronCreatePrompt / nHs)" raw
<span class="pr-h">Schedule a prompt to be enqueued at a future time. Use for both recurring schedules and one-shot reminders.</span>

Uses standard 5-field cron in the user's local timezone: minute hour day-of-month month day-of-week. "0 9 * * *" means 9am local — no timezone conversion needed.

<span class="pr-h">## One-shot tasks (recurring: false)</span>
For "remind me at X" or "at &lt;time&gt;, do Y" requests — fire once then auto-delete.
Pin minute/hour/day-of-month/month to specific values:
  "remind me at 2:30pm today to check the deploy" → cron: "30 14 &lt;today_dom&gt; &lt;today_month&gt; *", recurring: false

<span class="pr-h">## Recurring jobs (recurring: true, the default)</span>
For "every N minutes" / "every hour" / "weekdays at 9am" requests:
  "*/5 * * * *" (every 5 min), "0 * * * *" (hourly), "0 9 * * 1-5" (weekdays at 9am local)

<span class="pr-h">## Avoid the :00 and :30 minute marks when the task allows it</span>
<span class="hl-amber">Every user who asks for "9am" gets `0 9`, and every user who asks for "hourly" gets `0 *` — which means requests from across the planet land on the API at the same instant. When the user's request is approximate, pick a minute that is NOT 0 or 30:</span>
  "every morning around 9" → "57 8 * * *" or "3 9 * * *" (not "0 9 * * *")
  "hourly" → "7 * * * *" (not "0 * * * *")
Only use minute 0 or 30 when the user names that exact time and clearly means it ("at 9:00 sharp").

<span class="pr-h">## Durability</span>
By default (durable: false) the job lives only in this Claude session — nothing is written to disk, and the job is gone when Claude exits. Pass durable: true to write to `.claude/scheduled_tasks.json` so the job survives restarts.

<span class="pr-h">## Not for live watching</span>
<span class="hl-line">`CronCreate` re-runs a prompt at fixed wall-clock intervals. To watch a log file, process, or command output and be notified the moment something changes, use the `Monitor` tool instead — `Monitor` streams events as they happen; cron polls on a schedule.</span>

<span class="pr-h">## Runtime behavior</span>
Jobs only fire while the REPL is idle (not mid-query)... The scheduler shifts each job by a fixed per-job offset on top of whatever you pick: by default a recurring job runs up to 50% of its period late (max 30 min), and a one-shot job landing on :00 or :30 runs up to 90 s early.
Recurring tasks auto-expire after 7 days — they fire one final time, then are deleted.
```

### ScheduleWakeup：按缓存窗口挑间隔

`ScheduleWakeup` 的提示词几乎全部在讲怎么选 `delaySeconds`：

- **60–270 秒**：缓存不会过期，适合盯着 CI、部署这类会很快变化的外部状态。
- **300–3600 秒**：要付一次缓存失效的代价，适合确实没必要早查的情况。
- **空闲时默认 1200–1800 秒**，不要一小时烧 12 次缓存。
- 后台任务完成时系统会自动唤醒模型，不需要短间隔轮询。

> 不要选 300 秒：付了一次缓存失效的代价，却没换来更长的等待。要么 270 秒留在缓存里，要么直接 1200 秒以上。

```prompt title="ScheduleWakeup 工具的系统提示词原文 (kN + vN 5分钟缓存策略)" raw
<span class="pr-h">Schedule when to resume work in /loop dynamic mode — the user invoked /loop without an interval, asking you to self-pace iterations of a specific task.</span>

Do NOT schedule a short-interval wakeup to poll for background work you started — when harness-tracked work finishes, you are re-invoked automatically, so polling is wasted. Instead schedule a long fallback (1200s+) so the loop survives if the work hangs or never notifies. The exception is external work the harness cannot track (a CI run, a deploy, a remote queue) — there, pick a delay matched to how fast that state actually changes.

Pass the same /loop prompt back via `prompt` each turn so the next firing repeats the task. For an autonomous /loop (no user prompt), pass the literal sentinel `&lt;&lt;autonomous-loop-dynamic&gt;&gt;` as `prompt` instead — the runtime resolves it back to the autonomous-loop instructions at fire time. To end the loop, call this tool with `stop: true` (omit every other field) — the loop ends immediately and no further wakeups fire.

<span class="pr-h">## Picking delaySeconds (5-minute Prompt Cache TTL Regime)</span>

This session's requests use the default 5-minute Anthropic prompt-cache TTL. Sleeping past 300 seconds means the next wake-up reads your full conversation context uncached — slower and more expensive. So the natural breakpoints:

- <span class="pr-b">**Under 5 minutes (60s–270s)**</span>: cache stays warm. Right for actively polling external state the harness can't notify you about — a CI run, a deploy, a remote queue.
- <span class="pr-b">**5 minutes to 1 hour (300s–3600s)**</span>: pay the cache miss. Right when there's no point checking sooner — waiting on something that takes minutes to change, genuinely idle, or as the long fallback heartbeat when something else is the primary wake signal.

<span class="hl-amber">**Don't pick 300s.** It's the worst-of-both: you pay the cache miss without amortizing it. If you're tempted to "wait 5 minutes," either drop to 270s (stay in cache) or commit to 1200s+ (one cache miss buys a much longer wait). Don't think in round-number minutes — think in cache windows.</span>

<span class="hl-line">For idle ticks with no specific signal to watch, default to **1200s–1800s** (20–30 min). The loop checks back, you don't burn cache 12× per hour for nothing, and the user can always interrupt if they need you sooner.</span>

Think about what you're actually waiting for, not just "how long should I sleep." If you're polling a CI run that takes ~8 minutes, sleeping 60s burns the cache 8 times before it finishes — sleep ~270s twice instead.

The runtime clamps to [60, 3600], so you don't need to clamp yourself.
```

### 周期任务实际在什么时候触发

周期任务的触发时间由 `$Wn()` 计算，参数来自远程配置 `tengu_kairos_cron_config` 的本地默认值 `PQ`。分两种情况：

- **每 5 分钟一次的任务**（`*/5 * * * *`）：不加延迟，反而**提前 15 秒**，在上次触发后第 285 秒执行，确保赶在缓存过期之前。
- **其他周期任务**：在标准时间点上加一个延迟，最多半个周期，上限 30 分钟。延迟由任务 ID 前 8 位换算，同一个任务每次都一样，不同任务彼此错开。

```js title="真实源码：PQ 默认配置与 $Wn / cHs 抖动计算 (chunk-ew2zgws6.js)" raw
<span class="cm">// GrowthBook 远程配置 tengu_kairos_cron_config 的本地默认值 PQ</span>
<span class="kw">var</span> PQ = {
  recurringFrac: <span class="num">0.5</span>,           <span class="cm">// 周期任务最大延后比例：50% 周期</span>
  recurringCapMs: <span class="num">1_800_000</span>,    <span class="cm">// 周期任务最大延后上限：30 分钟</span>
  oneShotMaxMs: <span class="num">90_000</span>,         <span class="cm">// 整点/半点一次性任务提前上限：90 秒</span>
  oneShotFloorMs: <span class="num">0</span>,
  oneShotMinuteMod: <span class="num">30</span>,         <span class="cm">// 命中 :00 或 :30 (minute % 30 === 0)</span>
  recurringMaxAgeMs: <span class="num">604_800_000</span>,<span class="cm">// 7 天自动过期 (7 * 86400 * 1000 ms)</span>
  cacheLeadMs: <span class="num">15_000</span>           <span class="cm">// 5分钟缓存提前量：15 秒 (15,000 ms)</span>
};

<span class="cm">// 取 8 位任务 ID 的十六进制值除以 2^32，得到确定性 [0, 1) 浮点数</span>
<span class="kw">function</span> <span class="fn">T</span>(taskId) {
  <span class="kw">let</span> o = <span class="fn">parseInt</span>(taskId.<span class="fn">slice</span>(<span class="num">0</span>, <span class="num">8</span>), <span class="num">16</span>) / <span class="num">4294967296</span>;
  <span class="kw">return</span> Number.<span class="fn">isFinite</span>(o) ? o : <span class="num">0</span>;
}

<span class="kw">const</span> R = <span class="str">/^\*\/\d+ \* \* \* \*$/</span>; <span class="cm">// 匹配 */N * * * *</span>
<span class="kw">const</span> DDr = <span class="num">300_000</span>;              <span class="cm">// 5 分钟 Prompt Cache TTL (300,000 ms)</span>

<span class="cm">// 周期任务下次触发时间计算 ($Wn)</span>
<span class="kw">function</span> <span class="fn">$Wn</span>(cronExpr, lastFiredAt, taskId, cfg = PQ) {
  <span class="kw">let</span> i = <span class="fn">Fln</span>(cronExpr, lastFiredAt); <span class="cm">// 下一格标准 Cron 时间点</span>
  <span class="kw">if</span> (i === <span class="num">null</span>) <span class="kw">return</span> <span class="num">null</span>;
  <span class="kw">let</span> u = <span class="fn">Fln</span>(cronExpr, i);           <span class="cm">// 再下一格标准 Cron 时间点</span>
  <span class="kw">if</span> (u === <span class="num">null</span>) <span class="kw">return</span> i;
  <span class="kw">let</span> r = u - i;                      <span class="cm">// 任务真实周期毫秒数 (例如 */5 为 300,000ms)</span>

<span class="hl-line">  <span class="cm">// 核心特判：如果是 */5 * * * * (r == 300,000ms)，不加正向延迟，</span></span>
<span class="hl-line">  <span class="cm">// 而是直接返回 lastFiredAt + 300,000 - 15,000 = 285秒 (4分45秒)！</span></span>
  <span class="kw">if</span> (R.<span class="fn">test</span>(cronExpr) &amp;&amp; cfg.cacheLeadMs &gt; <span class="num">0</span> &amp;&amp; cfg.cacheLeadMs &lt; r
      &amp;&amp; r &gt;= DDr &amp;&amp; r - cfg.cacheLeadMs &lt; DDr) {
    <span class="kw">return</span> lastFiredAt + r - cfg.cacheLeadMs;
  }

  <span class="cm">// 其他周期任务：加上确定性延后 Jitter (最高 50% 周期，封顶 30 分钟)</span>
  <span class="kw">let</span> m = Math.<span class="fn">min</span>(<span class="fn">T</span>(taskId) * cfg.recurringFrac * r, cfg.recurringCapMs);
  <span class="kw">return</span> i + m;
}
```

### 动态唤醒的两道保险

`O(delaySeconds)` 先把延迟限制在 60–3600 秒，再向上取整到整分钟（调度器按分钟匹配）。如果模型想在缓存内唤醒，取整却把时间推过了 285 秒，就主动往回退一分钟，避免一次取整把热缓存变成冷缓存。

`X1i()` 处理模型忘记预约的情况：动态模式下某一轮结束时如果没有调用 `ScheduleWakeup`（或者遇到 API 报错），系统自动替它约一次 1200 秒后的唤醒。这个兜底只给一次，再忘就结束循环。

```js title="真实源码：ScheduleWakeup 缓存窗口对齐 O(e) 与 20m 兜底 X1i()" raw
<span class="kw">var</span> k = <span class="num">60</span>, IRe = <span class="num">3600</span>;   <span class="cm">// Clamp 范围 [60s, 3600s]</span>
<span class="kw">var</span> D = <span class="num">1200</span>, P = <span class="num">1</span>;      <span class="cm">// Keepalive 兜底延迟 1200s (20分钟)，最多容忍遗忘 1 次</span>

<span class="cm">// 当模型在动态 Loop 回合结束时忘记调 ScheduleWakeup（或遇到 API 报错）时触发</span>
<span class="kw">function</span> <span class="fn">X1i</span>(lastPrompt, pluginOwner) {
  <span class="kw">if</span> (<span class="fn">getKeepaliveCount</span>() &gt;= P) {
    <span class="fn">log</span>(<span class="str">"[loop] keepalive budget exhausted (model declined to reschedule twice) — ending loop"</span>);
    <span class="fn">ADn</span>(<span class="str">"model_stopped"</span>, { via_keepalive: <span class="num">true</span> });
    <span class="kw">return</span> <span class="num">null</span>;
  }
<span class="hl-amber">  <span class="cm">// 自动挂载一次 1200 秒 (20 分钟) 的 keepalive 兜底唤醒！</span></span>
  <span class="kw">return</span> <span class="fn">E</span>(D, lastPrompt, { viaKeepalive: <span class="num">true</span>, owner: { rearmedFor: pluginOwner } });
}

<span class="cm">// ScheduleWakeup 的目标时间戳计算函数 O(delaySeconds)</span>
<span class="cm">// 因为底层调度器按整分钟匹配 Cron (Ems)，如果向上取整到整分钟导致跨过 300s 缓存边界，</span>
<span class="cm">// 会自动往回退 1 分钟 (d -= 60000)，确保唤醒严格落在 Prompt Cache 存活期内！</span>
<span class="kw">function</span> <span class="fn">O</span>(delaySeconds) {
  <span class="kw">let</span> o = Number.<span class="fn">isNaN</span>(delaySeconds) ? k : Math.<span class="fn">round</span>(delaySeconds);
  <span class="kw">let</span> n = Math.<span class="fn">max</span>(k, Math.<span class="fn">min</span>(IRe, o)); <span class="cm">// Clamp 到 [60, 3600]</span>
  <span class="kw">let</span> r = Date.<span class="fn">now</span>();
  <span class="kw">let</span> s = r + n * <span class="num">1000</span>;
  <span class="kw">let</span> d = Math.<span class="fn">ceil</span>(s / <span class="num">60_000</span>) * <span class="num">60_000</span>; <span class="cm">// 对齐到整分钟边界</span>
  <span class="kw">let</span> u = <span class="fn">Gq</span>().cacheLeadMs;               <span class="cm">// 15,000ms</span>

<span class="hl-line">  <span class="cm">// 若模型选择了 &lt;= 300s 的热缓存轮询，但整分钟向上取整后超过了 285s (300s - 15s)，</span></span>
<span class="hl-line">  <span class="cm">// 则主动回调 60,000ms (1分钟)，绝不让向上取整把热缓存变成 Cache Miss！</span></span>
  <span class="kw">if</span> (u &gt; <span class="num">0</span> &amp;&amp; n * <span class="num">1000</span> &lt;= DDr) {
    <span class="kw">let</span> maxWarmMs = DDr - u; <span class="cm">// 285,000ms</span>
    <span class="kw">while</span> (d - r &gt; maxWarmMs &amp;&amp; d - <span class="num">60_000</span> &gt;= r + k * <span class="num">1000</span>) {
      d -= <span class="num">60_000</span>;
    }
  }
  <span class="kw">let</span> createdAt = s &lt; d ? s : d - <span class="num">1</span>;
  <span class="kw">return</span> { clamped: n, wasClamped: o !== n, targetMs: d, createdAt };
}
```

## 对照：其他 Agent 怎么把定时消息送进会话

Claude Code 只有一条通道：定时 prompt 排在你的消息之后，等空闲时发出。Prime Agent（Rust）的心跳任务有两条通道可选：

- **Steer（默认）**：在当前工具调用结束、下一轮开始的边界插进去，不用等整个任务做完。
- **FollowUp**：等会话彻底空闲再发，和 Claude Code 的方式类似。

入队前，它用 `heartbeat:<id>` 作为键，删掉两条通道里同一个心跳还没被消费的旧消息，所以同一个心跳在队列里最多只有一条。目标会话被删除时，相关任务会被自动清理。

```rust title="真实源码：crates/pa-daemon/src/scheduled_jobs.rs :: run_job()" raw
<span class="kw">impl</span> <span class="ty">AgentCronSchedulerHooks</span> <span class="kw">for</span> <span class="ty">QueueHooks</span> {
  <span class="kw">async fn</span> <span class="fn">run_job</span>(&amp;<span class="kw">self</span>, job: &amp;<span class="ty">AgentCronJob</span>) -&gt; anyhow::<span class="ty">Result</span>&lt;<span class="ty">Option</span>&lt;&amp;<span class="str">'static</span> <span class="ty">str</span>&gt;&gt; {
    <span class="cm">// 1. 若目标会话文件已被删除或归档，自动清理该会话下的全部定时任务</span>
    <span class="kw">if</span> Self::<span class="fn">persisted_target_gone</span>(job) {
      <span class="kw">self</span>.<span class="fn">cancel_jobs_for_dead_target</span>(job)?;
      <span class="kw">return</span> <span class="fn">Ok</span>(<span class="fn">Some</span>(<span class="str">"skipped"</span>));
    }
    <span class="kw">let</span> activity = <span class="kw">self</span>.<span class="fn">activity</span>();
    <span class="kw">if</span> <span class="fn">should_defer_heartbeat_cron_job</span>(job, &amp;activity) {
      <span class="kw">return</span> <span class="fn">Ok</span>(<span class="fn">Some</span>(<span class="str">"skipped"</span>));
    }

    <span class="kw">let</span> (done_tx, done_rx) = oneshot::<span class="fn">channel</span>();
    <span class="kw">let</span> heartbeat = <span class="fn">is_heartbeat_cron_job</span>(job);
<span class="hl-line">    <span class="kw">let</span> queue_key = heartbeat.<span class="fn">then</span>(|| <span class="fn">format!</span>(<span class="str">"heartbeat:{}"</span>, job.id));</span>
<span class="hl-line">    <span class="kw">let</span> rides_steering = heartbeat &amp;&amp; !matches!(job.delivery_mode, <span class="fn">Some</span>(DeliveryMode::FollowUp));</span>
    {
      <span class="kw">let</span> <span class="kw">mut</span> core = <span class="kw">self</span>.core.<span class="fn">lock</span>().<span class="fn">unwrap_or_else</span>(PoisonError::into_inner);
      <span class="cm">// 2. 唤醒后自动解除 post-abort / post-compact 挂起状态</span>
      core.queued_input_suspended = <span class="num">false</span>;

<span class="hl-amber">      <span class="cm">// 3. 队列原地去重：若队列里已有同一个 heartbeat:&lt;id&gt;，直接替换而非堆积！</span></span>
      <span class="kw">if</span> <span class="kw">let</span> <span class="fn">Some</span>(key) = &amp;queue_key {
        core.steering.<span class="fn">retain</span>(|item| item.queue_key.<span class="fn">as_deref</span>() != <span class="fn">Some</span>(key.<span class="fn">as_str</span>()));
        core.follow_up.<span class="fn">retain</span>(|item| item.queue_key.<span class="fn">as_deref</span>() != <span class="fn">Some</span>(key.<span class="fn">as_str</span>()));
      }
      <span class="kw">let</span> lane = <span class="kw">if</span> rides_steering { &amp;<span class="kw">mut</span> core.steering } <span class="kw">else</span> { &amp;<span class="kw">mut</span> core.follow_up };

      <span class="cm">// 4. 构造专用的 heartbeat_prompt 自定义消息行并推入优先级队列</span>
      <span class="kw">let</span> (message, preview, custom_message) = <span class="kw">if</span> heartbeat {
        <span class="kw">let</span> row = <span class="fn">create_heartbeat_prompt_message</span>(job, <span class="fn">now_ms</span>());
        <span class="kw">let</span> content = row.content.<span class="fn">text</span>();
        <span class="kw">let</span> preview = <span class="fn">format!</span>(<span class="str">"{}: {content}"</span>, HEARTBEAT_PROMPT_PREVIEW_LABEL);
        (content, <span class="fn">Some</span>(preview), <span class="fn">Some</span>(<span class="fn">custom_message_value</span>(&amp;row)))
      } <span class="kw">else</span> {
        (job.prompt.<span class="fn">clone</span>(), <span class="fn">None</span>, <span class="fn">None</span>)
      };

      <span class="fn">enqueue_priority</span>(lane, QueuedItem {
        priority: QueuePriority::Background,
        message, preview, custom_message,
        queue_key,
        done: <span class="fn">Some</span>(done_tx),
        policy: TurnPolicy::Injected,
        ..
      });
    }
    <span class="kw">self</span>.work_notify.<span class="fn">notify_one</span>(); <span class="cm">// 立即唤醒 Worker 主循环消费队列</span>
    <span class="cm">// 等待最多 15 分钟 (FIRE_SETTLE_TIMEOUT_MS)；若 TurnSettle::Failed 则触发指数退避</span>
    ...
  }
}
```

### 四种做法放在一起看

| 系统 | 定时消息怎么进入上下文 | 其他关键设计 |
| --- | --- | --- |
| Claude Code | 会话记录末尾加系统标记，prompt 作为元消息进入队列，空闲时发出 | 连续空转折叠成一句总结；长指令只发一次，之后发短提醒 |
| Prime Agent | 心跳包装成自定义消息（`TurnPolicy::Injected`），走 Steer 或 FollowUp 通道 | 以 `heartbeat:<id>` 去重，新消息替换未消费的旧消息 |
| Cronex（Codex CLI） | 模型忙时，通过 `PostToolUse` 钩子把到期任务附在工具结果旁；空闲时，`Stop` 钩子拉起的后台进程执行 `codex queue` 把消息排进会话 | 还有未到期任务时，阻止 Codex 主进程退出 |
| OpenClaw | 先在 JS 沙箱里运行 `trigger.script`，对比最多 16KB 的状态，返回 `fire: true` 时才把消息发给 Agent | 条件不满足就完全不打扰模型 |
| Open Ralph Wiggum | 不往旧会话插消息：每轮结束旧进程，从磁盘 `.ralph/ralph-tasks.md` 读任务，启动全新上下文 | 避免长时间运行后上下文质量下降（Context Rot），代价是用不上缓存 |

核心取舍只有一个：**在会话内注入**能保住 Prompt Cache，又快又省；**每轮外部重启**能清空历史，防止跑了几个小时之后模型被冗长的上下文拖累。

## 小结：/loop 的五个设计要点

- **它是一个 Skill**：把命令展开成指令，由模型自己用 `CronCreate` 或 `ScheduleWakeup` 预约下一次，所以模型能调整节奏、也能主动停止。
- **只追加，不修改**：每次唤醒都在会话记录末尾加消息，前面的历史不动，请求前缀保持不变。
- **长指令只发一次**：之后的唤醒用哨兵换成三行短提醒。
- **间隔围绕 5 分钟缓存设计**：270 秒、285 秒、1200 秒这些数字都由此而来。
- **空转有折叠，忘记有兜底**：连续空转合并成一句总结；模型忘记预约时，系统替它约一次 20 分钟后的唤醒。
