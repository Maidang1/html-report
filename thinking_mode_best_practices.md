---
title: 各家模型的 thinking 模式：参数、回传规则与最佳实践
kicker: 对照整理
deck: 汇总 Anthropic、OpenAI、Google、DeepSeek、Qwen 官方文档（截至 2026-11-23）对 thinking 的控制方式、可见性、工具循环中的回传规则、缓存影响和官方建议，再用三篇“想多想少”的研究给出 agent 场景下的选择方法。
meta:
  - 官方文档截至 2026-11-23
  - 5 家厂商 · 3 篇论文
  - 面向 agent harness
footer: 主要来源：platform.claude.com/docs（Steering thinking、Extended thinking、Effort）；developers.openai.com（Reasoning models、Reasoning best practices）；aistudio.google.com/docs/thinking（原 ai.google.dev/gemini-api/docs/thinking）；api-docs.deepseek.com/guides/thinking_mode；qwen.readthedocs.io 与 huggingface.co/Qwen/Qwen3-32B；arXiv 2502.08235、2508.13141、2507.04023。各家参数和模型列表更新很快，以官方页面为准。
index: index.html
updated: 2026-11-23
created: 2026-11-23 16:34
---

thinking 是模型在给出答案或调用工具之前先生成的一段推理 token。它的本质是**用推理时的计算换正确率**：想得越多，难题上越可能做对，但延迟和费用也越高，而且这部分 token 一律按输出计费。对 agent 来说，它还牵涉到三个工程问题：思考内容要不要在工具循环里回传，上一轮的思考还在不在上下文里，以及改动 thinking 配置会不会让前缀缓存失效。

本文把五家厂商官方文档里关于这些问题的说法汇总到一起，每家一节，最后给一张横向对比表、三篇相关研究的结论，以及在 agent 里怎么选。文中的参数名和规则都来自 2026-11-23 抓取的官方页面；模型列表变化很快，用之前请以官方页面为准。

- **控制方式在收敛**：从“给 token 预算”转向“给档位（effort / level），由模型自适应决定想多少”，Anthropic、OpenAI、Gemini、DeepSeek 都是这个方向
- **原始推理基本不可见**：闭源三家只给摘要或加密内容，计费却按完整推理算；开源模型（DeepSeek、Qwen）给原文
- **工具循环里必须回传**：Claude 的 thinking block、OpenAI 的 reasoning item、Gemini 的 thought signature、DeepSeek 带工具时的 `reasoning_content`，漏传轻则推理断档，重则直接 400
- **对话中途别改参数**：Claude 改 effort 会让消息部分缓存失效；OpenAI 和 Claude 都提供了不破坏前缀的调整方式
- **想多不一定好**：在 SWE-Bench 上，过度思考和解题率负相关；两次低 effort 挑一个，接近一次高 effort 的效果，成本低 43%

## thinking 决定了什么：一次请求里花多少推理算力

对单次回答，thinking 影响三件事：难题上的正确率（多步推理、复杂调试收益明显，事实查询几乎没有收益）、首个可见 token 的延迟、以及费用。Gemini 文档给的分层很直接：事实查询、分类这类请求“thinking is not required”；比较、类比这类中等任务用默认档；复杂数学和编码任务才建议开高。

对 agent，每一轮都是一次决策：规划步骤、选工具和填参数、出错后分析原因、判断是否完成。所以 agent 里关心的不只是“想多少”，还有**在哪里想**（工具结果回来后能不能接着想，即 interleaved thinking）和**想过的能不能留下**（跨工具调用、跨用户轮次的保留规则）。下面各家都按这四个维度来写。

| 维度 | 要回答的问题 |
|---|---|
| 控制方式 | 开 / 关、token 预算，还是档位；默认值是什么；能不能完全关掉 |
| 可见性与计费 | 返回原文、摘要、加密内容还是什么都不返回；按什么计费 |
| 工具循环 | 工具调用之间能否继续思考；思考内容要不要原样回传，漏传会怎样 |
| 跨轮与缓存 | 上一轮的思考是否留在上下文；改 thinking 配置是否影响前缀缓存 |

## Anthropic Claude：adaptive 加 effort，改配置会让缓存失效

**控制方式**。新模型的写法是 `thinking: {"type": "adaptive"}`，由模型对每个请求自己判断要不要想、想多少；思考深度用 `output_config.effort` 控制，它和 `thinking` 是平级字段，不放在 thinking 对象里。文档特别提醒：`adaptive` 是 thinking 模式，不是 effort 的取值。effort 有 `low`、`medium`、`high`、`xhigh`、`max` 五档，各模型支持的档位不同；多数模型默认 `high`，Opus 5.5 和 Haiku 5.5 默认 `medium`。

旧写法 `{"type": "enabled", "budget_tokens": N}` 正在退场：4.7 及以后的模型直接返回 400，4.6 标为 deprecated，只有 4.5 一代和 Mythos Preview 还在用。预算最少 1,024 token，而且“The budget is a target rather than a strict cap”。反过来，`type: "adaptive"` 在 Opus 4.5、Sonnet 4.5、Haiku 4.5 上会返回 400。部分新模型不能完全关闭 thinking，例如 Opus 5.5 在任何档位传 `disabled` 都返回 400。

```json title="示意：adaptive + effort 的请求写法（platform.claude.com/docs · Effort）"
{
  "model": "claude-opus-5-5",
  "max_tokens": 64000,
  "thinking": { "type": "adaptive" },
  "output_config": { "effort": "medium" },
  "messages": [{ "role": "user", "content": "..." }]
}
```

### 可见性、回传与跨轮：思考块要原样带回去

**可见性**。`display` 可以是 `"summarized"`（返回思考摘要）或 `"omitted"`（thinking 字段为空）。两种情况计费一样：“What you're billed for is the same regardless of the `display` setting; only what you see changes.” 真实的思考 token 数在 `usage.output_tokens_details.thinking_tokens` 里，`output_tokens` 才是计费口径的总数。

**工具循环**。adaptive 模式自动支持 interleaved thinking，每次工具结果回来都能再想；旧的手动模式在 4.5 一代需要 beta 头 `interleaved-thinking-2025-05-14`，Opus 4.6 的手动模式则完全没有 interleaved。无论哪种，思考块都要“pass them back unmodified, particularly during tool use”，因为里面装着工具调用背后的推理。

**跨轮保留**。Opus 4.5 和 4.6 及以后的模型会把之前轮次的思考块留在上下文里，并按输入 token 计费；Sonnet 4.5、Haiku 4.5 及更早的模型会把它们剥掉。所以同一套 harness 换模型后，上下文长度和费用曲线可能明显不同。

**max_tokens**。思考计入 `max_tokens`，而且这是硬上限。文档提醒，按“不思考”估的 `max_tokens` 遇到难题时往往不够；Opus 系列在 `xhigh` / `max` 档建议从 64k 起，Sonnet 5.5 做 agentic coding 建议设到 128,000。

### 缓存与调参：不改参数，改提示

effort 的取值会被渲染进 prompt，所以**对话中途改 effort、改 `budget_tokens`、或在 enabled 和 adaptive 之间切换，都会让消息部分的缓存断点失效**。文档里的例子是 effort 从 `medium` 改成 `low` 后，`cache_read_input_tokens` 变成 0。不破坏缓存的做法有两种：按消息设置 effort（beta），或者把引导语追加到最新的 user 消息里。

官方的调参顺序是先动 effort、再动提示：effort 是“a calibrated control rather than a wording-sensitive instruction”。各模型的起步建议不同：Haiku 5.5 从 `medium` 开始，Sonnet 5.5 从 `high` 开始（agentic 或对延迟敏感的场景除外），Opus 4.7 / 4.8 做编码和 agent 时从 `xhigh` 开始。

```text title="platform.claude.com/docs · Effort 与 Steering thinking 页的建议（原文摘录）"
Set effort explicitly: The API defaults to `high` (`medium` on Claude Opus 5.5
and Claude Haiku 5.5), but the right starting point depends on your model and
workload.
Use low for speed-sensitive or simple tasks.
Test your use case.
Consider dynamic effort.
Hold top-level effort constant within cached conversations.

If you want Claude to think less often, lower the effort level before reaching
for prompt-based steering.

Extended thinking adds latency and should only be used when it will
meaningfully improve answer quality ... When in doubt, respond directly.
```

## OpenAI：effort 与 mode 分开，reasoning item 决定连续性

**控制方式**。`reasoning.effort` 的取值随模型而定，可能包括 `none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`，默认值也随模型而定（如 `gpt-5.5` 默认 `medium`），部分模型不接受 `none`。GPT-5.6 和 GPT-6 在 Responses API 上还有一个独立的 `reasoning.mode`：`standard` 是默认，`pro` 用于最难的任务。文档的说法是“Mode selects standard or pro execution, while `reasoning.effort` controls how much reasoning the model applies within that mode”。档位建议：`medium` 是多数场景的默认配置，`xhigh`“Only use when your evals show a clear benefit”。

**可见性**。原始推理不对外暴露：“Persisted reasoning provides continuity; it does not expose the model's raw reasoning.” 可以选择返回推理摘要，推理 token 数在 `usage.output_tokens_details.reasoning_tokens` 里。

```json title="示意：Responses API 的 reasoning 参数（developers.openai.com · Reasoning models）"
{
  "model": "gpt-5.6",
  "reasoning": {
    "mode": "pro",
    "effort": "medium",
    "context": "all_turns"
  },
  "max_output_tokens": 25000,
  "input": "..."
}
```

### 跨轮与无状态：每个输出 item 原样回放

推理状态的延续靠 **reasoning item**。最省事的是 `previous_response_id`；自己管历史时，要把每次响应的全部输出 item 按原顺序放进下一次请求。函数调用时尤其要把 reasoning item 和工具结果一起回传，“This lets the model continue reasoning after a tool call”。跨用户轮次是否保留由 `reasoning.context` 控制：`current_turn` 不渲染之前轮次的推理，`all_turns` 会渲染；GPT-5.6 之前的模型默认 `current_turn`，GPT-5.6 默认 `all_turns`。另外，推理只能在同一模型家族内复用，换家族会被丢弃。

**无状态与 ZDR**。`store: false` 或组织开了零数据保留时，reasoning item 默认带 `encrypted_content`，原样回放即可；旧的 `include: ["reasoning.encrypted_content"]` 仍被接受，但已不是必需。Codex 走的就是这条路。

**缓存**。文档明确说，改动 reasoning item 之前的历史可能导致这段推理被丢弃。想在对话中途调整 effort，可以在 user 消息前插入一个 `configuration_update` item，这样“preserves the original prompt prefix for prompt caching”。这一点和 Claude 的“按消息调 effort”思路一致：**调整用追加，不用改写**。

```json title="developers.openai.com · 中途调整 effort 的 configuration_update item"
{"type": "configuration_update", "reasoning": {"effort": "high"}}
```

### token 预算与提示写法：预留空间，别教它怎么想

`max_output_tokens` 包含推理、可见输出和格式 token。文档建议刚开始实验时至少预留 25,000 token。预算耗尽时响应状态为 `incomplete`，`incomplete_details.reason` 为 `max_output_tokens`，而且可能在产出任何可见内容之前就发生，这时推理 token 照样计费。

提示写法上，best practices 页（例子仍是 o1 / o3 一代，但原则沿用）给出几条：

```text title="developers.openai.com · Reasoning best practices（原文摘录）"
Keep prompts simple and direct: The models excel at understanding and
responding to brief, clear instructions.
Avoid chain-of-thought prompts: Since these models perform reasoning
internally, prompting them to "think step by step" or "explain your
reasoning" is unnecessary.
Use delimiters for clarity: Use delimiters like markdown, XML tags, and
section titles...
Try zero shot first, then few shot if needed.
Be very specific about your end goal.
Developer messages are the new system messages.
```

- 选型建议：速度和成本优先用 GPT 模型，准确性和复杂问题用推理模型；也可以组合，“o-series for agentic planning and decision-making, GPT series for task execution”
- Chat Completions API 不会把 reasoning item 放进上下文，需要跨轮推理时用 Responses API

## Google Gemini：thinkingLevel 取代 budget，signature 必须带回

**控制方式**。Gemini 3 系列用 `thinkingLevel`（`minimal` / `low` / `medium` / `high`，各模型支持的子集不同），2.5 系列不支持 level，只能用 `thinkingBudget`：设为 0 关闭，设为 -1 开启动态思考。默认值差别很大：3 Pro / 3.1 Pro 默认 `high`，部分新 Flash 默认 `medium`，Flash-Lite 默认 `minimal`。能不能关也各不相同：2.5 Pro 的预算范围是 128 到 32768，不能关；2.5 Flash 可以设 0 关闭；Gemini 3 Flash 和 Flash-Lite 不支持完全关闭，`minimal` 只是“likely will not think”。页面上已出现 Gemini 4 系列，对它们来说 `thinkingLevel` 是必填，同时传 level 和 budget 返回 400。

**可见性与计费**。`includeThoughts: true` 返回思考摘要，部件上用 `thought` 布尔值区分。计费按完整思考算，即使只返回了摘要；回传的 thought signature 也会计入输入 token。

```json title="示意：generationConfig.thinkingConfig（aistudio.google.com/docs/thinking）"
{
  "contents": [ ... ],
  "generationConfig": {
    "thinkingConfig": {
      "thinkingLevel": "low",
      "includeThoughts": true
    }
  }
}
```

### thought signature：无状态 API 下的推理延续

Gemini API 是无状态的，推理靠 **thought signature** 延续，它是“encrypted representations of the model's internal thought process”。2.5 只在开启思考且请求带函数声明时返回 signature；Gemini 3 和 4 可能在任何部件上返回。官方建议“always pass all signatures back as received”，函数调用的 signature 则是**必须**回传。用 SDK 时自动处理，只有改写历史或直接调 REST 时才要手动管理，而且不能把带 signature 的部件拼接或合并。

**选档建议**。页面给出的方法是：用足够大的 `max_output_tokens` 在各档跑一遍评测，再“pick the lowest `thinking_level` that meets your quality bar”。`low` 适合对成本和延迟敏感的高吞吐场景，`medium` 适合交互式编码和对延迟敏感的工具调用；复杂任务从 `high` 起步，`xhigh` 只留给 `high` 不够的多步难题，因为它会在简单任务上“cause overthinking or exhaust `max_output_tokens`”。想省钱时应该降档，而不是把 `max_output_tokens` 设小：后者触发 `MAX_TOKENS` 时可能返回空内容，思考 token 照样计费。

## DeepSeek 与 Qwen：原文可见，回传规则看有没有工具

**DeepSeek**。thinking 默认开启，默认 effort 为 `high`。开关是 `thinking: {"type": "enabled" | "disabled"}`（用 OpenAI SDK 时放在 `extra_body` 里），深度是 `reasoning_effort`（`low` / `high` / `max`）；传入的其他档位会被映射，如 `medium` 映射为 `high`。thinking 模式下 `temperature`、`presence_penalty`、`frequency_penalty` 不生效，设置了也不报错。思考原文在与 `content` 平级的 `reasoning_content` 字段里。

回传规则分两种情况。**没有 `tools` 参数的普通多轮对话**，`reasoning_content` 不需要回传，传了也会被忽略。**带 `tools` 参数时**，`reasoning_content` 必须在后续所有请求里完整回传，包括模型那一轮没有调用工具的情况，否则返回 400。这和“开源模型一般剥掉历史思考”的常见印象相反，接入 agent 时最容易踩坑。

```python title="示意：DeepSeek thinking 模式（api-docs.deepseek.com/guides/thinking_mode）"
resp = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=messages,
    tools=tools,
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}},
)
# 带 tools 时：把整条 assistant 消息（含 reasoning_content）原样追加回 messages
messages.append(resp.choices[0].message)
```

### Qwen3：硬开关、软开关和采样参数

Qwen3 是混合模型，同一个模型可以想也可以不想。硬开关是 chat template 的 `enable_thinking`，默认 `True`；设为 `False` 时不生成任何思考内容。软开关是在 user 或 system 消息里写 `/think`、`/no_think`，模型以对话里最近一条为准；但 `enable_thinking=False` 时软开关无效。

**采样参数**。思考模式推荐 Temperature=0.6、TopP=0.95、TopK=20、MinP=0，模型卡原话是“DO NOT use greedy decoding”，否则会降低效果并出现无限重复；非思考模式推荐 Temperature=0.7、TopP=0.8。出现重复时可以把 `presence_penalty` 调到 0 到 2 之间，但值过高可能导致语言混杂。输出长度建议 32,768 token，数学和编程难题 38,912 token。

**历史中的思考**。模型卡要求历史里只保留最终输出，不带思考内容，官方 Jinja2 模板已经自动做了；不用该模板的框架要自己保证。

```python title="示意：Qwen3 的硬开关（qwen.readthedocs.io · Quickstart）"
text = tokenizer.apply_chat_template(
    messages,
    tokenize=False,
    add_generation_prompt=True,
    enable_thinking=True,  # False 时不生成 <think> 内容，/think 软开关也失效
)
```

## 横向对比：五家在四个维度上的差异

| | Claude | OpenAI | Gemini | DeepSeek | Qwen3 |
|---|---|---|---|---|---|
| 主要控制 | `thinking.type: adaptive` + `output_config.effort` | `reasoning.effort`，另有 `reasoning.mode` | `thinkingLevel`（3 / 4），`thinkingBudget`（2.5） | `thinking.type` + `reasoning_effort` | `enable_thinking`，`/think` `/no_think` |
| 档位 | low → max 五档 | none → max，随模型 | minimal → high（4 有 xhigh） | low / high / max | 开 / 关 |
| 默认 | 多数 `high`，Opus 5.5 / Haiku 5.5 为 `medium` | 随模型，常见 `medium` | 随模型，`minimal` 到 `high` | 开启，`high` | 开启 |
| 能否关闭 | 部分新模型不能 | 部分模型不接受 `none` | 2.5 Pro、3 系列不能完全关 | 能 | 能 |
| 返回内容 | 摘要或省略 | 摘要；无状态时带加密内容 | 摘要 + signature | 原文 | 原文（`<think>`） |
| 计费 | 按完整思考计 | 按推理 token 计 | 按完整思考计，signature 计入输入 | 输出 token | 自部署 |
| 工具循环回传 | thinking block 原样回传 | reasoning item 原样回传 | 函数调用 signature 必须回传 | 带 tools 时 `reasoning_content` 必须回传，否则 400 | 模板剥除历史思考 |
| 跨轮保留 | 4.5 / 4.6+ 保留并计费，更早的剥除 | `reasoning.context` 控制，GPT-5.6 默认 `all_turns` | 靠 signature 延续 | 无 tools 时忽略 | 不保留 |
| 中途调参与缓存 | 改 effort 使消息缓存失效；按消息 effort 或提示引导可保留 | 用 `configuration_update` item 保留前缀 | 文档未说明 | 文档未说明 | 自部署，取决于推理框架 |

## 研究怎么说：想多和想少一样会失败

**The Danger of Overthinking（Cuadron 等，arXiv 2502.08235，2025-02）**。这是和 agent 最相关的一篇。作者在 OpenHands 上跑 SWE-Bench Verified，分析了 19 个模型的轨迹，归纳出三种过度思考：**Analysis Paralysis**（一直规划、迟迟不执行）、**Rogue Actions**（受挫后不等环境反馈就连发多个相互依赖的动作）、**Premature Disengagement**（凭内部推演就宣布结束，没有用环境验证）。他们用 LLM 评委给每条轨迹打 0 到 10 分的过度思考分，结论是这个分数和解题率显著负相关，推理模型的平均分（3.505）高于非推理模型（2.228）。

最实用的是成本实验：o1 高 effort 解题率 29.1%、花费 $1,400；低 effort 21.0%、$400；**跑两次低 effort、挑过度思考分更低的那次，达到 27.3%、$800，比高 effort 便宜 43%**；跑三次则达到 30.3%，反超高 effort 且便宜 15%。作者的建议是使用原生函数调用、多次低 effort 采样后择优。

> 论文自身有两处不一致：轨迹数摘要写 4018、正文写 3908；引言说推理模型的过度思考“nearly three times”，但表中均值之比约为 1.57 倍。引用时以表格数据为准。

**OptimalThinkingBench（Aggarwal 等，Meta FAIR 与 CMU，arXiv 2508.13141）**。同时测“简单题想太多”和“难题想太少”两个方向，覆盖 33 个模型，结论是“no model is able to optimally think”：多数思考模型在最简单的问题上也要花 1,300 token 以上，大型非思考模型在难题上又明显不如小得多的思考模型。降低思考长度的各种方法（长度奖励 RL、路由等）大多是用一种失败换另一种失败；在 Qwen3-8B 上加一句“Don't Overthink”的提示后缀，就能明显缩短思考并提高综合分。

**Do LLMs Overthink Basic Math Reasoning?（Srivastava 等，arXiv 2507.04023，ACL 2026 Findings）**。在 14 类基础数学任务上评测 53 个模型：推理模型多生成约 18 倍 token，准确率有时反而更低；GPT-5 和 o 系列在这些任务上从 low 到 high “zero accuracy gain”；给推理模型强行限制 token 时，准确率最多下降约 36%。结论是基础任务上不要开放式地要求“think step by step”，预算要有意识地设置。

## 落到 agent harness：怎么选、怎么接

三份研究和五家文档的建议可以合成一套做法：

1. **起点用中档或自适应**：Claude 用 adaptive 加模型推荐的起步档，OpenAI 用 `medium`，Gemini 从默认档起，在评测中逐档下调，选能达标的最低档。
2. **按任务分流，而不是全程一个档**：分类、路由、摘要这类轻量调用关掉 thinking 或用最低档；规划、出错恢复、完成判定这些环节值得多想。分流优先用不同的子代理或不同的请求流，各自固定配置，避免在同一对话里反复改参数。
3. **对话中途调整用追加，不用改写**：Claude 用按消息的 effort 或在最新 user 消息里加引导语，OpenAI 用 `configuration_update` item。直接改顶层参数会让缓存失效（Claude 已有文档证实）。
4. **回传协议逐家适配**：Claude 回传 thinking block，OpenAI 回传全部 reasoning item（无状态时带加密内容），Gemini 回传 signature，DeepSeek 带工具时回传 `reasoning_content`，Qwen 剥掉历史思考。多模型 harness 里这层最容易出 bug，最好为每家写一个回放测试。
5. **输出上限跟着思考走**：thinking 计入 `max_tokens` / `max_output_tokens`，上限不够会出现空响应但照样计费。按各家建议预留（OpenAI 至少 25k 起步，Claude 高档 64k 起，Qwen 32k）。
6. **用多次低档对冲一次高档**：任务能自动验证时（有测试），先试“两次低 effort 择优”，往往比一次高 effort 更划算。
7. **提示里别教模型怎么想**：推理模型不需要“think step by step”；要它少想，先降档，再考虑提示引导。
8. **自己测**：用 20 到 50 个真实任务，每档跑 3 到 5 次，记录成功率、思考 token、总耗时、费用和缓存命中率，按任务类型画成本-成功率曲线，选拐点。
