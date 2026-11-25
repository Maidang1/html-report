---
title: DeepSeek 学习地图：模型时间线、两条主线加系统底座、阅读顺序
kicker: 学习地图
deck: 从 2023 年的 DeepSeek LLM 到 2026 年的 V4.1-Flash：每代模型解决了什么问题，架构线和训练线怎样一路继承下来，底下那层硬件协同设计为什么要单列，今天的 API 该怎么调，以及按什么顺序读 12 篇论文。
meta:
  - 官方文档截至 2026-11-23
  - 时间线 + 论文 + API + 阅读顺序
  - 冲突与未核实处单独标出
footer: 来源：api-docs.deepseek.com（Change Log、Models & Pricing、Thinking Mode 指南、公告 news251201 / news260424 / news260813 / news260910 / news261017）；huggingface.co/deepseek-ai 各模型卡；arXiv 论文页（2401.06066、2402.03300、2405.04434、2412.19437、2501.12948、2502.11089、2505.09343、2512.02556、2512.24880、2601.07372、2606.19348、2609.19969 等）；Nature 645, 633–638。2026 年的模型信息以官方页面为准。
index: index.html
updated: 2026-11-23
created: 2026-11-23 17:35
---

DeepSeek 值得单独学，是因为它几乎每一代都把关键做法写成了论文公开。三年下来，这些论文基本在回答两个问题：怎样把每个 token 的训练和推理成本压下来（架构线），以及怎样用便宜的强化学习换来推理能力（训练线）。但只讲这两条线，会漏掉 DeepSeek 自己反复强调的一层：硬件协同设计和基础设施。所以这篇的框架是**两条主线加一层系统底座**。

这篇是本专题的起点，不深入任何一个机制，只回答四个问题：各代模型的先后关系、每条线上的机制怎样继承、今天的 API 和 2025 年相比变了什么、论文按什么顺序读。官方资料自相矛盾或本文没能核实的地方，都在原处标出来，不替读者抹平。

- **四个时代**：稠密与专项分支（2023–2024 初）→ V2 家族（MLA + MoE）→ V3 基座家族（R1、混合思考、DSA）→ V4 / V4.1（1M 上下文）
- **继承是主线**：MLA 的共享 KV、NSA 的“压缩 + 选择 + 滑窗”、DSA 的 indexer 都留在了 V4 的 CSA 里；R1 的蒸馏思路在 V4 变成在线蒸馏
- **API 只剩两个模型 ID**：`deepseek-flash`（V4.1-Flash-1017，支持图像）和 `deepseek-v4-pro`（V4-Pro-0813，只支持文本），thinking 默认开启，effort 默认 `high`
- **最短路径五篇**：DeepSeekMoE、V2 的 MLA、DeepSeekMath 的 GRPO、R1，再加 V4 的注意力与后训练章节；完整路径 12 篇、8 个阶段
- **四处冲突没有定论**：V4 的 arXiv 编号与日期、V4-Pro 9 月 14 日后的路由、V4.1-Flash 的 552B 与 763B、老模型名的更替链条

## 框架：两条主线，下面垫一层系统底座

**架构线**解决“同样的效果，怎样少花算力和显存”。驱动它的只有两个瓶颈：每个 token 的 FLOPs，以及长上下文下 KV cache 和注意力的开销。前者靠 FFN 侧的稀疏化（MoE），后者靠注意力侧的压缩和稀疏化（MLA → NSA → DSA → CSA/HCA → CSA2）。

**训练线**解决“怎样让模型会推理”。弧线更短：先把 RL 做便宜（GRPO），再证明光靠 RL 就能长出推理能力（R1-Zero），然后让它好用、能迁移到小模型（R1 多阶段训练加蒸馏），最后让它在一个模型里可控（V3.1 混合思考、V4 的 effort 档位）。

**系统底座**是两条线共用的地基：FP8 / FP4 训练、DualPipe 流水线、DeepEP / DeepGEMM / FlashMLA 这些算子库、3FS 文件系统，以及 DeepSeek 在体系结构会议 ISCA ’25 上发表的协同设计论文。不少做法本来就跨在线和底座之间，硬塞进某一条线都别扭。

```svg title="图 1：两条技术主线和它们共用的系统底座（深色框是 2026 年的当前状态）" tone="light"
<svg viewBox="0 0 760 356" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="两条技术主线和下面的系统底座">
<defs>
<marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
<path d="M0 0L10 5L0 10z" fill="#504e49"/>
</marker>
</defs>
<text x="16.0" y="26.0" font-size="13.5" fill="#141413" text-anchor="start" font-weight="700">架构线：每个 token 的 FLOPs 和 KV cache 往下压</text>
<rect x="16" y="38" width="108" height="64" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="70.0" y="66.0" font-size="12.5" fill="#141413" text-anchor="middle" font-weight="600">DeepSeekMoE</text>
<text x="70.0" y="83.0" font-size="11" fill="#504e49" text-anchor="middle">细粒度 + 共享专家</text>
<text x="70.0" y="118.0" font-size="10.5" fill="#504e49" text-anchor="middle">2024-01</text>
<line x1="125" y1="70" x2="138" y2="70" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="140" y="38" width="108" height="64" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="194.0" y="58.0" font-size="12.5" fill="#141413" text-anchor="middle" font-weight="600">MLA</text>
<text x="194.0" y="75.0" font-size="11" fill="#504e49" text-anchor="middle">KV 压成</text>
<text x="194.0" y="90.0" font-size="11" fill="#504e49" text-anchor="middle">低秩潜向量</text>
<text x="194.0" y="118.0" font-size="10.5" fill="#504e49" text-anchor="middle">V2 · 2024-05</text>
<line x1="249" y1="70" x2="262" y2="70" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="264" y="38" width="108" height="64" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="318.0" y="58.0" font-size="12.5" fill="#141413" text-anchor="middle" font-weight="600">V3</text>
<text x="318.0" y="75.0" font-size="11" fill="#504e49" text-anchor="middle">无辅助损失均衡</text>
<text x="318.0" y="90.0" font-size="11" fill="#504e49" text-anchor="middle">MTP</text>
<text x="318.0" y="118.0" font-size="10.5" fill="#504e49" text-anchor="middle">2024-12</text>
<line x1="373" y1="70" x2="386" y2="70" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="388" y="38" width="108" height="64" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="442.0" y="66.0" font-size="12.5" fill="#141413" text-anchor="middle" font-weight="600">NSA → DSA</text>
<text x="442.0" y="83.0" font-size="11" fill="#504e49" text-anchor="middle">可训练稀疏注意力</text>
<text x="442.0" y="118.0" font-size="10.5" fill="#504e49" text-anchor="middle">2025-02 / V3.2</text>
<line x1="497" y1="70" x2="510" y2="70" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="512" y="38" width="108" height="64" rx="8" fill="#1B365D" stroke="#1B365D" stroke-width="1.2"/>
<text x="566.0" y="66.0" font-size="12.5" fill="#ffffff" text-anchor="middle" font-weight="600">CSA + HCA</text>
<text x="566.0" y="83.0" font-size="11" fill="#dfe7f1" text-anchor="middle">压缩 + 稀疏混合</text>
<text x="566.0" y="118.0" font-size="10.5" fill="#504e49" text-anchor="middle">V4 · 2026-04</text>
<line x1="621" y1="70" x2="634" y2="70" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="636" y="38" width="108" height="64" rx="8" fill="#1B365D" stroke="#1B365D" stroke-width="1.2"/>
<text x="690.0" y="66.0" font-size="12.5" fill="#ffffff" text-anchor="middle" font-weight="600">CSA2 + 编解码</text>
<text x="690.0" y="83.0" font-size="11" fill="#dfe7f1" text-anchor="middle">KV 字节再降</text>
<text x="690.0" y="118.0" font-size="10.5" fill="#504e49" text-anchor="middle">V4.1-Flash · 2026-09</text>
<text x="16.0" y="150.0" font-size="13.5" fill="#141413" text-anchor="start" font-weight="700">训练线：用便宜的 RL 换推理，再让推理可控</text>
<rect x="16" y="160" width="108" height="64" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="70.0" y="180.0" font-size="12.5" fill="#141413" text-anchor="middle" font-weight="600">GRPO</text>
<text x="70.0" y="197.0" font-size="11" fill="#504e49" text-anchor="middle">组内比较</text>
<text x="70.0" y="212.0" font-size="11" fill="#504e49" text-anchor="middle">去掉 critic</text>
<text x="70.0" y="240.0" font-size="10.5" fill="#504e49" text-anchor="middle">Math · 2024-02</text>
<line x1="125" y1="192" x2="138" y2="192" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="140" y="160" width="108" height="64" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="194.0" y="188.0" font-size="12.5" fill="#141413" text-anchor="middle" font-weight="600">R1-Zero</text>
<text x="194.0" y="205.0" font-size="11" fill="#504e49" text-anchor="middle">只用规则奖励做 RL</text>
<text x="194.0" y="240.0" font-size="10.5" fill="#504e49" text-anchor="middle">2025-01</text>
<line x1="249" y1="192" x2="262" y2="192" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="264" y="160" width="108" height="64" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="318.0" y="180.0" font-size="12.5" fill="#141413" text-anchor="middle" font-weight="600">R1</text>
<text x="318.0" y="197.0" font-size="11" fill="#504e49" text-anchor="middle">冷启动 + 四阶段</text>
<text x="318.0" y="212.0" font-size="11" fill="#504e49" text-anchor="middle">+ 蒸馏</text>
<text x="318.0" y="240.0" font-size="10.5" fill="#504e49" text-anchor="middle">2025-01</text>
<line x1="373" y1="192" x2="386" y2="192" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="388" y="160" width="108" height="64" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="442.0" y="188.0" font-size="12.5" fill="#141413" text-anchor="middle" font-weight="600">V3.1</text>
<text x="442.0" y="205.0" font-size="11" fill="#504e49" text-anchor="middle">一个模型两种模式</text>
<text x="442.0" y="240.0" font-size="10.5" fill="#504e49" text-anchor="middle">2025-08</text>
<line x1="497" y1="192" x2="510" y2="192" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="512" y="160" width="108" height="64" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="566.0" y="180.0" font-size="12.5" fill="#141413" text-anchor="middle" font-weight="600">V3.2</text>
<text x="566.0" y="197.0" font-size="11" fill="#504e49" text-anchor="middle">agentic 任务合成</text>
<text x="566.0" y="212.0" font-size="11" fill="#504e49" text-anchor="middle">+ RL</text>
<text x="566.0" y="240.0" font-size="10.5" fill="#504e49" text-anchor="middle">2025-12</text>
<line x1="621" y1="192" x2="634" y2="192" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="636" y="160" width="108" height="64" rx="8" fill="#1B365D" stroke="#1B365D" stroke-width="1.2"/>
<text x="690.0" y="180.0" font-size="12.5" fill="#ffffff" text-anchor="middle" font-weight="600">V4</text>
<text x="690.0" y="197.0" font-size="11" fill="#dfe7f1" text-anchor="middle">领域专家</text>
<text x="690.0" y="212.0" font-size="11" fill="#dfe7f1" text-anchor="middle">+ 在线蒸馏</text>
<text x="690.0" y="240.0" font-size="10.5" fill="#504e49" text-anchor="middle">effort 低 / 高 / 最高</text>
<rect x="16" y="262" width="728" height="78" rx="8" fill="none" stroke="#1B365D" stroke-width="1.2" stroke-dasharray="5 4"/>
<text x="30.0" y="284.0" font-size="13" fill="#141413" text-anchor="start" font-weight="700">系统底座：硬件协同设计与基础设施（两条线共用）</text>
<text x="30.0" y="312.0" font-size="11.5" fill="#141413" text-anchor="start">FP8 训练 · FP4 专家权重</text>
<text x="196.0" y="312.0" font-size="11.5" fill="#141413" text-anchor="start">DualPipe · EPLB</text>
<text x="320.0" y="312.0" font-size="11.5" fill="#141413" text-anchor="start">DeepEP · DeepGEMM · FlashMLA</text>
<text x="530.0" y="312.0" font-size="11.5" fill="#141413" text-anchor="start">3FS · smallpond</text>
<text x="650.0" y="312.0" font-size="11.5" fill="#141413" text-anchor="start">ISCA ’25 论文</text>
<text x="30.0" y="330.0" font-size="11" fill="#504e49" text-anchor="start">很多做法跨在线与底座之间：MTP 既是训练信号又能做投机解码；V4 的在线蒸馏是训练手段，动机是只部署一个模型。</text>
</svg>
```

> “两条线”或“三层”都不是 DeepSeek 官方的说法，是为了好学而做的编辑选择。三条线并列更准确，但对只有 Transformer 基础的读者来说，系统底座最难，适合当成“进阶选读”放在下面，而不是和两条主线并排。

## 时间线：四个时代，规模从 7B 涨到 1.6T，上下文从 4K 涨到 1M

2023 年底到 2024 年初是稠密模型和各个专项分支：LLM、Coder、MoE、Math、VL。2024 年是 V2 家族，MLA 和 DeepSeekMoE 第一次一起用在大规模模型上。2024 年 12 月到 2025 年 12 月是 V3 基座家族，中间经过 R1、V3.1 混合思考，到 V3.2 引入 DSA 稀疏注意力。2026 年是 V4 家族，以及架构换代的 V4.1-Flash。

```svg title="图 2：主要模型的发布时间和四个时代" tone="light" wide
<svg viewBox="0 0 780 320" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="DeepSeek 主要模型时间线">
<rect x="40.0" y="262" width="111.5" height="50" rx="6" fill="#EEF2F7" stroke="none"/>
<text x="96.8" y="283.0" font-size="11.5" fill="#141413" text-anchor="middle" font-weight="600">稠密与专项分支</text>
<text x="96.8" y="301.0" font-size="10.5" fill="#504e49" text-anchor="middle">上下文 4K–16K</text>
<rect x="153.5" y="262" width="145.7" height="50" rx="6" fill="#E3E9F1" stroke="none"/>
<text x="227.4" y="283.0" font-size="11.5" fill="#141413" text-anchor="middle" font-weight="600">V2 家族：MLA + MoE</text>
<text x="227.4" y="301.0" font-size="10.5" fill="#504e49" text-anchor="middle">上下文 128K</text>
<rect x="301.2" y="262" width="228.7" height="50" rx="6" fill="#EEF2F7" stroke="none"/>
<text x="416.6" y="283.0" font-size="11.5" fill="#141413" text-anchor="middle" font-weight="600">V3 基座家族</text>
<text x="416.6" y="301.0" font-size="10.5" fill="#504e49" text-anchor="middle">上下文 128K</text>
<rect x="531.9" y="262" width="208.1" height="50" rx="6" fill="#E3E9F1" stroke="none"/>
<text x="637.0" y="283.0" font-size="11.5" fill="#141413" text-anchor="middle" font-weight="600">V4 / V4.1</text>
<text x="637.0" y="301.0" font-size="10.5" fill="#504e49" text-anchor="middle">上下文 1M</text>
<line x1="36" y1="170" x2="748" y2="170" stroke="#504e49" stroke-width="1.4"/>
<line x1="77.8" y1="165" x2="77.8" y2="175" stroke="#504e49" stroke-width="1.2"/>
<line x1="77.8" y1="175" x2="77.8" y2="258" stroke="#c9ced6" stroke-width="1" stroke-dasharray="2 3"/>
<text x="81.8" y="254.0" font-size="10.5" fill="#504e49" text-anchor="start">2024</text>
<line x1="304.9" y1="165" x2="304.9" y2="175" stroke="#504e49" stroke-width="1.2"/>
<line x1="304.9" y1="175" x2="304.9" y2="258" stroke="#c9ced6" stroke-width="1" stroke-dasharray="2 3"/>
<text x="308.9" y="254.0" font-size="10.5" fill="#504e49" text-anchor="start">2025</text>
<line x1="531.9" y1="165" x2="531.9" y2="175" stroke="#504e49" stroke-width="1.2"/>
<line x1="531.9" y1="175" x2="531.9" y2="258" stroke="#c9ced6" stroke-width="1" stroke-dasharray="2 3"/>
<text x="535.9" y="254.0" font-size="10.5" fill="#504e49" text-anchor="start">2026</text>
<line x1="40.6" y1="170" x2="40.6" y2="144" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="40.6" cy="170" r="3.5" fill="#1B365D"/>
<text x="43.6" y="140.0" font-size="11.5" fill="#141413" text-anchor="start">Coder</text>
<line x1="57.1" y1="170" x2="57.1" y2="192" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="57.1" cy="170" r="3.5" fill="#1B365D"/>
<text x="60.1" y="204.0" font-size="11.5" fill="#141413" text-anchor="start">LLM 7B/67B</text>
<line x1="83.9" y1="170" x2="83.9" y2="116" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="83.9" cy="170" r="3.5" fill="#1B365D"/>
<text x="86.9" y="112.0" font-size="11.5" fill="#141413" text-anchor="start">DeepSeekMoE</text>
<line x1="99.2" y1="170" x2="99.2" y2="220" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="99.2" cy="170" r="3.5" fill="#1B365D"/>
<text x="102.2" y="232.0" font-size="11.5" fill="#141413" text-anchor="start">Math（GRPO）</text>
<line x1="157.2" y1="170" x2="157.2" y2="144" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="157.2" cy="170" r="3.5" fill="#1B365D"/>
<text x="160.2" y="140.0" font-size="11.5" fill="#141413" text-anchor="start" font-weight="600">V2（MLA）236B/21B</text>
<line x1="182.2" y1="170" x2="182.2" y2="192" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="182.2" cy="170" r="3.5" fill="#1B365D"/>
<text x="185.2" y="204.0" font-size="11.5" fill="#141413" text-anchor="start">Coder-V2</text>
<line x1="231.6" y1="170" x2="231.6" y2="220" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="231.6" cy="170" r="3.5" fill="#1B365D"/>
<text x="234.6" y="232.0" font-size="11.5" fill="#141413" text-anchor="start">V2.5</text>
<line x1="301.2" y1="170" x2="301.2" y2="144" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="301.2" cy="170" r="3.5" fill="#1B365D"/>
<text x="304.2" y="140.0" font-size="11.5" fill="#141413" text-anchor="start" font-weight="600">V3 671B/37B</text>
<line x1="316.5" y1="170" x2="316.5" y2="192" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="316.5" cy="170" r="3.5" fill="#1B365D"/>
<text x="319.5" y="204.0" font-size="11.5" fill="#141413" text-anchor="start" font-weight="600">R1</text>
<line x1="333.0" y1="170" x2="333.0" y2="220" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="333.0" cy="170" r="3.5" fill="#1B365D"/>
<text x="336.0" y="232.0" font-size="11.5" fill="#141413" text-anchor="start">NSA</text>
<line x1="449.5" y1="170" x2="449.5" y2="144" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="449.5" cy="170" r="3.5" fill="#1B365D"/>
<text x="452.5" y="140.0" font-size="11.5" fill="#141413" text-anchor="start">V3.1 混合思考</text>
<line x1="473.3" y1="170" x2="473.3" y2="192" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="473.3" cy="170" r="3.5" fill="#1B365D"/>
<text x="476.3" y="204.0" font-size="11.5" fill="#141413" text-anchor="start">V3.2-Exp（DSA）</text>
<line x1="513.0" y1="170" x2="513.0" y2="116" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="513.0" cy="170" r="3.5" fill="#1B365D"/>
<text x="516.0" y="112.0" font-size="11.5" fill="#141413" text-anchor="start">V3.2</text>
<line x1="602.7" y1="170" x2="602.7" y2="88" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="602.7" cy="170" r="3.5" fill="#1B365D"/>
<text x="599.7" y="84.0" font-size="11.5" fill="#141413" text-anchor="end" font-weight="600">V4 预览 Pro 1.6T · Flash 284B</text>
<line x1="671.7" y1="170" x2="671.7" y2="192" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="671.7" cy="170" r="3.5" fill="#1B365D"/>
<text x="668.7" y="204.0" font-size="11.5" fill="#141413" text-anchor="end">V4-Pro 正式版</text>
<line x1="688.8" y1="170" x2="688.8" y2="116" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="688.8" cy="170" r="3.5" fill="#1B365D"/>
<text x="685.8" y="112.0" font-size="11.5" fill="#141413" text-anchor="end" font-weight="600">V4.1-Flash</text>
<line x1="712.0" y1="170" x2="712.0" y2="220" stroke="#9aa3ad" stroke-width="1"/>
<circle cx="712.0" cy="170" r="3.5" fill="#1B365D"/>
<text x="709.0" y="232.0" font-size="11.5" fill="#141413" text-anchor="end">-1017</text>
<text x="40.0" y="30.0" font-size="11" fill="#504e49" text-anchor="start">横轴按月线性；圆点是首次公开（论文、权重或 API）的日期，2023 年两条只到月份。</text>
</svg>
```

下表参数量写成“总参数 / 每 token 激活参数”，这是 MoE 模型的惯例。“出处”一列优先给一手来源；“核对”一列说明本文是怎样确认的：**官方**指 api-docs.deepseek.com 的 Change Log、公告或 Hugging Face 模型卡；**已抽查**指写作时重新打开了模型卡或 arXiv 页面核对；**未复核**指只来自研究笔记里的记忆。Hugging Face 上的“updated”日期是仓库最后更新的时间，不等于发布日期，表中只当旁证。

| 日期 | 模型 | 规模（总 / 激活） | 上下文 | 关键新意 | 出处 | 核对 |
| --- | --- | --- | --- | --- | --- | --- |
| 2023-11 | DeepSeek Coder | 1.3B / 5.7B / 6.7B / 33B 稠密，2T tokens | 16K | 项目级代码语料 + FIM | [arXiv 2401.14196](https://arxiv.org/abs/2401.14196)；GitHub README | 规模和上下文已抽查；月份未找到一手日期 |
| 2023-11 下旬 | DeepSeek LLM | 7B / 67B 稠密，2T tokens | 4K | 缩放律与超参选择 | [arXiv 2401.02954](https://arxiv.org/abs/2401.02954)；GitHub README | 规模和上下文已抽查；日期未复核 |
| 2024-01-11 | DeepSeekMoE 16B | 16.4B / 2.8B | 4K | 细粒度专家 + 共享专家 | [arXiv 2401.06066](https://arxiv.org/abs/2401.06066) | 已抽查（论文 §5.1） |
| 2024-02-05 | DeepSeekMath 7B | 7B | — | 提出 GRPO；MATH 51.7%（不用工具） | [arXiv 2402.03300](https://arxiv.org/abs/2402.03300) | 已抽查 |
| 2024-03 | DeepSeek-VL | 1.3B / 7B | — | 视觉语言 | [arXiv 2403.05525](https://arxiv.org/abs/2403.05525) | 未复核 |
| 2024-05 | DeepSeek-V2 | 236B / 21B；V2-Lite 15.7B / 2.4B | 128K（Lite 32K） | MLA + 规模化 DeepSeekMoE；API 05-17 上线 | [arXiv 2405.04434](https://arxiv.org/abs/2405.04434)；[HF 卡](https://huggingface.co/deepseek-ai/DeepSeek-V2)；Change Log | 已抽查 |
| 2024-06 | DeepSeek-Coder-V2 | 236B / 21B；Lite 16B / 2.4B | 128K | 338 种编程语言；API 06-14 上线 | [arXiv 2406.11931](https://arxiv.org/abs/2406.11931)；Change Log | 规模未复核，API 日期官方 |
| 2024-09-05 | DeepSeek-V2.5 | 236B | — | chat 与 coder 合并 | [Change Log](https://api-docs.deepseek.com/updates) | 官方 |
| 2024-12 | DeepSeek-VL2 | Tiny / Small / 标准版，激活 1.0B / 2.8B / 4.5B，最大 27B | — | MoE 视觉语言 | [arXiv 2412.10302](https://arxiv.org/abs/2412.10302)；[HF 卡](https://huggingface.co/deepseek-ai/deepseek-vl2) | 已抽查 |
| 2024-12-26 | DeepSeek-V3 | 671B / 37B（HF 标 684B，含 MTP 模块），14.8T tokens | 128K | 无辅助损失均衡、MTP、FP8 训练、DualPipe | [arXiv 2412.19437](https://arxiv.org/abs/2412.19437)；Change Log | 已抽查 |
| 2025-01-20 | DeepSeek-R1 / R1-Zero + 蒸馏系列 | 基于 V3-Base；蒸馏 1.5B–70B | — | 纯 RL 涌现长思维链；MIT 许可 | Change Log；[arXiv 2501.12948](https://arxiv.org/abs/2501.12948) | 官方 |
| 2025-03-24 | V3-0324 | 671B | — | GPQA 59.1→68.4，AIME 39.6→59.4 | Change Log | 官方 |
| 2025-05-28 | R1-0528 | — | — | AIME 2025 70.0→87.5；加入 JSON 与函数调用 | Change Log | 官方 |
| 2025-08-21 | DeepSeek-V3.1 | 685B | 128K | 一个模型同时提供 thinking 与 non-thinking | Change Log；[HF 卡](https://huggingface.co/deepseek-ai/DeepSeek-V3.1) | 官方 |
| 2025-09-22 | V3.1-Terminus | 685B | — | 减少中英混杂，Agent 能力增强 | Change Log | 官方 |
| 2025-09-29 | V3.2-Exp | 685B | 128K | 首次引入 DSA | Change Log | 官方 |
| 2025-12-01 | V3.2 / V3.2-Speciale | 685B | 128K | thinking 首次融入工具调用；Speciale 只在临时端点开放到 12-15 | [news251201](https://api-docs.deepseek.com/news/news251201)；[arXiv 2512.02556](https://arxiv.org/abs/2512.02556) | 官方 |
| 2026-04-24 | **V4 预览**（Pro / Flash） | **Pro 1.6T / 49B，Flash 284B / 13B**，预训练 >32T tokens | **1M** | CSA + HCA 混合注意力、mHC、Muon、FP4 专家；MIT | [news260424](https://api-docs.deepseek.com/news/news260424)；[HF V4-Pro](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) | 官方 |
| 2026-07-31 | V4-Flash-0731 | 同 V4-Flash，只重做后训练 | 1M | 公测 | Change Log | 官方 |
| 2026-08-13 | **V4-Pro 正式版 / V4-Pro-0813** | HF 标 1.7T | 1M | Agent 能力升级；effort 分 low / high / max；Responses API | [news260813](https://api-docs.deepseek.com/news/news260813) | 官方 |
| 2026-08-21 | V4-Flash-Vision-Exp | HF 标 305B | — | 实验性多模态 | Change Log | 官方 |
| 2026-09-10 | **V4.1-Flash** | **552B 主干（HF 计 763B）**，prefill 激活 8B，decode 激活 16B | **1M** | 因果编码器–解码器、CSA2、FP4 KV、Engram、原生视觉 | [news260910](https://api-docs.deepseek.com/news/news260910)；[HF 卡](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) | 官方 |
| 2026-10-17 | V4.1-Flash-1017 | 同上 | 1M | 分数更高、输出更短、成本更低 | [news261017](https://api-docs.deepseek.com/news/news261017) | 官方 |

许可证分两段：2023–2024 年的 LLM、Coder、MoE、V2、VL2 都是“代码 MIT + DeepSeek 模型许可证（允许商用）”，模型卡上写得很清楚；R1 起转为 MIT，V3-0324 的权重也改成 MIT（后者是笔记的记忆，未复核），V3.1、V3.2、V4、V4.1-Flash 的模型卡都写明 MIT。

表外还有几条支线：Janus / Janus-Pro 做统一的多模态理解与生成（Janus-Pro 约在 2025-01-27 发布，未复核）；Prover-V1.5（[arXiv 2408.08152](https://arxiv.org/abs/2408.08152)）和 2025-04-30 的 Prover-V2（671B / 7B）做 Lean 4 定理证明；DeepSeek-OCR（3B，[arXiv 2510.18234](https://arxiv.org/abs/2510.18234)）把上下文渲染成图像来压缩；Math-V2（685B，HF 日期 2025-11-27）做可自我验证的证明。它们都不在主线的依赖链上。

### 官方资料有四处对不上

**V4 的论文编号与日期。** V4-Pro 模型卡链接的是 arXiv 2606.19348（标题“DeepSeek-V4: Towards Highly Efficient Million-Token Context Intelligence”）。写作时打开 abs 页，看到的确实是“Submitted on 26 Apr 2026”；可按 arXiv 的编号规则，2606 开头通常对应 2026 年 6 月。这个矛盾本文解决不了。引用时写“技术报告，标注日期 2026-04-26，arXiv 2606.19348（编号与日期不一致）”，并优先给 Hugging Face 上的 [DeepSeek_V4.pdf](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf)。发布日期以官方公告的 2026-04-24 为准。

**V4.1-Flash 的参数量。** 官方公告和模型卡写的是“552B backbone”，Hub 上显示总量 763B。两者差 211B，模型卡里的 Engram 条件记忆是 196B，加上视觉塔大致能补上差额，但这只是推算，官方没有给出拆分。本文写作“552B 主干（HF 计 763B）”。V4 系列也有同类出入：公告写 Flash 284B，HF 上 V4-Flash-Base 标 292B、指令版标 291B，V4-Pro-0813 标 1.7T。这多半是 HF 把 MTP 等附加模块也计入了，和 V3 的 671B 对 684B 是同一种情况。

**“V4 正式版”和“V4.1”的含义。** 2026-04-24 开源的权重自己标注为 preview。“正式发布”其实是 API 侧的里程碑：7 月 31 日 Flash-0731 公测，8 月 13 日 Pro 正式 GA，开源仓库只多了 -0731 / -0813 两个。“V4.1”目前只有 Flash 一个型号。V4.1-Flash 公告提到“直到 V4.1-Pro 上线”，但截至本文没有找到 V4.1-Pro 的发布。V4.1-Flash 的技术报告有两个入口：HF 上的 [DeepSeek_V41_Tech_Report.pdf](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/DeepSeek_V41_Tech_Report.pdf)，以及 arXiv 2609.19969（v1 时间戳 2026-09-17，晚于发布日）。

**V4-Pro 的去留和模型名的更替。** 这两条在下文“今天的 API”一节详细说明，配图 3。

## 架构线：每一步都继承了上一步的一个部件

### FFN 这条链：先把专家切细，再加一条“条件记忆”轴

传统 top-k MoE 有两个毛病：一个专家要学杂糅的知识，多个专家又重复学同样的通用知识。DeepSeekMoE 把每个专家切成更小的专家、激活更多个，同时隔离出始终激活的**共享专家**吸收通用知识，让路由专家专心专精。16B 版本只用 LLaMA2 7B 约 40% 的计算量就达到相当的效果。

到 V3，负载均衡改成**无辅助损失**的方式：给每个专家的亲和度分数加一个偏置 b_i，它只参与 top-k 选择、不进门控权重，每一步按专家的负载上调或下调。V4 把路由专家的权重降到 FP4。

2026 年 1 月的 Engram 论文（[arXiv 2601.07372](https://arxiv.org/abs/2601.07372)，作者单位为北京大学与 DeepSeek-AI）开辟了第二条稀疏轴：MoE 是条件计算，Engram 是**条件记忆**，用一张现代化的 N-gram 嵌入表做 O(1) 的确定性查找。寻址是确定的，所以表可以放在主机内存里预取；论文实测把 100B 参数的表卸载到主机内存，吞吐损失最高 2.8%。论文还发现 MoE 与 Engram 的参数分配存在 U 形规律，最优约是 75–80% 给 MoE。Engram 已确认用在 V4.1-Flash 上（196B）。**V4 有没有用 Engram 没有定论**：V4 的摘要没提，只有二手网站这样说，本文当作未核实。

### 注意力这条链：MLA → NSA → DSA → CSA/HCA → CSA2

V2 的 **MLA** 把 K 和 V 联合压缩成一个低秩潜向量，推理时只缓存它，上投影矩阵可以吸收进 Q 和 O 的投影。RoPE 和这种矩阵吸收不可交换，所以另带一个小的“解耦 RoPE”键。V2 模型卡给出的数字是：与 DeepSeek 67B 相比，KV cache 减少 93.3%，训练成本降低 42.5%，最大生成吞吐提升到 5.76 倍。

**NSA**（2025 年 2 月）让每个 query 走三条带门控的分支：压缩的粗粒度块摘要、按分数选出的 top-n 细粒度块、局部滑动窗口。块选择和 GPU 的访存方式、GQA 分组对齐，所以能拿到真实的墙钟加速，并且从预训练开始就是稀疏的。这篇论文获得了 ACL 2025 最佳论文。

V3.2 的 **DSA** 换了一种做法：一个轻量的 lightning indexer（头数少、用 ReLU，论文说可以用 FP8 实现）给前面每个 token 打分，每个 query 只对 top-k 个 token 做注意力，k = 2048。DSA 不是原生训练出来的，而是在 V3.1-Terminus 上继续训练改装进去的：先冻结其余参数、只训 indexer 做 1000 步稠密预热（约 2.1B tokens），再在 128K 长度上做稀疏训练。它是 V3.2 相对 V3.1-Terminus 唯一的架构改动。

V4 把这些部件拼成**混合注意力**。CSA 把每 m 个 token 的 KV 压成一条（由 2m 条有重叠的条目构造），用 FP4 的 lightning indexer 选出 top-k，再加上共享 KV 的 MQA 和未压缩的滑动窗口；HCA 用大得多、不重叠的压缩比，对全部压缩条目做稠密注意力。按 V4 报告，在 1M token 下与 V3.2 相比，V4-Pro 的单 token FLOPs 约为 27%、KV cache 约为 10%；V4-Flash 分别约为 10% 和 7%。研究笔记没有拿到 m、k 和窗口大小的具体值，也没有拿到层的交错方式。

下表把这条链上每一代保留了什么列出来。读懂 MLA 和 NSA 的人，读 V4 的注意力章节会很快；反过来跳过前面直接读 V4，会被成串的缩写卡住。

| 部件 | MLA（V2） | NSA | DSA（V3.2） | CSA / HCA（V4） | CSA2（V4.1-Flash） |
| --- | --- | --- | --- | --- | --- |
| 压缩 KV | 低秩潜向量 | 块摘要分支 | — | CSA 每 m 个 token 压一条；HCA 更大压缩比 | 沿用，主 KV cache 降到 FP4 |
| 共享 KV | 多头共享一个潜向量 | — | — | CSA 里的共享 KV MQA | 跨层共享全局 KV |
| 按分数选 top-k | — | 选 top-n 个块 | lightning indexer 选 2048 个 token | FP4 indexer | 跨层共享 indexer 键，每层可选 Full / Reindex / Reuse |
| 滑动窗口 | — | 局部窗口分支 | — | 未压缩滑窗 | “SWA Bounded Replay”按需重建，不持久化 |
| 怎么得到 | 从头训练 | 从预训练开始稀疏 | 在 V3.1-Terminus 上改装 | 从头训练 | 新架构 |

### V4.1-Flash：设计重心从 FLOPs 移到 KV 的字节数

V4.1-Flash 不是 V4 的小修小补。它借鉴 YOCO，采用**因果编码器–解码器**：40 层 = 20 层因果编码器 + 20 层解码器，解码器的全局 KV 从编码器最后一层的隐状态投影出来，于是 prefill 激活约 8B、decode 激活 16B。每个 MoE 层 1 个共享专家 + 384 个路由专家，激活 6 个。主 KV cache 用 FP4，每 token 的全局 KV 约 890 字节；公告说 HBM 占用约为上一代的 1/4、落到 SSD 的部分约为 1/8。它的技术报告标题就是“Pushing the Limits of KV Cache Compression”。

“8B 对 16B 的不对称是因为编码器负责 prefill、解码器负责生成”是研究笔记的解读，V4.1 报告原文的措辞还需要对照。模型卡还把推理强度改成了 1–100 的连续值，这和 API 上的 low / high / max 三档不是一回事。

### 残差流：mHC 让加宽的残差流重新稳定

Hyper-Connections 把残差流拓宽成 n 条并行流，但丢了恒等映射，规模一大训练就不稳。**mHC**（[arXiv 2512.24880](https://arxiv.org/abs/2512.24880)，2025-12-31）用 Sinkhorn-Knopp 把混合矩阵投影到双随机矩阵流形上，恢复类似恒等的信号传播。V4 用了 4 条残差流，同时引入 Muon 优化器，MTP 沿用 V3 的配置。

## 训练线：先把 RL 做便宜，再让推理变得可控

**GRPO**（DeepSeekMath）去掉了 PPO 的价值网络：对每个 prompt 采样 G 个输出，用组内归一化的奖励 (r_i − mean) / std 作为优势，KL 惩罚直接写进损失函数而不是加在奖励上，省掉一个和策略模型一样大的 critic。这篇论文的数据部分同样值得看：120B token 的数学语料从 Common Crawl 迭代挖出来，7B 模型不用工具在 MATH 上达到 51.7%，64 次自洽投票到 60.9%。

**R1-Zero** 在 V3-Base 上直接跑 GRPO，不做 SFT，奖励只有规则：答案准确性加格式。它刻意不用神经奖励模型，以避开 reward hacking。AIME 2024 的 pass@1 从 15.6% 升到 71.0%，回答长度自发变长，还出现了自我反思的“aha moment”。

**R1** 用四个阶段修好了 R1-Zero 可读性差、中英混杂的问题：冷启动 SFT；面向推理的 RL（加语言一致性奖励）；拒绝采样构造约 80 万条 SFT 数据（约 60 万推理 + 20 万非推理）；最后做全场景 RL。用这 80 万条数据去 SFT Qwen2.5 和 Llama 3 的小模型，效果好过直接在小模型上跑 RL。论文的“失败尝试”一节（PRM、MCTS）尤其值得读。R1 经同行评审后发表在 Nature 645, 633–638（2025 年 9 月），想读最完整的版本应读 Nature 版及补充材料；arXiv 版也已更新到 v2（2026-01-04）。媒体报道的“R1 训练成本约 29.4 万美元”本文没有核对补充材料，不采用。

**V3.1** 用 chat template 切换两种模式：thinking 以 `<think>` 开始，non-thinking 用 `</think>` 标记。V3.1-Base 还做了两阶段长上下文扩展：32K 阶段 630B tokens，128K 阶段 209B tokens。V3.1 没有独立论文，HF 模型卡就是一手来源。**V3.2** 在 DSA 之外贡献了可扩展的 RL 框架、大规模 agentic 任务合成管线（官方说法是 1,800+ 合成环境、85k+ 指令），以及在 IMO / IOI 2025 达到金牌水平的 Speciale。

**V4** 的后训练分两步：先按领域（数学、代码、Agent、指令遵循）各训一个 SFT + GRPO 专家，再用在线蒸馏（最小化对各教师的 reverse KL）合并成一个学生模型，对外提供 Non-think、Think High、Think Max 几档。R1 的“蒸馏胜过小模型 RL”和 V4 的在线蒸馏是同一个思想在两个尺度上的应用，值得放在一起读。**各个 effort 档位具体怎么训练（预算控制、长度惩罚），研究笔记没能取到，仍是未知。**

## 系统底座：为什么要单列一层

DeepSeek 在 ISCA ’25 的工业论文《Insights into DeepSeek-V3: Scaling Challenges and Reflections on Hardware for AI Architectures》（[arXiv 2505.09343](https://arxiv.org/abs/2505.09343)）里，核心论点就是 V3 的硬件感知协同设计：用 MLA 省显存，用 MoE 在计算和通信之间取舍，用 FP8，再加多平面网络拓扑，并向硬件厂商提出诉求。

2025 年 2 月的 Open Infra Week 连续开源了一组组件，自称在 V3 / R1 生产环境中验证过：FlashMLA（Hopper 上的 MLA 解码核）、DeepEP（专家并行 all-to-all 通信）、DeepGEMM（FP8 GEMM）、DualPipe 与 EPLB、3FS 文件系统和 smallpond，索引在 [open-infra-index](https://github.com/deepseek-ai/open-infra-index)。NSA 的标题里就写着“Hardware-Aligned”；V4 报告有一整节基础设施内容，包括适配 mHC 的 DualPipe 和 wave 式专家并行重叠；V4.1-Flash 的报告标题直接是 KV 字节数。

更要紧的是，很多做法本来就跨在两条线中间：FP8 训练、DualPipe、MTP（既增加训练信号，又能用于投机解码），以及 V4 的在线蒸馏（属于训练，动机是只部署一个模型）。放进底座、再标出它们和哪条线相连，比硬塞进某一条线更准确。V4.1-Flash 之后，设计重心已经从 FLOPs 移到 KV 的字节数和存储层级，下一阶段大概率会被内存层级主导，这也是单列这一层的理由。

## 今天的 API：两个模型 ID，thinking 由参数控制

截至 2026-11-23，[Models & Pricing 页](https://api-docs.deepseek.com/quick_start/pricing)只列出两个模型。价格按每百万 token 计，单位美元，写成“闲时 / 峰时”。峰时是周一至周五 UTC 01:00–04:00 和 06:00–10:00（中国法定节假日除外），闲时价为峰时的一半，这套峰谷计价从 2026-08-16 开始。

| | `deepseek-flash` | `deepseek-v4-pro` |
| --- | --- | --- |
| 实际模型 | DeepSeek-V4.1-Flash-1017 | DeepSeek-V4-Pro-0813 |
| 上下文 / 最长输出 | 1M / 384K | 1M / 384K |
| 图像输入 | 支持 | 不支持 |
| 并发上限 | 2500 | 500 |
| 缓存命中输入 | 0.003 / 0.006 | 0.022 / 0.044 |
| 缓存未命中输入 | 0.15 / 0.30 | 0.66 / 1.32 |
| 输出 | 0.60 / 1.20 | 1.98 / 3.96 |

两个模型都支持 JSON 输出、工具调用、Responses API、Anthropic 格式 API 和对话前缀续写（beta）；FIM（beta）只能在 non-thinking 模式下用。端点有两个：OpenAI 格式的 `https://api.deepseek.com` 和 Anthropic 格式的 `https://api.deepseek.com/anthropic`。2026-11-13 起，`deepseek-flash` 新增服务端工具 `deepseek_web_search`（thinking 模式下是“深度思考搜索”）和 `deepseek_read_url`、文件输入、安全检查参数，另有独立的 Search & Fetch、OCR、ASR、TTS API；11-18 起服务端搜索 / 抓取的结果按普通 token 计费。

### thinking 已经从“选哪个模型”变成“传什么参数”

两个模型默认都开启 thinking，effort 默认 `high`。OpenAI 格式下用 `thinking: {type: enabled|disabled}` 开关（OpenAI SDK 里要放进 `extra_body`），用 `reasoning_effort` 选档，只接受 `low` / `high` / `max`；别家的档位名会被映射：`minimal` / `low` → `low`，`medium` / `high` / `xhigh` → `high`，`max` / `ultra` → `max`。思考原文在返回消息的 `reasoning_content` 字段里，和 `content` 平级。low / high / max 三档是 2026-08-13 随 V4-Pro 正式版推出的。

```python title="示意：按 api-docs.deepseek.com/guides/thinking_mode 的说明改写"
from openai import OpenAI

client = OpenAI(api_key="...", base_url="https://api.deepseek.com")

resp = client.chat.completions.create(
    model="deepseek-flash",
    messages=[{"role": "user", "content": "9.11 和 9.9 哪个大？"}],
    reasoning_effort="high",                       # low / high / max
    extra_body={"thinking": {"type": "enabled"}},  # disabled 关闭思考
)

msg = resp.choices[0].message
print(msg.reasoning_content)  # 思考原文
print(msg.content)            # 最终回答
print(resp.model)             # 记录实际模型版本，别名会随代际浮动
```

- 思考模式下 `temperature`、`presence_penalty`、`frequency_penalty` 被静默忽略；`top_p` 在 flash 上只在 0.95–1.0 之间生效，在 v4-pro 上被忽略
- **带工具的多轮对话里，必须把之前所有轮次的 `reasoning_content` 原样传回，否则返回 400**；不带工具时可以不传
- Anthropic 格式用 `output_config.effort`，`none` 表示关闭 thinking；研究笔记抓到的映射表有乱码，具体参数名以线上指南为准
- 从 `deepseek-chat` 迁移来的代码如果不显式关闭 thinking，成本和延迟都会变

### 模型名的更替链条，以及 V4-Pro 的去留

常见的说法“`deepseek-chat` / `deepseek-reasoner` 被 `deepseek-flash` 取代”不准确，中间还隔了一代。2026-04-24 的公告说两个老名字在 2026-07-24 15:59（UTC）之后彻底停用，在此之前分别转发到 `deepseek-v4-flash` 的 non-thinking 和 thinking 模式。所以停用那天的接替者是 `deepseek-v4-flash`，`deepseek-flash` 要到 9 月 10 日才出现。停用是否按期执行，官方没有事后确认，Change Log 里也没有对应条目，现在的文档只是不再提这两个名字；按期执行的说法来自二手报道。

2026-09-10，`deepseek-v4-flash` 和 `deepseek-v4-flash-vision-exp` 两个模型下线，但名字还能用，会转到 V4.1-Flash-1017、按 Flash 价计费。`deepseek-coder` 早在 2024-09-05 就并入 V2.5，没有找到明确的停用日期。另外，Change Log 2025-01-20 的条目里有一句“V3 可用 `model='deepseek-reasoner'` 调用”，与同条目“R1 即 reasoner”矛盾，多半是文档笔误。

```svg title="图 3：API 模型名的更替（2026 年横轴放大；虚线表示名字仍可用或去向不明）" tone="light" wide
<svg viewBox="0 0 780 370" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="DeepSeek API 模型名的更替">
<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#504e49"/></marker></defs>
<text x="140.0" y="74.0" font-size="11.5" fill="#141413" text-anchor="end" font-family="SFMono-Regular, Menlo, Consolas, monospace">deepseek-chat</text>
<text x="140.0" y="120.0" font-size="11.5" fill="#141413" text-anchor="end" font-family="SFMono-Regular, Menlo, Consolas, monospace">deepseek-reasoner</text>
<text x="140.0" y="166.0" font-size="11.5" fill="#141413" text-anchor="end" font-family="SFMono-Regular, Menlo, Consolas, monospace">deepseek-coder</text>
<text x="140.0" y="212.0" font-size="11.5" fill="#141413" text-anchor="end" font-family="SFMono-Regular, Menlo, Consolas, monospace">deepseek-v4-flash</text>
<text x="140.0" y="258.0" font-size="11.5" fill="#141413" text-anchor="end" font-family="SFMono-Regular, Menlo, Consolas, monospace">deepseek-v4-pro</text>
<text x="140.0" y="304.0" font-size="11.5" fill="#141413" text-anchor="end" font-family="SFMono-Regular, Menlo, Consolas, monospace">deepseek-flash</text>
<line x1="529.8" y1="44" x2="529.8" y2="330" stroke="#9aa3ad" stroke-width="1" stroke-dasharray="2 3"/>
<text x="525.8" y="40.0" font-size="10.5" fill="#504e49" text-anchor="end">04-24 V4 上线</text>
<line x1="609.8" y1="44" x2="609.8" y2="330" stroke="#9aa3ad" stroke-width="1" stroke-dasharray="2 3"/>
<text x="605.8" y="40.0" font-size="10.5" fill="#504e49" text-anchor="end">07-24</text>
<line x1="651.1" y1="44" x2="651.1" y2="330" stroke="#9aa3ad" stroke-width="1" stroke-dasharray="2 3"/>
<text x="655.1" y="40.0" font-size="10.5" fill="#504e49" text-anchor="start">09-10</text>
<line x1="715.6" y1="44" x2="715.6" y2="330" stroke="#9aa3ad" stroke-width="1" stroke-dasharray="2 3"/>
<text x="719.6" y="40.0" font-size="10.5" fill="#504e49" text-anchor="start">11-23</text>
<rect x="150.0" y="61" width="459.8" height="18" rx="4" fill="#EEF2F7" stroke="#1B365D" stroke-width="1"/><text x="156.0" y="74.0" font-size="10.5" fill="#141413" text-anchor="start">67B → V2 → V2.5 → V3 → V3-0324 → V3.1 / V3.2 非思考 → 转发到 v4-flash 非思考</text>
<rect x="297.2" y="107" width="312.7" height="18" rx="4" fill="#EEF2F7" stroke="#1B365D" stroke-width="1"/><text x="303.2" y="120.0" font-size="10.5" fill="#141413" text-anchor="start">R1 → R1-0528 → V3.1 / V3.2 思考 → v4-flash 思考</text>
<text x="605.8" y="138.0" font-size="10.5" fill="#504e49" text-anchor="end">按 04-24 公告，07-24 15:59 UTC 后两个老名字停用（执行情况只有二手报道）</text>
<rect x="150.0" y="153" width="94.9" height="18" rx="4" fill="#EEF2F7" stroke="#1B365D" stroke-width="1"/><text x="156.0" y="166.0" font-size="10.5" fill="#141413" text-anchor="start">Coder-V2</text>
<line x1="244.9" y1="162" x2="313.3" y2="162" stroke="#504e49" stroke-width="1.2" stroke-dasharray="4 3"/>
<text x="319.3" y="166.0" font-size="10.5" fill="#504e49" text-anchor="start">2024-09-05 并入 V2.5，未找到明确停用日期</text>
<rect x="529.8" y="199" width="121.3" height="18" rx="4" fill="#EEF2F7" stroke="#1B365D" stroke-width="1"/><text x="535.8" y="212.0" font-size="10.5" fill="#141413" text-anchor="start">V4-Flash → -0731</text>
<line x1="651.1" y1="208" x2="715.6" y2="208" stroke="#504e49" stroke-width="1.2" stroke-dasharray="4 3"/>
<text x="653.1" y="230.0" font-size="10.5" fill="#504e49" text-anchor="end">09-10 模型下线；名字仍可用，转到 V4.1-Flash-1017</text>
<rect x="529.8" y="245" width="124.7" height="18" rx="4" fill="#1B365D" stroke="#1B365D" stroke-width="1"/><text x="535.8" y="258.0" font-size="10.5" fill="#ffffff" text-anchor="start">V4-Pro → -0813</text>
<rect x="654.5" y="245" width="61.1" height="18" rx="4" fill="#ffffff" stroke="#1B365D" stroke-width="1" stroke-dasharray="3 2"/>
<text x="715.6" y="276.0" font-size="10.5" fill="#504e49" text-anchor="end">09-14 起是否转到 V4.1-Flash：公告说转，Change Log 说继续</text>
<rect x="651.1" y="291" width="64.5" height="18" rx="4" fill="#1B365D" stroke="#1B365D" stroke-width="1"/><text x="657.1" y="304.0" font-size="10.5" fill="#ffffff" text-anchor="start">V4.1</text>
<text x="715.6" y="322.0" font-size="10.5" fill="#504e49" text-anchor="end">跟随代际浮动的别名；10-17 起指向 V4.1-Flash-1017</text>
<line x1="150" y1="344" x2="750" y2="344" stroke="#504e49" stroke-width="1.2"/>
<line x1="150" y1="340" x2="150" y2="348" stroke="#504e49" stroke-width="1.2"/>
<text x="154.0" y="360.0" font-size="10.5" fill="#504e49" text-anchor="start">2024</text>
<line x1="290" y1="340" x2="290" y2="348" stroke="#504e49" stroke-width="1.2"/>
<text x="294.0" y="360.0" font-size="10.5" fill="#504e49" text-anchor="start">2025</text>
<line x1="430" y1="340" x2="430" y2="348" stroke="#504e49" stroke-width="1.2"/>
<text x="434.0" y="360.0" font-size="10.5" fill="#504e49" text-anchor="start">2026（放大）</text>
<line x1="750" y1="340" x2="750" y2="348" stroke="#504e49" stroke-width="1.2"/>
</svg>
```

**V4-Pro 的去留是最明显的官方自相矛盾。** V4.1-Flash 公告（news260910）写着 `deepseek-v4-pro` 将“自 2026-09-14 04:00 UTC 起转到 V4.1-Flash，直到 V4.1-Pro 上线”，并说 V4-Pro“正在逐步退役”；同一天的 Change Log 却写着 V4 Pro API“在 2026-09-14 之后继续提供，计费不变”，当前价格页也仍然列着 `deepseek-v4-pro` = V4-Pro-0813。最可能的解释是公告发出后方针改了，本文按 Change Log 和价格页判断“V4-Pro 仍在服务”，但新闻页上那段话至今没有撤下。

> 需要可复现结果的场景，应当记录响应里返回的实际模型版本，不能只记别名：`deepseek-flash` 会在不另行通知的情况下换代，`deepseek-v4-pro` 的去向官方说法也不一致。thinking 参数在各家之间的对比，见根目录的《各家模型的 thinking 模式》。

## 阅读顺序：8 个阶段 12 篇，或 5 篇快速通道

排序原则：每篇 DeepSeek 论文配一篇外部先修；只在依赖关系要求时才在两条线之间切换；引入新机制的章节精读，堆基准分数的部分和支线略读。DeepSeek 官方没有推荐过阅读清单，下面的顺序是按前文的依赖结构编排的。

| 阶段 | 论文 | 精读 | 略读 | 配读先修 |
| --- | --- | --- | --- | --- |
| 0 | 先修，边读边补 | — | — | Transformer 1706.03762、RoPE 2104.09864、MQA 1911.02150、GQA 2305.13245、Switch 2101.03961、PPO 1707.06347、InstructGPT 2203.02155 |
| 1 | [DeepSeekMoE 2401.06066](https://arxiv.org/abs/2401.06066)；可选 [DeepSeek LLM 2401.02954](https://arxiv.org/abs/2401.02954) | §3 细粒度 + 共享专家，MoE 输出公式 | 实验部分；LLM 只看缩放律 | Switch / GShard |
| 2 | [DeepSeekMath 2402.03300](https://arxiv.org/abs/2402.03300) | GRPO 目标函数，PPO 与 GRPO 对比图，统一范式一节 | 数据管线 | PPO |
| 3 | [DeepSeek-V2 2405.04434](https://arxiv.org/abs/2405.04434) | MLA 一节和解耦 RoPE 附录 | 对齐与评测 | MQA / GQA、RoPE |
| 4 | [DeepSeek-V3 2412.19437](https://arxiv.org/abs/2412.19437) | §2 无辅助损失均衡、MTP；§3 DualPipe、FP8 | 评测；进阶可配 [ISCA 2505.09343](https://arxiv.org/abs/2505.09343) 和 FlashMLA / DeepEP / DeepGEMM 的 README | 流水线并行、FP8 格式 |
| 5 | [DeepSeek-R1 2501.12948](https://arxiv.org/abs/2501.12948) 或 [Nature 版](https://www.nature.com/articles/s41586-025-09422-z) | R1-Zero 曲线、四阶段管线、蒸馏表、失败尝试 | 基准表 | 先重读阶段 2 的 GRPO 公式 |
| 6 | [NSA 2502.11089](https://arxiv.org/abs/2502.11089)，然后 [V3.2 2512.02556](https://arxiv.org/abs/2512.02556) + [V3.1 HF 卡](https://huggingface.co/deepseek-ai/DeepSeek-V3.1) | NSA 三分支与核设计图；DSA 的 indexer + top-k 与预热 | V3.2 的 RL 扩展与 agentic 合成；V3.1 卡约 5 分钟 | FlashAttention 2205.14135 |
| 7 | [mHC 2512.24880](https://arxiv.org/abs/2512.24880)，然后 [Engram 2601.07372](https://arxiv.org/abs/2601.07372) | 恒等映射论证与 Sinkhorn 投影；条件记忆与 U 形分配律 | 基础设施优化部分 | Hyper-Connections 2409.19606 |
| 8 | [V4 2606.19348](https://arxiv.org/abs/2606.19348)，然后 [V4.1-Flash 2609.19969](https://arxiv.org/html/2609.19969v1) | V4 §2.3 CSA / HCA 与后训练；V4.1 编码器–解码器与 CSA2 | 基础设施、基准；V4.1 的 FP4 KV 和 Bounded Replay（做推理服务的人再精读） | StreamingLLM 2309.17453、Muon / Moonlight 2502.16982、YOCO 2405.05254 |

**快速通道（约 5 篇）**：DeepSeekMoE §3 → V2 的 MLA 一节 → DeepSeekMath 的 GRPO 一节 → R1 → V4 §2.3 加后训练。V3 只看 Fig. 2 和无辅助损失均衡、MTP 两节就够。这条路线覆盖了两条线的骨架，只牺牲了稀疏注意力的演化细节。

阶段 1–5 是 2024 年到 2025 年初的内容，二手讲解已经很多；阶段 6–8 是 2025 下半年到 2026 年的材料，好的二手讲解还很少，得更多地直接读 PDF。读到这一段时，最好对照上面那张注意力部件表自己画一遍 NSA、DSA、CSA/HCA、CSA2 的结构，否则“压缩 + 选择 + 滑窗”的继承关系很容易看丢。支线按兴趣挑：DeepSeek-OCR 是另一种长上下文压缩思路，可以配它的反驳论文（[arXiv 2512.03643](https://arxiv.org/abs/2512.03643)）一起读；Prover 系列在 Lean 里把可验证奖励推到了极致，和 R1 呼应；Janus / VL 要到 V4.1-Flash 走向多模态时才变得相关。

> 外部先修论文的 arXiv 编号写作时按记忆逐个核对过，没有逐一打开页面；DeepSeek 自家论文的编号都打开 abs 页确认过，V4 那条的矛盾见上文。

## 在本文件夹里继续：按阅读阶段写的计划文章

按“先能用、再懂原理、最后能复现”的顺序，每个主题在 `deepseek/` 里写一篇。写完一篇就把“状态”改成链接，并在 `README.md` 里补一行。

| 对应阅读阶段 | 主题 | 计划文章 | 动手练习 | 状态 |
| --- | --- | --- | --- | --- |
| — | API、thinking、工具调用、前缀缓存 | DeepSeek API 在 agent 里的用法 | 用 `deepseek-flash` 接一个带工具的小 agent，测缓存命中和首 token 延迟 | 未写 |
| 1、4 | MoE 与负载均衡 | DeepSeekMoE 到 V3：专家怎么分工 | 写一个 top-k 路由的玩具 MoE 层，观察负载不均，再加偏置项 | 未写 |
| 3 | MLA | MLA：KV 缓存为什么能压小 | 算一笔账：同样上下文下 MHA、GQA、MLA 的 KV 缓存大小 | 未写 |
| 2、5 | GRPO 与 R1 | GRPO 与 R1：只靠规则奖励训推理 | 用开源 RL 框架在小模型上跑一次 GRPO（数学题、规则判分） | 未写 |
| 4 | MTP、FP8、训练工程 | 系统底座：MTP、FP8、DualPipe 与 Open Infra | 读 FlashMLA、DeepEP、DeepGEMM、DualPipe 的仓库 | 未写 |
| 6 | 稀疏注意力 | NSA、DSA 到 CSA：怎样撑起 1M 上下文 | 对比全注意力和 top-k 稀疏注意力的计算量曲线 | 未写 |
| 7、8 | V4 / V4.1 技术报告 | V4 与 V4.1：混合注意力、mHC、Engram 和编码器–解码器 | 读完报告后回到 MLA 和稀疏注意力两篇补充对照 | 未写 |

> API 那一行可以先复用根目录已有的两篇：《各家模型的 thinking 模式》讲 DeepSeek 的 thinking 参数和回传规则，《Agent 的前缀缓存怎么测》讲缓存测试方法，DeepSeek 的缓存同样按前缀命中。本文件夹里已有的《九天从 bigram 到 DeepSeek API》和三篇 dsh 文章是另一条线（从零入门和 DeepSeek Harness 插件开发），不在上表里。

新增文章的 front matter 写 `index: index.html`，涉及模型列表、API 参数、价格的内容写明“截至某日”和官方出处。在仓库根目录生成文章并重建两个目录页：

```bash title="在仓库根目录运行"
S=.claude/skills/tech-article-html/scripts
node $S/md_to_article.mjs deepseek/deepseek_xxx.md
python3 $S/build_index.py deepseek --title 'DeepSeek 学习笔记' --back ../index.html
python3 $S/build_index.py .
```
