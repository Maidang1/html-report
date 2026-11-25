---
title: Agent 的前缀缓存怎么测：Codex、opencode、Pydantic AI 的做法与一套测试方案
kicker: 测试方法
deck: 前缀缓存是否命中，大部分由 harness 发出的请求决定。本文拆解 Codex、opencode、Pydantic AI 和两家官方诊断接口分别在哪一层测缓存，给出一套从 CI 到线上的四层测试方案，并逐条分析这套方案的盲区和容易误判的地方。
meta:
  - openai/codex @ a1604a8（2026-11-23）
  - anomalyco/opencode @ 1906056（2026-11-23）
  - 官方文档截至 2026-11-23
footer: 源码出处：openai/codex（core/tests/suite/scenarios_world_state_prefix_tests.rs、scenarios_strict_3p_cache.rs、prompt_cache_key.rs、core/tests/common/context_snapshot.rs 与 snapshots 目录）；anomalyco/opencode（packages/ai/src/protocols/utils/cache.ts、packages/ai/test/cache-policy.test.ts、packages/ai/test/provider/anthropic-messages-cache.recorded.test.ts、PR #5422）。文档出处：pydantic.dev/docs/ai/harness/warn-on-cache-busts、platform.claude.com/docs/en/build-with-claude/cache-diagnostics、developers.openai.com/api/docs/guides/prompt-caching/diagnostics、code.claude.com/docs/en/prompt-caching。标“示意”的代码由本文编写，不来自上述项目。
index: index.html
updated: 2026-11-23
created: 2026-11-23 17:03
---

Agent 每一轮都要把完整上下文重发一遍，能不能负担得起，取决于前缀缓存的命中率。缓存失效通常不会报错，只会让账单和延迟悄悄上涨。所以前缀缓存必须像功能一样写测试，而不能等用户发现费用异常。

缓存命中与否，**绝大部分由 harness 发出的请求决定**：只要新请求的开头和上一次逐字节一致，服务端就能复用。这意味着大部分测试可以在本地离线完成，只有少量需要真实 API。本文先讲清原理，再看三个开源项目和两家官方接口分别在哪一层测，最后给出我会怎么搭这套测试，以及它的盲区。

- **原理**：缓存按前缀精确匹配，前缀里任何一个字节变了，其后的内容全部重算
- **Codex**：用 mock 服务器录下每个请求，断言前缀逐字节保留；快照把请求分成 Window，多一个 Window 就是多一次断点
- **opencode**：单测检查 `cache_control` 断点的位置和数量；用录制回放的真实调用断言第二次请求 `cache_read > 0`
- **Pydantic AI 与官方接口**：线上按“少了 5% 且至少 2,000 token”判定缓存断崖；Anthropic 和 OpenAI 的诊断接口直接返回“哪里变了”
- **方案**：每个 PR 跑离线前缀断言，每晚跑真实 API，线上监控发现的断点回写成离线用例

## 原理：缓存只认“开头逐字节相同”

Anthropic 和 OpenAI 的前缀缓存都按**前缀精确匹配**：服务端从请求开头往后比，能对上的部分按缓存价计费，从第一个不同的字节开始，后面全部重新计算。请求的拼接顺序大致是工具定义、system、历史消息、新消息，所以越靠前的内容一旦变化，损失越大。

图 1 是三种情况。正常的 agent 循环里，每一轮只在末尾追加新消息，前面全部命中。但如果有人在 system 里写了一个时间戳，第三轮的 system 和第二轮不同，从 system 开始的所有内容都要重算，哪怕历史消息一个字都没改。

这也说明了为什么大部分测试可以离线做：**只要证明“新请求以旧请求为前缀”，就证明了 harness 这一侧没有破坏缓存**。剩下的风险在服务端，比如最小缓存长度、TTL、路由，这部分才需要真实调用来验证。

```svg title="图 1：前缀匹配的三种情况" tone="light"
<svg viewBox="0 0 760 300" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Noto Sans SC', sans-serif" font-size="13">
  <rect width="760" height="300" fill="#f5f4ed"/>
  <g fill="#141413">
    <text x="20" y="45">请求 1</text>
    <text x="20" y="105">请求 2</text>
    <text x="20" y="165">请求 3（只追加）</text>
    <text x="20" y="225">请求 3'（改了 system）</text>
  </g>
  <g stroke="#1B365D" stroke-width="1">
    <rect x="170" y="26" width="80" height="30" rx="4" fill="#E4ECF5"/>
    <rect x="250" y="26" width="100" height="30" rx="4" fill="#E4ECF5"/>
    <rect x="350" y="26" width="80" height="30" rx="4" fill="#E4ECF5"/>
  </g>
  <g stroke="#b9b6aa" stroke-width="1" fill="#e8e6dc">
    <rect x="170" y="86" width="80" height="30" rx="4"/>
    <rect x="250" y="86" width="100" height="30" rx="4"/>
    <rect x="350" y="86" width="80" height="30" rx="4"/>
    <rect x="170" y="146" width="80" height="30" rx="4"/>
    <rect x="250" y="146" width="100" height="30" rx="4"/>
    <rect x="350" y="146" width="80" height="30" rx="4"/>
    <rect x="430" y="146" width="80" height="30" rx="4"/>
    <rect x="170" y="206" width="80" height="30" rx="4"/>
  </g>
  <g stroke="#1B365D" stroke-width="1" fill="#E4ECF5">
    <rect x="430" y="86" width="80" height="30" rx="4"/>
    <rect x="510" y="146" width="80" height="30" rx="4"/>
    <rect x="250" y="206" width="100" height="30" rx="4"/>
    <rect x="350" y="206" width="80" height="30" rx="4"/>
    <rect x="430" y="206" width="80" height="30" rx="4"/>
    <rect x="510" y="206" width="80" height="30" rx="4"/>
  </g>
  <g fill="#141413" text-anchor="middle" font-size="12">
    <text x="210" y="46">tools</text><text x="300" y="46">system</text><text x="390" y="46">消息 1</text>
    <text x="210" y="106">tools</text><text x="300" y="106">system</text><text x="390" y="106">消息 1</text><text x="470" y="106">消息 2</text>
    <text x="210" y="166">tools</text><text x="300" y="166">system</text><text x="390" y="166">消息 1</text><text x="470" y="166">消息 2</text><text x="550" y="166">消息 3</text>
    <text x="210" y="226">tools</text><text x="300" y="226">system + 时间戳</text><text x="390" y="226">消息 1</text><text x="470" y="226">消息 2</text><text x="550" y="226">消息 3</text>
  </g>
  <g fill="#504e49" font-size="12">
    <text x="610" y="106">只算新增部分</text>
    <text x="610" y="166">只算新增部分</text>
    <text x="610" y="226">从 system 起全部重算</text>
  </g>
  <line x1="250" y1="196" x2="250" y2="246" stroke="#1B365D" stroke-width="2" stroke-dasharray="4 3"/>
  <g font-size="12" fill="#141413">
    <rect x="170" y="268" width="16" height="14" rx="3" fill="#e8e6dc" stroke="#b9b6aa"/>
    <text x="194" y="280">命中缓存，按缓存价计费</text>
    <rect x="360" y="268" width="16" height="14" rx="3" fill="#E4ECF5" stroke="#1B365D"/>
    <text x="384" y="280">写入缓存或重新计算</text>
    <text x="540" y="280">虚线：第一个不同的字节</text>
  </g>
</svg>
```

## 四层测试：从最便宜的离线断言到线上诊断

把“缓存有没有被破坏”拆开，可以在四个层次上验证，越往下越接近真实情况，也越贵、越慢、越不稳定：

```svg title="图 2：四层测试与各项目的位置" tone="light"
<svg viewBox="0 0 760 330" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Noto Sans SC', sans-serif" font-size="13">
  <rect width="760" height="330" fill="#f5f4ed"/>
  <g stroke="#d8d5c8" fill="#faf9f5">
    <rect x="20" y="20" width="620" height="64" rx="8"/>
    <rect x="20" y="94" width="620" height="64" rx="8"/>
    <rect x="20" y="168" width="620" height="64" rx="8"/>
    <rect x="20" y="242" width="620" height="64" rx="8"/>
  </g>
  <g font-weight="600" fill="#1B365D" font-family="ui-monospace, Menlo, monospace" font-size="12">
    <text x="36" y="47">L1</text><text x="36" y="121">L2</text><text x="36" y="195">L3</text><text x="36" y="269">L4</text>
  </g>
  <g fill="#141413" font-weight="600">
    <text x="70" y="47">离线前缀断言</text>
    <text x="70" y="121">断点放置单测</text>
    <text x="70" y="195">录制回放的真实调用</text>
    <text x="70" y="269">线上监控与诊断</text>
  </g>
  <g fill="#504e49" font-size="12">
    <text x="70" y="70">mock 服务器录请求，断言新请求以旧请求为前缀</text>
    <text x="70" y="144">检查请求体里 cache_control 的位置和数量</text>
    <text x="70" y="218">同一请求连发两次，断言第二次 cache_read &gt; 0</text>
    <text x="70" y="292">每轮记录缓存读写量，断崖告警，调诊断接口查原因</text>
  </g>
  <g fill="#141413" font-size="12" text-anchor="end">
    <text x="624" y="58">Codex</text>
    <text x="624" y="132">opencode</text>
    <text x="624" y="206">opencode</text>
    <text x="624" y="272">Pydantic AI</text>
    <text x="624" y="290">Anthropic / OpenAI 诊断</text>
  </g>
  <line x1="690" y1="30" x2="690" y2="296" stroke="#1B365D" stroke-width="1.5"/>
  <path d="M684 288 L690 300 L696 288 Z" fill="#1B365D"/>
  <g fill="#504e49" font-size="12">
    <text x="702" y="40">便宜</text><text x="702" y="56">稳定</text>
    <text x="702" y="276">贵</text><text x="702" y="292">接近真实</text>
  </g>
</svg>
```

| 层 | 测什么 | 能证明什么 | 证明不了什么 | 谁在做 |
|---|---|---|---|---|
| L1 离线前缀断言 | mock 服务器录下请求，比较相邻请求 | harness 只追加、请求设置不变 | 服务端是否真的命中 | Codex |
| L2 断点放置单测 | 编译后的请求体里 `cache_control` 的位置和数量 | 显式断点放对了、没超上限 | 断点之间的内容是否稳定 | opencode |
| L3 录制回放的真实调用 | 同一请求连发两次，看 `cache_read` | 服务端在这个场景下确实命中 | 线上真实流量的情况 | opencode |
| L4 线上监控与诊断 | 每轮的缓存读写量；官方诊断接口 | 真实流量下有没有回归，以及原因 | 回归发生前就拦住它 | Pydantic AI、Anthropic、OpenAI |

## Codex：mock 服务器加逐字节断言，快照里的 Window 就是断点

Codex 的 core 测试里有一个专门的场景系列（`scenarios_*`），用 wiremock 启动假的 Responses 服务器，让 agent 跑完多轮对话，再从 `mock.requests()` 拿到全部请求做断言。和缓存相关的断言有两类。

### 第一类：状态变化之后，旧前缀必须逐字节保留

`scenarios_world_state_prefix_tests.rs` 模拟一个扩展往上下文里注入指令：前两轮是 A，第三轮改成 B，压缩时再改成 C。测试断言：第三轮只是在末尾**追加**了 B，A 仍然留在原位；第二轮的整个 input 是第三轮 input 的前缀。

这条断言防的是最常见的错误写法：配置变了，就去改写 system 或者早先的消息。Codex 的设计原则是“变化一律追加”（权限、cwd、effort 变化都以新消息的形式插入），这个测试把原则变成了可以自动检查的规则。压缩是唯一允许重写的时机，所以 C 只出现在压缩后的新历史里。

```rust title="codex-rs/core/tests/suite/scenarios_world_state_prefix_tests.rs:164-174"
    assert_eq!(prefix_texts(&requests[0].input()), vec![prefix_a.clone()]);
    assert_eq!(prefix_texts(&requests[1].input()), vec![prefix_a.clone()]);
    assert_eq!(prefix_texts(&requests[2].input()), vec![prefix_a, prefix_b]);
    assert_eq!(prefix_texts(&replacement), vec![prefix_c.clone()]);
    assert_eq!(prefix_texts(&requests[4].input()), vec![prefix_c]);
    // Updating the policy preserves the entire old request prefix byte-for-byte.
    let unchanged_input = requests[1].input();
    assert_eq!(
        &requests[2].input()[..unchanged_input.len()],
        unchanged_input
    );
```

### 第二类：工具列表在运行中变化，工具前缀也不能动

`scenarios_strict_3p_cache.rs` 在四轮之间对一个 MCP 服务器做注册、更换端点、删除。工具定义位于请求最前面，一旦变化，整个请求都要重算。测试断言 8 个请求的 `additional_tools` 全部相同，失败信息直接写明“MCP registration or refresh changed the eager tool prefix”。工具集合的变化改由 Code Mode 的 `exec` 在运行时发现，而不是去改工具定义。

```rust title="codex-rs/core/tests/suite/scenarios_strict_3p_cache.rs:131-145"
    // Each of four turns needs two requests: one calls exec, the next includes its output.
    assert_eq!(requests.len(), 8);
    let prefix = requests[0].inputs_of_type("additional_tools");
    assert!(
        !prefix.is_empty(),
        "Responses Lite must supply a real tool prefix"
    );
    for (index, request) in requests.iter().enumerate() {
        assert_eq!(
            request.inputs_of_type("additional_tools"),
            prefix,
            "request {}: MCP registration or refresh changed the eager tool prefix",
            index + 1,
        );
    }
```

### 快照把请求分组成 Window，多一个 Window 就多一次断点

逐条写断言容易遗漏，所以 Codex 还有一层快照。`context_snapshot.rs` 里的 `group_requests` 把相邻请求逐项比较：只要新请求不再是上一个请求的延长（被截断、在某一项分叉、完全重复），或者请求设置变了（包括 `prompt_cache_key`，注释里专门说明了这一点），就另起一个 Window。

快照文件里每个 Window 只列出新增的部分。审查代码时只要看 Window 的数量和边界说明，就知道这次改动有没有多引入断点。图 3 是一份真实快照的结构：请求 1 到 4 都在 Window 1 里只追加，请求 4 触发压缩，请求 5 从第 06 项开始分叉，于是开启 Window 2。

```rust title="codex-rs/core/tests/common/context_snapshot.rs:191-256"
fn capture_body(mut body: Value) -> (Value, Value) {
    let settings = body
        .as_object_mut()
        .expect("request body should be an object");
    let input = settings
        .remove("input")
        .expect("request should contain input");
    // Telemetry does not affect request context. Keep the cache key in settings so
    // a change to it starts a new window.
    settings.remove("client_metadata");
    (input, body)
}

fn group_requests(requests: &[CapturedRequest]) -> Vec<Window<'_>> {
    let mut windows: Vec<Window> = Vec::new();
    for request in requests {
        let previous = windows
            .last()
            .and_then(|window| window.requests.last())
            .map(|entry| entry.request);
        let shared = previous
            .map(|previous| {
                previous
                    .input
                    .iter()
                    .zip(&request.input)
                    .take_while(|(left, right)| left == right)
                    .count()
            })
            .unwrap_or(0);
        let boundary = previous.and_then(|previous| {
            if shared < previous.input.len() {
                Some(if shared == request.input.len() {
                    InputBoundary::Truncated(shared)
                } else {
                    InputBoundary::Diverged(shared)
                })
            } else if shared == request.input.len() {
                Some(InputBoundary::Repeated)
            } else if request.settings.is_none() {
                Some(InputBoundary::SettingsUnavailable)
            } else {
                None
            }
        });
        let new_window = boundary.is_some()
            || previous.is_none_or(|previous| previous.settings != request.settings);
        if new_window {
            windows.push(Window {
                settings: request.settings.as_ref(),
                boundary,
                requests: Vec::new(),
            });
        }
        windows
            .last_mut()
            .expect("first request starts a window")
            .requests
            .push(WindowRequest {
                request,
                suffix_start: if new_window { 0 } else { shared },
            });
    }
    windows
}
```

```text title="core/tests/suite/snapshots/…retained_tool_output_survives_compaction_and_resume.snap:34-49"
06:function_call_output/codex_app.send_message_to_thread:Investigate the failing tests; report your findings without changing files.
07:configuration_update:reasoning={"effort":"low"}
-- request 2 (turn) --
08:message/assistant:
    I will investigate the tests.
09:function_call_output/codex_app.task_details:Investigate the failing tests carefully. Investigate the failing tests carefully. Investigate the failin...s carefully. Investigate the failing tests carefully. [hash=3c65017b6f98a38f]
-- request 3 (turn) --
10:message/assistant:
    I will use those task details.
11:function_call_output/codex_app.send_message_to_thread:Report your findings without changing files.
-- request 4 (compaction) --
12:function_call_output/example.progress:Output exceeded the available model context and was truncated
13:compaction_trigger

## Window 2 (after request 4: input diverged at item 06)
-- request 5 (turn) --
```

```svg title="图 3：Codex 快照中的 Window 划分（依据上面这份快照绘制）" tone="light"
<svg viewBox="0 0 760 290" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Noto Sans SC', sans-serif" font-size="12">
  <rect width="760" height="290" fill="#f5f4ed"/>
  <rect x="16" y="14" width="728" height="178" rx="8" fill="#faf9f5" stroke="#d8d5c8"/>
  <text x="30" y="36" fill="#141413" font-weight="600" font-size="13">Window 1</text>
  <rect x="16" y="204" width="728" height="72" rx="8" fill="#faf9f5" stroke="#1B365D"/>
  <text x="30" y="226" fill="#141413" font-weight="600" font-size="13">Window 2（after request 4: input diverged at item 06）</text>
  <g fill="#141413">
    <text x="30" y="66">请求 1</text><text x="30" y="100">请求 2</text><text x="30" y="134">请求 3</text><text x="30" y="168">请求 4 压缩</text><text x="30" y="258">请求 5</text>
  </g>
  <g stroke="#1B365D" fill="#E4ECF5">
    <rect x="130" y="50" width="320" height="24" rx="3"/>
    <rect x="450" y="84" width="80" height="24" rx="3"/>
    <rect x="530" y="118" width="80" height="24" rx="3"/>
    <rect x="610" y="152" width="80" height="24" rx="3"/>
  </g>
  <g stroke="#b9b6aa" fill="#e8e6dc">
    <rect x="130" y="84" width="320" height="24" rx="3"/>
    <rect x="130" y="118" width="400" height="24" rx="3"/>
    <rect x="130" y="152" width="480" height="24" rx="3"/>
    <rect x="130" y="242" width="240" height="24" rx="3"/>
  </g>
  <rect x="370" y="242" width="200" height="24" rx="3" fill="#E4ECF5" stroke="#1B365D"/>
  <g fill="#141413" text-anchor="middle">
    <text x="290" y="67">00–07 工具、指令、环境、首条消息</text>
    <text x="490" y="101">08–09</text><text x="570" y="135">10–11</text><text x="650" y="169">12–13</text>
    <text x="250" y="259">00–05 与之前相同</text>
    <text x="470" y="259">06 起为压缩后的新历史</text>
  </g>
  <line x1="370" y1="236" x2="370" y2="272" stroke="#1B365D" stroke-width="2" stroke-dasharray="4 3"/>
  <g font-size="12" fill="#504e49">
    <text x="600" y="259">分叉点 = 新断点</text>
  </g>
</svg>
```

> 快照里工具命名空间的 hash 会随 Cargo 和 Bazel 构建变化，`scenarios_strict_3p_cache.rs` 在写快照前把它替换成 `<INITIAL_BUILD_HASH>`，但前面的 `assert_eq!` 仍然逐字节比较真实前缀。快照负责“看得懂”，断言负责“不放过”，两者分工。

## opencode：断点放置单测，加上录制回放的命中测试

opencode 的 `packages/ai` 要同时支持十几家模型服务商，而各家的缓存协议不同：OpenAI 自动缓存，Anthropic、Vertex、Bedrock 需要显式断点，Gemini 走带外缓存。所以它的测试重点是**每家都把断点放对**，并且**真的能命中**。

### 断点放置：位置、数量、上限

Anthropic 每个请求最多 4 个 `cache_control` 断点，多出来的不会报错，只会被忽略。opencode 用一个计数器分配断点，用完了就把多余的记为丢弃并打警告日志：

```ts title="packages/ai/src/protocols/utils/cache.ts:29-35"
export const cacheControl = () => {
  const breakpoints = newBreakpoints(4)
  return (cache: CacheHint | undefined) => {
    if (cache === undefined || !take(breakpoints)) return undefined
    return { type: "ephemeral" as const, ttl: cache.ttl }
  }
}
```

`cache-policy.test.ts` 的 20 多个用例按服务商断言编译后的请求体。默认的 `auto` 策略在 Anthropic 上标记最后一个工具、system 的首尾两段和最后一条消息；在 OpenAI 和 Gemini 上什么也不标；在 Bedrock 上改用 `cachePoint`。另外还有“保留手动断点时仍不超过 4 个”“先给工具去重再计数”等用例。

```ts title="packages/ai/test/cache-policy.test.ts:63-97"
  it.effect("'auto' marks the last tool, first and last system parts, and final message boundary on Anthropic", () =>
    Effect.gen(function* () {
      const prepared = yield* compileRequest(
        ai.request({
          model: anthropicModel,
          system: [
            { type: "text", text: "Base agent" },
            { type: "text", text: "Project instructions" },
          ],
          tools: [{ name: "t1", description: "t1", inputSchema: { type: "object", properties: {} } }],
          messages: [
            Message.user("first user"),
            Message.assistant("assistant reply"),
            Message.user("latest user message"),
          ],
          cache: "auto",
        }),
      )

      expect(prepared.body).toMatchObject({
        tools: [{ name: "t1", cache_control: { type: "ephemeral" } }],
        system: [
          { type: "text", text: "Base agent", cache_control: { type: "ephemeral" } },
          { type: "text", text: "Project instructions", cache_control: { type: "ephemeral" } },
        ],
        messages: [
          { role: "user", content: [{ type: "text", text: "first user" }] },
          { role: "assistant", content: [{ type: "text", text: "assistant reply" }] },
          {
            role: "user",
            content: [{ type: "text", text: "latest user message", cache_control: { type: "ephemeral" } }],
          },
        ],
      })
    }),
```

- PR #5422 还给工具列表加了排序，让每次请求的工具顺序一致。工具顺序不稳定是很隐蔽的破坏点，因为工具定义位于请求的最前面。

### 真实命中：同一请求连发两次

断点放对了，不代表服务端真的命中。`anthropic-messages-cache.recorded.test.ts` 把同一个请求发两次，只断言第二次的 `cacheReadInputTokens > 0`；第一次可能是写入，也可能已经是热的，所以不做断言。测试第一次带 API key 真实运行并录成 cassette，之后 CI 里回放。

有两个细节值得照抄。第一，system prompt 用了一段约 5k token 的固定文本（`LARGE_CACHEABLE_SYSTEM`），因为前缀低于服务商的最小缓存长度时根本不会缓存，测试会误报失败。第二，另一个用例构造了 11 轮工具调用的长对话，验证长工具链仍落在缓存的回看范围内。

```ts title="packages/ai/test/provider/anthropic-messages-cache.recorded.test.ts:13-26"
// Two identical generations in a row. The first call writes the prefix into
// Anthropic's cache; the second should report a cache read against the same
// prefix. Cassette captures both interactions in order.
const cacheRequest = ai.request({
  id: "recorded_anthropic_cache",
  model,
  system: [{ type: "text", text: LARGE_CACHEABLE_SYSTEM, cache: new CacheHint({ type: "ephemeral" }) }],
  prompt: "Say hi.",
  // Manual hint on the system part is the only marker we want here — skip the
  // auto-policy's latest-user-message breakpoint so the cassette body matches.
  cache: "none",
  maxTokens: 16,
  temperature: 0,
})
```

```ts title="packages/ai/test/provider/anthropic-messages-cache.recorded.test.ts:75-87"
  recorded.effect.with("writes then reads cache_control on identical second call", { tags: ["cache"] }, () =>
    Effect.gen(function* () {
      const first = yield* LLMClient.generate(cacheRequest)
      // The first call may write the cache (cacheWriteInputTokens > 0) or it
      // may be a fresh miss (both fields 0) depending on whether the prefix is
      // already warm on Anthropic's side. The assertion that matters is that
      // the SECOND call reports a non-zero cache read.
      expect(first.usage?.cacheReadInputTokens ?? 0).toBeGreaterThanOrEqual(0)

      const second = yield* LLMClient.generate(cacheRequest)
      expect(second.usage?.cacheReadInputTokens ?? 0).toBeGreaterThan(0)
    }),
  )
```

## 线上：Pydantic AI 判定缓存断崖，官方接口告诉你哪里变了

前三层都在发布前运行，线上还需要两样东西：**发现**命中率掉了，以及**定位**是哪里变了。

### 发现：按“已建立的前缀”判定断崖

Pydantic AI Harness 的 `WarnOnCacheBusts` 是一个只观察、不修改请求的组件。它把每个会话的“已建立前缀”记为 `cache_read + cache_write`，当某次请求读到的缓存比这个值**少 5% 以上，并且至少少 2,000 token** 时，发出一次 `CacheBustWarning`。文档说这正是 Claude Code 判定缓存未命中的阈值。它还会根据服务商的缓存保留时长区分原因：TTL 过期和刚做过压缩不告警，只有意外和原因不明的才告警。

```python title="pydantic.dev/docs/ai/harness · WarnOnCacheBusts（文档示例）"
from pydantic_ai import Agent
from pydantic_ai.capabilities import Caching
from pydantic_ai_harness import WarnOnCacheBusts

agent = Agent('anthropic:claude-opus-5-5', capabilities=[Caching(), WarnOnCacheBusts()])
```

- 告警走 Python 标准库 `warnings`：CI 里可以用 `warnings.filterwarnings('error', category=CacheBustWarning)` 把它升级为失败
- 它只知道“掉了”，不知道“为什么掉”；定位要靠下面的诊断接口

```svg title="图 4：断崖判定示意（数值为虚构，仅说明规则）" tone="light"
<svg viewBox="0 0 760 300" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Noto Sans SC', sans-serif" font-size="12">
  <rect width="760" height="300" fill="#f5f4ed"/>
  <line x1="70" y1="250" x2="720" y2="250" stroke="#85837b"/>
  <line x1="70" y1="30" x2="70" y2="250" stroke="#85837b"/>
  <text x="20" y="36" fill="#504e49">token</text>
  <g fill="#e8e6dc" stroke="#b9b6aa">
    <rect x="100" y="210" width="40" height="40"/>
    <rect x="175" y="180" width="40" height="70"/>
    <rect x="250" y="150" width="40" height="100"/>
    <rect x="325" y="122" width="40" height="128"/>
    <rect x="475" y="96" width="40" height="154"/>
    <rect x="625" y="70" width="40" height="180"/>
  </g>
  <rect x="400" y="215" width="40" height="35" fill="#E4ECF5" stroke="#1B365D"/>
  <rect x="550" y="232" width="40" height="18" fill="#E4ECF5" stroke="#1B365D"/>
  <polyline points="120,205 195,176 270,146 345,118 420,96 495,92 570,70 645,66" fill="none" stroke="#1B365D" stroke-width="1.5" stroke-dasharray="5 4"/>
  <g fill="#141413" text-anchor="middle">
    <text x="120" y="268">1</text><text x="195" y="268">2</text><text x="270" y="268">3</text><text x="345" y="268">4</text>
    <text x="420" y="268">5</text><text x="495" y="268">6</text><text x="570" y="268">7</text><text x="645" y="268">8</text>
    <text x="395" y="288">轮次</text>
  </g>
  <g fill="#141413">
    <text x="372" y="184">第 5 轮：少了 &gt;5% 且 ≥2000</text>
    <text x="372" y="200" font-weight="600">→ 告警（unexpected）</text>
    <text x="528" y="40">第 7 轮：距上次超过 TTL</text>
    <text x="528" y="56" font-weight="600">→ 判为过期，不告警</text>
  </g>
  <line x1="570" y1="62" x2="570" y2="226" stroke="#85837b" stroke-dasharray="2 3"/>
  <g font-size="12" fill="#141413">
    <rect x="90" y="14" width="16" height="12" fill="#e8e6dc" stroke="#b9b6aa"/><text x="112" y="25">cache_read</text>
    <line x1="200" y1="20" x2="224" y2="20" stroke="#1B365D" stroke-width="1.5" stroke-dasharray="5 4"/><text x="230" y="25">已建立前缀 = read + write</text>
  </g>
</svg>
```

### 定位：让 API 比较相邻两次请求

两家都提供了诊断接口：在请求里带上上一次响应的 id，服务端会比较两次请求的结构，返回第一个不同的地方。

**Anthropic**：每一轮都带 `diagnostics.previous_message_id`（第一轮传 `null`），响应里的 `diagnostics.cache_miss_reason.type` 取值有 `model_changed`、`system_changed`、`tools_changed`、`messages_changed` 等，并附一个 `cache_missed_input_tokens` 的估计值。注意每一轮都要带这个字段，中间漏一次比较链就断了；改了 `thinking`、`output_config` 等设置时会返回 `unavailable`。

**OpenAI**（Responses API，GPT-5.6 及以后）：设置 `prompt_cache_options.comparison_response_id`，响应里的 `prompt_cache_diagnostics` 给出 `cache_hit` 或 `cache_miss`，未命中时附带 `reason`，取值包括 `tools_changed`、`reasoning_effort_changed`、`prompt_cache_key_changed`、`context_compacted`、`input_changed` 等。

```json title="示意：Anthropic 诊断的请求与返回（字段名取自 cache-diagnostics 文档）"
// 请求（第 2 轮起）
{ "diagnostics": { "previous_message_id": "msg_..." } }

// 响应
{
  "diagnostics": {
    "cache_miss_reason": {
      "type": "system_changed",
      "cache_missed_input_tokens": 41850
    }
  }
}
```

## 如果让我来做：一条从 CI 到线上的流水线

我会按图 5 搭三段：每个 PR 跑 L1 和 L2，每晚跑 L3，线上跑 L4；线上发现的每一次意外断点，都回写成一个 L1 用例。这样最贵的测试跑得最少，而回归一旦发生过一次，就会被最便宜的测试永久拦住。

```svg title="图 5：测试流水线与反馈回路" tone="light"
<svg viewBox="0 0 760 320" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Noto Sans SC', sans-serif" font-size="12">
  <rect width="760" height="320" fill="#f5f4ed"/>
  <defs><marker id="a5" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 Z" fill="#1B365D"/></marker></defs>
  <g stroke="#d8d5c8" fill="#faf9f5">
    <rect x="16" y="16" width="230" height="232" rx="10"/>
    <rect x="265" y="16" width="230" height="232" rx="10"/>
    <rect x="514" y="16" width="230" height="232" rx="10"/>
  </g>
  <g font-weight="600" font-size="13" fill="#141413">
    <text x="30" y="42">每个 PR（CI）· L1 + L2</text>
    <text x="279" y="42">每晚 / 发版前 · L3</text>
    <text x="528" y="42">线上 · L4</text>
  </g>
  <g stroke="#b9b6aa" fill="#ffffff">
    <rect x="30" y="58" width="202" height="40" rx="6"/><rect x="30" y="122" width="202" height="40" rx="6"/><rect x="30" y="186" width="202" height="48" rx="6"/>
    <rect x="279" y="58" width="202" height="40" rx="6"/><rect x="279" y="122" width="202" height="40" rx="6"/><rect x="279" y="186" width="202" height="48" rx="6"/>
    <rect x="528" y="58" width="202" height="40" rx="6"/><rect x="528" y="122" width="202" height="40" rx="6"/><rect x="528" y="186" width="202" height="48" rx="6"/>
  </g>
  <g fill="#141413">
    <text x="42" y="83">场景脚本驱动 agent 跑多轮</text>
    <text x="42" y="147">mock 服务器录下全部请求</text>
    <text x="42" y="207">前缀检查器 + Window 快照</text>
    <text x="42" y="224" fill="#504e49">断点数与白名单不符即失败</text>
    <text x="291" y="83">每家服务商一个真实场景</text>
    <text x="291" y="147">记录 cache_read / write</text>
    <text x="291" y="207">第 2 次起 cache_read &gt; 0</text>
    <text x="291" y="224" fill="#504e49">命中率低于下限即失败</text>
    <text x="540" y="83">每轮埋点：读、写、未命中</text>
    <text x="540" y="147">断崖检测（5% 且 2000）</text>
    <text x="540" y="207">调诊断接口拿 miss 原因</text>
    <text x="540" y="224" fill="#504e49">按原因聚合、告警</text>
  </g>
  <g stroke="#1B365D" stroke-width="1.4" fill="none" marker-end="url(#a5)">
    <line x1="131" y1="98" x2="131" y2="120"/><line x1="131" y1="162" x2="131" y2="184"/>
    <line x1="380" y1="98" x2="380" y2="120"/><line x1="380" y1="162" x2="380" y2="184"/>
    <line x1="629" y1="98" x2="629" y2="120"/><line x1="629" y1="162" x2="629" y2="184"/>
    <line x1="246" y1="132" x2="263" y2="132"/><line x1="495" y1="132" x2="512" y2="132"/>
    <path d="M629 248 L629 290 L131 290 L131 252"/>
  </g>
  <text x="250" y="308" fill="#141413">线上每发现一次意外断点，就把场景回写成一个 L1 用例</text>
</svg>
```

### L1 的核心：一个只追加检查器

检查器只做两件事：比较相邻请求的请求级设置，以及检查消息列表是否只在末尾追加。允许断开的地方（压缩、主动切换模型）必须显式登记到白名单里并写明原因，其他任何断点都让测试失败。

序列化时**不要排序 key**：发给服务端的就是这个字节序列，key 顺序本身也会影响前缀。设置的比较范围要包括 thinking、effort、cache key，因为 Claude 会把 effort 渲染进 prompt，改了它，即使消息一个字没变，缓存也会失效。

```python title="示意：只追加检查器（本文编写）"
import json

SETTINGS = ("model", "tools", "system", "tool_choice", "thinking",
            "output_config", "reasoning", "prompt_cache_key")

def canon(v):
    # 不排序 key：key 的顺序就是发出去的字节顺序
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))

def check_append_only(requests, allowed_breaks):
    """requests: 按时间顺序录下的请求体；allowed_breaks: {请求序号: 原因}"""
    problems = []
    for i in range(1, len(requests)):
        prev, cur, n = requests[i - 1], requests[i], i + 1
        if n in allowed_breaks:
            continue
        for key in SETTINGS:
            if canon(prev.get(key)) != canon(cur.get(key)):
                problems.append(f"request {n}: setting '{key}' changed")
        old, new = prev["messages"], cur["messages"]
        shared = 0
        while shared < min(len(old), len(new)) and canon(old[shared]) == canon(new[shared]):
            shared += 1
        if shared < len(old):
            problems.append(f"request {n}: messages diverged at item {shared:02}")
    return problems
```

### 每个场景一个用例

L1 的价值取决于场景是否覆盖了会改动上下文的操作。我会按下表各写一个场景，每个场景的预期断点数写进白名单：

| 场景 | 操作 | 预期 |
|---|---|---|
| 基线 | 10 轮对话，每轮 2 到 3 次工具调用 | 0 个断点 |
| 动态上下文 | system 或环境信息里有日期、cwd、git 状态 | 0 个断点；变化以追加消息表达 |
| 工具变化 | MCP 服务器注册、刷新、删除；技能加载 | 工具前缀不变；按名字排序 |
| 配置变化 | 改权限模式、切 effort、开关 thinking | 以追加消息或按消息的配置表达；Claude 上改顶层 effort 必然断开，要么禁止，要么登记 |
| thinking 回传 | 多轮带工具调用，开启 thinking | thinking block / reasoning item 原样回传，不被裁剪或改写 |
| 压缩 | 触发自动压缩 | 恰好 1 个断点，压缩后的下一轮起重新稳定 |
| 切换模型 | 会话中途换模型 | 恰好 1 个断点，并登记原因 |
| 恢复与分叉 | resume、fork、子代理 | resume 后第一轮与断开前前缀一致；fork 共享父会话前缀 |

L3 每家服务商只需要一两个场景：一个超过最小缓存长度的长 system 连发两次，一个长工具链对话。断言写成“第 2 次起 `cache_read > 0`，且整段对话的命中率不低于某个下限”，下限按首次录制结果留出余量。

## 潜在问题：这套测试会漏掉什么，又会误报什么

### 盲区：L1 全绿，线上仍可能不命中

- **只验证了 harness，没验证服务端**。L1 证明请求字节没变，但服务端是否命中还取决于最小缓存长度、TTL、路由到哪台机器。所以 L3 和 L4 不能省。
- **请求体之外的影响因素**。有些设置不在消息里，却会影响服务端渲染出的 prompt，比如 Claude 的 effort 会被渲染进 prompt，beta 头和 `tool_choice` 也会影响。检查器如果只比较 messages，就会漏掉这些。比较范围要包括全部请求级设置和会影响缓存的 HTTP 头。
- **mock 服务器回放的是假响应**。如果 harness 把真实响应里的某些字段（thinking 签名、加密推理内容、工具调用 id）改写后再回传，用假响应可能测不出来。mock 返回的数据要尽量贴近真实格式，最好直接用 L3 录下来的真实响应。
- **录制的 cassette 会过期**。回放测试证明的是“录制那天命中了”。服务商改了缓存行为（最小长度、TTL、路由），回放仍然是绿的。所以要定期重新录制，或者每晚至少跑一次真实调用。

### 不稳定：真实调用的结果有波动

- **第一次调用的冷热状态不确定**。同样的前缀可能已经被别的测试或别的人预热过。opencode 的做法是只对第二次调用做断言，第一次只要求 `>= 0`。
- **TTL 和时序**。两次调用之间如果被 CI 排队拖过 5 分钟，就会因为过期而不命中。真实测试要在同一个进程里紧接着发，并记录时间间隔，超过 TTL 的结果判为无效，而不是失败。
- **并发请求**。并行的子代理同时发出相同前缀的请求时，缓存可能还没写好，几个请求都会未命中。这不一定是 bug，但会拉低命中率。测试里要区分串行和并行场景。
- **路由与 `prompt_cache_key`**。OpenAI 文档提醒，没有分组需求时不要设置 `prompt_cache_key`，要设就保持稳定；key 变化本身就会被诊断为未命中的原因。测试要覆盖 key 在子代理、fork、resume 之间的继承规则（Codex 的 `prompt_cache_key.rs` 就在测这个）。

### 误读：指标和阈值本身的局限

- **平均命中率会掩盖断点**。一段 50 轮的对话中间断一次，平均命中率可能仍有 90%。要看每一轮的曲线，或者直接数断点个数。
- **固定阈值不适合所有会话**。“少了 5% 且至少 2,000 token”对短会话不敏感：前缀只有 3,000 token 时，整段失效也可能达不到 2,000。短 prompt 场景要单独设阈值，或者用 L1 的断点计数代替。
- **诊断接口是尽力而为**。两家诊断返回的 token 数都是估算值，Anthropic 的是按字节长度估算，不能当计费数字用。诊断记录的保留时间也很短，只能比较时间上相邻的请求；而且只报告第一个不同之处，修完一个还要再测一次。
- **各家用量字段不同**。Anthropic 是 `cache_read_input_tokens` 和 `cache_creation_input_tokens`，OpenAI 是 `cached_tokens`，Gemini 又是另一套。多服务商的 harness 要先统一成同一套字段，再计算命中率。

### 取舍：测试会反过来约束设计

- **预期内的断点必须登记**。压缩和切换模型本来就会断开，白名单不写清楚，测试就会形同虚设或者频繁误报。每条白名单都要写原因，并在代码审查时确认。
- **快照会频繁变化**。任何提示词改动都会让快照更新，审查者容易习惯性地直接接受。Codex 的做法值得借鉴：快照只负责让变化好看懂，真正拦截回归的是快照之外的 `assert_eq!`；会随构建变化的内容（如工具 hash）在快照里替换成占位符。
- **“只追加”有代价**。为了不破坏前缀，配置变化只能追加，过时的信息会一直留在上下文里，直到下一次压缩。测试锁定了“只追加”，也就锁定了这个代价，需要和上下文长度的预算一起权衡。

## 小结

1. 前缀缓存的大部分风险在 harness 这一侧，**用 mock 服务器做“只追加”断言**，是性价比最高的一层，应该放进每个 PR 的 CI。
2. 需要显式断点的服务商，要用单测检查**断点的位置和数量**；所有服务商都至少要有一个**真实的“连发两次”测试**。
3. 线上用**断崖检测发现问题、用官方诊断接口定位原因**，再把场景回写成离线用例。
4. 读结果时注意：平均命中率会掩盖断点，固定阈值对短会话不敏感，诊断返回的 token 数只是估算，回放测试会随服务商的变化而过期。
