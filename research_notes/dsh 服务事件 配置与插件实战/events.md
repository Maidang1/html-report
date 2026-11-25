# Cordis / dsh 事件系统（教程第 04 章「事件」）源码研究笔记

> 基准：dsh 仓库 commit `4b399c3`（`git log -1` = `4b399c39 Merge pull request #8961 ...`），vendor 的 Cordis 源码在 `vendor/cordis/src/`；实验运行在同版本 npm 包 `@deepseek-ai/cordis@4.2.0-alpha.1` 的 `lib/index.js` 上，Node v24.22.2。所有 `path:line` 均相对于 dsh 仓库根目录。代码片段用 `sed -n a,bp` 脚本原样复制。实验脚本在 `/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events/`。
>
> 读者已经理解 Fiber 生命周期与 `ctx.effect`（见 `deepseek/dsh_lifecycle_and_effects.md`），本笔记不再解释。已有文章 `deepseek/dsh_plugin_guide.md:271-278` 讲过分发模式，但写的是「四种」且漏掉了 `parallel`，本笔记给出完整的五种并以源码为准。
>
> 实验前置事实（`00-timing.mjs`）：`ctx.plugin()` 返回的 Fiber 不会同步执行 `apply()`，必须先 `await fiber`（Fiber 被包装成 thenable，`vendor` 编译产物 `lib/index.js:1857-1861`）或 `await fiber.await()`，监听器才注册上。最初的实验版本没有 await，导致所有事件都「没有监听器」。后面所有实验都已 await。
>
> 脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events/00-timing.mjs`

```js
// Is plugin apply() synchronous? (explains why listeners must be awaited before emitting)
const { Context } = await import(process.argv[2])
const root = new Context()
const fiber = root.plugin({ name: 't', apply(ctx) { console.log('  apply() runs'); ctx.on('t/x', () => console.log('  listener fired')) } })
console.log('right after root.plugin(): emit'); root.emit('t/x')
await fiber.await()
console.log('after await fiber.await(): emit'); root.emit('t/x')
```

命令：

```
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events && node 00-timing.mjs /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/cordis-lab/node_modules/@deepseek-ai/cordis/lib/index.js
```

输出：

```
right after root.plugin(): emit
  apply() runs
after await fiber.await(): emit
  listener fired
```

## 1. 有哪些分发模式，语义分别是什么，harness 何时选哪种

### Takeaway
4.2.0-alpha.1 恰好有五种分发模式：`emit`、`parallel`、`serial`、`bail`、`waterfall`（`vendor/cordis/src/events.ts:31`），没有其他模式。它们的差别在于：是否等待、是否收集返回值、能否短路、出错时怎样传播。harness 自有事件里 `emit` 最多（观察通知），`waterfall` 第二多（策略、拦截、包装），`parallel` 用于需要全部等完的扇出（flush、归档），`serial`/`bail` 很少用。

### Cited Findings

- 模式类型与 bail 判定。`isBailed` 只把 `null`、`false`、`undefined` 视为「继续」，**`0`、`''`、`NaN` 都算 bail 值**。—— `vendor/cordis/src/events.ts:12-14`、`:31`

`vendor/cordis/src/events.ts:12-14`

```ts
export function isBailed(value: any) {
  return value !== null && value !== false && value !== undefined
}
```

`vendor/cordis/src/events.ts:31-31`

```ts
export type DispatchMode = 'emit' | 'parallel' | 'serial' | 'bail' | 'waterfall'
```

- 五种模式的完整实现（`_resolve` 见第 4 节）：

`vendor/cordis/src/events.ts:187-265`

```ts
  /**
   * Run listeners concurrently and wait for all of them.
   *
   * @param args — optional `this`, the event name, then listener arguments.
   * @returns a promise resolving once every listener has settled.
   */
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

  /**
   * Compose listeners around the final `next` callback.
   *
   * The last dispatch argument is treated as the innermost `next`. Listeners
   * run outermost-first; a listener that does not call `next()` vetoes the
   * rest of the chain, including the built-in behavior.
   *
   * @param args — optional `this`, the event name, listener arguments, then `next`.
   * @returns the outermost listener's return value.
   */
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

- 对比表（由上面源码 + 实验 02/03/04 得出）：

| 模式 | 调用 | 同步/异步 | 返回值 | 短路 | 监听器同步 throw | 监听器 async reject | 源码 |
|---|---|---|---|---|---|---|---|
| `emit` | `ctx.emit(name, ...args)` | 同步循环，不 await 返回的 promise | 永远 `undefined` | 否 | **向调用方抛出，后续监听器不运行** | 被丢弃，成为进程级 unhandledRejection | `events.ts:207-210` |
| `parallel` | `await ctx.parallel(...)` | 全部并发启动，`Promise.allSettled` 等全部结束 | `undefined` | 否 | 每个监听器都在 `async` 包装里，所以都会运行；最后抛 `AggregateError` | 同左 | `events.ts:193-200` |
| `serial` | `await ctx.serial(...)` | 依次 await | 第一个 bail 值 | 是 | 调用 promise reject，后续不运行 | 同左 | `events.ts:218-224` |
| `bail` | `ctx.bail(...)` | 同步依次调用，**不 await** | 第一个 bail 值（async 监听器返回的 Promise 本身就是 bail 值） | 是 | 抛给调用方，后续不运行 | 不处理 | `events.ts:232-238` |
| `waterfall` | `ctx.waterfall(..., inner)` | 由监听器决定（返回 `next()` 的结果） | 最外层监听器的返回值 | 不调 `next()` 即否决 | 沿调用链抛出 | 沿 promise 链 reject | `events.ts:250-265` |

- 教程的模式表（与源码一致，但没讲错误行为）：—— `docs/cordis-tutorial/04-events.zh.md:80-90`

`docs/cordis-tutorial/04-events.zh.md:84-90`

~~~~
| 模式 | 调用 | 语义 |
|---|---|---|
| emit | `ctx.emit(name, ...args)` | 同步广播；不会等待或收集返回的 promise 与值。 |
| parallel | `await ctx.parallel(name, ...args)` | 所有监听器并发运行，并一同等待。 |
| serial | `await ctx.serial(name, ...args)` | 监听器按顺序运行并等待；第一个非 `null`/`false`/`undefined` 返回值胜出，并停止后续监听器。 |
| bail | `ctx.bail(name, ...args)` | serial 的同步版本。 |
| waterfall（瀑布式事件） | `ctx.waterfall(name, ...args, next)` | 环绕中间件，见下文。 |
~~~~

- primer 的模式表与「只能通过对应方法分发」「`@mode` 标签是公开约定」：—— `docs/cordis-primer.zh.md:19-29`

`docs/cordis-primer.zh.md:19-29`

~~~~
每个事件具有以下分发模式之一，且只能通过对应方法分发。

| 模式 | 是否 await？ | 分发顺序 | 是否有返回值？ |
|---|---|---|---|
| `emit` | 否 | 监听器按注册顺序观察 | 否 |
| `waterfall` | 否 | 监听器按注册顺序观察 | 是 |
| `parallel` | 是 | 所有监听器并行观察事件 | 否 |
| `serial` | 是 | 监听器按注册顺序观察 | 是 |
| `bail` | 否 | 监听器按注册顺序观察，直到某个监听器返回 bail 值 | 是 |

分发模式是事件公开约定的一部分。新的 harness 事件通过 `@mode` 标签记录模式，以便生成的目录可以将声明与分发调用点做交叉校验。
~~~~

- harness 的选择统计（生成的矩阵 `docs/event-producer-consumer.zh.md:12-115`，按 Mode 列计数）：`emit` 66、`waterfall` 22、`parallel` 9、`serial` 2（`agent/created`、`agent/turn-stopping`，行 18、27）、`bail` 1（`webserver/authorize`，行 98）。
- 典型选择：
  - 观察通知用 `emit`：`tools/result`、`tools/change`、`session/created|disposed|event`、`agent/status|error|assistant-stream`（`docs/event-producer-consumer.zh.md:90,95,77-79,20`）。
  - 可协作的决策/拦截用 `waterfall`：`tools/pre-execute|execute|post-execute`、`agent/pre-step`、`agent/request`、`approval/request`、`llm/stream`、`system-prompt/assemble`、`webserver/request`（同文件 91-93、24、122、34、65、87、101）。
  - 必须等所有监听器完成的扇出用 `parallel`：`session/flush`、`workspace/session-stop`、`feedback/committed`（行 80、112、51）。
  - 需要按顺序 await 的生命周期边界用 `serial`：`agent/created`（`packages/core/agent/src/index.ts:575`）、`agent/turn-stopping`（`packages/core/agent-loop/src/agent.ts:392`）。

`packages/core/agent-loop/src/agent.ts:391-394`

```ts
        if (turnEnds && this.inbox.nextStep.length === 0) {
          await this.dispatch.serial('agent/turn-stopping', { turn, signal })
          signal.throwIfAborted()
        }
```

- 实验 02：各模式返回值、`0` 也是 bail 值、serial 串行/parallel 并发/emit 不等待、bail 对 async 监听器直接返回第一个 Promise：

脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events/02-modes.mjs`

```js
// Return values and short-circuit for each dispatch mode
const { Context } = await import(process.argv[2])
const sleep = (ms) => new Promise(r => setTimeout(r, ms))
const root = new Context()
const t0 = Date.now(); const ts = () => String(Date.now() - t0).padStart(3) + 'ms'
await root.plugin({
  name: 'modes',
  apply(ctx) {
    ctx.on('m/x', (v) => { console.log(`  L1 sees ${v}`); return undefined })
    ctx.on('m/x', (v) => { console.log(`  L2 sees ${v}`); return 0 })        // 0 is a bail value!
    ctx.on('m/x', (v) => { console.log(`  L3 sees ${v}`); return 'L3' })
    ctx.on('m/async', async (tag) => { console.log(`  ${ts()} A start (${tag})`); await sleep(30); console.log(`  ${ts()} A end`); return 'A' })
    ctx.on('m/async', async (tag) => { console.log(`  ${ts()} B start (${tag})`); await sleep(10); console.log(`  ${ts()} B end`); return 'B' })
  },
})
console.log('emit  ->', root.emit('m/x', 'emit'))
console.log('bail  ->', root.bail('m/x', 'bail'))
console.log('serial->', await root.serial('m/x', 'serial'))
console.log('--- serial on async listeners'); console.log('serial ->', await root.serial('m/async', 'serial'))
console.log('--- parallel on async listeners'); console.log('parallel ->', await root.parallel('m/async', 'parallel'))
console.log('--- emit on async listeners (not awaited)'); const r = root.emit('m/async', 'emit'); console.log(`  ${ts()} emit returned`, r); await sleep(50)
console.log('--- bail on async listeners (first Promise is a bail value)'); const b = root.bail('m/async', 'bail'); console.log('  bail ->', b, '=', await b); await sleep(50)
```

命令：

```
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events && node 02-modes.mjs /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/cordis-lab/node_modules/@deepseek-ai/cordis/lib/index.js
```

输出：

```
  L1 sees emit
  L2 sees emit
  L3 sees emit
emit  -> undefined
  L1 sees bail
  L2 sees bail
bail  -> 0
  L1 sees serial
  L2 sees serial
serial-> 0
--- serial on async listeners
    3ms A start (serial)
   34ms A end
serial -> A
--- parallel on async listeners
   34ms A start (parallel)
   34ms B start (parallel)
   44ms B end
   66ms A end
parallel -> undefined
--- emit on async listeners (not awaited)
   66ms A start (emit)
   67ms B start (emit)
   67ms emit returned undefined
   77ms B end
   98ms A end
--- bail on async listeners (first Promise is a bail value)
  117ms A start (bail)
  147ms A end
  bail -> Promise { 'A' } = A
```

### Inferences
- `bail` 只适合同步监听器：任何 `async` 监听器返回的 Promise 都是 truthy，必然在第一个 async 监听器处短路（实验 02 最后一段）。
- `serial` 对监听器返回 `0`/`''` 也会停止；需要「数值结果」的事件不应用 serial/bail 返回数值。
- primer 表里 waterfall「是否 await：否」(`docs/cordis-primer.zh.md:24`) 指 Cordis 自己不 await；harness 的 waterfall 事件几乎都声明为返回 Promise，实际是异步链。

### Gaps
- 没有 priority/权重参数，顺序只由注册顺序和 `prepend` 决定（见第 2 节）；源码中未发现其他模式。

## 2. 监听器注册与顺序：`on` / `once` / `prepend` / `global` / disposer

### Takeaway
`ctx.on` 先 `assertActive`，用 `ctx.reflect.bind` 包一层代理，再用 `bail` 分发 `internal/listener` 让内部钩子有机会接管注册，最后通过 `fiber.effect` 把 hook 推进 `_hooks[name]` 数组：默认 `push`，`prepend` 用 `unshift`，因此后注册的 prepend 排在先注册的 prepend 之前。没有优先级。监听器随 Fiber 卸载自动移除；已卸载的 ctx 再 `on` 会抛 `INACTIVE_EFFECT`。

### Cited Findings
- 选项只有 `prepend` 和 `global`：

`vendor/cordis/src/events.ts:110-122`

```ts
/** Options accepted by `ctx.on()` and `ctx.once()`. */
export interface EventOptions {
  /** Add the listener before existing listeners for the same event. */
  prepend?: boolean
  /** Receive the event regardless of context filter checks. */
  global?: boolean
}

/** Registered listener record stored by the event service. */
export interface Hook extends EventOptions {
  ctx: Context
  callback: (...args: any[]) => any
}
```

- 注册与注销路径：

`vendor/cordis/src/events.ts:267-327`

```ts
  private register(label: string, name: string | symbol, callback: any, options: EventOptions): () => void {
    const method = options.prepend ? 'unshift' : 'push'
    return this.ctx.fiber.effect(() => {
      const hooks = this._hooks[name] ??= []
      hooks[method]({ ctx: this.ctx, callback, ...options })
      return () => this.unregister(name, callback)
    }, label)
  }

  private unregister(name: string | symbol, callback: any) {
    const hooks = this._hooks[name]
    if (!hooks) return
    const index = hooks.findIndex(hook => hook.callback === callback)
    if (index >= 0) {
      hooks.splice(index, 1)
      if (!hooks.length) delete this._hooks[name]
      return true
    }
  }

  /**
   * Register an event listener owned by the current fiber.
   *
   * The listener is removed automatically when the fiber unloads. Throws
   * `CordisError('INACTIVE_EFFECT')` if the fiber is already disposed.
   *
   * @param name — the event name to listen for.
   * @param listener — called with the dispatch arguments.
   * @param options — listener options; a boolean is shorthand for `prepend`.
   * @returns a disposer removing the listener; `true` if it was still registered.
   */
  on(name: string | symbol, listener: (...args: any) => any, options?: boolean | EventOptions) {
    if (typeof options !== 'object') {
      options = { prepend: options }
    }

    // handle special events
    this.ctx.fiber.assertActive()
    listener = this.ctx.reflect.bind(listener)
    const result = this.bail(this.ctx, 'internal/listener', name, listener, options)
    if (result) return result

    const label = `ctx.on(${typeof name === 'string' ? JSON.stringify(name) : name.toString()})`
    return this.register(label, name, listener, options)
  }

  /**
   * Register an event listener that disposes itself after the first call.
   *
   * @param name — the event name to listen for.
   * @param listener — called at most once with the dispatch arguments.
   * @param options — listener options; a boolean is shorthand for `prepend`.
   * @returns a disposer removing the listener; `true` if it was still registered.
   */
  once(name: string | symbol, listener: (...args: any) => any, options?: boolean | EventOptions) {
    const dispose = this.on(name, function (...args: any[]) {
      dispose()
      return listener.apply(this, args)
    }, options)
    return dispose
  }
```

  - `options` 若是布尔值则视为 `{ prepend: options }`（`events.ts:299-301`）。
  - `ctx.reflect.bind(listener)` 返回一个 Proxy，调用时把 `this` 与参数都过一遍 `trace`（`vendor/cordis/src/reflect.ts:448-457`），所以 `unregister` 比较的 `callback` 是这个代理，而不是你传入的原函数（`events.ts:305`、`:279`）。
  - `register` 用 `this.ctx.fiber.effect(...)` 注册，effect 的清理函数调用 `unregister`（`events.ts:269-273`）。
  - `once` 只是包装：第一次调用时先 `dispose()` 再调用原监听器（`events.ts:321-327`）。

`vendor/cordis/src/reflect.ts:448-457`

```ts
  bind<T extends Function>(callback: T) {
    return new Proxy(callback, {
      apply: (target, thisArg, args) => {
        return Reflect.apply(target, this.trace(thisArg), args.map(arg => this.trace(arg)))
      },
      construct: (target, args, newTarget) => {
        return Reflect.construct(target, args.map(arg => this.trace(arg)), newTarget)
      },
    })
  }
```

- 实验 01：顺序、`prepend`、布尔简写、`once`、disposer 返回值、`this`：

脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events/01-order.mjs`

```js
// Listener ordering, prepend, once, disposer return value, `this` binding
const { Context } = await import(process.argv[2])
const root = new Context()
const out = []
await root.plugin({
  name: 'order',
  apply(ctx) {
    ctx.on('demo/e', () => out.push('A (on #1)'))
    ctx.on('demo/e', () => out.push('B (on #2)'))
    ctx.on('demo/e', () => out.push('P (on #3, prepend:true)'), { prepend: true })
    ctx.on('demo/e', () => out.push('Q (on #4, options=true shorthand)'), true)
    ctx.once('demo/e', () => out.push('O (once #5)'))
    const off = ctx.on('demo/e', () => out.push('X (disposed before emit)'))
    console.log('off() first call  ->', off())
    console.log('off() second call ->', off())
    ctx.on('demo/this', function () { console.log('this with no thisArg   ->', this) })
    ctx.on('demo/this', function (p) { console.log('this with thisArg      ->', this, '| arg ->', p) })
  },
})
root.emit('demo/e'); console.log('emit #1:', out.join(', ')); out.length = 0
root.emit('demo/e'); console.log('emit #2:', out.join(', '))
root.emit('demo/this', { payload: 1 })
root.emit({ who: 'carrier' }, 'demo/this', { payload: 2 })
console.log('remaining hooks for demo/e:', root.events._hooks['demo/e'].length)
```

命令：

```
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events && node 01-order.mjs /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/cordis-lab/node_modules/@deepseek-ai/cordis/lib/index.js
```

输出：

```
off() first call  -> undefined
off() second call -> undefined
emit #1: Q (on #4, options=true shorthand), P (on #3, prepend:true), A (on #1), B (on #2), O (once #5)
emit #2: Q (on #4, options=true shorthand), P (on #3, prepend:true), A (on #1), B (on #2)
this with no thisArg   -> null
this with thisArg      -> null | arg -> { payload: 1 }
this with no thisArg   -> { who: 'carrier' }
this with thisArg      -> { who: 'carrier' } | arg -> { payload: 2 }
remaining hooks for demo/e: 4
```

  - 顺序为 `Q, P, A, B, O`：两个 prepend 都排在普通监听器前，后注册的 Q 在先注册的 P 前（unshift）。
  - `once` 的 O 只出现在第一次 emit。
  - **与文档不符**：`docs/cordis-api/events.zh.md:147` 与 `events.ts:94-96` 说 disposer「如果监听器仍注册则返回 `true`」，类型为 `() => boolean`；实测两次调用都返回 `undefined`。原因是 `register` 返回的是 `fiber.effect` 的 disposer（`events.ts:269`），其签名是 `Disposable<Promise<void>>`（`vendor/cordis/src/fiber.ts:443`），`unregister` 的 `true` 没有被透传。

- 实验 05（前半）：插件卸载时监听器自动移除，卸载后再 `ctx.on` 抛 `INACTIVE_EFFECT`；（中段）`internal/dispatch`；（后半）`Context.filter` 与 `global`：

脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events/05-lifecycle-filter.mjs`

```js
// listener auto-removal on dispose, INACTIVE_EFFECT, internal/dispatch, filter + global
const { Context } = await import(process.argv[2])
const root = new Context()
let saved
const fiber = root.plugin({ name: 'p', apply(ctx) { saved = ctx; ctx.on('life/x', () => console.log('  p listener fired')) } })
await fiber.await?.()
console.log('before dispose hooks =', root.events._hooks['life/x']?.length); root.emit('life/x')
await fiber.dispose()
console.log('after dispose hooks  =', root.events._hooks['life/x']?.length); root.emit('life/x')
try { saved.on('life/x', () => {}) } catch (e) { console.log('ctx.on after dispose ->', e.name, e.code ?? '', e.message) }

// internal/dispatch: observes every non-internal dispatch, and a throw vetoes it
await root.plugin({ name: 'spy', apply(ctx) {
  ctx.on('internal/dispatch', (mode, name, args, thisArg) => {
    console.log(`  [internal/dispatch] mode=${mode} name=${String(name)} args=${JSON.stringify(args)} thisArg=${thisArg ? 'object' : thisArg}`)
    if (name === 'guarded/x') throw new Error('guard vetoed guarded/x')
  })
  ctx.on('guarded/x', () => console.log('  guarded listener fired (should not)'))
  ctx.on('plain/x', () => console.log('  plain listener fired'))
}})
root.emit('plain/x', 1)
await root.serial('plain/x', 2)
try { root.emit('guarded/x') } catch (e) { console.log('emit guarded/x threw ->', e.message) }

// Context.filter on thisArg + global option
const r = new Context()
let ctxA
await r.plugin({ name: 'a', apply(ctx) { ctxA = ctx; ctx.on('f/x', () => console.log('  listener in ctx A')) } })
await r.plugin({ name: 'b', apply(ctx) {
  ctx.on('f/x', () => console.log('  listener in ctx B'))
  ctx.on('f/x', () => console.log('  listener in ctx B (global:true)'), { global: true })
}})
const onlyA = { [Context.filter](listenerCtx) { return listenerCtx === ctxA } }
console.log('dispatch with filter admitting only ctx A:'); r.emit(onlyA, 'f/x')
console.log('dispatch without thisArg:'); r.emit('f/x')
```

命令：

```
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events && node 05-lifecycle-filter.mjs /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/cordis-lab/node_modules/@deepseek-ai/cordis/lib/index.js
```

输出：

```
before dispose hooks = 1
  p listener fired
after dispose hooks  = undefined
ctx.on after dispose -> Error INACTIVE_EFFECT cannot create effect on inactive context
  [internal/dispatch] mode=emit name=plain/x args=[1] thisArg=null
  plain listener fired
  [internal/dispatch] mode=serial name=plain/x args=[2] thisArg=null
  plain listener fired
  [internal/dispatch] mode=emit name=guarded/x args=[] thisArg=null
emit guarded/x threw -> guard vetoed guarded/x
dispatch with filter admitting only ctx A:
  listener in ctx A
  listener in ctx B (global:true)
dispatch without thisArg:
  listener in ctx A
  listener in ctx B
  listener in ctx B (global:true)
```

- `global: true` 的含义仅是「跳过 thisArg 的 filter 检查」（`events.ts:170`）。实验 05 最后一段：filter 只接受 ctx A，但 ctx B 中 `global: true` 的监听器仍收到事件。
- harness 中的 `prepend` 用法：`tool-jobs` 的 pre-execute 记录输出上限并 `return next()`（`packages/jobs/tool-jobs/src/index.ts:287-291`）；`spill-policy` 的 post-execute 以 prepend 放在最外层，以便看到所有下游决策后再裁剪内容（`packages/spill/spill-policy/src/index.ts:134-151`）。primer 要求「仅当监听器必须在普通注册之前运行时才使用 `prepend: true`」（`docs/cordis-primer.zh.md:37`）。

`packages/jobs/tool-jobs/src/index.ts:286-291`

```ts
  const outputLimits = new WeakMap<ToolExecution, number>()
  ctx.on('tools/pre-execute', (exec, next) => {
    const maxBytes = visibleOutputLimit(ctx, exec)
    if (maxBytes !== undefined) outputLimits.set(exec, maxBytes)
    return next()
  }, { prepend: true })
```

- `user-approval` 明确指出 prepend 的竞争性：任何插件都能 prepend 到门禁监听器之前，所以「never」策略不能写成监听器，而是在分发前由服务自己判定：

`packages/interaction/user-approval/src/index.ts:297-311`

```ts
    // The 'never' policy is decided HERE, before any dispatch: a listener
    // registered with `prepend: true` after this service mounts would sit
    // ahead of any gate LISTENER, so a listener-shaped gate cannot keep the
    // documented promise that 'never' rejects deterministically regardless
    // of registration order — only the service's own request path can.
    if (this.effectivePolicy(session) === 'never') return 'rejected'
    // Enter the promise chain BEFORE dispatching: a listener that throws
    // SYNCHRONOUSLY (before its first await) must land in the same rejection
    // path as an async one — `Promise.resolve(call())` would let it escape
    // the containment into the caller.
    const answer: Promise<ApprovalOutcome> = Promise.resolve().then(
      () => this.ctx.waterfall(
        scopeTarget(req.agent, req.agent), 'approval/request', req,
        () => Promise.resolve<ApprovalOutcome>('unavailable'),
      ),
```

### Inferences
- 因为顺序只由「注册时间 + prepend」决定，而插件激活顺序取决于依赖和异步加载，不应假设跨插件的监听器顺序；需要确定性的策略应放在服务代码路径里（如上面的 approval `never`），或用 `ctx.tools.guard()` 这样的单调守卫（`docs/cookbook/extension-cookbook.zh.md:35`）。
- 想手动提前移除监听器时，用 `ctx.on` 返回的 disposer；不要依赖它的返回值判断是否曾注册。

### Gaps
- 没有核实 `fiber.effect` 的同步 disposer 在所有路径下都返回 `undefined`；只观测到 `on` 的情况。

## 3. waterfall 的 `next()`：组合方式、否决、多次调用

### Takeaway
`waterfall` 把最后一个参数当作最内层的默认行为 `inner`，然后从第一个监听器开始，每个监听器收到 `(...args, next)`；`next()` 调用下一个监听器，最后调用 `inner()`。监听器不调用 `next()` 就是否决，`inner` 不会运行；同一个 `next` 调用两次会同步抛 `next() called multiple times`。返回值是最外层监听器的返回值，所以「调了 next 却忘了 return」会让调用方拿到 `undefined`。

### Cited Findings
- 实现：`events.ts:250-265`（见第 1 节完整摘录）。关键点：`inner = args.pop()`（`:252`）、无剩余监听器时 `return inner()`（`:255`）、`called` 守卫（`:256-259`）。
- 教程的示例与纪律：—— `docs/cordis-tutorial/04-events.zh.md:94-140`

`docs/cordis-tutorial/04-events.zh.md:136-140`

~~~~
按顺序看第二行如何产生：监听器 1 先运行并调用 `next()`，从而调用监听器 2；监听器 2 看到 `blocked` 后直接返回而不调用 `next()`，因此最内层默认逻辑（传给 `ctx.waterfall` 的函数）从未运行；返回途中，监听器 1 再把替换消息转换为大写。

由此得到一项纪律：**只负责观察或标注的 waterfall 监听器必须调用 `next()`**；不调用就直接返回代表有意短路。如果日志监听器忘记调用 `next()`，会悄无声息地吞掉所有下游的默认行为。这是本仓库的常设规则（[waterfall 语义](../cordis-primer.zh.md#cordis-waterfall-semantics)）。

harness 使用 waterfall 处理协作插件可以包装或回答的决策：[`agent/pre-step`](../subsystems/core.zh.md#agentpre-step--waterfall) 允许插件拒绝拟议步骤或替换其输入，[`approval/request`](../subsystems/approval.zh.md#approvalrequest--waterfall) 允许策略代替用户作答。
~~~~

- primer 的 waterfall 语义：协作者修改共享请求/决策对象后委托，也可整体替换结果：

`docs/cordis-primer.zh.md:33-39`

~~~~
## Cordis Waterfall 语义

`ctx.waterfall` 是环绕中间件。监听器接收 `(...args, next)`。调用 `next()` 会执行下游监听器；下游返回值通过 `next()` 返回当前包装层，可由该层包装后继续向外返回。不调用 `next()` 直接返回则短路。

协作式监听器通常修改一个共享的请求或决策对象，然后委托。监听器也可以选择完全替换结果，下游监听器将只看到替换后的结果。仅当监听器必须在普通注册之前运行时才使用 `prepend: true`。

对于单决策事件，短路是设计意图。策略监听器在拥有决策权时可以不调用 `next()` 直接返回，而仅做标注或观察的监听器则必须委托。
~~~~

- 实验 03：

脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events/03-waterfall.mjs`

```js
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

命令：

```
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events && node 03-waterfall.mjs /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/cordis-lab/node_modules/@deepseek-ai/cordis/lib/index.js
```

输出：

```
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

  - 无监听器时 `waterfall` 直接返回 `inner()`。
  - 洋葱顺序：`P → L1 → L2 → INNER → L2 after → L1 after`，外层可以变换内层结果（`inner:hi` → `inner:hi!` → `INNER:HI!`）。
  - L2 否决时 INNER 不运行，L1 仍然在返回路径上把否决结果大写化，与教程 `04-events.zh.md:136` 描述一致。
  - 「调 `next()` 但没 return」返回 `undefined`。在 `tools/pre-execute` 里这会让 `gate.kind` 读 `undefined` 抛 TypeError，被 `prepare` 的 catch 转成工具错误结果（`packages/core/tools/src/pipeline.ts:204`、`:231-232`）；这是推断，没有专门实验。

- `internal/update` 也是 waterfall：Fiber 更新配置时以 `fiber` 为 thisArg、以 `restart` 为 inner 分发；监听器不调用 `next()` 就不重启（`vendor/cordis/src/fiber.ts:827-834`）。

`vendor/cordis/src/fiber.ts:817-834`

```ts
    const restart = () => {
      restarted = true
      return fiber.restart()
    }
    const publish = () => {
      if (!restarted && fiber.uid !== null && fiber._configRevision === configRevision
        && fiber._runner.epoch === epoch && fiber.committed === committed) {
        fiber._effectiveConfig = resolved
      }
    }
    const result = loaded
      ? fiber.context.waterfall(fiber, 'internal/update', resolved, restart)
      : restart()
    // a listener may veto the restart, in which case there is nothing to await
    if (result === undefined) {
      publish()
      return
    }
```

- 实验 06：插件用非 global 的 `internal/update` 监听器对「只改 level」做原地更新，否决重启；改 port 时 `next()` 重启：

脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events/06-internal-update.mjs`

```js
// internal/update waterfall: a plugin vetoes its own restart for a hot-reloadable config change
const { Context } = await import(process.argv[2])
const root = new Context()
let applies = 0
const fiber = await root.plugin({
  name: 'hot',
  apply(ctx, config) {
    applies++
    console.log(`  apply #${applies} config=${JSON.stringify(config)}`)
    ctx.on('internal/update', (next_config, next) => {
      if (next_config.port === config.port) {
        console.log(`  internal/update: only level changed -> apply in place, NOT calling next()`)
        config.level = next_config.level
        return
      }
      console.log('  internal/update: port changed -> next() restarts the fiber')
      return next()
    })
    ctx.on('internal/dispatch', () => console.log('  (internal/dispatch should not see internal/* events)'))
  },
}, { port: 1, level: 'info' })
const f = fiber
console.log('update level ->'); await f.update({ port: 1, level: 'debug' })
console.log('applies =', applies)
console.log('update port  ->'); await f.update({ port: 2, level: 'debug' })
console.log('applies =', applies)
```

命令：

```
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events && node 06-internal-update.mjs /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/cordis-lab/node_modules/@deepseek-ai/cordis/lib/index.js
```

输出：

```
  apply #1 config={"port":1,"level":"info"}
update level ->
  internal/update: only level changed -> apply in place, NOT calling next()
applies = 1
update port  ->
  internal/update: port changed -> next() restarts the fiber
  apply #2 config={"port":2,"level":"debug"}
applies = 2
```

### Inferences
- 观察/标注型 waterfall 监听器的模板就是 `const r = await next(); ...; return r`（如 `packages/guard/repeat-tool-reminder/src/index.ts:218-223`）；拥有决策权的监听器才可不调 `next()`。
- waterfall 不提供错误隔离：任意一层 throw 都会冒泡到 producer，producer 必须自己 catch（tools pipeline 的 `prepare`/`dispatch` 都有 try/catch，第 9 节）。

### Gaps
- 未测：async 监听器在 `await` 之后才调用两次 `next()`，此时错误以 rejection 形式出现在该监听器自己的 promise 中（由代码推断）。

## 4. `_resolve` 的 thisArg 规则、`Context.filter` 与 dsh 的 scope carrier

### Takeaway
所有分发方法都先走 `_resolve`：**如果第一个参数是 object 或 function，就把它当成 thisArg 拿掉**，下一个参数才是事件名。thisArg 有两个用途：作为监听器的 `this`，以及通过它上面的 `[Context.filter]` 方法筛选哪些 ctx 的监听器能收到事件（`global` 监听器除外）。dsh 用 `scopeTarget()` 构造只带 filter 的 carrier，实现「agent 作用域内的监听器只收到本 agent 的事件」。

### Cited Findings

`vendor/cordis/src/events.ts:162-185`

```ts
  private _resolve(type: string, args: any[]) {
    const thisArg = typeof args[0] === 'object' || typeof args[0] === 'function' ? args.shift() : null
    const name: string | symbol = args.shift()
    if ((typeof name !== 'string' || !name.startsWith('internal/')) && this._hooks['internal/dispatch']?.length) {
      this.emit('internal/dispatch', type, name, args, thisArg)
    }
    const filter = thisArg?.[Context.filter]
    const callbacks = (this._hooks[name] || [])
      .filter(hook => hook.global || !filter || filter.call(thisArg, hook.ctx))
      .map(hook => hook.callback)
    return [thisArg, callbacks, name] as const
  }

  /**
   * Resolve bound callbacks using the event's context filter.
   * @param type Dispatch mode reported to observers.
   * @param args Dispatch arguments, consumed through the event name.
   * @returns Matching callbacks bound to the dispatch receiver.
   * DSH retains callback enumeration for per-listener error containment.
   */
  dispatch(type: string, args: any[]) {
    const [thisArg, callbacks] = this._resolve(type, args)
    return callbacks.map(callback => callback.bind(thisArg))
  }
```

- 事件名永远是字符串或 symbol，因此 `ctx.emit('x', {payload})` 不会误把 payload 当 thisArg（实验 01：`this with no thisArg -> null`，payload 正常到达）；只有把对象放在**事件名之前**才是 thisArg（实验 01 第二次 `demo/this` 的 `this` 为 `{ who: 'carrier' }`）。没有 thisArg 时监听器的 `this` 是 `null`，不是 ctx。
- `_resolve` 用 `args.shift()` **原地修改**传入的数组（`events.ts:163-164`）。`events.dispatch('emit', args)` 返回后，`args` 只剩监听器参数；dsh 的生产者正是利用这一点，先把 `[carrier, name, ...payload]` 传给 `dispatch`，再用同一个数组 `callback(...args)`（实验 04 末尾：`args after dispatch = []`；`packages/core/agent/src/dispatch.ts:125-129`）。
- `Context.filter` 是 `symbols.filter`（`vendor/cordis/src/context.ts:47-48`）。Service 实例自带 filter，按 isolate 比对（`vendor/cordis/src/service.ts:64-66`），所以 `ctx.emit(serviceInstance, ...)` 只送达同一隔离域的监听器。Cordis 自己在 `internal/service` 中用这种 filter（`vendor/cordis/src/reflect.ts:371-375`）。
- dsh 的 scope carrier：

`packages/core/scope/src/index.ts:158-185`

```ts
/**
 * Build an opaque receiver that preserves the base filter, admits untagged
 * listeners globally, and admits tagged listeners for a matching key or any
 * of its ancestors ({@link bindScopeParent}): a listener owned by an enclosing
 * scope receives every descendant scope's events, which is what lets one
 * standing composition observe each of the agents composed under it. A tag
 * BELOW the dispatch key stays excluded — events flow up the chain, never
 * down.
 * @param base - subject or service whose existing Cordis filter is preserved.
 * @param key - routed scope identity, or `undefined` for an unscoped subject.
 * @returns a carrier whose subject remains available only through event arguments.
 */
export function scopeTarget<T extends object>(base: T, key: ScopeKey | undefined): Scoped<T> {
  const baseFilter = (base as { [CordisContext.filter]?: (ctx: Context) => boolean })[CordisContext.filter]
  const carrier = {
    [CordisContext.filter](ctx: Context): boolean {
      if (baseFilter !== undefined && !baseFilter.call(base, ctx)) return false
      const tag = scopeOf(ctx)
      if (tag === undefined) return true
      for (let cursor = key; cursor !== undefined; cursor = scopeParents.get(cursor)) {
        if (cursor === tag) return true
      }
      return false
    },
  }
  carrierKeys.set(carrier, key)
  return carrier as unknown as Scoped<T>
}
```

- 事件声明用 `this: Scoped<...>` 表达「需要 carrier 作为 thisArg」，例如工具事件 `'tools/pre-execute'(this: Scoped<ToolsService>, ...)`（`packages/core/tools/src/events.ts:18`）、`'agent/pre-step'(this: Scoped<Agent>, ...)`（`packages/core/agent/src/runtime-types.ts:351`）、`'session/event'(this: Scoped<Session>, ...)`（`packages/core/session/src/index.ts:85`）。Cordis 的 `emit` 等方法有一个 `thisArg: NoInfer<ThisType<Events[K]>>` 重载专门接这种声明（`vendor/cordis/src/events.ts:43-87`）。
- `agentEvents` 把 carrier 固定为 thisArg，并把 `agent` 注入 payload，避免调用方伪造 subject：

`packages/core/agent/src/dispatch.ts:107-148`

```ts
export function agentEvents(ctx: Context, agent: Agent, carrier: Scoped<Agent> = agentCarrier(agent)): AgentEventDispatch {
  // The ordinary dispatch methods forward through Cordis' variadic mixins. The
  // fused (carrier, name, payload, ...rest) tuple is provably a valid argument
  // list for the matching thisArg overload, but TypeScript cannot relate the
  // generic Tail<K> spread back to that overload's conditional parameter
  // tuple — hence one contained, shape-preserving cast per method.
  const fused = <K extends AgentSubjectEvent>(payload: PayloadRest<K>): PayloadOf<K> =>
    // The dispatcher owns the subject injection; callers pass PayloadRest, so
    // the fused record is exactly the declared payload. The spread comes
    // first, so a structurally acceptable payload that happens to carry an
    // `agent` field can never override the injected subject.
    ({ ...payload, agent } as PayloadOf<K>)
  return {
    emit(name, payload) {
      // Cordis emit invokes callbacks through Array.map: one synchronous throw
      // starves later listeners, and returned promises are discarded. Agent
      // notifications are non-vetoing, so resolve the same filtered callback
      // set ourselves and contain both failure modes independently.
      const args: unknown[] = [carrier, name, fused(payload)]
      const callbacks = ctx.events.dispatch('emit', args)
      for (const callback of callbacks) {
        try {
          const returned: unknown = callback(...args)
          void Promise.resolve(returned).catch((error: unknown) => {
            ctx.logger.warn(`agent event "${name}" listener rejected: ${String(error)}`)
          })
        } catch (error: unknown) {
          ctx.logger.warn(`agent event "${name}" listener threw: ${String(error)}`)
        }
      }
    },
    async serial(name, payload) {
      // oxlint-disable-next-line typescript/unbound-method -- the events mixin accessor returns a pre-bound function
      const serial = ctx.serial as (thisArg: Scoped<Agent>, name: string, ...args: unknown[]) => Promise<never>
      return await serial(carrier, name, fused(payload))
    },
    waterfall(name, payload, ...rest) {
      // oxlint-disable-next-line typescript/unbound-method -- the events mixin accessor returns a pre-bound function
      const waterfall = ctx.waterfall as (thisArg: Scoped<Agent>, name: string, ...args: unknown[]) => never
      return waterfall(carrier, name, fused(payload), ...rest)
    },
  }
```

### Inferences
- 事件 payload 的第一个参数可以是对象（如 `exec`），因为事件名总在它前面；但**不要用对象或函数作为事件名**（symbol 可以），否则会被当成 thisArg。
- carrier 的 filter 只对「打了 scope 标签的 ctx」生效：未标签的监听器（普通插件）收到所有 agent 的事件（`packages/core/scope/src/index.ts:176`）。插件若只关心某个 agent，要么在 agent 作用域 ctx 里注册，要么在回调里自己比较 `payload.agent`。
- 由于监听器的 `this` 是 carrier（一个只有 filter 方法的对象），在 harness 事件里使用 `this` 没有意义；`Scoped<T>` 的注释也说 subject 只能经由事件参数取得（`packages/core/scope/src/index.ts:168`）。

### Gaps
- `ctx.reflect.bind` 的 `trace` 对 `null` thisArg 的处理没有深入读（`reflect.ts:438-440` → `getTraceable`），实验只观察到 `this === null`。

## 5. 内部事件（`internal/*`）

### Takeaway
内部事件在 `Events` 接口末尾声明（`vendor/cordis/src/events.ts:330-375`），名字以 `internal/` 开头的事件**不会**触发 `internal/dispatch`（`events.ts:165`）。插件作者可观察的主要是 `internal/status`（Fiber 状态变化）、`internal/plugin`（Fiber 创建/卸载）、`internal/service`、`internal/dispatch`（总线诊断/守卫）；`internal/update`、`internal/config` 是可拦截的 waterfall；`internal/listener`、`internal/get`、`internal/set` 是框架内部机制。

### Cited Findings

`vendor/cordis/src/events.ts:330-375`

```ts
/**
 * Built-in framework events used by core services and extension points.
 *
 * Plugin and status events track fiber lifecycle, service events observe
 * dependency registration, update/get/set/listener events allow core services
 * to intercept runtime operations, and `internal/dispatch` exposes event-bus
 * diagnostics before public events are delivered.
 */
export interface Events {
  [key: symbol]: (...args: any[]) => any
  /** A plugin fiber was created or its uid was cleared on disposal. */
  'internal/plugin'(fiber: Fiber): void
  /** A fiber changed lifecycle state; receives the fiber and its previous state. */
  'internal/status'(fiber: Fiber, oldValue: FiberState): void
  /** A service binding was refreshed in the matching isolation scope. */
  'internal/service'(this: Context, name: string, value: any): void
  /**
   * Apply resolved config; omitting `next()` keeps the current instance.
   * @param resolvedConfig - the evaluated and validated candidate.
   * @param next - restarts the fiber and waits for its lifecycle work.
   * @mode waterfall
   */
  'internal/update'(this: Fiber, resolvedConfig: any, next: () => Awaitable<void>): Awaitable<void>
  /**
   * Persist original config from a programmatic update, before its lifecycle transition.
   * @param fiber - the updated fiber.
   * @param originalConfig - the raw candidate; omitted when update uses noSave.
   * @mode emit
   */
  'internal/commit'(fiber: Fiber, originalConfig: any): void
  /**
   * Resolve plugin config after injections are active; candidate resolution does not publish it.
   * @param fiber - the fiber whose candidate is being resolved.
   * @param next - obtains the raw candidate from inner listeners.
   * @mode waterfall
   */
  'internal/config'(fiber: Fiber, next: () => any): any
  /** Waterfall: a service is being read through the context proxy. */
  'internal/get'(ctx: Context, name: string, error: Error, next: () => any): any
  /** Waterfall: a service is being written through the context proxy. */
  'internal/set'(ctx: Context, name: string, value: any, error: Error, next: () => boolean): boolean
  /** Bail: a listener is being registered; a non-null result replaces registration. */
  'internal/listener'(this: Context, name: string, listener: any, prepend: boolean): void
  /** An event is being dispatched to listeners (fired for non-internal events only). */
  'internal/dispatch'(mode: DispatchMode, name: string | symbol, args: any[], thisArg: any): void
}
```

- 各内部事件的分发点：
  - `internal/status`：`Fiber._updateState` 在状态改变时 `emit(this, oldState)`（`vendor/cordis/src/fiber.ts:609-614`）。harness 用户：`agent`、`server`、`web`、`inspector` 等（`docs/event-producer-consumer.zh.md:128`；例 `packages/sdk/server/src/server.ts:236`）。
  - `internal/plugin`：创建时 `emit`（`fiber.ts:330`）；卸载时用逐个 try/catch 的隔离分发（`fiber.ts:150-167`），注释为「不让一个观察者破坏所有权清理」。harness 用户：`mcp-client`、`mcp-manager`（带 `global: true`，`packages/mcp/mcp-manager/src/index.ts:200-202`）、`lsp-stdio`、loader 等（`docs/event-producer-consumer.zh.md:126`）。
  - `internal/service`：服务绑定刷新时以带 filter 的 ctx 作为 thisArg 分发（`vendor/cordis/src/reflect.ts:371-375`）。
  - `internal/update`（waterfall，thisArg = fiber）与 `internal/commit`（emit）：`fiber.ts:810`、`:828`；`internal/config`（waterfall）：`fiber.ts:700`。harness 中 `llm-pi-ai`、`mcp-manager`、`modules` 监听 `internal/config`（`docs/event-producer-consumer.zh.md:124`）。
  - `internal/get` / `internal/set`（waterfall）：上下文代理读写服务时（`vendor/cordis/src/reflect.ts:196`、`:226`）。
  - `internal/listener`（bail）：每次 `ctx.on` 都以 `ctx` 为 thisArg 分发（`events.ts:306`）；非 null 返回值替代正常注册。
  - `internal/dispatch`（emit）：在每个非 internal 事件分发前、筛选监听器之前触发，参数 `(mode, name, args, thisArg)`（`events.ts:165-167`）。

`vendor/cordis/src/fiber.ts:149-167`

```ts
/** Notify plugin teardown without allowing one observer to break ownership cleanup. */
function emitPluginDisposed(context: Context, fiber: Fiber) {
  const args: any[] = ['internal/plugin', fiber]
  let callbacks: Function[]
  try {
    callbacks = context.events.dispatch('emit', args)
  } catch (error) {
    context.logger.error(error)
    return
  }
  for (const callback of callbacks) {
    try {
      const returned = callback(...args)
      void Promise.resolve(returned).catch(error => context.logger.error(error))
    } catch (error) {
      context.logger.error(error)
    }
  }
}
```

- EventsService 构造函数里注册的三个内建监听器，说明 `internal/update` 的「按 Fiber 存放」机制：非 global 的 `internal/update` 监听器被 `internal/listener` 截走，存进**该 Fiber 自己的** `_hooks['internal/update']`；全局唯一的 prepend 监听器在分发时只调用当前 Fiber（thisArg）上的那些钩子：

`vendor/cordis/src/events.ts:133-160`

```ts
  constructor(private ctx: Context) {
    defineProperty(this, symbols.tracker, {
      property: 'ctx',
      noShadow: true,
    })

    this.on('internal/listener', function (this: Context, name, listener, options: EventOptions) {
      if (name === 'internal/update' && !options.global) {
        const hooks = this.fiber._hooks['internal/update'] ??= new DisposableList()
        const method = options.prepend ? 'unshift' : 'push'
        return this.fiber.effect(() => hooks[method](listener), 'ctx.on("internal/update")')
      }
    })

    this.on('internal/update', function (config, next) {
      const cbs = [...this._hooks['internal/update'] || []]
      const _next = () => {
        const cb = cbs.shift() ?? next
        return cb.call(this, config, _next)
      }
      return _next()
    }, { global: true, prepend: true })

    this.on('internal/config', (fiber, next) => {
      const config = next()
      return fiber.runtime ? resolveConfig(fiber.runtime, config) : config
    })
  }
```

- `internal/dispatch` 的真实用法——作为同步守卫否决 `session/event`：`terminal-bash` 在有持久终端时，若会话追加 `sandbox/mode` 事件就 throw（`global: true` 绕过 session carrier 的 filter）：

`packages/terminal/terminal-bash/src/index.ts:49-59`

```ts
  owner.ctx.on('internal/dispatch', (_mode, eventName, args) => {
    if (eventName !== 'session/event') return
    const [session, event] = args as [Session, SessionEvent]
    if (session !== owner.session || event.type !== 'sandbox/mode') return
    const folded = state.sandboxPolicy.overrideOf(session) ?? null
    const currentMode = folded ?? state.sandboxPolicy.defaultMode
    if (event.data.mode === currentMode || !state.pty.hasOwnerActivity(owner)) return
    throw new Error(
      `cannot change sandbox mode from "${currentMode}" to "${event.data.mode}" while persistent terminal sessions are open or being created; wait for creation to settle and close them first`,
    )
  }, { global: true })
```

  这能否决追加，是因为 session 在 `log.push` **之前**通过 `events.dispatch` 解析监听器快照（`packages/core/session/src/index.ts:594-599`），而 `internal/dispatch` 在 `_resolve` 内同步 emit，throw 直接从 `dispatch()` 冒出：

`packages/core/session/src/index.ts:594-606`

```ts
      const publications = entry === undefined ? [] : events.map((event) => {
        const args: unknown[] = [this, event]
        const callbacks = collectSessionCallbacks(entry.emitCtx, [entry.carrier, 'session/event', ...args])
        return { event, args, callbacks }
      })
      for (const event of events) this.log.push(event)
      this.eventsSnapshot = undefined
      acceptSurface()
      if (entry !== undefined) {
        for (const { event, args, callbacks } of publications) {
          this.batchPublication = event
          invokeContainedSessionObservers(entry.emitCtx, 'session/event', entry.id, args, callbacks)
        }
```

- 实验 05 中段验证：`internal/dispatch` 看到 `emit` 与 `serial` 两种模式；监听器 throw 时 `root.emit('guarded/x')` 抛出，`guarded/x` 的监听器没有运行。实验 06 中注册了 `internal/dispatch` 监听器，`internal/update` 分发时它没有输出，验证 `internal/*` 不触发 `internal/dispatch`。

### Inferences
- 用 `events.dispatch(mode, ...)` 自己调用回调的 producer，传给 `internal/dispatch` 的 `mode` 是它传入的字符串，而不是事件声明的模式：`session/flush` 声明为 `parallel`（`packages/core/session/src/index.ts:91-93`），但实际以 `dispatch('emit', ...)` 解析（`:1091`），所以 `internal/dispatch` 观察者看到的是 `mode=emit`。诊断工具不能把这个字段当成事件契约。
- 普通插件可以安全地**观察** `internal/status`、`internal/plugin`；拦截 `internal/update` 只应针对自己的 Fiber（非 global 时框架已自动限定）；`internal/listener`/`get`/`set` 属于框架内部。

### Gaps
- `internal/get`/`internal/set` 在 harness 中是否有监听者：生成的矩阵（`docs/event-producer-consumer.zh.md:120-133`）没有列出，推断没有。

## 6. 类型：`Events` 声明合并与 `import type {}` 技巧

### Takeaway
事件名和监听器签名通过 `declare module '@deepseek-ai/cordis' { interface Events { ... } }` 合并进 Cordis 的 `Events` 接口，`ctx.on/emit/...` 的 `K extends keyof Events` 泛型因此能推断参数与返回类型。消费方需要让 TypeScript「看到」声明所在模块：`import type {} from '<pkg>'` 零运行时开销地拉入声明。

### Cited Findings
- 教程中的声明与说明：—— `docs/cordis-tutorial/04-events.zh.md:14-21`、`:44`、`:50`、`:65`

`docs/cordis-tutorial/04-events.zh.md:14-21`

~~~~
declare module '@deepseek-ai/cordis' {
  interface Context {
    stats: StatsService
  }
  interface Events {
    'stats/report'(name: string, count: number): void
  }
}
~~~~

`docs/cordis-tutorial/04-events.zh.md:65-65`

~~~~
`import type {} from './stats.ts'` 行不会在运行时导入任何内容；它的作用是让 TypeScript 看到声明合并。组合并运行：
~~~~

- 方法签名使用 `Parameters<Events[K]>`、`ReturnType<Events[K]>`、`ThisType<Events[K]>`（`vendor/cordis/src/events.ts:16-21`、`:43-105`）；例如 `serial` 返回 `Promisify<ReturnType<Events[K]>>`（`:62`），`bail` 返回 `ReturnType<Events[K]>`（`:72`）。`Events` 本身有 `[key: symbol]` 索引签名（`:339`），symbol 事件名无需声明。
- harness 的工具事件声明（含 `@mode` 标签与 `this: Scoped<ToolsService>`）：

`packages/core/tools/src/events.ts:6-58`

```ts
declare module '@deepseek-ai/cordis' {
  interface Events {
    /**
     * Allow, deny, cancel, or ask before dispatch. `next()` delegates to allow;
     * `cancel` selects the canonical pre-dispatch cancellation result, and missing
     * approval support turns `ask` into denial. Async gates must observe
     * `exec.signal`; the registry rechecks cancellation after they settle but
     * never abandons their promise.
     * Scope-filtered dispatch (`@deepseek-ai/dsh-scope`): agent-scoped listeners receive only that agent's calls.
     * @param exec - the pending call (name, parsed arguments, caller agent).
     * @mode waterfall
     */
    'tools/pre-execute'(this: Scoped<ToolsService>, exec: ToolExecution, next: () => Promise<PreToolDecision>): Promise<PreToolDecision>
    /**
     * Around-dispatch waterfall for timeout, retry, or metrics. `next()` returns
     * a normalized result; wrappers may change only `exec.signal`, while call
     * identity remains immutable. The registry re-fuses the original caller
     * signal before the body, so replacement cannot detach caller cancellation;
     * wrappers must still restore their signal and reach quiescence.
     * Scope-filtered dispatch (`@deepseek-ai/dsh-scope`): agent-scoped listeners receive only that agent's calls.
     * @param exec - the allowed call about to dispatch (name, parsed arguments, caller agent, signal).
     * @mode waterfall
     */
    'tools/execute'(this: Scoped<ToolsService>, exec: ToolDispatchExecution, next: () => Promise<ToolExecutionResult>): Promise<ToolExecutionResult>
    /**
     * Accept, replace, enrich, or block a normalized dispatch result. `next()`
     * accepts it unchanged; thrown tools still reach this waterfall as errors. Async
     * listeners must observe `exec.signal`; after they settle, caller
     * cancellation replaces only a successful accepted outcome with the code
     * selected by whether the tool body was invoked.
     * Scope-filtered dispatch (`@deepseek-ai/dsh-scope`): agent-scoped listeners receive only that agent's calls.
     * @param exec - the call that just ran (name, parsed arguments, caller agent).
     * @param result - the dispatch outcome a listener may accept, replace, or block.
     * @mode waterfall
     */
    'tools/post-execute'(this: Scoped<ToolsService>, exec: ToolExecution, result: Readonly<ToolExecutionResult>, next: () => Promise<PostToolDecision>): Promise<PostToolDecision>
    /**
     * Observe the frozen, lossless-JSON final outcome. Listener failures are contained.
     * Scope-filtered dispatch (`@deepseek-ai/dsh-scope`): keyed by `exec.agent`.
     * @param exec - the execution object that traversed the pipeline.
     * @param result - a deep-frozen snapshot of the final returned result.
     * @mode emit
     */
    'tools/result'(this: Scoped<ToolsService>, exec: Readonly<ToolExecution>, result: Readonly<ToolExecutionResult>): undefined
    /**
     * A tool was registered or unregistered, or a scoped restriction changed.
     * Unfiltered registry notification: scoped listeners receive changes from every
     * scope because a global contribution can affect every agent's next assembly.
     * @mode emit
     */
    'tools/change'(): void
  }
}
```

- 用户文档里的例子则用 side-effect import `import '@deepseek-ai/dsh-tools'` 拉入声明（`docs/user/develop/framework/events.zh.md:124-125`），cookbook 用 `import type { PreToolDecision, ToolExecution } from '@deepseek-ai/dsh-tools'`（`docs/cookbook/extension-cookbook.zh.md:19`）。任何来自该模块的 type import 都足以让声明合并生效；`import type {}` 是不需要具体类型时的写法（仓库中的使用见 `packages/test-support/session-snapshot/tests/fixtures/*.ts`）。
- 命名约定 `namespace/action`（`docs/cordis-tutorial/04-events.zh.md:44`；`docs/user/develop/framework/events.zh.md:104`）。`turn/*`、`step/*`、`tool/call`、`tool/result`、`compaction/*` 是**持久会话事件类型**，不是 Cordis 事件；要观察它们监听 `session/event` 并检查 `event.type`：

`docs/user/develop/framework/events.zh.md:104-106`

~~~~
Harness 的 Cordis 事件遵循 `namespace/action` 命名，例如 `agent/pre-step`、`agent/request-error`、`tools/result` 和 `session/event`。完整签名与触发模式见[子系统页面](../../../subsystems/core.zh.md)上生成的 `cordis-surface` 区块。

`turn/*`、`step/*`、`tool/call`、`tool/result` 和 `compaction/*` 是持久化的会话事件类型，不是同名 Cordis 事件。需要观察它们时，监听 `session/event` 并检查 `event.type`。
~~~~

### Inferences
- 用户文档 `events.zh.md:92-94` 把声明写成属性形式 `'my-plugin/ready': (payload) => void`，harness 源码统一用方法形式并声明 `this`；方法形式才能表达 `this: Scoped<...>`，从而启用 thisArg 重载。
- `@mode` 标签只是文档约定（primer `:29`），TypeScript 不会检查你用的分发方法与 `@mode` 一致；一致性由生成器交叉校验（`docs/event-producer-consumer.zh.md:135`）。

### Gaps
- 未检查 `pnpm run gen-cordis-catalog` 的具体校验规则（不在本主题范围）。

## 7. 错误处理：Cordis 原生行为 vs 仓库的隔离规范

### Takeaway
Cordis 的 `emit` **不隔离**监听器异常：同步 throw 直接抛给 producer，并让后续监听器全部跳过；async 监听器的 rejection 被丢弃成为 unhandledRejection。`bail`/`serial`/`waterfall` 同样在第一个异常处停止并抛出；只有 `parallel` 让所有监听器跑完后抛 `AggregateError`。所以 dsh 仓库规定：观察型事件的 producer 用 `ctx.events.dispatch('emit', args)` 拿到绑定好的回调列表，自己逐个 try/catch，并 `.catch` 返回的 promise。

### Cited Findings
- 实验 04（每种模式下中间监听器 throw / reject）：

脚本：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events/04-errors.mjs`

```js
// A throwing listener under every dispatch mode
const { Context } = await import(process.argv[2])
const sleep = (ms) => new Promise(r => setTimeout(r, ms))
process.on('unhandledRejection', (e) => console.log('  [process] unhandledRejection:', e.message))
const root = new Context()
const ran = []
await root.plugin({
  name: 'err',
  apply(ctx) {
    for (const ev of ['e/sync', 'e/wf']) {
      ctx.on(ev, (...a) => { ran.push('L1'); const next = a.at(-1); return typeof next === 'function' ? next() : undefined })
      ctx.on(ev, () => { ran.push('L2-throws'); throw new Error(`L2 failed on ${ev}`) })
      ctx.on(ev, (...a) => { ran.push('L3'); const next = a.at(-1); return typeof next === 'function' ? next() : undefined })
    }
    ctx.on('e/async', async () => { ran.push('A1'); await sleep(5); return undefined })
    ctx.on('e/async', async () => { ran.push('A2-rejects'); throw new Error('A2 rejected') })
    ctx.on('e/async', async () => { ran.push('A3'); await sleep(5); return undefined })
  },
})
async function run(label, fn) {
  ran.length = 0
  try { const v = await fn(); console.log(`${label}: resolved ${v}; ran=[${ran}]`) }
  catch (e) { console.log(`${label}: THREW ${e.constructor.name}: ${e.message}${e.errors ? ' errors=' + e.errors.map(x => x.message) : ''}; ran=[${ran}]`) }
}
await run('emit (sync throw)     ', () => root.emit('e/sync'))
await run('bail (sync throw)     ', () => root.bail('e/sync'))
await run('serial (sync throw)   ', () => root.serial('e/sync'))
await run('parallel (sync throw) ', () => root.parallel('e/sync'))
await run('waterfall (sync throw)', () => root.waterfall('e/wf', () => 'inner'))
await run('emit (async reject)   ', () => root.emit('e/async')); await sleep(20)
await run('serial (async reject) ', () => root.serial('e/async'))
await run('parallel(async reject)', () => root.parallel('e/async'))
// the dsh producer pattern: events.dispatch('emit', args) + per-listener try/catch
ran.length = 0
const args = ['e/sync']
for (const cb of root.events.dispatch('emit', args)) { try { void Promise.resolve(cb(...args)).catch(e => console.log('  contained rejection:', e.message)) } catch (e) { console.log('  contained throw:', e.message) } }
console.log(`dsh contained emit: ran=[${ran}]; args after dispatch = ${JSON.stringify(args)}`)
```

命令：

```
cd /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/lab-events && node 04-errors.mjs /tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/f5eb4f99-19a0-491a-ab82-996c21bc2a2b/scratchpad/cordis-lab/node_modules/@deepseek-ai/cordis/lib/index.js
```

输出：

```
emit (sync throw)     : THREW Error: L2 failed on e/sync; ran=[L1,L2-throws]
bail (sync throw)     : THREW Error: L2 failed on e/sync; ran=[L1,L2-throws]
serial (sync throw)   : THREW Error: L2 failed on e/sync; ran=[L1,L2-throws]
parallel (sync throw) : THREW AggregateError: one or more listeners of event "e/sync" failed errors=L2 failed on e/sync; ran=[L1,L2-throws,L3]
waterfall (sync throw): THREW Error: L2 failed on e/wf; ran=[L1,L2-throws]
emit (async reject)   : resolved undefined; ran=[A1,A2-rejects,A3]
  [process] unhandledRejection: A2 rejected
serial (async reject) : THREW Error: A2 rejected; ran=[A1,A2-rejects]
parallel(async reject): THREW AggregateError: one or more listeners of event "e/async" failed errors=A2 rejected; ran=[A1,A2-rejects,A3]
  contained throw: L2 failed on e/sync
dsh contained emit: ran=[L1,L2-throws,L3]; args after dispatch = []
```

  - `emit` 同步 throw：抛出，`ran=[L1,L2-throws]`，L3 未运行。
  - `emit` async reject：`emit` 正常返回，rejection 变成 `[process] unhandledRejection`。
  - `parallel`：三个都运行，抛 `AggregateError`（消息来自 `events.ts:198-199`）。
  - 最后一行：`events.dispatch` + 逐个 try/catch 的 dsh 写法，L3 照常运行。
- 仓库防御规范「在分发器中隔离回调异常」：—— `docs/defensive-patterns/general.zh.md:257-269`

`docs/defensive-patterns/general.zh.md:257-269`

~~~~
## 在分发器中隔离回调异常

逐个捕获并报告监听器的异常，不让分发 promise 被 reject，也不让后续监听器失去执行机会。监听器的所有者必须按[错误处理规范](../../.agents/skills/dsh-error-handling/SKILL.md#catch-recover-or-stop)处置其修改的状态；仅捕获异常不会恢复状态。

判定条件是：抛异常的监听器是否能让分发 promise 被 reject，或让其后的监听器不再运行。边界是调用方自己的 await——分发器有意抛给发起该操作的那条路径的错误，不属于隔离失败。

```ts
async function dispatch(listeners: ((event: string) => Promise<void>)[], event: string): Promise<void> {
  for (const listener of listeners) {
    try { await listener(event) } catch (error) { console.warn('listener failed', error) }
  }
}
```
~~~~

- `dispatch()` 方法正是 dsh 为此保留的（注释「DSH retains callback enumeration for per-listener error containment」，`vendor/cordis/src/events.ts:175-185`）。
- producer 实例（都采用相同形状）：
  - `tools/result`：`packages/core/tools/src/pipeline.ts:419-439`（第 9 节）。
  - agent 通知：`packages/core/agent/src/dispatch.ts:120-137`（第 4 节）。其注释说「Cordis emit invokes callbacks through Array.map」（`:121`），**与当前源码不符**：`emit` 用的是 `for...of`（`vendor/cordis/src/events.ts:209`）；不过结论（一个同步 throw 会饿死后续监听器、返回的 promise 被丢弃）在实验 04 中成立。
  - session 观察者：`packages/core/session/src/index.ts:145-170`。
  - web：

`packages/web/web/src/index.ts:181-191`

```ts
  /** Emit `web/providers-changed`, containing each listener's failure so none vetoes the mutation. */
  private emitProvidersChanged(): void {
    for (const listener of this.ctx.events.dispatch('emit', ['web/providers-changed'])) {
      try {
        const returned: unknown = listener()
        void Promise.resolve(returned).catch((error: unknown) => { this.warnListenerFailure(error) })
      } catch (error: unknown) {
        this.warnListenerFailure(error)
      }
    }
  }
```

  - `agent-loop/config-start-failed`：`packages/core/agent-loop/src/index.ts:394-404`。
  - Cordis 自身的 `internal/plugin` 卸载通知：`vendor/cordis/src/fiber.ts:150-167`。
- 非观察型事件的处理是「捕获后关闭失败」：
  - `approval/request`：先 `Promise.resolve().then(...)` 进入 promise 链，使同步 throw 也成为 rejection；任何 throw 都归一成 `'unavailable'`（`packages/interaction/user-approval/src/index.ts:303-318`）。
  - `session/flush`（parallel 语义）自己用 `Promise.allSettled` 并抛第一个失败，因为 flush 是调用方拥有的失败边界（`packages/core/session/src/index.ts:1088-1105`）。
  - 工具 waterfall 失败在 pipeline 的 try/catch 中转换为工具错误结果（`pipeline.ts:231-232`、`:333-334`；`postExecute` 的注释「a throwing listener → isError」`:505`）。

`packages/core/session/src/index.ts:1088-1105`

```ts
  async flush(session: Session): Promise<boolean> {
    const { carrier } = this.liveEntryFor(session)
    const callbackArgs: unknown[] = [session]
    const callbacks = collectSessionCallbacks(this.ctx, [carrier, 'session/flush', session])
    const results = await Promise.allSettled(callbacks.map((callback) => {
      try {
        return callback(...callbackArgs)
      } catch (error: unknown) {
        // Preserve the listener's exact rejection value; flush is a caller-owned
        // failure boundary, and Cordis listeners may throw arbitrary values.
        // oxlint-disable-next-line typescript/prefer-promise-reject-errors
        return Promise.reject(error)
      }
    }))
    const failure = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
    if (failure !== undefined) throw failure.reason
    return callbacks.length > 0
  }
```

### Inferences
- 如果你新建一个只用来通知的事件，直接 `ctx.emit` 会让任何一个有 bug 的监听器打断你的业务流程甚至抛到你的调用者；按仓库规范应改用 `dispatch` + 逐个隔离。
- 作为消费者，不能指望 producer 一定隔离：写 `tools/result` 监听器时有保护，但写第三方 `ctx.emit` 事件的监听器时，自己的异常会冒泡到别人的代码里。
- 隔离只是捕获，不恢复状态（防御规范 `general.zh.md:259`），监听器所有者仍需自己处置部分修改的状态。

### Gaps
- 未测 `parallel` 中同步 throw 的监听器：源码里每个回调都被包在 `async callback => ...`（`events.ts:195`），所以同步 throw 也转为 rejection，实验 04 的 `parallel (sync throw)` 行与此一致。

## 8. `event-producer-consumer` 文档讲了什么

### Takeaway
`docs/event-producer-consumer.zh.md` 是从 TypeScript Program **生成**的矩阵，列出每个 harness 自有事件的模式、声明位置、分发者（含分发方法）、监听者；它本身没有关于「谁拥有事件名」「payload 不可变」的条文。这些规则散落在事件声明的 JSDoc 和其他文档中。

### Cited Findings
- 文档定位与生成方式：

`docs/event-producer-consumer.zh.md:8-14`

~~~~
本矩阵展示哪些包会派发各个 harness 自有事件，以及哪些包会监听这些事件。事件之间存在多对多关系，因此密集的关系数据以表格而非一张大型关系图呈现。接收方和事件名称类型还涵盖有意绕过 `ctx.emit` 的内含派发位置，例如 subagent 生命周期封装。

应用级监听者不在本包矩阵范围内。Desktop Host 通过 [`installOperationDiagnostics`](../apps/desktop-host/src/operation-diagnostics.ts) 订阅 `plugin-manager/operation`，并向壳转发有界记录。

<!-- BEGIN GENERATED event-producer-consumer:events -->
| Event | Mode | Declared in | Dispatchers | Listeners |
| --- | --- | --- | --- | --- |
~~~~

`docs/event-producer-consumer.zh.md:135-135`

~~~~
Maintenance mode: generated: Cordis event declarations and producer/listener edges are resolved from the repository TypeScript Program.
~~~~

- 分发者列里标注 `events.dispatch` 的条目就是自行隔离的 producer，例如 `session/created|disposed|event|flush`、`tools/result`、`workflow/*`、`agent-loop/config-start-failed`（行 15、77-80、95、103-108）。
- 「未声明的事件字符串」表（行 117-133）列出 `internal/*` 的 harness 监听者与少数未声明事件（`agent/request` 注意：在 `runtime-types.ts:374` 有声明，但矩阵把它列在未声明表，行 122——生成器的分类结果，原因未查）。
- 规则来源（替代文档中不存在的「所有权」条文）：
  - 「分发模式是事件公开约定的一部分」「只能通过对应方法分发」：`docs/cordis-primer.zh.md:19`、`:29`。
  - payload 不可变：`tools/result` 的 `exec` 在通知前被 `Object.freeze`，result 是 deep-frozen 快照（`packages/core/tools/src/events.ts:43-49`；`pipeline.ts:421-423`）；`tools/execute` 的 wrapper 只能改 `exec.signal`（`events.ts:20-24`）；`tools/post-execute` 的 `result` 是 `Readonly`（`events.ts:41`）；session 事件 message 被 deepFreeze（`packages/core/session/src/index.ts:103-134`）。
  - 单一入口：`session/flush` 必须经 `ctx.sessions.flush()` 而非裸 `ctx.parallel('session/flush')`，「one owner and one spelling」（`packages/core/session/src/index.ts:1076-1082`）。
  - 拦截/策略用事件，直接能力调用用服务方法：`docs/cordis-primer.zh.md:49`。

### Inferences
- 「事件所有者 = 声明它的包」是从矩阵的 `Declared in` 列与 producer 的单一入口约定推出的惯例，不是明文规则。

### Gaps
- 未找到明确写出「只有声明方可以分发」的条文。

## 9. 真实流水线：工具事件，以及 agent / session 事件

### Takeaway
一次工具调用依次经过：`tools/pre-execute`（waterfall，返回 `PreToolDecision`，inner 为 allow）→ 守卫与审批 → `tools/execute`（waterfall，包住工具体，inner 为真正执行）→ `tools/post-execute`（waterfall，返回 `PostToolDecision`，inner 为 accept）→ `tools/result`（通知，dispatch + 逐个隔离，冻结后只读）。所有四个都以 `scopeTarget(this.tools, exec.agent)` 为 thisArg。

### Cited Findings
- 决策类型：

`packages/core/tools/src/contracts.ts:375-389`

```ts
export type PreToolDecision =
  | { kind: 'allow' }
  | { kind: 'deny'; reason: string; info?: ToolErrorInfo }
  | { kind: 'cancel' }
  | { kind: 'ask'; reason?: string; displayReason?: { readonly en: string; readonly [locale: string]: string } }

/**
 * Post-dispatch decision: accept, replace one projection, attach context for the
 * next request, or block by turning corrective feedback into an error result
 * whose optional `info` is its structured error identity.
 */
export type PostToolDecision =
  | { kind: 'accept'; content?: ContentBlock[]; value?: never; additionalContexts?: UserMessage[] }
  | { kind: 'accept'; value: JsonValue; content?: never; additionalContexts?: UserMessage[] }
  | { kind: 'block'; feedback: ContentBlock[]; info?: ToolErrorInfo; additionalContexts?: UserMessage[] }
```

- ① `tools/pre-execute`：inner 返回 `{ kind: 'allow' }`；`ask` 走 `ctx.approval`；allow 后还要过 `checkGuards`；任何异常 → `toolErrorResult`：

`packages/core/tools/src/pipeline.ts:198-233`

```ts
    try {
      const carrier = scopeTarget(this.tools, exec.agent)
      const gate = await this.ctx.waterfall(
        carrier, 'tools/pre-execute', exec,
        () => Promise.resolve<PreToolDecision>({ kind: 'allow' }),
      )
      const askResolution = gate.kind === 'ask'
        ? await this.serviceAsk(exec, gate)
        : { decision: gate, approvalCancelled: false }
      const { decision } = askResolution
      if (this.callerCancelled(exec) && askResolution.approvalCancelled) {
        return { kind: 'post-result', exec, result: toolAbortedBeforeDispatchResult() }
      }
      if (decision.kind === 'cancel') {
        return { kind: 'post-result', exec, result: toolAbortedBeforeDispatchResult() }
      }
      const denial = decision.kind === 'allow' ? this.tools.checkGuards(exec) : decision
      if (denial !== undefined) {
        const { reason: denialReason, info: denialInfo } = denial
        return {
          kind: 'post-result',
          exec,
          result: this.materializeFinalResult({
            content: [{ type: 'text', text: `Error: ${denialReason}` }],
            isError: true,
            error: { message: denialReason, ...denialInfo === undefined ? {} : { info: denialInfo } },
          }),
        }
      }
      if (this.callerCancelled(exec)) {
        return { kind: 'post-result', exec, result: toolAbortedBeforeDispatchResult() }
      }
      return { kind: 'dispatch', exec }
    } catch (error: unknown) {
      return { kind: 'final-result', exec, result: toolErrorResult(error) }
    }
```

- ② `tools/execute`：inner 是 `dispatchToolBody`；wrapper 抛错 → `wrapperFailureResult`：

`packages/core/tools/src/pipeline.ts:306-313`

```ts
  async dispatch(exec: ToolRunContext): Promise<ScheduledToolDispatch> {
    try {
      const mutableExec = exec as MutableToolRunContext
      const carrier = scopeTarget(this.tools, exec.agent)
      const result = await this.ctx.waterfall(
        carrier, 'tools/execute', mutableExec,
        () => this.dispatchToolBody(mutableExec),
      )
```

- ③ `tools/post-execute`：inner 为 `{ kind: 'accept' }`；`block` 变成 isError 结果；`accept` 可替换 `content` 或 `value`（不能同时，失败结果不能替换 value）：

`packages/core/tools/src/pipeline.ts:507-532`

```ts
  private async postExecute(exec: ToolExecution, result: ToolExecutionResult): Promise<ToolExecutionResult> {
    const decision = await this.ctx.waterfall(
      scopeTarget(this.tools, exec.agent), 'tools/post-execute', exec, result,
      () => Promise.resolve<PostToolDecision>({ kind: 'accept' }),
    )
    const decisionContexts = decision.additionalContexts ?? []
    if (decision.kind === 'block') {
      const message = failureMessageFromContent(decision.feedback)
      return this.markCanonical(exec, {
        content: decision.feedback,
        isError: true,
        error: { message, ...decision.info === undefined ? {} : { info: decision.info } },
        ...decisionContexts.length > 0 ? { additionalContexts: decisionContexts } : {},
      })
    }
    if (Object.hasOwn(decision, 'content') && Object.hasOwn(decision, 'value')) {
      throw new TypeError('tools/post-execute accept decision cannot replace both value and content')
    }
    const additionalContexts = [
      ...result.additionalContexts ?? [],
      ...decisionContexts,
    ]
    if (Object.hasOwn(decision, 'value')) {
      if (result.isError) {
        throw new TypeError('tools/post-execute cannot replace the value of a failed result')
      }
```

- ④ `tools/result`：冻结 → `events.dispatch('emit', [carrier, name, exec, result])` → 逐个 try/catch：

`packages/core/tools/src/pipeline.ts:419-439`

```ts
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

- 真实监听器：
  - 门禁（cookbook）：deny 或 `return next()`，`docs/cookbook/extension-cookbook.zh.md:26-31`。
  - 外层 post-execute 改写内容（prepend）：

`packages/spill/spill-policy/src/index.ts:134-151`

```ts
  ctx.on('tools/post-execute', async (exec, result, next): Promise<PostToolDecision> => {
    const decision = await next()
    if (decision.kind !== 'accept' || Object.hasOwn(decision, 'value') || exec.name === 'read') return decision
    const content = decision.content ?? result.content
    const hasImages = content.some(block => block.type === 'image')
    // Text-only PTC bindings retain their asynchronous log-only spill path.
    if (exec.parent !== undefined && !hasImages) return decision
    const retained = await bound(exec, content, exec.name, exec.callId, exec.parent === undefined ? 'result' : 'dispatch')
    if (retained === undefined) return decision
    const additionalContexts = [...decision.additionalContexts ?? []]
    if (exec.parent !== undefined && !result.isError && hasImages && !retained.some(block => block.type === 'image')) {
      additionalContexts.push(createUserMessage({ content: retained, source: { kind: 'ptc-mode' } }))
    }
    return {
      kind: 'accept', content: retained,
      ...additionalContexts.length > 0 ? { additionalContexts } : {},
    }
  }, { prepend: true })
```

  - 只附加上下文、始终委托：

`packages/guard/repeat-tool-reminder/src/index.ts:218-231`

```ts
  ctx.on('tools/post-execute', async (exec, _result, next): Promise<PostToolDecision> => {
    const reminder = observe(exec)
    const downstream = await next()
    if (!reminder) return downstream
    return { ...downstream, additionalContexts: prependContext(reminder, downstream.additionalContexts) }
  })

  // A user interjection changes the context; repetition across it is not a
  // loop. Pure reset hook: always delegates (attaching nothing, vetoing
  // nothing).
  ctx.on('agent/pre-step', ({ agent, messages }, next): Promise<PreStepDecision> => {
    if (messages.some(message => message.source.kind === 'user')) chains.delete(agent)
    return next()
  })
```

- cookbook 关于选哪个扩展点：`tools/pre-execute` 是可重排的策略层；单调最终拒绝用 `ctx.tools.guard()`；包裹分发用 `tools/execute`；变换用 `tools/post-execute`；只读观察用 `tools/result`（`docs/cookbook/extension-cookbook.zh.md:35`）。
- agent 事件（声明 `packages/core/agent/src/runtime-types.ts:291-430`）：
  - `agent/pre-step` waterfall，inner 返回 `{ kind: 'enter', messages }`，监听器可 reject 或替换 messages：

`packages/core/agent-loop/src/agent.ts:299-311`

```ts
      const decision = await this.dispatch.waterfall(
        'agent/pre-step', { messages: claimed, ...position, signal, modelSelection: selection },
        (): Promise<PreStepDecision> => {
          const sections = renderContextSections(assembly)
          const context = this.runtimeContext.project(sections)
          return Promise.resolve<PreStepDecision>({
            kind: 'enter',
            messages: context === undefined ? claimed : [...claimed, context],
          })
        },
      )
      signal.throwIfAborted()
      if (decision.kind === 'reject') {
```

  - `agent/turn-stopping` serial：所有监听器按序 await；文档强调「由数据决定，监听器顺序不影响结果」——反对者通过 `agent.steer()` 注入消息，循环再读 inbox（`runtime-types.ts:404-418`）。
  - `agent/created` serial（`packages/core/agent/src/index.ts:575-579`）。
  - `agent/status`、`agent/error`、`agent/assistant-stream`、`agent/inbox/*` 走 `agentEvents().emit`，带隔离（`packages/core/agent-loop/src/agent.ts:164`、`:267`、`:476`；`inbox.ts:111`、`:225-228`）。
  - `approval/request` waterfall，inner 返回 `'unavailable'`（`packages/interaction/user-approval/src/types.ts:87-90`；`index.ts:307-311`）。
- session 事件（声明 `packages/core/session/src/index.ts:55-93`）：`session/created|disposed|event` 为 emit 语义但用 dispatch + 隔离；`session/event` 的监听器快照在 `log.push` 之前解析、回调在之后调用（`:594-606`），因此监听器失败不会让已提交的追加失败（`:73-75`）；`session/flush` 为 parallel 语义、由 `flush()` 统一分发（`:1088-1105`）。

`packages/core/session/src/index.ts:145-170`

```ts
type SessionCallback = (...args: unknown[]) => unknown

/** Resolve one listener snapshot, including Cordis's internal dispatch checks. */
function collectSessionCallbacks(ctx: Context, args: unknown[]): SessionCallback[] {
  return [...ctx.events.dispatch('emit', args)] as SessionCallback[]
}

/** Invoke one resolved observe-only listener snapshot with per-listener containment. */
function invokeContainedSessionObservers(
  ctx: Context,
  name: 'session/event' | 'session/disposed',
  id: SessionId,
  args: unknown[],
  callbacks: SessionCallback[],
): void {
  for (const callback of callbacks) {
    try {
      const returned: unknown = callback(...args)
      void Promise.resolve(returned).catch((error: unknown) => {
        ctx.logger.warn(`session "${id}": ${name} listener rejected: ${String(error)}`)
      })
    } catch (error: unknown) {
      ctx.logger.warn(`session "${id}": ${name} listener threw: ${String(error)}`)
    }
  }
}
```

### Inferences
- 写工具策略插件时：决策类监听器返回带 `kind` 的对象；放行一律 `return next()`；附加上下文用 `additionalContexts` 并保留下游的（`{ ...downstream, additionalContexts: ... }`）。
- 因为 `tools/result` 的 producer 会 `.catch` 返回的 promise，async 监听器可以做 I/O 而不阻塞流水线；但它也不会被 await，所以不能用来保证「写完审计再继续」。

### Gaps
- 未逐一阅读 `dispatchToolBody`、`serviceAsk`、`normalizeDispatchResult` 的细节（不影响事件语义）。

## 10. 插件作者的事件陷阱

### Takeaway
最常见的错误是：waterfall 忘记 `return next()`、对 `bail`/`serial` 返回了被当成 bail 值的 `0`/`''`/Promise、以为 `ctx.emit` 会隔离监听器异常、在插件激活前就分发事件，以及依赖跨插件的监听器顺序。

### Cited Findings
1. **waterfall 不调用 `next()` = 否决；调了但不 `return` = 返回 `undefined`**。实验 03；教程 `04-events.zh.md:138`；`dsh_plugin_guide.md:471` 已提到前者。
2. **`next()` 只能调用一次**，第二次同步抛 `next() called multiple times`（`events.ts:258`，实验 03）。重试应放在 `tools/execute` wrapper 里对 `next` 的结果处理，而不是多次调用 `next`。
3. **`0`、`''` 是 bail 值**；**async 监听器在 `bail` 里总是短路**（`events.ts:12-14`、`:232-238`，实验 02）。
4. **`ctx.emit` 不隔离异常**：同步 throw 打断后续监听器并抛给 producer；async rejection 成为 unhandledRejection（`events.ts:207-210`，实验 04）。自己做 producer 时用 `events.dispatch` + 逐个 try/catch（`docs/defensive-patterns/general.zh.md:257-269`）。
5. **`emit` 不等待**：返回 promise 的 emit 监听器与 producer 并发执行，producer 后续代码看不到其结果（实验 02 emit 段）。
6. **顺序不可靠**：只有注册顺序 + `prepend`（unshift，后 prepend 的在最前），没有优先级（`events.ts:268-271`，实验 01）；其他插件也可以 prepend 到你前面（`packages/interaction/user-approval/src/index.ts:297-302`）。
7. **plugin 未激活前分发不会送达**：`ctx.plugin()` 不同步执行 apply（实验 00）。测试和脚本里先 `await`。
8. **disposer 返回值不可靠**：文档说返回 boolean，实测 `undefined`（实验 01）。
9. **作用域过滤**：harness 事件带 `Scoped<...>` carrier，作用域 ctx 里的监听器只收到本 scope（及后代）的事件，未打标签的根插件收到全部；`global: true` 绕过 filter（`packages/core/scope/src/index.ts:170-185`，`events.ts:170`，实验 05）。需要跨 scope 观察（如 terminal-bash）才用 `global`。
10. **事件名前不要放对象/函数**：会被当作 thisArg（`events.ts:163`）。自己调用 `events.dispatch` 时注意它会 `shift` 掉传入数组的前两个元素（实验 04 末尾）。
11. **`internal/dispatch` 是同步守卫点**：它的 throw 会让任意非 internal 事件分发失败（实验 05），所以只在明确要否决时 throw；观察用途务必包 try/catch。另外它报告的 `mode` 是 producer 传给 `dispatch` 的字符串，不一定是声明模式（`session/flush` 报 `emit`）。
12. **监听器是 effect**：Fiber 卸载后自动移除（实验 05）；在已卸载 ctx 上 `on` 抛 `INACTIVE_EFFECT`（`events.ts:291`、`:304`）。异步回调里延迟注册监听器要注意这一点。
13. **不要改 payload**：工具 `exec` 身份字段、`tools/result` 参数、session message 都被冻结或声明为 `Readonly`；`tools/execute` 只允许改 `exec.signal`（`packages/core/tools/src/events.ts:20-24`、`:43-49`）。
14. **`turn/*`、`tool/result` 等不是 Cordis 事件**，`ctx.on('turn/start')` 永远不会触发；监听 `session/event`（`docs/user/develop/framework/events.zh.md:106`）。

### Inferences
- 对照已有文章：`deepseek/dsh_plugin_guide.md:271` 称「分发方式分四种」并缺 `parallel`，正文若引用应更正为五种。

### Gaps
- 无。
