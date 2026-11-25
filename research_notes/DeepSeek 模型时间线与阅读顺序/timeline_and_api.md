# DeepSeek 模型时间线与 API 现状（截至 2026-11-23）

Method note: Primary sources fetched in this session (2026-11-23) are api-docs.deepseek.com (Change Log, Models & Pricing, Quick Start, Thinking Mode guide, and news pages 260424, 260813, 260910, 261017, 251201) and Hugging Face (the deepseek-ai org page, the model list sorted by created date pages 0–1, and the DeepSeek-V4-Pro and DeepSeek-V4.1-Flash model cards). Facts about 2023–2024 models that come only from arXiv IDs and HF repo names I already knew, and were **not re-fetched this session**, are marked [recall, not re-verified]. The writer should spot-check those before publishing. HF "updated" dates are not release dates, so they are given only as supporting evidence.

## 1. Model release timeline (2023 to late 2026)

### Takeaway
DeepSeek's line runs through four eras:
- **2023–early 2024:** dense LLM, Coder, MoE, Math and VL.
- **2024:** V2 family with MLA and DeepSeekMoE.
- **Dec 2024–Dec 2025:** V3 base family. R1 led to V3.1 hybrid thinking, then V3.2 with DSA sparse attention.
- **2026:** V4 family. Pro is 1.6T/49B, Flash is 284B/13B, both 1M context, MIT, CSA+HCA attention. Then V4.1-Flash (Sep 2026) uses a new causal encoder–decoder MoE with native vision.

The default API name moved from `deepseek-chat`/`deepseek-reasoner` to `deepseek-v4-*`, and now to `deepseek-flash` + `deepseek-v4-pro`.

### Cited Findings

**2023–2024 (pre-V3) [recall, not re-verified unless noted]**
- **DeepSeek Coder** (Nov 2023): 1.3B/6.7B/33B, 16K context, project-level code corpus with FIM. Paper arXiv 2401.14196. — [arXiv](https://arxiv.org/abs/2401.14196) [recall]
- **DeepSeek LLM** 7B/67B dense (late Nov 2023), 2T tokens, 4K context. Paper "Scaling Open-Source LLMs with Longtermism", arXiv 2401.02954. — [arXiv](https://arxiv.org/abs/2401.02954) [recall]
- **DeepSeekMoE 16B** (Jan 2024): 16.4B total / ~2.8B active. Introduced fine-grained expert segmentation plus shared experts. arXiv 2401.06066. — [arXiv](https://arxiv.org/abs/2401.06066) [recall]
- **DeepSeekMath 7B** (Feb 2024): introduced GRPO. arXiv 2402.03300. — [arXiv](https://arxiv.org/abs/2402.03300) [recall]
- **DeepSeek-VL** 1.3B/7B (Mar 2024). arXiv 2403.05525. — [arXiv](https://arxiv.org/abs/2403.05525) [recall]
- **DeepSeek-V2** (May 2024): 236B total / 21B active, 128K context. Introduced MLA (Multi-head Latent Attention) and DeepSeekMoE at scale, plus V2-Lite at 16B/2.4B. arXiv 2405.04434. — [arXiv](https://arxiv.org/abs/2405.04434) [recall]
- API Change Log confirms these chat upgrades:
  - `deepseek-chat` to DeepSeek-V2-0517 on 2024-05-17.
  - `deepseek-chat` to V2-0628 on 2024-06-28. HumanEval 79.88→84.76, MATH 55.02→71.02.
  - — [Change Log](https://api-docs.deepseek.com/updates)
- **DeepSeek-Coder-V2** (Jun 2024): 236B/21B and Lite 16B/2.4B, 128K context, 338 languages. arXiv 2406.11931. — [arXiv](https://arxiv.org/abs/2406.11931) [recall]
  - The API Change Log confirms `deepseek-coder` was upgraded to Coder-V2-0614 on 2024-06-14 and to Coder-V2-0724 on 2024-07-24. — [Change Log](https://api-docs.deepseek.com/updates)
- **Prover-V1 / Prover-V1.5-RL** (7B) are present on HF. Prover-V1.5 is arXiv 2408.08152 [recall]. — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created&p=1)
- **DeepSeek-V2.5** went live on 2024-09-05. It merged chat and coder, and both `deepseek-coder` and `deepseek-chat` now reach it. ArenaHard 68.3→76.3, HumanEval 89%. — [Change Log](https://api-docs.deepseek.com/updates)
- **V2.5-1210** went live on 2024-12-10. MATH-500 74.8→82.8, LiveCodeBench 29.2→34.38. The HF repo DeepSeek-V2.5-1210 is 236B. — [Change Log](https://api-docs.deepseek.com/updates); [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created&p=1)
- **DeepSeek-VL2** (tiny 3B / small 16B / 27B MoE): the HF repos were updated on 2024-12-18. Paper arXiv 2412.10302 [recall]. — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created&p=1)
- **Janus / JanusFlow 1.3B and Janus-Pro 1B/7B**: unified multimodal understanding and generation. Janus-Pro was released around 2025-01-27 [recall]. HF repos are present. — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created&p=1)
- **API features in 2024:**
  - JSON mode, function calling, chat prefix completion (beta) and FIM `/completions` (beta) arrived on 2024-07-25.
  - Disk context caching arrived on 2024-08-02.
  - — [Change Log](https://api-docs.deepseek.com/updates)

**V3 / R1 era (Dec 2024–2025)**
- **DeepSeek-V3:** `deepseek-chat` was upgraded to it on 2024-12-26. — [Change Log](https://api-docs.deepseek.com/updates)
  - 671B total / 37B active, 128K context, MLA + DeepSeekMoE, auxiliary-loss-free balancing, multi-token prediction, FP8 training, 14.8T tokens. arXiv 2412.19437. — [arXiv](https://arxiv.org/abs/2412.19437) [recall]
  - HF lists DeepSeek-V3 and V3-Base at "684B", which includes the MTP module. — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created&p=1)
- **DeepSeek-R1 and R1-Zero** were released on 2025-01-20, and `deepseek-reasoner` became R1. — [Change Log](https://api-docs.deepseek.com/updates)
  - Both are built on V3-Base. R1-Zero is pure RL (GRPO) with no SFT. MIT license. arXiv 2501.12948, later published in Nature (Sep 2025). — [arXiv](https://arxiv.org/abs/2501.12948) [recall]
- **Distilled models:** R1-Distill-Qwen-1.5B/7B/14B/32B and R1-Distill-Llama-8B/70B. — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created&p=1)
- **DeepSeek-V3-0324:** `deepseek-chat` was upgraded on 2025-03-24. MMLU-Pro 75.9→81.2, GPQA 59.1→68.4, AIME 39.6→59.4, LiveCodeBench 39.2→49.2. — [Change Log](https://api-docs.deepseek.com/updates)
  - Its weights moved to the MIT license [recall].
- **DeepSeek-Prover-V2** (671B and 7B): HF repos dated 2025-04-30, alongside the ProverBench dataset. — [HF org](https://huggingface.co/deepseek-ai)
- **DeepSeek-R1-0528:** `deepseek-reasoner` was upgraded on 2025-05-28. — [Change Log](https://api-docs.deepseek.com/updates)
  - AIME 2025 70.0→87.5, GPQA 71.5→81.0, LCB v6 63.5→73.3, Aider 57.0→71.6.
  - Adds JSON output and function calling.
  - The distilled DeepSeek-R1-0528-Qwen3-8B is on HF (2025-05-29). — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created&p=1)
- **DeepSeek-V3.1** (2025-08-21): hybrid reasoning, with one model serving both thinking and non-thinking modes. `deepseek-chat` is non-thinking and `deepseek-reasoner` is thinking. SWE-bench Verified 66.0, SWE-bench Multilingual 54.5, Terminal-bench 31.3. — [Change Log](https://api-docs.deepseek.com/updates)
  - HF has V3.1 and V3.1-Base (685B). — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created&p=1)
  - 128K context and UE8M0 FP8 scale format [recall].
- **DeepSeek-V3.1-Terminus** (2025-09-22): less Chinese-English mixing and fewer abnormal characters; better Code Agent and Search Agent. — [Change Log](https://api-docs.deepseek.com/updates)
- **DeepSeek-V3.2-Exp** (2025-09-29): introduced DSA (DeepSeek Sparse Attention), and it was served on both aliases. — [Change Log](https://api-docs.deepseek.com/updates)
  - HF has V3.2-Exp and V3.2-Exp-Base (685B). — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created)
- **DeepSeek-OCR** (3B) is on HF. Its updated date is 2025-11-04; the release was around 2025-10-20 [recall]. — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created)
- **DeepSeek-Math-V2** (685B, self-verifying theorem proving) is on HF with a date of 2025-11-27. — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created)
- **DeepSeek-V3.2 and V3.2-Speciale** (2025-12-01): — [news251201](https://api-docs.deepseek.com/news/news251201)
  - V3.2 is the official successor to V3.2-Exp. It is described as "the first DeepSeek model to integrate thinking directly into tool-use", using 1,800+ synthetic environments and 85k+ instructions.
  - Speciale is API-only, has no tool calls, and was available on a temporary endpoint until 2025-12-15 15:59 UTC.
  - Tech report: [paper.pdf](https://huggingface.co/deepseek-ai/DeepSeek-V3.2/resolve/main/assets/paper.pdf)
  - Both are 685B on HF. — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created)

**2026**
- **DeepSeek-OCR-2** (3B) is on HF with a date of 2026-02-03. — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created)
- **DeepSeek-V4 Preview** (2026-04-24): "officially live & open-sourced". — [news260424](https://api-docs.deepseek.com/news/news260424)
  - **V4-Pro is 1.6T total / 49B active. V4-Flash is 284B / 13B.**
  - **1M context, and "1M is now the default across all official DeepSeek services".**
  - Both have Thinking and Non-Thinking modes.
  - Novel attention: token-wise compression + DSA.
  - Tech report: [DeepSeek_V4.pdf](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf)
- **V4 model card:** — [HF V4-Pro](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro)
  - MoE with hybrid attention: Compressed Sparse Attention (CSA) + Heavily Compressed Attention (HCA).
  - Manifold-Constrained Hyper-Connections (mHC) and the Muon optimizer.
  - Pre-trained on >32T tokens. Post-training is domain SFT + GRPO, then on-policy distillation into one model.
  - FP4 (MoE experts) + FP8 mixed precision.
  - **MIT license** (code + weights).
  - Self-described as "a preview version of DeepSeek-V4 series".
  - Paper is arXiv 2606.19348 per the card's link. The ID looks odd for an April paper and was not independently checked.
- **V4 Base models** (V4-Pro-Base 1.6T, V4-Flash-Base 292B) are dated Apr 27 on HF. The released instruct repos are listed at 1.6T and 291B. — [HF org](https://huggingface.co/deepseek-ai)
- **V4-Pro-DSpark / V4-Flash-DSpark** (speculative decoding variants, Jul 4) and small `dspark_*`/`eagle3_*`/`dflash_*` draft models (Jun 28) are on HF. — [HF list](https://huggingface.co/models?author=deepseek-ai&sort=created)
- **V4-Flash-0731** (2026-07-31): public beta. Same architecture and size as V4-Flash-Preview, re-post-trained only. API only. — [Change Log](https://api-docs.deepseek.com/updates)
- **V4-Pro GA / V4-Pro-0813** (2026-08-13): — [news260813](https://api-docs.deepseek.com/news/news260813)
  - API names unchanged; "Major Agent upgrades"; offered in the app/web as "Expert Mode".
  - Reasoning effort low/high/max.
  - Native OpenAI Responses API with one-click Codex setup.
  - Peak/off-peak pricing from 2026-08-16 16:00 UTC.
  - HF repo DeepSeek-V4-Pro-0813 (1.7T). — [HF org](https://huggingface.co/deepseek-ai)
- **V4-Flash-Vision-Exp** (2026-08-21): experimental multimodal, `deepseek-v4-flash-vision-exp`. HF size 305B. — [Change Log](https://api-docs.deepseek.com/updates)
- **DeepSeek-V4.1-Flash** (2026-09-10): "the smallest model in the new architecture family". — [news260910](https://api-docs.deepseek.com/news/news260910)
  - **552B total MoE, 8B active for input / 16B for output.**
  - **New causal encoder–decoder with an asymmetric input/output split.**
  - Native visual understanding.
  - KV cache needs 1/4 the HBM and 1/8 the SSD of the previous generation.
  - Claims to beat V4-Pro on benchmarks.
- **V4.1-Flash model card:** — [HF V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash)
  - "multimodal MoE with 552B backbone parameters". The Hub lists 763B total.
  - ~8B active per token in prefill, 16B in decode.
  - **Context up to 1M.**
  - **Causal Encoder-Decoder: 40 layers = 20-layer causal encoder + 20-layer decoder.**
  - Each MoE layer has 1 shared + 384 routed experts, 6 active.
  - Components: CSA2 attention, FP4 main KV cache, Engram conditional memory (196B params), DSpark speculative decoding.
  - DeepSeek-ViT vision encoder with a 2-layer MLP projector.
  - **MIT license.**
  - No Jinja chat template; it ships a Python `encoding` reference instead.
  - "Continuously controllable reasoning effort" from 1 to 100.
  - Recommended sampling is temperature 1.0 with top_p 0.95/1.0.
  - Tech report: [DeepSeek_V41_Tech_Report.pdf](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/DeepSeek_V41_Tech_Report.pdf). The HF papers list shows the title "DeepSeek-V4.1-Flash: Pushing the Limits of KV Cache Compression". — [HF org](https://huggingface.co/deepseek-ai)
- **DeepSeek-V4.1-Flash-1017** (2026-10-17): — [news261017](https://api-docs.deepseek.com/news/news261017); [Change Log](https://api-docs.deepseek.com/updates)
  - A "Pareto improvement": better scores, shorter outputs, lower cost.
  - RL with self-feedback for end-to-end project building.
  - `deepseek-flash` now points to it; price unchanged.
  - Weights on HF and ModelScope.
  - Recommended harness: DeepSeek Harness ≥0.2.1-rc.1.
  - The in-house Search & Fetch API opened at $0.75 per 1K calls.
- **2026-11-13 "Full-suite application APIs":** — [Change Log](https://api-docs.deepseek.com/updates)
  - For `deepseek-flash`: web search (`deepseek_web_search` tool), deep thinking search, file input (PDF/Office/images), URL reading (`deepseek_read_url`), and a model safety check parameter.
  - Standalone Search & Fetch, OCR, ASR and TTS APIs.
  - An open-source demo chat client.
- **No V4.1-Pro release found.** news260910 says `deepseek-v4-pro` would route to V4.1-Flash "until V4.1-Pro launches", so V4.1-Pro is announced but not released as of 2026-11-23. — [news260910](https://api-docs.deepseek.com/news/news260910)

### Inferences
- **"V4 Preview" vs "V4 release":** the open weights of 2026-04-24 are self-labelled preview. The "release" milestones are API-side: Flash-0731 public beta and Pro GA on 2026-08-13, with the -0731/-0813 checkpoints. There was no separate "V4 final" open-weight drop other than the -0731/-0813 repos.
- **"V4.1" so far means Flash only.** For a reading map, V4.1-Flash is architecturally a new generation (encoder–decoder, Engram, CSA2, vision), not an incremental V4 update.
- **The V4.1-Flash parameter count is ambiguous.** The official "552B" is the backbone. The HF total of 763B plausibly adds Engram memory (196B) and the vision tower. Quote "552B backbone (763B incl. Engram etc. per HF)".

### Gaps
- License, context and exact dates for 2023–2024 models were not re-fetched this session. Verify via their HF cards: Coder, LLM and MoE used the "DeepSeek License" (code MIT + model license). V3 was originally the DeepSeek model license; R1 and V3-0324 onward are MIT.
- V3.2 / V3.2-Exp context (128K) and license (MIT) were not confirmed this session.
- The V4 arXiv ID 2606.19348 appears inconsistent with a late-April publication. Treat it as unverified and prefer the HF PDF link.
- Exact price tables in the news images (Aug/Sep 2026) were not readable. The current prices come from the pricing page (section 2).

## 2. Current API state (api-docs.deepseek.com, as of 2026-11-23)

### Takeaway
There are two live model IDs:
- `deepseek-flash`, which serves DeepSeek-V4.1-Flash-1017, with vision.
- `deepseek-v4-pro`, which serves DeepSeek-V4-Pro-0813, text only.

Both have 1M context and 384K max output, and thinking is on by default at effort `high`. Both have OpenAI-compatible and Anthropic-compatible (`/anthropic`) endpoints plus the Responses API. `deepseek-chat`/`deepseek-reasoner` were scheduled to die on 2026-07-24 15:59 UTC. **The claim's replacement pair is partly wrong**: at the retirement date the replacements were `deepseek-v4-flash`/`deepseek-v4-pro`. `deepseek-flash` only appeared on 2026-09-10.

### Cited Findings
- **Live models**, from the Models & Pricing page — [Pricing](https://api-docs.deepseek.com/quick_start/pricing):
  - `deepseek-flash` = DeepSeek-V4.1-Flash-1017 and `deepseek-v4-pro` = DeepSeek-V4-Pro-0813.
  - Both: 1M context, 384K max output, thinking + non-thinking with **thinking as the default**.
  - Both support JSON output, tool calls, the Responses API, the Anthropic API and chat prefix completion (beta).
  - FIM (beta) works in non-thinking mode only.
  - Vision: flash yes, v4-pro no.
  - Concurrency limit: flash 2500, pro 500.
- **Pricing per 1M tokens in USD, off-peak / peak** — [Pricing](https://api-docs.deepseek.com/quick_start/pricing):

  | Model | Cache-hit input | Cache-miss input | Output |
  |---|---|---|---|
  | `deepseek-flash` | $0.003 / $0.006 | $0.15 / $0.30 | $0.60 / $1.20 |
  | `deepseek-v4-pro` | $0.022 / $0.044 | $0.66 / $1.32 | $1.98 / $3.96 |

  - Peak hours are 01:00–04:00 and 06:00–10:00 UTC, Mon–Fri, excluding Chinese public holidays.
  - From 2026-11-18 12:00 Beijing time, server-side search/fetch tool results are billed as ordinary tokens, not per call.
- **Base URLs:** `https://api.deepseek.com` (OpenAI format) and `https://api.deepseek.com/anthropic` (Anthropic format). — [Quick Start](https://api-docs.deepseek.com/); [Pricing](https://api-docs.deepseek.com/quick_start/pricing)
- **Deprecated IDs and dates:**
  - `deepseek-chat` & `deepseek-reasoner`: on 2026-04-24, DeepSeek said they "will be fully retired and inaccessible after Jul 24th, 2026, 15:59 (UTC)". Until then they routed to `deepseek-v4-flash` non-thinking / thinking. — [news260424](https://api-docs.deepseek.com/news/news260424); [Change Log](https://api-docs.deepseek.com/updates)
    - The current docs no longer mention these names. — [Quick Start](https://api-docs.deepseek.com/)
    - Secondary sources report the cutoff happened on schedule. — [digitalapplied (Jul 30 2026, secondary)](https://www.digitalapplied.com/blog/model-deprecation-calendar-api-sunset-survival); [dev.to (secondary)](https://dev.to/zephyrelabs369/deepseek-retired-its-legacy-model-ids-is-your-api-relay-still-mapping-them-correctly-1jfm)
  - `deepseek-v4-flash` and `deepseek-v4-flash-vision-exp`: both models were retired on 2026-09-10. — [Change Log](https://api-docs.deepseek.com/updates); [Pricing](https://api-docs.deepseek.com/quick_start/pricing)
    - The names are still accepted, routed to V4.1-Flash-1017 and billed at the Flash price.
  - `deepseek-coder`: merged into V2.5 on 2024-09-05. — [Change Log](https://api-docs.deepseek.com/updates)
    - No explicit retirement date found.
  - V3.2-Speciale endpoint: expired 2025-12-15 15:59 UTC. — [Change Log](https://api-docs.deepseek.com/updates)
- **V4-Pro status conflict:**
  - news260910 says `deepseek-v4-pro` "will route to V4.1-Flash starting 04:00 UTC Sep 14, 2026 until V4.1-Pro launches" and calls V4-Pro "being phased out". — [news260910](https://api-docs.deepseek.com/news/news260910)
  - The Change Log entry for 2026-09-10 instead says the V4 Pro API "continues after September 14, 2026, with billing unchanged". — [Change Log](https://api-docs.deepseek.com/updates)
  - The pricing page still lists `deepseek-v4-pro` = V4-Pro-0813. This looks like a reversal or update after the announcement; the current docs favour "V4-Pro still served". — [Pricing](https://api-docs.deepseek.com/quick_start/pricing)
- **Thinking mode** — [Thinking Mode guide](https://api-docs.deepseek.com/guides/thinking_mode):
  - On by default, with effort defaulting to `high`.
  - OpenAI format: `thinking: {type: enabled|disabled}` (via `extra_body` in the OpenAI SDK) and `reasoning_effort: low|high|max`.
  - Anthropic format: `output_config.effort`, plus a reasoning/effort toggle where `none` disables thinking. The extracted mapping was slightly garbled, so check the live page.
  - Effort mapping: minimal/low→low, medium/high/xhigh→high, max/ultra→max.
  - `temperature`, `presence_penalty` and `frequency_penalty` are silently ignored in thinking mode.
  - `top_p` differs by model:
    - flash: effective 0.95–1.0 in thinking mode only.
    - v4-pro: ignored in thinking mode.
  - `tool_choice: none` disables tools.
  - **With tools, the client must pass back `reasoning_content` from all previous turns, or the API returns 400.** Without tools it is ignored.
- **Effort levels low/high/max** were introduced for V4-Pro and V4-Flash on 2026-08-13. — [news260813](https://api-docs.deepseek.com/news/news260813)
- **Application APIs:** server tools `deepseek_web_search` (non-thinking = web search, thinking = "deep thinking search") and `deepseek_read_url`, file input, and a safety check parameter, available across the Messages, Responses and Chat Completions APIs (2026-11-13). — [Change Log](https://api-docs.deepseek.com/updates)
- **DeepSeek Harness** (an agent harness, in developer preview) is promoted on the docs home page. — [Quick Start](https://api-docs.deepseek.com/)

### Inferences
- **Corrected API naming chain:**
  - `deepseek-chat`/`deepseek-reasoner` (2024–2026-07-24)
  - → `deepseek-v4-flash`/`deepseek-v4-pro` (2026-04-24 →)
  - → `deepseek-flash` (generation-agnostic alias, 2026-09-10 →) + `deepseek-v4-pro`
- **Thinking mode is now a parameter, not a model ID.** This differs from the 2025 split between `deepseek-reasoner` and `deepseek-chat`.

### Gaps
- No first-party post-cutoff confirmation (e.g. an error message) that `deepseek-chat`/`deepseek-reasoner` now fail. Only the pre-announcement plus secondary reports.
- The exact Anthropic-format parameter names for the thinking toggle are ambiguous in the fetched extraction.
- The pricing history before Sep 2026 is only in images that were not captured.

## 3. Verification of specific architecture claims

### Takeaway
Both claims hold, per primary sources:
- **V4 has a 1M default context:** confirmed by news260424 ("1M is now the default across all official DeepSeek services") and the HF card.
- **V4.1-Flash uses a causal encoder–decoder:** confirmed by news260910 and the HF card (20+20 layers, asymmetric prefill 8B / decode 16B active).

### Cited Findings
- V4: 1M context for both Pro and Flash; "1M is now the default across all official DeepSeek services". — [news260424](https://api-docs.deepseek.com/news/news260424); [HF V4-Pro](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro)
- V4.1-Flash: "new causal encoder-decoder design with an asymmetric split between input and output processing". — [news260910](https://api-docs.deepseek.com/news/news260910)
  - The card details a 20-layer causal encoder + 20-layer decoder, 1M context, 384 routed + 1 shared experts with 6 active, Engram 196B, CSA2, FP4 KV cache and DSpark. — [HF V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash)
- Both current API models list 1M context. — [Pricing](https://api-docs.deepseek.com/quick_start/pricing)

### Inferences
- **The asymmetric active parameters (8B input / 16B output) likely follow from the encoder handling prefill and the decoder handling generation.** This is an interpretation; confirm wording in the V4.1 tech report.

### Gaps
- The V4.1 tech report PDF was not read this session, so details beyond the card summary are unverified (e.g. how encoder and decoder share KV cache).

## 4. Change Log entries 2025–2026 (condensed, all from [Change Log](https://api-docs.deepseek.com/updates))

### Takeaway
There are 14 entries from 2025-01-20 to 2026-11-13: 7 in 2025 and 7 in 2026. They track the move from alias swaps (2025) to explicit model IDs, new API surfaces (Anthropic, Responses), peak pricing and application APIs (2026).

### Cited Findings
- **2025-01-20:** `deepseek-reasoner` = R1.
- **2025-03-24:** `deepseek-chat` → V3-0324.
- **2025-05-28:** `deepseek-reasoner` → R1-0528, with JSON + function calling.
- **2025-08-21:** V3.1 hybrid; chat = non-thinking, reasoner = thinking.
- **2025-09-22:** V3.1-Terminus.
- **2025-09-29:** V3.2-Exp.
- **2025-12-01:** V3.2, plus the V3.2-Speciale temporary endpoint (to 12-15).
- **2026-04-24:** V4 (`deepseek-v4-pro`, `deepseek-v4-flash`), OpenAI + Anthropic formats. Legacy aliases to be retired 2026-07-24.
- **2026-07-31:** V4-Flash-0731 public beta (re-post-trained only); Responses API.
- **2026-08-13:** V4-Pro GA; Responses API for Codex; effort low/high/max; peak/off-peak pricing (off-peak = 50%) from 2026-08-16 16:00 UTC.
- **2026-08-21:** V4-Flash-Vision-Exp.
- **2026-09-10:** V4.1-Flash (`deepseek-flash`, native vision). V4-Flash and Vision-Exp retired and routed to V4.1-Flash. V4-Pro continues after 09-14. Prices cut.
- **2026-10-17:** V4.1-Flash-1017; in-house Search API opened.
- **2026-11-13:** Application APIs (web search, file input, URL reading, safety), standalone Search & Fetch / OCR / ASR / TTS, and a demo client.
- **Discrepancy:** the 2025-01-20 entry also contains a sentence saying V3 can be invoked with `model='deepseek-reasoner'`. This is likely an extraction artefact or doc typo; the original text says R1 is `deepseek-reasoner`.

### Inferences
- **The 2026 cadence is roughly monthly API-side releases**, with open weights on HF for most (V4 Base/Preview, -0731, -0813, Vision-Exp, V4.1-Flash, -1017).

### Gaps
- The Change Log contains no entry confirming the actual 2026-07-24 cutoff event.
