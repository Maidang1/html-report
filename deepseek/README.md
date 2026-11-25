# DeepSeek 学习笔记

这个文件夹专门放 DeepSeek 相关的文章：模型与论文、架构、训练方法、API 和在 agent 里的用法。

## 约定

- 每篇文章一个 Markdown 源文件 + 生成的同名 HTML，文件名用 `deepseek_` 前缀的英文蛇形命名，例如 `deepseek_mla_attention.md`。
- front matter 里写 `index: index.html`，页头的“← 全部文章”回到本文件夹的目录页。
- 涉及模型列表、API 参数、价格的内容，写明“截至某日”和官方出处（api-docs.deepseek.com、huggingface.co/deepseek-ai、arXiv）。

## 生成

在仓库根目录运行：

```bash
S=.claude/skills/tech-article-html/scripts
node $S/md_to_article.mjs deepseek/deepseek_xxx.md                      # 生成文章
python3 $S/build_index.py deepseek --title 'DeepSeek 学习笔记' --back ../index.html
python3 $S/build_index.py .                                             # 根目录页的“专题”入口
```

## 计划中的文章

学习地图（`deepseek_learning_map.md`）第 8 节列出了建议顺序，写完一篇就在那里打勾、在这里补一行。

- [x] `deepseek_learning_map.md`：DeepSeek 学习地图
- [x] `deepseek_9day_gpt_to_api.md`：九天从 bigram 到 DeepSeek API（D1–D9 学习计划的完整讲解）
- [x] `dsh_plugin_guide.md`：给 dsh（DeepSeek Harness）写插件
- [x] `dsh_lifecycle_and_effects.md`：dsh 插件的生命周期与副作用（Fiber、`ctx.effect`、卸载顺序，含实际运行的实验）
- [x] `dsh_services_events_config_plugin.md`：dsh 的服务、事件、配置与插件实战（学习计划 03–07：调用方追踪、五种分发模式、volatile 与 patch 分层、HMR、教程 07 的问题、acme-ledger 示例插件）
