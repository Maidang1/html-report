# 从 apply(ctx) 写到可安装的 dsh 插件

给 dsh（DeepSeek Harness，`@deepseek-ai/dsh`）写插件，本质上就是写一个 Cordis 插件，再把它包成一个声明了 `dsh.bundle` 的 npm 包。插件模块可以导出 `name`、`inject`、`Config` 和 `apply(ctx, config)`。**`apply` 要等 `inject` 里的服务全部就绪、`Config` 同步校验通过才会被调用**；依赖消失时插件回到 PENDING，依赖回来后 `apply` 重新执行。工具用 `ctx.tools.register(defineTool(...))` 注册。一次工具调用实测按 **`tools/pre-execute` → `ctx.tools.guard()` → `tools/execute` 包裹层 →（参数校验）→ 工具体 → `tools/post-execute` → `tools/result`** 的顺序经过各扩展点；被拒绝的调用照样会走到 post-execute 和 result。打包时，bundle 只需要三个文件：`package.json`（含 `dsh.bundle.patch`）、`cordis.patch.yml` 和入口 JS。用户执行 `dsh plugin --profile <p> add <spec>` 后，dsh 在 `$DSH_HOME/profiles/<p>/` 里调用 PATH 上的 pnpm 安装，再把这个包追加进 `dsh.profile.bundles`。配置按“各 bundle 的 patch（按列表顺序）→ profile patch → home patch → 各 `--patch`”叠加，官方文档说的是这 4 类层，“五层”只是把 `dsh-base` 单独数成一层。实测还发现几处文档和实际行为不符，最影响新手的有三处：一是文档里 `allowBuilds: { 包名: true }` 的写法在 pnpm 10 和 11 上**都不生效**，必须复制 pnpm 打印出来的完整键（包名@git URL#sha）；二是 pnpm 10 要求的键名其实是 `onlyBuiltDependencies`；三是插件只要 import 了 `@deepseek-ai/schemastery` 却没声明成 peer，启动时就会报 `ERR_MODULE_NOT_FOUND`。下文结论都对应仓库 main 分支 commit `4b399c3`（2026-11-22）。验证分两部分：工具和钩子用的是一个裸宿主，由同版本 npm 包（`0.2.3-alpha.2`）拼成；打包和安装用的是全局安装的 `@deepseek-ai/dsh@0.2.2-rc.5` CLI，在 pnpm 10.34.9 和 11.28.5 下各跑了一遍。dsh 目前仍是 developer preview，细节可能随版本变化。

> **证据标注**：**[运行]** 表示本次实际执行过的命令或脚本，输出原样引用；**[源码]** 表示读过源码；**[文档]** 表示读过官方文档。源码链接统一指向 `deepseek-ai/deepseek-harness@4b399c3`。完整源码构建（`pnpm install && pnpm run build`）因为临时盘只有 3.9 GB，报 `ERR_PNPM_ENOSPC` 失败，所以 `pnpm dsh web --patch` 这条官方教程路径**没有端到端跑通**。改用两条替代路径：裸宿主跑工具和钩子，已发布的 CLI 跑打包和安装。

## “内核之外全是插件”：插件就是带 apply 的 Cordis 模块

官方教程给插件的定义很简单：插件是一个导出 `apply` 函数的模块，框架加载它时调用 `apply`，并传入 `ctx`，插件通过 `ctx` 注册各种能力。最小形态就是 `export const name = 'my-plugin'` 加上 `export function apply(ctx: Context) {}` 两行 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/index.md#L15-L29))。插件有三种写法：函数模块，对象（`export default { name, inject, apply }`），或者继承 `Service` 的类。文档建议大多数情况用函数形态，只有要给其他插件提供服务时才用类 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/index.md#L105-L138))。字段的权威定义在 vendored Cordis 的 `Plugin.Base` 里 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/registry.ts#L97-L132))。`name` 可选，只用于 Fiber 诊断和 logger 命名。`inject` 列出必需服务，可以写成数组，也可以写成“名字→配置”的映射。`Config` 必须是 Standard Schema 校验器。另外还有 `provide` 和 `intercept` 两个字段，入门教程里没有讲。

**`apply` 什么时候被调用，只由 `inject` 决定。**[运行] 实验里先挂载一个声明了 `inject: ['tools']` 的插件，这时还没有 `dsh-tools`：它的 Fiber 状态是 `0`（PENDING），`apply` 没有执行。挂上 `dsh-tools` 之后，输出 `[apply] runs now; ctx.tools = object`，状态变为 `2`（ACTIVE）。再把 tools 的 Fiber 释放掉，插件打印 `[effect] cleanup`，状态**回到 `0`**。重新挂上 tools 后，`apply` 又执行了一次。配置校验发生在 `apply` 之前，而且只能同步进行：Cordis 调用 `Config['~standard'].validate(config)`，校验器如果是异步的，会直接抛出 `Async config validation is not supported` ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/fiber.ts#L72-L91))。[运行] 把 `{ greeting: 42 }` 传给 `Schema.string()` 字段，`await root.plugin(...)` 被拒绝，错误信息是 `ValidationError invalid config: $.greeting expected string but got 42`。

“内核之外全是插件”这句话有据可查。README 自称采用 **everything-is-a-plugin** 架构 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/README.md#L7))。`architecture.md` 写道：模型适配器、工具注册表、会话日志乃至 agent loop 本身都是插件，“没有需要打补丁的特权内核” ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/architecture.md#L7-L11))。更能核实的是 extension cookbook 里的“功能→机制”对照表：hook 系统、`/goal`、`/loop`、压缩、内置工具、超时重试、权限审批、MCP、模型适配、热重载，每一行都对应某个文档化扩展点上的监听器或注册，并且注明“没有一行修改 loop” ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/extension-cookbook.md#L141-L175))。源码也印证了这一点：工具注册表本身就是 `class ToolsService extends Service`，它依赖 `systemPrompt` ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/index.ts#L99-L118))，而 `systemPrompt` 又依赖 `sessionProjections` ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/system-prompt/src/index.ts#L487))。因此 `inject: ['tools']` 实际上是在等待一整条依赖链。还要注意，`inject` 只保证注册表存在，不保证执行器存在：如果没有挂载 `dsh-tool-executor-native` 这类执行器，调用工具会得到 `TOOL_EXECUTOR_UNAVAILABLE` ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/README.md#L75-L84))。

Fiber 的六个状态、`ctx.effect` 的返回形态、卸载顺序和三条重载路径，本文不展开，详见 `deepseek/dsh_lifecycle_and_effects.md`。服务提供与注入、五种事件分发模式、`cordis.yml` 的协调方式和 HMR，详见 `deepseek/dsh_services_events_config_plugin.md`。

## 官方教程四步走：从一行 console.log 到带配置的 greet 工具

官方的学习顺序是：README 的“从源码运行”（`git clone` → `pnpm install` → `pnpm run build` → `pnpm dsh web`，Web UI 地址是 `http://127.0.0.1:3080`）([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/README.md#L29-L41)) → `basic/index.md`（第一个插件）→ `basic/tool.md` → `basic/config.md` → `basic/publish.md`，然后才进入 framework 文档和 cookbook。仓库要求 `pnpm@11.28.5`，Node 版本 `^24.22.3 || >=26.13.1` ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/package.json#L7-L9))。

第一步，在仓库根目录建 `scratch-plugin/src/my-plugin.ts`，里面只写 `export const name = 'hello-plugin'`，再加一个打印 `[hello-plugin] plugin loaded!` 的 `apply`。然后写一个 `scratch-plugin/cordis.yml`，用 `- insert: [{ id: hello, name: '/absolute/path/.../my-plugin.ts' }]` 插入一行。用 `pnpm dsh web --patch ./scratch-plugin/cordis.yml` 启动后，终端里应该能看到这行打印 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/index.md#L31-L64))。文档特别强调**路径必须是绝对路径**，原因是 patch 文件不会改变 loader 解析模块路径时用的 profile 目录。

第二步，把这个插件改造成工具插件 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/tool.md#L7-L46))：

```ts
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'greet-tool'
export const inject = ['tools']

export function apply(ctx: Context) {
  ctx.tools.register(defineTool({
    name: 'greet',
    description: 'Greet someone by name.',
    parameters: { name: { type: 'string', required: true, description: 'The name to greet' } },
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    async execute(args) { return `Hello, ${args.name}!` },
  }))
}
```

`register` 返回一个 disposer。`output: { schema, render }` 必填，`render` 必须是函数，`timeoutMs` 必须是正的有限数，`run_code` 是保留名，同一层里不能重名 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/index.ts#L120-L152))。`execute` 应该返回一个规范的 JSON 值，由 `render` 把它转换成给模型看的内容块，不要在 `execute` 里直接返回内容块。只有抛出 `HarnessError` 子类，结果里才会保留稳定的 `error.name` 和 `error.code` ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/adding-a-tool.md#L41-L50))。[运行] 实测结果如下：`greet(Ada)` 返回 `isError:false`；工具体里抛出普通 `Error('kaboom')`，得到的结果是 `isError:true`，`error` 中只有 `message`，没有 `info`；用 `arguments: '{}'` 调用，得到 `ToolArgsError`/`INVALID_ARGS`；插件释放后，`schemas()` 变成 `[]`，再调用 `greet` 得到 `UNKNOWN_TOOL`，说明注册确实是随插件一起撤销的 effect。工具体收到的 `args` 是冻结对象。

第三步是加配置。插件导出一个 `Config` 类型，再导出一个同名的 Schemastery schema，默认值写在 schema 上，例如 `Schema.object({ greeting: Schema.string().default('Hello'), ... })`。这样 `apply(ctx, config)` 拿到的就是“用户值或默认值”。文档明确说不要导出普通对象，因为普通对象没有实现 Standard Schema ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/config.md#L7-L45))。教程写的是 `import Schema from`，仓库内部的惯例则是 `import z from '@deepseek-ai/schemastery'`，两者等价 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/todo/tool-todo/src/index.ts#L22-L43))。[运行] 带 `Config` 的 greet 插件以 `{ greeting: 'Hi there' }` 挂载后，打印 `config= {"greeting":"Hi there"}`，调用工具返回 `Hi there, Ada!`。第四步是 publish，见后文打包一节。

这条教程路径上有两个坑。第一，`config.md` 的 YAML 示例在同一个 `scratch-plugin/cordis.yml` 里写的是相对路径 `name: './src/my-plugin.ts'` ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/config.md#L34-L43))，和前面“必须用绝对路径”的说法矛盾，照绝对路径写更稳妥。第二，tool 教程从仓库根目录下的 `scratch-plugin/` **按值 import** `@deepseek-ai/dsh-tools`，但根目录 `package.json` 没有声明这个依赖，pnpm 又使用严格的符号链接布局，所以 Node 能否解析到它存疑。第一个教程只用了 `import type`，编译后会被擦除，不受影响。这一点没有验证。如果不想构建整个 monorepo，可以把 `dsh-session-projection`、`dsh-system-prompt`、`dsh-tools`、`dsh-tool-executor-native` 依次挂到一个空的 `new Context()` 上，再直接调用 `ctx.tools.execute({ calls, signal })`。这样不需要 API key，也不需要 Web UI，就能验证工具和钩子。本文的工具实验都是这么做的。

## 一次工具调用依次经过五个扩展点，拒绝也会走完全程

`packages/core/tools/README.md` 给出的顺序是：`tools/pre-execute` → 注册的单调 guard → `tools/execute` 包裹层 → `tools/post-execute` → 定义自带的 `finalizeContent` → 只读的 `tools/result` ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/README.md))。源码 `pipeline.ts` 的 `prepare()` 先跑 pre-execute 的 waterfall，只有决定是 `allow` 时才调用 `checkGuards`；任何拒绝都会被包装成 `Error: <reason>` 的 `isError` 结果，阶段标记为 `post-result`，也就是说它**仍然会进入 post-execute** ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L185-L233))。`tools/result` 在分发前先冻结 `exec`，每个回调各自包在 try 里，回调出错只记一条 `logger.warn`，不影响结果，也不会被 await ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L420-L438))。

[运行] 在五个阶段各挂一个监听器，调用 `greet(Ada)`，实际轨迹如下：

```
1 [pre-execute] greet {"name":"Ada"}
2 [guard] greet
3 [execute wrapper] before
  [execute body] args= {"name":"Ada"} frozen? true
3 [execute wrapper] after isError= false
4 [post-execute] isError= false value= "Hi there, Ada!"
5 [tools/result] frozen? true content= [{"type":"text","text":"Hi there, Ada!"}]
```

另外几种情况也一并验证了。pre-execute 拒绝 `Mallory` 时，轨迹是 1 → 4 → 5，guard 和 execute 被跳过，结果为 `Error: Mallory is blocked by policy.`。guard 拒绝 `Eve` 时，轨迹是 1 → 2 → 4 → 5。post-execute 返回 `{ kind: 'accept', value: 'HI THERE, SHOUT!' }` 后，新值会**重新经过 `output.render`**，最终内容就是这个大写字符串。参数缺失（`{}`）时，轨迹是 1 → 2 → 3 before → 3 after（`isError=true`），工具体根本没有执行，这说明**参数校验发生在 `tools/execute` 内部**。

| 扩展点 | 分发方式 / 签名 | 能做什么 | 怎么拒绝 |
|---|---|---|---|
| `tools/pre-execute` | waterfall，`(exec, next) => Promise<PreToolDecision>` | 可扩展的 allow / deny / ask / cancel 策略；`ask` 只有在挂了 `approval` 服务时才生效，否则按 `APPROVAL_UNAVAILABLE` 拒绝 | 返回 `{ kind: 'deny', reason }` 或不调用 `next()` |
| `ctx.tools.guard(fn)` | 同步，`(exec) => string \| denial \| undefined` | 最后一道单调拒绝，没有 allow 结果，后续监听器无法推翻 | 返回字符串 |
| `tools/execute` | waterfall 包裹分发，`(exec, next) => Promise<Result>` | 截止时间、重试、计量；**唯一可以替换 `exec.signal` 的地方**，替换后必须恢复 | 改写 `next()` 的结果 |
| `tools/post-execute` | waterfall，`(exec, result, next)` | 替换展示内容**或**值（二者只能选一个，失败结果的值不能替换）、附加上下文 | `{ kind: 'block', feedback }` |
| `tools/result` | emit，只读，不被 await | 日志、审计、遥测 | 不能拒绝 |

表中签名和分发方式见 [源码 events.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/events.ts#L6-L57)，选择规则见 [文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/adding-a-tool.md#L58-L60)，`ask` 的降级逻辑见 [源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L440-L492)。内置的 `timeout-policy` 是 `tools/execute` 包裹层的标准范例，全文 81 行：先读取工具的 `timeoutMs`，没有就直接 `next()`；有的话创建 deadline，替换 `exec.signal`，在 `finally` 里恢复 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/guard/timeout-policy/src/index.ts#L25-L81))。`dsh-hooks-claude-code` 演示了 post-execute 的礼貌写法：先 `await next()`，再把自己的上下文合并到下游的决定上，这样后面的监听器仍然可以 block ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/experimental/hooks-claude-code/src/index.ts#L246-L282))。

由此可以推出写闸门时的三条规矩。第一，pre-execute 和 guard 看到的是**未经校验**的模型参数，读取 `exec.arguments` 时要做防御。第二，post-execute 先检查 `result.isError`，不要默认工具已经执行过。第三，必须在下一步之前完成的事情不要放进 `tools/result`。dsh 里没有“工具权限”这个属性，权限完全由钩子插件决定，工具插件本身保持“无策略”。同一个 waterfall 上多个监听器的先后顺序、作用域过滤和 `thisArg`，见 `deepseek/dsh_services_events_config_plugin.md`。

## 监听事件与打日志：session/event、ctx.logger 和不存在的 dispose 事件

插件作者需要的监听 API 只有 `ctx.on(name, handler)`，监听器本身就是 effect，插件卸载时自动移除 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/index.md#L66-L85))。一个常见误会是：`turn/*`、`step/*`、`tool/call`、`tool/result`、`compaction/*` 其实是**持久化会话记录的类型名**，并不是同名的 Cordis 事件。要观察它们，得监听 `session/event`，再按 `event.type` 分支处理 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/framework/events.md#L102-L106))。`session/event` 是 emit 模式，作用域限定在 agent 上的监听器只能收到经由该 agent 进入的会话的事件 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/session/src/index.ts#L78-L93))。实时 token 流来自 `agent/assistant-stream` ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/extension-cookbook.md#L35-L92))。官方的日志插件示例监听的是 `tools/result`，并且写了一行副作用 import `import '@deepseek-ai/dsh-tools'`，目的只是引入 `Events` 的声明合并以获得类型 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/framework/events.md#L119-L138))。

日志方面，`ctx.logger` 是 Cordis 内置的 `LoggerService`，可以用 `ctx.logger(name)` 拿到具名 logger，级别有 error、info、warn、debug ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/logger.ts#L7-L8))。但它只负责产生日志，输出要靠 exporter 插件，例如 vendored 的 `logger-console` ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/logger-console/README.md))。[运行] 在没有 exporter 的裸宿主里，`ctx.logger.info(...)` 没有任何输出，也不报错。教程里用 `console.log` 是图省事。正式插件建议用 `ctx.logger`，因为日志会带上插件名。至于 `web` profile 配置了哪个 exporter、开了哪些级别，没有核实。熟悉 Koishi 的人还要注意一点：Cordis 4 的内置事件只有 `internal/*` 一类，**没有 `dispose`/`ready`** ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/events.ts#L330-L360))。[运行] `ctx.on('dispose', …)` 在 `fiber.dispose()` 时从未触发，而 `ctx.effect` 的清理函数正常执行了。正确做法是在 `apply` 里做初始化，清理工作交给 `ctx.effect` 返回的 disposer。

## bundle、profile 与 dsh plugin：pnpm 的一层薄包装

**bundle** 就是在 `package.json` 里声明了 `"dsh": { "bundle": { "patch": "./cordis.patch.yml" } }` 的普通 npm 包。它对外贡献的是**一层配置 patch**，在 patch 里用**包名**而不是相对路径引用插件代码，比如 `- insert: [{ id: hello, name: dsh-hello-plugin }]`，这样 Node 解析能找到安装后的代码。**profile** 则是 `$DSH_HOME/profiles/<name>/` 这个目录，它的 manifest 声明 `dsh.profile`，说明由哪些 bundle 按什么顺序组成。文档的原话是：bundle 是作者分发的东西，profile 是用户启动的东西，“Nothing is both” ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/publish.md#L9-L62))。`dsh.bundle` 的全部字段是：必填的 `patch`（可以是字符串，也可以是有序列表；列表里的多个文件合起来算**同一层**），`platforms?`（Node 平台名或 `wsl`，宿主不匹配时整层跳过），`listedByDefault?` ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/util/package-manifest/src/types.ts#L31-L84))。判断一个包是不是 bundle，唯一标准是 manifest 里 `dsh.bundle.patch !== undefined`，不会去读 JS ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/boot/plugin-manager/src/operations.ts#L75-L85))。bundle 不一定要自带代码：`subagent-codex` 的 patch 只插入了两行，其中一行引用的是**别的包** `@deepseek-ai/dsh-tool-subagent`，并给它传了配置 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/subagent/subagent-codex/cordis.patch.yml#L1-L9))。仓库内置的 `base`（patch 564 行）、`web-app`（657 行，由 5 个文件组成的多文件 patch）、`headless`、`acp-app` 等都是同样的结构。

[运行] 第一次执行 `dsh plugin --profile demo add ./hello-plugin` 时，输出 `dsh: initialized profile demo at …/profiles/demo` 和 `Done in 189ms using pnpm v10.34.9`。生成的 profile 目录里有 `package.json`（权限 `0600`）、`cordis.patch.yml`（注释加 `[]`）、`pnpm-workspace.yaml`、`pnpm-lock.yaml`、`node_modules/` 和 `.plugin-manager/logs/`。其中 `package.json` 的内容是 `{"name":"dsh-profile-demo","private":true,"dependencies":{"dsh-hello-plugin":"link:/…/hello-plugin"},"dsh":{"profile":{"bundles":["@deepseek-ai/dsh-base","dsh-hello-plugin"]}}}`，和文档示例一致。`pnpm-workspace.yaml` 里固定写着 `nodeLinker: hoisted` 和 `autoInstallPeers: false`。源码注释解释了原因：扁平的 `node_modules` 让插件缺失的 peer（cordis 等）走运行时解析，所有插件因此共享安装方唯一的 cordis 实例 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/boot/app-boot/src/profile.ts#L174-L217))。如果 profile 名不是内置模板，初始 bundle 列表只有 `@deepseek-ai/dsh-base` ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/util/official-bundles/src/bundles.ts#L48))。

`dsh plugin --profile <name> <args…>` 会把参数原样转发给 PATH 上的 pnpm，工作目录设为 profile 目录。`add`、`remove`、`why`、`update` 都能用。找不到 pnpm 时退出码是 127。`.` 或 `../x` 这类相对路径先相对**调用目录**解析 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/apps/cli/reference/README.md#L85))。[运行] 两次测试中，dsh 打印的 pnpm 版本随 PATH 而变，dsh 并不自带固定版本的 pnpm。每次 pnpm 成功后，dsh 都会对账：新增依赖如果是 bundle，就在校验平台、预解析 patch YAML 之后追加到列表末尾；列表没有变化就不写文件 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/boot/plugin-manager/src/operations.ts#L87-L128))。[运行] 装一个没有 `dsh` 字段的包，安装成功，但 dsh 打印 `warning: plain-lib declares no dsh.bundle — installed as a plain dependency, not a profile layer`，bundles 列表不变。执行 `remove plain-lib dsh-hello-plugin` 后，依赖清空，bundles 回到只有 `["@deepseek-ai/dsh-base"]`。

**版本检查在安装和启动时各做一次。**安装前，路径类 spec 直接读取它的 `package.json`，registry 类 spec 先跑 `pnpm view … engines peerDependencies`，git 和 tarball 只能装完后再检查 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/boot/plugin-manager/src/operations.ts#L157-L200))。[运行] 安装一个 `engines.dsh: "<0.2.0"` 的包，输出 `installation rejected: Plugin old-bundle@1.0.0 is incompatible with dsh 0.2.2-rc.5 … Running it may cause crashes or data loss` 和 `nothing was installed`，并给出 `allow-version old-bundle@1.0.0 --dsh-version 0.2.2-rc.5 --accept-risk` 的豁免命令，退出码 1。豁免的粒度是“精确包版本 → 精确 dsh 版本”，记录在 profile 的 `compatibility.json` 里，升级插件或 dsh 后都不会继承 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/boot/app-boot/README.md#L54-L58))。启动时，不兼容的 bundle 会被跳过并列入 `skippedBundles`，不会让启动失败。不过 profile patch 或用户 patch 出错**仍然会让启动失败**，而且空文件或只有注释的 patch 文件也算出错，要禁用一层请写 `[]` ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/boot/app-boot/README.md#L52-L65))。

## 配置是四类层而非五层，patch 只替换写出的 config 键

官方文档写的是 **4 类层**，从一个空根开始依次应用：`dsh.profile.bundles` 里每个 bundle 的 patch（base 最先，之后按添加顺序），然后是 profile 自己的 `cordis.patch.yml`，然后是 `$DSH_HOME/cordis.patch.yml`（本机所有 profile 共享），最后是每个 `--patch`（按命令行参数顺序）([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/publish.md#L120-L134))。源码 `readProfilePatches` 的拼接顺序完全一致，此外在设置了遥测禁用环境变量时，**最后还会追加一个由代码生成的遥测 patch** ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/boot/app-boot/src/profile-context.ts#L75-L86))。仓库里已有的 `deepseek/dsh_plugin_guide.md` 讲的“五层”，是把 `dsh-base` 和其他 bundle 拆成两层来数，这只是通俗说法。实际层数并不固定：每个 bundle 算一层，`--patch` 可以出现多次，还可能多出遥测层。命令行参数**不属于** patch 层，它通过 `cmdlineArgs` 服务和行内 `!!js ctx.xxx ?? 默认值` 表达式起作用。所以用户 patch 如果用字面量整块替换 `config`，这类运行时读取就会丢失 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/apps/cli/reference/README.md#L32))。

[运行] 分层实验的设置如下：bundle patch 写 `greeting: from-bundle`，profile patch 写 `from-profile`，home patch 写 `from-home`。`--dump-config` 输出的行头是 `# == dsh-hello-plugin, patched by …/profiles/demo/cordis.patch.yml, …/home/cordis.patch.yml`，值为 `from-home`，**home 层胜过 profile 层**。再加一个 `--patch off.yml`，内容只有 `- id: hello` 和 `disabled: true`，结果这一行变成 `config: {greeting: from-home}` 加 `disabled: true`。也就是说，“整块替换 config”只针对 patch 里**写出了 `config` 键**的情况，只写 `disabled` 的覆盖不会清掉已有的配置。`--dump-default-config` 只显示 bundle 层（`from-bundle`）。如果 `--patch` 指向不存在的 id，stderr 会报 `patch: entry "nonexist" not found`，但 dump 照常输出。把 home 层改回 `[]` 再真实启动，打印 `[hello-plugin] loaded: from-profile`，和 dump 的结果一致。由此可以给 bundle 作者两条建议，文档也是这么写的：默认值放在 schema 里，不要放在 patch 里，因为用户可以覆盖你的行；覆盖更早层的某一行时，要把所有键重新写一遍，`web-app` 覆盖 `base` 时就是这么做的 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/bundle/web-app/cordis.patch.yml#L1-L36))。

禁用插件有两种方式。一种是在 profile 或 home 的 patch 里写 `- id: <行id>` 和 `disabled: true`，作用在行级，可以撤销，也不卸载包。另一种是 `dsh plugin remove`，把依赖和层一起删掉 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/apps/cli/reference/README.md#L64))。热重载方面，`web` 模板启用了 `dsh-hmr`，会监视 manifest、两个用户 patch 和所有 `--patch` 文件；其他模板只在启动时应用配置。

## 从 git 安装要授权构建脚本，而文档的示例键写错了

从 git 安装时拿到的是源码，没有人去跑 `build`，所以 TypeScript 包缺少 `lib/` 就会加载失败。作者需要提供一个能独立运行的 `prepare` 脚本，pnpm 会在 git 安装后执行它 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/publish.md#L161-L171))。但 pnpm 10 及以上版本默认拦截 git 依赖的 `prepare`，所以用户第一次 `add` 一定会失败，必须先把**pnpm 打印出来的确切键**加进 profile 的 `pnpm-workspace.yaml`。文档把这件事明确定性为安全决定：这等于允许该包在安装时、在 agent 的任何沙箱之外执行代码。因此只应该放行可信来源，并用 `github:you/hello-plugin#<sha>` 锁定 commit，防止作者之后的 push 悄悄改变执行的内容 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/publish.md#L172-L186))。版本检查也“不是针对恶意代码的沙箱” ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/boot/app-boot/README.md#L54))。

[运行] 实验用的是本地 git 仓库 `dsh-git-plugin`，其中 `prepare: node build.mjs` 会把 `src/` 复制到 `lib/`，`lib` 写进 `.gitignore`，安装 spec 为 `git+file://…#b0b1e5f…`。

| pnpm 版本 | 首次失败时 pnpm 的提示 | `allowBuilds: { dsh-git-plugin: true }`（文档示例） | 只写包名 | 写 pnpm 打印的完整键 |
|---|---|---|---|---|
| 10.34.9 | `not in the "onlyBuiltDependencies" allowlist`，建议 `onlyBuiltDependencies: - "dsh-git-plugin@git+file:///…#b0b1e5f…"` | 失败 | `onlyBuiltDependencies: [dsh-git-plugin]` 失败 | `onlyBuiltDependencies` 列表写完整键：**成功**，`lib/index.js` 由 prepare 生成 |
| 11.28.5 | `not in the "allowBuilds" allowlist`，建议 `allowBuilds: dsh-git-plugin@git+file:///…#b0b1e5f…: true` | 失败 | 同左，失败 | `allowBuilds` 映射写完整键：**成功**（509 ms），启动后打印 `[git-plugin] loaded` |

这里暴露出三个问题。第一，文档示例 `allowBuilds:\n  dsh-hello-plugin: true` 只写了包名，在两个 pnpm 版本上都不生效。文档正文其实说了“复制 pnpm 打印的确切键”，但示例本身会误导读者 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/publish.md#L174-L177))。第二，在 pnpm 10 下，dsh 自己追加的提示仍然叫用户改 `allowBuilds`，和 pnpm 的提示互相矛盾 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/apps/cli/src/plugin.ts#L104-L106))；GUI Plugin Manager 的“批准”按钮也只识别 pnpm 11 的 `allowBuilds:` 格式 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/boot/plugin-manager/src/build-approval.ts#L8-L26))。第三点不是问题，而是一个正面结论：既然键里带着 git URL 和 commit sha，**授权就绑定在具体的源码版本上**，换一个 commit 就得重新授权。“锁定 commit”因此不只是一条建议，pnpm 的允许列表本身就是这样实现的。不想让用户承担这一步，可以发布到 npm，或者分发 `pnpm pack` 打出的 tarball，这两种方式都“不需要授权”。不过 tarball 这条路没有实测。

## 发布到 npm：用 engines.dsh 声明兼容，peer 只用于共享实例

兼容性优先用 `engines.dsh` 的 SemVer 范围来声明，例如 `">=0.2.0 <0.3.0"`。规则是：声明了 `engines.dsh` 时只看它，**完全忽略 peer**；没有声明时，`@deepseek-ai/dsh` 和 `@deepseek-ai/dsh-*` 的每个 peer 范围都必须满足；两者都没有就不设限。prerelease 版本也参与匹配。`workspace:` 这类非法范围视为不兼容 ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/boot/app-boot/README.md#L54))。[运行] 写了 `>=0.2.0 <0.3.0` 的 bundle 在 dsh `0.2.2-rc.5` 上被接受，这与 npm 默认排除 prerelease 的语义不同。[运行] `npm view @deepseek-ai/dsh-subagent-codex@0.2.2-rc.5` 显示，官方包发布时 peer 依赖里的 `dsh-*` 都是**精确的 `0.2.2-rc.5`**（源码里写的是 `workspace:*`），而且这个包没有 `engines.dsh`，所以只能在恰好是这个 rc 的 dsh 上通过检查。第三方作者如果照抄这种写法，插件就会被锁死在某一个 rc 版本上。正确做法是写 `engines.dsh`，peer 用宽范围，只用来让运行时共享宿主的实例。

[运行] 这里还有一个实测踩到的坑。示例插件 `import Schema from '@deepseek-ai/schemastery'`，但 `package.json` 里没有声明它。`add` 和 `--dump-config` 都正常，真正启动时却报 `hello (dsh-hello-plugin): failed to import: Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@deepseek-ai/schemastery'` 和 `warning: 1 entry did not activate`，其他插件照常启动。加上 `"peerDependencies": { "@deepseek-ai/schemastery": "*" }` 后，不用重装，直接重启就打印出 `loaded: from-profile`。文档的规则是：需要与宿主共享实例的 dsh 包，要同时写进 `peerDependencies` 和 `devDependencies`（后者用于类型检查和独立测试）；有独立版本的第三方库放进 `dependencies` ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/publish.md#L105))。但入门示例的 `package.json` 里一个 peer 都没写。`files` 里必须列出 `cordis.patch.yml`（多文件 patch 要逐个列出）和构建好的 `lib/`，官方包还会加上类型、`src` 和图标 ([源码](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/subagent/subagent-codex/package.json))。要让别人发现你的插件，可以给仓库加 GitHub topic `dsh-plugin` ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/README.md#L46))，或者发布一份插件索引 JSON ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/guide/plugin-market.md#L302-L436))。

## 综合练习：一个带配置、工具、闸门和审计日志的 bundle

下面这个插件把前面各节的已验证要素拼在一起：用 `Config` 控制问候语和黑名单，注册 `greet` 工具，在 pre-execute 里按黑名单拒绝，用 guard 拦截空名字，用 `tools/result` 打审计日志，用 `ctx.effect` 托管一个资源。各部分单独都在裸宿主里跑通过（见前文的轨迹）。bundle 外壳的写法和 `hello-plugin` 实测通过的结构相同。**这个组合文件本身没有作为整体在 dsh CLI 上跑过**，对 `@deepseek-ai/dsh-tools` 的 peer 解析也只是比照 schemastery 推断出来的。

```js
// dsh-greet-guard/index.js
import Schema from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'greet-guard'
export const inject = ['tools']
export const Config = Schema.object({
  greeting: Schema.string().default('Hello'),
  blocked: Schema.array(Schema.string()).default([]),
})

export function apply(ctx, config) {
  const log = ctx.logger('greet-guard')            // 需要有 exporter 才会输出
  ctx.tools.register(defineTool({
    name: 'greet',
    description: 'Greet someone by name.',
    parameters: { name: { type: 'string', required: true } },
    output: { schema: { type: 'string' }, render: (_a, v) => [{ type: 'text', text: v }] },
    async execute(args) { return `${config.greeting}, ${args.name}!` },
  }))
  ctx.on('tools/pre-execute', async (exec, next) => {
    const who = typeof exec.arguments === 'object' ? exec.arguments?.name : undefined  // 参数尚未校验
    if (exec.name === 'greet' && config.blocked.includes(who))
      return { kind: 'deny', reason: `${who} is blocked by policy.` }
    return next()                                   // waterfall：放行必须调 next()
  })
  ctx.tools.guard(exec => exec.name === 'greet' && exec.arguments?.name === '' ? 'empty name' : undefined)
  ctx.on('tools/result', (exec, result) => {
    log.info(`${exec.name} isError=${result.isError}`)  // 只读，不被 await
  })
  ctx.effect(() => { const t = setInterval(() => {}, 60_000); return () => clearInterval(t) })
}
```

```json
{ "name": "dsh-greet-guard", "version": "0.1.0", "type": "module", "main": "index.js",
  "files": ["index.js", "cordis.patch.yml"],
  "engines": { "dsh": ">=0.2.0 <0.3.0" },
  "peerDependencies": { "@deepseek-ai/schemastery": "*", "@deepseek-ai/dsh-tools": "*" },
  "dsh": { "bundle": { "patch": "./cordis.patch.yml" } } }
```

`cordis.patch.yml` 的内容是 `- insert: [{ id: greet-guard, name: dsh-greet-guard, config: { blocked: [Mallory] } }]`。安装和验证的流程是：执行 `dsh plugin --profile demo add ./dsh-greet-guard`；用 `dsh --profile demo --dump-config` 确认这一行和它的来源；在 profile patch 里写 `- id: greet-guard` 加 `config: { greeting: Hi, blocked: [] }` 来覆盖配置（注意这会替换整块 `config`）；然后启动。按前文的实测，调用 `greet(Mallory)` 会走 pre-execute → post-execute → result 这条路径，返回 `Error: Mallory is blocked by policy.`。如果要从 GitHub 分发，作者需要增加 `prepare` 脚本，用户需要按 pnpm 版本把完整键写进 `onlyBuiltDependencies` 或 `allowBuilds`。

## 文档与实际行为的出入，以及没有验证的部分

| 出入点 | 文档说法 | 实际（证据） |
|---|---|---|
| 依赖丢失后的状态 | “ACTIVE → DISPOSED，服务回来后再加载” ([文档](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/framework/index.md#L38)) | 回到 **PENDING(0)** 再变回 ACTIVE；源码里 DISPOSED 的含义是“不可重启” [运行][源码] |
| 插件路径 | `basic/index.md` 要求绝对路径 | `config.md` 的同一份 YAML 用了 `./src/my-plugin.ts` [文档自相矛盾] |
| git 构建授权 | 示例 `allowBuilds: { dsh-hello-plugin: true }` | 只写包名在 pnpm 10 和 11 上都无效，必须写完整键 [运行] |
| pnpm 10 的键名 | 文档与 dsh 提示：“pnpm ≥10 … allowBuilds” | pnpm 10.34.9 要求 `onlyBuiltDependencies`，`allowBuilds` 无效 [运行] |
| 配置层数 | 本仓库已有文章说“五层” | 官方是 4 类层，层数可变，源码末尾还可能追加遥测 patch [文档][源码] |
| “整块替换 config” | 容易被理解成整行替换 | 只替换写出的 `config` 键，只写 `disabled` 不会清掉 config [运行] |
| 入门 bundle 示例 | 示例 `package.json` 没有 peer | import 了宿主包就必须声明 peer，否则启动时 `ERR_MODULE_NOT_FOUND` [运行] |
| Node 版本 | 源码根目录要求 `^24.22.3 \|\| >=26.13.1` | 已发布的 0.2.2-rc.5 要求 `>=24.9.0`，在 24.22.2 上能装能跑 [运行] |

以下内容**没有验证**。`pnpm dsh web --patch` 这条官方源码教程路径没有端到端跑通（磁盘不足）。tool 教程从 `scratch-plugin/` 按值 import `dsh-tools` 在 pnpm 严格链接下能否解析，没有验证。综合练习作为整体在 dsh CLI 上没有运行，`dsh-tools` 作为 peer 的解析是推断。真实的 `github:owner/repo#sha` 简写下 pnpm 打印的键长什么样没有验证，实验用的是 `git+file://`。`dsh plugin update`、`pnpm pack` 打出的 tarball 安装和真正的 `npm publish` 都没有执行。多个 bundle 覆盖同一行时的先后顺序没有测试。`web` profile 的 logger exporter 和日志级别没有核对。zod 等其他 Standard Schema 库能否用作 `Config` 没有试。pnpm 10 系列从哪个小版本开始认 `allowBuilds` 没有确认。全局安装 dsh 时 npm 提示有 5 个包的安装脚本没有授权（`node-pty`、`koffi` 等），这对 PTY 类功能有什么影响也没有验证。

## 结论

dsh 的插件模型确实“薄”：插件作者面对的只有 Cordis 的 `inject`、`Config`、`apply`、`ctx.on`、`ctx.effect`，再加上 `dsh-tools` 的五个扩展点；功能能否被替换，取决于它是否落在某个扩展点上。真正的门槛反而在插件代码之外。一是依赖解析：profile 采用 hoisted 布局，靠 peer 依赖与宿主共享实例，漏写 peer 的插件能装上、能 dump 配置，却在启动时静默失效。二是 pnpm 的构建授权：文档示例写错了，pnpm 10 和 11 的键名也不一样。三是版本声明：照抄官方的精确 peer 会把插件锁死在某个 rc 上。所以给新手的最短路径是：先用裸宿主验证工具和钩子，然后写 `engines.dsh` 和宽范围 peer，用本地目录 `add` 安装，用 `--dump-config` 核对分层，最后发布到 npm，避开 git 构建授权这一步。

从安全角度看，dsh 把“安装时执行代码”交给 pnpm 的允许列表来管，把运行时的工具权限交给 pre-execute、guard 和 `approval` 服务，两者是分开的。前者的授权键里带着 commit sha，所以锁定 commit 由 pnpm 机制直接落实，不只是文档建议。后者则意味着，没有挂载 `approval` 服务的宿主会把每一个 `ask` 都当成拒绝。写闸门插件的人应该把这两条当作设计前提，而不是事后发现的边界情况。

---

> **2026-11-23 更正（写文章时复测）**：上文说 pnpm 10 不认 `allowBuilds`，这一点不准确。复测结果：pnpm 10.34.9 写**完整键**（包名@git URL#sha）时，`allowBuilds` 和 `onlyBuiltDependencies` 都能放行构建；只写包名在 pnpm 10、11 上都无效。pnpm 10 的报错只提示 `onlyBuiltDependencies`，所以 dsh 提示写 `allowBuilds` 在 pnpm 10 上也能用，只是和 pnpm 自己的提示不一致。以 `deepseek/dsh_plugin_guide.md` 为准。
