# Cordis / dsh 服务（"03 服务"章节）源码笔记

> 范围：dsh 仓库 commit `4b399c3`（`4b399c39 Merge pull request #8961`），vendored Cordis 4.2.0-alpha.1（`vendor/cordis/src/*`）。所有 `path:line` 为 dsh 仓库相对路径。实验在 npm 安装的同版本 `@deepseek-ai/cordis@4.2.0-alpha.1` 上运行（node v24），脚本在 `/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services/`。下文 `$LIB` = `/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/cordis-lab/node_modules/@deepseek-ai/cordis/lib/index.js`。
>
> 与已有文章的去重：`deepseek/dsh_lifecycle_and_effects.md` 已讲 Fiber 六状态、epoch 字符串（"依赖和修订号拼成"）、"依赖消失与恢复"实验、"服务提供方先释放了资源"竞态、以及 `ctx.inject` 做可选依赖（其 1106 行一节）。本笔记只在服务需要时引用这些机制，重点放在：注册存储在哪、怎么撤销、谁在等谁、调用方追踪、check/init/invoke/intercept、isolate。
>
> FiberState 数值（实验输出里用到）：`PENDING=0, LOADING=1, ACTIVE=2, FAILED=3, DISPOSED=4, UNLOADING=5` — [vendor/cordis/src/fiber.ts:177-184](vendor/cordis/src/fiber.ts)

## Q1 `super(ctx, 'name')` 与 `ctx.provide(name, value)` 到底做了什么？注册存在哪里、怎么撤销？

### Takeaway
`Service` 构造函数只是 `ctx.reflect.provide(name, self, this[Service.check])` 的包装：它先给实例挂上 `tracker`（让后续方法调用能看到调用方 ctx），可调用服务还会换成一个函数对象，然后调用 `provide`。`provide` 本身是一个 `ctx.fiber.effect`：在根 `ReflectService.store`（按"隔离 symbol"为键）里写入一条带自增 `uid` 的 `Impl` 记录，同时加入提供方 fiber 的 `provisions` 集合；撤销时先从 store 删除并 notify 依赖方，再**等所有依赖方卸载完**才把 impl 从 `provisions` 移走。

### Cited Findings
- `Service` 构造函数：名字缺省时读类的静态 `provide` 字段；构造 `tracker = { associate: name, property: 'ctx' }`；若原型上有 `[symbols.invoke]` 就用 `createCallable` 造一个函数对象代替 `this`；设置 `ctx`/`name`/`[symbols.tracker]`，最后 `self.ctx.reflect.provide(name, self, this[symbols.check])` 并 `return self`（构造函数返回别的对象，所以 `new` 得到的是可调用代理）— [vendor/cordis/src/service.ts:45-62](vendor/cordis/src/service.ts)

`vendor/cordis/src/service.ts:45-62`（逐字摘录）

```ts
  constructor(protected ctx: Context, name: string) {
    name ??= this.constructor['provide'] as string

    let self = this
    const tracker: Tracker = {
      associate: name,
      property: 'ctx',
    }
    if (self[symbols.invoke]) {
      self = createCallable(name, joinPrototype(Object.getPrototypeOf(this), Function.prototype), tracker)
    }
    self.ctx = ctx
    self.name = name
    defineProperty(self, symbols.tracker, tracker)

    self.ctx.reflect.provide(name, self, this[symbols.check])
    return self
  }
```

- `Impl` 记录的字段：`uid`、`name`、`ctx`（提供方上下文，含隔离信息）、`dependents: Set<Fiber>`、`release`、`value`、`check`、`isolate` — [vendor/cordis/src/reflect.ts:115-133](vendor/cordis/src/reflect.ts)

`vendor/cordis/src/reflect.ts:115-133`（逐字摘录）

```ts
/** Concrete service implementation record stored in the root reflect service. */
export interface Impl {
  /** Unique identity of this provision. */
  uid: number
  /** The service name. */
  name: string
  /** The providing context, including its service isolation. */
  ctx: Context
  /** Fibers retaining this implementation until their teardown finishes. */
  dependents: Set<Fiber>
  /** Wake a provision disposer after its final dependent unloads. */
  release?: () => void
  /** The current service value. */
  value?: any
  /** Optional availability predicate consulted before dependents may load. */
  check?: () => boolean
  // Actual isolation symbol; may differ from the provider context after a blocked move.
  isolate: symbol
}
```

- `ReflectService.store` 是 `Dict<Impl, symbol>`，键是**隔离 symbol**而不是名字；`props` 按名字记录"这个名字是 service 还是 accessor"；`_implCounter` 是全局自增计数 — [vendor/cordis/src/reflect.ts:243-247](vendor/cordis/src/reflect.ts)。`ctx.reflect` 是根上下文构造时创建的单例 — [vendor/cordis/src/context.ts:73-86](vendor/cordis/src/context.ts)
- `provide()` 全文：包在 `this.ctx.fiber.effect(..., 'ctx.provide("name")')` 里；名字已被声明为 accessor 时抛错；根上下文的隔离表里没有这个名字就分配 `Symbol(name)`；按当前 ctx 的隔离 symbol 取 `key`；**先**创建 `impl`（`uid: ++this._implCounter`）**再**检查 `this.store[key]` 是否已占用——占用就抛 `service "x" has been registered at <fiber>`；成功后写入 `store[key]`、加入 `fiber.provisions`；若提供方 fiber 已 ACTIVE 立即 `notify([name])`。返回的 disposer：从 store 删除（仅当仍是自己）、在同一隔离标签下 notify、然后 `await` 一个 Promise，直到 `impl.dependents` 为空（由依赖方卸载时调用 `impl.release`）、最后 `provisions.delete(impl)` — [vendor/cordis/src/reflect.ts:313-349](vendor/cordis/src/reflect.ts)

`vendor/cordis/src/reflect.ts:313-349`（逐字摘录）

```ts
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

- 由于 `uid` 在重复检查之前就递增，一次失败的重复注册也会"吃掉"一个 uid（实验 1 中失败的 B 消耗了 uid 2，后来的 C 得到 uid 3）— [vendor/cordis/src/reflect.ts:324-326](vendor/cordis/src/reflect.ts)，见实验 1。
- 在 Service 子类里注册发生在构造函数中（同步、早于 `[Service.init]`），但 `provide` 在 fiber 不是 ACTIVE 时**不** notify；等 fiber 从 LOADING 进入 ACTIVE，`_updateState` 会遍历 store 中属于本 fiber 的 impl 再统一 notify（ACTIVE↔非 ACTIVE 切换都会 notify）— [vendor/cordis/src/fiber.ts:612-623](vendor/cordis/src/fiber.ts)

`vendor/cordis/src/fiber.ts:612-623`（逐字摘录）

```ts
    if (oldState === this.state) return
    // FIXME internal/fiber-info
    this.context.emit('internal/status', this, oldState)

    // only notify changes between ACTIVE and NON-ACTIVE states
    if (oldState !== FiberState.ACTIVE && this.state !== FiberState.ACTIVE) return
    for (const key of Reflect.ownKeys(this.ctx.reflect.store)) {
      const impl = this.ctx.reflect.store[key as symbol]
      if (impl.ctx.fiber !== this) continue
      this.ctx.isolate(impl.name, impl.isolate).reflect.notify([impl.name])
    }
  }
```

- 严格读取：`ctx.get(name)`（`strict` 默认 `true`）在提供方 fiber 不是 ACTIVE 时返回 `undefined`；`ctx.get(name, false)` 才能拿到尚在 LOADING 的值 — [vendor/cordis/src/reflect.ts:269-279](vendor/cordis/src/reflect.ts)，实验 6 输出 `ctx.get('_private') = undefined | ctx.get('_private', false) = { v: 1 }`。

`vendor/cordis/src/reflect.ts:269-279`（逐字摘录）

```ts
  get(name: string, strict = true) {
    return getTraceable(this.ctx, this._getImpl(name, strict)?.value)
  }

  _getImpl(name: string, strict = true) {
    const key = this.ctx[symbols.isolate][name]
    const impl = key && this.store[key]
    if (!impl) return
    if (strict && impl.ctx.fiber.state !== FiberState.ACTIVE) return
    return impl
  }
```

- `ctx.set(name, value)` 只允许提供该服务的同一 fiber 改值，否则抛 `cannot set property "x" in multiple fibers` — [vendor/cordis/src/reflect.ts:290-301](vendor/cordis/src/reflect.ts)
- 函数插件可以直接 `ctx.provide(name, value)`，dsh 中多见于测试替身，例如 `ctx.provide('sessions', this.sessions)` — [packages/test-support/client-runtime/src/index.ts:272-274](packages/test-support/client-runtime/src/index.ts)；`ctx.provide('workingDirectory', {...})` — [packages/test-support/agent-loop-testkit/src/working-directory-fixture.ts:13](packages/test-support/agent-loop-testkit/src/working-directory-fixture.ts)。生产服务几乎都用 `extends Service`（grep 到 80+ 个子类，如 `ToolsService`、`SystemPrompt`、`ShellExecutor`）— 例：[packages/core/tools/src/index.ts:99](packages/core/tools/src/index.ts)、[packages/core/system-prompt/src/index.ts:486](packages/core/system-prompt/src/index.ts)、[packages/shell/shell/src/index.ts:64](packages/shell/shell/src/index.ts)
- 抽象基类 + 具体实现的写法：`ShellExecutor` 在基类构造里固定 `super(ctx, 'shell')`，实现类 `LocalBashExecutor` 只 `super(ctx)` — [packages/shell/shell/src/index.ts:64-67](packages/shell/shell/src/index.ts)、[packages/shell/bash-local/src/index.ts:97-111](packages/shell/bash-local/src/index.ts)。基类注释明确写"one implementation per context; loading a second throws, which is cordis' standard duplicate-service behavior" — [packages/shell/shell/src/index.ts:36-40](packages/shell/shell/src/index.ts)

`packages/shell/shell/src/index.ts:64-67`（逐字摘录）

```ts
export abstract class ShellExecutor extends Service {
  constructor(ctx: Context) {
    super(ctx, 'shell')
  }
```

`packages/shell/bash-local/src/index.ts:97-111`（逐字摘录）

```ts
export class LocalBashExecutor extends ShellExecutor {
  static inject = ['subprocess']

  static Config = z.object({
    cwd: z.string().volatile(),
    timeoutMs: z.number().default(120_000).volatile(),
    maxTimeoutMs: z.number().default(600_000).volatile(),
    maxOutputBytes: z.number().default(64_000).volatile(),
    maxSpillBytes: z.number().default(DEFAULT_MAX_SPILL_BYTES).volatile(),
    graceMs: z.number().default(DEFAULT_GRACE_MS).volatile(),
  })

  constructor(ctx: Context, readonly config: Config) {
    super(ctx)
  }
```

- Loader 是一个不用 `super(ctx, name)` 自动注册的例子：它先手动 `defineProperty(this, Service.tracker, {...})`，再 `ctx.reflect.provide('loader', this, this[Service.check])` — [vendor/loader/src/index.ts:96-102](vendor/loader/src/index.ts)（说明 `super(ctx, name)` ≈ 设 tracker + `reflect.provide`）。

#### 实验 1：替换提供方 → 消费方用新 uid 重跑；重复注册抛错；未 inject 直接读会抛错
脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services/01-swap-dup.mjs`

命令：

```sh
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services && node 01-swap-dup.mjs $LIB
```

输出（原样）：

```text
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

解读：
- 消费方 epoch 由 `revision:uid` 组成（`0:1` → `0:3`），提供方换了 impl，uid 变了，消费方先卸载（`consumer effect disposed`）、在新提供方 C 上重跑 — 对应 `_refresh` 把 `impl.uid` 拼进 epoch：[vendor/cordis/src/fiber.ts:639-649](vendor/cordis/src/fiber.ts)
- B 在 A 仍活跃时加载，`provide` 抛 `service "greeter" has been registered at <Greeter>`，B 进入 FAILED(3)；A 卸载后 B **不会**自动顶上（失败的 fiber 只能通过 `update()` 恢复，见 [vendor/cordis/src/fiber.ts:655-656](vendor/cordis/src/fiber.ts)），需要重新加载一个提供方（C）。
- 一个**有 runtime 的插件**里，未在 `inject` 中声明就读 `ctx.greeter` 会抛 `cannot get property "greeter" without inject`，即使服务已存在；`ctx.get('greeter')` 不受此限制。

### Inferences
- "服务"在实现上就是"根 reflect store 里的一条以隔离 symbol 为键的 Impl 记录 + ctx 代理的 get 陷阱"。`Service` 基类不是必需的；它提供的增值是 tracker（调用方追踪）、`[Service.check]` 透传、可调用化和 `Service.init` 的类插件钩子。
- 因为注册是 effect，提供方卸载时撤销是自动的；作者不需要（也不应该）自己 `delete`。

### Gaps
- 未验证 `ctx.set()` 替换值时是否通知消费方：从代码看 `set` 只改 `impl.value`，不调用 `notify`（[vendor/cordis/src/reflect.ts:290-301](vendor/cordis/src/reflect.ts)），uid 不变，所以消费方不会重跑——这是推断，未做实验。

## Q2 `inject` 如何判断就绪？提供方变化/消失时消费方如何重跑？提供方如何等依赖方？

### Takeaway
就绪判定在消费方 fiber 的 `_checkImpl(name)`：取严格（提供方 ACTIVE）的 impl，再调用它的 `check`（以消费方 ctx 追踪后的服务为 `this`），通过才记入私有 `_store`；`_refresh` 用所有 inject 项的 `uid` 拼 epoch，任何一项缺失就是 INACTIVE（PENDING）。提供方的增删、ACTIVE 状态切换都会走 `reflect.notify(names)`，它遍历所有 runtime 的所有 fiber，对 inject 了该名字且隔离标签匹配的 fiber 重新 `_checkImpl` + `_refresh`。消费方开始加载时把 `_store` 快照为 `committed` 并把自己加入每个 impl 的 `dependents`；提供方卸载时先等 `dependents` 清空，再执行自己的 effects。

### Cited Findings
- `_checkImpl` 与 `_refresh`：

`vendor/cordis/src/fiber.ts:625-649`（逐字摘录）

```ts
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
```

  — `check` 抛错会被记日志并视为不可用 — [vendor/cordis/src/fiber.ts:628-634](vendor/cordis/src/fiber.ts)
- `notify(names, filter)`：遍历 `this.ctx.registry.values()` 下所有 fibers，`name in fiber.inject` 且 `filter(fiber.ctx, name)`（默认：隔离 symbol 相同）时 `_checkImpl`，有更新就 `_refresh`；之后再在过滤后的上下文上 emit `internal/service` 事件 — [vendor/cordis/src/reflect.ts:357-376](vendor/cordis/src/reflect.ts)

`vendor/cordis/src/reflect.ts:357-376`（逐字摘录）

```ts
  notify(names: string[], filter = (ctx: Context, name: string) => ctx[symbols.isolate][name] === this.ctx[symbols.isolate][name]) {
    for (const runtime of this.ctx.registry.values()) {
      for (const fiber of runtime.fibers) {
        let hasUpdate = false
        for (const name of names) {
          if (!(name in fiber.inject)) continue
          if (!filter(fiber.ctx, name)) continue
          hasUpdate = true
          fiber._checkImpl(name)
        }
        if (!hasUpdate) continue
        fiber._refresh()
      }
    }
    for (const name of names) {
      const self: Context = Object.create(this.ctx)
      self[symbols.filter] = (target: Context) => filter(target, name)
      this.ctx.events.emit(self, 'internal/service', name, this._getImpl(name, false)?.value)
    }
  }
```

- 新 fiber 创建后（`internal/plugin` 事件之后）立即对 inject 的每个名字 `_checkImpl` 再 `_refresh`，所以加载顺序无关 — [vendor/cordis/src/fiber.ts:336-347](vendor/cordis/src/fiber.ts)
- `committed` 与 `dependents`：`_reload` 开头 `this.committed = { ...this._store }`，并对非自身提供的 impl `impl.dependents.add(this)` — [vendor/cordis/src/fiber.ts:703-708](vendor/cordis/src/fiber.ts)。`_unload` 先 `while (provisions 中有 impl 的 dependents 非空) await this._release`，然后并发执行本 fiber 的 disposables，最后从自己 committed 的每个 impl 的 `dependents` 删掉自己，若该 impl 的 dependents 变空就调用 `impl.release?.()` 和 `impl.ctx.fiber._release?.()` 唤醒提供方 — [vendor/cordis/src/fiber.ts:734-760](vendor/cordis/src/fiber.ts)

`vendor/cordis/src/fiber.ts:703-708`（逐字摘录）

```ts
  private async _reload() {
    this.committed = { ...this._store }
    for (const impl of Object.values(this.committed)) {
      if (impl.ctx.fiber === this) continue
      impl.dependents.add(this)
    }
```

`vendor/cordis/src/fiber.ts:734-760`（逐字摘录）

```ts
  private async _unload() {
    // Dependents are notified when this fiber leaves ACTIVE, but may still
    // access its services during their own teardown, so the effects of this
    // fiber are only disposed after every dependent has unloaded.
    while ([...this.provisions].some(impl => impl.dependents.size)) {
      await new Promise<void>((resolve) => this._release = resolve)
    }
    this._release = undefined
    await Promise.all(this._disposables.clear().map(async (dispose) => {
      try {
        await composeError(async (info) => {
          await Promise.resolve()
          info.error = new Error()
          await runDisposable(dispose)
        }, this._runner.getOuterStack)
      } catch (reason) {
        this.ctx.logger.error(reason)
      }
    }))
    // release the providers waiting for this fiber to unload
    for (const impl of Object.values(this.committed ?? {})) {
      if (!impl.dependents.delete(this) || impl.dependents.size) continue
      impl.release?.()
      impl.ctx.fiber._release?.()
    }
    this.committed = undefined
    this._updateState(() => {
```

- 消费方读服务时走的是 `committed` 视图而不是 store 当前值：`locate()` 从调用点的 fiber 往上找，先看本 fiber 的 `provisions`（同隔离），再看 `fiber.committed?.[name]`；`name in fiber.inject` 但 committed 没有时返回 `{ fiber }`，get 陷阱据此抛 `cannot get required service "x" in inactive context` — [vendor/cordis/src/reflect.ts:147-166](vendor/cordis/src/reflect.ts)、[vendor/cordis/src/reflect.ts:193-202](vendor/cordis/src/reflect.ts)。这就是为什么消费方在卸载期间（提供方已从 store 删除）仍能访问服务（实验 2 中 `consumer teardown start, still can use db: rows`）。

`vendor/cordis/src/reflect.ts:147-166`（逐字摘录）

```ts
export function locate(ctx: Context, name: string): { fiber?: Fiber; impl?: Impl } | undefined {
  const key = ctx[symbols.isolate][name]
  let table = ctx[symbols.isolate]
  let fiber = ctx.fiber
  while (true) {
    let impl: Impl | undefined
    for (const provision of fiber.provisions) {
      if (provision.name === name && provision.ctx[symbols.isolate][name] === key) impl = provision
    }
    if (impl) return { impl }
    while (table !== fiber.ctx[symbols.isolate]) {
      if (!table || Object.hasOwn(table, name)) return
      table = Object.getPrototypeOf(table)
    }
    impl = fiber.committed?.[name]
    if (impl || name in fiber.inject) return { fiber, impl }
    if (!fiber.runtime) return
    fiber = fiber.parent.fiber
  }
}
```

`vendor/cordis/src/reflect.ts:176-206`（逐字摘录）

```ts
  static handler: ProxyHandler<Context> = {
    get: (target, prop, ctx: Context) => {
      if (isSpecialProperty(prop)) {
        return Reflect.get(target, prop, ctx)
      }
      if (Reflect.has(target, prop)) {
        return getTraceable(ctx, Reflect.get(target, prop, ctx))
      }

      const error = new Error(`cannot get property "${prop}" without inject`)

      try {
        const def = target.reflect.props[prop]
        if (def?.type === 'accessor') {
          return def.get.call(ctx, ctx[symbols.receiver], error)
        }

        // a fiber-less def site cannot declare `inject` at all, so it keeps the unchecked root access.
        const defSite = (ctx[symbols.shadow] as Context | undefined) ?? ctx
        if (!defSite.fiber.runtime) return ctx.reflect.get(prop, false)
        return ctx.events.waterfall('internal/get', ctx, prop, error, () => {
          const result = locate(defSite, prop)
          if (!result) throw error
          if (result.impl) return getTraceable(ctx, result.impl.value)
          error.message = `cannot get required service "${prop}" in inactive context`
          throw error
        })
      } catch (e: any) {
        throw e === error ? enhanceError(e) : e
      }
    },
```

- `[Service.init]`：类插件构造完成后，fiber 执行 `instance?.[symbols.init]?.()` 并把其返回值（Promise）作为加载结果；fiber 在它 resolve 之前保持 LOADING — [vendor/cordis/src/fiber.ts:274-288](vendor/cordis/src/fiber.ts)。结合 Q1 的"非 ACTIVE 不 notify"，异步 init 完成前消费方不会启动。dsh 源码中的注释佐证："Initialized before dependent plugins activate." — [packages/core/system-prompt/src/index.ts:506-507](packages/core/system-prompt/src/index.ts)

`vendor/cordis/src/fiber.ts:274-288`（逐字摘录）

```ts
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
```

- `[Service.check]` 只在 notify 时被重新求值；如果 check 依赖的内部状态变了，服务**自己**必须调用 `ctx.reflect.notify([name])`。真实例子：loader 在每个 entry 的 fiber 加载结束、且没有剩余任务时 `this.ctx.reflect.notify(['loader'])` — [vendor/loader/src/config/entry.ts:251-261](vendor/loader/src/config/entry.ts)

`vendor/loader/src/config/entry.ts:251-261`（逐字摘录）

```ts
  async init() {
    try {
      await (this._initTask ??= this._init())
    } finally {
      this._initTask = undefined
    }
    this.fiber?.await().catch(() => {}).finally(() => {
      if (this.loader.getTasks().length) return
      this.ctx.reflect.notify(['loader'])
    })
  }
```

#### 实验 2：`[Service.init]` 被等待；`[Service.check]` 返回 false 让消费方 PENDING，且只有 notify 才会重查；提供方卸载等依赖方
脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services/02-check-init-order.mjs`

命令：

```sh
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services && node 02-check-init-order.mjs $LIB
```

输出（原样）：

```text
   2 Db constructor (super already registered impl)
   4 Db init start
  59 Db init done
  84 provider state 2 | consumer state 0 | ctx.get(db) = object
  95 after ready=true WITHOUT notify: consumer state 0
  95 consumer apply -> rows
 106 after reflect.notify: consumer state 2
 106 --- dispose provider
 106 consumer teardown start, still can use db: rows
 142 consumer teardown end
 142 provider own effect disposed
 142 provider dispose() resolved; consumer state 0
```

解读：
- 构造函数里 `super()` 已注册，但 init 跑了 50ms，期间消费方未启动；init 结束提供方 ACTIVE(2) 后，因 `check()` 返回 false，消费方仍是 PENDING(0)。
- 把 `ready` 置为 true **不会**自动唤醒消费方（`after ready=true WITHOUT notify: consumer state 0`）；手动 `root.reflect.notify(['db'])` 之后消费方才 apply。
- 卸载提供方：消费方的异步 teardown（30ms）先完成，并且 teardown 中仍能调用 `ctx.db.query()`；之后才执行提供方自己的 effect（`provider own effect disposed` 在 137ms）。这正是 `_unload` 的 `while (...dependents.size) await` 行为。

### Inferences
- `inject` 是持续的订阅：任何会改变 `(是否存在, 提供方是否 ACTIVE, check 结果, uid)` 的事件都会通过 notify 重算 epoch；epoch 变化 ⇒ 卸载并重新加载（细节见已有 lifecycle 文章，不再展开）。
- 对作者而言 `check` 是"服务存在但暂不可用"的表达手段；但它是**拉模式**，忘记 notify 会让消费方永远 PENDING。dsh packages 中没有一个包自己实现 `[Service.check]`（grep `Service.check` 在 packages 下 0 处，仅 vendor/loader 使用）— 这说明它是框架层工具，不是常规插件写法。

### Gaps
- 未测量 notify 的开销（遍历所有 runtime 的所有 fiber，[vendor/cordis/src/reflect.ts:358-370](vendor/cordis/src/reflect.ts)），大规模组合下的性能未知。

## Q3 traceable context / `symbols.tracker` / caller：为什么 `ctx.tools.register()` 知道是谁调用的？

### Takeaway
通过 ctx 代理读到的服务对象不是原始实例，而是 `getTraceable(ctx, value)` 包出的 Proxy：只要实例上有 `[symbols.tracker]`，Proxy 就把 `tracker.property`（Service 中是 `'ctx'`）这个属性改写为**读取方（use site）的 ctx**，并让方法以一个"把 `ctx` 覆盖成调用方 ctx"的 shadow 作为 `this` 执行。于是服务方法里写的 `this.ctx` 实际是调用插件的上下文：`this.ctx.effect(...)` 把注册挂到调用方 fiber 上，调用方卸载时注册自动撤销；`this.ctx` 上的自定义标记（如 dsh 的 agent scope）也随之可见。

### Cited Findings
- `getTraceable`：非对象原样返回；有 `[symbols.tracker]` 的对象包成 traceable proxy — [vendor/cordis/src/utils.ts:120-129](vendor/cordis/src/utils.ts)；ctx 代理的 get 陷阱对 `Reflect.has(target, prop)` 的属性和 locate 到的 impl 值都过一遍 `getTraceable` — [vendor/cordis/src/reflect.ts:181-183](vendor/cordis/src/reflect.ts)、[vendor/cordis/src/reflect.ts:199](vendor/cordis/src/reflect.ts)

`vendor/cordis/src/utils.ts:120-129`（逐字摘录）

```ts
/** Wrap services/functions so method calls see the caller's active context. */
export function getTraceable<T>(ctx: Context, value: T): T {
  if (!isObject(value)) return value
  if (Object.hasOwn(value, symbols.shadow)) {
    return Object.getPrototypeOf(value)
  }
  const tracker = value[symbols.tracker]
  if (!tracker) return value
  return createTraceable(ctx, value, tracker)
}
```

- `createTraceable` 的 get：`symbols.caller` 返回 def site；`tracker.property`（`'ctx'`）返回 use site；对于访问器/函数，构造 `createShadow`——一个把 `ctx` 只读覆盖为 `useSite.extend({ [symbols.shadow]: defSite })` 的 `withProps` 代理——并用 `createShadowMethod` 包装函数，使得 `thisArg === receiver` 时换成 shadow；带 tracker 的嵌套服务继续递归包裹 — [vendor/cordis/src/utils.ts:153-206](vendor/cordis/src/utils.ts)

`vendor/cordis/src/utils.ts:153-206`（逐字摘录）

```ts
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
      if (typeof prop === 'symbol') {
        return Reflect.get(target, prop, receiver)
      }
      // `ctx` rather than `useSite`, so that `ctx.foo.bar` and `ctx['foo.bar']` are
      // governed by the same def site
      if (tracker.associate && useSite.reflect.props[`${tracker.associate}.${prop}`]) {
        return Reflect.get(ctx, `${tracker.associate}.${prop}`, withProp(ctx, symbols.receiver, receiver))
      }
      let shadow: any, innerValue: any
      const desc = getPropertyDescriptor(target, prop)
      if (desc && 'value' in desc) {
        innerValue = desc.value
      } else {
        shadow = createShadow(useSite, target, tracker.property, receiver)
        innerValue = Reflect.get(target, prop, shadow)
      }
      const innerTracker = innerValue?.[symbols.tracker]
      if (innerTracker) {
        return createTraceable(useSite, innerValue, innerTracker)
      } else if (!tracker.noShadow && typeof innerValue === 'function') {
        shadow ??= createShadow(useSite, target, tracker.property, receiver)
        return createShadowMethod(useSite, innerValue, receiver, shadow)
      } else {
        return innerValue
      }
    },
```

- 注释说明 def site 与 use site 的分工："def site: where the accessing code was defined, governs service resolution; use site: where the service is consumed, governs intercept, isolate and effects" — [vendor/cordis/src/utils.ts:170-172](vendor/cordis/src/utils.ts)
- 真实使用：`ToolsService.register()` 最后一行 `return this.layers.effect(this.ctx, layer => layer.tools.insert(...), { label: 'tools.register()' })` — [packages/core/tools/src/index.ts:145-152](packages/core/tools/src/index.ts)；`ScopedLayers.effect(ctx, action)` 用 `scopeOf(ctx)` 选层，再 `ctx.effect(function* () { undo = action(layer); yield () => { undo(); ...; this.onChange() } })`，所以注册归属于调用方 fiber，并按调用方 ctx 上的 scope 分层 — [packages/core/scope/src/store.ts:218-262](packages/core/scope/src/store.ts)。`scopeOf(ctx)` 读的是 ctx 上的私有 `kScope` 标记 — [packages/core/scope/src/index.ts:154-156](packages/core/scope/src/index.ts)

`packages/core/tools/src/index.ts:128-152`（逐字摘录）

```ts
  register(definition: ToolDefinition, options: ToolRegistrationOptions = {}): () => void {
    const name = definition.name
    const output = (definition as Partial<ToolDefinition>).output
    if (output === undefined || typeof output !== 'object'
      || typeof output.render !== 'function'
      || (output.presentationMeta !== undefined && typeof output.presentationMeta !== 'function')) {
      throw new TypeError(`tool "${name}" must declare output { schema, render, presentationMeta? }`)
    }
    assertSupportedJsonSchema(output.schema)
    const timeoutMs = definition.timeoutMs
    if (timeoutMs !== undefined
      && (!Number.isFinite(timeoutMs) || timeoutMs <= 0)) {
      throw new TypeError(`tool "${name}" timeoutMs must be a positive finite number`)
    }
    // Reserved unconditionally: any agent may select a code mode for itself,
    // so a name free to take under the deployment default would become a
    // collision the moment a preset mounted.
    if (name === RUN_CODE_NAME) {
      throw new Error(`tool name "${RUN_CODE_NAME}" is reserved for the PTC mode presentation transport and cannot be registered or shadowed`)
    }
    return this.layers.effect(
      this.ctx,
      layer => layer.tools.insert(name, { definition, when: options.when }),
      { label: 'tools.register()' },
    )
```

`packages/core/scope/src/store.ts:225-262`（逐字摘录）

```ts
   */
  effect(
    ctx: Context,
    action: (layer: L) => () => void,
    options: { label: string; notify?: boolean },
  ): () => void {
    const scope = scopeOf(ctx)
    const notify = options.notify ?? true
    const dispose = ctx.effect(function* (this: ScopedLayers<L>) {
      let layer: L
      let created = false
      if (scope === undefined) {
        layer = this.global
      } else {
        const existing = this.scoped.get(scope)
        if (existing === undefined) {
          layer = this.createLayer(scope)
          this.scoped.set(scope, layer)
          created = true
        } else {
          layer = existing
        }
      }

      let undo: () => void
      try {
        undo = action(layer)
      } catch (error) {
        if (scope !== undefined && created && layer.isEmpty()) this.scoped.delete(scope)
        throw error
      }

      yield () => {
        undo()
        if (scope !== undefined && layer.isEmpty()) this.scoped.delete(scope)
        if (notify) this.onChange()
      }
      if (notify) this.onChange()
```

- `tools.restrict()` 进一步利用调用方 ctx：`scopeOf(this.ctx)` 为空时抛 "tools.restrict() requires a scoped context (agent.ctx)" — [packages/core/tools/src/index.ts:162-166](packages/core/tools/src/index.ts)。`SystemPrompt.section/context/variable` 同样用 `this.layers.effect(this.ctx, ...)` — [packages/core/system-prompt/src/index.ts:545-598](packages/core/system-prompt/src/index.ts)、[packages/core/system-prompt/src/index.ts:638-647](packages/core/system-prompt/src/index.ts)
- 构造函数里 `this.ctx` 是提供方自己的 ctx：`ToolsService` 构造时 `ctx.systemPrompt.tools(...)` 注册的工具提供器因此归属于 tools 服务的 fiber，tools 卸载时自动从 systemPrompt 撤下 — [packages/core/tools/src/index.ts:111-118](packages/core/tools/src/index.ts)

`packages/core/tools/src/index.ts:99-118`（逐字摘录）

```ts
export class ToolsService extends Service {
  static inject = ['systemPrompt']
  static Config = z.object({
    mode: z.never().description('Removed. Mount a native or PTC tool executor instead.'),
    maxParallelSubCalls: z.never().description('Moved to the PTC tool executor configuration.'),
  })

  private readonly layers = new ScopedLayers(scope => new ToolLayer(scope), () => { this.ctx.emit('tools/change') })
  private readonly scoped = new WeakMap<ScopeKey, Registration>()
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

#### 实验 3：方法里的 `this.ctx` 是调用方；调用方卸载后注册自动撤销；可调用服务
脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services/03-caller-registry.mjs`

命令：

```sh
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services && node 03-caller-registry.mjs $LIB
```

输出（原样）：

```text
   2 constructor: this.ctx.fiber.name = Commands
  39 init done
  39 plugin-one apply
  40 register("hello") called; this.ctx.fiber.name = plugin-one
  40 register("bye") called; this.ctx.fiber.name = plugin-two
  52 typeof ctx.commands = function | ctx.commands("hello") = hi from one
  53 list = [ 'hello', 'bye' ]
  64 --- dispose plugin-one
  64 auto-unregister "hello"
  64 list = [ 'bye' ]
  64 --- what if registry captured the ctx from the constructor instead? (anti-pattern)
```

解读：构造函数里 `this.ctx.fiber.name = Commands`（提供方），而 `register()` 中分别是 `plugin-one`、`plugin-two`；`p1.dispose()` 后 `"hello"` 自动注销，registry 服务本身没写任何"按插件清理"的逻辑。同时可见：`init done`（36ms）之后 `plugin-one apply`（37ms）才运行。

### Inferences
- "注册类 API 返回 disposer，并用 `this.ctx.effect` 实现" 是 dsh 服务的统一范式：使用方无需保存 disposer，插件卸载即撤销；提前撤销时调用返回的 disposer 即可（effect disposer 语义见已有 lifecycle 文章）。
- 调用方追踪只对"通过 ctx 读到的服务对象 + 用点号调用方法"生效；把 ctx 或方法从代理上拆下来就会失效（见实验 4 后半与"坑"）。

### Gaps
- `tracker.noShadow`（reflect/registry 服务使用，[vendor/cordis/src/reflect.ts:250-253](vendor/cordis/src/reflect.ts)、[vendor/cordis/src/registry.ts:199-204](vendor/cordis/src/registry.ts)）的完整语义只读了代码（不对函数创建 shadow，[vendor/cordis/src/utils.ts:200-202](vendor/cordis/src/utils.ts)），未做实验。

## Q4 `[Service.init]` / `[Service.check]` / `[Service.invoke]` / `[Service.extend]` / mergeIntercept / resolveIntercept

### Takeaway
插件作者真正会用到的是 `[Service.init]`（异步初始化，dsh 中 41 处）；`[Service.check]`、`[Service.invoke]`、`[Service.extend]`、`mergeIntercept/resolveIntercept` 在 packages/ 中均为 0 处使用，只出现在框架层（loader 的 check+resolveIntercept、logger 的 invoke）。`inject` 可以写成 `{ name: config }` 的映射，配合 `ctx.intercept()` 和 `resolveIntercept` 实现"每个消费方给服务传参数"。

### Cited Findings
- 符号定义均为 `Symbol.for('cordis.xxx')`（跨副本共享）— [vendor/cordis/src/utils.ts:68-76](vendor/cordis/src/utils.ts)；`Service` 上的静态别名 — [vendor/cordis/src/service.ts:13-28](vendor/cordis/src/service.ts)
- **`[Service.init]`**：类插件构造后被调用并等待（Q2 的 fiber.ts:274-288）。真实用法：
  - `SystemPrompt`：`this.admission = await this.ctx.sessionProjections.register(...)` — [packages/core/system-prompt/src/index.ts:534-536](packages/core/system-prompt/src/index.ts)
  - `WorkspaceRegistry`：打开存储域，并 `this.ctx.effect(() => () => domain.close(), 'workspace.domainClose')` 把关闭挂到服务自己的 fiber — [packages/workspace/workspace/src/index.ts:224-230](packages/workspace/workspace/src/index.ts)
  - `SshConnection`：`async [Service.init]() { await this.ready }` — [packages/ssh/ssh/src/index.ts:111](packages/ssh/ssh/src/index.ts)

`packages/workspace/workspace/src/index.ts:224-230`（逐字摘录）

```ts
  protected async [Service.init](): Promise<void> {
    const domain = await this.ctx.storageDomain.open(workspaceDomainSpec)
    this.ctx.effect(() => () => domain.close(), 'workspace.domainClose')
    this.table = domain.table('workspaces')
    this.global = domain.global
    this.state = domain.global.get()

```

- **`[Service.check]`**：构造函数把 `this[symbols.check]` 作为第三个参数传给 `provide`（service.ts:60）；消费方 `_checkImpl` 用 `impl.check.call(getTraceable(this.ctx, impl.value))` 调用，所以 check 内的 `this.ctx` 是**被检查的消费方**的 ctx（实验 5 打印了 consumer 名字）— [vendor/cordis/src/fiber.ts:629](vendor/cordis/src/fiber.ts)。唯一真实用法 loader：

`vendor/loader/src/index.ts:204-208`（逐字摘录）

```ts
  [Service.check]() {
    const config: Loader.Intercept = Service.prototype[Service.resolveIntercept].call(this)
    if (config.await && this.getTasks().length) return false
    return true
  }
```

  其 Intercept 类型 `{ await?: boolean }`，注释 "Keep dependent plugins pending while loader entries are still loading" — [vendor/loader/src/index.ts:65-69](vendor/loader/src/index.ts)
- **`[Service.resolveIntercept]` / `[Service.mergeIntercept]`**：先沿 `this.ctx[Context.intercept]` 原型链从近到远收集（`unshift`，所以根最先），再把"准入该访问的 fiber 的 `inject[name]`"放在最前，最后用 `mergeIntercept`（默认 `Object.assign({}, ...values)`，后者优先）合并 — [vendor/cordis/src/service.ts:86-112](vendor/cordis/src/service.ts)

`vendor/cordis/src/service.ts:86-112`（逐字摘录）

```ts
  [symbols.mergeIntercept](...values: T[]): T {
    return Object.assign({}, ...values)
  }

  /**
   * Resolve the intercept metadata visible to the current caller.
   *
   * The metadata injected by the fiber that admits the access applies first,
   * then intercepts added by ancestor contexts, closest to the root first.
   * The values are combined with `[Service.mergeIntercept]`.
   *
   * @returns the merged metadata.
   */
  [symbols.resolveIntercept](): T {
    const values: T[] = []
    let intercept = this.ctx[Context.intercept]
    while (this.name in intercept) {
      if (Object.hasOwn(intercept, this.name)) {
        values.unshift(intercept[this.name])
      }
      intercept = Object.getPrototypeOf(intercept)
    }
    const value = locate((this as any)[symbols.caller] ?? this.ctx, this.name)?.fiber?.inject[this.name]
    if (!isNullable(value)) values.unshift(value)
    const merge = this[symbols.mergeIntercept] ?? Service.prototype[symbols.mergeIntercept]
    return merge.apply(this, values)
  }
```

- `inject` 的三种形态由 `Inject.resolve` 统一成 `name → config|null` 映射：数组 → 值为 null；对象 → 保留值；`@Inject` 装饰器生成的带 `checkProto` 的对象会合并父类的 inject — [vendor/cordis/src/registry.ts:71-88](vendor/cordis/src/registry.ts)。真实 `@Inject` 用法：`@Inject('loader') @Inject('timer') class Hmr extends Service` — [packages/boot/hmr/src/index.ts:211-213](packages/boot/hmr/src/index.ts)

`vendor/cordis/src/registry.ts:71-88`（逐字摘录）

```ts
  export function resolve(inject: Inject | null | undefined, result: Dict = Object.create(null)) {
    if (!inject) return result
    if (Array.isArray(inject)) {
      for (const name of inject) {
        result[name] = null
      }
    } else if (Reflect.has(inject, symbols.checkProto)) {
      Object.assign(result, resolve(Object.getPrototypeOf(inject)))
      for (const name of Object.keys(inject)) {
        result[name] = inject[name] ?? null
      }
    } else {
      for (const name of Object.keys(inject)) {
        result[name] = inject[name] ?? null
      }
    }
    return result
  }
```

- `ctx.intercept(name, config)`：创建子上下文，原型继承父 intercept 表并设置 `name` 项 — [vendor/cordis/src/context.ts:147-151](vendor/cordis/src/context.ts)；loader 的 cordis.yml `intercept:` 条目选项写入 `entry.ctx[Context.intercept]` — [vendor/loader/src/config/isolate.ts:92](vendor/loader/src/config/isolate.ts)、[vendor/loader/src/config/isolate.ts:127](vendor/loader/src/config/isolate.ts)
- **`[Service.invoke]`**：有它时构造函数返回 `createCallable(...)` 生成的函数对象（原型是类原型与 `Function.prototype` 的拼接），调用时走 `applyTraceable` → `value[symbols.invoke].apply(proxy, args)` — [vendor/cordis/src/utils.ts:230-243](vendor/cordis/src/utils.ts)。框架内的例子是 `ctx.logger('name')` — [vendor/cordis/src/logger.ts:203-210](vendor/cordis/src/logger.ts)、[vendor/cordis/src/logger.ts:243-244](vendor/cordis/src/logger.ts)。实验 3 中 `typeof ctx.commands = function | ctx.commands("hello") = hi from one`。

`vendor/cordis/src/utils.ts:230-243`（逐字摘录）

```ts
function applyTraceable(proxy: any, value: any, thisArg: any, args: any[]) {
  if (!value[symbols.invoke]) return Reflect.apply(value, thisArg, args)
  return value[symbols.invoke].apply(proxy, args)
}

/** Create a callable service object that dispatches through `symbols.invoke`. */
export function createCallable(name: string, proto: {}, tracker: Tracker) {
  const self = function (...args: any[]) {
    const proxy = createTraceable(self['ctx'], self, tracker)
    return Reflect.apply(proxy, this, args)
  }
  defineProperty(self, 'name', name)
  return Object.setPrototypeOf(self, proto)
}
```

- **`[Service.extend]`**：派生一个原型继承自当前实例的新对象（可调用服务则再造一个 callable），并 `Object.assign(self, props)` — [vendor/cordis/src/service.ts:68-76](vendor/cordis/src/service.ts)。packages 中未见使用。
- **`[symbols.filter]`**：Service 默认事件过滤器 `ctx[isolate][this.name] === this.ctx[isolate][this.name]`，使服务发出的事件只送达同一隔离作用域的监听者 — [vendor/cordis/src/service.ts:64-66](vendor/cordis/src/service.ts)

#### 实验 5：`inject` 写成映射 + `ctx.intercept` + check 中 resolveIntercept
脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services/05-inject-map-intercept.mjs`

命令：

```sh
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services && node 05-inject-map-intercept.mjs $LIB
```

输出（原样）：

```text
  check() for consumer patient sees intercept {"await":true}
  check() for consumer eager sees intercept {}
  check() for consumer via-intercept sees intercept {"await":true}
eager apply
states: 0 2 0
  check() for consumer patient sees intercept {"await":true}
  check() for consumer eager sees intercept {}
  check() for consumer via-intercept sees intercept {"await":true}
patient apply
via-intercept apply
states after busy=false + notify: 2 2 2
```

解读：同一个 `gate` 服务对三个消费方给出不同就绪结论：`inject: { gate: { await: true } }` 与 `root.intercept('gate', { await: true })` 两种写法都让 check 看到 `{"await":true}` 从而保持 PENDING；普通 `inject: ['gate']` 立即启动。改状态后仍需手动 notify。

### Inferences
- 对普通插件作者：只需要掌握 `[Service.init]`（异步就绪）；`check`/`invoke`/`intercept` 是写"框架级服务"（如 loader、logger）时的工具。
- `Service.init` 期间 `this.ctx.effect` 归属服务自身 fiber，是放置资源关闭逻辑的正确位置（workspace 示例）。

### Gaps
- `Service.metadata`（类型参数 `T`）只用于 TypeScript 推断 `ctx.intercept` / `@Inject` 的配置类型（[vendor/cordis/src/context.ts:145](vendor/cordis/src/context.ts)、[vendor/cordis/src/registry.ts:22-24](vendor/cordis/src/registry.ts)），无运行时行为；未进一步研究。

## Q5 `ctx.isolate` 与隔离作用域：解决什么问题，cordis.yml 怎么配？

### Takeaway
store 以"隔离 symbol"为键，每个 ctx 携带一张 `name → symbol` 的隔离表（原型链继承）。`ctx.isolate(name, label?)` 只是给子 ctx 的这张表里把 `name` 换成新 symbol，于是该子树中的 `provide`/读取/notify 都落在另一个槽位：两组插件可以各有一个 `shell`，互不冲突，也不会触发"重复注册"错误。同一 label symbol 可让两个子树共享一个槽位；新隔离的子树**不会回退**到父作用域的实现。cordis.yml 中用条目选项 `isolate: { shell: true }`（条目私有）或 `isolate: { shell: 'label' }`（同名 label 共享）。

### Cited Findings
- `isolate()` 实现与文档语义（"never fall back to the enclosing plugin's injected name … until a provider in the new scope supplies name, reading it throws"）— [vendor/cordis/src/context.ts:111-130](vendor/cordis/src/context.ts)

`vendor/cordis/src/context.ts:126-130`（逐字摘录）

```ts
  isolate(name: string, label?: symbol) {
    const shadow = Object.create(this[symbols.isolate])
    shadow[name] = label ?? Symbol(name)
    return this.extend({ [symbols.isolate]: shadow })
  }
```

- `locate()` 在向上走 fiber 时，若遇到对 `name` 有 own 隔离项的表就返回 undefined（不跨越隔离边界）— [vendor/cordis/src/reflect.ts:157-160](vendor/cordis/src/reflect.ts)；`notify` 默认过滤器按隔离 symbol 相等 — [vendor/cordis/src/reflect.ts:357](vendor/cordis/src/reflect.ts)
- loader 实现 cordis.yml 的 `isolate`：`label === true` → 条目私有 `LocalRealm`（symbol 名后缀 `#<entry id>`）；字符串 → 全局 `GlobalRealm`（后缀 `@<label>`），同 label 共享 — [vendor/loader/src/config/isolate.ts:25-89](vendor/loader/src/config/isolate.ts)；配置变化时计算 diff、在 store 中把 impl 从旧 symbol 挪到新 symbol（`impl.isolate = symbol2`，这就是 `Impl.isolate` 注释"may differ from the provider context after a blocked move"的来源）并带过滤器 notify — [vendor/loader/src/config/isolate.ts:96-156](vendor/loader/src/config/isolate.ts)、[vendor/cordis/src/reflect.ts:131-132](vendor/cordis/src/reflect.ts)；schema 校验 `isolate.<name> must be true or a string` — [vendor/loader/src/config/utils.ts:146-150](vendor/loader/src/config/utils.ts)

`vendor/loader/src/config/isolate.ts:77-101`（逐字摘录）

```ts
  function access(entry: Entry, name: string, create = false) {
    let realm: Realm | undefined
    const label = entry.options.isolate?.[name]
    if (!label) return
    if (label === true) {
      realm = entry.realm ??= new LocalRealm(entry)
    } else if (create) {
      realm = realms[label] ??= new GlobalRealm(label)
    } else {
      realm = realms[label]
    }
    return realm?.access(name, create)
  }

  ctx.on('loader/entry-init', (entry) => {
    entry.ctx[Context.intercept] = Object.create(entry.ctx[Context.intercept])
    entry.ctx[Context.isolate] = Object.create(entry.ctx[Context.isolate])
  })

  ctx.on('loader/patch-context', async (entry, next) => {
    // step 1: generate new isolate map
    const newMap: Dict<symbol> = Object.create(entry.parent.ctx[Context.isolate])
    for (const name of Object.keys(entry.options.isolate ?? {})) {
      newMap[name] = access(entry, name, true)
    }
```

- 文档示例：两个 `cordis-plugin-group` 条目各 `isolate: { shell: true }`，各自挂一个 `dsh-bash-local`（不同 `timeoutMs`）和一个消费插件 — [docs/user/develop/framework/service.zh.md:113-141](docs/user/develop/framework/service.zh.md)

`docs/user/develop/framework/service.zh.md:117-141`（逐字摘录）

````md
```yaml
- id: group-a
  name: '@deepseek-ai/cordis-plugin-group'
  group: true
  isolate:
    shell: true
  config:
    - name: '@deepseek-ai/dsh-bash-local'
      config:
        timeoutMs: 5000
    - name: './src/plugin-a.ts'

- id: group-b
  name: '@deepseek-ai/cordis-plugin-group'
  group: true
  isolate:
    shell: true
  config:
    - name: '@deepseek-ai/dsh-bash-local'
      config:
        timeoutMs: 60000
    - name: './src/plugin-b.ts'
```

`plugin-a` 和 `plugin-b` 各自看到自己组内的 Bash 实例，互不影响。
````

- 仓库中的真实 yml：terminal 面板快照把 `dsh-terminal`、`dsh-terminal-panel`、`dsh-tool-terminal` 放进 `isolate: { terminals: true }` 的组 — [snapshots/session/terminal-panel-tools/cordis.yml:3-17](snapshots/session/terminal-panel-tools/cordis.yml)

`snapshots/session/terminal-panel-tools/cordis.yml:3-17`（逐字摘录）

```yaml
- insert:
    - id: terminal-controller-fixture
      name: './terminal-controller-fixture.mjs'
    - id: optional-persistent-terminals
      name: cordis:group
      group: true
      isolate:
        terminals: true
      config:
        - id: pty
          name: '@deepseek-ai/dsh-terminal'
        - id: terminal-panel
          name: '@deepseek-ai/dsh-terminal-panel'
        - id: tool-terminal
          name: '@deepseek-ai/dsh-tool-terminal'
```

- 代码中的真实 `ctx.isolate`：
  - SQLite 会话持久化在同一插件里同时创建一个旧版 JSONL 实现（放进 `serviceCtx.isolate('sessionPersistence')`，不占用公共槽位）和对外的 SQLite 实现 — [packages/session/session-persistence-sqlite/src/index.ts:42-56](packages/session/session-persistence-sqlite/src/index.ts)
  - CUA 为自己私有一个 `ptcRuntime`：`const runtimeCtx = base.isolate('ptcRuntime')`，在其下加载 `NodePtcRuntime` 和 `inject: ['ptcRuntime']` 的初始化插件，不影响全局 `ptcRuntime` — [packages/experimental/cua/src/tool-registration.ts:149-156](packages/experimental/cua/src/tool-registration.ts)

`packages/session/session-persistence-sqlite/src/index.ts:42-56`（逐字摘录）

```ts
export async function apply(ctx: Context, config: Config): Promise<void> {
  let service!: Fiber
  ctx.effect(function* () {
    const resources = ctx.plugin({ name: 'session-segment-resources', apply() {} })
    yield resources.dispose
    service = ctx.plugin({
      name: 'session-persistence-sqlite-service',
      apply(serviceCtx: Context) {
        const legacy = new JsonlSessionPersistence(serviceCtx.isolate('sessionPersistence'), config)
        new SqliteSessionPersistence(serviceCtx, resources.ctx, config, legacy)
      },
    })
    yield service.dispose
  }, 'session-persistence-sqlite resources')
  await service.await()
```

#### 实验 4：两组看到不同 `shell`；共享 label；隔离但无提供方则静默 PENDING；两个反模式
脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services/04-isolate-pitfalls.mjs`

命令：

```sh
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services && node 04-isolate-pitfalls.mjs $LIB
```

输出（原样）：

```text
user-root sees root-shell$ ls
user-A sees A-shell$ ls
user-B sees B-shell$ ls
user-joined-label sees shared-shell$ ls
store keys: [ 'Symbol(shell)', 'Symbol(shell)', 'Symbol(shell)', 'Symbol(shared)' ]
--- pitfall: capture provider ctx in constructor
after caller disposed, bad.entries = [ 'x' ]
--- pitfall: destructured method loses `this`
destructured call threw: TypeError Cannot read properties of undefined (reading 'id')
```

解读：root、A、B、共享 label 四个槽位共存（store 有 4 个键，其中三个都叫 `Symbol(shell)` 但互不相等）；`user-isolated-without-provider` 没有输出——它在自己的新作用域里找不到 `shell`，即使根上有 `root-shell` 也不回退，保持 PENDING 且不报错。

### Inferences
- `isolate` 解决的是"扁平命名空间 + 每作用域单实现"的限制：不改名字、不改消费方代码，即可让不同子树拿到不同实现（或私藏一个内部实现）。
- 由于 Service 的事件 filter 也按隔离标签比较（service.ts:64-66），隔离同时隔开了该服务发出的事件。

### Gaps
- 未在实验中运行 loader + cordis.yml（实验安装中只有 cordis 与 cosmokit，没有 loader 包），yml 行为基于 loader 源码阅读与文档。

## Q6 可选依赖：`ctx.get` vs `ctx.inject(deps, cb)` vs 顶层 `inject`；`inject` 映射

### Takeaway
顶层 `inject`（数组或映射）是硬依赖：缺任一项整个插件 PENDING，且在有 runtime 的插件里**不声明就不能用 `ctx.x` 读取**。`ctx.get(name)` 是无依赖的即时探测（默认只返回 ACTIVE 提供方的值），不订阅变化。`ctx.inject(deps, cb)` 等价于 `ctx.plugin({ inject, apply: cb })`，创建一个子 fiber，只让这一部分随依赖出现/消失而加载/卸载。dsh 里的标准组合是"`ctx.get` 决定初始态 + `ctx.inject` 订阅变化"。

### Cited Findings
- `ctx.inject` 实现：`return this.plugin({ inject, apply: callback, name: callback.name })` — [vendor/cordis/src/registry.ts:300-302](vendor/cordis/src/registry.ts)；文档"每当某个必需服务发生变化时，系统都会卸载并重新运行该回调" — [docs/cordis-api/registry.zh.md:28](docs/cordis-api/registry.zh.md)
- 未 inject 读取会抛错的条件：def site 有 `fiber.runtime`（即不是根上下文）时走 `locate`，找不到即 `cannot get property "x" without inject`；根上下文（无 runtime）退化为 `ctx.reflect.get(prop, false)` 不检查 — [vendor/cordis/src/reflect.ts:185-202](vendor/cordis/src/reflect.ts)；实验 1 输出 `ctx.greeter threw: cannot get property "greeter" without inject`。
- 教程的可选依赖写法：跳过 `inject`，用 `ctx.get('greeter')?.greet(...)` — [docs/cordis-tutorial/03-services.zh.md:80-90](docs/cordis-tutorial/03-services.zh.md)；用户文档同样 — [docs/user/develop/framework/service.zh.md:89-100](docs/user/develop/framework/service.zh.md)
- 真实组合（tool-bash）：`ctx.get('jobs') === undefined` 时先注册前台版 bash 工具；`ctx.inject(['jobs'], (jobCtx) => {...})` 在 jobs 出现时撤下前台版、注册后台版，并在 `jobCtx.effect` 的清理里恢复前台版（仅当外层插件仍 ACTIVE）— [packages/shell/tool-bash/src/index.ts:588-602](packages/shell/tool-bash/src/index.ts)。该插件的硬依赖是 `inject = ['tools', 'shell', 'systemPrompt', 'shellEnv', 'workingDirectory']` — [packages/shell/tool-bash/src/index.ts:36](packages/shell/tool-bash/src/index.ts)

`packages/shell/tool-bash/src/index.ts:588-602`（逐字摘录）

```ts
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

- 服务类上的 `static inject` 同样是硬依赖：`ToolsService.inject = ['systemPrompt']`、`SystemPrompt.inject = ['sessionProjections']`、`LocalBashExecutor.inject = ['subprocess']` — [packages/core/tools/src/index.ts:100](packages/core/tools/src/index.ts)、[packages/core/system-prompt/src/index.ts:487](packages/core/system-prompt/src/index.ts)、[packages/shell/bash-local/src/index.ts:98](packages/shell/bash-local/src/index.ts)。服务的依赖消失 ⇒ 服务 fiber 卸载 ⇒ 服务本身被撤销 ⇒ 其消费方也连锁卸载。
- `inject` 映射形式（`{ name: config }`）见 Q4 与实验 5；`Plugin.Base.inject` 类型为 `Inject = (keyof M)[] | { [K in keyof M]?: M[K] }` — [vendor/cordis/src/registry.ts:19](vendor/cordis/src/registry.ts)、[vendor/cordis/src/registry.ts:105-106](vendor/cordis/src/registry.ts)。dsh 中 `ctx.get(` 出现 409 处（含测试外源码），远多于 `ctx.provide(`（39 处）。

### Inferences
- `ctx.get` 拿到的值也经过 `getTraceable`（reflect.ts:270），所以通过 `ctx.get('tools')?.register(...)` 注册的条目同样归属调用方 fiber；但 `ctx.get` 不建立 `dependents` 关系——提供方卸载时不会等待你，也不会让你重跑。长期持有 `ctx.get` 的返回值是危险的。
- 已有 lifecycle 文章 1106 行已讲 `ctx.inject` 可选依赖，正文可只引用 tool-bash 的"get + inject 双轨"作为新例子。

### Gaps
- 无。

## Q7 命名规则、扁平命名空间、`declare module` 类型声明

### Takeaway
每个隔离作用域内，一个名字只能有一个实现（重复即抛错）；名字还与 accessor/mixin 名（`on`、`emit`、`plugin`、`inject`、`get`、`provide`、`effect` 等）共用 `reflect.props` 表，冲突会抛错；以 `_` 开头、纯数字、`prototype`/`then` 和 symbol 名不会走服务解析；点号名（`remote.files`）可通过父服务的 `tracker.associate` 以 `ctx.remote.files` 访问。类型层面靠 `declare module '@deepseek-ai/cordis' { interface Context { tools: ToolsService } }` 声明合并，纯编译期。

### Cited Findings
- 教程："每个应用中的服务名称共用一个扁平命名空间。请为自有服务添加有辨识度的前缀" — [docs/cordis-tutorial/03-services.zh.md:92-94](docs/cordis-tutorial/03-services.zh.md)；声明合并"不会生成代码；没有该声明时，服务在运行时仍能工作，但消费方会失去类型安全" — [docs/cordis-tutorial/03-services.zh.md:39-40](docs/cordis-tutorial/03-services.zh.md)
- `isSpecialProperty`：symbol、`prototype`/`then`、数字串、`_` 前缀直接 `Reflect.get(target)`，不查服务 — [vendor/cordis/src/reflect.ts:80-91](vendor/cordis/src/reflect.ts)、[vendor/cordis/src/reflect.ts:178-180](vendor/cordis/src/reflect.ts)
- 内置 mixin 占用的名字：`get set provide accessor mixin`、`runtime effect`、`inject plugin`、`on once parallel emit serial bail waterfall` — [vendor/cordis/src/reflect.ts:255-258](vendor/cordis/src/reflect.ts)；`provide` 对已声明为 accessor 的名字抛 `property "x" is already declared as accessor` — [vendor/cordis/src/reflect.ts:315-319](vendor/cordis/src/reflect.ts)
- 内置服务名 `reflect registry events logger` 是 Context 实例的 own 属性 — [vendor/cordis/src/context.ts:79-83](vendor/cordis/src/context.ts)；get 陷阱对 `Reflect.has(target, prop)` 为真的属性直接返回（不走 locate），所以与 ctx 已有属性同名的服务读不到（推断，见下）。
- 点号服务：traceable get 中 `tracker.associate && useSite.reflect.props[`${associate}.${prop}`]` 时转读 `ctx['associate.prop']` — [vendor/cordis/src/utils.ts:184-188](vendor/cordis/src/utils.ts)；真实用法 `this.ctx.provide(`remote.${name}`, face)` — [packages/test-support/client-runtime/src/remote.ts:77](packages/test-support/client-runtime/src/remote.ts)
- 真实 `declare module` 例：tools — [packages/core/tools/src/index.ts:32-36](packages/core/tools/src/index.ts)；shell — [packages/shell/shell/src/index.ts:30-34](packages/shell/shell/src/index.ts)。tools 包同时用声明合并扩展其他包的接口（`@deepseek-ai/dsh-system-prompt` 的 `AssembleContext`、`@deepseek-ai/dsh-llm` 的 `MessageSourceMap`）— [packages/core/tools/src/index.ts:18-30](packages/core/tools/src/index.ts)

`packages/core/tools/src/index.ts:32-36`（逐字摘录）

```ts
declare module '@deepseek-ai/cordis' {
  interface Context {
    tools: ToolsService
  }
}
```

#### 实验 6：命名规则
脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services/06-names.mjs`

命令：

```sh
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-services && node 06-names.mjs $LIB
```

输出（原样）：

```text
provide('on') threw: property "on" is already declared as accessor
inside apply (fiber LOADING): ctx.get('_private') = undefined | ctx.get('_private', false) = { v: 1 }
fiber state: 2 (2 = ACTIVE; the throw was caught)
after ACTIVE: f.ctx._private = undefined | f.ctx.get('_private') = { v: 1 }
ctx.remote.files.list() = [ 'a.txt' ]
```

解读：`provide('on')` 抛错；`_private` 能 provide 进 store、能用 `ctx.get` 取到，但 `ctx._private` 读的是普通属性（undefined）；apply 内（fiber 仍 LOADING）严格 `ctx.get` 也拿不到自己刚提供的服务；`remote.files` 能以 `ctx.remote.files` 访问。

### Inferences
- harness 已占用大量通用名（`tools`、`llm`、`shell`、`agents`、`systemPrompt` 等），教程建议加前缀；`docs/subsystems/*` 中有自动生成的 `cordis-surface` 区块列出所有名字（[docs/cordis-tutorial/03-services.zh.md:94](docs/cordis-tutorial/03-services.zh.md)）。

### Gaps
- 未实验"服务名与 Context own 属性（如 `fiber`、`extend`）同名"的情形；按 reflect.ts:181-183 推断会被 own 属性遮蔽。

## Q8 真实 dsh 服务剖析：`tools`、`systemPrompt`、`shell`

### Takeaway
三种典型结构：(1) **注册表型**（`ToolsService`、`SystemPrompt`）——构造函数 `super(ctx, name)` + 用 `static inject` 声明依赖，内部用 `ScopedLayers` 存条目，每个 `register/section/...` 方法都 `this.layers.effect(this.ctx, ...)`，条目归属调用方 fiber 与 agent scope，变更时 emit `tools/change` / `system-prompt/change`；(2) **异步初始化型**（`SystemPrompt`、`WorkspaceRegistry`）——`[Service.init]` 中 await 外部资源，消费方在其完成后才启动；(3) **能力接缝型**（`shell`）——抽象基类固定服务名，具体实现（`dsh-bash-local`、`bash-sandbox`、`pwsh-*`）任选其一挂载，消费方（`dsh-tool-bash`）只 `inject: ['shell']`。

### Cited Findings
- `ToolsService`：`static inject = ['systemPrompt']`、`static Config`（已移除字段用 `z.never()`）、`layers = new ScopedLayers(..., () => this.ctx.emit('tools/change'))`、构造里 `super(ctx, 'tools')` 后向 systemPrompt 注册工具提供器 — [packages/core/tools/src/index.ts:99-118](packages/core/tools/src/index.ts)；`register` 校验后 `this.layers.effect(this.ctx, ...)` — [packages/core/tools/src/index.ts:128-152](packages/core/tools/src/index.ts)；`setExecutor` 用生成器 effect `yield this.ctx.systemPrompt.section(...)` 组合多个子注册 — [packages/core/tools/src/index.ts:326-350](packages/core/tools/src/index.ts)；`guard()` 同模式 — [packages/core/tools/src/index.ts:385-387](packages/core/tools/src/index.ts)
- 重复工具名报错文案提示使用 `agent.ctx` 做逐 agent 变体："tool \"x\" is already registered (for a per-agent variant, register through that agent's `agent.ctx` instead)" — [packages/core/tools/src/index.ts:67-70](packages/core/tools/src/index.ts)（这依赖于 Q3 的调用方追踪：`agent.ctx.tools.register()` 中 `this.ctx` 带 agent scope）。
- `SystemPrompt`：`static inject = ['sessionProjections']`，构造函数 `super(ctx, 'systemPrompt')` 后用自己的 `this.section(...)` 注册 harness 身份、persona 前后缀（这些条目归属 systemPrompt 自身 fiber），`[Service.init]` 中 `await this.ctx.sessionProjections.register(...)` — [packages/core/system-prompt/src/index.ts:486-536](packages/core/system-prompt/src/index.ts)

`packages/core/system-prompt/src/index.ts:509-536`（逐字摘录）

```ts
  constructor(ctx: Context, config: Config) {
    super(ctx, 'systemPrompt')
    this.toolOrder = validateToolOrder(config.toolOrder)
    // Keep harness-owned openers independent of the selected loop plugin.
    if (config.includeHarnessIdentity ?? true) {
      this.section({
        name: 'harness:identity',
        order: this.getSectionOrder('HARNESS_IDENTITY'),
        text: 'You are an AI agent powered by DeepSeek Harness.',
      })
    }
    this.section({
      name: PERSONA_PREFIX_SECTION,
      order: this.getSectionOrder('DEPLOYMENT_PERSONA_PREFIX'),
      // The fallback narrows the optional input type; the schema already defaults it.
      text: config.personaPrefix ?? '',
    })
    this.section({
      name: PERSONA_SUFFIX_SECTION,
      order: this.getSectionOrder('DEPLOYMENT_PERSONA_SUFFIX'),
      text: config.personaSuffix ?? '',
    })
    if (!(config.includeRuntimeContext ?? true)) this.suppressRuntimeContext()
  }

  protected async [Service.init](): Promise<void> {
    this.admission = await this.ctx.sessionProjections.register(sectionAdmissionsDefinition)
  }
```

- `shell`：见 Q1 的 ShellExecutor / LocalBashExecutor 摘录；`LocalBashExecutor` 的 Config 字段大量是 `.volatile()`（改配置不重载服务）— [packages/shell/bash-local/src/index.ts:100-107](packages/shell/bash-local/src/index.ts)；消费方 `dsh-tool-bash` 直接 `ctx.shell.resolve(...)` / `ctx.shell.execute(...)` / 读 `ctx.shell.sandboxMode` — [packages/shell/tool-bash/src/index.ts:251](packages/shell/tool-bash/src/index.ts)、[packages/shell/tool-bash/src/index.ts:568-571](packages/shell/tool-bash/src/index.ts)。shell 目录下的实现包：`bash-local bash-sandbox pwsh-local pwsh-sandbox` — `ls packages/shell`。
- 教程："卸载 Cordis 配置项 `dsh-bash-local`，挂载另一个 `shell` 提供方，所有注入 `'shell'` 的插件都会重新启动并使用新实现" — [docs/cordis-tutorial/03-services.zh.md:78](docs/cordis-tutorial/03-services.zh.md)（与实验 1 的 uid 变化 → 重跑一致；注意必须先卸载旧的，否则新提供方 FAILED）。

### Inferences
- 注册表型服务的"自动注销"完全来自 Cordis 的调用方追踪 + `ctx.effect`，服务代码中没有任何"plugin → entries"的映射表；`ScopedLayers` 只负责按 agent scope 分层和空层回收。
- `ToolsService` 依赖 `systemPrompt`：若 systemPrompt 被替换/重载，tools 服务也会重建，所有已注册工具的插件（inject 了 tools）随之重跑并重新注册——这正是"注册在 apply 中做、不在模块顶层做"的原因。

### Gaps
- 未逐一剖析 `llm` 服务（包 `packages/llm/*`），因篇幅聚焦在 tools/systemPrompt/shell。

## 插件作者的服务相关陷阱（汇总）

### Takeaway
多数坑来自"服务对象是带调用方上下文的代理"和"inject 是持续订阅"这两点。

### Cited Findings
1. **未 inject 就读 `ctx.x` 会抛错**（即使服务已加载）：`cannot get property "x" without inject` — [vendor/cordis/src/reflect.ts:185-198](vendor/cordis/src/reflect.ts)，实验 1。可选依赖用 `ctx.get` / `ctx.inject`。
2. **同一作用域重复提供会让后来者 FAILED，且旧的卸载后它不会自动接班** — [vendor/cordis/src/reflect.ts:325-327](vendor/cordis/src/reflect.ts)、[vendor/cordis/src/fiber.ts:655-656](vendor/cordis/src/fiber.ts)，实验 1。需要两个实现就用 `isolate`。
3. **在构造函数里保存 ctx 再在方法里用它注册**，条目会归属提供方 fiber，调用方卸载后不会撤销（实验 4：`after caller disposed, bad.entries = [ 'x' ]`）。方法里始终用 `this.ctx`。
4. **解构服务方法会丢 `this` 和调用方追踪**：`const { run } = ctx.shell; run()` → `TypeError`（实验 4）。原因：`createShadowMethod` 只在 `thisArg === receiver` 时替换为 shadow — [vendor/cordis/src/utils.ts:161-168](vendor/cordis/src/utils.ts)。
5. **`[Service.check]` 是拉模式**：状态变化后必须 `ctx.reflect.notify([name])`，否则消费方永远 PENDING（实验 2、5）；check 抛错被视为不可用并记日志 — [vendor/cordis/src/fiber.ts:628-634](vendor/cordis/src/fiber.ts)。
6. **缺依赖是静默的**：消费方 PENDING，不报错（实验 4 的 `user-isolated-without-provider`；教程也说明进程会以 0 退出 — [docs/cordis-tutorial/03-services.zh.md:72](docs/cordis-tutorial/03-services.zh.md)）。新隔离作用域不回退到父实现。
7. **`ctx.get` 默认严格**：提供方在 LOADING（包括 `[Service.init]` 期间、或自己 `apply` 内刚 provide）时返回 `undefined` — [vendor/cordis/src/reflect.ts:273-279](vendor/cordis/src/reflect.ts)，实验 6。且 `ctx.get` 不建立依赖、不保证之后仍可用。
8. **名字冲突**：不能用 `on/emit/plugin/inject/get/provide/effect/...` 等 mixin 名；`_` 前缀名不能经 `ctx._x` 读取 — [vendor/cordis/src/reflect.ts:80-91](vendor/cordis/src/reflect.ts)、[vendor/cordis/src/reflect.ts:255-258](vendor/cordis/src/reflect.ts)，实验 6。
9. **提供方卸载会等待依赖方 teardown**，若某依赖方 teardown 挂起，提供方的 `dispose()` 也挂起（[vendor/cordis/src/fiber.ts:738-741](vendor/cordis/src/fiber.ts)、[vendor/cordis/src/reflect.ts:340-343](vendor/cordis/src/reflect.ts)）；反过来，提供方不要在依赖方卸载完成前自行释放资源（已有 lifecycle 文章"服务提供方先释放了资源"一节详述）。
10. **`ctx.set` 只能由提供方 fiber 调用**，且（据代码推断）不触发消费方重跑 — [vendor/cordis/src/reflect.ts:290-301](vendor/cordis/src/reflect.ts)。
11. **服务自己的 `static inject` 消失会连带卸载服务和它的所有消费方**（如 systemPrompt → tools → 所有工具插件），注册必须写在 `apply`/构造函数里而非模块顶层，才能在重建后自动恢复。

### Inferences
- 一句话规则：**在 `apply` 里通过 `ctx.<service>.<method>()` 调用注册类 API，不保存 disposer 也会自动撤销；需要可选能力时 `ctx.get` 定初值、`ctx.inject` 订阅变化。**

### Gaps
- 未覆盖 HMR 场景下服务热替换的时序（属第 6 章内容）。
