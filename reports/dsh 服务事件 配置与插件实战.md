# 拆开 dsh 的服务、事件、配置与插件接线

dsh（commit `4b399c3`，vendored Cordis 4.2.0-alpha.1）里，插件之间的所有协作都落在三样东西上：一张以"隔离 symbol"为键的服务表、一条不带异常隔离的事件总线、一棵按 `id` 协调的 Loader 条目树。**服务**是根 `ReflectService.store` 里的一条 `Impl` 记录，`inject` 是持续订阅，服务方法里的 `this.ctx` 被 Cordis 改写成调用方的 context，所以 `ctx.tools.register()` 这类注册会自动挂到调用方 Fiber 上、随调用方卸载撤销。**事件**有且只有五种分发模式，其中只有 `parallel` 会让所有监听器跑完。`emit` 遇到一个同步 throw 就中断后面的监听器并把异常抛给发出方，所以 dsh 自己的通知类事件都改用 `ctx.events.dispatch('emit', …)` 逐个 try/catch。**配置**在激活时才校验，按 id 覆盖的 patch 会整块替换 `config`，volatile 字段要同时满足五个条件才能免重启生效。HMR 默认只热重载配置，模块热替换需要显式开启。**接进 harness** 时，教程 07 的组合在 `4b399c3` 上跑不通：原样运行时四个插件全停在 PENDING、exit 0，补齐两行之后，用 Loader 加载仍然会因为 executor 晚激活而崩溃。本文逐一给出源码位置和实验输出，最后用一个在真实 harness 包上跑通的 `acme-ledger` 插件把这些机制串起来，并附一张可直接对照的坑点清单。Fiber 状态机、`ctx.effect`、逆序卸载和"提供方先释放资源"的竞态已在 `deepseek/dsh_lifecycle_and_effects.md` 中讲过，本文只在需要时引用其结论。

> 依据说明：所有 `path:line` 都相对 dsh 仓库根目录（commit `4b399c3`）。实验运行在与仓库同版本的 npm 发布包上（`@deepseek-ai/cordis@4.2.0-alpha.1`、`cordis-plugin-loader/include@1.2.0-alpha.1`、`dsh-*@0.2.3-alpha.2`，Node v24.22.2）。其中 `dsh-tools` 的 `src/*.ts` 与 `packages/core/tools/src/` 用 `cmp` 核对过，逐字节一致。FiberState 数值为 `PENDING=0, LOADING=1, ACTIVE=2, FAILED=3, DISPOSED=4, UNLOADING=5`（`vendor/cordis/src/fiber.ts:177-184`）。

---

## 服务是一条 Impl 记录，调用方追踪让注册自动归属（03 Service）

### 提供：`super(ctx, name)` 只是 `reflect.provide` 的包装

`Service` 构造函数做的事很少（`vendor/cordis/src/service.ts:45-62`）：给实例装上 tracker `{ associate: name, property: 'ctx' }`。如果原型上有 `[Service.invoke]`，就换成一个可调用的函数对象（`ctx.logger('x')` 就是这样实现的）。最后调用 `ctx.reflect.provide(name, self, this[Service.check])` 并返回 `self`。**`Service` 基类不是必需的**：Loader 自己手动装 tracker 再调 `reflect.provide`（`vendor/loader/src/index.ts:96-102`），ui-conversation 的 `ConversationDefinitionRegistry` 只用一行 `Object.defineProperty(this, Service.tracker, { value: { property: 'ctx' } })` 就获得了调用方追踪（`packages/client/ui-conversation/src/client/conversation/definition-registry.ts:10-15`）。

`provide()` 本身是一个 `ctx.fiber.effect`（`vendor/cordis/src/reflect.ts:313-349`）。它先按当前 ctx 的隔离表取 symbol 作为键，**先**以 `uid: ++this._implCounter` 创建 `Impl`，**再**检查 `store[key]` 是否已被占用，占用就抛 `service "x" has been registered at <fiber>`。写入成功后，记录同时进入提供方 Fiber 的 `provisions`。提供方 Fiber 不是 ACTIVE 时不 notify，等它进入 ACTIVE 时由 `_updateState` 统一通知（`vendor/cordis/src/fiber.ts:612-623`）。因此 Service 子类虽然在构造函数里就注册了，但 `[Service.init]` 完成前消费方不会启动。撤销函数分三步：从 store 删除并通知同隔离域，`await` 直到 `impl.dependents` 清空，最后才从 `provisions` 中移走（`reflect.ts:333-347`）。所以消费方在自己的 teardown 里仍能读到服务。

实验 1（替换与重复注册）的输出说明了几个容易误解的点：

```text
consumer apply: [A] hello world | impl uid = 1 | epoch = 0:1
B state: 3 B error: service "greeter" has been registered at <Greeter>
consumer effect disposed
consumer apply: [C] hello world | impl uid = 3 | epoch = 0:3
ctx.greeter threw: cannot get property "greeter" without inject
```

消费方的 epoch 由 `revision:uid…` 拼成（`fiber.ts:639-649`），换一个提供方，uid 就变，消费方先卸载、再在新实现上重跑。重复提供的 B 直接 FAILED，**A 卸载后 B 也不会自动接班**：失败的 Fiber 只能靠 `update()` 恢复（`fiber.ts:655-656`）。失败的注册还会白白消耗一个 uid（B 用掉了 2）。教程说"卸载 `dsh-bash-local`、挂载另一个 `shell` 提供方，所有注入者都会重启"（`docs/cordis-tutorial/03-services.zh.md:78`），这句话成立的前提是**先卸旧、再装新**。

### 注入：`inject` 是持续订阅，`check` 是拉模式

就绪判定在消费方的 `_checkImpl`（`fiber.ts:625-646`）：只取提供方处于 ACTIVE 的 impl，如果有 `check` 就以消费方 ctx 调用它，`check` 抛错按不可用处理并记日志。`_refresh` 用所有 inject 项的 uid 拼出 epoch，缺任何一项就是 INACTIVE。提供方的增删和状态切换都会触发 `reflect.notify(names)`，后者遍历**所有 runtime 的所有 Fiber**，重新检查（`reflect.ts:357-376`）。消费方开始加载时，先把 `_store` 快照成 `committed`，再把自己加入每个 impl 的 `dependents`（`fiber.ts:703-708`）。提供方卸载时，先 `while (dependents 非空) await`，然后才执行自己的 effects（`fiber.ts:734-760`）。

实验 2 把这个时序打了出来：

```text
   4 Db init start
  59 Db init done
  84 provider state 2 | consumer state 0 | ctx.get(db) = object
  95 after ready=true WITHOUT notify: consumer state 0
 106 after reflect.notify: consumer state 2
 106 consumer teardown start, still can use db: rows
 142 consumer teardown end
 142 provider own effect disposed
```

结论有两条。第一，`[Service.init]` 会被等待（`fiber.ts:274-288`），这是普通插件作者真正需要的扩展点：packages 中有 41 处，例如 `SystemPrompt` 在 init 里 `await this.ctx.sessionProjections.register(...)`（`packages/core/system-prompt/src/index.ts:534-536`），`WorkspaceRegistry` 在 init 里打开存储域，并用 `this.ctx.effect` 挂上关闭函数（`packages/workspace/workspace/src/index.ts:224-230`）。第二，**`[Service.check]` 只在 notify 时重新求值**：状态变了而不手动 `ctx.reflect.notify([name])`，消费方会永远停在 PENDING。packages 下没有一个包自己实现 check，唯一的真实用户是 Loader（`vendor/loader/src/index.ts:204-208`，配合 `entry.ts:251-261` 中手动 notify）。`[Service.invoke]`、`[Service.extend]`、`mergeIntercept/resolveIntercept` 在 packages 中同样是零使用。实验 5 演示了 `inject: { gate: { await: true } }` 和 `ctx.intercept('gate', {...})` 怎样让同一个服务对不同消费方给出不同的就绪结论。这些都是写框架级服务用的工具。

### 归属：为什么 `ctx.tools.register()` 知道是谁调的

通过 ctx 代理读到的服务不是原始实例，而是 `getTraceable(ctx, value)` 包出来的 Proxy（`vendor/cordis/src/utils.ts:120-129`）。在这个 Proxy 上读 `tracker.property`（即 `'ctx'`）时，得到的是**使用方**的 context；方法调用时，`this` 被换成一个 `ctx` 已被覆盖的 shadow（`utils.ts:153-206`）。源码注释把分工写得很清楚："def site … governs service resolution; use site … governs intercept, isolate and effects"（`utils.ts:170-172`）。`ToolsService.register()` 的最后一行是 `this.layers.effect(this.ctx, …)`（`packages/core/tools/src/index.ts:145-152`），`ScopedLayers.effect` 按 `scopeOf(ctx)` 选层，再调用 `ctx.effect(function* …)`（`packages/core/scope/src/store.ts:219-262`）。所以这条注册既**归属调用方 Fiber**，又**落在调用方的 agent 作用域层**。`tools.restrict()` 甚至要求调用方必须带 scope（`tools/src/index.ts:162-166`）。工具重名的报错文案会提示"register through that agent's `agent.ctx`"（`tools/src/index.ts:67-70`），这个提示正是建立在调用方追踪之上的。

实验 3 中，构造函数里的 `this.ctx.fiber.name = Commands`（提供方），而两次 `register()` 里分别是 `plugin-one`、`plugin-two`；`p1.dispose()` 之后输出 `auto-unregister "hello"`、`list = [ 'bye' ]`，registry 服务本身没有写任何"按插件清理"的代码。实验 4 给出了两个反模式：在构造函数里存下 ctx、方法里用它注册，调用方卸载后条目还在（`after caller disposed, bad.entries = [ 'x' ]`）；把方法解构出来调用，`this` 丢失（`TypeError Cannot read properties of undefined`）。原因是 `createShadowMethod` 只在 `thisArg === receiver` 时才替换 `this`（`utils.ts:161-168`）。

### 替换与隔离：一个作用域一个实现，`isolate` 开第二个槽

store 以隔离 symbol 为键。`ctx.isolate(name, label?)` 只是在子 ctx 的隔离表里给 `name` 换一个新 symbol（`vendor/cordis/src/context.ts:126-130`），于是这棵子树的 provide、读取和 notify 都落到另一个槽里。实验 4 中，root、A、B 和共享 label 四个槽共存，store 有 4 个键，其中三个都叫 `Symbol(shell)`，但互不相等。**新隔离的子树不会回退到父作用域的实现**：没有提供方时，消费方静默 PENDING，不报错（`reflect.ts:157-160`）。在 cordis.yml 里，`isolate: { shell: true }` 表示条目私有（symbol 后缀 `#<id>`），`isolate: { shell: 'label' }` 表示同 label 共享（后缀 `@<label>`）（`vendor/loader/src/config/isolate.ts:77-101`）。仓库里的真实用法有三处：terminal 面板组（`snapshots/session/terminal-panel-tools/cordis.yml:3-17`）；SQLite 会话持久化把旧版 JSONL 实现私藏进 `serviceCtx.isolate('sessionPersistence')`（`packages/session/session-persistence-sqlite/src/index.ts:42-56`）；CUA 为自己私有一个 `ptcRuntime`（`packages/experimental/cua/src/tool-registration.ts:149-156`）。Service 默认的事件 filter 也按隔离 symbol 比较（`service.ts:64-66`），所以隔离同时隔开了该服务发出的事件。

### 可选依赖与命名

在有 runtime 的插件里，**不声明 inject 就读 `ctx.x` 会抛错**，即使服务已经存在（`reflect.ts:185-202`）。可选依赖有两种写法：`ctx.get(name)` 做即时探测，默认只返回 ACTIVE 提供方的值，不订阅变化，也不建立 `dependents`，长期持有返回值是危险的；`ctx.inject(deps, cb)` 等价于 `ctx.plugin({ inject, apply: cb })`（`vendor/cordis/src/registry.ts:300-302`），只让这一部分随依赖加载和卸载。dsh 的标准组合是"`ctx.get` 定初值 + `ctx.inject` 订阅变化"，范例是 `tool-bash`：`jobs` 不在时先注册前台版 bash，`jobs` 出现时换成后台版，`jobs` 消失时在 `jobCtx.effect` 的清理里换回前台版（`packages/shell/tool-bash/src/index.ts:588-602`）。lifecycle 一文的"可选依赖"一节讲的是同一机制。

命名方面，每个隔离域里一个名字只能有一个实现，而且服务名和 mixin 名共用 `reflect.props`。`on/emit/plugin/inject/get/provide/effect` 等名字被占用，`provide('on')` 会抛 `property "on" is already declared as accessor`（`reflect.ts:255-258`、`:315-319`）。以 `_` 开头、纯数字、`prototype`/`then` 的名字不走服务解析（`reflect.ts:80-91`）。实验 6 中，`_private` 能 provide，也能用 `ctx.get` 取到，但 `ctx._private` 是 `undefined`。点号名 `remote.files` 可以以 `ctx.remote.files` 访问。教程要求给自有服务加辨识度高的前缀，因为 harness 已经占用了 `tools`、`llm`、`shell`、`systemPrompt` 等常用名字（`docs/cordis-tutorial/03-services.zh.md:92-94`）。类型靠 `declare module '@deepseek-ai/cordis' { interface Context { … } }` 声明合并，纯编译期，不生成代码。

dsh 里的真实服务大致分三类：**注册表型**（`ToolsService`、`SystemPrompt`：方法一律 `this.layers.effect(this.ctx, …)`，变更时 emit `tools/change` 等事件）、**异步初始化型**（`SystemPrompt`、`WorkspaceRegistry`：在 `[Service.init]` 里 await 外部资源）、**能力接缝型**（`ShellExecutor` 抽象基类固定 `super(ctx, 'shell')`，`dsh-bash-local`、`bash-sandbox`、`pwsh-*` 任选一个挂载，`packages/shell/shell/src/index.ts:36-40, 64-67`）。依赖会连锁：`ToolsService.inject = ['systemPrompt']`，所以 systemPrompt 重建时，tools 和所有工具插件都会依次重跑。这就是"注册写在 `apply` 里，不写在模块顶层"的根本原因。

---

## 五种分发模式里只有 parallel 让所有监听器跑完（04 事件）

### 分发模式与错误行为

4.2.0-alpha.1 一共有五种分发模式：`emit | parallel | serial | bail | waterfall`（`vendor/cordis/src/events.ts:31`）。旧文 `deepseek/dsh_plugin_guide.md:271` 写成"四种"，漏掉了 `parallel`，应当更正。教程和 primer 的表格只讲了语义，没讲出错时怎么办。实验 04 在每种模式下都让中间一个监听器 throw 或 reject，结果如下：

| 模式 | 同步/异步 | 返回值 | 中间监听器同步 throw | 中间监听器 async reject |
|---|---|---|---|---|
| `emit` | 同步循环，不 await | 永远 `undefined` | **抛给发出方，后续不运行**（`ran=[L1,L2-throws]`） | 被丢弃，成为进程级 `unhandledRejection` |
| `parallel` | 全部并发，`Promise.allSettled` | `undefined` | 全部运行，最后抛 `AggregateError`（`ran=[L1,L2-throws,L3]`） | 同左 |
| `serial` | 依次 await | 第一个 bail 值 | reject，后续不运行 | 同左 |
| `bail` | 同步依次调用 | 第一个 bail 值 | 抛出，后续不运行 | 不处理 |
| `waterfall` | 由监听器的 `next()` 决定 | 最外层的返回值 | 沿调用链抛出 | 沿 promise 链 reject |

另外两个细节容易出错。`isBailed` 只把 `null/false/undefined` 当作"继续"，**`0`、`''` 都会短路**（`events.ts:12-14`，实验 02 中 `bail -> 0`、`serial-> 0`）。`bail` 不 await，所以第一个 async 监听器返回的 Promise 本身就是 bail 值（实验 02：`bail -> Promise { 'A' }`）。harness 自有事件的模式分布是：emit 66、waterfall 22、parallel 9、serial 2（`agent/created`、`agent/turn-stopping`）、bail 1（`webserver/authorize`），统计来源是生成的矩阵 `docs/event-producer-consumer.zh.md:12-115`。观察通知用 `emit`；允许协作者改写或否决的决策用 `waterfall`（`tools/*-execute`、`agent/pre-step`、`approval/request`、`system-prompt/assemble`）；需要等全部完成的扇出用 `parallel`（`session/flush`、`workspace/session-stop`）。

### 错误隔离：dsh 不用裸 `emit` 发通知

正因为 `emit` 不隔离，仓库规范要求"在分发器中隔离回调异常"（`docs/defensive-patterns/general.zh.md:257-269`）。vendored Cordis 为此保留了 `events.dispatch(type, args)`，它只返回筛选并绑定好的回调，注释写明"DSH retains callback enumeration for per-listener error containment"（`events.ts:175-185`）。dsh 的通知类发出方都是同一个写法：`tools/result`（`packages/core/tools/src/pipeline.ts:419-439`）、agent 通知（`packages/core/agent/src/dispatch.ts:120-137`）、session 观察者（`packages/core/session/src/index.ts:145-170`）、`web/providers-changed`（`packages/web/web/src/index.ts:181-191`），以及 Cordis 自己的 `internal/plugin` 卸载通知（`fiber.ts:149-167`）。

```ts
for (const callback of ctx.events.dispatch('emit', [carrier, name, ...payload])) {
  try { void Promise.resolve(callback(...payload)).catch(report) } catch (e) { report(e) }
}
```

有两个注意点。`dispatch` 会用 `args.shift()` **原地**拿掉数组开头的 thisArg 和事件名，dsh 正是利用这一点，用同一个数组调用回调（实验 04 末尾 `args after dispatch = []`）。`agent/dispatch.ts:121` 的注释说"Cordis emit invokes callbacks through Array.map"，与当前源码不符，当前实现是 `for…of`（`events.ts:209`），但"一个同步 throw 饿死后续监听器"的结论仍然成立。非观察类事件采用"捕获后关闭失败"：`approval/request` 先用 `Promise.resolve().then(...)` 进入 promise 链，把同步 throw 也变成 rejection，再归一成 `'unavailable'`（`packages/interaction/user-approval/src/index.ts:303-318`）；工具 waterfall 的异常由 pipeline 的 try/catch 转成工具错误结果。

### waterfall 与 `next()`

`waterfall` 把最后一个参数当作最内层默认行为，监听器依次收到 `(...args, next)`（`events.ts:250-265`）。实验 03 的输出把洋葱结构展示得很清楚：

```text
  P (prepend) before / L1 before / L2 before / INNER runs / L2 after / L1 after, got inner:hi!
normal -> INNER:HI!
veto   -> VETOED-BY-L2
next() twice -> next() called multiple times
listener calls next() but forgets to return -> undefined
```

规则有四条。不调 `next()` 就是否决，内层默认行为不运行。同一个 `next` 调两次会同步抛错，所以重试要写在 `tools/execute` 包装器里处理 `next` 的结果，而不是多次调用 `next`。**调了 `next()` 却忘了 `return`，调用方拿到的是 `undefined`**。只做观察或标注的监听器必须写成 `const r = await next(); …; return r`（教程 `04-events.zh.md:136-138`，范例见 `packages/guard/repeat-tool-reminder/src/index.ts:218-231`）。Cordis 自己也用 waterfall：`internal/update` 以 `restart` 作为 inner，非 global 的监听器被 `internal/listener` 截走，存在该 Fiber 自己的 hooks 上（`events.ts:133-160`）。实验 06 中，监听器对"只改 level"不调 `next()`，实现了就地更新（`applies = 1`），改 port 时调用 `next()` 才重启（`apply #2`）。

### 顺序、thisArg 与作用域过滤

顺序只由注册时间和 `prepend` 决定：`prepend` 用 `unshift`，后注册的 prepend 排在最前（实验 01：`Q, P, A, B, O`），没有优先级参数。别的插件随时可以 prepend 到你前面，所以 user-approval 把 `never` 策略写在服务代码路径里，而不是写成监听器（`user-approval/src/index.ts:297-302`）。`ctx.on` 返回的 disposer 在文档里写的是"仍注册则返回 `true`"（`docs/cordis-api/events.zh.md:147`），实测两次调用都返回 `undefined`，因为它返回的是 `fiber.effect` 的 disposer（`events.ts:269`），**不要依赖这个返回值**。

所有分发方法都先经过 `_resolve`：如果第一个参数是对象或函数，就把它当成 thisArg 拿掉（`events.ts:162-173`）。thisArg 上的 `[Context.filter]` 决定哪些 ctx 的监听器能收到事件，`global: true` 的监听器跳过这层检查。dsh 用 `scopeTarget(base, key)` 构造只带 filter 的 carrier（`packages/core/scope/src/index.ts:158-185`）：**未打 scope 标签的监听器收到所有 agent 的事件，带标签的只收到本作用域及其后代的事件**，事件沿作用域链向上传，不向下传。监听器里的 `this` 是 carrier，没有实际用处，subject 只能从参数里取。

`internal/dispatch` 在每个非 internal 事件分发前同步触发，它抛错会让这次分发失败。`terminal-bash` 就用它否决了持久终端存在时的 `sandbox/mode` 会话事件，并加上 `global: true` 绕过 session carrier 的过滤（`packages/terminal/terminal-bash/src/index.ts:49-59`）。这一招之所以有效，是因为 session 在 `log.push` **之前**就解析好监听器快照（`packages/core/session/src/index.ts:594-606`）。还要注意，`internal/dispatch` 报告的 `mode` 是发出方传给 `dispatch` 的字符串：`session/flush` 声明为 parallel，观察者看到的却是 `emit`。最后，`turn/*`、`tool/result`、`compaction/*` 是持久会话事件类型，**不是 Cordis 事件**，`ctx.on('turn/start')` 永远不会触发，要监听 `session/event` 再检查 `event.type`（`docs/user/develop/framework/events.zh.md:104-106`）。

### dsh 里的事件用法：一次工具调用经过的五个事件

一次工具调用依次经过以下阶段（`pipeline.ts:191-234, 306-313, 507-532, 419-439`）。四个按 agent 过滤的事件都以 `scopeTarget(this.tools, exec.agent)` 作为 thisArg。

| 阶段 | 事件（模式） | inner 默认值 | 监听器能做什么 |
|---|---|---|---|
| 门禁 | `tools/pre-execute`（waterfall） | `{ kind: 'allow' }` | allow / deny / cancel / ask（ask 交给 `ctx.get('approval')`，没有审批服务时降级为 deny） |
| 守卫 | `tools.guard()`（不是事件） | — | 只在 allow 后执行，单调拒绝 |
| 执行 | `tools/execute`（waterfall） | 工具主体 | 超时、重试、计量；只允许改 `exec.signal` |
| 变换 | `tools/post-execute`（waterfall） | `{ kind: 'accept' }` | 替换 content **或** value（不能两者都换），`block`，附加 `additionalContexts` |
| 观察 | `tools/result`（emit，自带隔离） | — | 只读，exec 和 result 已冻结；不会被 await |
| 变更 | `tools/change`（emit，不过滤） | — | 注册表增删通知 |

cookbook 对扩展点的分工写得很明确：可重排的策略写在 `pre-execute`，单调的最终拒绝用 `ctx.tools.guard()`，包裹执行用 `execute`，变换用 `post-execute`，只读观察用 `result`（`docs/cookbook/extension-cookbook.zh.md:35`）。

---

## 配置在激活时才校验，volatile 要五个条件同时成立（05 配置）

### Schema 从哪里来、何时校验、失败会怎样

Cordis 在 `registry.plugin()` 第一次遇到某个插件时，从**插件对象**上读取 `Config`（`vendor/cordis/src/registry.ts:322-330`）。Loader 先用 `unwrapExports` 取 `exports.default ?? exports`（`vendor/loader/src/index.ts:229-237`）。由此产生第一个坑：**`export default function apply` 搭配具名 `export const Config`，Config 会被静默丢掉**。实验 E1 中，这样写的插件收到 `apply config = undefined`，必填字段也没有被校验。正确写法有两种：不写 default，只导出 `Config` 和 `apply`；或者 default 导出一个带 `static Config` 的类或对象。`Config` 必须是 Standard Schema，不能是普通对象，也不能异步校验（`fiber.ts:80-92` 遇到 Promise 会抛 `Async config validation is not supported`）。

校验由 `internal/config` waterfall 的内置监听器完成（`events.ts:156-159`），时机是**激活时**：`_runner.execute` 先 `resolveConfig`，再 `new callback(ctx, config)`（`fiber.ts:272-291`）。因此 PENDING 插件的配置错误要等依赖到齐后才暴露。Loader 在这个 waterfall 里登记了两个监听器（`vendor/loader/src/index.ts:104-131`）：最外层的负责把 `ValidationError` 的位置改写成 cordis.yml 的行列号，最内层的负责先做 `!!js` 插值。实际执行顺序是插值、校验、改写位置。实验 E1 的失败输出：

```text
entry 2928a2d0:bad: state=FAILED
  ValidationError: invalid config:
  - $.targets expected array but got not-an-array (at targets, <lab-config>/scripts/e1/cordis.yml:4:14)
    at bad (<lab-config>/scripts/e1/cordis.yml:1:3)
```

出错条目进入 FAILED，进程不退出，其他条目照常运行。教程里"打印错误后以状态码 1 退出"描述的是教程启动器自己的行为。**FAILED 不会自愈**：`_setEpoch` 遇到 `_error` 直接返回（`fiber.ts:655-656`），只有 `fiber.update()` 能清掉错误，修好依赖并不够（`vendor/README.md` V25）。对 ACTIVE Fiber 调用 `fiber.update(bad)` 时校验是同步的，会当场抛错（实验 E3：`threw synchronously: ValidationError: - $.mode expected string but got 42`）。

### volatile：五个条件全部满足才免重启

`Entry.update()` 只有在以下五个条件**同时**满足时才走 volatile 路径（`vendor/loader/src/config/entry.ts:155-171`）：变化的键只有 `config`；Fiber 处于 ACTIVE；context 原型没变；`equalExceptVolatile(旧, 新, schema)` 为真；`_commitVolatile()` 用 `fiber.resolveConfig()` 完整解析候选值后，普通字段的有效值仍然深相等（`entry.ts:173-211`）。满足时，新值被写进运行中的 volatile 引用，并只向这一个 Fiber 发一次 `loader/volatile-update(paths)`。不满足时，走 partial-dispose、`_patchContext`、`fiber.update(config, true)`，也就是重启。`equalExceptVolatile` 只沿带 `dict` 的 object schema 递归，`!!js` 节点、未知字段一律严格比较原值（`vendor/loader/src/config/diff.ts:17-45`）。

实验 E2 的两步对照很直观。只改 `level`（volatile）时，没有 dispose，也没有 apply，只收到 `volatile-update paths=[["level"]] level=5`。同时改 `level` 和普通字段 `mode` 时，插件先 `dispose` 再 `apply mode=slow level=6`，没有收到 volatile 事件。实验 E3 揭示了一个更隐蔽的坑：给 volatile 字段写入非法值 `"oops"`，只得到一条 `volatile config update failed` 的 warn，Fiber 仍是 ACTIVE，引用里还是旧值 1。**但这次运行时编辑已经经 Include 的 journal 写回磁盘**，下次激活时插件会因此进入 FAILED。此外有三点：`loader/volatile-update` 是不等待的 emit，监听器的异常会被吞掉（`entry.ts:206-209`）；直接调用 `fiber.update()`、HMR 代码替换、依赖替换都不走这条路径（`docs/cordis-tutorial/05-config.zh.md:93`）；volatile 值要每次用 `.get()` 现读，不能缓存。

### `!!js`：只对 `config` 和 `disabled` 求值

`!!js` 是 Include 定义的 YAML 标签，解析成 `{ __jsExpr }`（`vendor/include/src/index.ts:23-37`），由 `new Function('ctx','expr','with (ctx) { return eval(expr) }')` 求值（`vendor/loader/src/config/utils.ts:3-27`）。它只在两处生效：`config` 在激活时用**插件自己的 ctx** 求值，可以使用它 inject 的服务；group 和 include 这类树载体会把子行表达式留到子行激活时再求值（`index.ts:128-129`）。`disabled` 在每次树协调时用 Loader 一侧的 ctx 求值（`entry.ts:92-116`）。实验 E1 中，`!!js process.env.DEMO_GREETING + ' / ' + typeof loader` 求值为 `Hi-from-env / object`，可见 `process` 和裸标识符 `loader` 都能访问。`disabled: !!js` **不是响应式的**：实验 E2 step 3 只改环境变量、不动文件，`include.refresh()` 发现文件内容没变，直接返回（`include/src/index.ts:411`），条目没有挂载；step 4 改了文件之后才挂上。`!!js` 拥有完整的进程权限，所以 patch 文件要当作代码对待，用 `--patch` 加载不可信文件就等于执行不可信代码。base bundle 用 `disabled: !!js "!ctx.get('profileContext')"` 控制 plugin-manager 和 hmr 两行（`packages/bundle/base/cordis.patch.yml:15-31`）。

---

## cordis.yml 按 id 协调，patch 整块替换，HMR 以插件为单位替换（06 组合与 HMR）

### Loader 的条目模型

Loader 本身是一棵只存在于内存的 `EntryTree`。`cordis:include` 条目会开出一棵子树：读取 YAML，应用 patches，再交给 `root.update(list)`（`vendor/include/src/index.ts:664-680`）。`EntryGroup.update()` 按 id 比较新旧列表（`vendor/loader/src/config/group.ts:86-104`）：新列表里的 id 一律 `create()`，由 `Entry.update` 决定是不做事、提交 volatile 还是重启；只在旧列表里的 id 被 `remove()`。同一列表中出现重复 id 时，后者覆盖前者。条目字段有 `id/name/config/group/disabled/inject/intercept/isolate`，不合法的行以 `EntryValidationError` 拒绝（`vendor/loader/src/config/utils.ts:119-162`）。条目上写的 `inject` 会**叠加**到插件自己的 inject 上（`vendor/loader/src/index.ts:149-155`），yml 里一个笔误就能让插件永久 PENDING，且没有任何提示（实验 E3 中写了 `inject: [nobody]` 的 `needs`）。

**教程 06 关于匿名行的说法在 `4b399c3` 上已经过时。** 教程说没有 id 的行"每次读取都会获得新 id，只要配置文件有任何编辑就会被删除重建"（`docs/cordis-tutorial/06-composition-and-hmr.zh.md:59`）。但 Include 的 `_assignIds` 会让"与上次读取的某个匿名行深相等"的行沿用旧 id（`vendor/include/src/index.ts:615-662`）。实验 E2 中，匿名行在编辑相邻行时保持 `d105eab2`，没有重启；只有改了它自己的 `mode`，才换成新 id `b06fcc51` 并 dispose、apply。结论不变：每一行都应该写显式 `id`，否则一改自身配置就会删除重建，失去 volatile 和原地更新的机会，patch 层也无法定位它。

插件自己调用 `ctx.fiber.dispose()` 时，Loader 排除 8 种非自释放情形后，会把 `disabled: true` **写回用户文件**（`vendor/loader/src/index.ts:157-195`，实验 E3 的 `selfdispose.mjs`）。对 bundle 插入的行，这个修改写进的是内存中的父级 patch，重启后丢失。

### 分层：bundle → profile → home → `--patch` → 派生 patch

profile 模式下，dsh 把各层拼成一份扁平的 patch 列表，顺序是：各 bundle（按 `dsh.profile.bundles` 顺序）、profile 目录的 `cordis.patch.yml`、`$DSH_HOME/cordis.patch.yml`、命令行 `--patch`（按 argv 顺序），最后可能追加一条禁用遥测的 patch（`packages/boot/app-boot/src/profile-context.ts:75-86`）。这份列表作为根 Include 的 `config.patches`，根 Include 固定 `id: 'include'`，是为了让启动诊断稳定（`packages/boot/app-boot/src/index.ts:786-801`）。`--dump-config` 与真正挂载走的是同一个 `applyEntryPatches`（`profile.ts:747-762`）。层数并不固定，"五层"只是通俗说法。

合并规则（`vendor/include/src/patch.ts:134-180`）由实验 E5 和 E6 验证：按 id 覆盖时**逐键整值替换**，`x` 的 `config` 从 `{a:1,b:2}` 变成 `{b:3}`，`a` 丢失（仓库多处明文写着"does not deep-merge"，如 `packages/boot/app-boot/README.md:210`）；`name` 与目标不一致的覆盖会被整体跳过，只留一条 warn；目标不存在时也只 warn；group 的子行可以被顶层 patch 按 id 继续覆盖；后面的层用 `insert` 写同一个 id，运行时**整行替换**（E6 只剩一个 `include:x`，加载的是后插入的 `p2.mjs`）。对插件作者来说，结论是**默认值必须写在 schema 的 `.default()` 里**，写在 bundle patch 里的"默认值"会被用户层整块覆盖。

### HMR：`@deepseek-ai/dsh-hmr` 的行为边界

dsh 挂载的是自己的 `@deepseek-ai/dsh-hmr`，不是 vendored 的上游副本。它是一个 `@Inject('loader') @Inject('timer')` 的 Service（`packages/boot/hmr/src/index.ts:211-226`），要求 Node 内部 ESM loader 可用（`--expose-internals` 或 native addon），否则构造时直接抛错（`index.ts:307-314`）。**base bundle 默认 `root: []`**，也就是只热重载 profile 配置，模块热替换需要主动开启（`packages/bundle/base/cordis.patch.yml:27-31`）。acp-app bundle 直接禁用了 hmr。没有挂载 timer 时，HMR 会静默停在 PENDING。

模块根的变更经 `ctx.debounce` 合并（默认 100ms）后分四类处理（`index.ts:436-489`）：进程入口静态依赖图里的文件（externals）调用 `loader.exit()` 请求整进程重启；ESM 缓存里已有的文件进入部分重载；某个 Include 的配置文件调用 `include.refresh()`；其余文件只 emit `hmr/change`。部分重载（`index.ts:590-763`）沿 ESM link 图把"accepted"向上传播（`node_modules` 不参与），找出入口闭包受影响的 Loader 条目，逐出缓存并保留备份，重新 import，然后 `registry.delete(旧)`，再在**原父 ctx 上、用原始配置**执行 `registry.plugin(新)`。任何一步失败都会回滚缓存并把旧插件挂回去。`registry.delete` 命中自释放判定的 case 4，因此不会被记成 `disabled: true`。实验 E4 的输出覆盖了主要行为：

```text
--- A: edit dep.mjs (dependency of hello only)
  [hello] dispose msg=v1 / [hello] apply msg=v2 config={"n":1}
--- B: three quick saves of dep.mjs 40ms apart (debounce 200ms)
  [hello] dispose msg=v2 / [hello] apply msg=v3c config={"n":1}
--- C: edit unused.txt (not a loaded module)
  hmr/change unused.txt
--- D: edit cordis.yml (hello config n: 1 -> 2)
  [hello] dispose msg=v3c / [hello] apply msg=v3c config={"n":2}
--- E: syntax error in hello.mjs
  <warn hmr> Unexpected end of input
  hello fiber state after failed reload = ACTIVE
--- F: touch run.mjs (process entry point = HMR externals)
  !! loader.exit() called (full reload requested)
```

依赖改动会带动入口插件重载，而 `other` 不受影响；去抖把三次保存合并成一次；语法错误时旧实例保持 ACTIVE，修好后才替换。还有几条边界只能从源码读出来：主 watcher 只监听 `change`，**新建文件不触发任何动作**；模块顶层的副作用（定时器、连接、全局注册）在模块被逐出时不会被清理；被宿主静态图引用的模块、不由 Loader 条目启动的插件会留作 pending，日志提示"changed modules keep running their previous code until a restart"（`index.ts:403-405, 829-872`）。profile 配置文件则由 `watchConfig` 逐个精确监视（监听 add、change、unlink 三种事件），任一文件变化后 `reconcileProfile()` 重组全部层，再交给根 Include 的 `internal/update` 监听器，后者只 `_scheduleApply()`，不重启 Include 本身（`vendor/include/src/index.ts:284-290`）。

### 诊断 PENDING

PENDING 是合法状态，不报错也不打日志。另外，没有 logger exporter 时，Loader、Include、HMR 的错误只写进环形缓冲（`vendor/cordis/src/logger.ts:212-224`），本地实验务必挂上 `@deepseek-ai/cordis-plugin-logger-console`。建议的排查顺序是：先确认日志能看到；再确认条目有 Fiber（导入失败的条目没有 Fiber，`entry.ts:263-270`）；然后看状态，PENDING 查 inject 和隔离域，FAILED 看 `fiber.await()` 抛出的错误；最后在 profile 模式下用 `--dump-config` 核对最终组合。app-boot 的 `captureStartupComposition` 会列出每个缺失服务在 store 里的同名实现，并标注 `same-scope` 或 `other-scope`（`packages/boot/app-boot/src/startup-diagnostics.ts:74-104`），正好用来诊断"服务存在但被 isolate 挡住"的情况。

---

## 教程 07 在 4b399c3 上跑不通：缺两行组合，还有一个没写进 inject 的顺序依赖

教程 07 的组合只列了 `dsh-system-prompt`、`dsh-tools`、`tool-logger`、`greet-tool` 四行，并说明"`dsh-tools` 会注入 `systemPrompt`"（`docs/cordis-tutorial/07-into-the-harness.zh.md:76-96`）。但 `4b399c3` 上的真实依赖链更长：`greet-tool → tools`（`ToolsService.inject = ['systemPrompt']`，`packages/core/tools/src/index.ts:100`）`→ systemPrompt`（`SystemPrompt.inject = ['sessionProjections']`，`packages/core/system-prompt/src/index.ts:486-487`）`→ sessionProjections`（`packages/session/session-projection/src/index.ts:282-296`）。此外还需要一个 executor 插件，**这条依赖没有用 inject 表达**。四组实验逐步补齐组合，结果如下：

| 实验 | 组合 | 结果 |
|---|---|---|
| A | 教程原样四行 | **无任何输出，`exit=0`**。状态转储：`ToolsService PENDING missing=systemPrompt`、`SystemPrompt PENDING missing=sessionProjections`，tool-logger 和 greet-tool 都是 `missing=tools` |
| B | + `dsh-session-projection` | greet-tool 激活，但它在 `apply` 里发起、不等结果的那次 `execute` 抛出 `ToolExecutorUnavailableError`，未处理的 rejection 让进程崩溃 |
| C | 再 + `dsh-tool-executor-native`，用 Loader 加载 | **仍然崩溃，`exit=1`**，栈指向 `greet-tool.ts:28` 的 `Proxy.execute → selectExecutor`，`code: 'TOOL_EXECUTOR_UNAVAILABLE'` |
| D | 程序化加载，按顺序逐个 `await`，executor 先于 greet-tool | 输出与教程一致：先 `[tool-logger] greet -> Hello, Cordis!`，后 `tool replied: [...]` |

实验 C 失败的原因是：greet-tool 和 native executor 都只声明了 `inject: ['tools']`（`packages/core/tool-executor-native/src/index.ts:24-27`）。`tools` 一就绪，两者就同时可以激活，没有任何依赖规定 `setExecutor()` 要先执行，而 greet-tool 在 `apply` 里立刻就调用了 `execute`。`ctx.tools.execute()` 本身只做分派：`selectExecutor(input.agent).execute(input)`，找不到 executor 就抛错（`tools/src/index.ts:353-378`、`executor.ts:96-102`）。更出人意料的是，**没有 executor 时，连 `systemPrompt.assemble()` 都会失败**。原因是 ToolsService 构造时向 systemPrompt 注册的 schema provider 要先经过 `selectExecutor()` 再 `describe()`（`tools/src/index.ts:111-118`）。实验 `no-executor.mjs` 的输出：

```text
schemas(): [ 'noop' ]
assemble -> TOOL_EXECUTOR_UNAVAILABLE The selected tool executor is unavailable. Load an executor in this composition before running the agent.
execute -> TOOL_EXECUTOR_UNAVAILABLE
```

所以"装了 tools 但没装 executor"的效果不是"工具看得见、调不了"，而是整个 agent 都无法组装 prompt。可用的修正是：组合里补上 `@deepseek-ai/dsh-session-projection` 和 `@deepseek-ai/dsh-tool-executor-native`（base bundle 本来就有这几行，`packages/bundle/base/cordis.patch.yml:173-174, 526-543`）；演示调用不要放在 `apply` 里，或者等 executor 就绪后再调用。目前没有可以 inject 的"executor 就绪"服务，只能捕获 `TOOL_EXECUTOR_UNAVAILABLE` 后重试，或在组合层保证加载顺序。真实场景中，调用来自 agent loop，那时 executor 早已就绪，所以这个问题只影响"在 apply 里自调工具"的演示写法。这些实验用的是 npm 发布包，没有在 pnpm workspace 里按教程原样执行 `node --import tsx ../../vendor/cordis/bin.js`；包源码与仓库逐字节一致，但 workspace 是否会额外加载其他插件没有验证。

实验 D 还确认了流水线里一个对写门禁很重要的事实：**参数校验不在流水线前段**，而在 `defineTool` 包装出的 `execute` 里（`packages/core/tools/src/schema.ts:578-603`）。传入 `{"name":42}` 时，`[pre-execute] greet args={"name":42}` 和 `[post-execute] greet isError=true` 都执行了，最后才以 `ToolArgsError`（`INVALID_ARGS`）结束。JSON 解析失败的参数甚至会以原始字符串的形式进入流水线（`packages/core/tools/src/batch.ts:98-105`）。因此 `tools/pre-execute` 监听器和 guard 不能假定参数已经通过 schema 校验。调用不存在的工具时同样先经过 pre-execute，最终以 `ToolNotFoundError`（`UNKNOWN_TOOL`）结束。`tools/result` 在 `execute` 返回的 promise 兑现之前发出（日志里 `[tool-logger]` 先于 `tool replied`），并且不会被 await，所以不能用它来保证"审计写完再继续"。

---

## acme-ledger：把服务、事件和卸载顺序装进一个插件（07 自写插件）

### 设计

示例由四个插件组成，在真实的 Cordis Loader 和真实的 `dsh-tools / system-prompt / session-projection / tool-executor-native` 0.2.3-alpha.2 上运行。没有 LLM 和 agent loop，模型的调用由脚本直接调用 `root.tools.execute(...)` 代替，因此作用域过滤和会话日志没有参与。

| 插件 | 角色 | 关键写法 |
|---|---|---|
| `acme-ledger` | 提供 `acmeLedger` 服务，持有 `FileHandle` 和 flush 定时器 | 一个异步生成器 effect，**最后才 `yield ctx.provide(...)`**；`tag` 字段标为 `.volatile()` |
| `acme-ledger-memory` | 同名服务的内存实现 | 演示更换提供方 |
| `acme-ledger-audit` | 消费方：监听 `tools/result` 记账，注册 `ledger_stats` 工具和一个 prompt section | 顶层 `inject = ['tools','systemPrompt','acmeLedger']`；最后一条记录写在 async disposer 里 |
| `acme-ledger-tail` | 观察方：监听 `acme-ledger/flushed` | 没有顶层 inject，只用 `ctx.inject(['acmeLedger'], …)` 让 `watch` 这一部分等服务 |

设计上的四个选择分别对应前面各节的机制。**调用方归属**：服务契约是抽象类，没有继承 `Service`，在构造函数里装上 `Service.tracker`，于是 `append()` 能用 `this.ctx.fiber.name` 给记录署名，`watch()` 用 `this.ctx.effect()` 把订阅登记在调用方身上。**清理顺序**：按 lifecycle 一文推荐的生成器写法，资源先 `yield`、服务最后 `yield`，逆序回卷时依次是撤下服务并等消费方卸载完、清除定时器、最终 flush、关闭文件。**事件隔离**：照搬 `tools/result` 的 `dispatch` 加逐个 try/catch。**写入串行化**：`flush()` 串在一条 promise 链上，撤服务时如果正有一次定时 flush 在进行，最终 flush 会排在它后面。

### 源码（实际运行的版本）

```ts
// acme-ledger.ts — file-backed provider of the `acmeLedger` service.
import { open, type FileHandle } from 'node:fs/promises'
import { Service, type Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/cordis-plugin-loader'
import Schema from '@deepseek-ai/schemastery'
import { log } from './log.ts'

export interface LedgerEntry { kind: string; [key: string]: unknown }
export interface FlushInfo { path: string; count: number; reason: string; owners: string[] }
export interface LedgerStats { backend: string; path: string; appended: number; flushed: number; buffered: number; watchers: number }

declare module '@deepseek-ai/cordis' {
  interface Context { acmeLedger: AcmeLedger }
  interface Events {
    /** A batch was written to the backend. Listener failures are contained. @mode emit */
    'acme-ledger/flushed'(info: FlushInfo): void
  }
}

/** The service contract both providers implement. Not a Service subclass: published by ctx.provide(). */
export abstract class AcmeLedger {
  protected readonly ctx: Context
  constructor(ctx: Context) {
    this.ctx = ctx
    // Let Cordis trace `this.ctx` to the CALLER's context when accessed as `ctx.acmeLedger`.
    Object.defineProperty(this, Service.tracker, { value: { property: 'ctx' } })
  }
  protected readonly watchers = new Set<(entry: LedgerEntry & { owner: string }) => void>()

  append(entry: LedgerEntry): void {
    const stamped = { ...entry, owner: this.ctx.fiber.name, tag: this.tag() }
    this.write(stamped)
    for (const watcher of this.watchers) {
      try { watcher(stamped) } catch (error) { log(`[acme-ledger] watcher failed: ${(error as Error).message}`) }
    }
  }

  /** Watch appends for the CALLER's lifetime; the disposer is an effect on the caller's fiber. */
  watch(listener: (entry: LedgerEntry & { owner: string }) => void): () => void {
    const watchers = this.watchers
    return this.ctx.effect(() => {
      watchers.add(listener)
      return () => { watchers.delete(listener) }
    }, 'acmeLedger.watch()')
  }

  protected abstract tag(): string
  protected abstract write(entry: LedgerEntry): void
  abstract stats(): LedgerStats
}

export const name = 'acme-ledger'
export interface Config { path: string; flushIntervalMs: number; tag: Schema.Volatile<string> }
export const Config: Schema<Config> = Schema.object({
  path: Schema.string().required().description('Append-only JSONL file. Changing it restarts the plugin.'),
  flushIntervalMs: Schema.natural().default(50),
  tag: Schema.string().default('v1').volatile().description('Stamped on each entry; changes apply in place.'),
}) as any

/** Emit with per-listener containment, the way dsh-tools notifies `tools/result`. */
function emitContained(ctx: Context, name: 'acme-ledger/flushed', info: FlushInfo): void {
  for (const callback of ctx.events.dispatch('emit', [name, info])) {
    try {
      const returned: unknown = callback(info)
      void Promise.resolve(returned).catch((error: unknown) => log(`[acme-ledger] async listener failed: ${(error as Error).message}`))
    } catch (error) {
      log(`[acme-ledger] listener failed (contained): ${(error as Error).message}`)
    }
  }
}

class FileLedger extends AcmeLedger {
  buffer: (LedgerEntry & { owner: string })[] = []
  appended = 0
  flushed = 0
  private chain: Promise<void> = Promise.resolve()
  private readonly owner: Context
  private readonly config: Config
  private readonly handle: FileHandle
  constructor(ctx: Context, owner: Context, config: Config, handle: FileHandle) {
    super(ctx)
    this.owner = owner
    this.config = config
    this.handle = handle
  }
  protected tag(): string { return this.config.tag.get() }
  protected write(entry: LedgerEntry & { owner: string }): void { this.appended++; this.buffer.push(entry) }
  stats(): LedgerStats {
    return { backend: 'file', path: this.config.path, appended: this.appended, flushed: this.flushed, buffered: this.buffer.length, watchers: this.watchers.size }
  }
  /** Serialized flush: an interval flush and the final flush never interleave. */
  flush(reason: string): Promise<void> {
    this.chain = this.chain.then(async () => {
      if (this.buffer.length === 0) return
      const batch = this.buffer.splice(0)
      await this.handle.appendFile(batch.map(e => JSON.stringify(e)).join('\n') + '\n')
      this.flushed += batch.length
      emitContained(this.owner, 'acme-ledger/flushed', {
        path: this.config.path, count: batch.length, reason, owners: [...new Set(batch.map(e => e.owner))],
      })
    })
    return this.chain
  }
}

export function apply(ctx: Context, config: Config): void {
  log(`[acme-ledger] apply path=${config.path} tag=${config.tag.get()}`)
  ctx.on('loader/volatile-update', (paths) => {
    log(`[acme-ledger] volatile update ${JSON.stringify(paths)} -> tag=${config.tag.get()} (no restart)`)
  })
  ctx.effect(async function* () {
    const handle = await open(config.path, 'a')                       // 1. resource, disposed LAST
    yield async () => { await handle.close(); log(`[acme-ledger] file closed ${config.path}`) }
    const ledger = new FileLedger(ctx, ctx, config, handle)
    yield async () => { await ledger.flush('final'); log(`[acme-ledger] final flush done (appended=${ledger.appended}, flushed=${ledger.flushed})`) }
    const timer = setInterval(() => { void ledger.flush('interval') }, config.flushIntervalMs)
    yield () => { clearInterval(timer); log('[acme-ledger] timer cleared') }
    yield ctx.provide('acmeLedger', ledger)                            // 4. publish LAST
    log('[acme-ledger] service published')
  }, 'acme-ledger:resources')
}
```

```ts
// acme-ledger-memory.ts — alternative provider of the same service (no resources).
import type { Context } from '@deepseek-ai/cordis'
import { AcmeLedger, type LedgerEntry, type LedgerStats } from './acme-ledger.ts'
import { log } from './log.ts'

class MemoryLedger extends AcmeLedger {
  readonly entries: LedgerEntry[] = []
  protected tag(): string { return 'mem' }
  protected write(entry: LedgerEntry): void { this.entries.push(entry) }
  stats(): LedgerStats {
    return { backend: 'memory', path: '', appended: this.entries.length, flushed: 0, buffered: this.entries.length, watchers: this.watchers.size }
  }
}
export const name = 'acme-ledger-memory'
export function apply(ctx: Context): void {
  ctx.effect(function* () {
    const ledger = new MemoryLedger(ctx)
    yield () => { log(`[acme-ledger-memory] dropped ${ledger.entries.length} in-memory entries: ${ledger.entries.map(e => e.kind).join(',')}`) }
    yield ctx.provide('acmeLedger', ledger)
    log('[acme-ledger-memory] service published')
  }, 'acme-ledger-memory:resources')
}
```

```ts
// acme-ledger-audit.ts — consumer.
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type {} from './acme-ledger.ts'
import { log } from './log.ts'

export const name = 'acme-ledger-audit'
export const inject = ['tools', 'systemPrompt', 'acmeLedger']

export function apply(ctx: Context): void {
  const ledger = ctx.acmeLedger            // traceable proxy: `this.ctx` is THIS plugin's context
  log(`[audit] apply (backend=${ledger.stats().backend})`)
  ledger.append({ kind: 'audit-start' })

  ctx.on('tools/result', (exec, result) => {
    ledger.append({ kind: 'tool', tool: exec.name, isError: result.isError })
  })

  ctx.tools.register(defineTool({
    name: 'ledger_stats',
    description: 'Report how many audit entries the ledger holds.',
    parameters: {},
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          backend: { type: 'string', required: true },
          appended: { type: 'integer', required: true },
          flushed: { type: 'integer', required: true },
          buffered: { type: 'integer', required: true },
        },
      },
      render: (_args, v) => [{ type: 'text', text: `${v.backend}: ${v.appended} appended, ${v.flushed} flushed, ${v.buffered} buffered` }],
    },
    async execute() {
      const { backend, appended, flushed, buffered } = ledger.stats()
      return { backend, appended, flushed, buffered }
    },
  }))

  ctx.systemPrompt.section({
    name: 'acme-ledger:audit',
    order: 5100,   // after TOOLS_SDK: 5000; third parties must pick a number themselves
    text: 'Every tool call in this deployment is recorded in an audit ledger; call ledger_stats to inspect it.',
  })

  // The final write. Async disposer: runs while the provider waits in its unprovide step.
  ctx.effect(() => async () => {
    await new Promise(r => setTimeout(r, 10))
    try { ledger.append({ kind: 'audit-closing' }); log('[audit] final entry appended') }
    catch (error) { log(`[audit] final entry FAILED: ${(error as Error).message}`) }
  }, 'audit:final-entry')
}
```

```ts
// acme-ledger-tail.ts — observer with no hard dependency on the service.
import type { Context } from '@deepseek-ai/cordis'
import Schema from '@deepseek-ai/schemastery'
import type {} from './acme-ledger.ts'
import { log } from './log.ts'

export const name = 'acme-ledger-tail'
export interface Config { faulty: boolean }
export const Config: Schema<Config> = Schema.object({ faulty: Schema.boolean().default(false) })

export function apply(ctx: Context, config: Config): void {
  if (config.faulty) ctx.on('acme-ledger/flushed', () => { throw new Error('tail listener bug') })
  ctx.on('acme-ledger/flushed', (info) => {
    log(`[tail] flushed ${info.count} (${info.reason}) from ${info.owners.join('+')} -> ${info.path.split('/').pop()}`)
  })
  ctx.inject(['acmeLedger'], (ctx) => {   // only this child fiber waits for / restarts with the provider
    ctx.acmeLedger.watch((entry) => {
      if (entry.kind !== 'tool') log(`[tail] watch: ${entry.kind} by ${entry.owner} tag=${String(entry.tag)}`)
    })
  })
}
```

`log.ts` 只是一个带相对毫秒时间戳的 `console.log`。等价的 cordis.yml 如下。注意 executor 行要在任何"apply 里自调工具"的插件之前就绪，并且本地调试时应加上 `@deepseek-ai/cordis-plugin-logger-console`。实验本身是通过 `root.loader.create/update/remove` 以程序方式驱动的，走的是真实的 `Entry.update → _commitVolatile` 路径，**没有用下面这份 YAML 运行过**。

```yaml
- name: '@deepseek-ai/cordis-plugin-logger-console'
- name: '@deepseek-ai/dsh-session-projection'
- name: '@deepseek-ai/dsh-system-prompt'
- name: '@deepseek-ai/dsh-tools'
- name: '@deepseek-ai/dsh-tool-executor-native'
- id: acme-ledger
  name: './acme/acme-ledger.ts'
  config: { path: ./ledger.jsonl, flushIntervalMs: 50, tag: v1 }
- id: acme-ledger-audit
  name: './acme/acme-ledger-audit.ts'
- id: acme-ledger-tail
  name: './acme/acme-ledger-tail.ts'
  config: { faulty: true }
```

还有两点运行前提。插件以 `.ts` 形式靠 Node 24 的 type stripping 直接运行，**没有经过 `tsc` 类型检查**，`Schema.Volatile<string>` 这样的类型写法是否完全正确没有核实。第一次运行时 provider 用了 TS parameter property 语法，strip-only 模式不支持，导入失败，唯一的表现是 `ledger=none`，加上 logger-console 之后才看到报错。所以上面的类都用显式字段赋值。

### 运行记录

驱动脚本分 8 步执行，下面是关键输出（第一列为相对毫秒）：

```text
   74 tail=ACTIVE audit=PENDING                                   # 1. 先装消费方：audit 缺 acmeLedger
  218 tools=ACTIVE executor=ACTIVE audit=PENDING                  # 2. harness 就绪，audit 仍等服务
  224 [audit] apply (backend=file)                                # 3. provider 到达
  275 tools visible: greet,ledger_stats
  276 prompt sections: harness:identity,deployment:persona-prefix,acme-ledger:audit,deployment:persona-suffix
  277 [acme-ledger] listener failed (contained): tail listener bug  # 4. 抛错的监听器被隔离
  277 [tail] flushed 3 (interval) from acme-ledger-audit -> a.jsonl
  401 [acme-ledger] volatile update [["tag"]] -> tag=v2 (no restart) # 5. 只改 tag
  401 same fiber object: true ledger=ACTIVE audit=ACTIVE
  517 [audit] final entry appended                                # 6. 改 path：provider 重启
  519 [acme-ledger] timer cleared
  521 [tail] flushed 1 (final) from acme-ledger-audit -> a.jsonl
  521 [acme-ledger] file closed /tmp/acme-ledger-FV3oBY/a.jsonl
  522 [acme-ledger] apply path=/tmp/acme-ledger-FV3oBY/b.jsonl tag=v2
  715 file A: audit-start@…/v1 | tool:greet@…/v1 | … | tool:greet@…/v2 | audit-closing@…/v2
  731 audit=PENDING after file provider removed                   # 7. 卸载 provider，再换内存实现
  731 file B: audit-start@…/v2 | tool:greet@…/v2 | audit-closing@…/v2
  736 [audit] apply (backend=memory)
  802 [acme-ledger-memory] dropped 3 in-memory entries: audit-start,tool,audit-closing
  871 [audit] final entry appended                                # 8. dispose 整个 Loader Fiber
  873 [acme-ledger] timer cleared
  874 [acme-ledger] file closed /tmp/acme-ledger-FV3oBY/b.jsonl
  926 active resources before: ["PipeWrap","Timeout"]
  927 active resources after : ["PipeWrap"]
```

这份输出验证了前面每一节的结论。消费方在 provide 之前保持 PENDING，provide 之后立刻激活，它注册的工具和 section 同时出现。volatile 更新没有换 Fiber 对象，之后的记录 tag 变成 `v2`。在配置重启、单条移除、整树 dispose 三种卸载路径下，消费方的 `audit-closing` 都写在 `timer cleared` 和 `file closed` 之前，没有丢失。内存实现被移除时丢弃的 3 条记录里也有 `audit-closing`。dispose 之后 `Timeout` 消失，没有残留句柄（`PipeWrap` 是 stdout 重定向造成的）。`ledger_stats` 第一次返回 `2 appended`、第二次返回 `3 appended`，因为工具自身那次调用的 `tools/result` 要等它返回之后才入账。

另外两个附加实验确认了归属。`caller-context.mjs` 中，audit 的 `fiber.getEffects()` 是 `["ctx.on(\"tools/result\")","tools.register()","systemPrompt.section()","audit:final-entry"]`，卸载后 `tools.schemas()` 变成 `[]`，section 也从 `assemble()` 中消失。tail 的 `watch()` 订阅落在 `ctx.inject` 创建的子 Fiber 上，tail 卸载后 watchers 从 1 变成 0。从 root 调用 `append()` 时，记录的署名是 `root`。`lazy-access.mjs` 中，消费方在 disposer 里**重新读取** `ctx.acmeLedger` 也能成功写入（文件内容为 `audit-start,audit-closing`），因为撤回步骤要等 `dependents` 清空后才把 impl 从 `provisions` 中移走（`reflect.ts:333-347`）。

这里有一个不能外推的边界。本实验的消费方清理只花了 10ms。lifecycle 一文引用的官方审计规则指出，外层整体卸载时，"最后 provide"的写法"不是完整修复"。耗时更长的清理、多个消费方并发卸载时的表现没有测试过。

---

## 坑点清单

| # | 症状 | 原因 | 对策 | 依据 |
|---|---|---|---|---|
| 1 | `cannot get property "x" without inject` | 有 runtime 的插件读服务必须先声明 | 硬依赖写 `inject`，可选依赖用 `ctx.get` 加 `ctx.inject` | `reflect.ts:185-202`；实验 1 |
| 2 | 第二个提供方 FAILED，旧的卸载后也不接班 | 同一隔离域只能有一个实现，FAILED 不自愈 | 先卸旧再装新；需要并存就用 `isolate` | `reflect.ts:324-327`、`fiber.ts:655-656`；实验 1 |
| 3 | 调用方卸载后，注册还留在服务里 | 服务方法用了构造时存下的 ctx，或没登记成 effect | 方法里始终用 `this.ctx.effect(...)`，返回 disposer | 实验 3、4；`store.ts:219-262` |
| 4 | 解构服务方法后 `TypeError` | 只有 `thisArg === receiver` 时才替换成 shadow | 始终写 `ctx.svc.method()` | `utils.ts:161-168`；实验 4 |
| 5 | 改了内部状态，消费方仍 PENDING | `[Service.check]` 是拉模式 | 状态变化后 `ctx.reflect.notify([name])` | 实验 2、5 |
| 6 | `ctx.get` 在 apply 或 init 期间返回 `undefined` | 默认严格模式，只认 ACTIVE 提供方 | 需要时用 `ctx.get(name, false)`；不要长期持有返回值 | `reflect.ts:269-279`；实验 6 |
| 7 | 一个监听器抛错，后面全没执行，异常抛到发出方 | `emit` 不隔离 | 发通知用 `events.dispatch('emit', …)` 逐个 try/catch | `events.ts:207-210`；实验 04 |
| 8 | async 监听器的 reject 变成 `unhandledRejection` | `emit` 丢弃返回的 promise | 同上，对返回值 `.catch` | 实验 04 |
| 9 | waterfall 下游默认行为消失，或调用方拿到 `undefined` | 没调 `next()`，或调了没 `return` | 观察型监听器写 `return await next()` 的形式；`next` 只调一次 | `events.ts:250-265`；实验 03 |
| 10 | `serial`/`bail` 在返回 `0` 或 `''` 时停了；`bail` 遇到 async 监听器总是短路 | `isBailed` 只放行 `null/false/undefined` | 数值结果不要用 bail 类模式 | `events.ts:12-14`；实验 02 |
| 11 | 监听器顺序和预期不一样 | 只有注册顺序加 `prepend`，其他插件可以插到前面 | 确定性策略写在服务代码路径里，或用 `tools.guard()` | `user-approval/src/index.ts:297-302` |
| 12 | `ctx.on('turn/start')` 从不触发 | 它是会话事件类型，不是 Cordis 事件 | 监听 `session/event` 再判断 `event.type` | `events.zh.md:104-106` |
| 13 | 门禁拿到的参数类型不对，甚至是字符串 | 参数校验在工具主体里，晚于 pre-execute 和 guard | 门禁自己做防御性检查 | `schema.ts:578-603`、`batch.ts:98-105` |
| 14 | Config 被忽略，apply 收到 `undefined` | `export default function` 搭配具名 `Config` | 只用具名导出，或 default 导出带 `static Config` 的对象或类 | `loader/src/index.ts:232`；实验 E1 |
| 15 | 用户覆盖一个字段后，其他 config 字段消失 | patch 按 id 整块替换 `config` | 默认值写在 schema 的 `.default()` 里 | `patch.ts:165-169`；实验 E5 |
| 16 | 改了 volatile 字段，插件却重启了 | 同次编辑还改了别的键或普通字段，或 Fiber 不是 ACTIVE | 单独改 volatile；需要立即生效的值用 `.get()` 现读 | `entry.ts:155-211`；实验 E2 |
| 17 | volatile 非法值只 warn，下次启动却 FAILED | 非法值已写回磁盘 | 改配置后检查日志 | 实验 E3 |
| 18 | `disabled: !!js` 在环境变化后不生效 | 只在树协调时求值，文件没变就不协调 | 改文件或手动触发协调 | `include/src/index.ts:411`；实验 E2 |
| 19 | yml 里的插件永久 PENDING，没有提示 | 条目 `inject` 叠加到插件 inject 上（例如笔误） | 用 `captureStartupComposition` 或遍历 registry 查缺失服务 | `loader/src/index.ts:149-155`；实验 E3 |
| 20 | 插件自释放后，文件里多了 `disabled: true` | Loader 把自释放写回用户文件 | 需要"暂时停止"时不要调 `ctx.fiber.dispose()` | `loader/src/index.ts:157-195` |
| 21 | 匿名行改了自身配置就重建 | 没有 id，按内容匹配失败后换新 id | 每行写显式 `id`（教程 06 关于"改相邻行也重建"的说法已过时） | `include/src/index.ts:615-662`；实验 E2 |
| 22 | 改代码没反应 | 默认 `root: []`；没有 timer 或 `--expose-internals`；新建文件不触发；`node_modules` 不参与 | 开启 root，挂上 timer，保存已加载的那个文件 | `hmr/src/index.ts:211-226, 436-489`；实验 E4 |
| 23 | HMR 后定时器或连接翻倍 | 模块顶层副作用不会随逐出清理 | 所有副作用放进 `apply` 或 `ctx.effect` | `hmr` 逐出逻辑；lifecycle 一文"模块级状态" |
| 24 | 按教程 07 运行没有输出，exit 0 | 缺 `dsh-session-projection`，整条链 PENDING | 补上该行 | 实验 A |
| 25 | `TOOL_EXECUTOR_UNAVAILABLE`（execute 或 assemble 时） | 没有 executor，或 executor 比调用方晚激活 | 组合里加 `dsh-tool-executor-native`；不要在 apply 里自调工具 | `tools/src/index.ts:111-118, 353-378`；实验 B、C |
| 26 | 本地 `.ts` 插件被静默跳过 | strip-only 不支持某些 TS 语法；没有 logger 看不到错误 | 挂上 `cordis-plugin-logger-console`；避免 parameter property | `logger.ts:212-224`；acme 首次运行 |
| 27 | 消费方最后一次写入失败 | 资源 effect 和服务发布是并发执行的兄弟 effect | 一个生成器 effect，服务最后 `yield ctx.provide` | lifecycle 一文竞态一节；acme 运行记录 |
| 28 | 第三方 section 位置不好控制 | `getSectionOrder()` 只认内置名字 | 自选数字（本文用 5100），目前没有官方区间 | `system-prompt/src/index.ts:191-231` |

---

## Conclusion

读完源码再看 dsh 的插件模型，最值得记住的不是 API 列表，而是**归属**的计算方式：一个注册属于谁，由"是哪个 context 调用了这个方法"决定，与注册写在哪个类里无关。只要服务方法通过 tracker 拿到调用方 ctx，并把修改包进 `this.ctx.effect`，"插件卸载即撤销"就成为免费的性质。配置重启、HMR 替换、更换提供方、isolate 变更，最终都归结为同一个动作：卸载再加载。反过来，dsh 在 Cordis 之上补的东西几乎都在弥补"免费性质"覆盖不到的地方，包括 `events.dispatch` 加逐个隔离、作用域 carrier、生成器 effect 里最后 provide 的约定、`captureStartupComposition` 诊断。这说明在这个框架里，**错误隔离和启动顺序是使用者的责任**，框架不提供兜底。

教程 07 跑不通暴露出一个更普遍的问题：dsh 的不少依赖没有写进 `inject`，例如 executor 必须先于工具调用就绪、section 的 order 区间、"tools 少了 executor 就组装不了 prompt"。这些约束只在 base bundle 的行顺序和代码注释里体现。第三方插件要想稳定运行，最可靠的做法是直接叠加在 base bundle 上，不要从零拼组合。依赖启动顺序的行为（apply 里自调工具、等待别的插件的副作用）要么改成由事件触发，要么在代码里显式处理 `TOOL_EXECUTOR_UNAVAILABLE` 这类"尚未就绪"的错误。文档方面，有三处与 `4b399c3` 不符，值得向上游反馈：教程 07 的组合清单、教程 06 关于匿名 id 的描述、`ctx.on` disposer 返回值的说明。
