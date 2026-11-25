---
title: 九天从 bigram 到 DeepSeek API：先写一个小 GPT，再把模型接进 agent
kicker: 学习路线
deck: 按九天的学习计划，从只看前一个字的 bigram 写到多头注意力的小 GPT，讲清 KV cache 和采样，看 DeepSeek 用 MoE 和 MLA 换掉了哪两个部件，再把 DeepSeek API 的结构化输出、流式、工具调用、重试、成本、thinking、前缀缓存和 TTFT 逐项接进 agent。
meta:
  - DeepSeek API 文档截至 2026-11-23
  - D1–D9 学习计划
  - Python · PyTorch · OpenAI SDK
footer: 来源：Karpathy “Let's build GPT: from scratch” 与 nanoGPT（D1–D3 的结构参照，代码为本文重写的教学示意）；DeepSeek-V2（arXiv 2405.04434）与 V3（arXiv 2412.19437）论文及 V3 公开 config；api-docs.deepseek.com 的 JSON Output、Tool Calls、Context Caching、Rate Limit、Error Codes、Pricing、Create Chat Completion、Thinking Mode 页面（2026-11-23 读取）。文中代码都是示意，没有在本文里实际运行；价格请从官方定价页填入。
index: index.html
updated: 2026-11-23
created: 2026-11-23 17:47
---

这份计划分成三段。前四天自己动手写一个最小的语言模型：从只看前一个字的 bigram 开始，加上 self-attention，再拼成多头、FFN、残差俱全的小 GPT，最后弄清推理时的 KV cache 和采样。第五天看 DeepSeek 在这个结构上改了哪里。后四天换到工程侧，把 DeepSeek API 接进一个 agent：结构化输出、流式、工具调用循环、重试与成本统计，以及 thinking 档位、前缀缓存和首 token 延迟的测量。

每一节对应计划里的一天，左边讲要点，右边是代码或图。代码都是为本文写的教学示意，模型部分参照 nanoGPT 的结构，API 部分按 2026-11-23 的 DeepSeek 官方文档写。

- **D1–D3 是一条线**：bigram 只看一个字，self-attention 让每个位置看见前面所有位置，多头、FFN、残差和 LayerNorm 让它能堆深
- **D4 是推理的两件事**：KV cache 省掉重复计算，采样决定从概率分布里怎么挑下一个 token
- **D5 只改了两个部件**：MLA 替换注意力里的 K、V 缓存方式，MoE 替换 FFN
- **D6–D9 都绕着同一份 messages 转**：工具调用要原样追加消息，thinking 带工具时要回传 `reasoning_content`，前缀缓存要求前面的内容一个字都不改

## 路线总览：三段九天，后面几天都用到前面的东西

九天不是九个孤立的主题。D2 写的注意力和 D3 写的 FFN，正是 D5 里 DeepSeek 用 MLA 和 MoE 替换的两个部件；D4 里每次生成都要维护的 KV cache，放到服务端跨请求保留下来，就是 D9 要测的前缀缓存；D6 的流式解析、D7 的消息追加、D8 的 usage 统计，在 D9 的测量代码里会全部用上。

所以建议按顺序做，每一天的代码都留着：D3 的 GPT 是 D4 加 KV cache 的基础，D7 的工具循环加上 D8 的重试和记账，就是 D9 跑实验用的 agent。

```svg title="图 1：九天计划的三段，以及前后几天之间的联系" tone="light"
<svg viewBox="0 0 640 250" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="九天学习路线">
<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#504e49"/></marker></defs>
<text x="20" y="30" font-size="13" fill="#141413" text-anchor="start" font-weight="700">原理</text>
<text x="20" y="110" font-size="13" fill="#141413" text-anchor="start" font-weight="700">架构</text>
<text x="20" y="190" font-size="13" fill="#141413" text-anchor="start" font-weight="700">工程</text>
<text x="620" y="30" font-size="12" fill="#504e49" text-anchor="end">模型怎么工作</text>
<text x="620" y="110" font-size="12" fill="#504e49" text-anchor="end">DeepSeek 改了哪里</text>
<text x="620" y="190" font-size="12" fill="#504e49" text-anchor="end">API 怎么接进 agent</text>
<rect x="20" y="40" width="138" height="44" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="89.0" y="67.0" text-anchor="middle" font-size="12.5" font-weight="600" fill="#141413">D1 bigram</text>
<line x1="159" y1="62" x2="171" y2="62" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="174" y="40" width="138" height="44" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="243.0" y="67.0" text-anchor="middle" font-size="12.5" font-weight="600" fill="#141413">D2 self-attention</text>
<line x1="313" y1="62" x2="325" y2="62" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="328" y="40" width="138" height="44" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="397.0" y="67.0" text-anchor="middle" font-size="12.5" font-weight="600" fill="#141413">D3 小 GPT</text>
<line x1="467" y1="62" x2="479" y2="62" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="482" y="40" width="138" height="44" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="551.0" y="67.0" text-anchor="middle" font-size="12.5" font-weight="600" fill="#141413">D4 KV cache 与采样</text>
<rect x="20" y="120" width="138" height="44" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="89.0" y="147.0" text-anchor="middle" font-size="12.5" font-weight="600" fill="#141413">D5 MoE 与 MLA</text>
<rect x="20" y="200" width="138" height="44" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="89.0" y="227.0" text-anchor="middle" font-size="12.5" font-weight="600" fill="#141413">D6 结构化与流式</text>
<line x1="159" y1="222" x2="171" y2="222" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="174" y="200" width="138" height="44" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="243.0" y="227.0" text-anchor="middle" font-size="12.5" font-weight="600" fill="#141413">D7 工具调用循环</text>
<line x1="313" y1="222" x2="325" y2="222" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="328" y="200" width="138" height="44" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="397.0" y="227.0" text-anchor="middle" font-size="12.5" font-weight="600" fill="#141413">D8 重试与成本</text>
<line x1="467" y1="222" x2="479" y2="222" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="482" y="200" width="138" height="44" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="551.0" y="227.0" text-anchor="middle" font-size="12.5" font-weight="600" fill="#141413">D9 thinking 与 TTFT</text>
<line x1="92" y1="86" x2="92" y2="117" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<line x1="92" y1="166" x2="92" y2="197" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<text x="104" y="105" font-size="11.5" fill="#504e49" text-anchor="start">D2 的注意力、D3 的 FFN 正是 D5 要替换的两个部件</text>
<text x="104" y="185" font-size="11.5" fill="#504e49" text-anchor="start">D4 的 KV cache 在 D9 里变成服务端的前缀缓存</text>
</svg>
```

## D1 bigram：只看前一个字，猜下一个字

bigram 是最小的语言模型：给定当前这个字符，预测下一个字符。整个模型就是一张“词表大小 × 词表大小”的表，第 i 行是“当前字符为 i 时，下一个字符分别是什么”的打分（logits）。它几乎学不到什么，但数据准备、训练循环、loss 和生成这几步，后面的 GPT 原封不动地沿用。

数据准备只有两件事：按字符把文本编码成整数；随机切出长度为 `block_size` 的片段，输入 `x` 和目标 `y` 错开一位。一个长度为 T 的片段同时提供了 T 个训练样本：看到第 1 个字猜第 2 个，看到前 2 个字猜第 3 个，依此类推。bigram 只用到每个位置自己，后面的 GPT 才会用到前面的全部。

```python title="data.py（示意）"
import torch

text = open("input.txt", encoding="utf-8").read()
chars = sorted(set(text))
stoi = {c: i for i, c in enumerate(chars)}
itos = {i: c for c, i in stoi.items()}
encode = lambda s: [stoi[c] for c in s]
decode = lambda ids: "".join(itos[i] for i in ids)

data = torch.tensor(encode(text), dtype=torch.long)
n = int(0.9 * len(data))
train, val = data[:n], data[n:]

def get_batch(split, block_size=8, batch_size=32):
    d = train if split == "train" else val
    ix = torch.randint(len(d) - block_size, (batch_size,))
    x = torch.stack([d[i:i + block_size] for i in ix])
    y = torch.stack([d[i + 1:i + block_size + 1] for i in ix])  # 向后错一位
    return x, y
```

```python title="bigram.py（示意）"
import torch.nn as nn
import torch.nn.functional as F

class Bigram(nn.Module):
    def __init__(self, vocab_size):
        super().__init__()
        self.table = nn.Embedding(vocab_size, vocab_size)  # 每个字符一行 logits

    def forward(self, idx, targets=None):
        logits = self.table(idx)                            # (B, T, vocab)
        if targets is None:
            return logits, None
        B, T, V = logits.shape
        loss = F.cross_entropy(logits.view(B * T, V), targets.view(B * T))
        return logits, loss
```

- **先算初始 loss 作为自检**：词表 65 个字符时，随机初始化的 loss 应该接近 `-ln(1/65) ≈ 4.17`；明显偏大说明初始化有问题
- **训练后大约 2.5**：再往下降不动了，因为模型只有一个字的上下文
- **`cross_entropy` 要求二维输入**：所以把 `(B, T, V)` 摊平成 `(B·T, V)`

### 生成：每次抽一个字，拼回去再抽

生成和训练用的是同一个 `forward`。每一步只取最后一个位置的 logits，做 softmax 得到概率，用 `torch.multinomial` 按概率抽一个 token，拼到序列末尾，再进入下一步。bigram 其实只需要最后一个字，但这样写，换成 GPT 后代码不用改，只需把输入截到 `block_size` 以内。

```python title="generate（示意）"
@torch.no_grad()
def generate(model, idx, max_new_tokens, block_size):
    for _ in range(max_new_tokens):
        logits, _ = model(idx[:, -block_size:])   # GPT 只能看 block_size 个位置
        probs = F.softmax(logits[:, -1, :], dim=-1)
        nxt = torch.multinomial(probs, num_samples=1)
        idx = torch.cat([idx, nxt], dim=1)
    return idx
```

> bigram 生成的文字有单词的样子，但连不成句子：它不知道两个字之前写过什么。D2 要解决的就是这个问题，让每个位置都能看见前面所有的位置。

## D2 self-attention：每个位置按相关程度汇总前面的信息

最朴素的办法是把前面所有位置的向量取平均。用一个下三角矩阵 `tril` 做矩阵乘法就能一次算完：第 t 行在前 t 个位置上各为 1/t，其余为 0。再进一步，把固定的平均换成可学的权重：先算一个分数矩阵，把右上角（未来的位置）填成 `-inf`，做 softmax 之后每一行和为 1，未来的位置权重为 0。

这张下三角的权重矩阵就是**因果遮罩**。它让训练可以并行：一个长度为 T 的片段一次前向就得到 T 个位置的预测，而每个位置都没有偷看它要预测的那个字。

```svg title="图 2：因果遮罩下的注意力权重（数值为示意）" tone="light"
<svg viewBox="0 0 640 260" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="因果遮罩">
<text x="240" y="28" font-size="12" fill="#504e49" text-anchor="middle">被看的位置（key）</text>
<text x="110" y="130" font-size="12" fill="#504e49" text-anchor="middle" transform="rotate(-90 110 130)">当前位置（query）</text>
<text x="138" y="60.0" font-size="13" fill="#141413" text-anchor="middle">我</text>
<text x="165.0" y="238" font-size="13" fill="#141413" text-anchor="middle">我</text>
<rect x="150" y="40" width="30" height="30" fill="#1B365D" fill-opacity="1.00" stroke="#fff"/>
<rect x="180" y="40" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="195.0" y="59.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<rect x="210" y="40" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="225.0" y="59.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<rect x="240" y="40" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="255.0" y="59.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<rect x="270" y="40" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="285.0" y="59.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<rect x="300" y="40" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="315.0" y="59.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<text x="138" y="90.0" font-size="13" fill="#141413" text-anchor="middle">爱</text>
<text x="195.0" y="238" font-size="13" fill="#141413" text-anchor="middle">爱</text>
<rect x="150" y="70" width="30" height="30" fill="#1B365D" fill-opacity="1.00" stroke="#fff"/>
<rect x="180" y="70" width="30" height="30" fill="#1B365D" fill-opacity="1.00" stroke="#fff"/>
<rect x="210" y="70" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="225.0" y="89.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<rect x="240" y="70" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="255.0" y="89.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<rect x="270" y="70" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="285.0" y="89.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<rect x="300" y="70" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="315.0" y="89.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<text x="138" y="120.0" font-size="13" fill="#141413" text-anchor="middle">学</text>
<text x="225.0" y="238" font-size="13" fill="#141413" text-anchor="middle">学</text>
<rect x="150" y="100" width="30" height="30" fill="#1B365D" fill-opacity="0.49" stroke="#fff"/>
<rect x="180" y="100" width="30" height="30" fill="#1B365D" fill-opacity="0.66" stroke="#fff"/>
<rect x="210" y="100" width="30" height="30" fill="#1B365D" fill-opacity="1.00" stroke="#fff"/>
<rect x="240" y="100" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="255.0" y="119.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<rect x="270" y="100" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="285.0" y="119.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<rect x="300" y="100" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="315.0" y="119.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<text x="138" y="150.0" font-size="13" fill="#141413" text-anchor="middle">大</text>
<text x="255.0" y="238" font-size="13" fill="#141413" text-anchor="middle">大</text>
<rect x="150" y="130" width="30" height="30" fill="#1B365D" fill-opacity="0.32" stroke="#fff"/>
<rect x="180" y="130" width="30" height="30" fill="#1B365D" fill-opacity="0.49" stroke="#fff"/>
<rect x="210" y="130" width="30" height="30" fill="#1B365D" fill-opacity="0.66" stroke="#fff"/>
<rect x="240" y="130" width="30" height="30" fill="#1B365D" fill-opacity="0.83" stroke="#fff"/>
<rect x="270" y="130" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="285.0" y="149.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<rect x="300" y="130" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="315.0" y="149.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<text x="138" y="180.0" font-size="13" fill="#141413" text-anchor="middle">模</text>
<text x="285.0" y="238" font-size="13" fill="#141413" text-anchor="middle">模</text>
<rect x="150" y="160" width="30" height="30" fill="#1B365D" fill-opacity="0.32" stroke="#fff"/>
<rect x="180" y="160" width="30" height="30" fill="#1B365D" fill-opacity="0.32" stroke="#fff"/>
<rect x="210" y="160" width="30" height="30" fill="#1B365D" fill-opacity="0.49" stroke="#fff"/>
<rect x="240" y="160" width="30" height="30" fill="#1B365D" fill-opacity="0.49" stroke="#fff"/>
<rect x="270" y="160" width="30" height="30" fill="#1B365D" fill-opacity="0.83" stroke="#fff"/>
<rect x="300" y="160" width="30" height="30" fill="#f0eee6" stroke="#fff"/>
<text x="315.0" y="179.0" font-size="10" fill="#6b6a64" text-anchor="middle">−∞</text>
<text x="138" y="210.0" font-size="13" fill="#141413" text-anchor="middle">型</text>
<text x="315.0" y="238" font-size="13" fill="#141413" text-anchor="middle">型</text>
<rect x="150" y="190" width="30" height="30" fill="#1B365D" fill-opacity="0.23" stroke="#fff"/>
<rect x="180" y="190" width="30" height="30" fill="#1B365D" fill-opacity="0.32" stroke="#fff"/>
<rect x="210" y="190" width="30" height="30" fill="#1B365D" fill-opacity="0.41" stroke="#fff"/>
<rect x="240" y="190" width="30" height="30" fill="#1B365D" fill-opacity="0.49" stroke="#fff"/>
<rect x="270" y="190" width="30" height="30" fill="#1B365D" fill-opacity="0.49" stroke="#fff"/>
<rect x="300" y="190" width="30" height="30" fill="#1B365D" fill-opacity="0.66" stroke="#fff"/>
<text x="380" y="70" font-size="13" fill="#141413" text-anchor="start">每一行是一个位置的注意力权重，和为 1</text>
<text x="380" y="96" font-size="12.5" fill="#504e49" text-anchor="start">• 颜色越深，权重越大</text>
<text x="380" y="120" font-size="12.5" fill="#504e49" text-anchor="start">• 右上角填 −∞，softmax 后变成 0：</text>
<text x="380" y="140" font-size="12.5" fill="#504e49" text-anchor="start">  不能偷看后面的字</text>
<text x="380" y="164" font-size="12.5" fill="#504e49" text-anchor="start">• 第 1 行只能看自己，最后一行能看全部</text>
<text x="380" y="188" font-size="12.5" fill="#504e49" text-anchor="start">• 训练时 6 个位置并行计算，</text>
<text x="380" y="208" font-size="12.5" fill="#504e49" text-anchor="start">  一次得到 6 个预测和 6 个 loss</text>
</svg>
```

### 权重从哪来：Query、Key、Value

分数不再是固定的，而是从数据里算出来的：每个位置用三个线性层各生成一个向量。Query 表示“我在找什么”，Key 表示“我能提供什么”，两者点积得到相关程度；Value 是“被选中时我实际交出去的内容”。权重是 `softmax(QKᵀ / √d)`，输出是权重对 V 的加权和。

右边是一个注意力头的完整实现。注意 `tril` 用 `register_buffer` 注册，它不是可训练参数，但会跟着模型一起移到 GPU 上。

```python title="Head（示意）"
class Head(nn.Module):
    def __init__(self, n_embd, head_size, block_size):
        super().__init__()
        self.key = nn.Linear(n_embd, head_size, bias=False)
        self.query = nn.Linear(n_embd, head_size, bias=False)
        self.value = nn.Linear(n_embd, head_size, bias=False)
        self.register_buffer("tril", torch.tril(torch.ones(block_size, block_size)))

    def forward(self, x):
        B, T, C = x.shape
        k, q, v = self.key(x), self.query(x), self.value(x)        # (B, T, hs)
        w = q @ k.transpose(-2, -1) * k.shape[-1] ** -0.5          # (B, T, T)
        w = w.masked_fill(self.tril[:T, :T] == 0, float("-inf"))   # 遮住未来
        w = F.softmax(w, dim=-1)
        return w @ v                                               # (B, T, hs)
```

- **为什么除以 √d**：Q、K 各维方差为 1 时，点积的方差约为 d；不缩放的话分数很大，softmax 接近 one-hot，梯度变得很小
- **这是 self-attention**：Q、K、V 都来自同一个 `x`；如果 K、V 来自另一段序列（比如编码器的输出），就是 cross-attention
- **注意力本身不知道顺序**：打乱输入顺序，每个位置得到的结果不变，所以 D3 要加位置编码

## D3 小 GPT：多头、FFN、残差，叠成 N 层

一个头只能学一种“关注方式”。**多头**让几个头并行，各自学不同的关系，比如有的头盯着前一个字，有的头盯着同一个单词的开头；输出拼接后再过一层线性投影。注意力负责在位置之间交换信息，**FFN** 负责在每个位置上单独加工这些信息：一个先放大到 4 倍宽度、再缩回来的两层 MLP。

把这两块叠深的关键是**残差**和 **LayerNorm**：每一块的输出加回到输入上（`x = x + f(x)`），梯度可以沿着这条“直通路”传回底层；LayerNorm 放在每块之前（pre-norm），让每层的输入保持稳定的尺度。图 3 右侧标出了 DeepSeek 替换的两个部件，D5 再展开。

```svg title="图 3：小 GPT 的结构，以及 DeepSeek 替换的两个部件" tone="light"
<svg viewBox="0 0 640 430" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="GPT 结构与 DeepSeek 的替换">
<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#504e49"/></marker></defs>
<rect x="150" y="16" width="200" height="40" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="250.0" y="41.0" text-anchor="middle" font-size="14" font-weight="600" fill="#141413">token 嵌入 + 位置编码</text>
<line x1="250.0" y1="57" x2="250.0" y2="82" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="120" y="84" width="260" height="250" rx="12" fill="none" stroke="#d8d5c8" stroke-width="1.5" stroke-dasharray="6 5"/>
<text x="128" y="104" font-size="12" fill="#504e49" text-anchor="start">× N 层</text>
<rect x="150" y="112" width="200" height="34" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="250.0" y="134.0" text-anchor="middle" font-size="14" font-weight="600" fill="#141413">LayerNorm</text>
<line x1="250.0" y1="147" x2="250.0" y2="160" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="150" y="162" width="200" height="44" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="250.0" y="181.0" text-anchor="middle" font-size="14" font-weight="600" fill="#141413">多头因果自注意力</text><text x="250.0" y="199.0" text-anchor="middle" font-size="11.5" fill="#504e49">D2 · D3</text>
<text x="360" y="224" font-size="12" fill="#504e49" text-anchor="start">+ 残差</text>
<line x1="250.0" y1="207" x2="250.0" y2="214" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="150" y="232" width="200" height="30" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="250.0" y="252.0" text-anchor="middle" font-size="14" font-weight="600" fill="#141413">LayerNorm</text>
<line x1="250.0" y1="263" x2="250.0" y2="270" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="150" y="272" width="200" height="40" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="250.0" y="289.0" text-anchor="middle" font-size="14" font-weight="600" fill="#141413">前馈网络 FFN</text><text x="250.0" y="307.0" text-anchor="middle" font-size="11.5" fill="#504e49">D3</text>
<text x="360" y="330" font-size="12" fill="#504e49" text-anchor="start">+ 残差</text>
<line x1="250.0" y1="335" x2="250.0" y2="344" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="150" y="346" width="200" height="34" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="250.0" y="368.0" text-anchor="middle" font-size="14" font-weight="600" fill="#141413">LayerNorm → 线性层</text>
<line x1="250.0" y1="381" x2="250.0" y2="394" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="150" y="396" width="200" height="30" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="250.0" y="416.0" text-anchor="middle" font-size="14" font-weight="600" fill="#141413">logits → 采样（D4）</text>
<rect x="440" y="158" width="180" height="52" rx="8" fill="#1B365D" stroke="#1B365D" stroke-width="1.2"/><text x="530.0" y="181.0" text-anchor="middle" font-size="14" font-weight="600" fill="#fff">MLA</text><text x="530.0" y="199.0" text-anchor="middle" font-size="11.5" fill="#dfe7f1">缓存低秩向量，KV 小得多</text>
<line x1="438" y1="184" x2="354" y2="184" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<rect x="440" y="266" width="180" height="52" rx="8" fill="#1B365D" stroke="#1B365D" stroke-width="1.2"/><text x="530.0" y="289.0" text-anchor="middle" font-size="14" font-weight="600" fill="#fff">MoE</text><text x="530.0" y="307.0" text-anchor="middle" font-size="11.5" fill="#dfe7f1">每个 token 只走几个专家</text>
<line x1="438" y1="292" x2="354" y2="292" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/>
<text x="530.0" y="140" font-size="12" fill="#504e49" text-anchor="middle">DeepSeek 替换成</text>
</svg>
```

### 代码：Block 和整个模型

右边把 D2 的 `Head` 组装起来。`MultiHead` 拼接多个头，`Block` 是“注意力 + FFN”加两条残差，`GPT` 再加上 token 嵌入、位置嵌入、N 个 Block 和输出层。`forward` 的签名和 bigram 一样，D1 的训练循环和 `generate` 可以直接复用。

```python title="gpt.py（示意，结构参照 nanoGPT）"
class MultiHead(nn.Module):
    def __init__(self, n_embd, n_head, block_size, dropout):
        super().__init__()
        hs = n_embd // n_head
        self.heads = nn.ModuleList([Head(n_embd, hs, block_size) for _ in range(n_head)])
        self.proj = nn.Linear(n_embd, n_embd)
        self.drop = nn.Dropout(dropout)

    def forward(self, x):
        return self.drop(self.proj(torch.cat([h(x) for h in self.heads], dim=-1)))

class Block(nn.Module):
    def __init__(self, n_embd, n_head, block_size, dropout):
        super().__init__()
        self.ln1, self.ln2 = nn.LayerNorm(n_embd), nn.LayerNorm(n_embd)
        self.attn = MultiHead(n_embd, n_head, block_size, dropout)
        self.ffn = nn.Sequential(
            nn.Linear(n_embd, 4 * n_embd), nn.GELU(),
            nn.Linear(4 * n_embd, n_embd), nn.Dropout(dropout),
        )

    def forward(self, x):
        x = x + self.attn(self.ln1(x))   # 残差 1
        x = x + self.ffn(self.ln2(x))    # 残差 2
        return x

class GPT(nn.Module):
    def __init__(self, vocab, n_embd=384, n_head=6, n_layer=6, block_size=256, dropout=0.2):
        super().__init__()
        self.tok = nn.Embedding(vocab, n_embd)
        self.pos = nn.Embedding(block_size, n_embd)
        self.blocks = nn.Sequential(*[Block(n_embd, n_head, block_size, dropout) for _ in range(n_layer)])
        self.ln_f = nn.LayerNorm(n_embd)
        self.head = nn.Linear(n_embd, vocab)

    def forward(self, idx, targets=None):
        B, T = idx.shape
        x = self.tok(idx) + self.pos(torch.arange(T, device=idx.device))
        logits = self.head(self.ln_f(self.blocks(x)))
        if targets is None:
            return logits, None
        return logits, F.cross_entropy(logits.view(B * T, -1), targets.view(B * T))
```

- **规模**：右边的默认配置约 1000 万参数，在 Tiny Shakespeare 上用一块 GPU 训练十几分钟，验证集 loss 能到 1.5 左右，生成的文字已经有台词的格式
- **建议逐步加**：先只加一个 Head，再换多头，再加 FFN，再加残差和 LayerNorm，每一步都记下验证集 loss，就能看清各个部件分别贡献了多少
- **训练时注意过拟合**：只看训练 loss 会被骗，每隔几百步在验证集上估一次 loss

## D4 推理：KV cache 省掉重复计算，采样决定挑哪个字

### KV cache：生成第 t 个字时，前面的 K、V 不用重算

D1 的 `generate` 每一步都把整段序列重新送进模型，前面每个位置的 K、V 被一遍遍重算。可因果遮罩保证了前面位置的 K、V 不会因为后面多了字而改变，所以可以把每层算过的 K、V 存起来，每一步只为新来的那个 token 算 Q、K、V，再和缓存拼在一起做注意力。生成 n 个 token 时，注意力里的 K、V 投影计算从约 n²/2 次降到 n 次。

代价是显存。每个 token 要在每一层为每个头存一份 K 和一份 V，KV cache 的大小是：`2 × 层数 × KV 头数 × 每头维度 × 每个数的字节数 × token 数`。上下文一长，它往往比模型权重还大，这也是 D5 里 MLA 要解决的问题。

```python title="带 KV cache 的单头注意力（示意）"
def attend_step(q_new, k_new, v_new, cache):
    """q_new/k_new/v_new: (B, 1, hs)，只是新 token 的那一行。"""
    if cache is not None:
        k = torch.cat([cache[0], k_new], dim=1)   # (B, t, hs)
        v = torch.cat([cache[1], v_new], dim=1)
    else:
        k, v = k_new, v_new
    w = F.softmax(q_new @ k.transpose(-2, -1) * k.shape[-1] ** -0.5, dim=-1)
    # 新 token 本来就只能看见过去，这里不需要遮罩
    return w @ v, (k, v)
```

- **预填充（prefill）和逐字解码（decode）**：第一步把整段 prompt 一次算完并填满缓存，这一步决定首 token 延迟（TTFT，D9 会测）；之后每步只算一个 token
- **服务端的前缀缓存就是把这份 KV cache 跨请求保留下来**：下一个请求的开头和上一次完全一样时，可以直接复用，D9 的缓存命中测的就是它

### 采样：temperature、top-k、top-p 各管什么

拿到最后一个位置的 logits 之后，怎么选下一个 token 由采样策略决定。**temperature** 先把 logits 除以 T：T < 1 时分布更尖，结果更确定；T > 1 时更平，结果更多样；T → 0 等价于每次取最大值（贪心）。**top-k** 只保留分数最高的 k 个候选；**top-p**（nucleus）按概率从高到低累加，保留累计概率刚达到 p 的那一小组候选，候选数量会随分布的尖锐程度自动变化。

```python title="sample（示意）"
def sample(logits, temperature=1.0, top_k=None, top_p=None):
    if temperature == 0:
        return logits.argmax(dim=-1, keepdim=True)          # 贪心
    logits = logits / temperature
    if top_k is not None:
        kth = torch.topk(logits, top_k).values[..., -1, None]
        logits = logits.masked_fill(logits < kth, float("-inf"))
    if top_p is not None:
        sorted_logits, idx = torch.sort(logits, descending=True)
        cum = F.softmax(sorted_logits, dim=-1).cumsum(dim=-1)
        drop = cum - F.softmax(sorted_logits, dim=-1) > top_p  # 保留刚好越过 p 的那个
        sorted_logits = sorted_logits.masked_fill(drop, float("-inf"))
        logits = torch.full_like(logits, float("-inf")).scatter(-1, idx, sorted_logits)
    return torch.multinomial(F.softmax(logits, dim=-1), num_samples=1)
```

- **API 里的对应参数**：DeepSeek 的 thinking 模式下 `temperature` 不生效（设置了也不报错）；`deepseek-flash` 的 `top_p` 在 thinking 模式下只能取 0.95–1.0
- **重复惩罚**：`frequency_penalty` / `presence_penalty` 在 DeepSeek 的 API 参考里已标为 deprecated，thinking 模式下同样不生效

## D5 DeepSeek 改了哪里：FFN 换成 MoE，注意力换成 MLA

### MoE：参数很多，但每个 token 只用一小部分

D3 里每个 token 都要经过同一个 FFN。MoE 把这一个 FFN 换成很多个小 FFN（“专家”），再加一个路由器：路由器给每个专家打分，每个 token 只送进分数最高的 k 个专家，输出按分数加权求和。总参数可以很大，每个 token 的计算量只取决于被激活的那几个专家。

DeepSeekMoE 在此基础上做了两点改动：把专家切得更细、数量更多，让每个专家更专门；另外设少量**共享专家**，所有 token 都经过，用来存放通用知识，避免每个路由专家都重复学一遍。V3 还去掉了用来平衡负载的辅助损失，改成给每个专家的路由分数加一个偏置项：哪个专家负载过高，就把它的偏置调低，负载过低就调高。

```python title="玩具 MoE 层（示意，未包含负载均衡）"
class MoE(nn.Module):
    def __init__(self, n_embd, n_experts=8, k=2, n_shared=1):
        super().__init__()
        ffn = lambda: nn.Sequential(nn.Linear(n_embd, 2 * n_embd), nn.GELU(), nn.Linear(2 * n_embd, n_embd))
        self.experts = nn.ModuleList([ffn() for _ in range(n_experts)])
        self.shared = nn.ModuleList([ffn() for _ in range(n_shared)])
        self.router = nn.Linear(n_embd, n_experts, bias=False)
        self.k = k

    def forward(self, x):                        # x: (N, C)，先把 B、T 摊平
        scores = self.router(x).softmax(dim=-1)  # (N, E)
        w, idx = scores.topk(self.k, dim=-1)     # 每个 token 选 k 个专家
        out = sum(s(x) for s in self.shared)
        for e, expert in enumerate(self.experts):
            hit = (idx == e)                     # (N, k)
            rows = hit.any(dim=-1).nonzero(as_tuple=True)[0]
            if rows.numel():
                gate = (w * hit).sum(dim=-1)[rows, None]
                out = out.index_add(0, rows, gate * expert(x[rows]))
        return out
```

- **V3 的规模**：671B 总参数，每个 token 激活 37B；每个 MoE 层有 256 个路由专家和 1 个共享专家，每个 token 选 8 个路由专家
- **动手观察负载不均**：用右边的玩具层训练几百步，统计每个专家收到的 token 数，通常会有少数专家被挤爆、另一些几乎闲置，这就是负载均衡要解决的问题

### MLA：缓存一个压缩向量，而不是每个头的 K 和 V

D4 算过，KV cache 和“头数 × 每头维度”成正比。MLA 的做法是：每个 token 先投影出一个低维的潜在向量 c 并缓存它，计算注意力时再用上投影矩阵从 c 恢复出各个头的 K、V。推理时上投影还可以合并进 Q 和输出投影，所以不用真的把 K、V 展开。RoPE 位置编码没法这样合并，MLA 单独留了一小段带 RoPE 的维度，也一起缓存。

下表按 V3 公开 config 估算每个 token 每层要缓存多少个数：128 个头、每头 128 维、潜在向量 512 维、RoPE 部分 64 维，共 61 层，按 BF16（每个数 2 字节）计。MHA 和 GQA 两行是假设用同样头数和维度的对照，不是 DeepSeek 真实用过的配置。

| 注意力方式 | 每 token 每层缓存的数 | 61 层合计（BF16） | 128K 上下文 |
| --- | --- | --- | --- |
| MHA：每头各存 K、V | 2 × 128 × 128 = 32,768 | 约 3.8 MB | 约 488 GiB |
| GQA（8 组）：8 组共享 K、V | 2 × 8 × 128 = 2,048 | 约 244 KB | 约 30.5 GiB |
| MLA：潜在向量 + RoPE 部分 | 512 + 64 = 576 | 约 69 KB | 约 8.6 GiB |

> MLA 缓存的数只有同样配置 MHA 的约 1/57。省下的显存可以换成更长的上下文或更大的批量。DeepSeek 后来的 NSA、DSA 和 V4 的 token 级压缩，则是在“每个 token 要看多少个历史 token”这个方向上继续省，详见本专题的《学习地图》。

## D6 API 的输出：JSON 模式和流式

### 结构化输出：`json_object` 只保证是合法 JSON，不保证字段对

把 `response_format` 设为 `{"type": "json_object"}`，模型输出的就是合法 JSON。官方文档有三条要求：system 或 user 消息里必须出现 “json” 这个词，并给一个期望格式的示例；`max_tokens` 要设够，否则 JSON 会在中途被截断（此时 `finish_reason` 为 `length`）；`content` 偶尔会只有空格，即使 `finish_reason` 是 `stop`，代码要能处理这种情况，例如重试。

JSON 模式不检查字段和类型，所以拿到结果后还要用 schema 校验一遍。需要服务端强制遵守 schema 时，可以改用 D7 里工具调用的 strict 模式。

```python title="JSON 输出 + 本地校验（示意）"
import json
from openai import OpenAI
from pydantic import BaseModel, ValidationError

client = OpenAI(api_key="...", base_url="https://api.deepseek.com")

class Paper(BaseModel):
    title: str
    year: int
    keywords: list[str]

SYSTEM = '从用户给的文字中抽取论文信息，输出 json，例如：{"title": "...", "year": 2024, "keywords": ["..."]}'

def extract(text, retries=2):
    for _ in range(retries + 1):
        r = client.chat.completions.create(
            model="deepseek-flash",
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
            response_format={"type": "json_object"},
            max_tokens=1024,
            extra_body={"thinking": {"type": "disabled"}},
        )
        content = r.choices[0].message.content or ""
        if not content.strip() or r.choices[0].finish_reason == "length":
            continue                                   # 空内容或被截断：重试
        try:
            return Paper.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError):
            continue                                   # 字段不对：重试
    raise RuntimeError("结构化输出失败")
```

### 流式：思考、回答、工具调用分别从三个字段流出来

设 `stream=True` 后，响应按 SSE 一块块返回，每块的 `delta` 里可能有 `reasoning_content`（思考）、`content`（回答）或 `tool_calls`（工具调用的片段）。工具调用的参数是一段段拼出来的，要按 `index` 把同一个调用的 `function.arguments` 字符串拼完整，结束后再解析成 JSON。

加上 `stream_options={"include_usage": True}`，每块都会带 `usage` 字段，但只有最后一块有值，前面都是 `null`。`stream_options` 必须和 `stream: true` 一起用，否则返回 400。

```python title="流式读取（示意）"
def stream_chat(messages, tools=None):
    reasoning, answer, calls, usage = [], [], {}, None
    stream = client.chat.completions.create(
        model="deepseek-flash", messages=messages, tools=tools,
        stream=True, stream_options={"include_usage": True},
    )
    for chunk in stream:
        if chunk.usage:
            usage = chunk.usage                        # 只有最后一块非空
        if not chunk.choices:
            continue
        d = chunk.choices[0].delta
        if getattr(d, "reasoning_content", None):
            reasoning.append(d.reasoning_content)
        if d.content:
            answer.append(d.content)
        for tc in d.tool_calls or []:                  # 按 index 拼接参数片段
            c = calls.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
            c["id"] = tc.id or c["id"]
            if tc.function and tc.function.name:
                c["name"] += tc.function.name
            if tc.function and tc.function.arguments:
                c["arguments"] += tc.function.arguments
    return "".join(reasoning), "".join(answer), list(calls.values()), usage
```

- **`reasoning_content` 是 DeepSeek 的扩展字段**：OpenAI SDK 的类型定义里没有它，所以用 `getattr` 读
- **服务繁忙时连接不会断**：流式请求会持续收到 `: keep-alive` 注释行，非流式请求会收到空行；SDK 会跳过这些，但自己解析 SSE 时要处理
- **超时**：请求 10 分钟内还没开始推理，服务端会关闭连接

## D7 工具调用：一个循环，直到模型不再要工具

工具调用的流程是一个循环（图 4）：把 `messages` 和 `tools` 发出去；如果 `finish_reason` 是 `tool_calls`，就从 `message.tool_calls` 里取出每个调用的 `id`、函数名和参数，执行后追加结果，再发下一次请求；直到 `finish_reason` 变成 `stop`。

追加消息的顺序有讲究：先把模型返回的 assistant 消息**原样**追加（包括 `tool_calls` 和 `reasoning_content`），再为每个调用追加一条 `role: "tool"` 的消息，用 `tool_call_id` 对应回去。DeepSeek 的 thinking 文档写得很明确：请求带了 `tools` 时，之前所有轮次的 `reasoning_content` 都必须完整回传，即使那一轮没有调用工具，否则返回 400。

```svg title="图 4：多轮工具调用循环" tone="light"
<svg viewBox="0 0 640 350" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="多轮工具调用循环">
<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#504e49"/></marker></defs>
<rect x="20" y="14" width="140" height="36" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="90.0" y="37.0" text-anchor="middle" font-size="14" font-weight="600" fill="#141413">你的代码</text>
<line x1="90" y1="52" x2="90" y2="340" stroke="#d8d5c8" stroke-width="1.5" stroke-dasharray="4 4"/>
<rect x="250" y="14" width="140" height="36" rx="8" fill="#1B365D" stroke="#1B365D" stroke-width="1.2"/><text x="320.0" y="37.0" text-anchor="middle" font-size="14" font-weight="600" fill="#fff">DeepSeek API</text>
<line x1="320" y1="52" x2="320" y2="340" stroke="#d8d5c8" stroke-width="1.5" stroke-dasharray="4 4"/>
<rect x="480" y="14" width="140" height="36" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="550.0" y="37.0" text-anchor="middle" font-size="14" font-weight="600" fill="#141413">工具函数</text>
<line x1="550" y1="52" x2="550" y2="340" stroke="#d8d5c8" stroke-width="1.5" stroke-dasharray="4 4"/>
<line x1="92" y1="82" x2="316" y2="82" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/><text x="204.0" y="75" font-size="12" fill="#141413" text-anchor="middle">messages + tools</text>
<line x1="318" y1="124" x2="94" y2="124" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/><text x="206.0" y="117" font-size="12" fill="#141413" text-anchor="middle">finish_reason = tool_calls</text><text x="206.0" y="140" font-size="11" fill="#504e49" text-anchor="middle">tool_calls[]：id · name · arguments</text>
<line x1="92" y1="172" x2="546" y2="172" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/><text x="319.0" y="165" font-size="12" fill="#141413" text-anchor="middle">校验 arguments 后执行</text>
<line x1="548" y1="206" x2="94" y2="206" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/><text x="321.0" y="199" font-size="12" fill="#141413" text-anchor="middle">结果</text>
<text x="100" y="234" font-size="11.5" fill="#504e49" text-anchor="start">追加 assistant 原消息（含 reasoning_content）</text>
<text x="100" y="252" font-size="11.5" fill="#504e49" text-anchor="start">再追加 role=tool、tool_call_id 对应的结果</text>
<line x1="92" y1="292" x2="316" y2="292" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/><text x="204.0" y="285" font-size="12" fill="#141413" text-anchor="middle">再次请求（前缀不变，只在末尾追加）</text>
<line x1="318" y1="326" x2="94" y2="326" stroke="#504e49" stroke-width="1.4" marker-end="url(#ar)"/><text x="206.0" y="319" font-size="12" fill="#141413" text-anchor="middle">stop → 结束；tool_calls → 回到第 2 步</text>
</svg>
```

### 代码：带上限的工具循环

右边的循环有三处保护：参数先解析和校验，解析失败就把错误作为工具结果返回给模型，让它自己修正，而不是让程序崩溃；未知的工具名同样作为错误返回；最多循环 `max_steps` 次，防止模型反复调用。

```python title="agent_loop.py（示意）"
TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询城市的当前天气",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}},
            "required": ["city"],
        },
    },
}]
IMPL = {"get_weather": lambda city: f"{city}：24℃，晴"}

def run(messages, max_steps=8):
    for _ in range(max_steps):
        r = client.chat.completions.create(model="deepseek-flash", messages=messages, tools=TOOLS)
        msg = r.choices[0].message
        messages.append(msg)                                   # 原样追加，含 reasoning_content
        if r.choices[0].finish_reason != "tool_calls":
            return msg.content
        for call in msg.tool_calls:
            try:
                args = json.loads(call.function.arguments)
                fn = IMPL[call.function.name]
                result = fn(**args)
            except KeyError:
                result = f"错误：没有名为 {call.function.name} 的工具"
            except (json.JSONDecodeError, TypeError) as e:
                result = f"错误：参数无效（{e}），请按 schema 重新调用"
            messages.append({"role": "tool", "tool_call_id": call.id, "content": str(result)})
    raise RuntimeError("超过最大步数")
```

- **参数不一定是合法 JSON**：API 参考明确说模型生成的 `arguments` 可能无效，执行前必须校验
- **strict 模式（Beta）**：`base_url` 改成 `https://api.deepseek.com/beta`，每个函数设 `"strict": true`，schema 里每个对象都要把所有属性列进 `required` 并设 `"additionalProperties": false`；不支持字符串的 `minLength` / `maxLength` 和数组的 `minItems` / `maxItems`，schema 不合规时服务端直接报错
- **thinking 模式下的限制**：`tool_choice` 设成 `required` 或指定某个函数会返回 400，只能让模型自己决定
- **Chat Completions 格式不支持在对话中途插入工具调用**：需要这样做时，要改用 Anthropic 或 Responses 格式的接口

## D8 重试、限流和成本：哪些错误能重试，钱花在哪

### 重试：只重试服务端的临时错误，并限制并发

DeepSeek 的限流按**并发数**计算，按账号计、不按 key 计：`deepseek-flash` 是 2500，`deepseek-v4-pro` 是 500。一个请求从发出到响应完成都算占用一个并发，超过上限返回 429。所以除了退避重试，更重要的是在客户端用信号量把并发控制在上限以内。

可以重试的是服务端的临时错误（500、503）、网关超时（504）和网络错误；429 要先降低并发再重试。400、401、402、422 是请求本身的问题，重试多少次结果都一样，应该直接报错。

```python title="retry.py（示意）"
import random, threading, time
import openai

RETRYABLE = {429, 500, 503, 504}
slots = threading.Semaphore(64)          # 客户端并发上限，远低于账号上限

def call_with_retry(fn, max_attempts=5, base=1.0, cap=30.0):
    for attempt in range(max_attempts):
        try:
            with slots:
                return fn()
        except openai.APIStatusError as e:
            if e.status_code not in RETRYABLE or attempt == max_attempts - 1:
                raise                        # 400/401/402/422：重试没用
        except (openai.APIConnectionError, openai.APITimeoutError):
            if attempt == max_attempts - 1:
                raise
        time.sleep(random.uniform(0, min(cap, base * 2 ** attempt)))  # 指数退避 + 全抖动
```

| 状态码 | 含义 | 处理 |
| --- | --- | --- |
| 400 / 422 | 请求格式或参数错误（如带工具时漏传 `reasoning_content`） | 改请求，不重试 |
| 401 | API key 错误 | 检查 key，不重试 |
| 402 | 余额不足 | 充值，不重试 |
| 429 | 超过并发上限 | 降低并发，退避后重试 |
| 500 / 503 | 服务端错误 / 过载 | 稍等后重试 |
| 504 | 网关超时 | 稍后重试 |

### 成本：按“缓存命中输入、未命中输入、输出”三档记账

定价页把 token 分成三类分别计价：命中缓存的输入、未命中缓存的输入、输出；高峰和低谷时段价格不同，低谷是高峰的一半。所以统计成本不能只看 `prompt_tokens`，要从 `usage` 里分别取 `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens`。思考 token 记在 `completion_tokens_details.reasoning_tokens` 里，单独记录它，可以看出 thinking 占了多少输出。

```python title="usage 统计（示意，价格从定价页填入）"
from dataclasses import dataclass

PRICE_PER_M = {                     # 元或美元 / 百万 token，按定价页和时段填写
    "deepseek-flash": {"hit": None, "miss": None, "out": None},
}

@dataclass
class Ledger:
    hit: int = 0
    miss: int = 0
    out: int = 0
    reasoning: int = 0

    def add(self, usage):
        self.hit += usage.prompt_cache_hit_tokens
        self.miss += usage.prompt_cache_miss_tokens
        self.out += usage.completion_tokens
        details = usage.completion_tokens_details
        self.reasoning += (details.reasoning_tokens or 0) if details else 0

    def cost(self, model):
        p = PRICE_PER_M[model]
        return (self.hit * p["hit"] + self.miss * p["miss"] + self.out * p["out"]) / 1e6

    @property
    def hit_rate(self):
        return self.hit / max(1, self.hit + self.miss)
```

- **`prompt_tokens` = 命中 + 未命中**：`prompt_tokens_details.cached_tokens` 和 `prompt_cache_hit_tokens` 是同一个数
- **按会话累计，也按轮记录**：总命中率会掩盖中途某一轮缓存突然失效，逐轮记录才能看出是哪一轮出的问题

## D9 thinking、前缀缓存和 TTFT：三个指标一起测

### thinking 对比：同一批题目，在每个档位上都跑一遍

thinking 的开关和深度都在同一个模型上设置：`thinking: {"type": "enabled" | "disabled"}`，`reasoning_effort` 取 `low` / `high` / `max`，两个都不传时默认开启、档位为 `high`。对比的方法是固定一批有标准答案的题目，在每个档位上各跑几次，记录正确率、输出 token（其中多少是思考）和耗时，画成“成本—正确率”曲线，选拐点处的档位。

```python title="档位对比（示意）"
SETTINGS = {
    "off":  {"extra_body": {"thinking": {"type": "disabled"}}},
    "low":  {"reasoning_effort": "low"},
    "high": {"reasoning_effort": "high"},
    "max":  {"reasoning_effort": "max"},
}

def evaluate(problems, runs=3):
    report = {}
    for name, kw in SETTINGS.items():
        ok, ledger, secs = 0, Ledger(), 0.0
        for p in problems:
            for _ in range(runs):
                t0 = time.perf_counter()
                r = client.chat.completions.create(
                    model="deepseek-flash", messages=[{"role": "user", "content": p.prompt}], **kw)
                secs += time.perf_counter() - t0
                ledger.add(r.usage)
                ok += p.check(r.choices[0].message.content)
        n = len(problems) * runs
        report[name] = {"acc": ok / n, "out_tok": ledger.out / n,
                        "reasoning_tok": ledger.reasoning / n, "sec": secs / n}
    return report
```

- **每档跑多次**：thinking 输出波动大，只跑一次的正确率不可信
- **`max` 档的默认 `max_tokens` 更大**：thinking 模式默认 64K，`max` 档 128K，非 thinking 是 8K；比较时要么统一设置，要么记下被截断的次数
- **在 agent 任务上也测一遍**：单轮题目上的最优档位，放到多步工具任务里不一定还是最优

### 前缀缓存：发两次同样开头的请求，看第二次命中多少

DeepSeek 的上下文缓存默认开启，按前缀匹配：后一个请求只有在开头与某个已缓存的前缀单元**完全一致**时才能命中。缓存单元在请求边界（用户输入结束、模型输出结束）以及长输入里固定的 token 间隔处生成。缓存是尽力而为的，不保证 100% 命中；不再使用后通常在几小时到几天内清除。

测法很简单：构造一段足够长的固定前缀，连续发两次只有末尾不同的请求，比较第二次的 `prompt_cache_hit_tokens`。在 agent 里，则是逐轮记录命中数，检查每一轮是否都命中了上一轮的全部前缀。

```python title="缓存命中自检（示意）"
LONG_SYSTEM = open("system_prompt.md").read()   # 几千 token 的固定内容

def ask(question):
    r = client.chat.completions.create(
        model="deepseek-flash",
        messages=[{"role": "system", "content": LONG_SYSTEM},
                  {"role": "user", "content": question}],
        extra_body={"thinking": {"type": "disabled"}},
    )
    u = r.usage
    return u.prompt_cache_hit_tokens, u.prompt_cache_miss_tokens

print(ask("第一个问题"))   # 期望：hit ≈ 0（除非之前跑过）
print(ask("第二个问题"))   # 期望：hit 接近 system 的长度
```

- **破坏缓存的常见原因**：system 里放了时间戳、工具列表顺序变化、修改或删掉了历史消息、带工具时漏传 `reasoning_content`
- **`user_id` 参数**：官方说明它用于 KV cache、安全和调度隔离；多租户场景下给不同用户设不同的值，同一用户的请求保持一致

### TTFT：分开测“第一块数据”和“第一个回答字”

开启 thinking 后，首 token 延迟要分成两个数：**首块延迟**是从发出请求到收到第一块数据的时间，主要由排队和预填充决定，前缀缓存命中会让它明显变短；**首个可见字延迟**是到第一个 `content` 字符的时间，中间还隔着整段思考。agent 的用户感受到的是后者，调缓存效果要看前者。

```python title="TTFT 测量（示意）"
def measure(messages, **kw):
    t0 = time.perf_counter()
    first_chunk = first_reasoning = first_content = hit = None
    stream = client.chat.completions.create(
        model="deepseek-flash", messages=messages, stream=True,
        stream_options={"include_usage": True}, **kw)
    for chunk in stream:
        now = time.perf_counter() - t0
        first_chunk = first_chunk or now
        if chunk.choices:
            d = chunk.choices[0].delta
            if getattr(d, "reasoning_content", None) and first_reasoning is None:
                first_reasoning = now
            if d.content and first_content is None:
                first_content = now
        if chunk.usage:
            hit = chunk.usage.prompt_cache_hit_tokens
    return {"first_chunk": first_chunk, "first_reasoning": first_reasoning,
            "first_content": first_content, "total": time.perf_counter() - t0, "cache_hit": hit}
```

- **把 TTFT 和缓存命中放在同一行记录**：这样能直接看出命中多少 token 换来了多少毫秒
- **多跑几次取分位数**：TTFT 受服务端负载影响很大，看 p50 和 p90，不要看单次
- **高峰和低谷分开测**：DeepSeek 分时段定价，负载也随时段变化

> D9 的三件事在本仓库根目录各有一篇展开的文章：《各家模型的 thinking 模式》对比了 DeepSeek 和其他厂商的 thinking 参数与回传规则，《Agent 的前缀缓存怎么测》讲了 Codex、opencode 等开源 agent 怎么做缓存测试，可以接着读。
