---
title: 给 dsh 写插件：从 `apply(ctx)` 到打包安装
kicker: 插件开发
deck: dsh 插件就是一个 Cordis 模块，交付时再包成声明了 dsh.bundle 的 npm 包。本文按官方教程的顺序走完“写插件 → 注册工具 → 挂钩子 → 打包 → 安装 → 叠配置”，每一步都区分仓库原文和实际运行结果，并列出文档与实际行为不符的地方：被拒绝的调用照样走完 post-execute 和 result，配置是四类层而不是五层，构建授权必须写 pnpm 打印的完整键，漏写 peer 的插件能装上却在启动时 import 失败。
meta:
  - deepseek-ai/deepseek-harness @ 4b399c3（2026-11-22）
  - '@deepseek-ai/dsh 0.2.2-rc.5 CLI · 仓库 0.2.3-alpha.2'
  - '@deepseek-ai/cordis 4.2.0-alpha.1'
  - pnpm 10.34.9 与 11.28.5 实测
footer: 来源：github.com/deepseek-ai/deepseek-harness（commit 4b399c3）的 README、docs/user/develop（basic 的 index / tool / config / publish，framework 的 index / events）、docs/cookbook（extension-cookbook、adding-a-tool）、packages/core/tools（README、src/contracts.ts、src/pipeline.ts、src/events.ts）、packages/boot（app-boot、plugin-manager）、apps/cli（reference/README、src/plugin.ts）、vendor/cordis/src。标“仓库原文”的代码按原文摘录；标“本文实测”的输出原样贴出，临时目录路径缩写为 &lt;scratch&gt;：工具和钩子部分在一个用 npm 包（0.2.3-alpha.2，带 cordis 4.2.0-alpha.1）拼成的裸宿主上运行，打包和安装部分用全局安装的 @deepseek-ai/dsh 0.2.2-rc.5，在 Node 24.22.2、pnpm 10.34.9 和 11.28.5 下运行。没有运行的：官方的 pnpm dsh web --patch 源码教程路径（完整源码构建因磁盘不足报 ERR_PNPM_ENOSPC）、综合练习插件作为整体、npm publish 和 tarball 安装。标“示意（未运行）”的代码是本文写的。dsh 仍是开发者预览版，接口可能变化。
index: index.html
updated: 2026-11-23
created: 2026-11-23 18:04
---

dsh（DeepSeek Harness，npm 包 `@deepseek-ai/dsh`）自称 “everything-is-a-plugin”：模型适配器、工具注册表、会话日志乃至 agent 循环本身都是 Cordis 插件。所以给 dsh 写插件，就是写一个 Cordis 插件，再把它包成一个声明了 `dsh.bundle` 的 npm 包，用 `dsh plugin add` 装进某个 profile。

本文按官方教程的顺序走一遍，每一步都标出证据来源。**仓库原文**：代码或说法摘自 commit `4b399c3`，标注路径和行号。**本文实测**：本文实际运行过，输出原样贴出。**示意（未运行）**：本文写的代码，没有作为整体运行。插件的生命周期细节见[dsh 插件的生命周期与副作用](dsh_lifecycle_and_effects.html)，服务、事件分发模式和 HMR 见[dsh 的服务、事件、配置与插件实战](dsh_services_events_config_plugin.html)，本文只讲写插件和装插件需要的部分。

- **`apply` 何时运行只由 `inject` 决定**：服务全部就绪、`Config` 同步校验通过才调用；依赖消失后插件回到 PENDING，依赖回来 `apply` 再跑一次
- **一次工具调用的实测顺序**：`tools/pre-execute` → `ctx.tools.guard()` → `tools/execute` 包裹层（参数校验在里面）→ 工具体 → `tools/post-execute` → `tools/result`；被拒绝的调用仍会走到 post-execute 和 result
- **bundle 最少三个文件**：`package.json`（含 `dsh.bundle.patch`）、`cordis.patch.yml`、入口 JS；`dsh plugin` 只是在 profile 目录里调用 PATH 上的 pnpm，再对账 bundle 列表
- **配置是四类层**：各 bundle patch → profile patch → home patch → 各 `--patch`；“五层”只是把 `dsh-base` 单独数成一层
- **三个新手最容易踩的坑**：构建授权要写 pnpm 打印的完整键（含 git URL 和 sha）；import 了宿主包却不声明 peer，启动时 `ERR_MODULE_NOT_FOUND`；照抄官方包的精确 rc peer 会把插件锁死在一个版本上

## 插件是一个带 `apply` 的 Cordis 模块，`inject` 决定它何时运行

官方教程的定义：插件是导出 `apply` 函数的模块，框架加载时调用 `apply` 并传入 `ctx`，插件通过 `ctx` 注册能力。模块还可以导出三个字段。`name` 可选，只用于 Fiber 诊断和 logger 命名。`inject` 列出必需的服务，可以写数组，也可以写“服务名 → 配置”的映射。`Config` 必须是实现了 Standard Schema 的校验器，dsh 用的是自己 fork 的 Schemastery。Cordis 的 `Plugin.Base` 还定义了 `provide` 和 `intercept`，入门教程没有讲（`vendor/cordis/src/registry.ts:97-132`）。

插件有三种写法：函数模块、对象（`export default { name, inject, apply }`）、继承 `Service` 的类。文档建议大多数情况用函数形式，只有给别的插件提供服务时才用类（`docs/user/develop/basic/index.md:105-138`）。

图 1 是一个插件从被加载到开始工作要过的三道关，以及它在 `apply` 里能注册的东西。**这些注册全都是挂在插件 Fiber 上的 effect**：插件卸载、配置变化引起热替换、依赖的服务消失时，会被统一撤销，不用手写清理代码。

```svg title="图 1：插件模块的四个导出、apply 被调用前的三道关，以及 apply 里能注册的东西" tone="light"
<svg viewBox="0 0 720 340" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="dsh 插件的组成与加载条件">
<defs><marker id="a1" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#555"/></marker></defs>
<rect x="20" y="20" width="200" height="164" rx="8" fill="#F4F6F9" stroke="#1B365D" stroke-width="1.2"/>
<text x="120" y="44" text-anchor="middle" font-size="13.5" font-weight="600" fill="#1a1a1a">插件模块 index.js</text>
<line x1="34" y1="54" x2="206" y2="54" stroke="#c9ced6"/>
<text x="36" y="78" font-size="12" font-family="ui-monospace, Menlo, monospace" fill="#1a1a1a">name</text><text x="206" y="78" text-anchor="end" font-size="11" fill="#555">可选，诊断用</text>
<text x="36" y="104" font-size="12" font-family="ui-monospace, Menlo, monospace" fill="#1a1a1a">inject</text><text x="206" y="104" text-anchor="end" font-size="11" fill="#555">必需的服务</text>
<text x="36" y="130" font-size="12" font-family="ui-monospace, Menlo, monospace" fill="#1a1a1a">Config</text><text x="206" y="130" text-anchor="end" font-size="11" fill="#555">同步校验器</text>
<text x="36" y="156" font-size="12" font-family="ui-monospace, Menlo, monospace" fill="#1a1a1a">apply(ctx, config)</text>
<line x1="221" y1="100" x2="256" y2="100" stroke="#555" stroke-width="1.3" marker-end="url(#a1)"/>
<rect x="260" y="20" width="240" height="46" rx="7" fill="#fff" stroke="#1B365D" stroke-width="1.1"/>
<text x="274" y="39" font-size="12.5" font-weight="600" fill="#1a1a1a">① inject 的服务都在吗</text>
<text x="274" y="57" font-size="11" fill="#555">否：停在 PENDING(0)，不调用 apply</text>
<line x1="380" y1="67" x2="380" y2="77" stroke="#555" stroke-width="1.3" marker-end="url(#a1)"/>
<rect x="260" y="80" width="240" height="46" rx="7" fill="#fff" stroke="#1B365D" stroke-width="1.1"/>
<text x="274" y="99" font-size="12.5" font-weight="600" fill="#1a1a1a">② Config 同步校验</text>
<text x="274" y="117" font-size="11" fill="#555">失败：ValidationError；异步校验器直接报错</text>
<line x1="380" y1="127" x2="380" y2="137" stroke="#555" stroke-width="1.3" marker-end="url(#a1)"/>
<rect x="260" y="140" width="240" height="46" rx="7" fill="#1B365D"/>
<text x="274" y="159" font-size="12.5" font-weight="600" fill="#fff">③ 调用 apply(ctx, config)</text>
<text x="274" y="177" font-size="11" fill="#dfe7f1">进入 ACTIVE(2)</text>
<rect x="530" y="20" width="170" height="166" rx="7" fill="#F4F6F9" stroke="#9aa3ae" stroke-dasharray="5 4"/>
<text x="544" y="42" font-size="12.5" font-weight="600" fill="#1a1a1a">某个依赖消失</text>
<text x="544" y="66" font-size="11" fill="#333">所有 effect 被撤销</text>
<text x="544" y="86" font-size="11" fill="#333">Fiber 回到 PENDING(0)</text>
<text x="544" y="106" font-size="11" fill="#333">（不是文档说的 DISPOSED）</text>
<text x="544" y="134" font-size="11" fill="#333">依赖回来后从 ① 重新走，</text>
<text x="544" y="154" font-size="11" fill="#333">apply 再执行一次</text>
<line x1="501" y1="163" x2="526" y2="163" stroke="#555" stroke-width="1.3" marker-end="url(#a1)"/>
<line x1="529" y1="43" x2="504" y2="43" stroke="#555" stroke-width="1.3" marker-end="url(#a1)"/>
<line x1="380" y1="187" x2="380" y2="214" stroke="#555" stroke-width="1.3" marker-end="url(#a1)"/>
<rect x="14" y="218" width="692" height="80" rx="10" fill="none" stroke="#c9ced6" stroke-dasharray="6 5"/>
<rect x="24" y="230" width="128" height="56" rx="7" fill="#F4F6F9" stroke="#1B365D"/><text x="88" y="253" text-anchor="middle" font-size="10.5" font-family="ui-monospace, Menlo, monospace" fill="#1a1a1a">tools.register()</text><text x="88" y="273" text-anchor="middle" font-size="11" fill="#555">给模型用的工具</text>
<rect x="160" y="230" width="128" height="56" rx="7" fill="#F4F6F9" stroke="#1B365D"/><text x="224" y="253" text-anchor="middle" font-size="10.5" font-family="ui-monospace, Menlo, monospace" fill="#1a1a1a">on('tools/…')</text><text x="224" y="273" text-anchor="middle" font-size="11" fill="#555">工具流水线钩子</text>
<rect x="296" y="230" width="128" height="56" rx="7" fill="#F4F6F9" stroke="#1B365D"/><text x="360" y="253" text-anchor="middle" font-size="10.5" font-family="ui-monospace, Menlo, monospace" fill="#1a1a1a">tools.guard()</text><text x="360" y="273" text-anchor="middle" font-size="11" fill="#555">最后一道拒绝</text>
<rect x="432" y="230" width="128" height="56" rx="7" fill="#F4F6F9" stroke="#1B365D"/><text x="496" y="253" text-anchor="middle" font-size="10.5" font-family="ui-monospace, Menlo, monospace" fill="#1a1a1a">on('session/event')</text><text x="496" y="273" text-anchor="middle" font-size="11" fill="#555">会话记录</text>
<rect x="568" y="230" width="128" height="56" rx="7" fill="#F4F6F9" stroke="#1B365D"/><text x="632" y="253" text-anchor="middle" font-size="10.5" font-family="ui-monospace, Menlo, monospace" fill="#1a1a1a">effect()</text><text x="632" y="273" text-anchor="middle" font-size="11" fill="#555">自管的资源</text>
<text x="20" y="324" font-size="12" fill="#444">虚线框里的注册都是插件 Fiber 的 effect：卸载、热替换、依赖消失时统一撤销。</text>
</svg>
```

### 实测：依赖没到时 `apply` 不跑，依赖消失后回到 PENDING

把一个 `inject: ['tools']` 的插件先挂上去，再挂 `dsh-tools`，然后把 `dsh-tools` 的 Fiber 释放、再挂回来。状态值用的是 Cordis 的枚举：PENDING=0、LOADING=1、ACTIVE=2、FAILED=3、DISPOSED=4、UNLOADING=5（`vendor/cordis/src/fiber.ts:170-185`）。

输出说明了三件事。第一，`tools` 不在时插件停在 0，`apply` 根本没被调用。第二，依赖消失时 effect 被清理，状态**回到 0 而不是 4**；文档 `framework/index.md:38` 写的是 “ACTIVE → DISPOSED”，但源码注释里 DISPOSED 的含义是“已移除，不能再启动”。第三，依赖回来后 `apply` 完整地又执行了一次，所以 `apply` 里不要假设自己只跑一次。

```js title="本文实测 · scratchpad/verify/run2.mjs"
import { Context } from '@deepseek-ai/cordis'
import SessionProjection from '@deepseek-ai/dsh-session-projection'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools from '@deepseek-ai/dsh-tools'
const root = new Context()
const p = {
  name: 'early-plugin', inject: ['tools'],
  apply(ctx) {
    console.log('[apply] runs now; ctx.tools =', typeof ctx.tools)
    ctx.logger.info('hello from ctx.logger')
    ctx.effect(() => { console.log('[effect] setup'); return () => console.log('[effect] cleanup') })
  },
}
const f = root.plugin(p)               // tools not mounted yet
console.log('state before tools mounted:', f.state)
await root.plugin(SessionProjection); await root.plugin(SystemPrompt)
const tf = await root.plugin(Tools)
await new Promise(r => setTimeout(r, 10))
console.log('state after tools mounted:', f.state)
await tf.dispose()                      // remove the injected service
await new Promise(r => setTimeout(r, 10))
console.log('state after tools disposed:', f.state)
await root.plugin(Tools); await new Promise(r => setTimeout(r, 10))
console.log('state after tools re-mounted:', f.state)
```

```text title="本文实测 · node run2.mjs 输出"
state before tools mounted: 0
[apply] runs now; ctx.tools = object
[effect] setup
state after tools mounted: 2
[effect] cleanup
state after tools disposed: 0
[apply] runs now; ctx.tools = object
[effect] setup
state after tools re-mounted: 2
```

注意 `ctx.logger.info('hello from ctx.logger')` 这一行没有任何输出，原因见第 4 节。还有一点：`dsh-tools` 自己也是插件（`class ToolsService extends Service`，依赖 `systemPrompt`，而 `systemPrompt` 又依赖 `sessionProjections`），所以 `inject: ['tools']` 实际等的是一整条依赖链。`inject` 只保证注册表在，不保证有执行器；没挂 `dsh-tool-executor-native` 这类执行器时，调用工具会得到 `TOOL_EXECUTOR_UNAVAILABLE`（`packages/core/tools/README.md:75-84`）。

配置校验发生在 `apply` 之前，而且必须同步：Cordis 调用 `Config['~standard'].validate(config)`，校验器返回 Promise 就抛 `Async config validation is not supported`（`vendor/cordis/src/fiber.ts:72-91`）。Fiber 的六个状态如何转换、effect 的卸载顺序、三条重载路径，见[生命周期与副作用](dsh_lifecycle_and_effects.html)。

## 官方教程四步：从一行日志到带配置的 greet 工具

官方的学习顺序是：README 的“从源码运行”→ `basic/index.md`（第一个插件）→ `basic/tool.md` → `basic/config.md` → `basic/publish.md`，之后才是 framework 文档和 cookbook。源码运行要求 `pnpm@11.28.5` 和 Node `^24.22.3 || >=26.13.1`（根 `package.json:7-9`）。

本文**没有跑通这条官方路径**：`pnpm install` 要解析 1444 个包，在 3.9 GB 的临时盘上报 `ERR_PNPM_ENOSPC`，所以 `pnpm dsh web --patch` 没有执行。下面的教程代码是仓库原文；它们描述的行为改在一个裸宿主上验证（本节末尾）。

```bash title="仓库原文 · README.md:29-41 · 从源码运行"
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness
pnpm install
pnpm run build
pnpm dsh web
```

### 第一步：一个只打印日志的插件，用 `--patch` 叠进配置

在仓库根目录建 `scratch-plugin/`，写一个只打印一行的插件，再写一个 `cordis.yml` 插入一行，用 `pnpm dsh web --patch ./scratch-plugin/cordis.yml` 启动，终端里应能看到 `[hello-plugin] plugin loaded!`，Web UI 在 `http://127.0.0.1:3080`。

文档特别强调**路径必须是绝对路径**，因为 patch 文件只贡献配置，不改变 loader 解析模块路径时用的 profile 目录（`basic/index.md:56`）。但后面 `config.md` 的同一份 YAML 写的是相对路径 `./src/my-plugin.ts`，两处自相矛盾，照绝对路径写更稳妥。

```ts title="仓库原文 · docs/user/develop/basic/index.md:31-44 · scratch-plugin/src/my-plugin.ts"
import type { Context } from '@deepseek-ai/cordis'

export const name = 'hello-plugin'

export function apply(ctx: Context) {
  // Required dependencies are ready before apply runs.
  console.log('[hello-plugin] plugin loaded!')
}
```

```yaml title="仓库原文 · docs/user/develop/basic/index.md:46-54 · scratch-plugin/cordis.yml"
- insert:
    - id: hello
      name: '/absolute/path/to/deepseek-harness/scratch-plugin/src/my-plugin.ts'
```

### 第二步：用 `defineTool` 注册一个工具

`inject = ['tools']` 让 Cordis 等工具注册表就绪。`defineTool` 根据 `parameters` 推导并校验 `args`；`execute` 返回 `output.schema` 声明的规范值，再由 `output.render` 转成给模型看的内容块。**不要在 `execute` 里直接返回内容块**（`docs/cookbook/adding-a-tool.md:41-50`）。

`register` 返回一个 disposer，并做几项检查：`output: { schema, render }` 必填，`render` 必须是函数，`timeoutMs` 必须是正的有限数，`run_code` 是保留名，同一层里不能重名（`packages/core/tools/src/index.ts:120-152`）。工具体抛出的错误只有是 `HarnessError` 子类时，结果里才保留稳定的 `error.name` 和 `error.code`。

这里有一个**没有验证**的疑点：教程从仓库根下的 `scratch-plugin/` 按值 import `@deepseek-ai/dsh-tools`，但根 `package.json` 没声明这个依赖，pnpm 又是严格符号链接布局，Node 能否解析到存疑。第一步只用了 `import type`，编译后会被擦除，不受影响。

```ts title="仓库原文 · docs/user/develop/basic/tool.md:7-46 · greet 工具"
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'greet-tool'
export const inject = ['tools']

export function apply(ctx: Context) {
  ctx.tools.register(defineTool({
    name: 'greet',
    description: 'Greet someone by name.',
    parameters: {
      name: { type: 'string', required: true, description: 'The name to greet' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args) {
      return `Hello, ${args.name}!`
    },
  }))
}
```

### 第三步：导出同名的 `Config` 类型和 schema

插件导出一个 `Config` 接口，再导出一个同名的 Schemastery schema，默认值写在 schema 上，`apply(ctx, config)` 拿到的就是“用户值或默认值”。文档明确说不要导出普通对象，因为普通对象没有实现 Standard Schema。教程写 `import Schema from`，仓库内部惯例是 `import z from '@deepseek-ai/schemastery'`，两者是同一个默认导出。

第四步是打包发布，放到第 5 节以后讲。

```ts title="仓库原文 · docs/user/develop/basic/config.md:7-45"
import type { Context } from '@deepseek-ai/cordis'
import Schema from '@deepseek-ai/schemastery'

export const name = 'my-plugin'

export interface Config {
  greeting: string
  maxRetries: number
  verbose?: boolean
}

export const Config: Schema<Config> = Schema.object({
  greeting: Schema.string().default('Hello'),
  maxRetries: Schema.number().default(3),
  verbose: Schema.boolean().default(false),
})

export function apply(ctx: Context, config: Config) {
  console.log(config.greeting)  // User value or schema default.
}
```

### 不构建 monorepo 也能验证：把四个包挂到一个空 Context 上

不想（或像本文一样没法）构建整个仓库时，可以从 npm 装同版本的包，把 `dsh-session-projection`、`dsh-system-prompt`、`dsh-tools`、`dsh-tool-executor-native` 依次挂到 `new Context()` 上，然后直接调用 `root.tools.execute({ calls, signal })`。不需要模型、API key 和 Web UI。本文第 1、3 节的实测都是这样跑的，脚本是 `scratchpad/verify/run.mjs`。

右边是脚本的宿主部分和几次调用的结果。带 `Config` 的插件以 `{ greeting: 'Hi there' }` 挂载，`greet(Ada)` 返回 `Hi there, Ada!`；以 `{ greeting: 42 }` 挂载时 `await root.plugin(...)` 被拒绝。工具体抛普通 `Error('kaboom')` 时，`error` 里只有 `message`，没有 `info`，印证了“只有 `HarnessError` 保留 name/code”。插件释放后 `schemas()` 变成空数组，再调用得到 `UNKNOWN_TOOL`：工具注册确实随插件一起撤销。

```js title="本文实测 · scratchpad/verify/run.mjs（宿主部分节选）"
const root = new Context()
await root.plugin(SessionProjection.default)
await root.plugin(SystemPrompt.default)
await root.plugin(Tools.default)
await root.plugin(Native)
const fiber = await root.plugin(myPlugin, { greeting: 'Hi there' })
log('fiber state after await:', fiber.state)
log('registered:', root.tools.schemas().map(s => s.name))
```

```text title="本文实测 · node run.mjs 输出（节选）"
[apply] tools ready? true config= {"greeting":"Hi there"}
fiber state after await: 2
registered: [ 'greet' ]

=== call greet(boom)
  ...
=> isError true content [{"type":"text","text":"Error: kaboom"}] error {"message":"kaboom"}

=== Config validation failure
caught: ValidationError invalid config:
  - $.greeting expected string but got 42 (at greeting)

=== dispose plugin
registered after dispose: []
=>  {"isError":true,"error":{"message":"unknown tool \"greet\"","info":{"name":"ToolNotFoundError","code":"UNKNOWN_TOOL"}},"content":[{"type":"text","text":"Error: unknown tool \"greet\""}]}
```

## 一次工具调用经过五个扩展点，被拒绝的调用也走到最后

`packages/core/tools/README.md` 给的顺序是：`tools/pre-execute` → 注册的单调 guard → `tools/execute` 包裹层 → `tools/post-execute` → 定义自带的 `finalizeContent` → 只读的 `tools/result`。源码 `pipeline.ts` 的 `prepare()` 先跑 pre-execute 的 waterfall，决定是 `allow` 才去 `checkGuards`；任何拒绝都被包装成内容为 `Error: <reason>` 的 `isError` 结果，阶段标为 `post-result`，而 `post-result` **仍会进入 post-execute**（`pipeline.ts:32-35, 185-233`）。

图 2 把这条路径和两种拒绝的走向画在一起。上面一行是放行的调用，下面两条虚线是实测中被拒绝的调用实际经过的阶段。

```svg title="图 2：工具调用流水线的五个扩展点，以及两种拒绝的实际路径（本文实测）" tone="light"
<svg viewBox="0 0 720 270" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="工具调用流水线">
<defs><marker id="a2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#555"/></marker></defs>
<text x="12" y="30" font-size="12" fill="#444">放行（greet(Ada)）：1 → 2 → 3 → 4 → 5</text>
<rect x="12" y="50" width="112" height="96" rx="7" fill="#F4F6F9" stroke="#1B365D" stroke-width="1.1"/>
<text x="68" y="72" text-anchor="middle" font-size="12" font-weight="600" fill="#1a1a1a">1 pre-execute</text>
<text x="68" y="92" text-anchor="middle" font-size="10.5" fill="#555">waterfall</text>
<text x="68" y="112" text-anchor="middle" font-size="10.5" fill="#555">allow / deny</text>
<text x="68" y="128" text-anchor="middle" font-size="10.5" fill="#555">ask / cancel</text>
<line x1="125" y1="98" x2="143" y2="98" stroke="#555" stroke-width="1.3" marker-end="url(#a2)"/>
<rect x="146" y="50" width="112" height="96" rx="7" fill="#F4F6F9" stroke="#1B365D" stroke-width="1.1"/>
<text x="202" y="72" text-anchor="middle" font-size="12" font-weight="600" fill="#1a1a1a">2 guard</text>
<text x="202" y="92" text-anchor="middle" font-size="10.5" fill="#555">同步函数</text>
<text x="202" y="112" text-anchor="middle" font-size="10.5" fill="#555">只能拒绝</text>
<text x="202" y="128" text-anchor="middle" font-size="10.5" fill="#555">返回字符串即拒</text>
<line x1="259" y1="98" x2="277" y2="98" stroke="#555" stroke-width="1.3" marker-end="url(#a2)"/>
<rect x="280" y="50" width="160" height="96" rx="7" fill="#F4F6F9" stroke="#1B365D" stroke-width="1.1"/>
<text x="360" y="70" text-anchor="middle" font-size="12" font-weight="600" fill="#1a1a1a">3 tools/execute 包裹层</text>
<rect x="292" y="80" width="136" height="24" rx="5" fill="#fff" stroke="#9aa3ae"/>
<text x="360" y="96" text-anchor="middle" font-size="10.5" fill="#333">参数校验（ToolArgsError）</text>
<rect x="292" y="112" width="136" height="24" rx="5" fill="#1B365D"/>
<text x="360" y="128" text-anchor="middle" font-size="10.5" fill="#fff">工具体 execute()</text>
<line x1="441" y1="98" x2="459" y2="98" stroke="#555" stroke-width="1.3" marker-end="url(#a2)"/>
<rect x="462" y="50" width="112" height="96" rx="7" fill="#F4F6F9" stroke="#1B365D" stroke-width="1.1"/>
<text x="518" y="72" text-anchor="middle" font-size="12" font-weight="600" fill="#1a1a1a">4 post-execute</text>
<text x="518" y="92" text-anchor="middle" font-size="10.5" fill="#555">waterfall</text>
<text x="518" y="112" text-anchor="middle" font-size="10.5" fill="#555">换值或换内容</text>
<text x="518" y="128" text-anchor="middle" font-size="10.5" fill="#555">block</text>
<line x1="575" y1="98" x2="593" y2="98" stroke="#555" stroke-width="1.3" marker-end="url(#a2)"/>
<rect x="596" y="50" width="112" height="96" rx="7" fill="#F4F6F9" stroke="#1B365D" stroke-width="1.1"/>
<text x="652" y="72" text-anchor="middle" font-size="12" font-weight="600" fill="#1a1a1a">5 tools/result</text>
<text x="652" y="92" text-anchor="middle" font-size="10.5" fill="#555">emit，只读</text>
<text x="652" y="112" text-anchor="middle" font-size="10.5" fill="#555">exec 已冻结</text>
<text x="652" y="128" text-anchor="middle" font-size="10.5" fill="#555">不被 await</text>
<path d="M202 147 V176 H508 V150" fill="none" stroke="#555" stroke-width="1.2" stroke-dasharray="5 4" marker-end="url(#a2)"/>
<text x="214" y="192" font-size="11.5" fill="#333">guard 拒绝（Eve）：1 → 2 → 4 → 5，跳过 3</text>
<path d="M68 147 V214 H528 V150" fill="none" stroke="#555" stroke-width="1.2" stroke-dasharray="5 4" marker-end="url(#a2)"/>
<text x="80" y="232" font-size="11.5" fill="#333">pre-execute 拒绝（Mallory）：1 → 4 → 5，跳过 2 和 3</text>
<text x="12" y="260" font-size="12" fill="#444">拒绝被包装成 isError 结果（阶段 post-result），所以 post-execute 和 tools/result 都会看到它。</text>
</svg>
```

### 实测：五个阶段各挂一个监听器

被测插件在每个阶段打印一行：pre-execute 拒绝 `Mallory`，guard 拒绝 `Eve`，post-execute 把 `Shout` 的值改成大写。右边是这部分代码和三次调用的输出。

输出里值得注意的几点。放行的调用严格按 1→2→3→4→5 执行，工具体收到的 `args` 是冻结对象。`Mallory` 跳过了 guard 和工具体，`Eve` 跳过了工具体，但两者都到达了 4 和 5，post-execute 看到的是 `isError= true`。另外两次调用没贴全文：`Shout` 在 post-execute 返回 `{ kind: 'accept', value: ... }` 后，新值**重新经过 `output.render`**，最终内容是 `HI THERE, SHOUT!`；参数 `{}` 时轨迹是 1→2→3 before→3 after（`isError= true`），工具体没有执行，结果是 `ToolArgsError` / `INVALID_ARGS`，说明**参数校验发生在 `tools/execute` 里面**，pre-execute 和 guard 看到的是未经校验的参数。

```js title="本文实测 · scratchpad/verify/run.mjs（钩子部分节选）"
    ctx.on('tools/pre-execute', async (exec, next) => {
      log('  1 [pre-execute]', exec.name, JSON.stringify(exec.arguments))
      if (exec.arguments.name === 'Mallory') return { kind: 'deny', reason: 'Mallory is blocked by policy.' }
      return next()
    })
    ctx.tools.guard((exec) => {
      log('  2 [guard]', exec.name)
      return exec.arguments.name === 'Eve' ? 'Eve denied by guard' : undefined
    })
    ctx.on('tools/execute', async (exec, next) => {
      log('  3 [execute wrapper] before')
      const r = await next()
      log('  3 [execute wrapper] after isError=', r.isError)
      return r
    })
    ctx.on('tools/post-execute', async (exec, result, next) => {
      log('  4 [post-execute] isError=', result.isError, 'value=', JSON.stringify(result.value))
      if (!result.isError && exec.arguments.name === 'Shout') return { kind: 'accept', value: String(result.value).toUpperCase() }
      return next()
    })
    ctx.on('tools/result', (exec, result) => {
      log('  5 [tools/result] frozen?', Object.isFrozen(result), 'content=', JSON.stringify(result.content), 'error=', JSON.stringify(result.error))
    })
```

```text title="本文实测 · node run.mjs 输出（三次调用）"
=== call greet(Ada)
  1 [pre-execute] greet {"name":"Ada"}
  2 [guard] greet
  3 [execute wrapper] before
  [execute body] args= {"name":"Ada"} frozen? true
  3 [execute wrapper] after isError= false
  4 [post-execute] isError= false value= "Hi there, Ada!"
  5 [tools/result] frozen? true content= [{"type":"text","text":"Hi there, Ada!"}] error= undefined
=> isError false content [{"type":"text","text":"Hi there, Ada!"}] 

=== call greet(Mallory)
  1 [pre-execute] greet {"name":"Mallory"}
  4 [post-execute] isError= true value= undefined
  5 [tools/result] frozen? true content= [{"type":"text","text":"Error: Mallory is blocked by policy."}] error= {"message":"Mallory is blocked by policy."}
=> isError true content [{"type":"text","text":"Error: Mallory is blocked by policy."}] error {"message":"Mallory is blocked by policy."}

=== call greet(Eve)
  1 [pre-execute] greet {"name":"Eve"}
  2 [guard] greet
  4 [post-execute] isError= true value= undefined
  5 [tools/result] frozen? true content= [{"type":"text","text":"Error: Eve denied by guard"}] error= {"message":"Eve denied by guard"}
=> isError true content [{"type":"text","text":"Error: Eve denied by guard"}] error {"message":"Eve denied by guard"}
```

### 五个扩展点各管什么，怎么拒绝

下表的签名和分发方式来自 `packages/core/tools/src/events.ts:6-57`，选用规则来自 `docs/cookbook/adding-a-tool.md:58-60`。四个调用事件都按作用域过滤：挂在某个 agent 作用域上的监听器只收到这个 agent 的调用。

| 扩展点 | 分发方式 / 签名 | 用来做什么 | 怎么拒绝 |
| --- | --- | --- | --- |
| `tools/pre-execute` | waterfall，`(exec, next) => Promise<PreToolDecision>` | 可扩展的 allow / deny / ask / cancel 策略 | 返回 `{ kind: 'deny', reason }`，或不调用 `next()` |
| `ctx.tools.guard(fn)` | 同步，`(exec) => string \| denial \| undefined` | 最后一道单调拒绝；没有 allow 结果，后面的监听器推翻不了 | 返回字符串 |
| `tools/execute` | waterfall 包裹分发，`(exec, next) => Promise<Result>` | 截止时间、重试、计量；**唯一可以替换 `exec.signal` 的地方**，替换后必须恢复 | 改写 `next()` 的结果 |
| `tools/post-execute` | waterfall，`(exec, result, next)` | 替换展示内容**或**值（不能同时换，失败结果的值不能换）、附加给模型的上下文 | 返回 `{ kind: 'block', feedback }` |
| `tools/result` | emit，只读，不被 await | 日志、审计、遥测；回调出错只记一条 warn | 不能拒绝 |

### 权限门禁的官方写法，以及 `ask` 的降级

dsh 里没有“工具权限”这个属性。工具插件本身不带策略，权限由单独的钩子插件在 `tools/pre-execute` 上决定。右边是 cookbook 里的门禁示例：不放行就返回 `deny`，放行就 `return next()`，让后面的监听器继续判断。

返回 `ask` 时，管线会去取 `approval` 服务。**没挂这个服务时，`ask` 按拒绝处理**，错误码 `APPROVAL_UNAVAILABLE`，消息是 “requires approval (not yet supported)”（`pipeline.ts:440-492`）。Web profile 挂了它，裸宿主没有。

由此可以总结写门禁的三条规矩：pre-execute 和 guard 读 `exec.arguments` 时要做防御，因为参数还没校验；post-execute 先看 `result.isError`，不要默认工具已经执行过；必须在下一步之前完成的事不要放进 `tools/result`，它不被 await。多个监听器挂在同一个 waterfall 上的先后顺序和 `thisArg`，见[服务、事件、配置与插件实战](dsh_services_events_config_plugin.html)。

```ts title="仓库原文 · docs/cookbook/extension-cookbook.md:11-33 · 权限门禁"
import type { Context } from '@deepseek-ai/cordis'
import type { PreToolDecision, ToolExecution } from '@deepseek-ai/dsh-tools'

declare function isAllowed(exec: ToolExecution): Promise<boolean>

export const name = 'permission-gate'

export function apply(ctx: Context) {
  ctx.on('tools/pre-execute', async (exec, next): Promise<PreToolDecision> => {
    if (!(await isAllowed(exec))) {
      return { kind: 'deny', reason: 'Denied by policy.' }
    }
    return next()
  })
}
```

仓库里有两个可以直接照着读的真实例子。`packages/guard/timeout-policy` 是 `tools/execute` 包裹层的标准写法，全文 81 行：读取工具的 `timeoutMs`，没有就直接 `next()`；有的话建一个 deadline，替换 `exec.signal`，在 `finally` 里换回来。`dsh-hooks-claude-code` 演示了 post-execute 的写法：先 `await next()`，再把自己的上下文合并到下游的决定上，这样后面的监听器仍然可以 block（`hooks-claude-code/src/index.ts:246-282`）。

## 监听事件与打日志：`session/event`、`ctx.logger` 和不存在的 dispose 事件

插件作者需要的监听 API 只有 `ctx.on(name, handler)`，监听器本身就是 effect，插件卸载时自动移除。常见的误会是把 `turn/*`、`step/*`、`tool/call`、`tool/result`、`compaction/*` 当成 Cordis 事件。它们其实是**持久化会话记录的类型名**，要观察它们得监听 Cordis 事件 `session/event`，再按 `event.type` 分支（`docs/user/develop/framework/events.md:102-106`）。`session/event` 是 emit 模式；挂在 agent 作用域上的监听器只收到经由这个 agent 进入的会话的事件。实时 token 流来自另一个事件 `agent/assistant-stream`。

官方的日志插件示例监听的是工具流水线的 `tools/result`，不是会话记录。那行 `import '@deepseek-ai/dsh-tools'` 只是为了引入 `Events` 的声明合并，拿到事件的类型。

```ts title="仓库原文 · docs/user/develop/framework/events.md:119-138 · 日志插件"
import type { Context } from '@deepseek-ai/cordis'
import '@deepseek-ai/dsh-tools'

export const name = 'tool-logger'

export function apply(ctx: Context) {
  ctx.on('tools/result', (exec, result) => {
    console.log(`[tool] ${exec.name}(${JSON.stringify(exec.arguments)})`)
    const text = result.content
      .map(block => block.type === 'text' ? block.text : '')
      .join('')
    console.log(`[tool result] ${text.slice(0, 100)}`)
  })
}
```

### `ctx.logger` 只产生日志，输出要靠 exporter 插件

`ctx.logger` 是 Cordis 内置的 `LoggerService`，`ctx.logger(name)` 拿具名 logger，级别有 error、info、warn、debug（`vendor/cordis/src/logger.ts:7-8`）。但它只负责产生日志，打到控制台要靠 exporter 插件，例如仓库 vendor 的 `logger-console`。第 1 节的 `run2.mjs` 在没有 exporter 的裸宿主里调用了 `ctx.logger.info(...)`，**没有任何输出，也不报错**。教程用 `console.log` 是图省事；正式插件建议用 `ctx.logger`，日志会带上插件名。`web` profile 配了哪个 exporter、开了哪些级别，本文没有核实。

### Cordis 4 没有 `dispose` 事件，清理交给 `ctx.effect`

熟悉 Koishi 或旧版 Cordis 的人会写 `ctx.on('dispose', ...)` 或 `ctx.on('ready', ...)`。dsh 用的 Cordis 4 内置事件只有 `internal/*` 一类（`internal/plugin`、`internal/status`、`internal/service` 等），**没有 `dispose` 和 `ready`**（`vendor/cordis/src/events.ts:330-360`）。

`run.mjs` 的被测插件里就挂了一行 `ctx.on('dispose', () => log('[dispose] greet-plugin unloading'))`。第 2 节的输出里，`fiber.dispose()` 之后只有 `registered after dispose: []`，这行从未打印；而 `run2.mjs` 里 `ctx.effect` 的清理函数正常打印了 `[effect] cleanup`。正确做法是在 `apply` 里初始化，清理写成 `ctx.effect` 返回的函数。

```js title="本文实测 · scratchpad/verify/run.mjs · 挂了但从未触发的一行"
    ctx.on('dispose', () => log('[dispose] greet-plugin unloading'))
```

## bundle 和 profile：`dsh plugin` 是 pnpm 外面的一层薄包装

**bundle** 是在 `package.json` 里声明了 `"dsh": { "bundle": { "patch": "./cordis.patch.yml" } }` 的普通 npm 包，它对外贡献的是**一层配置 patch**。patch 里用**包名**而不是相对路径引用插件代码，这样 Node 解析能找到安装后的代码。**profile** 是 `$DSH_HOME/profiles/<name>/` 这个目录，它的 manifest 声明 `dsh.profile`，列出由哪些 bundle 按什么顺序组成。文档的原话是：bundle 是作者分发的东西，profile 是用户启动的东西，“Nothing is both”（`docs/user/develop/basic/publish.md:9-16`）。

`dsh.bundle` 的全部字段是：必填的 `patch`（字符串或有序列表，列表里的多个文件合起来算**同一层**）、`platforms?`（Node 平台名或 `wsl`，宿主不匹配时整层跳过）、`listedByDefault?`（`packages/util/package-manifest/src/types.ts:31-84`）。判断一个包是不是 bundle，唯一标准是 `dsh.bundle.patch !== undefined`，不读 JS。bundle 也不一定自带代码：`subagent-codex` 的 patch 只插入两行，其中一行引用的是**别的包** `@deepseek-ai/dsh-tool-subagent` 并给它配置。

右边是本文实测用的最小 bundle，三个文件。`peerDependencies` 那一行是踩坑后补上的，原因见第 8 节。

```json title="本文实测 · dsh-try/work/hello-plugin/package.json"
{
  "name": "dsh-hello-plugin",
  "version": "0.1.0",
  "type": "module",
  "main": "index.js",
  "files": [
    "index.js",
    "cordis.patch.yml"
  ],
  "engines": {
    "dsh": ">=0.2.0 <0.3.0"
  },
  "dsh": {
    "bundle": {
      "patch": "./cordis.patch.yml"
    }
  },
  "peerDependencies": {
    "@deepseek-ai/schemastery": "*"
  }
}
```

```yaml title="本文实测 · dsh-try/work/hello-plugin/cordis.patch.yml"
- insert:
    - id: hello
      name: dsh-hello-plugin
      config:
        greeting: from-bundle
```

```js title="本文实测 · dsh-try/work/hello-plugin/index.js"
import Schema from '@deepseek-ai/schemastery'
export const name = 'hello-plugin'
export const Config = Schema.object({ greeting: Schema.string().default('Hello') })
export function apply(ctx, config) {
  console.log('[hello-plugin] loaded:', config.greeting)
}
```

### `dsh plugin add` 实际做了什么

`dsh plugin --profile <name> <args…>` 把参数原样转发给 PATH 上的 pnpm，工作目录是 profile 目录，`add`、`remove`、`why`、`update` 都能用，找不到 pnpm 时退出码 127。`.`、`../x` 这类相对路径先相对**调用目录**解析（`apps/cli/reference/README.md:85`）。图 3 是一次 `add` 从初始化到启动的完整流程。

```svg title="图 3：dsh plugin add 的流程，以及启动时插件被 import 的位置" tone="light"
<svg viewBox="0 0 720 470" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="dsh plugin add 的流程">
<defs><marker id="a3" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#555"/></marker></defs>
<rect x="20" y="14" width="420" height="34" rx="7" fill="#1B365D"/>
<text x="230" y="36" text-anchor="middle" font-size="12.5" font-family="ui-monospace, Menlo, monospace" fill="#fff">dsh plugin --profile demo add ./hello-plugin</text>
<line x1="230" y1="49" x2="230" y2="66" stroke="#555" stroke-width="1.3" marker-end="url(#a3)"/>
<rect x="20" y="70" width="420" height="70" rx="7" fill="#F4F6F9" stroke="#1B365D"/>
<text x="34" y="90" font-size="12.5" font-weight="600" fill="#1a1a1a">1 profile 不存在就初始化（已存在的文件不动）</text>
<text x="34" y="110" font-size="11" fill="#444">package.json：dependencies + dsh.profile.bundles（非内置名只有 dsh-base）</text>
<text x="34" y="128" font-size="11" fill="#444">cordis.patch.yml：[]　pnpm-workspace.yaml：hoisted，autoInstallPeers: false</text>
<line x1="230" y1="141" x2="230" y2="158" stroke="#555" stroke-width="1.3" marker-end="url(#a3)"/>
<rect x="20" y="162" width="420" height="52" rx="7" fill="#F4F6F9" stroke="#1B365D"/>
<text x="34" y="182" font-size="12.5" font-weight="600" fill="#1a1a1a">2 安装前兼容预检</text>
<text x="34" y="202" font-size="11" fill="#444">路径读 package.json；registry 先 pnpm view；git 和 tarball 装完再查</text>
<line x1="441" y1="188" x2="476" y2="188" stroke="#555" stroke-width="1.3" marker-end="url(#a3)"/>
<rect x="480" y="162" width="220" height="52" rx="7" fill="#fff" stroke="#9aa3ae" stroke-dasharray="5 4"/>
<text x="494" y="182" font-size="11.5" fill="#1a1a1a">不兼容：nothing was installed</text>
<text x="494" y="202" font-size="11" fill="#555">退出码 1，提示 allow-version</text>
<line x1="230" y1="215" x2="230" y2="232" stroke="#555" stroke-width="1.3" marker-end="url(#a3)"/>
<rect x="20" y="236" width="420" height="52" rx="7" fill="#F4F6F9" stroke="#1B365D"/>
<text x="34" y="256" font-size="12.5" font-weight="600" fill="#1a1a1a">3 在 profile 目录运行 PATH 上的 pnpm add</text>
<text x="34" y="276" font-size="11" fill="#444">pnpm 版本随 PATH 而变，dsh 不自带</text>
<line x1="441" y1="262" x2="476" y2="262" stroke="#555" stroke-width="1.3" marker-end="url(#a3)"/>
<rect x="480" y="236" width="220" height="52" rx="7" fill="#fff" stroke="#9aa3ae" stroke-dasharray="5 4"/>
<text x="494" y="256" font-size="11.5" fill="#1a1a1a">git 依赖的 prepare 被拦截</text>
<text x="494" y="276" font-size="11" fill="#555">要授权完整键（第 7 节）</text>
<line x1="230" y1="289" x2="230" y2="306" stroke="#555" stroke-width="1.3" marker-end="url(#a3)"/>
<rect x="20" y="310" width="420" height="52" rx="7" fill="#F4F6F9" stroke="#1B365D"/>
<text x="34" y="330" font-size="12.5" font-weight="600" fill="#1a1a1a">4 对账：新依赖声明了 dsh.bundle.patch 吗</text>
<text x="34" y="350" font-size="11" fill="#444">是：校验平台、预解析 patch YAML，追加到 bundles 末尾</text>
<line x1="441" y1="336" x2="476" y2="336" stroke="#555" stroke-width="1.3" marker-end="url(#a3)"/>
<rect x="480" y="310" width="220" height="52" rx="7" fill="#fff" stroke="#9aa3ae" stroke-dasharray="5 4"/>
<text x="494" y="330" font-size="11.5" fill="#1a1a1a">否：warning，作为普通依赖</text>
<text x="494" y="350" font-size="11" fill="#555">bundles 列表不变</text>
<line x1="230" y1="363" x2="230" y2="380" stroke="#555" stroke-width="1.3" marker-end="url(#a3)"/>
<rect x="20" y="384" width="420" height="52" rx="7" fill="#1B365D"/>
<text x="34" y="404" font-size="12.5" font-weight="600" fill="#fff">5 dsh --profile demo 启动：叠加配置层，import 插件</text>
<text x="34" y="424" font-size="11" fill="#dfe7f1">这一步才真正 import 入口 JS 和它依赖的包</text>
<line x1="441" y1="410" x2="476" y2="410" stroke="#555" stroke-width="1.3" marker-end="url(#a3)"/>
<rect x="480" y="384" width="220" height="52" rx="7" fill="#fff" stroke="#9aa3ae" stroke-dasharray="5 4"/>
<text x="494" y="404" font-size="11.5" fill="#1a1a1a">缺 peer：ERR_MODULE_NOT_FOUND</text>
<text x="494" y="424" font-size="11" fill="#555">该行不激活，其余照常（第 8 节）</text>
<text x="20" y="460" font-size="12" fill="#444">第 1 到 4 步发生在安装时，第 5 步发生在每次启动时；add 和 --dump-config 成功不代表启动能成功。</text>
</svg>
```

### 实测：安装、生成的 profile、普通依赖和版本拒绝

第一次 `add` 会初始化 profile。生成的 `package.json` 权限是 `0600`，内容和文档示例一致；`pnpm-workspace.yaml` 固定写 `nodeLinker: hoisted` 和 `autoInstallPeers: false`。源码注释解释了原因：扁平的 `node_modules` 让插件缺的 peer（cordis 等）走运行时解析，所有插件共享安装方那**唯一一份 cordis 实例**（`packages/boot/app-boot/src/profile.ts:180-189`）。

装一个没有 `dsh` 字段的包，pnpm 成功，dsh 打一行警告，bundles 列表不变。执行 `remove` 会把依赖和层一起删掉。

```text title="本文实测 · dsh plugin --profile art add ./hello-plugin（pnpm 10.34.9）"
dsh: initialized profile art at <scratch>/dsh-try/home/profiles/art

dependencies:
+ dsh-hello-plugin link:<scratch>/dsh-try/work/hello-plugin

Already up to date
Done in 206ms using pnpm v10.34.9
```

```json title="本文实测 · $DSH_HOME/profiles/art/package.json（add 之后）"
{
  "name": "dsh-profile-art",
  "private": true,
  "dependencies": {
    "dsh-hello-plugin": "link:<scratch>/dsh-try/work/hello-plugin"
  },
  "dsh": {
    "profile": {
      "bundles": [
        "@deepseek-ai/dsh-base",
        "dsh-hello-plugin"
      ]
    }
  }
}
```

```text title="本文实测 · add ./plain（package.json 里没有 dsh 字段）"
Already up to date
Done in 186ms using pnpm v10.34.9
dsh: warning: plain-lib declares no dsh.bundle — installed as a plain dependency, not a profile layer
```

**版本检查在安装时和启动时各做一次。**安装一个 `engines.dsh: "<0.2.0"` 的包，dsh 在调用 pnpm 之前就拒绝了，什么都没装，退出码 1，并给出豁免命令。豁免的粒度是“精确的包版本 → 精确的 dsh 版本”，记录在 profile 的 `compatibility.json` 里，升级插件或 dsh 后都不继承（`packages/boot/app-boot/README.md:54-58`）。

启动时，不兼容或加载失败的 bundle 会被跳过、列入 `skippedBundles`，不会让启动失败。但 profile patch 或用户 patch 出错**仍会让启动失败**，而且空文件或只有注释的 patch 也算出错，要禁用一层请写 `[]`。

```json title="本文实测 · dsh-try/work/old/package.json"
{"name":"old-bundle","version":"1.0.0","engines":{"dsh":"<0.2.0"},"dsh":{"bundle":{"patch":"./p.yml"}}}
```

```text title="本文实测 · dsh plugin --profile art add ./old（退出码 1）"
dsh: installation rejected: Plugin old-bundle@1.0.0 is incompatible with dsh 0.2.2-rc.5: requirements {"engines.dsh":"<0.2.0"}. Running it may cause crashes or data loss. Update the plugin or install a plugin version compatible with this dsh runtime. To accept this risk explicitly, grant the exact-version exemption for old-bundle@1.0.0 on dsh 0.2.2-rc.5 with `dsh plugin allow-version` or the plugin manager, then retry the installation or restart dsh. Exact-version exemption: not active.
dsh: nothing was installed.
dsh: to accept the risk, run: dsh plugin --profile art allow-version old-bundle@1.0.0 --dsh-version 0.2.2-rc.5 --accept-risk
dsh: plugin command failed; diagnostics: <scratch>/dsh-try/home/profiles/art/.plugin-manager/logs/operation-GtuOEJ/pnpm.log
```

## 配置是四类层，patch 只替换写出来的 `config` 键

官方文档写的是 **4 类层**，从一个空根开始依次应用：`dsh.profile.bundles` 里每个 bundle 的 patch（base 最先，之后按添加顺序），profile 自己的 `cordis.patch.yml`，`$DSH_HOME/cordis.patch.yml`（本机所有 profile 共享），最后是每个 `--patch`（按命令行顺序）（`docs/user/develop/basic/publish.md:120-127`）。源码 `readProfilePatches` 的拼接顺序完全一致，此外在设置了遥测禁用环境变量时，**末尾还会追加一个由代码生成的遥测 patch**（`packages/boot/app-boot/src/profile-context.ts:75-86`）。

本文旧版说的“五层”，是把 `dsh-base` 和其他 bundle 拆开来数。实际层数并不固定：每个 bundle 各算一层，`--patch` 可以出现多次，还可能多出遥测层。命令行参数**不是** patch 层，它通过 `cmdlineArgs` 服务和行内 `!!js ctx.xxx ?? 默认值` 表达式起作用，所以用户 patch 用字面量整块替换某行的 `config` 时，这类运行时读取会一起丢掉。

```svg title="图 4：配置层的叠加顺序，右侧是本文实测时 hello 这一行在每层的值" tone="light"
<svg viewBox="0 0 720 410" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="配置层叠加顺序">
<defs><marker id="a4" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#555"/></marker></defs>
<text x="20" y="24" font-size="12" font-weight="600" fill="#444">官方分类</text>
<text x="140" y="24" font-size="12" font-weight="600" fill="#444">按顺序应用（后面的层按 id 覆盖前面的行）</text>
<text x="524" y="24" font-size="12" font-weight="600" fill="#444">实测：id: hello</text>
<rect x="140" y="38" width="360" height="30" rx="6" fill="#fff" stroke="#c9ced6"/>
<text x="154" y="58" font-size="11.5" fill="#555">空根 []</text>
<text x="20" y="98" font-size="11.5" fill="#1a1a1a">1 bundle patch</text>
<text x="20" y="116" font-size="10.5" fill="#555">每个 bundle 一层</text>
<rect x="140" y="80" width="360" height="30" rx="6" fill="#F4F6F9" stroke="#1B365D"/>
<text x="154" y="100" font-size="11.5" fill="#1a1a1a">@deepseek-ai/dsh-base（列表第一个）</text>
<rect x="140" y="116" width="360" height="30" rx="6" fill="#F4F6F9" stroke="#1B365D"/>
<text x="154" y="136" font-size="11.5" fill="#1a1a1a">其他 bundle，按 dsh.profile.bundles 顺序</text>
<text x="524" y="136" font-size="11.5" font-family="ui-monospace, Menlo, monospace" fill="#333">greeting: from-bundle</text>
<text x="20" y="178" font-size="11.5" fill="#1a1a1a">2 profile patch</text>
<rect x="140" y="160" width="360" height="30" rx="6" fill="#F4F6F9" stroke="#1B365D"/>
<text x="154" y="180" font-size="11.5" font-family="ui-monospace, Menlo, monospace" fill="#1a1a1a">profiles/&lt;p&gt;/cordis.patch.yml</text>
<text x="524" y="180" font-size="11.5" font-family="ui-monospace, Menlo, monospace" fill="#333">greeting: from-profile</text>
<text x="20" y="222" font-size="11.5" fill="#1a1a1a">3 home patch</text>
<rect x="140" y="204" width="360" height="30" rx="6" fill="#F4F6F9" stroke="#1B365D"/>
<text x="154" y="224" font-size="11.5" font-family="ui-monospace, Menlo, monospace" fill="#1a1a1a">$DSH_HOME/cordis.patch.yml</text>
<text x="524" y="224" font-size="11.5" font-family="ui-monospace, Menlo, monospace" fill="#333">greeting: from-home</text>
<text x="20" y="266" font-size="11.5" fill="#1a1a1a">4 --patch</text>
<text x="20" y="284" font-size="10.5" fill="#555">可出现多次</text>
<rect x="140" y="248" width="360" height="30" rx="6" fill="#F4F6F9" stroke="#1B365D"/>
<text x="154" y="268" font-size="11.5" fill="#1a1a1a">每个 --patch 文件，按命令行顺序</text>
<text x="524" y="268" font-size="11.5" font-family="ui-monospace, Menlo, monospace" fill="#333">disabled: true</text>
<rect x="140" y="292" width="360" height="30" rx="6" fill="#fff" stroke="#9aa3ae" stroke-dasharray="5 4"/>
<text x="154" y="312" font-size="11.5" fill="#555">（条件）遥测禁用环境变量派生的 patch</text>
<line x1="510" y1="40" x2="510" y2="318" stroke="#555" stroke-width="1.2" marker-end="url(#a4)"/>
<rect x="140" y="340" width="560" height="54" rx="7" fill="#1B365D"/>
<text x="154" y="361" font-size="11.5" fill="#fff">合成结果：- id: hello · name: dsh-hello-plugin · config: {greeting: from-home} · disabled: true</text>
<text x="154" y="381" font-size="11" fill="#dfe7f1">home 层胜过 profile 层；只写 disabled 的覆盖没有清掉已有的 config</text>
</svg>
```

### 实测：用 `--dump-config` 看每层改了什么

实验设置：bundle patch 写 `from-bundle`，profile patch 写 `from-profile`，home patch 写 `from-home`。`--dump-config` 的行头列出了改过这一行的每个文件，值是 `from-home`，**home 层胜过 profile 层**，和文档一致。

再加一个只含 `- id: hello` 和 `disabled: true` 的 `--patch off.yml`，这一行变成 `config` 保留 `from-home`、再加 `disabled: true`。也就是说，“patch 整块替换 `config`”针对的是 patch 里**写出了 `config` 键**的情况，只写 `disabled` 的覆盖不会清掉已有配置。`--dump-default-config` 只显示 bundle 层。`--patch` 指向不存在的 id 时只在 stderr 报一行，dump 照常输出。把 home 层改回 `[]` 再真实启动，打印 `[hello-plugin] loaded: from-profile`，和 dump 的结果一致。

```text title="本文实测 · dsh --profile art --dump-config（三层都写了 greeting）"
# == dsh-hello-plugin, patched by <scratch>/dsh-try/home/profiles/art/cordis.patch.yml, <scratch>/dsh-try/home/cordis.patch.yml
- id: hello
  name: dsh-hello-plugin
  config:
    greeting: from-home
```

```text title="本文实测 · 再加 --patch ./off.yml（只写 disabled: true）"
# == dsh-hello-plugin, patched by <scratch>/dsh-try/home/profiles/art/cordis.patch.yml, <scratch>/dsh-try/home/cordis.patch.yml, <scratch>/dsh-try/work/off.yml
- id: hello
  name: dsh-hello-plugin
  config:
    greeting: from-home
  disabled: true
```

```text title="本文实测 · --dump-default-config 与 --patch ./bad.yml（id 不存在）"
# --dump-default-config 只显示 bundle 层
# == dsh-hello-plugin
- id: hello
  name: dsh-hello-plugin
  config:
    greeting: from-bundle

# --patch ./bad.yml 的 stderr
dsh: [<scratch>/dsh-try/work/bad.yml] patch: entry "nonexist" not found
```

由此给 bundle 作者两条建议，文档也是这么写的：默认值放在 schema 里，不要只放在 patch 里，因为用户可以覆盖你的行；覆盖更早层的某一行时要把所有键重写一遍，内置的 `web-app` 覆盖 `base` 就是这么做的（`packages/bundle/web-app/cordis.patch.yml:1-36`）。

禁用插件有两种方式。一种是在 profile 或 home 的 patch 里写 `- id: <行 id>` 加 `disabled: true`，作用在行级，可以撤销，也不卸载包；`base` 自己就用这种方式默认关掉了 `tool-plugin-manager`。另一种是 `dsh plugin remove`，把依赖和层一起删掉。热重载方面，`web` 模板启用了 `dsh-hmr`，会监视 profile manifest、两个用户 patch 和所有 `--patch` 文件；其他模板只在启动时应用配置。patch 如何按 id 协调、HMR 如何回滚，见[服务、事件、配置与插件实战](dsh_services_events_config_plugin.html)。

## 从 git 安装：要授权构建脚本，而且必须写 pnpm 打印的完整键

从 git 安装拿到的是源码，没人去跑 `build`，TypeScript 包缺了 `lib/` 就会加载失败。作者需要提供一个能独立运行的 `prepare` 脚本，pnpm 会在 git 安装后执行它（`publish.md:161-171`）。但 pnpm 10 起默认拦截 git 依赖的 `prepare`，用户第一次 `add` 一定失败，必须把 **pnpm 打印出来的确切键**加进 profile 的 `pnpm-workspace.yaml`。

文档把这件事定性为安全决定：这等于允许该包在安装时、在 agent 的任何沙箱之外执行代码，所以只应放行可信来源，并用 `github:you/hello-plugin#<sha>` 锁定 commit，防止作者之后的 push 悄悄改变要执行的内容（`publish.md:172-186`）。版本检查也“不是针对恶意代码的沙箱”。

实验用一个本地 git 仓库 `dsh-git-plugin`：`prepare` 把 `src/` 复制到 `lib/`，`lib` 写进 `.gitignore`，安装 spec 是 `git+file://…/gitplug#b0b1e5f…`。

```json title="本文实测 · dsh-try/work/gitplug/package.json"
{ "name": "dsh-git-plugin", "version": "0.1.0", "type": "module", "main": "lib/index.js",
  "files": ["lib", "cordis.patch.yml"],
  "scripts": { "prepare": "node build.mjs" },
  "engines": { "dsh": ">=0.2.0 <0.3.0" },
  "dsh": { "bundle": { "patch": "./cordis.patch.yml" } } }
```

```js title="本文实测 · dsh-try/work/gitplug/build.mjs"
import {mkdirSync,copyFileSync} from 'node:fs';mkdirSync('lib',{recursive:true});copyFileSync('src/index.js','lib/index.js')
```

### 两个 pnpm 版本的报错不一样，dsh 的提示只写了一种

pnpm 10.34.9 的报错要求把键加进 `onlyBuiltDependencies` 列表，pnpm 11.28.5 要求加进 `allowBuilds` 映射。两者给出的键都是 **`包名@git URL#完整 sha`**。dsh 在 pnpm 报错之后追加一行提示，不管哪个 pnpm 版本都叫用户写 `allowBuilds`（`apps/cli/src/plugin.ts:104-106`）；GUI 的 Plugin Manager “批准”按钮也只解析 pnpm 11 的 `allowBuilds:` 格式（`packages/boot/plugin-manager/src/build-approval.ts:8-26`）。

```text title="本文实测 · pnpm 10.34.9 首次 add 的报错（新 profile，新 store）"
 ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED  Failed to prepare git-hosted package fetched from "file://<scratch>/dsh-try/work/gitplug": The git-hosted package "dsh-git-plugin@0.1.0" needs to execute build scripts but is not in the "onlyBuiltDependencies" allowlist.
This error happened while installing a direct dependency of <scratch>/dsh-try/home/profiles/art10b
Add the package to "onlyBuiltDependencies" in your project's pnpm-workspace.yaml to allow it to run scripts. For example:
onlyBuiltDependencies:
  - "dsh-git-plugin@git+file://<scratch>/dsh-try/work/gitplug#b0b1e5fab6b2cdaf8a85d6e468ebb549be631b38"
...
dsh: git-hosted plugins build on install via their prepare script, which pnpm blocks until allowed — add the exact key pnpm printed above under allowBuilds in <scratch>/dsh-try/home/profiles/art10b/pnpm-workspace.yaml, then re-run
```

```text title="本文实测 · pnpm 11.28.5 首次 add 的报错"
[ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED] The git-hosted package "dsh-git-plugin@0.1.0" needs to execute build scripts but is not in the "allowBuilds" allowlist.
This error happened while installing a direct dependency of <scratch>/dsh-try/home/profiles/art11
Add the package to "allowBuilds" in your project's pnpm-workspace.yaml to allow it to run scripts. For example:
allowBuilds:
  dsh-git-plugin@git+file://<scratch>/dsh-try/work/gitplug#b0b1e5fab6b2cdaf8a85d6e468ebb549be631b38: true
...
dsh: git-hosted plugins build on install via their prepare script, which pnpm blocks until allowed — add the exact key pnpm printed above under allowBuilds in <scratch>/dsh-try/home/profiles/art11/pnpm-workspace.yaml, then re-run
```

### 实测：哪种写法能放行

逐一改 `pnpm-workspace.yaml` 后重跑 `add`，结果如下表。**决定成败的是键是否完整**：只写包名在两个版本上都失败；写完整键时，pnpm 10.34.9 用 `onlyBuiltDependencies` 和 `allowBuilds` 都能放行（`allowBuilds` 这一项在全新 profile 和全新 store 上复测过，`prepare` 确实执行，生成了 `lib/index.js`），pnpm 11.28.5 用 `allowBuilds` 放行，启动后打印 `[git-plugin] loaded`。所以文档示例 `allowBuilds:` 下写 `dsh-hello-plugin: true` 是错的；pnpm 10 下 dsh 的 `allowBuilds` 提示本身可用，只是和 pnpm 自己建议的键名不同，容易让人以为两者冲突。

| `pnpm-workspace.yaml` 写法 | pnpm 10.34.9 | pnpm 11.28.5 |
| --- | --- | --- |
| `allowBuilds:` 下写 `dsh-git-plugin: true`（文档示例的形式） | 失败 | 失败 |
| `onlyBuiltDependencies:` 下写 `- dsh-git-plugin` | 失败 | 未测 |
| `onlyBuiltDependencies:` 下写完整键（pnpm 10 报错建议的形式） | 成功 | 未测 |
| `allowBuilds:` 下写 `"完整键": true`（pnpm 11 报错建议的形式） | 成功 | 成功 |

键里带着 git URL 和 commit sha，意味着**授权绑定在具体的源码版本上**，换一个 commit 就要重新授权。“锁定 commit”因此不只是文档里的一条建议，pnpm 的允许列表本身就是这样实现的。

复测时还看到一个现象：pnpm 10.34.9 下，一个**全新的** profile 在默认 store 里安装同一个 sha 时，没有任何授权也成功了，`node_modules/dsh-git-plugin/lib/index.js` 已经存在，因为另一个 profile 之前授权并构建过它，构建结果被 store 复用了；pnpm 11.28.5 在同样情况下仍然要求授权。也就是说在 pnpm 10 上，授权的实际作用范围可能是整个 store，而不只是写了允许列表的那个 profile。这一点只观察到一次，没有进一步查 pnpm 的实现。

不想让用户承担这一步，可以发布到 npm（发布时带上构建好的 `lib/`），或者分发 `pnpm pack` 打出的 tarball，文档说这两种都“不需要授权”。tarball 这条路本文没有实测。

```yaml title="本文实测 · 放行后的 profiles/art11/pnpm-workspace.yaml（pnpm 11）"
packages:
  - .

nodeLinker: hoisted
autoInstallPeers: false

allowBuilds:
  "dsh-git-plugin@git+file://<scratch>/dsh-try/work/gitplug#b0b1e5fab6b2cdaf8a85d6e468ebb549be631b38": true
```

## 发布到 npm：用 `engines.dsh` 声明兼容，peer 只用来共享宿主实例

### import 了宿主包就要声明 peer，否则启动时才失败

本文第一版 `hello-plugin` 的 `index.js` 里 `import Schema from '@deepseek-ai/schemastery'`，但 `package.json` 没有声明它。`add` 和 `--dump-config` 都正常，真正启动时却报 `ERR_MODULE_NOT_FOUND`，这一行不激活，其他插件照常启动。补上 `"peerDependencies": { "@deepseek-ai/schemastery": "*" }` 后**不用重装**，直接重启就打印出 `[hello-plugin] loaded: from-profile`。

原因在于 profile 的布局：hoisted、`autoInstallPeers: false`。**声明为 peer 的包才会走运行时解析**，用安装方的那一份；没声明的宿主包不会被顺手解析到。文档的规则是：需要和宿主共享实例的 dsh 包，同时写进 `peerDependencies` 和 `devDependencies`（后者用于类型检查和独立测试）；有独立版本的第三方库放 `dependencies`（`publish.md:105`）。但入门示例的 `package.json` 里一个 peer 都没写，因为它的 `index.js` 什么也不 import；一旦照 `config.md` 加上 Schemastery，就会撞上这个错误。

```text title="本文实测 · 缺 peer 时 dsh --profile demo 的启动输出（堆栈节选）"
dsh: warning: 1 entry did not activate
hello (dsh-hello-plugin): failed to import: Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@deepseek-ai/schemastery' imported from <scratch>/dsh-try/work/hello-plugin/index.js
    at Object.getPackageJSONURL (node:internal/modules/package_json_reader:331:9)
    at packageResolve (node:internal/modules/esm/resolve:784:25)
    ...
```

```text title="本文实测 · 补上 peerDependencies 后重启（未重装）"
[hello-plugin] loaded: from-profile
```

### 兼容性写 `engines.dsh`，不要照抄官方包的精确 rc peer

兼容性优先用 `engines.dsh` 的 SemVer 范围声明，例如 `">=0.2.0 <0.3.0"`。规则是：声明了 `engines.dsh` 时只看它，**完全忽略 peer**；没声明时，`@deepseek-ai/dsh` 和 `@deepseek-ai/dsh-*` 的每个 peer 范围都必须满足；两者都没有就不设限。prerelease 版本参与匹配，`workspace:` 这类非法范围视为不兼容（`packages/boot/app-boot/README.md:54`）。本文的 `hello-plugin` 写的是 `>=0.2.0 <0.3.0`，在 dsh `0.2.2-rc.5` 上被接受，这和 npm 默认把 prerelease 排除在范围外的语义不同。

官方包发布时，源码里的 `workspace:*` peer 被替换成了**精确的 `0.2.2-rc.5`**，而且这个包没有 `engines.dsh`，所以它只能在恰好是这个 rc 的 dsh 上通过检查。第三方作者照抄这种写法，插件就会被锁死在一个 rc 版本上。正确做法是写 `engines.dsh`，peer 用宽范围（如 `*`），只用来让运行时共享宿主的实例。

```text title="本文实测 · npm view @deepseek-ai/dsh-subagent-codex@0.2.2-rc.5 peerDependencies engines --json"
{
  "@deepseek-ai/cordis": "~4.1.0",
  "@deepseek-ai/dsh-llm": "0.2.2-rc.5",
  "@deepseek-ai/dsh-session": "0.2.2-rc.5",
  "@deepseek-ai/dsh-timeout": "0.2.2-rc.5",
  "@deepseek-ai/dsh-subagent": "0.2.2-rc.5",
  "@deepseek-ai/dsh-subprocess": "0.2.2-rc.5"
}
```

发布清单还有几条。`files` 里必须列出 `cordis.patch.yml`（多文件 patch 要逐个列出）和构建好的 `lib/`，否则发布后 patch 或代码会缺失；官方包还会加上类型、`src` 和图标。要让别人发现你的插件，可以给仓库加 GitHub topic `dsh-plugin`（`README.md:46`），或者发布一份插件索引 JSON（`docs/user/guide/plugin-market.md:302-436`）。本文没有执行真正的 `npm publish`。

## 综合练习：一个带配置、工具、闸门和审计日志的 bundle

下面这个插件把前面各节验证过的要素拼在一起：用 `Config` 控制问候语和黑名单，注册 `greet` 工具，在 pre-execute 里按黑名单拒绝，用 guard 拦截空名字，在 `tools/result` 上打审计日志，用 `ctx.effect` 托管一个定时器。

**这份组合代码是示意，没有作为整体运行过。**其中各个机制分别在第 1 到 8 节实测过：钩子顺序和拒绝路径用的是裸宿主，bundle 外壳的结构和 `hello-plugin` 相同。把 `@deepseek-ai/dsh-tools` 声明为 peer 后能否在 profile 里解析到，是比照 schemastery 的结果推断的。日志要有 exporter 才会出现在终端。

```js title="示意（未运行） · dsh-greet-guard/index.js"
import Schema from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'greet-guard'
export const inject = ['tools']
export const Config = Schema.object({
  greeting: Schema.string().default('Hello'),
  blocked: Schema.array(Schema.string()).default([]),
})

export function apply(ctx, config) {
  const log = ctx.logger('greet-guard')              // 需要 exporter 才有输出
  ctx.tools.register(defineTool({
    name: 'greet',
    description: 'Greet someone by name.',
    parameters: { name: { type: 'string', required: true } },
    output: { schema: { type: 'string' }, render: (_a, v) => [{ type: 'text', text: v }] },
    async execute(args) { return `${config.greeting}, ${args.name}!` },
  }))
  ctx.on('tools/pre-execute', async (exec, next) => {
    const args = exec.arguments                       // 还没校验，可能不是对象
    const who = args && typeof args === 'object' ? args.name : undefined
    if (exec.name === 'greet' && config.blocked.includes(who))
      return { kind: 'deny', reason: `${who} is blocked by policy.` }
    return next()                                     // waterfall：放行必须调用 next()
  })
  ctx.tools.guard(exec =>
    exec.name === 'greet' && exec.arguments?.name === '' ? 'empty name' : undefined)
  ctx.on('tools/result', (exec, result) => {
    log.info(`${exec.name} isError=${result.isError}`) // 只读，不被 await
  })
  ctx.effect(() => {
    const t = setInterval(() => {}, 60_000)
    return () => clearInterval(t)                     // 卸载时清理，不要用 ctx.on('dispose')
  })
}
```

```json title="示意（未运行） · dsh-greet-guard/package.json"
{
  "name": "dsh-greet-guard",
  "version": "0.1.0",
  "type": "module",
  "main": "index.js",
  "files": ["index.js", "cordis.patch.yml"],
  "engines": { "dsh": ">=0.2.0 <0.3.0" },
  "peerDependencies": { "@deepseek-ai/schemastery": "*", "@deepseek-ai/dsh-tools": "*" },
  "dsh": { "bundle": { "patch": "./cordis.patch.yml" } }
}
```

安装和验证的步骤如下。按第 3 节的实测推断，`greet(Mallory)` 会走 pre-execute → post-execute → result，返回 `Error: Mallory is blocked by policy.`。覆盖配置时要记得：profile patch 里写出的 `config` 会整块替换 bundle 层的 `config`，所以 `blocked` 也要重新写。要从 GitHub 分发，作者要加 `prepare` 脚本，用户要把 pnpm 打印的完整键写进允许列表。

```yaml title="示意（未运行） · dsh-greet-guard/cordis.patch.yml"
- insert:
    - id: greet-guard
      name: dsh-greet-guard
      config:
        blocked: [Mallory]
```

```bash title="示意（未运行） · 安装与核对"
dsh plugin --profile demo add ./dsh-greet-guard
dsh --profile demo --dump-config | grep -A6 '# == dsh-greet-guard'
# 覆盖配置：写进 $DSH_HOME/profiles/demo/cordis.patch.yml
#   - id: greet-guard
#     config: { greeting: Hi, blocked: [Mallory, Eve] }
dsh --profile demo
```

## 文档与实际行为的出入，以及没有验证的部分

下表汇总前文发现的出入。“实测”指本文运行过，“源码”指对照 commit `4b399c3` 的源码。

| 出入点 | 文档或常见说法 | 实际情况 |
| --- | --- | --- |
| 依赖消失后的状态 | `framework/index.md:38`：ACTIVE → DISPOSED，服务回来后再加载 | 回到 PENDING(0)，再变回 ACTIVE；源码里 DISPOSED 表示“不能再启动”（实测、源码） |
| 被拒绝的工具调用 | 容易理解成“拒绝即结束” | 拒绝结果阶段为 post-result，仍会进入 post-execute 和 `tools/result`（实测、源码） |
| 会话事件名 | `turn/*`、`tool/result` 等看起来像 Cordis 事件 | 它们是会话记录类型，要监听 `session/event` 再按 `event.type` 分支（文档本身写明） |
| `ctx.logger` | 教程用 `console.log`，没提 logger 输出 | 没有 exporter 插件时 `ctx.logger.info` 不输出也不报错（实测） |
| `dispose` 事件 | 来自 Koishi / 旧版 Cordis 的习惯 | Cordis 4 没有 `dispose`、`ready` 事件，`ctx.on('dispose')` 从不触发（实测、源码） |
| 插件路径 | `basic/index.md` 要求绝对路径 | `config.md` 的同一份 YAML 用了 `./src/my-plugin.ts`（文档自相矛盾） |
| 配置层数 | 本文旧版说“五层” | 官方是 4 类层，层数随 bundle 和 `--patch` 数量变化，末尾还可能追加遥测 patch（文档、源码） |
| “整块替换 config” | 容易理解成整行替换 | 只替换 patch 里写出的 `config` 键，只写 `disabled` 不会清掉 config（实测） |
| git 构建授权 | `publish.md:174-177` 示例 `allowBuilds: { dsh-hello-plugin: true }` | 只写包名在 pnpm 10 和 11 上都无效，必须写 pnpm 打印的完整键（含 git URL 和 sha）（实测） |
| pnpm 10 的键名 | 文档与 dsh 提示：pnpm 10 起用 `allowBuilds` | pnpm 10.34.9 的报错建议 `onlyBuiltDependencies`；实测两者写完整键都能放行，GUI 批准按钮只认 pnpm 11 格式（实测、源码） |
| 入门 bundle 示例 | 示例 `package.json` 没有 peer | import 了宿主包就要声明 peer，否则 add 成功、启动时 `ERR_MODULE_NOT_FOUND`（实测） |
| 版本声明 | 官方包发布后 dsh-* peer 是精确 rc 版本、无 `engines.dsh` | 照抄会锁死在一个 rc 上；应写 `engines.dsh`，有它时 peer 不参与判定（实测、文档） |
| Node 版本 | 源码根要求 `^24.22.3 \|\| >=26.13.1` | 已发布的 0.2.2-rc.5 要求 `>=24.9.0`，在 24.22.2 上能装能跑（实测） |

以下内容**没有验证**：

- 官方 `pnpm dsh web --patch` 源码教程路径没有端到端跑通（完整构建报 `ERR_PNPM_ENOSPC`）；第 2 节的教程行为是在裸宿主上验证的。
- tool 教程从 `scratch-plugin/` 按值 import `@deepseek-ai/dsh-tools`，在 pnpm 严格链接下能否解析。
- 综合练习插件没有作为整体运行；把 `dsh-tools` 声明为 peer 后在 profile 里能否解析是推断。
- 真实的 `github:owner/repo#sha` 简写下 pnpm 打印的键长什么样；实验用的是 `git+file://`。
- pnpm 10 下“授权随 store 复用”只观察到一次，没有查实现；pnpm 10 系列从哪个小版本起认 `allowBuilds` 也没有确认。
- `dsh plugin update`、`pnpm pack` 打出的 tarball 安装、真正的 `npm publish` 都没有执行。
- 多个 bundle 覆盖同一行时的先后顺序没有测试（按文档应是 bundles 列表中靠后者胜）。
- `web` profile 配了哪个 logger exporter、哪些级别；zod 等其他 Standard Schema 库能否用作 `Config`。
- 全局安装 dsh 时 npm 提示 5 个包（`node-pty`、`koffi` 等）的安装脚本未授权，本文没有授权它们；`dsh plugin` 和 base profile 启动不受影响，对 PTY 类功能的影响未验证。

> 总结成给新手的最短路径：先在裸宿主上验证工具和钩子；`package.json` 写 `engines.dsh` 和宽范围 peer，把 import 的每个宿主包都声明成 peer；用本地目录 `add` 安装，用 `--dump-config` 核对分层，再真实启动一次确认插件激活；最后发布到 npm，避开 git 构建授权这一步。
