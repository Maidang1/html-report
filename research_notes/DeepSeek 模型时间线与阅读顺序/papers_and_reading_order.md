# DeepSeek papers: technical lines and reading order (as of 2026-11-23)

Scope: the ideas in DeepSeek's papers and how to study them. Release timeline and API details are covered by another researcher.

Verification legend:
- **[verified]**: checked in this session against the arXiv abstract or HTML page, a Hugging Face model card, or ACL/Nature pages.
- **[memory]**: taken from well-established knowledge of the paper. The arXiv ID is given, but the specific number or detail was not re-fetched in this session. The writer should spot-check these before publishing.

---

## Q1. Architecture / efficiency line

### Takeaway
This line is one continuous effort to cut the cost per token of training and inference. It runs DeepSeekMoE (2024-01), then V2 with MLA (2024-05), then V3 with aux-loss-free balancing, MTP, FP8 and DualPipe (2024-12), then NSA (2025-02), DSA in V3.2 (2025-09/12), V4's CSA+HCA hybrid attention plus mHC plus Muon (2026-04), and V4.1-Flash's causal encoder–decoder with CSA2 and FP4 KV (2026-09). Two problems drive the whole line: "too many FLOPs per token" (MoE, later Engram) and "too much KV cache / attention cost at long context" (MLA, then NSA/DSA, then CSA/HCA, then CSA2).

### Cited Findings

**DeepSeekMoE: arXiv 2401.06066 (2024-01-11) [memory]** — [arXiv](https://arxiv.org/abs/2401.06066)
- Problem: in conventional top-k MoE (GShard/Switch), each expert ends up learning a mix of knowledge ("knowledge hybridity"), and several experts relearn the same common knowledge ("redundancy").
- Core idea, part 1 (fine-grained expert segmentation): split each of N experts into m smaller ones and activate mK of the mN. Total and active compute stay the same, but the number of possible expert combinations grows enormously.
- Core idea, part 2 (shared expert isolation): K_s experts are always active and absorb common knowledge, so the routed experts can specialise.
- What to look at: the MoE layer output equation (residual + Σ shared FFN + Σ gate·routed FFN) and the figure comparing conventional top-2, fine-grained and fine-grained+shared.
- Prerequisites: FFN in a Transformer, top-k gating, and a load-balancing auxiliary loss (Shazeer 2017, arXiv 1701.06538; GShard 2006.16668; Switch 2101.03961).

**DeepSeek-V2: arXiv 2405.04434 (2024-05) [memory]** — [arXiv](https://arxiv.org/abs/2405.04434)
- Size: 236B total parameters, 21B active, 128K context.
- Problem: the KV cache limits inference batch size and long context. MQA and GQA shrink the cache but cost quality.
- Core idea (Multi-head Latent Attention, MLA): compress K and V jointly into a low-rank latent c_KV and cache only that latent. At inference the up-projection matrices can be absorbed into the Q and O projections. RoPE does not commute with the absorption, so a small separate "decoupled RoPE" key is carried alongside the latent.
- Reported vs DeepSeek 67B: KV cache down 93.3%, training cost down 42.5%, max generation throughput 5.76×.
- MoE side: DeepSeekMoE is used at scale, with device-limited routing and expert-, device- and communication-level balance losses.
- What to look at: the MLA figure comparing MHA/GQA/MQA/MLA, the equations for c_KV and the decoupled RoPE key, and the appendix showing why RoPE breaks matrix absorption.
- Prerequisites: MHA, MQA (1911.02150), GQA (2305.13245), RoPE (2104.09864), how the KV cache works.

**DeepSeek-V3: arXiv 2412.19437 (2024-12-27) [verified date via HF card; details memory]** — [arXiv](https://arxiv.org/abs/2412.19437)
- Size and cost: 671B total, 37B active, 14.8T tokens. Training took 2.788M H800 GPU-hours, which the report prices at ≈ $5.576M at $2/GPU-hour, explicitly excluding prior research and ablations. This "$5.6M" figure is often quoted out of context.
- Auxiliary-loss-free load balancing:
  - Problem: an auxiliary balance loss hurts model quality.
  - Idea: add a per-expert bias b_i to the affinity scores, used only for top-k selection and not for the gating weights. After each step, decrease the bias of overloaded experts and increase it for underloaded ones.
  - A tiny sequence-wise balance loss is kept as a safeguard.
- Multi-Token Prediction (MTP):
  - Sequential MTP modules (depth D=1 in V3) predict the token after next while keeping the causal chain. This differs from the parallel heads in Gloeckle et al., arXiv 2404.19737.
  - Purpose: a denser training signal. At inference the module can be dropped or reused for speculative decoding.
- FP8 mixed-precision training: fine-grained quantization (1×128 tiles for activations, 128×128 blocks for weights) and increased-precision accumulation (promoting to FP32 on CUDA cores), validated at very large scale.
- DualPipe: a bidirectional pipeline schedule that overlaps forward/backward compute with MoE all-to-all communication, plus custom cross-node all-to-all kernels.
- What to look at: Fig. 2 (architecture: MLA + DeepSeekMoE), the bias-adjusted top-k equation, Fig. 3 (MTP), the FP8 figures (mixed-precision framework, fine-grained quantization), the DualPipe schedule figure, and the cost table.
- Prerequisites: V2 (MLA, DeepSeekMoE); pipeline parallelism (1F1B, ZeRO-bubble) for DualPipe; FP8 formats (E4M3/E5M2, arXiv 2209.05433).

**Companion paper: "Insights into DeepSeek-V3: Scaling Challenges and Reflections on Hardware for AI Architectures", arXiv 2505.09343 (2025-05-14; ISCA '25 Industry Track) [verified]** — [arXiv](https://arxiv.org/abs/2505.09343)
- Covers hardware-aware co-design on 2,048 H800s: MLA for memory, MoE for the compute/communication trade-off, FP8, a multi-plane network topology, and requests to hardware vendors.
- This is the best short "why" paper for the V3 architecture.

**Native Sparse Attention (NSA): arXiv 2502.11089 (2025-02-16) [memory for details; award verified]** — [arXiv](https://arxiv.org/abs/2502.11089), [ACL Anthology 2025.acl-long.1126](https://aclanthology.org/2025.acl-long.1126)
- Problem: earlier sparse-attention methods either are not trainable end to end (inference-only heuristics) or do not give real wall-clock speedups on GPUs.
- Core idea: each query attends through three gated branches:
  - compressed coarse-grained tokens (block summaries);
  - selected fine-grained blocks (top-n blocks chosen using the compression-branch scores);
  - a sliding window for local context.
- Design: blockwise selection is aligned with GPU memory access and GQA groups, which gives real kernel speedups. It is "natively trainable", meaning the model is pretrained with sparsity from the start.
- Award: Best Paper at ACL 2025 (Vienna). Co-authors are from DeepSeek, Peking University and the University of Washington, and include Liang Wenfeng — [SCMP](https://www.scmp.com/tech/big-tech/article/3320255/deepseek-founder-shares-best-paper-award-top-global-ai-research-conference).
- What to look at: the three-branch architecture figure and the kernel design figure.
- Prerequisites: FlashAttention-style tiling, GQA, sliding-window attention.

**DeepSeek Sparse Attention (DSA) in V3.2: arXiv 2512.02556 (2025-12-02) [verified]; first shipped in V3.2-Exp** — [arXiv](https://arxiv.org/abs/2512.02556), [V3.2-Exp repo](https://github.com/deepseek-ai/DeepSeek-V3.2-Exp)
- DSA is the only architectural change from V3.1-Terminus, introduced by continued training.
- Two components: a **lightning indexer** (few heads, ReLU, runs in FP8, scores every preceding token cheaply) and **fine-grained token selection** (each query attends to the top-k tokens only) — [arXiv PDF](https://arxiv.org/pdf/2512.02556).
- Relation to NSA: NSA selects blocks with three branches. DSA selects individual tokens via a separate learned indexer and is retrofitted onto an existing dense MLA model instead of trained natively. [inference]
- No standalone DSA arXiv paper exists. The V3.2-Exp repo cites it as "Boosting Long-Context Efficiency with DeepSeek Sparse Attention" — [V3.2-Exp repo](https://github.com/deepseek-ai/DeepSeek-V3.2-Exp).
- [memory] Two training details: top-k = 2048 tokens, and a dense warm-up stage that trains only the indexer (KL to the dense attention distribution) before sparse training. Verify these in the PDF.
- V3.2's other contributions belong to the training line (see Q2).
- What to look at: the DSA architecture figure (indexer, then top-k, then MLA in MQA mode) and the cost-vs-position plots.
- Prerequisites: MLA (V2), NSA (for contrast).

**mHC: Manifold-Constrained Hyper-Connections, arXiv 2512.24880 (late Dec 2025, revised Jan 2026; PMLR vol. 306) [verified]** — [arXiv HTML](https://arxiv.org/html/2512.24880v2), [PMLR](https://proceedings.mlr.press/v306/xie26aa.html)
- Problem: Hyper-Connections (HC) widen the residual stream into n parallel streams with learnable mixing, but lose the identity-mapping property, which makes training unstable at scale.
- Core idea: project the residual mixing matrices onto the manifold of doubly-stochastic matrices (the Birkhoff polytope) using Sinkhorn-Knopp. This restores identity-like signal propagation while keeping HC's gains, and comes with infrastructure optimisations.
- Overhead: about 6.7% training-time overhead at n=4, per a secondary source — [Raschka gallery](https://www.sebastianraschka.com/llm-architecture-gallery/mhc/) (secondary).
- Adoption: used in V4 with 4 residual streams.
- Prerequisites: residual connections and Pre-LN, and the original Hyper-Connections paper (arXiv 2409.19606, ByteDance) [memory].

**Engram: "Conditional Memory via Scalable Lookup: A New Axis of Sparsity for LLMs", arXiv 2601.07372 (2026-01-12; DeepSeek-AI + PKU) [verified via search snippet of abs page]** — [arXiv](https://arxiv.org/abs/2601.07372v1)
- Core idea: MoE is "conditional computation". Engram adds "conditional memory", a modernised N-gram embedding table with O(1) deterministic lookup.
- Because addressing is deterministic, tables can be prefetched from host memory. Offloading a 100B-parameter table costs less than 3% overhead.
- Scaled to 27B parameters, Engram beats an iso-parameter, iso-FLOP MoE baseline.
- The paper reports a U-shaped scaling law for splitting a parameter budget between experts and Engram memory.
- Code is at github.com/deepseek-ai/Engram.
- Adoption: not in the V4 abstract. The V4.1-Flash report lists "Engram conditional memory" as added — [V4.1-Flash HTML](https://arxiv.org/html/2609.19969v1).

**DeepSeek-V4: "Towards Highly Efficient Million-Token Context Intelligence", arXiv 2606.19348 [verified]** — [arXiv abs](https://arxiv.org/abs/2606.19348), [HTML](https://arxiv.org/html/2606.19348v1), [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro)
- **Date flag:** the arXiv abs page says "Submitted on 26 Apr 2026", and the HF paper page says "Published Apr 26". However, an ID beginning 2606 normally means June 2026. Cite it as "technical report dated 2026-04-26 (arXiv 2606.19348)" and let the timeline researcher settle the release date.
- Models: V4-Pro has 1.6T total / 49B active parameters, and V4-Flash has 284B / 13B. Both have 1M context, were pretrained on more than 32T tokens, and are MIT licensed.
- Problem: making 1M-token context affordable.
- **Hybrid attention:** layers mix **Compressed Sparse Attention (CSA)** and **Heavily Compressed Attention (HCA)**.
  - CSA compresses every m tokens' KV into one entry (built from 2m overlapping entries). A lightning indexer, whose attention runs in FP4, picks the top-k compressed entries. CSA also uses shared-KV MQA and a sliding window of recent uncompressed tokens.
  - HCA uses a much larger, non-overlapping compression ratio m′ ≫ m, with **dense** attention over all compressed entries plus the sliding window.
  - Both add partial RoPE on the last 64 dimensions, RMSNorm on Q and compressed KV, and learnable attention sinks.
- Efficiency at 1M tokens vs V3.2: V4-Pro needs ≈27% of the single-token FLOPs and 10% of the KV cache. V4-Flash needs ≈10% of the FLOPs and 7% of the KV cache.
- Other components:
  - **mHC** for residual connections.
  - **Muon optimizer** for faster convergence and stability.
  - MTP kept with the same configuration as V3.
  - FP4 for routed-expert weights and FP4 quantization-aware training in post-training.
  - DualPipe 1F1B overlap adapted for mHC.
  - A fine-grained "wave" expert-parallel overlap scheme, compared against Comet.
- What to look at: Fig. 1 (benchmarks and FLOPs/KV vs V3.2), Fig. 2 (overall architecture), Fig. 3 (CSA), Fig. 4 (HCA), Fig. 5 (EP overlap). Read §2.3.1–2.3.4 and the Muon section.
- Prerequisites: MLA, DSA (the indexer idea), MQA, sliding-window attention and attention sinks (StreamingLLM, arXiv 2309.17453 [memory]), and Muon (Keller Jordan's 2024 blog; Moonshot's "Muon is Scalable", arXiv 2502.16982 [memory]).

**DeepSeek-V4.1-Flash: "Pushing the Limits of KV Cache Compression", arXiv 2609.19969 (v1 stamped 2026-09-17; author "DeepSeek-AI") [verified]** — [arXiv HTML](https://arxiv.org/html/2609.19969v1)
- Model: multimodal MoE with 552B backbone parameters and 1M context, pretrained on 45T multimodal tokens.
- **Causal encoder–decoder**, inspired by YOCO (arXiv 2405.05254 [memory]):
  - 20 causal encoder layers and 20 decoder layers.
  - The decoder's global KV is projected from the encoder's final hidden states, so most prompt tokens skip full decoder computation.
  - Activates 8B parameters per token in prefill and 16B in decode.
- **CSA2:** global KV and indexer keys are shared across layers, and top-k indices can be reused. Each layer runs in Full, Reindex or Reuse mode. V4.1 uses pure CSA2 instead of V4's CSA/HCA mix.
- Other components:
  - A hierarchical sparse indexer in the decoder.
  - **FP4 main KV cache** (V4 used FP8). SWA KV stays FP8.
  - Global KV of ≈890 bytes per token, ≈1/4 of V4-Flash.
  - "SWA Bounded Replay", which reconstructs sliding-window states instead of persisting them, bringing persistent KV on SSD/host to ≈1/8 of V4-Flash.
  - Single-Pass mHC, Engram conditional memory, and "DSpark" speculative decoding.
- Flag: I found only a V4.1-**Flash** report. I found no V4.1-Pro report. Treat "V4.1" claims beyond Flash as unverified.

### Inferences
- The chain of attention papers (MLA → NSA → DSA → CSA/HCA → CSA2) is the backbone of the architecture line. Each step keeps the previous one's trick:
  - MLA's latent/MQA-style shared KV survives as V4's "shared KV MQA".
  - DSA's lightning indexer survives into CSA.
  - NSA's three-branch compressed + selected + sliding-window design reappears almost one to one in CSA (compressed entries + top-k + sliding window).
- A learner who understands MLA and NSA can read V4's attention section quickly.
- The FFN side follows a parallel chain: DeepSeekMoE, then aux-loss-free balancing, then FP4 experts, then Engram (a second sparsity axis). The residual stream gets its own upgrade (mHC). For the article, "attention / FFN / residual / numerics" is a clean way to sort the architecture innovations.

### Gaps
- The exact values of m, m′, k and the window size in V4, and the layer interleaving pattern, were not visible in the fetched excerpt.
- I could not confirm whether V4 (as opposed to V4.1-Flash) uses Engram. The V4 abstract does not mention it, and a secondary site (kili-technology) claims it does. Treat that claim as unverified.
- The arXiv ID vs submission date mismatch for V4 is unresolved.
- NSA speedup numbers (e.g. "up to ~11.6× decoding at 64k") and DSA's k=2048 are from memory and not re-fetched.

---

## Q2. Training / reasoning line

### Takeaway
The reasoning line runs as follows:
1. DeepSeekMath introduces GRPO, a critic-free PPO variant (2024-02).
2. R1-Zero shows that pure RL with rule-based rewards on a strong base model produces long chain-of-thought and self-reflection. R1 adds cold-start SFT and multi-stage training for readability, and distillation transfers the skill to small dense models (2025-01; peer-reviewed in Nature 2025-09).
3. V3.1 folds thinking and non-thinking into one model through the chat template.
4. V3.2 scales RL and agentic-task synthesis.
5. V4 trains per-domain specialists (SFT + GRPO) and merges them by on-policy distillation, exposing Non-think / Think High / Think Max effort modes.

### Cited Findings

**DeepSeekMath: arXiv 2402.03300 (2024-02-05) [memory]** — [arXiv](https://arxiv.org/abs/2402.03300)
- Data: a 120B-token math corpus mined from Common Crawl with an iterative fastText classifier. Shows that data curation matters more than model size: the 7B model reaches ≈51.7% on MATH without tools.
- **GRPO (Group Relative Policy Optimization):**
  - Drop PPO's value network.
  - For each prompt, sample a group of G outputs and use the group-normalised reward (r_i − mean)/std as the advantage.
  - Add a KL penalty to a reference model directly in the loss instead of in the reward.
  - Saves the memory and compute of a critic the size of the policy.
- Also offers a "unified paradigm" view of SFT, RFT, DPO, PPO and GRPO as one gradient formula with different data sources and gradient coefficients.
- What to look at: the PPO-vs-GRPO figure and the GRPO objective equation.
- Prerequisites: policy gradient and PPO (arXiv 1707.06347), RLHF/InstructGPT (2203.02155).

**DeepSeek-R1: arXiv 2501.12948 (2025-01-22) [memory for numbers]** — [arXiv](https://arxiv.org/abs/2501.12948)
- **R1-Zero:**
  - GRPO applied directly to V3-Base with no SFT.
  - Rule-based rewards only: accuracy (verifiable answers, test cases) plus format (`<think>` tags). Neural reward models were deliberately avoided because of reward hacking.
  - AIME 2024 pass@1 rose from 15.6% to 71.0% (86.7% with majority voting). Response length grew on its own, and an "aha moment" of self-reflection appeared.
  - Problems: poor readability and language mixing.
- **R1, four stages:**
  1. Cold-start SFT on thousands of long-CoT examples.
  2. Reasoning-oriented RL with an added language-consistency reward.
  3. Rejection sampling to build ≈600k reasoning + ≈200k non-reasoning SFT samples, then SFT of V3-Base.
  4. RL over all scenarios (rule rewards for reasoning, reward models for helpfulness and harmlessness).
- **Distillation:** the 800k samples are used to SFT Qwen2.5 and Llama 3 models (1.5B–70B). Distilling beats running RL on the small model directly.
- **"Unsuccessful attempts":** PRM and MCTS. This section is worth reading.
- What to look at: the R1-Zero training curves (accuracy and response length), the "aha moment" table, the 4-stage pipeline description, and the distillation table.
- Prerequisites: DeepSeekMath (GRPO), V3 (the base model), CoT prompting.

**R1 in Nature [verified]** — [Nature Index entry](https://www.nature.com/nature-index/article/10.1038/s41586-025-09422-z), [Scientific American](https://scientificamerican.com/article/secrets-of-chinese-ai-model-deepseek-revealed-in-landmark-paper), [arXiv](https://arxiv.org/abs/2501.12948)
- Citation: "DeepSeek-R1 incentivizes reasoning in LLMs through reinforcement learning", Nature 645, 633–638, doi:10.1038/s41586-025-09422-z. Published online 2025-09-17, in the 2025-09-18 issue.
- Described as the first major LLM to go through peer review. It updates the January preprint.
- Limitations named in the Nature summary: computational cost, language mixing, prompt sensitivity.
- [memory, flag] Press coverage reported a disclosed R1 training cost of ≈ US$294k on top of the V3 base cost. Verify against the Nature supplementary information before using.
- For a learner, the Nature version plus its supplement is the more complete, peer-reviewed text. The arXiv version is the historical one.

**V3.1 hybrid thinking [verified]** — [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V3.1)
- One model, two modes selected by the chat template: the thinking prefix ends with `<think>`, the non-thinking prefix ends with `</think>`. In multi-turn chats, earlier turns' thinking is dropped.
- Training changes:
  - V3.1-Base extends V3 with a larger two-phase long-context extension: 32K phase at 630B tokens (10×), 128K phase at 209B tokens (3.3×).
  - UE8M0 FP8 scale format.
- No separate arXiv paper exists. The HF card is the primary source.

**V3.2: arXiv 2512.02556 [verified]** — [arXiv](https://arxiv.org/abs/2512.02556)
- Besides DSA, the report contributes:
  - a scalable RL framework with more post-training compute;
  - a large-scale agentic task synthesis pipeline that mixes reasoning with tool use;
  - V3.2-Speciale, a high-compute variant reported at gold-medal level on IMO and IOI 2025.

**V4 post-training and effort levels [verified]** — [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro), [arXiv HTML](https://arxiv.org/html/2606.19348v1)
- Two stages:
  1. **Specialist training:** per-domain experts (math, code, agent, instruction following), each trained with SFT then GRPO-based RL with domain-specific reward models.
  2. **On-policy distillation** into one unified student by minimising reverse KL to the teachers.
- Effort modes:
  - **Non-think**: output is just the summary after `</think>`.
  - **Think High**: `<think>`…`</think>` then the summary.
  - **Think Max**: a special system prompt plus thinking. The card recommends at least 384K context for local Think Max.
  - "V4-Pro-Max" in the paper means V4-Pro in Max mode.
- Gap: the paper's "Reasoning Efforts" subsection could not be retrieved, so how effort is trained or controlled (budgets, RL length penalties) is unverified.

### Inferences
- The line has a clear arc, from "make RL cheap" (GRPO), to "RL alone creates reasoning" (R1-Zero), to "make it usable and transferable" (R1 multi-stage, distillation), to "make it controllable in one model" (V3.1 hybrid, V4 effort modes). V4 also reuses distillation, now on-policy and between a model's own specialists rather than from R1 into small models.
- R1's "distillation > small-model RL" result and V4's on-policy distillation are worth pairing in the article: same idea, two scales.

### Gaps
- How V4 trains the Non-think / High / Max modes is unverified.
- The R1 Nature supplement (training cost, safety evaluation details) was not fetched.

---

## Q3. Other notable papers and open-source releases

### Takeaway
For a learner, three groups of other releases are worth knowing:
1. **Open Infra Week repos** (Feb 2025): FlashMLA, DeepEP, DeepGEMM, DualPipe/EPLB, 3FS/smallpond. These are the code form of V3's systems sections.
2. **Two 2026 architecture papers**: mHC and Engram, both direct V4/V4.1 prerequisites.
3. **DeepSeek-OCR**: an idea paper about compressing context by rendering it as images.

Janus, Prover, Coder and VL are side branches. Skim or skip them unless the reader's interest is multimodal or formal math.

### Cited Findings
- **Open Infra Week, Feb 2025** [verified] — [open-infra-index via search summary](https://docsearch.algolia.com/mcp/docs/repo/deepseek-ai/open-infra-index), [GitHub org](https://github.com/orgs/deepseek-ai/repositories). The index describes itself as "production-tested" components for V3/R1.
  - FlashMLA: MLA decoding kernels for Hopper, ≈3000 GB/s memory-bound and 580 TFLOPS BF16 on H800.
  - DeepEP: expert-parallel all-to-all communication, with NVLink to RDMA forwarding.
  - DeepGEMM: FP8 GEMM for dense and MoE, 1350+ FP8 TFLOPS, ≈300 lines of core logic.
  - DualPipe: the pipeline schedule from the V3 report.
  - EPLB: expert-parallel load balancer using redundant experts.
  - 3FS (Fire-Flyer File System): a parallel file system over SSD and RDMA.
  - smallpond: data processing on DuckDB + 3FS.
  - Licences: MIT on the individual repos checked; the index repo's own licence is unclear.
- **"Insights into DeepSeek-V3"** (arXiv 2505.09343, ISCA '25) [verified] — [arXiv](https://arxiv.org/abs/2505.09343). This is the systems/co-design paper. See Q1.
- **Fire-Flyer AI-HPC** (arXiv 2408.14158, SC24) [memory]: DeepSeek's cost-effective cluster design (10k PCIe A100s, HFReduce, 3FS origins). This is the early infrastructure paper.
- **DeepSeek-OCR: Contexts Optical Compression**, arXiv 2510.18234 (2025-10-21) [verified] — [arXiv](https://arxiv.org/abs/2510.18234)
  - Architecture: DeepEncoder (SAM window attention, then a 16× token compressor, then CLIP global attention) plus a DeepSeek3B-MoE decoder.
  - Results: ≈97% OCR precision at compression under 10× and ≈60% at 20×.
  - Framed as a first look at "optical" compression of long context.
  - A critical follow-up argues that vision-based compression does not beat truncation for language modelling — [arXiv 2512.03643](https://arxiv.org/html/2512.03643v1).
  - Fits the long-context theme as an alternative idea. Skim.
- **mHC** (2512.24880) and **Engram** (2601.07372): see Q1. Read both before V4/V4.1.
- **Side branches** [memory; arXiv IDs not re-verified this session]:
  - DeepSeek LLM, arXiv 2401.02954: scaling laws and hyperparameter choices. Useful background for the V2/V3 data and hyperparameter decisions.
  - DeepSeek-Coder, 2401.14196, and Coder-V2, 2406.11931.
  - DeepSeek-VL2, 2412.10302: a MoE vision–language model.
  - Janus, 2410.13848, and Janus-Pro, 2501.17811: unified multimodal understanding and generation with decoupled visual encoders.
  - DeepSeek-Prover V1, 2405.14333; V1.5, 2408.08152 (RL from proof-assistant feedback, RMaxTS); Prover-V2, 2504.21801 (subgoal decomposition, Lean 4).

### Inferences
- Learner priority:
  - Infra repos: read the READMEs while reading V3 §3. FlashMLA pairs with MLA, DeepEP and EPLB with MoE, DeepGEMM with FP8.
  - mHC and Engram: required for V4/V4.1.
  - OCR: optional.
  - Prover: optional, but conceptually pairs with R1 (verifiable rewards; Lean is the extreme case).
  - Janus/VL: only matters once V4.1-Flash goes multimodal.

### Gaps
- I did not confirm whether any 2026 Open Infra–style repo releases exist (e.g. CSA kernels, an updated FlashMLA for sparse attention). The GitHub org shows FlashMLA and DeepEP still being updated, but details were not checked.
- "DSpark" (V4.1 speculative decoding) appears only in the V4.1-Flash report. No standalone paper was found.

---

## Q4. Two lines or three?

### Takeaway
"Two lines" (cost-reducing architecture vs reasoning training) is a good top-level story, but it hides a third line that DeepSeek itself treats as first-class: **infrastructure / hardware co-design**. Recommendation: present **two main lines plus a systems base layer** (or simply three lines). Many architecture choices only make sense because of hardware limits, such as H800 interconnect bandwidth, FP8/FP4 tensor cores and kernel memory access patterns.

### Cited Findings
- DeepSeek published a dedicated systems paper at a computer architecture venue (ISCA '25) whose central thesis is "hardware-aware model co-design" for V3: MLA for memory, MoE for the compute/communication trade-off, FP8, and a multi-plane network — [arXiv 2505.09343](https://arxiv.org/abs/2505.09343).
- It ran a whole open-source week of seven infrastructure components (kernels, communication, file system, data processing) described as production-tested for V3/R1 — [open-infra-index summary](https://docsearch.algolia.com/mcp/docs/repo/deepseek-ai/open-infra-index).
- NSA's title itself says "Hardware-Aligned" — [ACL Anthology](https://aclanthology.org/2025.acl-long.1126). DSA's indexer is designed with few heads and ReLU so it can run in FP8 — [V3.2 PDF](https://arxiv.org/pdf/2512.02556).
- The V4 report has an infrastructure section: DualPipe adapted to mHC, wave-style EP overlap vs Comet, FP4 indexer and experts. V4.1-Flash's headline is KV bytes per token and SSD/host-persisted KV — [V4 HTML](https://arxiv.org/html/2606.19348v1), [V4.1-Flash HTML](https://arxiv.org/html/2609.19969v1).
- mHC explicitly pairs the math constraint with "rigorous infrastructure optimization", and Engram's selling point is deterministic addressing that allows host-memory prefetch — [mHC](https://arxiv.org/html/2512.24880v2), [Engram](https://arxiv.org/abs/2601.07372v1).

### Inferences
- Several innovations sit on the boundary between lines, so a two-line split forces awkward placements. FP8 training, DualPipe, MTP (training signal and speculative decoding), and on-policy distillation (training, but motivated by serving one model) all cross lines. Possible framings:
  - **A (recommended):** Line 1 Architecture (attention, FFN/MoE, residual, memory). Line 2 Training and reasoning (GRPO, R1, hybrid thinking, specialists, distillation, effort modes). Plus an "Infrastructure foundation" layer that both lines rest on (FP8/FP4, DualPipe, DeepEP/DeepGEMM/FlashMLA, 3FS, ISCA paper).
  - **B:** Three equal lines. This is more accurate, but the infrastructure line is the hardest for a learner with only Transformer basics, so mark it optional/advanced.
- An alternative sub-framing inside Line 1: "attention / KV-cache chain" vs "FFN sparsity chain". This helps readers see that MLA → NSA → DSA → CSA/HCA → CSA2 is one story.

### Gaps
- No DeepSeek source states a "two-line" or "three-line" framing itself. This is an editorial choice.

---

## Q5. Recommended reading order (learner with basic Transformer knowledge)

### Takeaway
Read in stages. Each DeepSeek paper is paired with one external prerequisite, and the reader alternates between lines only where a dependency forces it. Read closely the papers that introduce a mechanism (DeepSeekMoE, V2 §MLA, DeepSeekMath §GRPO, V3 §2–3, R1, V4 §2.3). Skim benchmark-heavy reports and side branches.

### Cited Findings (sources for each item are listed in Q1–Q3; external prerequisites marked [memory] with arXiv IDs)

**Stage 0: prerequisites (read alongside, not all up front)**
- Transformer, "Attention Is All You Need" (1706.03762); RoPE (2104.09864); MQA (1911.02150) and GQA (2305.13245); KV-cache basics.
- MoE basics: Shazeer 2017 (1701.06538), GShard (2006.16668), Switch Transformer (2101.03961).
- RL: PPO (1707.06347), InstructGPT/RLHF (2203.02155).
- Later: FlashAttention (2205.14135), Hyper-Connections (2409.19606), YOCO (2405.05254), speculative decoding (2211.17192), Muon (Moonlight 2502.16982).

**Stage 1: foundations (Line 1 start)**
1. DeepSeekMoE 2401.06066. **Close read** of §3 (fine-grained + shared experts). Skim experiments. Prerequisites: Switch/GShard.
2. (Optional skim) DeepSeek LLM 2401.02954, for scaling-law and hyperparameter background.

**Stage 2: RL basics (Line 2 start)**
3. DeepSeekMath 2402.03300. **Close read** of the GRPO section and the "unified paradigm" section. Skim the data pipeline. Prerequisite: PPO.

**Stage 3: MLA**
4. DeepSeek-V2 2405.04434. **Close read** of the MLA section, including the decoupled RoPE appendix. Skim the alignment and evaluation sections. Prerequisites: MQA/GQA, RoPE.

**Stage 4: the V3 synthesis (both lines meet here)**
5. DeepSeek-V3 2412.19437. **Close read** of §2 (aux-loss-free balancing, MTP) and §3 (DualPipe, FP8). Skim evaluation. Optional advanced companions:
   - the "Insights into DeepSeek-V3" ISCA paper (2505.09343);
   - the FlashMLA, DeepEP, DeepGEMM and DualPipe READMEs.

**Stage 5: reasoning**
6. DeepSeek-R1 2501.12948, or the Nature version (doi 10.1038/s41586-025-09422-z). **Close read** of R1-Zero, the 4-stage pipeline, distillation, and "Unsuccessful attempts". Re-read the GRPO equation from Stage 2 first.

**Stage 6: sparse attention**
7. NSA 2502.11089. **Close read** of the method (three branches) and the kernel figure. Prerequisite: FlashAttention tiling.
8. DeepSeek-V3.2 2512.02556. **Close read** of the DSA section (indexer + top-k; compare with NSA). Skim the RL-scaling and agentic-synthesis sections, which are useful for Line 2 readers. Also read the V3.1 HF card for hybrid thinking (5 minutes).

**Stage 7: 2026 building blocks**
9. mHC 2512.24880 (read the Hyper-Connections paper first). Medium depth: focus on the identity-mapping argument and Sinkhorn projection.
10. Engram 2601.07372. Medium depth: conditional memory as a second sparsity axis, and the U-shaped allocation law.

**Stage 8: current frontier**
11. DeepSeek-V4 2606.19348.
    - **Close read** of §2.3 (CSA, HCA, efficiency) and the post-training pipeline (specialists, then on-policy distillation; effort modes).
    - Skim the infrastructure and benchmark sections.
    - Prerequisites: DSA, MLA, mHC, Muon, StreamingLLM attention sinks.
12. DeepSeek-V4.1-Flash 2609.19969. **Close read** of the encoder–decoder section and CSA2 (Full/Reindex/Reuse). Skim FP4 KV and SWA Bounded Replay unless you work on inference serving. Prerequisite: YOCO.

**Optional side reading:** DeepSeek-OCR 2510.18234 (long-context compression idea, plus its critique 2512.03643); Prover-V1.5/V2 (verifiable rewards in Lean); Janus/Janus-Pro (multimodal); Fire-Flyer AI-HPC 2408.14158 (infrastructure history).

### Inferences
- **Fast track for a busy reader (about 5 papers):** DeepSeekMoE §3, then V2 MLA section, then DeepSeekMath GRPO section, then R1, then V4 §2.3 plus post-training. V3 can be read via its Fig. 2 and the aux-loss-free/MTP sections only.
- **Pacing:** Stages 1–5 cover the 2024–early 2025 core, which most explainers focus on. Stages 6–8 hold the 2025-H2 to 2026 material, which few secondary explainers yet cover well. Readers will lean more on primary PDFs there, so the article should give more hand-holding (diagrams of CSA/HCA vs NSA vs DSA) in that part.
- **Suggested "read alongside" pairings for the article:**
  - DeepSeekMoE ↔ Switch Transformer
  - V2/MLA ↔ GQA paper
  - DeepSeekMath ↔ PPO
  - V3 ↔ ISCA Insights paper
  - NSA ↔ FlashAttention
  - mHC ↔ Hyper-Connections
  - V4.1 ↔ YOCO
  - V4 Muon section ↔ Moonlight

### Gaps
- External prerequisite arXiv IDs are from memory and were not re-fetched.
- I did not find a DeepSeek-endorsed reading list. The order above is editorial, based on the dependency structure documented in Q1–Q3.
