# 读懂 DeepSeek：一条主线贯穿三年模型

从 2023 年底的 DeepSeek LLM 7B/67B 到 2026 年 9 月的 V4.1-Flash，DeepSeek 的论文基本都在回答两个问题：怎样把每个 token 的训练和推理成本压下来（架构线），以及怎样用便宜的强化学习换来推理能力（训练线）。不过只讲这两条线会漏掉 DeepSeek 自己当一等公民来对待的第三层：硬件协同设计和基础设施。所以本文建议的框架是“两条主线加一层系统底座”。截至 2026-11-23，官方 API 只剩两个模型 ID。`deepseek-flash` 指向 V4.1-Flash-1017，支持视觉；`deepseek-v4-pro` 指向 V4-Pro-0813，只支持文本。两者都是 1M 上下文、最长 384K 输出，默认开启 thinking，effort 默认 `high`。老名字 `deepseek-chat`/`deepseek-reasoner` 按公告在 2026-07-24 停用。对有 Transformer 基础的读者，最短的路径是五篇：DeepSeekMoE、V2（MLA）、DeepSeekMath（GRPO）、R1，再加 V4 的注意力与后训练章节。完整路径一共 12 篇，分 8 个阶段。本文是对旧文的独立重写。几处官方资料自相矛盾或尚未核实的地方都单独标了出来，没有替读者抹平：V4 的 arXiv 编号与日期对不上，V4-Pro 的路由说法前后冲突，V4.1-Flash 的参数量有 552B 和 763B 两种说法，2023–2024 年的细节多数凭记忆、未重新抓取。

## 四个时代，规模从 67B 涨到 1.6T，上下文从 4K 涨到 1M

DeepSeek 的模型谱系可以切成四段。2023 年底到 2024 年初是稠密模型和各个专项分支：LLM、Coder、MoE、Math、VL。2024 年是 V2 家族，MLA 和 DeepSeekMoE 第一次一起上了大规模模型。2024 年 12 月到 2025 年 12 月是 V3 基座家族，中间经过 R1、V3.1 混合思考，到 V3.2 引入 DSA 稀疏注意力。2026 年是 V4 家族，以及架构换代的 V4.1-Flash。

下表的“可信度”一列需要说明。“官方”指本轮直接抓取过 api-docs.deepseek.com 的 Change Log/新闻页或 Hugging Face 模型卡，并且得到了确认。“未复核”指日期、规模或上下文来自研究者的记忆，只附了 arXiv 链接，本轮没有重新打开核对，发布前应抽查。还有一点要注意：Hugging Face 上的“updated”日期是仓库最后更新的时间，不等于发布日期，表中只把它当旁证。

| 日期 | 模型 | 规模（总/激活） | 上下文 | 关键新意 | 出处 | 可信度 |
|---|---|---|---|---|---|---|
| 2023-11 | DeepSeek Coder | 1.3B/6.7B/33B | 16K | 项目级代码语料 + FIM | [arXiv 2401.14196](https://arxiv.org/abs/2401.14196) | 未复核 |
| 2023-11 下旬 | DeepSeek LLM | 7B/67B 稠密，2T tokens | 4K | 缩放律与超参选择 | [arXiv 2401.02954](https://arxiv.org/abs/2401.02954) | 未复核 |
| 2024-01 | DeepSeekMoE 16B | 16.4B / ~2.8B | — | 细粒度专家 + 共享专家 | [arXiv 2401.06066](https://arxiv.org/abs/2401.06066) | 未复核 |
| 2024-02 | DeepSeekMath 7B | 7B | — | 提出 GRPO | [arXiv 2402.03300](https://arxiv.org/abs/2402.03300) | 未复核 |
| 2024-03 | DeepSeek-VL | 1.3B/7B | — | 视觉语言 | [arXiv 2403.05525](https://arxiv.org/abs/2403.05525) | 未复核 |
| 2024-05 | DeepSeek-V2（及 V2-Lite 16B/2.4B） | 236B / 21B | 128K | MLA + 规模化 DeepSeekMoE；API 于 05-17 上线 | [arXiv 2405.04434](https://arxiv.org/abs/2405.04434)；[Change Log](https://api-docs.deepseek.com/updates) | 规模未复核，API 日期官方 |
| 2024-06 | DeepSeek-Coder-V2 | 236B / 21B（Lite 16B/2.4B） | 128K | 338 种编程语言；API 06-14 上线 | [arXiv 2406.11931](https://arxiv.org/abs/2406.11931)；[Change Log](https://api-docs.deepseek.com/updates) | 规模未复核，API 日期官方 |
| 2024-09-05 | DeepSeek-V2.5 | 236B | — | chat 与 coder 合并 | [Change Log](https://api-docs.deepseek.com/updates) | 官方 |
| 2024-12-10 | V2.5-1210 | 236B | — | MATH-500 74.8→82.8 | [Change Log](https://api-docs.deepseek.com/updates) | 官方 |
| 2024-12 | DeepSeek-VL2 | 3B/16B/27B MoE | — | MoE 视觉语言 | [HF 列表](https://huggingface.co/models?author=deepseek-ai&sort=created&p=1) | 论文号未复核 |
| 2024-12-26 | DeepSeek-V3 | 671B / 37B（HF 标 684B，含 MTP 模块），14.8T tokens | 128K | 无辅助损失均衡、MTP、FP8 训练、DualPipe | [arXiv 2412.19437](https://arxiv.org/abs/2412.19437)；[Change Log](https://api-docs.deepseek.com/updates) | 日期官方，细节未复核 |
| 2025-01-20 | DeepSeek-R1 / R1-Zero + 蒸馏系列 | 基于 V3-Base；蒸馏 1.5B–70B | — | 纯 RL 涌现长 CoT；MIT 许可 | [Change Log](https://api-docs.deepseek.com/updates)；[arXiv 2501.12948](https://arxiv.org/abs/2501.12948) | 官方 |
| 2025-03-24 | V3-0324 | 671B | — | GPQA 59.1→68.4，AIME 39.6→59.4 | [Change Log](https://api-docs.deepseek.com/updates) | 官方 |
| 2025-04-30 | Prover-V2 | 671B / 7B | — | Lean 4 子目标分解 | [HF org](https://huggingface.co/deepseek-ai) | 官方（HF 日期） |
| 2025-05-28 | R1-0528 | — | — | AIME 2025 70.0→87.5；加入 JSON 与函数调用 | [Change Log](https://api-docs.deepseek.com/updates) | 官方 |
| 2025-08-21 | DeepSeek-V3.1 | 685B | 128K（未复核） | 一个模型同时提供 thinking 与 non-thinking | [Change Log](https://api-docs.deepseek.com/updates)；[HF 卡](https://huggingface.co/deepseek-ai/DeepSeek-V3.1) | 官方 |
| 2025-09-22 | V3.1-Terminus | 685B | — | 减少中英混杂，Agent 能力增强 | [Change Log](https://api-docs.deepseek.com/updates) | 官方 |
| 2025-09-29 | V3.2-Exp | 685B | — | 首次引入 DSA | [Change Log](https://api-docs.deepseek.com/updates) | 官方 |
| 2025-10 | DeepSeek-OCR | 3B | — | 把上下文渲染成图像来压缩 | [arXiv 2510.18234](https://arxiv.org/abs/2510.18234) | 官方 |
| 2025-11-27 | DeepSeek-Math-V2 | 685B | — | 可自我验证的定理证明 | [HF 列表](https://huggingface.co/models?author=deepseek-ai&sort=created) | HF 日期 |
| 2025-12-01 | V3.2 / V3.2-Speciale | 685B | 未确认 | thinking 首次融入工具调用；Speciale 只在临时端点开放到 12-15 | [news251201](https://api-docs.deepseek.com/news/news251201)；[arXiv 2512.02556](https://arxiv.org/abs/2512.02556) | 官方 |
| 2026-04-24 | **V4 Preview**（Pro / Flash） | **Pro 1.6T/49B，Flash 284B/13B**，预训练 >32T tokens | **1M** | CSA+HCA 混合注意力、mHC、Muon、FP4 专家；MIT | [news260424](https://api-docs.deepseek.com/news/news260424)；[HF V4-Pro](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) | 官方 |
| 2026-07-31 | V4-Flash-0731 | 架构与规模同 V4-Flash，只重做后训练 | 1M | 公测 | [Change Log](https://api-docs.deepseek.com/updates) | 官方 |
| 2026-08-13 | **V4-Pro GA / V4-Pro-0813** | HF 标 1.7T | 1M | Agent 能力大幅升级；effort 分 low/high/max；Responses API | [news260813](https://api-docs.deepseek.com/news/news260813) | 官方 |
| 2026-08-21 | V4-Flash-Vision-Exp | HF 标 305B | — | 实验性多模态 | [Change Log](https://api-docs.deepseek.com/updates) | 官方 |
| 2026-09-10 | **V4.1-Flash** | **552B 主干（HF 标 763B）**，prefill 激活 8B，decode 激活 16B | **1M** | 因果编码器–解码器、CSA2、FP4 KV、Engram、原生视觉 | [news260910](https://api-docs.deepseek.com/news/news260910)；[HF 卡](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) | 官方 |
| 2026-10-17 | V4.1-Flash-1017 | 同上 | 1M | 帕累托改进：分数更高、输出更短、成本更低 | [news261017](https://api-docs.deepseek.com/news/news261017) | 官方 |

表外还有几条支线。Janus / Janus-Pro 做统一的多模态理解与生成，Janus-Pro 约在 2025-01-27 发布（记忆）。Prover-V1/V1.5 是 7B 定理证明模型。DeepSeek-OCR-2（3B）在 HF 上的日期是 2026-02-03。它们都能在 [HF 列表](https://huggingface.co/models?author=deepseek-ai&sort=created)里找到，但都不在主线依赖链上。

**有几处冲突必须明说。**

第一，V4 的论文编号与发布日期对不上。V4-Pro 模型卡链接的论文是 arXiv 2606.19348，abs 页写的是“Submitted on 26 Apr 2026”，HF 论文页也写着“Published Apr 26”（[arXiv](https://arxiv.org/abs/2606.19348)）。可是按 arXiv 的编号规则，2606 开头通常对应 2026 年 6 月。两份笔记一份说“已核实”，一份说“ID 看着可疑、未独立核对”。本文的处理是：日期以官方新闻 2026-04-24 为准，引用时写“技术报告，标注日期 2026-04-26，arXiv 2606.19348（编号与日期的矛盾未解决）”，同时优先给 HF 上的 [DeepSeek_V4.pdf](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf)。

第二，V4.1-Flash 的参数量有两种说法。官方新闻和模型卡写的是“552B backbone”，Hub 上显示总量是 763B（[HF 卡](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash)）。两者相差 211B，模型卡列出的 Engram 条件记忆是 196B，再加上视觉塔，大致能补上这个差额。不过这只是推算，官方没有给出拆分。建议写成“552B 主干（HF 计 763B，含 Engram 等）”。

第三，V4 系列的规模数字也不完全一致。新闻稿写 Flash 是 284B，但 HF 上 V4-Flash-Base 标 292B，指令版标 291B，V4-Pro-0813 标 1.7T（[HF org](https://huggingface.co/deepseek-ai)）。这类出入多半来自 HF 把 MTP 等附加模块也计入了参数，和 V3 的 671B 对 684B 是同一种情况。

第四，“V4 正式版”的含义。2026-04-24 的开源权重自己标注为 preview。所谓“正式发布”其实是 API 侧的里程碑：7 月 31 日 Flash-0731 公测，8 月 13 日 Pro 正式 GA。之后没有另外的“V4 final”开源包，只有 -0731/-0813 两个仓库。

第五，“V4.1”到现在只有 Flash 一个型号。研究者没有找到 V4.1-Pro 的报告或发布（[news260910](https://api-docs.deepseek.com/news/news260910)）。V4.1-Flash 的技术报告有两个入口：HF 上的 [PDF](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/DeepSeek_V41_Tech_Report.pdf)，以及 arXiv 2609.19969（v1 时间戳 2026-09-17，晚于 9 月 10 日的发布）（[arXiv HTML](https://arxiv.org/html/2609.19969v1)）。

## 架构线：注意力和 FFN 两条链，都在砍 KV cache 和 FLOPs

把架构论文按“它在解决哪个瓶颈”来排，脉络会非常清楚。驱动整条线的只有两个问题：每个 token 的 FLOPs 太多，以及长上下文下 KV cache 和注意力的开销太大。前者靠 FFN 侧的稀疏化解决，后者靠注意力侧的压缩和稀疏化解决。残差流和数值精度是另外两个较小的分支。用“注意力 / FFN / 残差 / 数值”四格来归类，比按时间顺序罗列好记得多。

**FFN 链从 DeepSeekMoE 开始。** 传统 top-k MoE 有两个毛病：一个专家要学杂糅的知识，多个专家又重复学同样的通用知识。DeepSeekMoE 的对策是把每个专家切成 m 个更小的专家，激活 mK 个，同时隔离出始终激活的共享专家来吸收通用知识，让路由专家专心专精（[arXiv 2401.06066](https://arxiv.org/abs/2401.06066)）。到 V3，负载均衡改成**无辅助损失**的方式：给每个专家的亲和度分数加一个偏置 b_i。这个偏置只参与 top-k 选择，不进门控权重，每一步根据专家的负载上调或下调（[arXiv 2412.19437](https://arxiv.org/abs/2412.19437)）。V4 把路由专家的权重降到 FP4（[HF V4-Pro](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro)）。2026 年 1 月的 Engram 论文开辟了“第二条稀疏轴”：MoE 是条件计算，Engram 是条件记忆，用一张现代化的 N-gram 嵌入表做 O(1) 的确定性查找。因为寻址是确定的，表可以放在主机内存里预取，卸载 100B 参数的表开销不到 3%（[arXiv 2601.07372](https://arxiv.org/abs/2601.07372v1)）。Engram 已确认用在 V4.1-Flash 上，规模 196B。**V4 有没有用 Engram 尚无定论**：V4 摘要没有提到，只有二手网站声称用了，本文把它当作未核实。

**注意力链是全书的骨干：MLA → NSA → DSA → CSA/HCA → CSA2。** V2 的 MLA 把 K 和 V 联合压缩成一个低秩潜向量 c_KV，推理时只缓存这个潜向量，上投影矩阵可以被吸收进 Q 和 O 的投影里。RoPE 和这种矩阵吸收不可交换，所以要另外带一个小的“解耦 RoPE”键。V2 报告称，与 DeepSeek 67B 相比，**KV cache 减少 93.3%，训练成本降低 42.5%，最大生成吞吐提升到 5.76 倍**（[arXiv 2405.04434](https://arxiv.org/abs/2405.04434)；这些数字来自记忆，未复核）。2025 年 2 月的 NSA 让每个 query 走三条带门控的分支：压缩的粗粒度块摘要、按分数选出的 top-n 细粒度块、局部滑动窗口。块选择和 GPU 的访存方式、GQA 分组对齐，所以能拿到真实的墙钟加速，并且从预训练开始就是稀疏的。这篇论文获得了 **ACL 2025 最佳论文**（[ACL Anthology](https://aclanthology.org/2025.acl-long.1126)；[SCMP](https://www.scmp.com/tech/big-tech/article/3320255/deepseek-founder-shares-best-paper-award-top-global-ai-research-conference)）。V3.2 的 DSA 换了一种做法：用一个轻量的 **lightning indexer**（头数少，用 ReLU，FP8 运行）给前面每个 token 打分，每个 query 只对 top-k 个 token 做注意力。DSA 不是原生训练出来的，而是在 V3.1-Terminus 上通过继续训练改装进去的，也是 V3.2 相对 V3.1-Terminus 唯一的架构改动（[arXiv 2512.02556](https://arxiv.org/abs/2512.02556)）。笔记里提到 top-k=2048、先做只训练 indexer 的稠密预热，这两个细节来自记忆，需要回到 PDF 核对。

V4 把这些部件拼成了**混合注意力**。CSA 把每 m 个 token 的 KV 压成一条（由 2m 条有重叠的条目构造），用 FP4 的 lightning indexer 选出 top-k，再加上共享 KV 的 MQA 和未压缩的滑动窗口。HCA 用大得多、不重叠的压缩比 m′，对全部压缩条目做稠密注意力。在 1M token 下与 V3.2 相比，**V4-Pro 的单 token FLOPs 约为 27%，KV cache 为 10%；V4-Flash 分别约为 10% 和 7%**（[arXiv HTML](https://arxiv.org/html/2606.19348v1)）。可以看出每一步都继承了上一步的技巧：MLA 的共享 KV 留在了 CSA 的 MQA 里，DSA 的 indexer 进了 CSA，NSA 的“压缩 + 选择 + 滑窗”三分支几乎一一对应地出现在 CSA 中。所以读懂 MLA 和 NSA 的人，读 V4 注意力章节会很快。笔记没有拿到 m、m′、k 和窗口大小的具体值，也没有拿到层的交错方式。

V4.1-Flash 不是 V4 的小修小补，而是新一代架构。它借鉴了 YOCO，采用**因果编码器–解码器**：20 层因果编码器加 20 层解码器，解码器的全局 KV 从编码器最后一层的隐状态投影出来，大部分 prompt token 因此不必走完整的解码器计算，于是 prefill 激活 8B、decode 激活 16B。CSA2 在层间共享全局 KV 和 indexer 键，每层可以选 Full、Reindex 或 Reuse 三种模式。主 KV cache 降到 FP4，每 token 的全局 KV 约 890 字节，约为 V4-Flash 的 1/4。“SWA Bounded Replay”在需要时重建滑窗状态而不是持久化它，让落到 SSD/主机的 KV 降到约 1/8（[arXiv HTML](https://arxiv.org/html/2609.19969v1)；[news260910](https://api-docs.deepseek.com/news/news260910)）。“8B 对 16B 的不对称是因为编码器负责 prefill、解码器负责生成”是研究者的解读，需要对照报告原文的措辞。

残差流有自己的一次升级，就是 mHC。Hyper-Connections 把残差流拓宽成 n 条并行流，但丢了恒等映射，规模一大训练就不稳。mHC 用 Sinkhorn-Knopp 把混合矩阵投影到双随机矩阵流形（Birkhoff 多面体）上，恢复类似恒等的信号传播（[arXiv 2512.24880](https://arxiv.org/html/2512.24880v2)；[PMLR](https://proceedings.mlr.press/v306/xie26aa.html)）。V4 使用了 4 条残差流，同时引入 Muon 优化器，MTP 沿用 V3 的配置（[HF V4-Pro](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro)）。

## 训练线：先把 RL 做便宜，再让推理变得可控

训练线的弧线更短，也更容易讲成一个故事：先把 RL 做便宜（GRPO），再证明光靠 RL 就能长出推理能力（R1-Zero），然后让它好用、可迁移（R1 多阶段训练加蒸馏），最后让它在一个模型里可控（V3.1 混合思考、V4 effort 档位）。

DeepSeekMath 的 GRPO 去掉了 PPO 的价值网络。对每个 prompt 采样 G 个输出，用组内归一化的奖励 (r_i − mean)/std 作为优势，KL 惩罚直接写进损失函数而不是加在奖励上，这样就省掉了一个和策略模型一样大的 critic（[arXiv 2402.03300](https://arxiv.org/abs/2402.03300)）。这篇论文的数据部分同样值得一看：120B token 的数学语料从 Common Crawl 里迭代挖出来，7B 模型不用工具在 MATH 上约达 51.7%（记忆，未复核）。

R1-Zero 在 V3-Base 上直接跑 GRPO，不做 SFT，奖励只有规则：答案准确性加格式。它刻意不用神经奖励模型，以避开 reward hacking。按论文报告，AIME 2024 的 pass@1 从 15.6% 升到 71.0%，回答长度自发变长，还出现了自我反思的“aha moment”（[arXiv 2501.12948](https://arxiv.org/abs/2501.12948)；具体数字来自记忆）。R1 用四个阶段修好了 R1-Zero 可读性差、中英混杂的问题：冷启动 SFT，面向推理的 RL（加语言一致性奖励），拒绝采样构造约 80 万条 SFT 数据，最后做全场景 RL。用这 80 万条数据去 SFT Qwen2.5 和 Llama 3 的小模型，效果好过直接在小模型上跑 RL。论文里的“失败尝试”一节（PRM、MCTS）尤其值得一读。2025 年 9 月，R1 经同行评审后发表在 **Nature 645, 633–638**，被称为第一个经历同行评审的主流大模型（[Nature](https://www.nature.com/nature-index/article/10.1038/s41586-025-09422-z)；[Scientific American](https://scientificamerican.com/article/secrets-of-chinese-ai-model-deepseek-revealed-in-landmark-paper)）。如果想读最完整的版本，应该读 Nature 版及其补充材料；arXiv 版是历史版本。媒体报道过“R1 训练成本约 29.4 万美元”，但笔记没有核对补充材料，本文不采用这个数字。

V3.1 用 chat template 切换两种模式：thinking 的前缀以 `<think>` 结尾，non-thinking 的前缀以 `</think>` 结尾。V3.1-Base 还做了更大规模的两阶段长上下文扩展：32K 阶段 630B token，128K 阶段 209B token。V3.1 没有独立论文，HF 模型卡就是一手来源（[HF 卡](https://huggingface.co/deepseek-ai/DeepSeek-V3.1)）。V3.2 在 DSA 之外贡献了可扩展的 RL 框架、大规模 agentic 任务合成管线（官方说法是 1,800+ 合成环境、85k+ 指令），以及在 IMO/IOI 2025 达到金牌水平的 Speciale（[arXiv 2512.02556](https://arxiv.org/abs/2512.02556)；[news251201](https://api-docs.deepseek.com/news/news251201)）。V4 的后训练分两步：先按领域（数学、代码、Agent、指令遵循）各训一个 SFT+GRPO 专家，再用 on-policy 蒸馏（最小化对各教师的 reverse KL）合并成一个学生模型，对外提供 Non-think、Think High、Think Max 三档（[HF V4-Pro](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro)）。R1 的“蒸馏胜过小模型 RL”和 V4 的 on-policy 蒸馏是同一个思想在两个尺度上的应用，值得放在一起读。**各个 effort 档位具体怎么训练（预算控制、长度惩罚）笔记没能取到，仍属未知。** V4.1-Flash 模型卡则改成了 1–100 的“连续可控推理强度”（[HF 卡](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash)）。

## 系统底座应单列一层，而不是硬塞进两条线

“两条线”作为顶层叙事够用，但它藏起了一层 DeepSeek 自己反复强调的东西。DeepSeek 在计算机体系结构会议 ISCA '25 上专门发了一篇论文，核心论点就是 V3 的“硬件感知模型协同设计”：用 MLA 省显存，用 MoE 在计算和通信之间取舍，用 FP8，再加多平面网络拓扑，并向硬件厂商提出了诉求（[arXiv 2505.09343](https://arxiv.org/abs/2505.09343)）。2025 年 2 月的 Open Infra Week 连续开源了七个组件，自称在 V3/R1 生产环境中验证过：FlashMLA（Hopper 上的 MLA 解码核）、DeepEP（专家并行 all-to-all）、DeepGEMM（FP8 GEMM，1350+ TFLOPS）、DualPipe、EPLB、3FS 文件系统、smallpond（[open-infra-index](https://docsearch.algolia.com/mcp/docs/repo/deepseek-ai/open-infra-index)）。NSA 的标题里就写着“Hardware-Aligned”。V4 报告有一整节基础设施内容，包括适配 mHC 的 DualPipe 和与 Comet 对比的 wave 式 EP 重叠。V4.1-Flash 的标题干脆就是 KV 字节数（[V4 HTML](https://arxiv.org/html/2606.19348v1)；[V4.1 HTML](https://arxiv.org/html/2609.19969v1)）。

更要紧的是，很多创新本来就跨在两条线中间。FP8 训练、DualPipe、MTP（既增加训练信号，又能用于投机解码），以及 V4 的 on-policy 蒸馏（属于训练，但动机是只部署一个模型），放进哪条线都别扭。所以本文的立场是采用“两条主线加一层系统底座”的框架：架构线与训练线并列，FP8/FP4、DualPipe、DeepEP/DeepGEMM/FlashMLA、3FS 和 ISCA 论文作为两条线共同的地基。这层地基对只有 Transformer 基础的读者最难，适合标成“进阶选读”。另一种做法是三条线并列，这样更准确，但学习曲线更陡。需要说明的是，DeepSeek 官方从没提出过“两条线”或“三条线”，这是编辑上的选择。

## 官方 API 现状：两个模型 ID，thinking 由参数控制

截至 2026-11-23，[Models & Pricing 页](https://api-docs.deepseek.com/quick_start/pricing)只列出两个模型：

| | `deepseek-flash` | `deepseek-v4-pro` |
|---|---|---|
| 实际模型 | DeepSeek-V4.1-Flash-1017 | DeepSeek-V4-Pro-0813 |
| 上下文 / 最长输出 | 1M / 384K | 1M / 384K |
| 视觉 | 支持 | 不支持 |
| 并发上限 | 2500 | 500 |
| 缓存命中输入（闲时/峰时，$/1M） | 0.003 / 0.006 | 0.022 / 0.044 |
| 缓存未命中输入 | 0.15 / 0.30 | 0.66 / 1.32 |
| 输出 | 0.60 / 1.20 | 1.98 / 3.96 |

两个模型都支持 JSON 输出、工具调用、Responses API、Anthropic 格式 API 和对话前缀续写（beta）。FIM（beta）只能在 non-thinking 模式下用。峰时是周一至周五 UTC 01:00–04:00 和 06:00–10:00，中国法定节假日除外，闲时价为峰时的 50%。这套峰谷计价从 2026-08-16 开始。从 2026-11-18 起，服务端搜索/抓取工具的结果按普通 token 计费（[Pricing](https://api-docs.deepseek.com/quick_start/pricing)；[Change Log](https://api-docs.deepseek.com/updates)）。端点有两个：OpenAI 格式的 `https://api.deepseek.com` 和 Anthropic 格式的 `https://api.deepseek.com/anthropic`（[Quick Start](https://api-docs.deepseek.com/)）。2026-11-13 起，`deepseek-flash` 新增了一组服务端工具：`deepseek_web_search`（thinking 模式下是“深度思考搜索”）、`deepseek_read_url`、文件输入、安全检查参数，另外上线了独立的 Search & Fetch、OCR、ASR、TTS API（[Change Log](https://api-docs.deepseek.com/updates)）。

**Thinking 现在由请求参数控制，不再靠换模型 ID。** 两个模型默认都开启 thinking，effort 默认是 `high`。OpenAI 格式下用 `thinking: {type: enabled|disabled}` 开关（OpenAI SDK 里要放进 `extra_body`），用 `reasoning_effort: low|high|max` 选档。别的厂商的档位名会被映射：minimal/low→low，medium/high/xhigh→high，max/ultra→max。thinking 模式下，`temperature`、`presence_penalty`、`frequency_penalty` 会被静默忽略。`top_p` 在 flash 上只在 0.95–1.0 之间生效，在 v4-pro 上被忽略。最容易踩的坑是：**带工具的多轮对话里，客户端必须把之前所有轮次的 `reasoning_content` 原样传回，否则 API 返回 400**（[Thinking Mode 指南](https://api-docs.deepseek.com/guides/thinking_mode)）。Anthropic 格式走 `output_config.effort`，用 `none` 关闭 thinking，但研究者抓到的映射表有乱码，具体参数名以线上页面为准。low/high/max 三档是 2026-08-13 随 V4-Pro GA 一起推出的（[news260813](https://api-docs.deepseek.com/news/news260813)）。

**更名与停用链条**是：`deepseek-chat`/`deepseek-reasoner`（2024 → 2026-07-24）→ `deepseek-v4-flash`/`deepseek-v4-pro`（2026-04-24 起）→ `deepseek-flash`（与代际无关的别名，2026-09-10 起）+ `deepseek-v4-pro`。常见的说法“老别名被 `deepseek-flash` 取代”并不准确。2026-04-24 的公告说 `deepseek-chat`/`deepseek-reasoner` 会在 2026-07-24 15:59 UTC 之后彻底停用，在此之前分别路由到 `deepseek-v4-flash` 的 non-thinking 和 thinking 模式（[news260424](https://api-docs.deepseek.com/news/news260424)）。也就是说，停用那天的接替者是 `deepseek-v4-flash`，`deepseek-flash` 要到 9 月 10 日才出现。停用是否按期执行，官方没有事后确认，Change Log 里也没有对应条目，现在的文档只是不再提这两个名字。按期执行的说法来自二手报道（[digitalapplied](https://www.digitalapplied.com/blog/model-deprecation-calendar-api-sunset-survival)；[dev.to](https://dev.to/zephyrelabs369/deepseek-retired-its-legacy-model-ids-is-your-api-relay-still-mapping-them-correctly-1jfm)）。2026-09-10，`deepseek-v4-flash` 和 `deepseek-v4-flash-vision-exp` 两个模型下线，但名字还能用，会路由到 V4.1-Flash-1017，按 Flash 价计费。`deepseek-coder` 早在 2024-09-05 就合并进了 V2.5，没有找到明确的停用日期。Change Log 2025-01-20 的条目里有一句“V3 可用 `model='deepseek-reasoner'` 调用”，与同条目中“R1 即 reasoner”的说法矛盾，多半是文档笔误或抽取错误（[Change Log](https://api-docs.deepseek.com/updates)）。

**V4-Pro 的去留是最明显的官方自相矛盾。** news260910 写着 `deepseek-v4-pro` 将“自 2026-09-14 04:00 UTC 起路由到 V4.1-Flash，直到 V4.1-Pro 上线”，并说 V4-Pro“正在逐步退役”（[news260910](https://api-docs.deepseek.com/news/news260910)）。同一天的 Change Log 却写着 V4 Pro API“在 2026-09-14 之后继续提供，计费不变”（[Change Log](https://api-docs.deepseek.com/updates)），而当前价格页仍然列着 `deepseek-v4-pro` = V4-Pro-0813。最可能的解释是公告发出后方针改了，以 Change Log 和价格页为准，判断为“V4-Pro 仍在服务”。但读者应当知道，新闻页上的那段话至今没有撤下。

## 阅读顺序：8 个阶段 12 篇，或 5 篇快速通道

排序的原则是：每篇 DeepSeek 论文配一篇外部先修；只有在依赖关系要求时才在两条线之间切换；引入新机制的章节精读，堆基准分数的部分和支线略读。DeepSeek 官方没有推荐过阅读清单，下面的顺序是根据前文的依赖结构编排的。外部先修论文的 arXiv 编号来自记忆，本轮没有重新核对。

| 阶段 | 论文 | 精读 | 略读 | 配读先修 |
|---|---|---|---|---|
| 0 | 先修，边读边补 | — | — | Transformer 1706.03762、RoPE 2104.09864、MQA 1911.02150、GQA 2305.13245、Switch 2101.03961、PPO 1707.06347、InstructGPT 2203.02155 |
| 1 | [DeepSeekMoE 2401.06066](https://arxiv.org/abs/2401.06066)；可选 [DeepSeek LLM 2401.02954](https://arxiv.org/abs/2401.02954) | §3 细粒度 + 共享专家，MoE 输出公式 | 实验部分；LLM 只看缩放律 | Switch / GShard |
| 2 | [DeepSeekMath 2402.03300](https://arxiv.org/abs/2402.03300) | GRPO 目标函数，PPO 与 GRPO 对比图，统一范式一节 | 数据管线 | PPO |
| 3 | [DeepSeek-V2 2405.04434](https://arxiv.org/abs/2405.04434) | MLA 一节和解耦 RoPE 附录 | 对齐与评测 | MQA/GQA、RoPE |
| 4 | [DeepSeek-V3 2412.19437](https://arxiv.org/abs/2412.19437) | §2 无辅助损失均衡、MTP；§3 DualPipe、FP8 | 评测；进阶可配 [ISCA 2505.09343](https://arxiv.org/abs/2505.09343) 和 FlashMLA/DeepEP/DeepGEMM README | 流水线并行、FP8 格式 |
| 5 | [DeepSeek-R1 2501.12948](https://arxiv.org/abs/2501.12948) 或 [Nature 版](https://www.nature.com/nature-index/article/10.1038/s41586-025-09422-z) | R1-Zero 曲线、四阶段管线、蒸馏表、失败尝试 | 基准表 | 先重读阶段 2 的 GRPO 公式 |
| 6 | [NSA 2502.11089](https://arxiv.org/abs/2502.11089)，然后 [V3.2 2512.02556](https://arxiv.org/abs/2512.02556) + [V3.1 HF 卡](https://huggingface.co/deepseek-ai/DeepSeek-V3.1) | NSA 三分支与核设计图；DSA 的 indexer + top-k | V3.2 的 RL 扩展与 agentic 合成；V3.1 卡约 5 分钟 | FlashAttention 2205.14135 |
| 7 | [mHC 2512.24880](https://arxiv.org/html/2512.24880v2)，然后 [Engram 2601.07372](https://arxiv.org/abs/2601.07372v1) | 中等深度：恒等映射论证与 Sinkhorn 投影；条件记忆与 U 形分配律 | 基础设施优化部分 | Hyper-Connections 2409.19606 |
| 8 | [V4 2606.19348](https://arxiv.org/html/2606.19348v1)，然后 [V4.1-Flash 2609.19969](https://arxiv.org/html/2609.19969v1) | V4 §2.3 CSA/HCA 与后训练；V4.1 编码器–解码器与 CSA2 | 基础设施、基准；V4.1 的 FP4 KV 和 Bounded Replay（做推理服务的人再精读） | StreamingLLM 2309.17453、Muon/Moonlight 2502.16982、YOCO 2405.05254 |

**快速通道（约 5 篇）**：DeepSeekMoE §3 → V2 的 MLA 一节 → DeepSeekMath 的 GRPO 一节 → R1 → V4 §2.3 加后训练。V3 只看 Fig. 2 和无辅助损失均衡、MTP 两节就够。这条路线覆盖了架构线和训练线的骨架，只牺牲了稀疏注意力的演化细节。

节奏要预先说明。阶段 1–5 是 2024 年到 2025 年初的核心内容，二手讲解已经很多。阶段 6–8 是 2025 下半年到 2026 年的材料，好的二手讲解还很少，读者得更多地直接啃 PDF。所以在这一段最好自己动手画一张 NSA、DSA、CSA/HCA、CSA2 的对比示意图，否则“压缩 + 选择 + 滑窗”的继承关系很容易看丢。支线按兴趣挑：[DeepSeek-OCR 2510.18234](https://arxiv.org/abs/2510.18234) 是另一种长上下文压缩思路，可以配着它的反驳论文 [2512.03643](https://arxiv.org/html/2512.03643v1) 一起读；Prover 系列在 Lean 里把可验证奖励推到了极致，概念上和 R1 呼应；Janus/VL 到 V4.1-Flash 走向多模态时才变得相关。

## 结论

把 DeepSeek 三年的论文放在一起看，最值得带走的结论不是哪个单项技巧，而是它的继承方式：几乎每个新机制都保留了前代的一个部件。共享 KV 从 MLA 传到 CSA，indexer 从 DSA 传到 CSA，三分支从 NSA 传到 CSA，蒸馏从 R1 传到 V4。这意味着学习成本主要花在前端。啃透 MLA、NSA、GRPO 三个点之后，2026 年的 V4 和 V4.1 读起来是增量。反过来，跳过前面直接读 V4，会被成串的缩写卡住。V4.1-Flash 是第一次明显的断代：编码器–解码器、Engram、原生视觉，它的设计重心已经从降低 FLOPs 移到了 KV 的字节数和存储层级。下一阶段大概率会被内存层级而不是算力主导，这正是本文主张把系统底座单列出来的理由。

对 API 使用者来说，有两个变化需要改掉旧习惯。第一，thinking 已经从“选哪个模型”变成了“传什么参数”，而且默认开启，从 `deepseek-chat` 迁移过来的代码如果不显式关闭，成本和延迟都会变。第二，`deepseek-flash` 是一个跟着代际走的浮动别名，底层模型会在不另行通知的情况下换代。需要可复现结果的场景应当记录响应里返回的实际模型版本，不能只记别名。至于 V4-Pro 什么时候被 V4.1-Pro 取代，官方信息本身就互相矛盾，在 V4.1-Pro 真正发布之前，不宜依赖任何一方的说法。
