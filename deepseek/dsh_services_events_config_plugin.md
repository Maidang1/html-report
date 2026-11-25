---
title: dsh 的服务、事件、配置与插件实战：从 Cordis 源码到一个能卸载干净的插件
kicker: 机制拆解
deck: 对应学习计划的 03 服务、04 事件、05/06 配置·组合·HMR、07 接进 harness。本文读 dsh 仓库里 vendored Cordis 和 harness 包的源码，用实际运行的实验逐条核对教程里的说法：服务的归属是怎么算出来的、五种事件分发模式出错时各自怎样、配置什么时候校验、改哪些字段才不重启、HMR 替换的边界在哪；再指出教程 07 在这个 commit 上跑不通的原因，最后写一个在真实 harness 包上跑通的插件，把服务、事件和卸载清理串起来。
meta:
  - deepseek-ai/deepseek-harness @ 4b399c3（2026-11-22）
  - '@deepseek-ai/cordis 4.2.0-alpha.1 · dsh-* 0.2.3-alpha.2'
  - 实验均在 Node 24 上实际运行
footer: 来源：github.com/deepseek-ai/deepseek-harness（commit 4b399c3）的 vendor/cordis、vendor/loader、vendor/include、packages/core/tools、packages/boot/hmr、packages/boot/app-boot、docs/cordis-tutorial（03–07）。标注仓库路径的代码按行号原样摘录；标注“实验”的脚本和输出来自本文编写的实验，运行在与仓库同版本的 npm 包上（dsh-tools 的源码与仓库逐字节一致），临时目录路径替换成了 &lt;lab&gt;，毫秒数每次运行略有出入。
index: index.html
updated: 2026-11-24
created: 2026-11-24 01:00
---

前两篇讲了[怎么给 dsh 写插件](dsh_plugin_guide.html)和[插件活多久、卸载时留下什么](dsh_lifecycle_and_effects.html)。这一篇补上剩下的四块：服务、事件、配置与组合、接进 harness。它们对应 Cordis 教程的 03 到 07 章，也是学习计划里 D16 到 D19 的内容。

写法和上一篇一样：每个说法都落到源码行号，能跑的都跑一遍。教程大部分是对的，但有三处与 commit `4b399c3` 不符，其中一处会让教程 07 的示例直接跑不起来，文中会逐一指出。Fiber 状态机、`ctx.effect`、逆序卸载和“提供方先释放资源”的竞态已在上一篇讲过，这里只引用结论。

- **服务就是一条记录**：根 store 里以隔离 symbol 为键的 `Impl`。同一作用域只能有一个实现，第二个提供方直接 FAILED，旧的卸载后它也不会接班
- **注册归属调用方**：通过 `ctx.x` 拿到的服务是一个 Proxy，方法里的 `this.ctx` 是调用方的 context，所以 `ctx.tools.register()` 随调用方卸载自动撤销
- **五种分发模式**：只有 `parallel` 在有监听器出错时仍让其余监听器跑完；`emit` 一个同步 throw 就中断，dsh 的通知类事件因此全部改用逐个 try/catch
- **配置激活时才校验**：按 id 覆盖的 patch 整块替换 `config`，volatile 字段要五个条件同时成立才免重启；HMR 默认只热重载配置
- **教程 07 跑不通**：缺 `dsh-session-projection` 时整条链静默 PENDING、exit 0；补齐后还缺一个没写进 `inject` 的 executor 顺序依赖

## 服务是一条 Impl 记录：同一作用域只有一个实现，后来者不接班

Cordis 里“服务”没有什么特别的数据结构。`ctx.provide(name, value)` 往根 `ReflectService.store` 里写一条 `Impl`，键是当前作用域给这个名字分配的隔离 symbol。`Service` 基类的构造函数也只是装上调用方追踪（下一节讲），再调 `ctx.reflect.provide`。所以**继承 `Service` 不是必需的**：Loader 自己就是手动装 tracker 再 provide 的。

看 `provide()` 的顺序：它本身是一个 `fiber.effect`，先以 `++this._implCounter` 建好 `Impl`，**再**检查这个键是否已被占用，占用就抛 `service "x" has been registered at <…>`。写入后记录同时进入提供方 Fiber 的 `provisions`。提供方还不是 ACTIVE 时不通知；等它进入 ACTIVE 再统一 notify，所以 Service 子类虽然在构造函数里就注册了，消费方要等 `[Service.init]` 完成才会启动。

撤销函数分三步：从 store 删掉并通知同隔离域；`await` 直到 `impl.dependents` 清空；最后才从 `provisions` 移走。第三步排在最后，消费方才能在自己的 teardown 里继续读到服务。

```ts title="vendor/cordis/src/reflect.ts:313-349"
  provide(name: string, value?: any, check?: () => boolean) {
    return this.ctx.fiber.effect(() => {
      if (!this.props[name]) {
        this.props[name] ??= { type: 'service' }
      } else if (this.props[name].type !== 'service') {
        throw new Error(`property "${name}" is already declared as ${this.props[name].type}`)
      }
      this.props[name] = { type: 'service' }

      this.ctx[symbols.root][symbols.isolate][name] ??= Symbol(name)
      const key = this.ctx[symbols.isolate][name]
      const impl: Impl = { uid: ++this._implCounter, name, value, ctx: this.ctx, check, isolate: key, dependents: new Set() }
      if (this.store[key]) {
        throw new Error(`service "${name}" has been registered at <${this.store[key].ctx.fiber.name}>`)
      }
      this.store[key] = impl
      this.ctx.fiber.provisions.add(impl)
      if (this.ctx.fiber.state === FiberState.ACTIVE) {
        this.notify([name])
      }
      return async () => {
        if (this.store[impl.isolate] === impl) delete this.store[impl.isolate]
        this.ctx.isolate(name, impl.isolate).reflect.notify([name])
        // Retired bindings cannot gain new dependents. Wait only for their
        // cleanup, even if a fiber has left the registry or starts a new load.
        // Yield even without direct dependents so child cleanup can still
        // access the inherited service before its provision is removed.
        await new Promise<void>((resolve) => {
          impl.release = resolve
          if (!impl.dependents.size) resolve()
        })
        impl.release = undefined
        // ensure self access before dependencies cleanup
        this.ctx.fiber.provisions.delete(impl)
      }
    }, `ctx.provide(${JSON.stringify(name)})`)
  }
```

### 换提供方会让消费方重跑，重复提供会直接失败

消费方的 epoch 由修订号和各个 inject 服务的 `uid` 拼成（上一篇讲过 epoch 驱动重载）。换一个提供方，uid 就变，消费方先卸载、再在新实现上重跑。实验 1 先挂提供方 A，再在 A 还活着时挂 B，然后卸 A、挂 C：

- B 加载即 FAILED（状态 3），**A 卸载后 B 也不会顶上**：FAILED 只有 `update()` 能清掉，依赖变化不会让它重试
- 失败的 B 也消耗了一个 uid，所以 C 拿到的是 3
- 有 runtime 的插件里，**没在 `inject` 里声明就读 `ctx.greeter` 会抛错**，即使服务已经存在；`ctx.get()` 不受这个限制

教程说“卸载 `dsh-bash-local`、挂载另一个 `shell` 提供方，所有注入者都会重启”。这句话成立的前提是**先卸旧、再装新**。

```text title="实验 1 输出：替换提供方、重复注册、未 inject 直接读"
consumer state before provider: 0
consumer apply: [A] hello world | impl uid = 1 | epoch = 0:1
--- duplicate provider B while A active
B state: 3 B error: service "greeter" has been registered at <Greeter>
--- dispose A
consumer effect disposed
consumer state after A gone: 0
--- B has failed; load C
consumer apply: [C] hello world | impl uid = 3 | epoch = 0:3
--- access without inject
ctx.greeter threw: cannot get property "greeter" without inject
ctx.get("greeter"): [C] hello via get
FiberState map: undefined
```

### 就绪判定：inject 是持续订阅，check 是拉模式

就绪判定在消费方的 `_checkImpl`：只认提供方处于 ACTIVE 的 impl；如果 impl 带 `check`，就以消费方的 ctx 调它，`check` 返回假或抛错都按“不可用”处理。`_refresh` 把所有 inject 项的 uid 拼成 epoch，缺一项就是 INACTIVE，也就是 PENDING。

提供方增删、状态切换都会触发 `reflect.notify(names)`，它遍历所有 Fiber 重新检查。问题在 `check`：**它只在 notify 时被重新求值**。服务内部状态变了而不手动 `ctx.reflect.notify([name])`，消费方会一直停在 PENDING。实验 2 里把 `ready` 置真后消费方仍是 0，调完 notify 才变成 2。packages 下没有一个包自己实现 check，唯一的真实用户是 Loader。

普通插件作者真正需要的扩展点是 `[Service.init]`：它会被等待，packages 里有 41 处。`SystemPrompt` 在 init 里 `await` 注册 session projection，`WorkspaceRegistry` 在 init 里打开存储并用 `this.ctx.effect` 挂上关闭函数。

```ts title="vendor/cordis/src/fiber.ts:625-656"
  _checkImpl(name: string) {
    const impl = this.ctx.reflect._getImpl(name, true)
    if (!impl) return delete this._store[name]
    try {
      if (impl.check && !impl.check.call(getTraceable(this.ctx, impl.value))) {
        return delete this._store[name]
      }
    } catch (error) {
      impl.ctx.logger.error(error)
      return delete this._store[name]
    }
    this._store[name] = impl
  }

  _refresh() {
    let epoch = `${this._revision}`
    for (const name of Object.keys(this.inject)) {
      const impl = this._store[name]
      if (!impl) {
        epoch = INACTIVE
        break
      }
      epoch += ':' + impl.uid
    }
    this._setEpoch(epoch)
  }

  private _setEpoch(epoch: string) {
    const oldEpoch = this._runner.epoch
    if (epoch === oldEpoch) return
    // a failed fiber only recovers through update(), which clears _error
    if (this._error) return
```

```text title="实验 2 输出：init 被等待；check 只在 notify 时重查；提供方等消费方卸载完"
   4 Db init start
  59 Db init done
  84 provider state 2 | consumer state 0 | ctx.get(db) = object
  95 after ready=true WITHOUT notify: consumer state 0
 106 after reflect.notify: consumer state 2
 106 consumer teardown start, still can use db: rows
 142 consumer teardown end
 142 provider own effect disposed
```

## 调用方追踪：`ctx.tools.register()` 为什么知道是谁调的

上一篇说过，`ctx.tools.register()` 本身就是 effect，调用方卸载时注册自动撤销。可 `ToolsService` 只有一个实例，属于它自己的 Fiber，它怎么知道这次是哪个插件在调？

```svg title="图 1：同一个 ToolsService，两个调用方各自拿到“自己的 this.ctx”，注册落在调用方 Fiber 上" tone="light"
<svg viewBox="0 0 680 300" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="调用方追踪">
<defs><marker id="a1" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#504e49"/></marker></defs>
<rect x="14" y="40" width="170" height="64" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="99" y="64" text-anchor="middle" font-size="12" font-weight="600" fill="#141413">plugin-one 的 Fiber</text>
<text x="99" y="84" text-anchor="middle" font-size="11" fill="#504e49">ctx.tools.register(hello)</text>
<rect x="14" y="170" width="170" height="64" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="99" y="194" text-anchor="middle" font-size="12" font-weight="600" fill="#141413">plugin-two 的 Fiber</text>
<text x="99" y="214" text-anchor="middle" font-size="11" fill="#504e49">ctx.tools.register(bye)</text>
<rect x="250" y="96" width="180" height="84" rx="8" fill="#fff" stroke="#504e49" stroke-width="1.2" stroke-dasharray="5 4"/>
<text x="340" y="120" text-anchor="middle" font-size="12" font-weight="600" fill="#141413">getTraceable Proxy</text>
<text x="340" y="140" text-anchor="middle" font-size="11" fill="#504e49">读 tracker.property（'ctx'）</text>
<text x="340" y="156" text-anchor="middle" font-size="11" fill="#504e49">→ 返回使用方 ctx；方法调用</text>
<text x="340" y="172" text-anchor="middle" font-size="11" fill="#504e49">时 this 换成 shadow</text>
<rect x="496" y="96" width="170" height="84" rx="8" fill="#1B365D" stroke="#1B365D" stroke-width="1.2"/>
<text x="581" y="120" text-anchor="middle" font-size="12" font-weight="600" fill="#fff">ToolsService 实例</text>
<text x="581" y="140" text-anchor="middle" font-size="11" fill="#dfe7f1">只有一个，属于</text>
<text x="581" y="156" text-anchor="middle" font-size="11" fill="#dfe7f1">ToolsService 自己的 Fiber</text>
<line x1="185" y1="72" x2="248" y2="118" stroke="#504e49" stroke-width="1.4" marker-end="url(#a1)"/>
<line x1="185" y1="202" x2="248" y2="160" stroke="#504e49" stroke-width="1.4" marker-end="url(#a1)"/>
<line x1="431" y1="138" x2="494" y2="138" stroke="#504e49" stroke-width="1.4" marker-end="url(#a1)"/>
<path d="M 581 95 C 581 18, 120 10, 110 38" fill="none" stroke="#1B365D" stroke-width="1.4" marker-end="url(#a1)"/>
<text x="380" y="64" text-anchor="middle" font-size="11" font-weight="600" fill="#141413">this.layers.effect(this.ctx, …)：effect 登记在 plugin-one 上</text>
<path d="M 581 181 C 581 270, 120 280, 110 236" fill="none" stroke="#1B365D" stroke-width="1.4" marker-end="url(#a1)"/>
<text x="380" y="222" text-anchor="middle" font-size="11" font-weight="600" fill="#141413">同一行代码，effect 登记在 plugin-two 上</text>
<text x="360" y="290" text-anchor="middle" font-size="11" fill="#504e49">p1.dispose() → 只撤 hello，bye 不受影响；registry 没写任何“按插件清理”的代码</text>
</svg>
```

答案在 `getTraceable`。通过 ctx 代理读到的服务不是原始实例，而是一个 Proxy。在 Proxy 上读 tracker 声明的属性（`'ctx'`）时，返回的是**使用方**的 context；调用方法时，`this` 被换成一个 `ctx` 已被覆盖的 shadow。源码注释把分工写得很清楚：def site 决定服务解析，**use site 决定 intercept、isolate 和 effects**。

于是 `ToolsService.register()` 最后那行 `this.layers.effect(this.ctx, …)` 里的 `this.ctx` 是调用方的 ctx。`ScopedLayers.effect` 按 `scopeOf(ctx)` 选层，再调 `ctx.effect(...)`：这条注册既**归属调用方 Fiber**，又**落在调用方的 agent 作用域层**。工具重名的报错文案提示“register through that agent's `agent.ctx`”，依据的就是这套追踪。

```ts title="vendor/cordis/src/utils.ts:153-180"
function createShadow(useSite: Context, target: any, property: string | undefined, receiver: any) {
  if (!property) return receiver
  const value = getPropertyDescriptor(target, property)?.value
  if (!value) return receiver
  const defSite = (value[symbols.shadow] as Context | undefined) ?? value
  return withProp(receiver, property, useSite.extend({ [symbols.shadow]: defSite }))
}

function createShadowMethod(ctx: Context, value: any, outer: any, shadow: {}) {
  return new Proxy(value, {
    apply: (target, thisArg, args) => {
      if (thisArg === outer) thisArg = shadow
      return getTraceable(ctx, Reflect.apply(target, thisArg, args))
    },
  })
}

// `ctx` may be a shadow, i.e. a pair of:
// - def site: where the accessing code was defined, governs service resolution
// - use site: where the service is consumed, governs intercept, isolate and effects
function createTraceable(ctx: Context, value: any, tracker: Tracker) {
  const defSite = (ctx[symbols.shadow] as Context | undefined) ?? ctx
  const useSite = ctx[symbols.shadow] ? Object.getPrototypeOf(ctx) as Context : ctx
  const proxy = new Proxy(value, {
    get: (target, prop, receiver) => {
      if (prop === symbols.original) return target
      if (prop === symbols.caller) return defSite
      if (prop === tracker.property) return useSite
```
```ts title="packages/core/tools/src/index.ts:145-152"
    if (name === RUN_CODE_NAME) {
      throw new Error(`tool name "${RUN_CODE_NAME}" is reserved for the PTC mode presentation transport and cannot be registered or shadowed`)
    }
    return this.layers.effect(
      this.ctx,
      layer => layer.tools.insert(name, { definition, when: options.when }),
      { label: 'tools.register()' },
    )
```

### 两种写法会让追踪失效

实验 3 写了一个最小的 registry 服务，两个插件各注册一条，`p1.dispose()` 之后只剩 `bye`，而 registry 没写任何“按插件清理”的代码。实验 4 演示了两种破坏追踪的写法：

- **在构造函数里存下 ctx，方法里用它注册**：存下的是提供方自己的 ctx，条目归属提供方，调用方卸载后还在（`after caller disposed, bad.entries = [ 'x' ]`）
- **把方法解构出来调用**：`const { run } = ctx.shell; run()` 直接 `TypeError`。`createShadowMethod` 只在 `thisArg === receiver` 时替换 `this`，解构后 `this` 是 `undefined`

规则很简单：服务方法里始终用 `this.ctx`，调用方始终写 `ctx.svc.method()`。自己写服务时，不继承 `Service` 也能获得追踪，只要在构造函数里加一行 `Object.defineProperty(this, Service.tracker, { value: { property: 'ctx' } })`，dsh 的 `ConversationDefinitionRegistry` 和后文的示例插件都这样写。

### 隔离开第二个槽，可选依赖用 get 加 inject

需要两个同名实现并存时用 `ctx.isolate(name, label?)`：它只是在子 ctx 的隔离表里给 `name` 换一个新 symbol，这棵子树的 provide、读取和 notify 都落到另一个槽。**隔离后的子树不会回退到父作用域的实现**，没有提供方时静默 PENDING。cordis.yml 里 `isolate: { shell: true }` 是条目私有，`isolate: { shell: 'label' }` 是同 label 共享。

可选依赖有两种写法。`ctx.get(name)` 是即时探测，只返回 ACTIVE 提供方的值，不订阅变化，返回值不能长期持有。`ctx.inject(deps, cb)` 等价于 `ctx.plugin({ inject, apply: cb })`，只让这一部分随依赖加载和卸载。dsh 的标准组合是“`ctx.get` 定初值，`ctx.inject` 订阅变化”，`tool-bash` 是范例：没有 `jobs` 时先注册前台版 bash，`jobs` 出现时换成后台版，`jobs` 消失时在清理函数里换回前台版。

```ts title="packages/shell/tool-bash/src/index.ts:588-602"
  if (!backgroundEnabled) {
    ctx.tools.register(bashTool(undefined))
    return
  }
  let foregroundOnly = ctx.get('jobs') === undefined ? ctx.tools.register(bashTool(undefined)) : undefined
  ctx.inject(['jobs'], (jobCtx) => {
    foregroundOnly?.()
    foregroundOnly = undefined
    const unregister = ctx.tools.register(bashTool(jobCtx.jobs))
    jobCtx.effect(() => () => {
      unregister()
      if (ctx.fiber.state === FiberState.ACTIVE) foregroundOnly = ctx.tools.register(bashTool(undefined))
    })
  })
}
```

> 依赖会连锁：`ToolsService.inject = ['systemPrompt']`，所以 systemPrompt 重建时 tools 和所有工具插件依次重跑。这也是“注册写在 `apply` 里、不写在模块顶层”的根本原因。

## 五种分发模式里，只有 parallel 让所有监听器跑完

Cordis 4.2.0-alpha.1 恰好有五种分发模式：`emit | parallel | serial | bail | waterfall`。[插件指南](dsh_plugin_guide.html)原先写成“四种”，漏了 `parallel`，已经更正。教程的表格只讲了语义，没讲**有监听器出错时会怎样**，而这恰恰决定了你该选哪一种。实验 04 在每种模式下让中间一个监听器 throw 或 reject：

| 模式 | 是否等待 | 返回值 | 中间监听器同步 throw | 中间监听器 async reject |
|---|---|---|---|---|
| `emit` | 同步循环，不 await | 永远 `undefined` | **抛给发出方，后面的不运行**（`ran=[L1,L2-throws]`） | 被丢弃，成为进程级 `unhandledRejection` |
| `parallel` | 全部并发，`Promise.allSettled` | `undefined` | 全部运行，最后抛 `AggregateError`（`ran=[L1,L2-throws,L3]`） | 同左 |
| `serial` | 依次 await | 第一个 bail 值 | reject，后面的不运行 | 同左 |
| `bail` | 同步依次调用，不 await | 第一个 bail 值 | 抛出，后面的不运行 | 不处理 |
| `waterfall` | 由监听器的 `next()` 决定 | 最外层的返回值 | 沿调用链抛出 | 沿 promise 链 reject |

源码很短，差别一眼可见：`parallel` 把每个回调包进 `async` 再 `allSettled`，所以一个出错不影响其他；`emit` 就是一个裸 `for` 循环。`serial` 和 `bail` 用 `isBailed` 判断是否停下，它只把 `null`、`false`、`undefined` 当作“继续”，**`0` 和空字符串都会短路**。`bail` 不 await，第一个 async 监听器返回的 Promise 本身就是 bail 值（实验 02：`bail -> Promise { 'A' }`）。

harness 自有事件的分布是：emit 66、waterfall 22、parallel 9、serial 2、bail 1。观察通知用 `emit`；允许别人改写或否决的决策用 `waterfall`（`tools/*-execute`、`agent/pre-step`、`approval/request`）；需要等全部完成的扇出用 `parallel`（`session/flush`、`workspace/session-stop`）。

```ts title="vendor/cordis/src/events.ts:193-238"
  async parallel(...args: any[]) {
    const [thisArg, callbacks, name] = this._resolve('parallel', args)
    const results = await Promise.allSettled(callbacks.map(async callback => Reflect.apply(callback, thisArg, args)))
    const errors = results.filter((result): result is PromiseRejectedResult => result.status === 'rejected')
    if (!errors.length) return
    const label = typeof name === 'symbol' ? String(name) : `"${name}"`
    throw new AggregateError(errors.map(error => error.reason), `one or more listeners of event ${label} failed`)
  }

  /**
   * Run listeners synchronously without waiting for returned promises.
   *
   * @param args — optional `this`, the event name, then listener arguments.
   */
  emit(...args: any[]) {
    const [thisArg, callbacks] = this._resolve('emit', args)
    for (const callback of callbacks) Reflect.apply(callback, thisArg, args)
  }

  /**
   * Run listeners in order, awaiting each, until one returns a bail value.
   *
   * @param args — optional `this`, the event name, then listener arguments.
   * @returns the first bail value (see {@link isBailed}), if any.
   */
  async serial(...args: any[]) {
    const [thisArg, callbacks] = this._resolve('serial', args)
    for (const callback of callbacks) {
      const result = await Reflect.apply(callback, thisArg, args)
      if (isBailed(result)) return result
    }
  }

  /**
   * Run listeners synchronously until one returns a bail value.
   *
   * @param args — optional `this`, the event name, then listener arguments.
   * @returns the first bail value (see {@link isBailed}), if any.
   */
  bail(...args: any[]) {
    const [thisArg, callbacks] = this._resolve('bail', args)
    for (const callback of callbacks) {
      const result = Reflect.apply(callback, thisArg, args)
      if (isBailed(result)) return result
    }
  }
```

### dsh 不用裸 emit 发通知

正因为 `emit` 不隔离，仓库的防御性规范要求“在分发器中隔离回调异常”。vendored Cordis 为此保留了 `events.dispatch(type, args)`：它只返回筛选、绑定好的回调列表，由发出方自己逐个调用，源码注释写明这是为了“per-listener error containment”。

dsh 的通知类发出方都是同一个写法：`tools/result`、agent 通知、session 观察者、`web/providers-changed`，以及 Cordis 自己的 `internal/plugin`。右边是 `tools/result` 的版本：同步 throw 用 try/catch 接住，async reject 用 `.catch` 接住，都只记一条 warn。

`dispatch` 会用 `args.shift()` **原地**拿掉数组开头的 thisArg 和事件名，dsh 正是利用这一点用同一个数组调用回调。你自己写插件发事件时，照抄这个模式即可，后文示例插件就是这么做的。

```ts title="packages/core/tools/src/pipeline.ts:419-439"
  /** Notify observers without exposing a mutation or error channel into the outcome. */
  private notifyResult(exec: ToolExecution, result: ToolExecutionResult): void {
    // Freeze the registry's live object before observers receive its readonly
    // WeakMap-keyable view.
    Object.freeze(exec)
    const { name: toolName, callId } = exec
    const reportFailure = (error: unknown): void => {
      this.ctx.logger.warn(`tool "${toolName}" (${callId}): tools/result observer failed: ${toolErrorMessage(error)}`)
    }
    const callbacks = this.ctx.events.dispatch('emit', [
      scopeTarget(this.tools, exec.agent), 'tools/result', exec, result,
    ])
    for (const callback of callbacks) {
      try {
        const returned: unknown = callback(exec, result)
        void Promise.resolve(returned).catch(reportFailure)
      } catch (error: unknown) {
        reportFailure(error)
      }
    }
  }
```

> 不是观察类的事件则“捕获后关闭失败”：`approval/request` 先进入 promise 链，把同步 throw 也变成 rejection，再归一成 `'unavailable'`；工具 waterfall 里的异常由 pipeline 的 try/catch 转成工具错误结果。

## waterfall：不调 next() 是否决，调了不 return 是 bug

`waterfall` 把最后一个参数当作最内层的默认行为，监听器依次收到 `(...args, next)`，从外到内包成洋葱。实现只有十几行，规则全在里面：

- **不调 `next()` 就是否决**，内层默认行为不运行
- **同一个 `next` 调两次会同步抛错**，所以重试要写在 `tools/execute` 包装器里处理 `next` 的结果，而不是多次调用 `next`
- **调了 `next()` 却忘了 `return`，调用方拿到 `undefined`**。只做观察的监听器要写成 `const r = await next(); …; return r`

```ts title="vendor/cordis/src/events.ts:250-265"
  waterfall(...args: any[]) {
    const [thisArg, callbacks] = this._resolve('waterfall', args)
    const inner = args.pop()
    const dispatch = () => {
      const callback = callbacks.shift()
      if (!callback) return inner()
      let called = false
      const next = () => {
        if (called) throw new Error('next() called multiple times')
        called = true
        return dispatch()
      }
      return Reflect.apply(callback, thisArg, [...args, next])
    }
    return dispatch()
  }
```

实验 03 把洋葱结构打了出来。`P` 是用 `prepend` 注册的，所以排在最外层；L2 否决时 INNER 不运行，但 L1 仍在返回路径上把否决结果转成大写。最后一行就是“忘了 return”：在 `tools/pre-execute` 里，这会让后续读 `gate.kind` 时抛 TypeError，被 pipeline 转成一条工具错误。

Cordis 自己也用 waterfall：`internal/update` 以 `restart` 作为 inner。插件可以监听它，对“只改日志级别”这类变更不调 `next()`，实现就地更新而不重启（实验 06：改 level 时 `applies = 1`，改 port 才 `apply #2`）。

```js title="实验 03：waterfall 的顺序、变换、否决、next() 两次、忘记 return"
// waterfall: order, transform, veto, next() twice, no listeners
const { Context } = await import(process.argv[2])
const root = new Context()
console.log('no listeners ->', await root.waterfall('w/t', 'hi', async () => 'inner:hi'))
await root.plugin({
  name: 'wf',
  apply(ctx) {
    ctx.on('w/t', async (input, next) => { console.log('  L1 before'); const r = await next(); console.log('  L1 after, got', r); return r.toUpperCase() })
    ctx.on('w/t', async (input, next) => { console.log('  L2 before'); if (input.includes('blocked')) return 'vetoed-by-L2'; const r = await next(); console.log('  L2 after'); return r + '!' })
    ctx.on('w/t', (input, next) => { console.log('  P (prepend) before'); return next() }, { prepend: true })
  },
})
console.log('normal ->', await root.waterfall('w/t', 'hi', async () => { console.log('  INNER runs'); return 'inner:hi' }))
console.log('veto   ->', await root.waterfall('w/t', 'blocked', async () => { console.log('  INNER runs'); return 'inner' }))
const r2 = new Context()
await r2.plugin({ name: 'twice', apply(ctx) { ctx.on('w/2', (next) => { next(); return next() }) } })
try { r2.waterfall('w/2', () => 'inner') } catch (e) { console.log('next() twice ->', e.message) }
const r3 = new Context()
await r3.plugin({ name: 'forgot-return', apply(ctx) { ctx.on('w/3', async (next) => { await next() /* forgot to return */ }) } })
console.log('listener calls next() but forgets to return ->', await r3.waterfall('w/3', async () => ({ kind: 'allow' })))
```

```text title="实验 03 输出"
no listeners -> inner:hi
  P (prepend) before
  L1 before
  L2 before
  INNER runs
  L2 after
  L1 after, got inner:hi!
normal -> INNER:HI!
  P (prepend) before
  L1 before
  L2 before
  L1 after, got vetoed-by-L2
veto   -> VETOED-BY-L2
next() twice -> next() called multiple times
listener calls next() but forgets to return -> undefined
```

### 顺序、thisArg 和作用域过滤

监听器顺序只由注册时间和 `prepend` 决定，后注册的 prepend 排在最前（实验 01：`Q, P, A, B, O`），没有优先级参数。别的插件随时能插到你前面，所以 user-approval 把 `never` 策略写在服务代码路径里，而不是写成监听器。`ctx.on` 返回的 disposer，文档说“仍注册则返回 `true`”，实测两次都返回 `undefined`，**不要依赖这个返回值**。

所有分发方法都先经过 `_resolve`：第一个参数如果是对象或函数，就被当成 thisArg 拿掉，它上面的 `[Context.filter]` 决定哪些 ctx 的监听器能收到事件。dsh 用 `scopeTarget(base, key)` 构造只带 filter 的 carrier：**没打 scope 标签的监听器收到所有 agent 的事件，带标签的只收到本作用域及其后代的事件**。

还有一个常见误会：`turn/*`、`tool/result`、`compaction/*` 是持久会话事件类型，**不是 Cordis 事件**，`ctx.on('turn/start')` 永远不会触发，要监听 `session/event` 再判断 `event.type`。

### 一次工具调用经过的扩展点

把上面几种模式放回 harness 里看，一次工具调用依次经过五个扩展点。cookbook 对分工写得很明确：可重排的策略写在 `pre-execute`，单调的最终拒绝用 `ctx.tools.guard()`，包裹执行用 `execute`，变换用 `post-execute`，只读观察用 `result`。

```svg title="图 2：一次工具调用依次经过的扩展点（参数校验在第三格里面）" tone="light"
<svg viewBox="0 0 680 230" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="工具调用流水线">
<defs><marker id="a2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#504e49"/></marker></defs>
<g font-size="11" fill="#504e49" text-anchor="middle">
<rect x="10" y="40" width="118" height="92" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="69" y="62" font-size="12" font-weight="600" fill="#141413">tools/pre-execute</text>
<text x="69" y="80">waterfall</text><text x="69" y="98">allow / deny /</text><text x="69" y="114">cancel / ask</text>
<rect x="148" y="40" width="108" height="92" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="202" y="62" font-size="12" font-weight="600" fill="#141413">tools.guard()</text>
<text x="202" y="80">不是事件</text><text x="202" y="98">allow 之后才跑</text><text x="202" y="114">单调拒绝</text>
<rect x="276" y="40" width="128" height="92" rx="8" fill="#1B365D" stroke="#1B365D" stroke-width="1.2"/>
<text x="340" y="62" font-size="12" font-weight="600" fill="#fff">tools/execute</text>
<text x="340" y="80" fill="#dfe7f1">waterfall，inner =</text><text x="340" y="98" fill="#dfe7f1">工具主体</text><text x="340" y="116" fill="#fff" font-weight="600">参数校验在这里</text>
<rect x="424" y="40" width="118" height="92" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="483" y="62" font-size="12" font-weight="600" fill="#141413">tools/post-execute</text>
<text x="483" y="80">waterfall</text><text x="483" y="98">accept / 替换 /</text><text x="483" y="114">block</text>
<rect x="562" y="40" width="108" height="92" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/>
<text x="616" y="62" font-size="12" font-weight="600" fill="#141413">tools/result</text>
<text x="616" y="80">emit，逐个 try</text><text x="616" y="98">只读、已冻结</text><text x="616" y="114">不被 await</text>
</g>
<line x1="129" y1="86" x2="146" y2="86" stroke="#504e49" stroke-width="1.4" marker-end="url(#a2)"/>
<line x1="257" y1="86" x2="274" y2="86" stroke="#504e49" stroke-width="1.4" marker-end="url(#a2)"/>
<line x1="405" y1="86" x2="422" y2="86" stroke="#504e49" stroke-width="1.4" marker-end="url(#a2)"/>
<line x1="543" y1="86" x2="560" y2="86" stroke="#504e49" stroke-width="1.4" marker-end="url(#a2)"/>
<text x="340" y="24" text-anchor="middle" font-size="11" font-weight="600" fill="#141413">四个按 agent 过滤的事件都以 scopeTarget(tools, exec.agent) 作 thisArg</text>
<path d="M 69 134 L 69 172 L 340 172 L 340 134" fill="none" stroke="#9a3412" stroke-width="1.3" stroke-dasharray="5 4"/>
<text x="204" y="192" text-anchor="middle" font-size="11" fill="#141413">门禁和 guard 看到的是未校验的参数：</text>
<text x="204" y="208" text-anchor="middle" font-size="11" fill="#504e49">{"name":42} 照样走过 pre-execute，JSON 解析失败时甚至是原始字符串</text>
<text x="616" y="160" text-anchor="middle" font-size="11" fill="#504e49">在 execute() 的</text>
<text x="616" y="176" text-anchor="middle" font-size="11" fill="#504e49">promise 兑现前发出</text>
</svg>
```

| 阶段 | 事件（模式） | inner 默认值 | 监听器能做什么 |
|---|---|---|---|
| 门禁 | `tools/pre-execute`（waterfall） | `{ kind: 'allow' }` | allow / deny / cancel / ask；ask 交给 `ctx.get('approval')`，没有审批服务时降级为 deny |
| 守卫 | `tools.guard()`（不是事件） | — | 只在 allow 后执行，单调拒绝 |
| 执行 | `tools/execute`（waterfall） | 工具主体 | 超时、重试、计量；只允许改 `exec.signal` |
| 变换 | `tools/post-execute`（waterfall） | `{ kind: 'accept' }` | 替换 content **或** value（不能都换）、`block`、附加 `additionalContexts` |
| 观察 | `tools/result`（emit，逐个隔离） | — | 只读，exec 和 result 已冻结；不会被 await |
| 变更 | `tools/change`（emit，不过滤） | — | 注册表增删通知 |

## 配置在激活时才校验，volatile 要五个条件同时成立

插件的配置 schema 从哪里来？Cordis 在 `registry.plugin()` 第一次遇到某个插件时，从**插件对象**上读 `Config`。Loader 导入模块后先取 `exports.default ?? exports`。两者叠在一起就是第一个坑：**`export default function apply` 搭配具名 `export const Config`，Config 会被静默丢掉**，插件收到 `config = undefined`，必填字段也不校验（实验 E1）。正确写法是不写 default、只具名导出 `Config` 和 `apply`，或者 default 导出一个带 `static Config` 的类。

校验的时机是**激活时**：`_runner.execute` 先 `resolveConfig`，再调插件。所以 PENDING 插件的配置错误要等依赖到齐才暴露。`Config` 必须是 Standard Schema，不能异步校验。

```ts title="vendor/cordis/src/registry.ts:322-330"
    let runtime = this._internal.get(callback)
    if (!runtime) {
      let name = plugin.name
      if (name === 'apply') name = undefined
      runtime = { name, callback, fibers: new DisposableList(), Config: plugin.Config }
      this._internal.set(callback, runtime)
    }

    const fiber = new Fiber(this.ctx, config, Inject.resolve(plugin.inject), runtime, getOuterStack)
```
```ts title="vendor/cordis/src/fiber.ts:272-291"
      this._runner = {
        epoch: INACTIVE,
        getOuterStack,
        execute: function (this: Fiber) {
          const config = this.resolveConfig(this.config)
          this._effectiveConfig = config
          if (isConstructor(runtime.callback)) {
            // eslint-disable-next-line new-cap
            const instance = new runtime.callback(this.ctx, config)
            for (const hook of instance?.[symbols.initHooks] ?? []) {
              hook()
            }
            return instance?.[symbols.init]?.()
          } else {
            return runtime.callback(this.ctx, config)
          }
        },
        collect,
      }
```

Loader 在 `internal/config` waterfall 里登记了两个监听器，实际执行顺序是 `!!js` 插值、校验、把错误位置改写成 cordis.yml 的行列号。所以校验失败时，报错能直接指到 yml 的第几行第几列。

出错条目进入 FAILED，进程不退出，其他条目照常运行。教程里“打印错误后以状态码 1 退出”是教程启动器自己的行为。**FAILED 不会自愈**，修好依赖不够，只有 `fiber.update()` 能清掉错误。对 ACTIVE 的 Fiber 调 `fiber.update(bad)` 时校验是同步的，会当场抛错。

```text title="实验 E1 输出：非法配置的报错指到 cordis.yml 行列"
entry 2928a2d0:bad: state=FAILED
  ValidationError: invalid config:
  - $.targets expected array but got not-an-array (at targets, <lab-config>/scripts/e1/cordis.yml:4:14)
  stack:
  - $.targets expected array but got not-an-array (at targets, <lab-config>/scripts/e1/cordis.yml:4:14)
    at bad (<lab-config>/scripts/e1/cordis.yml:1:3)
```

### volatile：五个条件全部满足才免重启

`Entry.update()` 只有在五个条件**同时**满足时才走 volatile 路径：变化的键只有 `config`；Fiber 是 ACTIVE；context 原型没变；`equalExceptVolatile(旧, 新, schema)` 为真；`_commitVolatile()` 完整解析候选值后，普通字段的有效值仍然深相等。满足时，新值写进运行中的 volatile 引用，只向这一个 Fiber 发一次 `loader/volatile-update`；否则走 partial-dispose 加 `fiber.update(config, true)`，也就是重启。

实验 E2 的前两步是对照：step 1 只改 `level`（volatile），没有 dispose 也没有 apply，只收到一次 volatile-update；step 2 同时改了 `level` 和普通字段 `mode`，插件先 dispose 再 apply，没有 volatile 事件。

```ts title="vendor/loader/src/config/entry.ts:155-171"
    // step 3: check if options are changed
    if (this.fiber?.uid) {
      const changes = Object.keys({ ...this.options, ...legacy })
        .filter(key => !deepEqual(this.options[key], legacy[key], key === 'config'))
      // Only an active fiber in an unchanged context takes volatile-only config changes without a remount.
      const volatileOnly = changes.length === 1 && changes[0] === 'config'
        && this.fiber.state === FiberState.ACTIVE && Object.getPrototypeOf(this.ctx) === this.parent.ctx
        && equalExceptVolatile(legacy.config, this.options.config, this.fiber.runtime?.Config)
      if (volatileOnly) this.fiber.rawConfig = this.options.config
      const pending = volatileOnly && this._commitVolatile() ? [] : changes
      if (!pending.length && !force) return
      this.context.emit('loader/partial-dispose', this, legacy, true)
      await this._patchContext(pending)
    } else {
      await this.init()
    }
  }
```
```text title="实验 E2 输出：volatile 原地更新、普通字段重启、匿名行 id、!!js 的 disabled"
  [keep] apply mode=fast level=1
  [anon] apply mode=fast level=1
ids after step 0: keep(keep) d105eab2(anon) gated(gated)
--- step 1
  [keep] volatile-update paths=[["level"]] level=5
ids after step 1: keep(keep) d105eab2(anon) gated(gated)
--- step 2
  [keep] dispose
  [anon] dispose
  [keep] apply mode=slow level=6
  [anon] apply mode=slow level=1
ids after step 2: keep(keep) gated(gated) b06fcc51(anon)
--- step 3
  (GATE=on, file untouched; calling include.refresh())
ids after step 3: keep(keep) gated(gated) b06fcc51(anon)
--- step 4
  [keep] dispose
  [gated] apply mode=y level=1
ids after step 4: keep(keep) gated(gated) b06fcc51(anon)
--- file on disk now:
- id: keep
  name: ./probe.mjs
  disabled: true
  config: { label: keep, mode: slow }
- name: ./probe.mjs
  config: { label: anon, mode: slow }
- id: gated
  name: ./probe.mjs
  disabled: !!js process.env.GATE !== 'on'
  config: { label: gated, mode: y }
```

再补三点。给 volatile 字段写入非法值只得到一条 warn，Fiber 仍是 ACTIVE、引用仍是旧值，**但这次编辑已经写回磁盘**，下次激活时插件会 FAILED（实验 E3）。`loader/volatile-update` 是不等待的 emit，监听器的异常会被吞掉。volatile 值要每次 `.get()` 现读，不能缓存。

### !!js：只对 config 和 disabled 求值

`!!js` 是 Include 定义的 YAML 标签，用 `new Function` 加 `with (ctx)` 求值，拥有完整的进程权限，所以 **patch 文件要当代码对待**，用 `--patch` 加载不可信文件等于执行不可信代码。它只在两处生效：`config` 在激活时用插件自己的 ctx 求值，可以用它 inject 的服务；`disabled` 在每次树协调时用 Loader 一侧的 ctx 求值。

`disabled: !!js` **不是响应式的**。上面 E2 的 step 3 只改了环境变量、没动文件，`include.refresh()` 发现文件内容没变就直接返回，`gated` 没有挂载；step 4 改了文件之后才挂上。base bundle 正是用 `disabled: !!js "!ctx.get('profileContext')"` 控制 plugin-manager 和 hmr 两行。

## cordis.yml 按 id 协调，patch 整块替换 config

Loader 本身是一棵只存在于内存的条目树。`cordis:include` 条目读 YAML、应用 patches，再交给 `EntryGroup.update()` 按 id 协调：新列表里的 id 一律 create，由 `Entry.update` 决定不做事、提交 volatile 还是重启；只在旧列表里的 id 被 remove。profile 模式下，dsh 把各层拼成一份扁平的 patch 列表交给根 Include。

```svg title="图 3：profile 模式下各层 patch 被拍平成一张列表，交给根 Include 按 id 协调" tone="light"
<svg viewBox="0 0 680 300" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="配置分层">
<defs><marker id="a3" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#504e49"/></marker></defs>
<g font-size="11" fill="#504e49">
<rect x="14" y="20" width="250" height="36" rx="6" fill="#EEF2F7" stroke="#1B365D"/><text x="26" y="43"><tspan font-weight="600" fill="#141413">1 bundles</tspan>  按 dsh.profile.bundles 顺序</text>
<rect x="14" y="64" width="250" height="36" rx="6" fill="#EEF2F7" stroke="#1B365D"/><text x="26" y="87"><tspan font-weight="600" fill="#141413">2 profile</tspan>  目录里的 cordis.patch.yml</text>
<rect x="14" y="108" width="250" height="36" rx="6" fill="#EEF2F7" stroke="#1B365D"/><text x="26" y="131"><tspan font-weight="600" fill="#141413">3 home</tspan>  $DSH_HOME/cordis.patch.yml</text>
<rect x="14" y="152" width="250" height="36" rx="6" fill="#EEF2F7" stroke="#1B365D"/><text x="26" y="175"><tspan font-weight="600" fill="#141413">4 --patch</tspan>  按 argv 顺序</text>
<rect x="14" y="196" width="250" height="36" rx="6" fill="#fff" stroke="#1B365D" stroke-dasharray="5 4"/><text x="26" y="219"><tspan font-weight="600" fill="#141413">5 派生</tspan>  可能追加的禁用遥测 patch</text>
</g>
<line x1="265" y1="126" x2="318" y2="126" stroke="#504e49" stroke-width="1.4" marker-end="url(#a3)"/>
<text x="292" y="116" text-anchor="middle" font-size="11" fill="#504e49">拍平</text>
<rect x="320" y="92" width="150" height="68" rx="8" fill="#1B365D"/>
<text x="395" y="116" text-anchor="middle" font-size="12" font-weight="600" fill="#fff">根 Include</text>
<text x="395" y="134" text-anchor="middle" font-size="11" fill="#dfe7f1">id: include</text>
<text x="395" y="150" text-anchor="middle" font-size="11" fill="#dfe7f1">config.patches = 列表</text>
<line x1="471" y1="126" x2="508" y2="126" stroke="#504e49" stroke-width="1.4" marker-end="url(#a3)"/>
<rect x="510" y="92" width="156" height="68" rx="8" fill="#EEF2F7" stroke="#1B365D"/>
<text x="588" y="116" text-anchor="middle" font-size="12" font-weight="600" fill="#141413">EntryGroup.update</text>
<text x="588" y="134" text-anchor="middle" font-size="11" fill="#504e49">按 id：新 → create</text>
<text x="588" y="150" text-anchor="middle" font-size="11" fill="#504e49">只在旧列表 → remove</text>
<rect x="320" y="196" width="346" height="84" rx="8" fill="#fbe9e2" stroke="#9a3412"/>
<text x="334" y="218" font-size="12" font-weight="600" fill="#141413">按 id 覆盖是逐键整值替换，不做深合并</text>
<text x="334" y="238" font-size="11" fill="#504e49">bundle：  - id: x  config: { a: 1, b: 2 }</text>
<text x="334" y="254" font-size="11" fill="#504e49">用户层：  - id: x  config: { b: 3 }</text>
<text x="334" y="270" font-size="11" fill="#141413" font-weight="600">结果：    config: { b: 3 }，a 没了（实验 E5）</text>
</svg>
```

层的顺序是：各 bundle、profile 目录的 `cordis.patch.yml`、`$DSH_HOME/cordis.patch.yml`、命令行 `--patch`，最后可能追加一条禁用遥测的 patch。`--dump-config` 和真正挂载走的是同一个 `applyEntryPatches`，所以用它核对最终组合是可靠的。

合并规则是**逐键整值替换**：右边第 167–170 行对 overrides 的每个键直接 `target[key] = value`。仓库多处明文写着“does not deep-merge”。对插件作者的结论只有一条：**默认值写在 schema 的 `.default()` 里**，写在 bundle patch 里的“默认值”会被用户层整块覆盖。

```ts title="packages/boot/app-boot/src/profile-context.ts:75-86"
export function readProfilePatches(binName: string, context: ProfileContext, initialProfile?: Profile): PatchOptions[] {
  const patches = structuredClone([
    ...readBundlePatches(binName, context, initialProfile),
    ...(initialProfile?.patches ?? loadOptionalPatches(binName, context.patchPath) ?? []),
    ...(loadOptionalPatches(binName, join(context.home, PROFILE_PATCH_FILENAME)) ?? []),
    ...context.overlayPaths.flatMap(path => loadOverlayPatches(binName, path)),
  ])
  const telemetryPatch = resolveTelemetryPatch(context.telemetryDisabledEnv,
    composeEntries([patches]).some(row => row.id === TELEMETRY_ROW_ID))
  if (telemetryPatch !== undefined) patches.push(telemetryPatch)
  return patches
}
```
```ts title="vendor/include/src/patch.ts:160-172"
    // the entries of a group config the patch replaces go away with it
    if ('config' in overrides && target.group && Array.isArray(target.config)) {
      unmount(target.config)
    }

    const overridden = keys.get(id) ?? new Map<string, number>()
    keys.set(id, overridden)
    for (const [key, value] of Object.entries(overrides)) {
      target[key] = value
      overridden.set(key, index)
    }

    // entries of a group config the patch supplies can be patched in turn
```

实验 E5 还确认了三条边角规则：`name` 与目标不一致的覆盖整体跳过，只留一条 warn；目标不存在时也只 warn；后面的层用 `insert` 写同一个 id，运行时**整行替换**，连模块都换掉（E6 里加载的是后插入的 `p2.mjs`）。

```text title="实验 E5 输出：x 的 config 从 {a:1,b:2} 变成 {b:3}" full
  warn: patch: name mismatch for "x" (expected "pkg-x", got "other-name"), skipping
  warn: patch: entry "missing" not found
[
 {
  "id": "x",
  "name": "pkg-x",
  "config": {
   "b": 3
  },
  "disabled": {
   "__jsExpr": "process.platform === 'win32'"
  }
 },
 {
  "id": "grp",
  "name": "cordis:group",
  "group": true,
  "config": [
   {
    "id": "child",
    "name": "pkg-child",
    "config": {
     "deep": 2
    }
   }
  ]
 },
 {
  "id": "x",
  "name": "pkg-x2"
 }
]
```

### 匿名行、条目 inject 和自释放

**教程 06 关于匿名行的说法在这个 commit 上已经过时。** 教程说没有 id 的行“每次读取都会获得新 id，只要配置文件有任何编辑就会被删除重建”。但 Include 的 `_assignIds` 会让与上次读取的某个匿名行深相等的行沿用旧 id。上面 E2 的输出里，匿名行在 step 1 编辑相邻行时保持 `d105eab2`；step 2 改了它自己的 `mode`，才换成新 id `b06fcc51` 并重建。结论不变：**每一行都写显式 `id`**，否则一改自身配置就删除重建，失去 volatile 的机会，patch 也无法定位它。

另外两个不起眼的行为：

- 条目上写的 `inject` 会**叠加**到插件自己的 inject 上，yml 里一个笔误就能让插件永久 PENDING，没有任何提示
- 插件自己调 `ctx.fiber.dispose()` 时，Loader 会把 `disabled: true` **写回用户文件**（排除 8 种非自释放情形后）。需要“暂时停一下”时不要用自释放

### HMR：以插件为单位替换，失败就回滚

dsh 挂载的是自己的 `@deepseek-ai/dsh-hmr`。它要求 Node 内部 ESM loader 可用（`--expose-internals` 或 native addon），而且 **base bundle 默认 `root: []`，也就是只热重载 profile 配置**，模块热替换要主动开启。没挂载 `timer` 时，HMR 静默停在 PENDING。

文件变化先经 `ctx.debounce` 合并，然后分四类：进程入口静态依赖图里的文件调用 `loader.exit()` 请求整进程重启；ESM 缓存里已有的模块进入部分重载；某个 Include 的配置文件调用 `include.refresh()`；其余文件只 emit `hmr/change`。注意最后一行：**主 watcher 只监听 `change`，新建文件不触发任何动作**。

部分重载沿 ESM 依赖图向上找出受影响的 Loader 条目，逐出缓存并保留备份，重新 import，在原父 ctx 上用原配置重新挂载；任何一步失败都回滚，把旧插件挂回去。

```ts title="packages/boot/hmr/src/index.ts:436-489"
    this.watcher = watch(root, {
      ...this.config,
      cwd: watchBaseDir,
      ignored: path => match(relative(watchBaseDir, path)),
      ignoreInitial: true,
    })

    const changed = new Set<string>()
    const dispatch = this.ctx.debounce(() => {
      void this.runExclusive(async () => {
        if (!await this.applicationReady) return
        const batch = [...changed]
        changed.clear()
        const includes = new Set<Include>()
        let fullReload = false
        for (const path of batch) {
          const filename = canonicalPath(resolve(watchBaseDir, path))
          const configuredFilename = resolve(this.baseDir, path)
          if (this.configPaths.has(filename) || this.configPaths.has(configuredFilename)) continue
          const isManifest = basename(filename) === 'package.json' && !filename.includes(`${sep}node_modules${sep}`)
          if (isManifest) {
            this.manifests.invalidate(filename)
          }
          const url = pathToFileURL(filename).href
          if (this.externals.has(url)) {
            fullReload = true
            continue
          }
          if (this.esmHas(url) || this.esmHas(url, 'json')) {
            this.stashed.add(url)
            continue
          }
          if (isManifest) continue
          const include = [...loader.entries()].map(entry => entry.subtree as Include | undefined)
            .find(tree => tree?.filename === filename || tree?.filename === configuredFilename)
          if (include !== undefined) includes.add(include)
          else this.ctx.emit('hmr/change', url)
        }
        if (!fullReload && includes.size === 0 && this.stashed.size === 0) return
        if (fullReload) {
          loader.exit()
          return
        }
        for (const include of includes) {
          // A rejected refresh reports unsaved runtime changes; Include keeps its running tree.
          try { await include.refresh() } catch (error) { this.ctx.logger.warn(error) }
        }
        if (this.stashed.size > 0) {
          try { await this.partialReload() } finally { this.stashed.clear() }
        }
        await loader.await()
      }).catch((error: unknown) => { this.ctx.logger.warn(error) })
    }, this.config.debounce)
    this.watcher.on('change', (path) => { changed.add(path); dispatch() })
```

实验 E4 覆盖了主要行为：A 改了 `hello` 的依赖 `dep.mjs`，只有 `hello` 重载，`other` 不受影响；B 三次快速保存被去抖合并成一次；D 改 cordis.yml 只重挂这一条；E 语法错误时旧实例保持 ACTIVE，G 修好后才替换；F 动了进程入口，请求整进程重启。

> 模块顶层的副作用（定时器、连接、全局注册）在模块被逐出时**不会被清理**，热重载一次就多一份。所有副作用都要放进 `apply` 或 `ctx.effect`，上一篇的“模块级状态”一节讲过同一件事。

```text title="实验 E4 输出：依赖重载、去抖、配置编辑、语法错误回滚、入口变更"
  [other] apply
  [hello] apply msg=v1 config={"n":1}
  <info hmr> watching %o [".","../run.mjs"]
--- A: edit dep.mjs (dependency of hello only)
  [hello] dispose msg=v1
  [hello] apply msg=v2 config={"n":1}
  <info hmr> reload plugin at %C hello.mjs
  hmr/reload plugins=hello
--- B: three quick saves of dep.mjs 40ms apart (debounce 200ms)
  [hello] dispose msg=v2
  [hello] apply msg=v3c config={"n":1}
  <info hmr> reload plugin at %C hello.mjs
  hmr/reload plugins=hello
--- C: edit unused.txt (not a loaded module)
  hmr/change unused.txt
--- D: edit cordis.yml (hello config n: 1 -> 2)
  [hello] dispose msg=v3c
  [hello] apply msg=v3c config={"n":2}
--- E: syntax error in hello.mjs
  <warn hmr> Unexpected end of input
  <warn hmr> Unexpected end of input
  hello fiber state after failed reload = ACTIVE
--- G: fix hello.mjs
  [hello] dispose msg=v3c
  [hello] apply (fixed) msg=v3c
  <info hmr> reload plugin at %C hello.mjs
  hmr/reload plugins=hello
--- F: touch run.mjs (process entry point = HMR externals)
  !! loader.exit() called (full reload requested)
```

## 教程 07 在 4b399c3 上跑不通：缺一行组合，还有一个没写进 inject 的顺序依赖

教程 07 的组合只列了四行，并解释“`dsh-tools` 会注入 `systemPrompt`，所以组合中也要列出该服务的提供方”。但在这个 commit 上，依赖链比教程写的长一截：`greet-tool → tools → systemPrompt → sessionProjections`。`SystemPrompt` 自己 inject 了 `sessionProjections`，而组合里没有提供它的插件。

```yaml title="docs/cordis-tutorial/07-into-the-harness.zh.md:79-82"
- name: '@deepseek-ai/dsh-system-prompt'
- name: '@deepseek-ai/dsh-tools'
- name: './tool-logger.ts'
- name: './greet-tool.ts'
```

原样运行的结果是**没有任何输出，exit 0**。没有报错，因为 PENDING 是合法状态。把每个 Fiber 的状态打出来，整条链都卡在缺失的服务上：

```text title="实验 A：教程原样组合的状态转储"
[dump] Loader                               ACTIVE
[dump] isolate                              ACTIVE
[dump] Include                              ACTIVE
[dump] tool-logger                          PENDING  missing=tools
[dump] ToolsService                         PENDING  missing=systemPrompt
[dump] greet-tool                           PENDING  missing=tools
[dump] SystemPrompt                         PENDING  missing=sessionProjections
```

逐步补齐组合，四组实验的结果：

| 实验 | 组合 | 结果 |
|---|---|---|
| A | 教程原样四行 | 无输出，`exit=0`；整条链 PENDING |
| B | + `dsh-session-projection` | greet-tool 激活，但它在 `apply` 里发起的那次 `execute` 抛 `ToolExecutorUnavailableError`，未处理的 rejection 让进程崩溃 |
| C | 再 + `dsh-tool-executor-native`，用 Loader 加载 | **仍然崩溃**，`exit=1` |
| D | 程序化加载，逐个 `await`，executor 先于 greet-tool | 输出与教程一致 |

### 实验 C 为什么还会崩：executor 没有写进任何 inject

greet-tool 和 native executor 都只声明了 `inject: ['tools']`。`tools` 一就绪，两者同时可以激活，没有任何依赖规定 executor 的 `setExecutor()` 要先跑，而 greet-tool 在 `apply` 里立刻就调用了 `execute`。`execute()` 本身只做分派：`selectExecutor(input.agent).execute(input)`，找不到 executor 就抛错。

```ts title="教程 07 的 greet-tool.ts（实验 C 所用，与教程一致）"
import type { Context } from '@deepseek-ai/cordis'
import { brandString } from '@deepseek-ai/dsh-brand'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { ToolCallId } from '@deepseek-ai/dsh-llm'

export const name = 'greet-tool'
export const inject = ['tools']

export function apply(ctx: Context) {
  ctx.tools.register(defineTool({
    name: 'greet',
    description: 'Greet the named person.',
    parameters: {
      name: { type: 'string', required: true, description: 'Who to greet' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args) {
      return `Hello, ${args.name}!`
    },
  }))

  // Drive one call through the real execution pipeline, standing in for
  // the model. ToolCallId brands the correlation id a provider would issue.
  void (async () => {
    const result = await ctx.tools.execute({
      calls: [{
        type: 'tool-call', id: brandString<ToolCallId>('demo-1'),
        name: 'greet', arguments: JSON.stringify({ name: 'Cordis' }),
      }],
      signal: new AbortController().signal,
    })
    console.log('tool replied:', JSON.stringify(result.results[0]?.result.content))
  })()
}
```
```ts title="packages/core/tools/src/index.ts:353-378"
  /**
   * Resolve the nearest scoped provider, or the deployment default.
   * @param scope - Agent or preset scope; omitted selects only the root contribution.
   * @returns the selected executor; throws when it is missing or unloaded.
   */
  private selectExecutor(scope?: ScopeKey): ToolExecutor {
    let record: Registration | undefined
    for (const key of scopeChainOf(scope)) {
      record = this.scoped.get(key)
      if (record !== undefined) break
    }
    record ??= this.root
    const executor = record?.executor
    if (executor === undefined) throw new ToolExecutorUnavailableError()
    return executor
  }

  /**
   * Delegate a raw call batch to its Agent's provider or the deployment default.
   * Select at dispatch; accepted batches settle through that provider even if it unloads.
   * @param input - original calls, optional owning Agent, and optional logging step.
   * @returns the selected executor's settled batch; unavailable selections reject.
   */
  async execute(input: ToolBatchInput): Promise<ToolBatchResult> {
    return this.selectExecutor(input.agent).execute(input)
  }
```
```text title="实验 C 输出：栈指向 greet-tool.ts:28 的 execute"
<lab>/node_modules/@deepseek-ai/dsh-tools/lib/index.js:633
		if (executor === void 0) throw new ToolExecutorUnavailableError();
		                               ^

ToolExecutorUnavailableError: The selected tool executor is unavailable. Load an executor in this composition before running the agent.
    at Proxy.selectExecutor (<lab>/node_modules/@deepseek-ai/dsh-tools/lib/index.js:633:34)
    at Object.apply (<lab>/node_modules/@deepseek-ai/cordis/lib/index.js:124:36)
    at Proxy.execute (<lab>/node_modules/@deepseek-ai/dsh-tools/lib/index.js:643:15)
    at Object.apply (<lab>/node_modules/@deepseek-ai/cordis/lib/index.js:124:36)
    at <lab>/t07c/greet-tool.ts:28:36
    at new apply (<lab>/t07c/greet-tool.ts:36:5)
    at Fiber.execute (<lab>/node_modules/@deepseek-ai/cordis/lib/index.js:385:24)
    at <lab>/node_modules/@deepseek-ai/cordis/lib/index.js:460:34
    at composeError (<lab>/node_modules/@deepseek-ai/cordis/lib/index.js:207:18)
    at Fiber._execute (<lab>/node_modules/@deepseek-ai/cordis/lib/index.js:455:10) {
  code: 'TOOL_EXECUTOR_UNAVAILABLE'
}

Node.js v24.22.2
exit=1
```

### 没有 executor，连 system prompt 都组装不了

更出人意料的是，**没有 executor 时，`systemPrompt.assemble()` 也会失败**。ToolsService 的构造函数向 systemPrompt 注册了一个 schema provider，它要先 `selectExecutor()` 再 `describe()`。所以“装了 tools 但没装 executor”的效果不是“工具看得见、调不了”，而是整个 agent 都组装不了 prompt。

修正办法：组合里补上 `@deepseek-ai/dsh-session-projection` 和 `@deepseek-ai/dsh-tool-executor-native`（base bundle 本来就有这几行）；演示调用不要放在 `apply` 里。目前没有可以 inject 的“executor 就绪”服务。真实场景中调用来自 agent loop，那时 executor 早已就绪，所以这个问题只影响“在 apply 里自调工具”的写法。

```ts title="packages/core/tools/src/index.ts:108-120"
  private root: Registration | undefined
  private readonly guards = new ScopedLayers(() => new GuardLayer(), () => {})

  constructor(ctx: Context) {
    super(ctx, 'tools')
    ctx.systemPrompt.tools(({ scope, toolSet }) => {
      const executor = this.selectExecutor(scope)
      const description = executor.describe(this.definitions(scope, toolSet))
      return { ...description, knownNames: description.knownNames ?? [...this.view(scope).knownNames] }
    })
  }
```
```text title="实验：只装 tools、不装 executor"
schemas(): [ 'noop' ]
assemble -> TOOL_EXECUTOR_UNAVAILABLE The selected tool executor is unavailable. Load an executor in this composition before running the agent.
execute -> TOOL_EXECUTOR_UNAVAILABLE
```

### 参数校验不在流水线前段

实验 D 还确认了一个写门禁时很重要的事实：**参数校验在 `defineTool` 包装出的 `execute` 里**，也就是图 2 的第三格。传入 `{"name":42}` 时，pre-execute 和 post-execute 监听器都执行了，最后才以 `ToolArgsError`（`INVALID_ARGS`）结束；JSON 解析失败的参数甚至会以原始字符串进入流水线。所以 **`tools/pre-execute` 监听器和 guard 不能假定参数已经通过校验**。

另外，`tools/result` 在 `execute` 返回的 promise 兑现**之前**发出，而且不会被 await，所以不能用它保证“审计写完再继续”。

```ts title="packages/core/tools/src/schema.ts:578-603"
  const parameters = parameterSchemaSpecToJsonSchema(options.parameters)
  const outputSchema = valueSchemaSpecToJsonSchema(options.output.schema)
  const validate = (args: unknown): string[] => validateJsonSchemaValue(parameters, args, '')
  const tool: ToolDefinition = {
    name: options.name,
    description: options.description,
    parameters: parameters as unknown as Record<string, unknown>,
    output: {
      schema: outputSchema,
      render(args: unknown, value: JsonValue): ContentBlock[] {
        return userRender(args as InferArgs<S>, value as InferValue<NoInfer<O>>)
      },
      ...userPresentationMeta !== undefined ? {
        presentationMeta(args: unknown, value: JsonValue): JsonValue {
          return userPresentationMeta(args as InferArgs<S>, value as InferValue<NoInfer<O>>)
        },
      } : {},
    },
    ...(options.deferLoading === true ? { deferLoading: options.deferLoading } : {}),
    ...(options.timeoutMs !== undefined ? { timeoutMs: options.timeoutMs } : {}),
    async execute(args: unknown, exec: ToolRunContext): Promise<JsonValue> {
      const violations = validate(args)
      if (violations.length > 0) throw new ToolArgsError(violations)
      return userExecute(args as InferArgs<S>, exec) as Promise<JsonValue>
    },
  }
```

## 自写插件 acme-ledger：服务、事件和卸载顺序放在一起

最后把前面的机制装进一个插件。acme-ledger 是一个审计账本：记录每次工具调用，定期写到文件。它由四个插件组成，跑在真实的 Cordis Loader 和真实的 `dsh-tools / system-prompt / session-projection / tool-executor-native` 包上；没有 LLM，模型的调用由脚本直接调 `root.tools.execute(...)` 代替。

| 插件 | 角色 | 关键写法 |
|---|---|---|
| `acme-ledger` | 提供 `acmeLedger` 服务，持有文件句柄和 flush 定时器 | 一个异步生成器 effect，**最后才 `yield ctx.provide(...)`**；`tag` 字段标为 `.volatile()` |
| `acme-ledger-memory` | 同名服务的内存实现 | 演示更换提供方 |
| `acme-ledger-audit` | 消费方：监听 `tools/result` 记账，注册 `ledger_stats` 工具和一段 system prompt | 顶层 `inject = ['tools','systemPrompt','acmeLedger']`；最后一条记录写在 async disposer 里 |
| `acme-ledger-tail` | 观察方：监听 `acme-ledger/flushed` | 没有顶层 inject，只用 `ctx.inject(['acmeLedger'], …)` 让 `watch` 这一部分等服务 |

### 提供方：调用方署名、逐个隔离的事件、最后发布服务

四个设计选择分别对应前面各节。**调用方归属**：服务契约是一个抽象类，没有继承 `Service`，只在构造函数里装上 tracker，于是 `append()` 能用 `this.ctx.fiber.name` 给记录署名，`watch()` 用 `this.ctx.effect()` 把订阅登记在调用方身上。

**事件隔离**：`emitContained` 照搬 `tools/result` 的 `dispatch` 加逐个 try/catch。**写入串行化**：`flush()` 串在一条 promise 链上，撤服务时如果正有一次定时 flush 在进行，最终 flush 会排在它后面。

**清理顺序**：按上一篇推荐的生成器写法，资源先 `yield`、服务最后 `yield`。逆序回卷时依次是：撤下服务并等消费方卸载完、清除定时器、最终 flush、关闭文件。

```ts title="acme/acme-ledger.ts（实验所用源码）"
/**
 * acme-ledger: file-backed provider of the `acmeLedger` service.
 * Owns a FileHandle and a flush timer; publishes the service LAST inside one
 * generator effect so reverse disposal is: withdraw service (wait for
 * consumers) -> stop timer -> final flush -> close file.
 */
import { open, type FileHandle } from 'node:fs/promises'
import { Service, type Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/cordis-plugin-loader'
import Schema from '@deepseek-ai/schemastery'
import { log } from './log.ts'

export interface LedgerEntry { kind: string; [key: string]: unknown }
export interface FlushInfo { path: string; count: number; reason: string; owners: string[] }
export interface LedgerStats { backend: string; path: string; appended: number; flushed: number; buffered: number; watchers: number }

declare module '@deepseek-ai/cordis' {
  interface Context {
    acmeLedger: AcmeLedger
  }
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
    // Same trick as dsh ui-conversation's ConversationDefinitionRegistry: let Cordis
    // trace `this.ctx` to the CALLER's context when accessed as `ctx.acmeLedger`.
    Object.defineProperty(this, Service.tracker, { value: { property: 'ctx' } })
  }
  protected readonly watchers = new Set<(entry: LedgerEntry & { owner: string }) => void>()

  /** Append one entry stamped with the calling plugin's name. */
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
    // 1. Resource: the file handle. Disposed LAST.
    const handle = await open(config.path, 'a')
    yield async () => { await handle.close(); log(`[acme-ledger] file closed ${config.path}`) }

    const ledger = new FileLedger(ctx, ctx, config, handle)
    // 2. Final flush. Runs after the timer is gone and the service is withdrawn.
    yield async () => { await ledger.flush('final'); log(`[acme-ledger] final flush done (appended=${ledger.appended}, flushed=${ledger.flushed})`) }

    // 3. Timer. Cleared before the final flush.
    const timer = setInterval(() => { void ledger.flush('interval') }, config.flushIntervalMs)
    yield () => { clearInterval(timer); log('[acme-ledger] timer cleared') }

    // 4. Publish LAST: reverse disposal withdraws it first and waits for dependents.
    yield ctx.provide('acmeLedger', ledger)
    log('[acme-ledger] service published')
  }, 'acme-ledger:resources')
}
```

### 消费方和观察方

消费方 `acme-ledger-audit` 的每一项注册都是 effect：`ctx.on`、`ctx.tools.register`、`ctx.systemPrompt.section`，卸载时自动撤销，不用写任何清理代码。唯一手写的 effect 是最后一条记录：它是 async disposer，运行时提供方正停在“等 dependents 清空”那一步，所以服务仍然可用。

section 的 `order` 选了 5100，因为 `getSectionOrder()` 只认内置名字，第三方目前没有官方区间，只能自己挑一个数。

```ts title="acme/acme-ledger-audit.ts"
/**
 * acme-ledger-audit: consumer. Records every tool result into the ledger,
 * contributes a `ledger_stats` tool and a prompt section, and writes a final
 * "closing" entry when it unloads (the write that must not be lost).
 */
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type {} from './acme-ledger.ts'
import { log } from './log.ts'

export const name = 'acme-ledger-audit'
export const inject = ['tools', 'systemPrompt', 'acmeLedger']

export function apply(ctx: Context): void {
  // Captured once: a proxy whose `this.ctx` is THIS plugin's context.
  const ledger = ctx.acmeLedger
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
    order: 5100,
    text: 'Every tool call in this deployment is recorded in an audit ledger; call ledger_stats to inspect it.',
  })

  // The final write. Async disposer: runs while the provider waits in its unprovide step.
  ctx.effect(() => async () => {
    await new Promise(r => setTimeout(r, 10))
    try {
      ledger.append({ kind: 'audit-closing' })
      log('[audit] final entry appended')
    } catch (error) {
      log(`[audit] final entry FAILED: ${(error as Error).message}`)
    }
  }, 'audit:final-entry')
}
```

观察方 `acme-ledger-tail` 演示了两件事：事件监听不需要服务在场，所以它没有顶层 inject；`watch` 需要服务，就用 `ctx.inject` 开一个子 Fiber，只让这一部分随服务加载和卸载。`faulty: true` 时多挂一个会抛错的监听器，用来验证提供方的事件隔离。

内存实现只有一个资源（内存里的记录），同样最后发布服务。

```ts title="acme/acme-ledger-tail.ts"
/**
 * acme-ledger-tail: observer. Hears `acme-ledger/flushed` with NO dependency on
 * the service; only the optional `watch` part waits for `acmeLedger`.
 */
import type { Context } from '@deepseek-ai/cordis'
import Schema from '@deepseek-ai/schemastery'
import type {} from './acme-ledger.ts'
import { log } from './log.ts'

export const name = 'acme-ledger-tail'
export interface Config { faulty: boolean }
export const Config: Schema<Config> = Schema.object({ faulty: Schema.boolean().default(false) })

export function apply(ctx: Context, config: Config): void {
  if (config.faulty) {
    ctx.on('acme-ledger/flushed', () => { throw new Error('tail listener bug') })
  }
  ctx.on('acme-ledger/flushed', (info) => {
    log(`[tail] flushed ${info.count} (${info.reason}) from ${info.owners.join('+')} -> ${info.path.split('/').pop()}`)
  })
  // Partial dependency: only this child fiber waits for / restarts with the provider.
  ctx.inject(['acmeLedger'], (ctx) => {
    ctx.acmeLedger.watch((entry) => {
      if (entry.kind !== 'tool') log(`[tail] watch: ${entry.kind} by ${entry.owner} tag=${String(entry.tag)}`)
    })
  })
}
```
```ts title="acme/acme-ledger-memory.ts"
/** acme-ledger-memory: an alternative provider of the same `acmeLedger` service (no resources). */
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

### 装进组合

等价的 cordis.yml 如下。executor 行要在任何“apply 里自调工具”的插件之前就绪；本地调试一定加上 `cordis-plugin-logger-console`，否则 Loader 的错误只写进内存环形缓冲，你什么也看不到。

实验本身用 `root.loader.create/update/remove` 程序化驱动，走的是真实的 `Entry.update → _commitVolatile` 路径，**没有用这份 YAML 直接运行过**。插件以 `.ts` 形式靠 Node 24 的类型剥离直接运行，没有经过 `tsc` 类型检查。第一次运行时提供方用了 TS 的 parameter property 语法，strip-only 模式不支持，导入失败，唯一的表现是 `ledger=none`，挂上 logger-console 才看到报错。

```yaml title="cordis.yml（等价组合，未直接运行）"
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

### 运行记录：三种卸载路径下最后一条记录都没丢

驱动脚本分 8 步，第一列是相对毫秒：

- **1–3 启动**：先装消费方，audit 停在 PENDING；harness 就绪后仍等服务；提供方一到，audit 立刻激活，它注册的工具和 section 同时出现
- **4 事件**：抛错的 tail 监听器被隔离（`listener failed (contained)`），正常的监听器照常收到
- **5 volatile**：只改 `tag`，`same fiber object: true`，之后的记录 tag 变成 `v2`
- **6 改 path**：提供方重启。`[audit] final entry appended` 在 `timer cleared` 和 `file closed` **之前**
- **7 换实现**：卸文件版、装内存版，file B 里有 `audit-closing`
- **8 整树 dispose**：最后一条照样写进文件，`Timeout` 从活跃资源里消失，没有残留句柄

`ledger_stats` 第一次返回 `2 appended`，第二次 `3 appended`：工具自己那次调用的 `tools/result` 要等它返回之后才入账。

```text title="实验：acme-ledger 8 步运行记录" full
    4 === 1. consumers first, no provider
   74 tail=ACTIVE audit=PENDING
   74 === 2. harness rows (session-projection, system-prompt, tools, native executor, greet)
tool replied: [{"type":"text","text":"Hello, Cordis!"}]
  218 tools=ACTIVE executor=ACTIVE audit=PENDING (audit still waits for acmeLedger)
  218 === 3. provider arrives
  223 [acme-ledger] apply path=/tmp/acme-ledger-FV3oBY/a.jsonl tag=v1
  224 [audit] apply (backend=file)
  224 [tail] watch: audit-start by acme-ledger-audit tag=v1
  225 [acme-ledger] service published
  274 ledger=ACTIVE audit=ACTIVE tail=ACTIVE
  275 tools visible: greet,ledger_stats
  276 prompt sections: harness:identity,deployment:persona-prefix,acme-ledger:audit,deployment:persona-suffix
  276 === 4. tool calls flow into the ledger; flush events reach tail (one tail listener throws)
  276 greet -> Hello, Ledger!
  277 ledger_stats -> file: 2 appended, 0 flushed, 2 buffered
  277 [acme-ledger] listener failed (contained): tail listener bug
  277 [tail] flushed 3 (interval) from acme-ledger-audit -> a.jsonl
  399 ledger_stats -> file: 3 appended, 3 flushed, 0 buffered
  399 === 5. volatile config update (tag only)
  401 [acme-ledger] volatile update [["tag"]] -> tag=v2 (no restart)
  401 same fiber object: true ledger=ACTIVE audit=ACTIVE
  437 [acme-ledger] listener failed (contained): tail listener bug
  437 [tail] flushed 2 (interval) from acme-ledger-audit -> a.jsonl
  503 === 6. ordinary config update (path A -> B): provider restarts, consumer restarts
  517 [audit] final entry appended
  519 [acme-ledger] timer cleared
  520 [acme-ledger] listener failed (contained): tail listener bug
  521 [tail] flushed 1 (final) from acme-ledger-audit -> a.jsonl
  521 [acme-ledger] final flush done (appended=6, flushed=6)
  521 [acme-ledger] file closed /tmp/acme-ledger-FV3oBY/a.jsonl
  522 [acme-ledger] apply path=/tmp/acme-ledger-FV3oBY/b.jsonl tag=v2
  525 [audit] apply (backend=file)
  525 [tail] watch: audit-start by acme-ledger-audit tag=v2
  526 [acme-ledger] service published
  577 [acme-ledger] listener failed (contained): tail listener bug
  577 [tail] flushed 1 (interval) from acme-ledger-audit -> b.jsonl
  608 ledger=ACTIVE audit=ACTIVE tail=ACTIVE
  630 [acme-ledger] listener failed (contained): tail listener bug
  630 [tail] flushed 1 (interval) from acme-ledger-audit -> b.jsonl
  715 file A: audit-start@acme-ledger-audit/v1 | tool:greet@acme-ledger-audit/v1 | tool:ledger_stats@acme-ledger-audit/v1 | tool:ledger_stats@acme-ledger-audit/v1 | tool:greet@acme-ledger-audit/v2 | audit-closing@acme-ledger-audit/v2
  717 file B: audit-start@acme-ledger-audit/v2 | tool:greet@acme-ledger-audit/v2
  717 === 7. provider swap: unload file provider, load memory provider
  729 [audit] final entry appended
  730 [acme-ledger] timer cleared
  730 [acme-ledger] listener failed (contained): tail listener bug
  730 [tail] flushed 1 (final) from acme-ledger-audit -> b.jsonl
  730 [acme-ledger] final flush done (appended=3, flushed=3)
  730 [acme-ledger] file closed /tmp/acme-ledger-FV3oBY/b.jsonl
  731 audit=PENDING after file provider removed
  731 file B: audit-start@acme-ledger-audit/v2 | tool:greet@acme-ledger-audit/v2 | audit-closing@acme-ledger-audit/v2
  735 [acme-ledger-memory] service published
  736 [audit] apply (backend=memory)
  736 [tail] watch: audit-start by acme-ledger-audit tag=mem
  787 memory=ACTIVE audit=ACTIVE
  790 ledger_stats -> memory: 1 appended, 0 flushed, 1 buffered
  801 [audit] final entry appended
  802 [acme-ledger-memory] dropped 3 in-memory entries: audit-start,tool,audit-closing
  803 audit=PENDING
  803 === 8. full dispose (dispose the Loader fiber that owns every entry)
  805 [acme-ledger] apply path=/tmp/acme-ledger-FV3oBY/b.jsonl tag=v3
  806 [audit] apply (backend=file)
  806 [tail] watch: audit-start by acme-ledger-audit tag=v3
  807 [acme-ledger] service published
  856 ledger2=ACTIVE audit=ACTIVE
  871 [audit] final entry appended
  873 [acme-ledger] timer cleared
  874 [acme-ledger] final flush done (appended=2, flushed=2)
  874 [acme-ledger] file closed /tmp/acme-ledger-FV3oBY/b.jsonl
  926 active resources before: ["PipeWrap","Timeout"]
  927 active resources after : ["PipeWrap"]
  928 file B: audit-start@acme-ledger-audit/v2 | tool:greet@acme-ledger-audit/v2 | audit-closing@acme-ledger-audit/v2 | audit-start@acme-ledger-audit/v3 | audit-closing@acme-ledger-audit/v3
  928 done
```

另一个实验直接核对归属。audit 的 `fiber.getEffects()` 列出了它的四项注册，卸载后 `ledger_stats` 和 section 都消失了；tail 的 `watch` 订阅落在 `ctx.inject` 建的子 Fiber（`ctx.plugin()`）上，tail 卸载后 watchers 从 1 变成 0；从 root 调 `append()` 时署名是 `root`。

> 这里有一个不能外推的边界：本实验的消费方清理只花了 10ms。上一篇引用的官方审计规则指出，外层整体卸载时“最后 provide”的写法不是完整修复；耗时更长的清理、多个消费方并发卸载时的表现没有测试过。

```text title="实验：effect 归属与卸载后的注册表"
    5 [acme-ledger] apply path=/tmp/acme-cc-vDEmnx/x.jsonl tag=v1
    7 [audit] apply (backend=file)
    9 [acme-ledger] service published
audit fiber effects: ["ctx.on(\"tools/result\")","tools.register()","systemPrompt.section()","audit:final-entry"]
tail fiber effects : ["ctx.on(\"acme-ledger/flushed\")","ctx.plugin()"]
watchers before tail dispose: 1
   61 [tail] watch: from-root by root tag=v1
watchers after tail dispose : 0
tools before audit dispose: [ 'ledger_stats' ]
   73 [audit] final entry appended
tools after audit dispose : []
sections after audit dispose: harness:identity,deployment:persona-prefix,deployment:persona-suffix
   74 [acme-ledger] timer cleared
   74 [acme-ledger] final flush done (appended=3, flushed=3)
   74 [acme-ledger] file closed /tmp/acme-cc-vDEmnx/x.jsonl
```

## 坑点清单：28 条，按出现顺序

前面各节的结论汇总成一张表，写插件和排查问题时可以直接对照。“依据”一列是仓库路径（相对 dsh 根目录）或本文的实验编号。

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
| 9 | waterfall 下游默认行为消失，或调用方拿到 `undefined` | 没调 `next()`，或调了没 `return` | 观察型监听器写成 `return await next()`；`next` 只调一次 | `events.ts:250-265`；实验 03 |
| 10 | `serial`/`bail` 返回 `0` 或 `''` 时停了；`bail` 遇到 async 监听器总是短路 | `isBailed` 只放行 `null/false/undefined` | 数值结果不要用 bail 类模式 | `events.ts:12-14`；实验 02 |
| 11 | 监听器顺序和预期不一样 | 只有注册顺序加 `prepend`，其他插件可以插到前面 | 确定性策略写在服务代码路径里，或用 `tools.guard()` | `user-approval/src/index.ts:297-302` |
| 12 | `ctx.on('turn/start')` 从不触发 | 它是会话事件类型，不是 Cordis 事件 | 监听 `session/event` 再判断 `event.type` | `events.zh.md:104-106` |
| 13 | 门禁拿到的参数类型不对，甚至是字符串 | 参数校验在工具主体里，晚于 pre-execute 和 guard | 门禁自己做防御性检查 | `schema.ts:578-603`、`batch.ts:98-105` |
| 14 | Config 被忽略，apply 收到 `undefined` | `export default function` 搭配具名 `Config` | 只用具名导出，或 default 导出带 `static Config` 的类 | `loader/src/index.ts:232`；实验 E1 |
| 15 | 用户覆盖一个字段后，其他 config 字段消失 | patch 按 id 整块替换 `config` | 默认值写在 schema 的 `.default()` 里 | `patch.ts:165-169`；实验 E5 |
| 16 | 改了 volatile 字段，插件却重启了 | 同次编辑还改了别的键或普通字段，或 Fiber 不是 ACTIVE | 单独改 volatile；值用 `.get()` 现读 | `entry.ts:155-211`；实验 E2 |
| 17 | volatile 非法值只 warn，下次启动却 FAILED | 非法值已写回磁盘 | 改配置后检查日志 | 实验 E3 |
| 18 | `disabled: !!js` 在环境变化后不生效 | 只在树协调时求值，文件没变就不协调 | 改文件或手动触发协调 | `include/src/index.ts:411`；实验 E2 |
| 19 | yml 里的插件永久 PENDING，没有提示 | 条目 `inject` 叠加到插件 inject 上（例如笔误） | 用 `captureStartupComposition` 或遍历 registry 查缺失服务 | `loader/src/index.ts:149-155`；实验 E3 |
| 20 | 插件自释放后，文件里多了 `disabled: true` | Loader 把自释放写回用户文件 | 需要暂时停止时不要调 `ctx.fiber.dispose()` | `loader/src/index.ts:157-195` |
| 21 | 匿名行改了自身配置就重建 | 没有 id，按内容匹配失败后换新 id | 每行写显式 `id` | `include/src/index.ts:615-662`；实验 E2 |
| 22 | 改代码没反应 | 默认 `root: []`；没有 timer 或 `--expose-internals`；新建文件不触发；`node_modules` 不参与 | 开启 root，挂上 timer，保存已加载的那个文件 | `hmr/src/index.ts:211-226, 436-489`；实验 E4 |
| 23 | HMR 后定时器或连接翻倍 | 模块顶层副作用不会随逐出清理 | 所有副作用放进 `apply` 或 `ctx.effect` | 上一篇“模块级状态” |
| 24 | 按教程 07 运行没有输出，exit 0 | 缺 `dsh-session-projection`，整条链 PENDING | 补上该行 | 实验 A |
| 25 | `TOOL_EXECUTOR_UNAVAILABLE`（execute 或 assemble 时） | 没有 executor，或 executor 比调用方晚激活 | 组合里加 `dsh-tool-executor-native`；不要在 apply 里自调工具 | `tools/src/index.ts:111-118, 353-378`；实验 B、C |
| 26 | 本地 `.ts` 插件被静默跳过 | strip-only 不支持某些 TS 语法；没有 logger 看不到错误 | 挂上 `cordis-plugin-logger-console`；避免 parameter property | `logger.ts:212-224`；acme 首次运行 |
| 27 | 消费方最后一次写入失败 | 资源 effect 和服务发布是并发执行的兄弟 effect | 一个生成器 effect，服务最后 `yield ctx.provide` | 上一篇竞态一节；acme 运行记录 |
| 28 | 第三方 section 位置不好控制 | `getSectionOrder()` 只认内置名字 | 自选数字（本文用 5100），目前没有官方区间 | `system-prompt/src/index.ts:191-231` |

读完源码再看 dsh 的插件模型，最值得记住的是**归属**的算法：一个注册属于谁，由“哪个 context 调用了这个方法”决定，和注册代码写在哪个类里无关。只要服务方法通过 tracker 拿到调用方 ctx，并把修改包进 `this.ctx.effect`，“插件卸载即撤销”就是白送的。配置重启、HMR 替换、换提供方，最后都归结为同一个动作：卸载再加载。

反过来，dsh 在 Cordis 之上补的东西，几乎都在填这份“白送”覆盖不到的地方：`events.dispatch` 加逐个隔离、作用域 carrier、“最后 provide”的约定、启动组合诊断。**错误隔离和启动顺序是插件作者自己的责任**。教程 07 跑不通也说明，dsh 有不少依赖没写进 `inject`，只体现在 base bundle 的行顺序里。第三方插件最稳的做法是叠加在 base bundle 上，而不是从零拼组合。
