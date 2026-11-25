# dsh 插件的打包、配置与安装：bundle、profile、cordis.patch.yml、配置分层、`dsh plugin add`

版本基准：
- 源码：github.com/deepseek-ai/deepseek-harness，main 分支 commit `4b399c39a6d1479cd84ca591b4d7c88e24d38a96`（2026-11-22T16:50:28+08:00），根 `package.json` 版本 `0.2.3-alpha.2`，`packageManager: pnpm@11.28.5`，`engines.node: ^24.22.3 || >=26.13.1`。下文 `SRC/` = `https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/`。
- npm：`@deepseek-ai/dsh` dist-tags（2026-11-23 查询）：`latest: 0.2.2-rc.5`、`next: 0.2.2-rc.5`、`alpha: 0.2.3-alpha.2`；registry `time.modified = 2026-11-22T09:57:12Z`。`@deepseek-ai/cordis` latest `4.1.0`，`@deepseek-ai/schemastery` `3.19.0`。dsh 仍是 developer preview / rc 版本，下文行为可能随版本变化。
- 实验环境（本人实际运行）：Linux arm64，Node v24.22.2，`npm install -g --prefix <scratch>/prefix @deepseek-ai/dsh@0.2.2-rc.5 pnpm@10`（得到 pnpm 10.34.9），另装 pnpm 11.28.5 做对照；`DSH_HOME=<scratch>/home`。脚本与产物在 `/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/66d83128-a37c-4163-ae6b-f001409e9960/scratchpad/dsh-try/`。下文凡标 **[已运行]** 的均为实际执行的命令和输出；标 **[只读]** 的只读了源码/文档。

---

## Q1 什么是 bundle：`dsh.bundle`、`cordis.patch.yml`、必需文件、命名、内置 bundle 的结构

### Takeaway
bundle 就是一个在 `package.json` 里声明 `"dsh": { "bundle": { "patch": ... } }` 的普通 npm 包，它对外贡献的是**一层配置 patch**（YAML 数组），插件行用**包名**引用安装后的代码；最小只需 `package.json` + `cordis.patch.yml` + 入口 JS 三个文件。仓库内置 bundle（`packages/bundle/*`）结构相同，只是 patch 文件更长，且 `web-app` 用了多文件 patch 列表。

### Cited Findings
- **两个概念、两种 manifest**：bundle 是"携带一层配置的 npm 包"，manifest 声明 `dsh.bundle`，回答"这个包贡献什么"；profile 是 `$DSH_HOME/profiles/<name>` 下的目录，manifest 声明 `dsh.profile`，回答"由哪些 bundle、按什么顺序组成"。"A bundle is what you author and distribute; a profile is what a user boots… Nothing is both." — [publish.md L9-16](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L9-L16)
- **最小 bundle 的三个文件**：`package.json`（声明 dsh.bundle）、`cordis.patch.yml`（profile 列出该 bundle 时应用的层）、`index.js`（patch 行引用的插件模块）。官方示例 `package.json`：`name: dsh-hello-plugin`、`type: module`、`main: index.js`、`files: ["index.js","cordis.patch.yml"]`、`dsh.bundle.patch: "./cordis.patch.yml"` — [publish.md L26-44](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L26-L44)
- **patch 里按包名引用**："plugin rows reference the package by name instead of a relative source path so Node resolution finds the installed code"，示例 `- insert: [{ id: hello, name: dsh-hello-plugin }]` — [publish.md L56-62](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L56-L62)
- **`patch` 可以是有序列表**：如 `["./base.patch.yml", "./web.patch.yml"]`，按顺序作为**同一层**应用，每个文件里的相对插件路径相对该文件解析 — [publish.md L64](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L64)；类型定义 `patch: string | string[]`，"each relative to the declaring package root" — [package-manifest/src/types.ts L70-78](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/util/package-manifest/src/types.ts#L70-L78)
- **`dsh.bundle` 的全部字段**（`DshBundleManifest`）：`patch`（必需）、`platforms?: string[]`（Node 平台名或 `wsl`，省略=全平台）、`listedByDefault?: boolean`（GUI 列表中是否在未启用前就显示，省略=true）。`DshManifest` 另有 `manifestVersion?: 1`、`profile`、`client` — [types.ts L31-41, L70-84](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/util/package-manifest/src/types.ts#L31-L84)。`DshPackageManifest` 还定义了顶层 `icon`（SVG/PNG/JPEG/WebP，≤256 KiB，相对 manifest 目录）和 `engines.dsh` — [types.ts L8-29, L58-68](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/util/package-manifest/src/types.ts#L8-L68)
- **`platforms` 语义**：不支持的宿主跳过整层、保留选择、通过 `skippedBundles` 报告；`linux` 包含所有 Linux，`wsl` 只放行带 WSL 标记的 Linux；`macos` 这类别名非法 — [app-boot README L60](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L60)
- **没声明 `dsh.bundle` 的包**：仍能安装，但只是普通依赖，`dsh plugin` 打印警告且不激活任何层；适用于被插件包 import 的库 — [publish.md L64](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L64)；源码 `dsh: warning: ${name} declares no dsh.bundle — installed as a plain dependency, not a profile layer` — [plugin-manager/src/operations.ts L106-112](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/plugin-manager/src/operations.ts#L106-L112)
- **判定 bundle 的唯一标准**是 manifest 里 `dsh.bundle.patch !== undefined`（不读 JS）— [operations.ts L75-85](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/plugin-manager/src/operations.ts#L75-L85)
- **命名**：文档未规定命名规则；官方示例用 `dsh-hello-plugin`；内置用 `@deepseek-ai/dsh-*`。profile 中 `dsh.profile.bundles` 使用"installed package names" — [types.ts L80-84](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/util/package-manifest/src/types.ts#L80-L84)。行 `id` 是后续层覆盖的唯一抓手（见 Q4）。
- **内置 bundle 一览** [只读]：`packages/bundle/` 下有 `base`、`web-app`、`headless`、`sdk-app`、`sdk-minimal`、`acp-app`；它们的 `package.json` 都是 `"dsh": {"bundle": {"patch": "./cordis.patch.yml"}}`，`files` 含 `lib/index.js`、`cordis.patch.yml`、`lib/types/**`、`src`，`exports` 额外暴露 `./cordis.patch.yml` 与 `./package.json`，peer 为 `@deepseek-ai/cordis: workspace:~` — [bundle/base/package.json](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/bundle/base/package.json)、[bundle/acp-app/package.json](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/bundle/acp-app/package.json)
- **`web-app` 用多文件 patch**：`patch: ["./cordis.patch.yml","./presets/standard.patch.yml","./presets/ptc.patch.yml","./presets/minimal.patch.yml","./presets/cordis.patch.yml"]`，并把这些文件逐一列进 `files` — [bundle/web-app/package.json](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/bundle/web-app/package.json)
- **规模**：`base/cordis.patch.yml` 564 行，`web-app/cordis.patch.yml` 657 行，`acp-app` 23 行，`headless` 46 行 [已运行 `wc -l`]。`base` 的 patch 是"ONE insert over the empty profile root"，文件头注释说明：后续 bundle 与用户 patch 按 id 定位这些行，后写者胜；因为 patch 整体替换 `config`，按模式取值不同的行不放在 base，而由各模式 bundle 完整重述 — [bundle/base/cordis.patch.yml L1-20](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/bundle/base/cordis.patch.yml#L1-L20)
- **`web-app` 覆盖 base 的真实示例**：顶层 `- id: system-prompt` / `config: personaPrefix: …`、`- id: session-query-sqlite` / `config: {path: ':memory:', openAt: never}`（注释"each row below restates every key it owns"），然后才是 `- insert:` 新行 — [bundle/web-app/cordis.patch.yml L1-36](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/bundle/web-app/cordis.patch.yml#L1-L36)
- **一个小而真实的第三方式 bundle 样板**：`packages/subagent/subagent-codex` 的 patch 只有一个 insert，两行：`id: subagent-codex / name: '@deepseek-ai/dsh-subagent-codex'` 与 `id: tool-subagent-codex / name: '@deepseek-ai/dsh-tool-subagent' / config: {provider: codex, toolName: subagent_codex, maxDepth: provider-managed}` — [subagent-codex/cordis.patch.yml L1-9](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/subagent/subagent-codex/cordis.patch.yml#L1-L9)。说明一个 bundle 可以 insert **别的包**的插件并给它配置。
- `packages/*/*/package.json` 中含 `"bundle"` 字样的有 122 个 [已运行 grep；含 client UI 包等，非全部都是用户可装插件]。
- **"同一层"的拼接实现**：`bundlePatchFiles` 校验声明、`bundlePatchPaths` 解析为绝对路径，一层内按顺序拼接 patch 列表 — [app-boot README L52](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L52)

### Inferences
- 写教程时，"最小 bundle = package.json + cordis.patch.yml + 入口"可以直接用；但要提醒 `files` 必须包含 `cordis.patch.yml`（否则 npm 发布后 patch 丢失），内置包也都把它列进 `files`。
- subagent-codex 的写法说明 bundle 的本质是"一层配置"，不一定要自带插件代码。

### Gaps
- 没有找到官方的包命名约定（例如是否推荐 `dsh-plugin-*` 前缀）；只有 GitHub topic `dsh-plugin` 的建议（见 Q5）。

---

## Q2 profile：是什么、在磁盘哪里、它自己的 package.json / pnpm-workspace.yaml、插件如何装进去

### Takeaway
profile = `$DSH_HOME/profiles/<name>/`（默认 `~/.dsh/profiles/<name>`），是一个由 pnpm 管理的私有小项目：`package.json`（依赖 + `dsh.profile.bundles` 有序列表）、`cordis.patch.yml`（用户层，初始为 `[]`）、`pnpm-workspace.yaml`（`nodeLinker: hoisted`、`autoInstallPeers: false`），首次 `dsh plugin` 时自动生成；插件通过 pnpm 装进该目录的 `node_modules`，dsh 再把声明了 `dsh.bundle` 的依赖追加到 bundles 列表。

### Cited Findings
- **位置与组成**：profile 在 `$DSH_HOME/profiles/<name>`，含 `package.json`（out-of-tree 插件依赖 + `dsh.profile.bundles`）和 `cordis.patch.yml`（用户自己的 patch 层，应用在所有 bundle 层之后）— [app-boot/src/profile.ts L1-22](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/src/profile.ts#L1-L22)；[publish.md L68-75](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L68-L75)
- **目录名校验**：`resolveProfileDir` 拒绝空名、含 `/` `\`、`.`、`..` 和 `node_modules` — [profile.ts L165-172](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/src/profile.ts#L165-L172)
- **`initProfile` 写入的三个文件**（已存在则不碰，可重复执行）：`package.json` = `{name: "dsh-profile-<dir basename>", private: true, dependencies: {}, dsh: {profile: {bundles}}}`；`cordis.patch.yml` = 注释 + `[]`；`pnpm-workspace.yaml` = `packages: [.]`、`nodeLinker: hoisted`、`autoInstallPeers: false` — [profile.ts L174-217](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/src/profile.ts#L174-L217)
- **为什么 hoisted + 不自动装 peer**：源码注释"The hoisted linker gives out-of-tree plugins a flat node_modules whose missing peers (cordis and friends) use the runtime resolution, so every plugin shares the installation's single cordis instance instead of a duplicate. pnpm ≥10 reads its settings from pnpm-workspace.yaml, not .npmrc." — [profile.ts L180-189](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/src/profile.ts#L180-L189)
- **初始 bundle 列表**：非内置名 → `DEFAULT_PROFILE_BUNDLES = ['@deepseek-ai/dsh-base']` — [official-bundles/src/bundles.ts L48](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/util/official-bundles/src/bundles.ts#L48)；内置模板 `acp/web/headless/sdk` = base + 对应 app bundle，`sdk-minimal` 只有自己的 bundle — [bundles.ts L10-25](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/util/official-bundles/src/bundles.ts#L10-L25)；[cli reference L17](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L17)
- **两种创建方式**：`dsh --profile <name> --from-default-profile <template>` 从内置模板创建/重置；`dsh plugin` 初始化 base-backed profile 并维护 bundle 列表；"You never write a profile manifest by hand" — [publish.md L75](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L75)；其他不存在的 profile 名直接启动会报错并提示 `dsh plugin --profile <name> add <package>` — [cli reference L17](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L17)
- **启动**：`dsh <name>` 是 `dsh --profile <name>` 的简写；`plugin` 是保留的子命令名，名为 plugin 的 profile 要用 `dsh --profile plugin` — [cli reference L13](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L13)
- **模块解析"双锚点"**：bundle 名先从 dsh 安装本身解析，再从 profile 目录解析；profile `node_modules` 里 pnpm 管理的条目优先 — [profile.ts L16-20](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/src/profile.ts#L16-L20)；"In-box bundle names always resolve from the dsh installation itself; pnpm manages only out-of-tree packages" — [publish.md L136](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L136)
- **profile 里的其他文件**：版本豁免存于 profile 自己的 `compatibility.json`（不写进 package.json）— [app-boot README L58](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L58)；`sanitizeProfile` 可把 `cordis.patch.yml` 改名为 `.bak-<ms时间戳>` 并恢复 bundle 列表（Desktop 原生崩溃恢复用）— [app-boot README L73](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L73)
- **bundle 加载失败不致命**：bundle 解析、manifest、patch 加载失败会跳过该 bundle（不修改选择），列入 `skippedBundles` 并在每次启动打印一次；profile 与用户 patch 的错误仍会让启动失败 — [app-boot README L52](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L52)
- **[已运行] 首次 `dsh plugin add` 的实际产物**：`dsh plugin --profile demo add ./hello-plugin` 输出 `dsh: initialized profile demo at …/home/profiles/demo`，`Done in 189ms using pnpm v10.34.9`；目录里出现 `cordis.patch.yml`、`node_modules/`、`package.json`（权限 `-rw-------`）、`.plugin-manager/`（含 `logs/`）、`pnpm-lock.yaml`、`pnpm-workspace.yaml`。`package.json` 内容：
  ```json
  { "name": "dsh-profile-demo", "private": true,
    "dependencies": { "dsh-hello-plugin": "link:/…/work/hello-plugin" },
    "dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base", "dsh-hello-plugin"] } } }
  ```
  `pnpm-workspace.yaml` 与源码模板一致（`packages: [.]`、`nodeLinker: hoisted`、`autoInstallPeers: false`），`cordis.patch.yml` 为注释 + `[]`。与 [publish.md L85-103](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L85-L103) 的示例一致。

### Inferences
- profile 本质是"一个 pnpm 项目 + dsh 元数据"，所以 `pnpm why`、`pnpm update` 等都能在其中用（见 Q3）。
- `.plugin-manager/logs/operation-*/pnpm.log` 是排查安装失败的地方（dsh 失败时会打印这个路径）。

### Gaps
- 未验证 Desktop 保留 profile（`desktop`）的初始化细节，只读到"必须先打开一次 Desktop 再完全退出"的报错文案 — [apps/cli/src/plugin.ts L11-15](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/src/plugin.ts#L11-L15)。

---

## Q3 `dsh plugin add/remove/update`：接受的 specifier、写了什么、TypeScript 包的 `prepare`、pnpm `allowBuilds`/`onlyBuiltDependencies`、安全含义、commit 锁定

### Takeaway
`dsh plugin --profile <name> <args…>` 是 pnpm 的薄包装：在 profile 目录里运行 pnpm（任何 pnpm 动词都可），相对路径先按调用目录锚定，成功后按"依赖是否声明 `dsh.bundle`"对 `dsh.profile.bundles` 做对账；安装前后都做 `engines.dsh`/peer 兼容检查。git 安装拿到的是源码，要靠作者的 `prepare` 脚本构建，而 pnpm 默认拦截 git 依赖的 `prepare`，用户必须把 pnpm 打印的**完整键（包名@git URL#sha）**加进 profile 的 `pnpm-workspace.yaml`——这等于授权该包在安装时在本机、沙箱之外执行代码。实测发现文档的 `allowBuilds: { dsh-hello-plugin: true }` 写法在 pnpm 10 与 11 上都不生效，且 pnpm 10 用的键名是 `onlyBuiltDependencies`。

### Cited Findings
**命令形态与转发**
- `dsh plugin --profile <name> <args...>`：profile 不存在时先初始化（内置模板，或其他名只放 `@deepseek-ai/dsh-base`），然后把 `<args...>` 原样转发给 `pnpm`，工作目录为 profile 目录；`add`、`remove`、`why`、`update` 等所有 pnpm 动词都可用；pnpm 必须在 PATH 上 — [cli reference L85](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L85)；找不到 pnpm 时退出码 127 并提示 `dsh: pnpm was not found; install pnpm and make it available on PATH.` — [apps/cli/src/plugin.ts L99](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/src/plugin.ts#L99)
- `--profile <name>` 必须放在被转发参数之前；`-h/--help` 在转发参数之前打印 dsh 自己的帮助 — [cli reference L87](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L87)
- **相对路径锚定**：`.`、`../plugin` 及其 `file:`/`link:` 形式先相对**调用目录**解析，所以在插件 checkout 里 `add .` 装的是该 checkout 而不是 profile 本身 — [cli reference L85](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L85)；实现 `anchorPathSpec` 正则 `^(?:file|link:)?(\.{1,2}(?:[/\\].*)?)$` — [operations.ts L64-73](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/plugin-manager/src/operations.ts#L64-L73)
- **dsh 自有子命令**（不转发 pnpm）：`allow-version <pkg@ver> --dsh-version <exact> --accept-risk`、`revoke-version …`、`version-exemptions` — [apps/cli/src/plugin.ts L17-63](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/src/plugin.ts#L17-L63)
- **文档中的 specifier 示例**：npm 名 `@deepseek-ai/dsh-subagent-codex`（可一次多个）、本地目录 `./hello-plugin`、`github:deepseek-harness/turtle-ui`、`github:you/hello-plugin#<sha>`、tarball `./hello-plugin-0.1.0.tgz` — [cli reference L92-103](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L92-L103)；[publish.md L82, L166, L181-186](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L161-L186)。插件索引里的 git 版本被安装为 `git+<url>.git#<commit>`，子目录用 `&path:/<path>` — [plugin-market.md L436](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/guide/plugin-market.md#L436)

**写了什么（bundle 对账）**
- 每次成功运行后对账：新依赖若解析到声明了 `dsh.bundle.patch` 的包就加入层栈（所以 `update` 后新获得声明的包也会被激活），无 bundle 的依赖保持普通依赖并一次性警告，已移除的依赖离开层栈 — [cli reference L85](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L85)
- 实现 `reconcile()`：保留旧列表中仍为依赖且仍是 bundle 的项；对新增依赖检查 `dsh.bundle` → `assertBundlePlatform` → 预先 `loadOverlayPatches` 解析每个 patch 文件（YAML 坏了会在安装时就报错）→ 追加到末尾；列表无变化则不写文件；写入用原子写、权限 `0o600` — [operations.ts L87-128](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/plugin-manager/src/operations.ts#L87-L128)
- **安装前预检**：对路径 spec 直接读其 `package.json`；对 registry spec 先 `pnpm view <spec> name version engines peerDependencies --json`；git/tarball 只能装完后检查 — [operations.ts L157-200](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/plugin-manager/src/operations.ts#L157-L200)
- **[已运行] 普通依赖**：`dsh plugin --profile demo add ./plain`（无 dsh 字段）→ pnpm 成功，随后 `dsh: warning: plain-lib declares no dsh.bundle — installed as a plain dependency, not a profile layer`；bundles 列表不变。
- **[已运行] 版本不兼容**：`add ./old`（`engines.dsh: "<0.2.0"`）→ `dsh: installation rejected: Plugin old-bundle@1.0.0 is incompatible with dsh 0.2.2-rc.5: requirements {"engines.dsh":"<0.2.0"}. Running it may cause crashes or data loss. …`、`dsh: nothing was installed.`、`dsh: to accept the risk, run: dsh plugin --profile demo allow-version old-bundle@1.0.0 --dsh-version 0.2.2-rc.5 --accept-risk`、`dsh: plugin command failed; diagnostics: …/.plugin-manager/logs/operation-PXzDjZ/pnpm.log`，exit 1。
- **[已运行] remove**：`dsh plugin --profile demo remove plain-lib dsh-hello-plugin` → `dependencies` 被清空、`bundles` 回到 `["@deepseek-ai/dsh-base"]`，与 [publish.md L118](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L118)"removes both the dependency and the layer"一致。

**本地 link 安装与 peer 依赖（实测踩坑）**
- 文档：linked checkout 保留自己的 `node_modules`；需要与宿主共享实例的 dsh 包同时写进 `peerDependencies` 和 `devDependencies`；在 runtime resolution 中存在的 peer 使用安装方的副本；独立版本的第三方依赖与无状态 dsh 工具放 `dependencies` — [publish.md L105](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L105)
- **[已运行]** 我的 hello-plugin `index.js` 里 `import Schema from '@deepseek-ai/schemastery'`，但 package.json 未声明它、目录也没有 node_modules。`add` 成功，`--dump-config` 正常，但启动时：`hello (dsh-hello-plugin): failed to import: Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@deepseek-ai/schemastery' imported from …/hello-plugin/index.js`，`dsh: warning: 1 entry did not activate`（其余插件照常启动）。给 package.json 加 `"peerDependencies": {"@deepseek-ai/schemastery": "*"}` 后不需重装，直接重启即输出 `[hello-plugin] loaded: from-profile`。即：**未声明的宿主包不会被"顺手"解析到；声明为 peer 后才走 runtime resolution 共享宿主副本**。

**git 安装、`prepare`、构建授权**
- git 安装取的是源码不是构建产物；没人运行 `build`，TS 包没有 `lib/` 会加载失败。作者需提供 `prepare` 脚本（pnpm 在 git 安装后运行），必须自包含、不能依赖兄弟 monorepo checkout；可用专门的 tsdown 配置不带 project references、不做类型检查地转译 `src/` — [publish.md L161-171](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L161-L171)
- 用户侧：pnpm ≥10 在显式允许前拒绝运行 git 依赖的 `prepare`，第一次 `add` 失败；把 pnpm 打印的**确切包键**复制进 profile 的 `pnpm-workspace.yaml` 的 `allowBuilds` 后重跑 — [publish.md L172-179](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L172-L179)；[cli reference L107](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L107)
- **安全含义**：把该授权视为"permission to execute the package's code on your machine at install time, outside any sandbox the agent runs under"；只允许信任的源，并锁定 commit（`github:you/hello-plugin#<sha>`）防止之后的 push 悄悄改变执行内容 — [publish.md L181](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L181)。版本检查"are not a sandbox against malicious package code" — [app-boot README L54](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L54)
- 不想让用户授权：发布到 npm（`pnpm publish` 时构建好 `lib/`）或分发 `pnpm pack` 的 tarball；"Installing a built tarball or a local checkout needs no allowance" — [publish.md L183-186](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L183-L186)；[cli reference L107](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L107)
- dsh 的失败提示是靠正则识别 git spec（`^git\+|^github:|\.git(?:#|$)`）后追加打印的 — [apps/cli/src/plugin.ts L104-106](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/src/plugin.ts#L104-L106)。GUI 的 Plugin Manager 能解析 pnpm 11 格式的 `ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED … allowBuilds:\n  <key>: true` 输出来提供"批准"按钮，并拒绝记录含密码/HTTP 用户名的 git URL 键 — [plugin-manager/src/build-approval.ts L8-26](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/plugin-manager/src/build-approval.ts#L8-L26)
- **[已运行] 实验：本地 git 仓库 + prepare**。构造 `dsh-git-plugin`：`scripts.prepare: "node build.mjs"`（把 `src/index.js` 复制到 `lib/index.js`），`.gitignore` 忽略 `lib`，提交得到 sha `b0b1e5f…`。
  - pnpm **10.34.9**：`dsh plugin --profile demo add "git+file://…/gitplug#b0b1e5f…"` → `ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED … is not in the "onlyBuiltDependencies" allowlist. … onlyBuiltDependencies:\n  - "dsh-git-plugin@git+file:///…/gitplug#b0b1e5f…"`，随后 dsh 提示"add the exact key pnpm printed above under **allowBuilds** in …/pnpm-workspace.yaml"。
    - 写 `allowBuilds: { dsh-git-plugin: true }` → 仍失败。
    - 写 `onlyBuiltDependencies: [dsh-git-plugin]`（仅包名）→ 仍失败。
    - 写 `onlyBuiltDependencies: ["dsh-git-plugin@git+file:///…/gitplug#b0b1e5f…"]`（pnpm 打印的完整键）→ 成功，`node_modules/dsh-git-plugin/lib/index.js` 已由 prepare 生成，bundles 追加 `dsh-git-plugin`。
  - pnpm **11.28.5**（新 profile `p11`）：首次失败信息改为 `… is not in the "allowBuilds" allowlist. … allowBuilds:\n  dsh-git-plugin@git+file:///…/gitplug#b0b1e5f…: true`；写 `allowBuilds: { dsh-git-plugin: true }`（仅包名）→ 仍失败；写完整键 `"dsh-git-plugin@git+file:///…#b0b1e5f…": true` → `Done in 509ms using pnpm v11.28.5`，bundles 追加；启动 profile 打印 `[git-plugin] loaded`。
  - dsh 用的是 PATH 上的 pnpm（`using pnpm v10.34.9` / `v11.28.5` 随 PATH 而变），并不自带固定版本。

### Inferences
- 允许列表的键包含 git URL 与 commit sha，所以**换 commit 就要重新授权**——这本身就是"锁定 commit"的安全机制：授权绑定在具体源码版本上。
- `prepare` 在 profile 目录的 pnpm 安装过程中执行，不经过 dsh 的 agent 沙箱，这是"安装时代码执行"风险的来源。
- 对教程读者最实用的建议：GitHub 分发 TS 插件时要么提交构建产物/发布 npm，要么在 README 写明需要复制 pnpm 打印的完整键。

### Gaps
- 没有用真实 `github:owner/repo#sha` 测试（离线用 `git+file://` 代替）；`github:` 简写下 pnpm 打印的键的确切形态（大概率是 `name@git+https://…#sha` 或 `name@github:…`）未验证。
- 未测试 `dsh plugin update`；仅依据文档"`update` that gains the declaration activates it"。
- 未确认 pnpm 10 系列中从哪个版本起开始支持/改名为 `allowBuilds`（仓库 `packageManager` 是 pnpm 11.28.5，测试的 10.34.9 不认 `allowBuilds`）。

---

## Q4 配置分层：几层、顺序、合并/patch 语义、插件 `Config` 值来源、用户如何覆盖 bundle 默认值、如何禁用插件

### Takeaway
官方文档列出的是 **4 类层**（各 bundle patch 按列表顺序 → profile 的 `cordis.patch.yml` → `$DSH_HOME/cordis.patch.yml` → 各 `--patch`，按 argv 顺序），从一个空根开始叠加；"五层"的说法是把 `dsh-base` 和其他已安装 bundle 拆成两层，属于通俗化——实际层数不固定（每个 bundle 一层、`--patch` 可多次），源码里在最后还会追加一个代码派生的遥测 patch。按 `id` 的覆盖 patch 中，写出的 `config` 整块替换（不深合并），但未写出的字段（如只写 `disabled: true`）不会清掉原 `config`。插件最终拿到的 `config` = 合成后该行的 `config` 再经插件导出的 Schemastery schema 校验并填默认值。

### Cited Findings
- **官方顺序（4 类）**：空根上依次应用 1) `dsh.profile.bundles` 中每个 bundle 的 patch（base 先，之后按添加顺序）；2) profile 自己的 `cordis.patch.yml`；3) home 级 `$DSH_HOME/cordis.patch.yml`（所有 profile 共享的本机偏好）；4) 每个 `--patch <path>`，按 argv 顺序 — [publish.md L120-127](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L120-L127)；同样表述见 [cli reference L13](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L13)（并强调 home 层"outranks the per-profile layer"）
- **App 参数不是 patch 层**："App arguments are not another patch layer"；命令行参数通过 `cmdlineArgs` 服务 + 行内 `!!js ctx.xxx ?? 默认` 表达式生效 — [publish.md L129, L138-159](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L129-L159)；用户 patch 用字面量替换整个 `config` 会去掉这类运行时读取 — [cli reference L32](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L32)
- **源码的实际拼接**：`readProfilePatches` = `[...readBundlePatches, ...profile patches, ...home patch, ...overlayPaths.flatMap(loadOverlayPatches)]`，之后若有遥测禁用环境变量且存在遥测行，再 `push(telemetryPatch)` — [app-boot/src/profile-context.ts L75-86](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/src/profile-context.ts#L75-L86)；文件头注释称"then any launcher layers (`--patch` files and flag-derived patches)" — [profile.ts L11-14](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/src/profile.ts#L11-L14)
- **"五层"出处与核对**：本仓库已有文章 `deepseek/dsh_plugin_guide.md` 第 320-349 行用"五层"：①dsh-base ②已安装 bundle ③profile patch ④home patch ⑤`--patch`。与官方 4 类等价（官方把①②合为"bundle patches"）。前一轮研究笔记 `research_notes/dsh 服务事件 配置与插件实战/config_composition_hmr.md` L1058 已指出"五层只是通俗说法"。本次源码核对支持这一点（见上）。
- **合并语义**：后面的层按行胜出；patch 替换目标行的**完整 `config`**，不深合并，也可插入新行 — [publish.md L131-134](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L131-L134)；[cli reference L13](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L13)。对 bundle 作者的两个后果：覆盖更早层的行时须重述所有键；用户可在 profile patch 中覆盖你的行，所以默认值放 schema — [publish.md L133-134](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L133-L134)
- **用户层的注意事项**：patch 指向不存在的 entry 打印 stderr 警告；空文件或只有注释的文件会让启动失败——要禁用该层写 `[]` — [app-boot README L65](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L65)
- **插入行的路径解析**：insert 行里的绝对路径、`./`/`../` 相对路径转为 file URL（相对 patch 文件）；按 id 的覆盖行的 name 断言和替换 `config` 保持字面量 — [app-boot README L69](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L69)
- **Config 值来源**：插件导出 `Config` 类型和同名 Schemastery schema，默认值写在 schema 字段上；加载时 Cordis 用 schema 校验并填默认值；不能导出普通对象作为 `Config`（不实现 Standard Schema）— [config.md L5-45](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/config.md#L5-L45)；非法配置让加载失败 — [config.md L74](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/config.md#L74)
- **查看合成结果**：`--dump-default-config` 只打印 bundle 层；`--dump-config` 加上 profile、home 与 `--patch` 层，带注释标明每行来源与改过它的 overlay；`!!js` 不求值；找不到目标的 patch 报到 stderr；dump 不运行 app 命令行 provider — [cli reference L55](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L55)。`--dump-config-schema` 输出 JSON Schema 2020-12，`x-cordis.entries` 列出每行 schema 状态；它会 import 插件模块，只对可信 profile 运行 — [cli reference L60-70](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L60-L70)
- **[已运行] 分层实验**（hello-plugin 的 bundle patch 写 `config: {greeting: from-bundle}`，schema 默认 `Hello`）：
  - `dsh --profile demo --dump-config` 输出 `# == @deepseek-ai/dsh-base`（第 1 行）…`# == dsh-hello-plugin`（第 372 行）后接 `- id: hello / name: dsh-hello-plugin / config: greeting: from-bundle`。
  - profile patch 写 `- id: hello / config: {greeting: from-profile}`，home patch 写 `{greeting: from-home}` → dump 头变成 `# == dsh-hello-plugin, patched by …/profiles/demo/cordis.patch.yml, …/home/cordis.patch.yml`，`greeting: from-home`（**home 胜过 profile**，与文档一致）。
  - 再加 `--patch ./off.yml`（内容 `- id: hello / disabled: true`）→ 行变成 `config: {greeting: from-home}` + `disabled: true`：**只写 `disabled` 的覆盖不会清掉已有 `config`**，即"整块替换"只针对写出的 `config` 键本身。
  - `--dump-default-config` 只显示 `greeting: from-bundle`。
  - `--patch ./bad.yml`（`- id: nonexist`）→ stderr `dsh: [<path>/bad.yml] patch: entry "nonexist" not found`，dump 照常输出。
  - 把 home patch 改回 `[]` 后实际启动：输出 `[hello-plugin] loaded: from-profile`（实际运行值与 dump 一致）。
  - `--dump-config-schema` 中该行：`{'path': '/99', 'id': 'hello', 'name': 'dsh-hello-plugin', 'status': 'schema', 'configRef': '#/$defs/config70'}`。
- **如何禁用插件**：在 profile 或 home 的 `cordis.patch.yml` 写 `- id: <行id>` + `disabled: true`（行级、可逆、不卸载包）；`disabled` 接受布尔、null 和 `!!js` 表达式 — [cli reference L64](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/reference/README.md#L64)；或 `dsh plugin --profile <p> remove <pkg>` 同时移除依赖与层 — [publish.md L118](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L118)。base 自身也用这种方式默认关掉行，例如 `id: tool-plugin-manager … disabled: true` — [bundle/base/cordis.patch.yml L15-18](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/bundle/base/cordis.patch.yml#L15-L18)
- **HMR**：启用的 `dsh-hmr` 监视 profile manifest、两个用户 patch 文件、每个 `--patch` 文件，并重读有序 bundle 层；未启用时需重启（Config Editor 保存例外）— [app-boot README L67](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L67)；内置 `web` 模板开启热重载，其他模板只在启动时应用 — [app-boot README L52](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L52)

### Inferences
- 教程可以写"四类层（常被说成五层）"，并画出：base → 其他 bundle（按安装顺序）→ profile patch → home patch → `--patch…` →（代码派生的遥测 patch）。
- "整块替换 `config`" 需要精确表述：针对的是被覆盖行里写出的 `config` 字段；`disabled` 等其他字段各自覆盖。已有文章的"整行 config 替换"表述基本正确，但"整行"容易被误解为 name/disabled 也被替换。前一轮笔记 E6 实验说明：用 `insert` 再写同一个 id 才是整行替换。

### Gaps
- 未测试多个 bundle 同时覆盖同一行时的顺序（按文档应是 bundles 列表顺序，后者胜）。

---

## Q5 发布到 npm：包含什么、针对 dsh/cordis peer 的版本声明

### Takeaway
发布时把 `cordis.patch.yml`（以及多文件 patch 的每个文件）和构建好的 `lib/` 列进 `files`；兼容性优先用 `engines.dsh` SemVer 范围声明（它单独决定、忽略 peer），没有 `engines.dsh` 时才看所有 `@deepseek-ai/dsh*` peer 范围。官方包发布时把 `workspace:*` 变成**精确版本** peer，第三方若照抄会把插件锁死在某个 dsh rc 上。

### Cited Findings
- **`engines.dsh`**：如 `"engines": { "dsh": ">=0.2.0 <0.3.0" }`；安装和 profile 启动都会拒绝范围不满足的 bundle；没有 `engines.dsh` 时检查 `@deepseek-ai/dsh*` peer 范围 — [publish.md L66](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L66)
- **精确规则**：有 `engines.dsh` 时只看它，忽略 `peerDependencies`；否则 `@deepseek-ai/dsh` 与 `@deepseek-ai/dsh-*` 的每个 peer 范围都必须匹配；prerelease 参与匹配；两者都没有则不设限；空/非法范围（含 `engines.dsh` 中的 `workspace:`）视为不兼容；非对象 `engines` 或非字符串 `engines.dsh` 视为 manifest 损坏 — [app-boot README L54](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L54)
- **检查时机**：bundle 不是行，`loadProfileDirectory` 在启动和每次重组时检查每个 bundle 的要求；不兼容且无豁免的 bundle 像不可读的一样被跳过并列入 `skippedBundles`；被拒绝的行在启动器自己的副本中变成 `disabled: true`，不改用户文件 — [app-boot README L56](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L56)
- **豁免**：精确 `包@版本` → 精确 dsh 版本列表，存于 profile 的 `compatibility.json`；升级插件或 dsh 都不继承 — [app-boot README L58](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/app-boot/README.md#L58)；CLI 命令见 [apps/cli/src/plugin.ts L17-63](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/src/plugin.ts#L17-L63)
- **注意 prerelease**：当前 latest 是 `0.2.2-rc.5`。我的实验 bundle 写 `engines.dsh: ">=0.2.0 <0.3.0"`，在 dsh 0.2.2-rc.5 上被接受 [已运行]（rc 版本落在该范围内被接受，说明检查不是 npm 默认的"排除 prerelease"语义，与"Prereleases participate in range matching"一致）。
- **官方包发布后的 peer 形态** [已运行 `npm view @deepseek-ai/dsh-subagent-codex@0.2.2-rc.5`]：`peerDependencies` 为 `@deepseek-ai/cordis: "~4.1.0"`，`@deepseek-ai/dsh-llm`/`dsh-session`/`dsh-timeout`/`dsh-subagent`/`dsh-subprocess` 全是精确 `"0.2.2-rc.5"`；源码中对应写法是 `workspace:~`（cordis）与 `workspace:*`（dsh-*）— [subagent-codex/package.json](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/subagent/subagent-codex/package.json)。该包没有 `engines.dsh`，因此只能在恰好 0.2.2-rc.5 的 dsh 上通过检查。
- **peer 与 dev 双写**：需与宿主共享实例的 dsh 包同时放 `peerDependencies` 和 `devDependencies`（dev 供类型检查/独立测试），第三方依赖放 `dependencies` — [publish.md L105](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L105)。subagent-codex 正是这样：peer 6 个、dev 17 个（含 `dsh-app-boot`、`dsh-loader-smoke` 测试用）、`dependencies` 含 `@deepseek-ai/schemastery`、`@openai/codex: 0.153.4` 等 — [subagent-codex/package.json](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/subagent/subagent-codex/package.json)
- **`files` 内容**（官方包惯例）：`lib/index.js`、`cordis.patch.yml`、`lib/types/**/*.d.ts(.map)`、`lib/**/*.js.map`、`src`，有图标/本地化的再加 `icon.svg`、`locale/*.json`；`publishConfig.access: public` — [subagent-codex/package.json](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/subagent/subagent-codex/package.json)；[bundle/base/package.json](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/bundle/base/package.json)
- **可发现性**：给仓库加 GitHub topic `dsh-plugin` — [README.md L46](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/README.md#L46)；或发布插件索引 JSON（实验性"Custom plugin indexes" bundle 的 Discover 标签页），索引版本的 `engines.dsh` 应与包自己的一致，安装时 DSH 会再检查一次 — [plugin-market.md L302-436](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/guide/plugin-market.md#L302-L436)。参考生成器只收录声明了 `dsh.bundle`、未 deprecated、非 private 的 npm 版本；git 仓库按 `vX.Y.Z`/`X.Y.Z`/`<name>@X.Y.Z` tag 收录 — [plugin-market.md L397](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/guide/plugin-market.md#L397)

### Inferences
- 第三方插件应该写 `engines.dsh`（例如 `>=0.2.0 <0.3.0`），而不是照搬官方包的精确 peer；peer 依赖写宽范围（如 `*` 或 `~4.1.0`）只用来让运行时共享宿主实例。因为有 `engines.dsh` 时 peer 范围不参与兼容判定，宽 peer 不会造成误拒。
- dsh 目前处于 0.2.x 预览，`<0.3.0` 上限是合理的防护。

### Gaps
- 没有实际 `npm publish`（不应向公共 registry 发布测试包）；`pnpm pack` 生成的 tarball 安装路径未实测，只依据文档。

---

## Q6 文档与源码/实测的不一致

### Takeaway
主要有四处：`allowBuilds` 示例写法实测不可用、pnpm 10 与 11 的键名不同、"五层"与官方"4 类层"的说法差异、以及 linked checkout 对 peer 声明的硬性要求在入门示例中没有体现。

### Cited Findings
- **`allowBuilds` 示例 vs 实测**：文档示例 `allowBuilds:\n  dsh-hello-plugin: true` — [publish.md L174-177](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L174-L177)。实测 [已运行] 只写包名在 pnpm 10.34.9 与 11.28.5 下都不放行；必须写 pnpm 打印的完整键 `dsh-git-plugin@git+file:///…#<sha>`。文档正文确实说了"copy the exact package key pnpm printed"，示例本身是简化，容易误导。
- **pnpm 10 vs 11**：文档和 dsh 的错误提示都说"pnpm ≥10 … allowBuilds" — [publish.md L172](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/user/develop/basic/publish.md#L172)、[apps/cli/src/plugin.ts L105](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/apps/cli/src/plugin.ts#L105)。实测 pnpm 10.34.9 的报错要求的是 `onlyBuiltDependencies`（列表形式），且 `allowBuilds` 不生效；pnpm 11.28.5 才要求 `allowBuilds`（映射形式）。dsh 在 pnpm 10 下的提示因此与 pnpm 自己的提示矛盾。GUI 侧 `build-approval.ts` 的正则也只识别 `allowBuilds:` 格式 — [build-approval.ts L14](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/plugin-manager/src/build-approval.ts#L14)。仓库 `packageManager` 是 `pnpm@11.28.5` — [package.json](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/package.json)。
- **"五层"**：见 Q4；官方文档写的是 4 类层，源码另有遥测派生 patch。
- **入门示例缺 peer**：publish.md 的 `index.js` 不 import 任何东西所以能跑；一旦按 config.md 的写法 `import Schema from '@deepseek-ai/schemastery'`，就必须在 package.json 声明 peer，否则启动时 `ERR_MODULE_NOT_FOUND`（[已运行]，见 Q3）。文档 L105 有原则性说明但示例 package.json 里没有 `peerDependencies`。
- **Node 版本**：源码根要求 `^24.22.3 || >=26.13.1` — [package.json](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/package.json)；已发布的 `@deepseek-ai/dsh@0.2.2-rc.5` 的 `engines.node` 为 `>=24.9.0` [已运行 读取安装后的 package.json]，在 Node 24.22.2 上安装和运行均无问题。
- **全局 npm 安装的 install-scripts 警告** [已运行]：npm 11.19.1 安装 dsh 时提示 5 个包（`@deepseek-ai/dsh-subprocess-local` postinstall、`koffi`、`node-pty`、`@google/genai`、`protobufjs`）的安装脚本"not yet covered by allowScripts"。本实验未授权这些脚本，`dsh plugin` 与 base profile 启动仍正常（可能影响 PTY/终端等功能，未验证）。

### Inferences
- 教程里写 git 安装时，建议直接展示 pnpm 实际打印的报错样式，并让读者"复制整行键"，同时注明 pnpm 10 用 `onlyBuiltDependencies`、pnpm 11 用 `allowBuilds`。

### Gaps
- 未向上游确认这些差异是否已有 issue。

---

## 附：完整实验步骤（可复现）[已运行]

```sh
S=<scratch>/dsh-try
npm install -g --prefix $S/prefix @deepseek-ai/dsh@0.2.2-rc.5 pnpm@10     # dsh 0.2.2-rc.5, pnpm 10.34.9
export PATH=$S/prefix/bin:$PATH DSH_HOME=$S/home

# hello-plugin/package.json
{ "name": "dsh-hello-plugin", "version": "0.1.0", "type": "module", "main": "index.js",
  "files": ["index.js", "cordis.patch.yml"],
  "engines": { "dsh": ">=0.2.0 <0.3.0" },
  "peerDependencies": { "@deepseek-ai/schemastery": "*" },      # 没有这一行启动时 import 失败
  "dsh": { "bundle": { "patch": "./cordis.patch.yml" } } }
# hello-plugin/index.js
import Schema from '@deepseek-ai/schemastery'
export const name = 'hello-plugin'
export const Config = Schema.object({ greeting: Schema.string().default('Hello') })
export function apply(ctx, config) { console.log('[hello-plugin] loaded:', config.greeting) }
# hello-plugin/cordis.patch.yml
- insert:
    - id: hello
      name: dsh-hello-plugin
      config:
        greeting: from-bundle

dsh plugin --profile demo add ./hello-plugin        # initialized profile demo … Done in 189ms using pnpm v10.34.9
dsh --profile demo --dump-config                    # … "# == dsh-hello-plugin" + greeting: from-bundle
echo '- id: hello\n  config:\n    greeting: from-profile' > $DSH_HOME/profiles/demo/cordis.patch.yml
timeout 30 dsh --profile demo </dev/null            # [hello-plugin] loaded: from-profile
dsh plugin --profile demo remove dsh-hello-plugin   # 依赖与层一起移除
```
注：`dsh --profile demo`（仅 base + 插件）启动后进入常驻状态，实验用 `timeout` 结束（退出码 124）。
