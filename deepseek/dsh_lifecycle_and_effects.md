---
title: dsh 插件的生命周期与副作用：Fiber、`ctx.effect` 和卸载顺序
kicker: 机制拆解
deck: dsh 插件的生死由一个 epoch 字符串驱动：依赖到齐时加载，其余任何变化都先卸载。`ctx.effect` 同步执行并先登记 disposer；卸载时同一 effect 内逆序串行，同一 Fiber 的顶层 effect 逆序启动、并发执行，提供方等所有依赖它的 Fiber 卸完再释放。本文用实际运行的实验把这些规则逐条验证，复现“提供方先释放资源”的四种形状和修法，并标出文档与源码对不上的地方。
meta:
  - deepseek-ai/deepseek-harness @ 4b399c3（2026-11-22）
  - '@deepseek-ai/cordis 4.2.0-alpha.1'
  - Node v24.22.2
  - 12 个实验脚本实际运行
footer: 来源：github.com/deepseek-ai/deepseek-harness commit 4b399c3 的 vendor/cordis/src（fiber.ts、reflect.ts、registry.ts）、docs/cordis-tutorial（02、03）、.agents/skills/cordis-styling-nitpicking/SKILL.md，以及 packages 下的 spill-local、skill-filesystem、subagent、mcp-client。代码面板按出处分三类：“仓库原文”按该 commit 逐字摘录并注明路径和行号；“本文实验（实际运行）”是为本文编写的脚本，用 npm 安装的 @deepseek-ai/cordis@4.2.0-alpha.1（与仓库 vendor/cordis 版本号一致，未逐字节核对）在 Node v24.22.2 上实际运行，运行了 e01、e02、e03、e03b（V1–V4 及 V1fix、V2fix、V4fix）、e03c、e04、e04b、e05、e06、e06b、e07、e08，输出原样贴出，毫秒数每次运行略有出入；“示意（未运行）”只用来说明写法，没有执行过。
index: index.html
updated: 2026-11-23
created: 2026-11-23 19:52
---

插件怎么写、怎么装，见[《给 dsh 写插件》](dsh_plugin_guide.html)；服务、事件、配置分层和 HMR 的细节，见[《dsh 的服务、事件、配置与插件实战》](dsh_services_events_config_plugin.html)。这一篇只讲插件**什么时候加载和卸载，卸载时按什么顺序撤销它留下的东西**。dsh 的插件会因为改配置、热重载、依赖消失被反复卸载和重新加载，`apply` 里做过的每件事都要能撤销干净，而且撤销的先后顺序会直接决定最后一次写入能不能落盘。

下面的结论分三种依据。读 deepseek-harness commit `4b399c3` 源码得出的，代码面板标“仓库原文”；用 npm 上同版本的 Cordis 实际跑过的，标“本文实验（实际运行）”，输出原样贴出；只用来说明写法、没有执行过的，标“示意（未运行）”。每个实验脚本都通过共用的 `lib.mjs` 监听 `internal/status` 事件，把状态变化打印成 `[status] 名字: 旧 -> 新`，左列是距进程启动的毫秒数。

- **epoch 决定往哪走**：epoch 是 `"<revision>:<依赖 impl uid>…"`，缺一个必需依赖就变成 `__INACTIVE__`。从 INACTIVE 变成真实值走加载，其他任何变化都先卸载
- **依赖消失回到 PENDING，不是 DISPOSED**：Fiber 留在原处，依赖回来后 `apply` 整个重跑；`apply` 抛错时先回滚 effect 再进 FAILED，只有 `update()` 能救回来
- **两层卸载规则**：同一个 effect 内的 disposer 逆序串行；同一 Fiber 的顶层 effect 逆序启动、`Promise.all` 并发执行；提供方等所有 dependents 卸完再释放
- **竞态只出现在绕开 inject 依赖边的地方**：父插件持有资源、兄弟 effect、`ctx.get` 拿到的服务、不归 Fiber 管的后台任务，四种形状都能稳定复现，也都能修
- **UNLOADING 期间不能新建 effect**：`ctx.on`、`ctx.plugin`、`ctx.effect` 都会抛 `INACTIVE_EFFECT`，JSDoc 只写了“已释放”这一种情况

## epoch：一个字符串决定 Fiber 往哪走

每个加载的插件实例都有一个 Fiber，`ctx.plugin()` 返回的就是它。`FiberState` 是一个 const enum，依次是 PENDING=0、LOADING=1、ACTIVE=2、FAILED=3、DISPOSED=4、UNLOADING=5。注意 DISPOSED 的数值比 UNLOADING 小，日志里看到 5 是 UNLOADING。

六个状态里有四个**不单独存储**，由 `_getState()` 从三个事实推导：`uid === null` 是 DISPOSED；否则 `_error` 有值是 FAILED；否则 epoch 不是 INACTIVE 是 ACTIVE；剩下的都是 PENDING。LOADING 和 UNLOADING 只在一次过渡（`inertia`）进行中被显式设置。

每次状态真正变化时，`_updateState` 发出 `internal/status(fiber, oldState)`，第二个参数是旧状态；进入或离开 ACTIVE 时，还会对本 Fiber 提供的每个服务调用 `notify`，让消费方重新检查依赖。卸载顺序的第一步就从这里开始。

```ts title="仓库原文 · vendor/cordis/src/fiber.ts:169-184"
/**
 * Lifecycle state for one plugin fiber.
 *
 * `PENDING` — waiting for required services; `LOADING` — the plugin callback
 * is running; `ACTIVE` — loaded and providing; `FAILED` — the callback or its
 * config threw; `UNLOADING` — disposers are running; `DISPOSED` — the fiber
 * was removed and cannot restart.
 */
export const enum FiberState {
  PENDING,
  LOADING,
  ACTIVE,
  FAILED,
  DISPOSED,
  UNLOADING,
}
```

```ts title="仓库原文 · vendor/cordis/src/fiber.ts:602-623"
  private _getState() {
    if (this.uid === null) return FiberState.DISPOSED
    if (this._error) return FiberState.FAILED
    if (this._runner.epoch !== INACTIVE) return FiberState.ACTIVE
    return FiberState.PENDING
  }

  private _updateState(callback: () => void | FiberState) {
    const oldState = this.state
    this.state = callback() ?? this._getState()
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

### epoch 怎么算：任何变化都先卸载

`_refresh()` 以 `_revision` 开头，为 inject 里的每个名字拼接 `:impl.uid`，缺任何一个就整体变成 INACTIVE。“可用”由 `_checkImpl` 判断：提供方 Fiber 必须是 ACTIVE，可选的 `check` 不抛错、不返回 false。

`_setEpoch` 有三条短路：epoch 没变什么也不做；**`_error` 有值也什么都不做**，注释写的是 “a failed fiber only recovers through update()”；已经有过渡在进行时，只记下目标值，等当前过渡结束再对齐。剩下只有两个分支：**INACTIVE 变成真实值走 `_reload()`，其余一律走 `_unload()`**。

因为 epoch 里带着 impl 的 uid，提供方换成新实例时即使服务名不变，所有消费方也会卸载再重跑。`restart()` 只是 `_revision++` 再 `_refresh()`，同样落到“epoch 变了”这一支。

```ts title="仓库原文 · vendor/cordis/src/fiber.ts:639-668"
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
    this._runner.epoch = epoch
    if (this.inertia) return
    this._updateState(() => {
      if (epoch !== INACTIVE && oldEpoch === INACTIVE) {
        this.inertia = this._reload()
        return FiberState.LOADING
      } else {
        this.inertia = this._unload()
        return FiberState.UNLOADING
      }
    })
  }
```

### 实际的转换比教程的单线图多三条边

把源码路径和实验输出放在一起，得到图 1。教程第 2 章画的是 `PENDING → LOADING → ACTIVE → UNLOADING → DISPOSED`，再加一条 `LOADING ↘ FAILED`。实际上还有三类边：ACTIVE 经 UNLOADING 回到 PENDING（依赖消失）、UNLOADING 直接回到 LOADING（restart、换提供方、默认的配置更新），以及失败时**先经过 UNLOADING 回滚 effect** 才落到 FAILED。

图里每条边都对应下表中至少一次实际运行。dispose() 也走 ACTIVE→UNLOADING 这条边：它把 uid 置为 null，所以卸载结束时 `_getState()` 得出 DISPOSED。

```svg title="图 1：Fiber 状态机，每条边都在实验中观察到" tone="light"
<svg viewBox="0 0 870 500" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="Fiber 状态机：PENDING、LOADING、ACTIVE、UNLOADING、DISPOSED、FAILED 之间的实际转换">
<defs><marker id="lf1" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#504e49"/></marker></defs>
<rect x="20" y="190" width="110" height="48" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="75" y="211" text-anchor="middle" font-size="12" font-weight="600" fill="#141413">PENDING</text><text x="75" y="227" text-anchor="middle" font-size="11" fill="#504e49">等必需依赖</text>
<rect x="200" y="190" width="110" height="48" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="255" y="211" text-anchor="middle" font-size="12" font-weight="600" fill="#141413">LOADING</text><text x="255" y="227" text-anchor="middle" font-size="11" fill="#504e49">apply 在跑</text>
<rect x="380" y="190" width="110" height="48" rx="8" fill="#1B365D" stroke="#1B365D" stroke-width="1.2"/><text x="435" y="211" text-anchor="middle" font-size="12" font-weight="600" fill="#ffffff">ACTIVE</text><text x="435" y="227" text-anchor="middle" font-size="11" fill="#dfe7f1">epoch ≠ INACTIVE</text>
<rect x="560" y="190" width="110" height="48" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="615" y="211" text-anchor="middle" font-size="12" font-weight="600" fill="#141413">UNLOADING</text><text x="615" y="227" text-anchor="middle" font-size="11" fill="#504e49">disposer 在跑</text>
<rect x="740" y="190" width="110" height="48" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="795" y="211" text-anchor="middle" font-size="12" font-weight="600" fill="#141413">DISPOSED</text><text x="795" y="227" text-anchor="middle" font-size="11" fill="#504e49">uid === null</text>
<rect x="380" y="380" width="110" height="48" rx="8" fill="#EEF2F7" stroke="#1B365D" stroke-width="1.2"/><text x="435" y="401" text-anchor="middle" font-size="12" font-weight="600" fill="#141413">FAILED</text><text x="435" y="417" text-anchor="middle" font-size="11" fill="#504e49">_error 有值</text>
<line x1="131" y1="214" x2="197" y2="214" stroke="#504e49" stroke-width="1.4" marker-end="url(#lf1)"/>
<line x1="311" y1="214" x2="377" y2="214" stroke="#504e49" stroke-width="1.4" marker-end="url(#lf1)"/>
<line x1="491" y1="214" x2="557" y2="214" stroke="#504e49" stroke-width="1.4" marker-end="url(#lf1)"/>
<line x1="671" y1="214" x2="737" y2="214" stroke="#504e49" stroke-width="1.4" marker-end="url(#lf1)"/>
<text x="165" y="207" text-anchor="middle" font-size="11" fill="#504e49">依赖齐全</text>
<text x="345" y="207" text-anchor="middle" font-size="11" fill="#504e49">apply 结束</text>
<text x="525" y="207" text-anchor="middle" font-size="11" fill="#504e49">epoch 变化</text>
<text x="705" y="207" text-anchor="middle" font-size="11" fill="#504e49">uid = null</text>
<path d="M 600 189 C 600 120, 270 120, 270 187" fill="none" stroke="#504e49" stroke-width="1.4" marker-end="url(#lf1)"/>
<text x="410" y="118" text-anchor="middle" font-size="11" font-weight="600" fill="#141413">epoch 换成新值：卸完立刻重载</text>
<text x="410" y="132" text-anchor="middle" font-size="11" fill="#504e49">restart() · update() · 换提供方</text>
<path d="M 580 189 C 580 30, 75 30, 75 187" fill="none" stroke="#504e49" stroke-width="1.4" marker-end="url(#lf1)"/>
<text x="327" y="42" text-anchor="middle" font-size="11" font-weight="600" fill="#141413">epoch 变为 INACTIVE（必需依赖消失）：卸完回到 PENDING</text>
<text x="327" y="57" text-anchor="middle" font-size="11" fill="#504e49">Fiber 仍在注册表里，依赖回来后 apply 整个重跑</text>
<path d="M 270 239 C 270 300, 600 300, 600 241" fill="none" stroke="#504e49" stroke-width="1.4" marker-end="url(#lf1)"/>
<text x="435" y="306" text-anchor="middle" font-size="11" fill="#504e49">apply 抛错（置 _error）</text>
<text x="435" y="321" text-anchor="middle" font-size="11" fill="#504e49">或 LOADING 中被 dispose()</text>
<line x1="640" y1="239" x2="493" y2="399" stroke="#504e49" stroke-width="1.4" marker-end="url(#lf1)"/>
<text x="528" y="348" text-anchor="end" font-size="11" fill="#504e49">effect 回滚完，</text>
<text x="528" y="363" text-anchor="end" font-size="11" fill="#504e49">_error 仍在</text>
<line x1="379" y1="398" x2="242" y2="241" stroke="#504e49" stroke-width="1.4" marker-end="url(#lf1)"/>
<text x="300" y="352" text-anchor="end" font-size="11" fill="#504e49">只有 update() 清掉 _error</text>
<text x="300" y="367" text-anchor="end" font-size="11" fill="#504e49">restart() 直接以原错误 reject</text>
<path d="M 60 239 C 60 520, 655 520, 660 241" fill="none" stroke="#504e49" stroke-width="1.4" stroke-dasharray="5 4" marker-end="url(#lf1)"/>
<text x="360" y="486" text-anchor="middle" font-size="11" fill="#504e49">PENDING 时被 dispose()：经 UNLOADING 到 DISPOSED（e08 里的子 Fiber）</text>
</svg>
```

| 触发 | 状态序列 | 依据 |
|---|---|---|
| 依赖齐全，首次加载 | PENDING → LOADING → ACTIVE | 实验 e01 |
| 必需依赖消失 | ACTIVE → UNLOADING → **PENDING**，Fiber 仍在注册表里 | 实验 e01、e08 |
| 依赖恢复 | PENDING → LOADING → ACTIVE，**apply 整个重跑** | 实验 e01 |
| `restart()` / 换提供方 / 默认的 `update()` | ACTIVE → UNLOADING → LOADING → ACTIVE | 实验 e07；换提供方仅源码 |
| apply 同步抛错或异步 reject | LOADING → UNLOADING → FAILED，先回滚 effect | 实验 e04 |
| FAILED 时 `update(config)` | FAILED → LOADING → ACTIVE | 实验 e04、e04b |
| FAILED 时 `restart()` | 状态不变，以原错误 reject | 实验 e04b |
| LOADING 中 `dispose()` | 等 apply 结束，LOADING → UNLOADING → DISPOSED | 实验 e05 |
| PENDING 时 `dispose()` | PENDING → UNLOADING → DISPOSED | 实验 e08 |
| `dispose()` | ACTIVE → UNLOADING → DISPOSED | 实验 e01 |

### 依赖消失时消费方回到 PENDING，disposer 里仍能用服务

`e01` 先加载声明了 `inject: ['store']` 的消费方，再加载提供方，然后释放提供方，再重新加载它。消费方在自己的 disposer 里读一次 `ctx.store`，看服务在卸载期间是否还可用。

输出有三点值得注意。第一，最开始的 PENDING 没有触发 status 事件，因为状态没有变化。第二，`provider effect #1 disposed` 出现在 `consumer: UNLOADING -> PENDING` **之后**：提供方先等消费方卸完，才回收自己的 effect。第三，第二次 apply 拿到的是 `store#2`，说明 apply 闭包重新执行了一遍，必须写成可以重复执行的样子。

消费方能在 disposer 里继续用服务，靠的是 `fiber.committed`：`_reload()` 开始时把当时解析到的实现快照下来，注释叫它 “Required implementations retained through dependent teardown”，卸载期间 `locate()` 仍能从这里解析服务，哪怕注册表里的条目已经删掉。

```js title="本文实验（实际运行）· exp/e01-dep-reappear.mjs"
// Q1: consumer with inject:['store']; provider unloaded then reloaded.
import { makeRoot, log, sleep } from './lib.mjs'
const root = makeRoot()
let gen = 0
const provider = {
  name: 'provider',
  apply(ctx) {
    const id = ++gen
    log(`provider apply #${id}`)
    ctx.provide('store', { id, get: () => `store#${id}` })
    ctx.effect(() => () => log(`provider effect #${id} disposed`))
  },
}
let runs = 0
const consumer = {
  name: 'consumer',
  inject: ['store'],
  apply(ctx) {
    const run = ++runs
    log(`consumer apply run=${run}, sees ${ctx.store.get()}`)
    ctx.effect(() => () => log(`consumer run=${run} disposer: store reachable? ${(() => { try { return ctx.store.get() } catch (e) { return 'THROWS ' + e.message } })()}`))
  },
}
log('--- load consumer first (no provider yet)')
const c = root.plugin(consumer)
await sleep(5)
log('consumer state =', c.state)
log('--- load provider')
let p = root.plugin(provider)
await p; await sleep(5)
log('--- dispose provider')
await p.dispose()
await sleep(5)
log('consumer state =', c.state)
log('--- reload provider')
p = root.plugin(provider)
await p; await sleep(5)
log('consumer state =', c.state, 'runs =', runs)
log('--- dispose consumer')
await c.dispose()
log('node', process.version)
```

```text title="本文实验输出（实际运行，输出原样）· node e01-dep-reappear.mjs"
   2ms --- load consumer first (no provider yet)
  10ms consumer state = 0
  10ms --- load provider
  10ms   [status] provider: PENDING -> LOADING
  10ms provider apply #1
  11ms   [status] provider: LOADING -> ACTIVE
  11ms   [status] consumer: PENDING -> LOADING
  11ms consumer apply run=1, sees store#1
  11ms   [status] consumer: LOADING -> ACTIVE
  16ms --- dispose provider
  17ms   [status] provider: ACTIVE -> UNLOADING
  17ms   [status] consumer: ACTIVE -> UNLOADING
  17ms consumer run=1 disposer: store reachable? store#1
  17ms   [status] consumer: UNLOADING -> PENDING
  17ms provider effect #1 disposed
  17ms   [status] provider: UNLOADING -> DISPOSED
  23ms consumer state = 0
  23ms --- reload provider
  23ms   [status] provider: PENDING -> LOADING
  23ms provider apply #2
  23ms   [status] provider: LOADING -> ACTIVE
  23ms   [status] consumer: PENDING -> LOADING
  23ms consumer apply run=2, sees store#2
  23ms   [status] consumer: LOADING -> ACTIVE
  29ms consumer state = 2 runs = 2
  29ms --- dispose consumer
  29ms   [status] consumer: ACTIVE -> UNLOADING
  29ms consumer run=2 disposer: store reachable? store#2
  29ms   [status] consumer: UNLOADING -> DISPOSED
  29ms node v24.22.2
```

> 教程第 6 章也说 PENDING 是正常状态：缺提供方的插件会一直等着，什么也不打印，而且不会让 Node 事件循环保持存活。看到插件“没反应”，先查它是不是停在 PENDING。

### apply 抛错：先回滚，再进 FAILED

`_reload()` 捕获到错误后，如果 epoch 没被别人改过，就设置 `_error` 并把 epoch 改成 INACTIVE。新 epoch 和旧值不同，于是最后的 `_updateState` 走 `_unload()`：**失败的加载也会完整卸载一次**，已经注册的 effect 被回滚。卸载结束后 `_getState()` 看到 `_error`，状态落在 FAILED。

```ts title="仓库原文 · vendor/cordis/src/fiber.ts:703-732"
  private async _reload() {
    this.committed = { ...this._store }
    for (const impl of Object.values(this.committed)) {
      if (impl.ctx.fiber === this) continue
      impl.dependents.add(this)
    }
    const oldEpoch = this._runner.epoch
    try {
      await Promise.resolve()
      if (this._runner.epoch === oldEpoch) {
        await this._execute(this._runner)
      }
    } catch (reason) {
      // impl guarantees that the error is non-null (?)
      this.ctx.logger.error(reason)
      // A superseded load still needs cleanup, but cannot fail the new target.
      if (this._runner.epoch === oldEpoch) {
        this._error = reason
        this._runner.epoch = INACTIVE
      }
    }
    this._updateState(() => {
      if (this._runner.epoch === oldEpoch) {
        this.inertia = undefined
      } else {
        this.inertia = this._unload()
        return FiberState.UNLOADING
      }
    })
  }
```

`e04` 里的插件先注册一个包在 effect 里的 `setInterval` 和一个 `ctx.on('custom/ping')`，然后同步抛错，或者 `await sleep(3)` 后抛错。两种情况下 interval effect 都在 UNLOADING 期间被释放，失败后 20ms 内计时器一次也没触发，`getEffects()` 为空，发出 `custom/ping` 也没有监听器响应。之后用 `fiber.update({ ok: true })` 恢复，状态 FAILED → LOADING → ACTIVE。

```js title="本文实验（实际运行）· exp/e04-apply-fails.mjs"
// Q4: apply() throws synchronously or rejects after await: state, cleanup of already-registered effects, recovery.
import { makeRoot, log, sleep } from './lib.mjs'
const root = makeRoot()
root.logger && (root.logger.error = (e) => log('  [logger.error]', e?.message ?? e))
let ticks = 0
function mk(name, kind) {
  return { name, apply(ctx, config) {
    ctx.effect(() => { const h = setInterval(() => ticks++, 2); return () => { clearInterval(h); log(`  ${name}: interval effect disposed`) } })
    ctx.on('custom/ping', () => log(`  ${name}: got ping`))
    if (config?.ok) { log(`  ${name}: apply ok with config`, JSON.stringify(config)); return }
    if (kind === 'sync') throw new Error(`${name} boom (sync)`)
    return (async () => { await sleep(3); throw new Error(`${name} boom (async)`) })()
  } }
}
for (const kind of ['sync', 'async']) {
  log(`=== ${kind}`)
  const f = root.plugin(mk(`p-${kind}`, kind))
  try { await f; log('  await fiber resolved') } catch (e) { log('  await fiber rejected:', e.message) }
  log(`  state=${f.state}`)
  const before = ticks; await sleep(20); log(`  interval ticks during 20ms after failure: ${ticks - before}`)
  await root.parallel('custom/ping')
  log('  effects still registered:', JSON.stringify(f.getEffects().map(e => e.label)))
  log('  --- recover via fiber.update({ ok: true })')
  try { await f.update({ ok: true }) } catch (e) { log('  update rejected', e.message) }
  log(`  state=${f.state}`)
  await root.parallel('custom/ping')
  await f.dispose()
}
```

```text title="本文实验输出（实际运行，输出原样）· node e04-apply-fails.mjs" full
   1ms === sync
   3ms   [status] p-sync: PENDING -> LOADING
   3ms   [logger.error] p-sync boom (sync)
   3ms   [status] p-sync: LOADING -> UNLOADING
   3ms   p-sync: interval effect disposed
   3ms   [status] p-sync: UNLOADING -> FAILED
   4ms   await fiber rejected: p-sync boom (sync)
   4ms   state=3
  25ms   interval ticks during 20ms after failure: 0
  25ms   effects still registered: []
  25ms   --- recover via fiber.update({ ok: true })
  25ms   [status] p-sync: FAILED -> LOADING
  25ms   p-sync: apply ok with config {"ok":true}
  25ms   [status] p-sync: LOADING -> ACTIVE
  25ms   state=2
  25ms   p-sync: got ping
  25ms   [status] p-sync: ACTIVE -> UNLOADING
  25ms   p-sync: interval effect disposed
  25ms   [status] p-sync: UNLOADING -> DISPOSED
  25ms === async
  25ms   [status] p-async: PENDING -> LOADING
  29ms   [logger.error] p-async boom (async)
  29ms   [status] p-async: LOADING -> UNLOADING
  29ms   p-async: interval effect disposed
  29ms   [status] p-async: UNLOADING -> FAILED
  29ms   await fiber rejected: p-async boom (async)
  29ms   state=3
  50ms   interval ticks during 20ms after failure: 0
  50ms   effects still registered: []
  50ms   --- recover via fiber.update({ ok: true })
  50ms   [status] p-async: FAILED -> LOADING
  50ms   p-async: apply ok with config {"ok":true}
  50ms   [status] p-async: LOADING -> ACTIVE
  50ms   state=2
  50ms   p-async: got ping
  50ms   [status] p-async: ACTIVE -> UNLOADING
  50ms   p-async: interval effect disposed
  50ms   [status] p-async: UNLOADING -> DISPOSED
```

### FAILED 只能靠 `update()` 恢复，`restart()` 不行

`e04b` 的插件第一次 apply 抛错、第二次成功。FAILED 之后先 `restart()`，结果直接以原来的错误 reject，`attempts` 仍是 1，apply 根本没有再跑；再用**同样的配置**调用 `update(f.config)`，就恢复到 ACTIVE。

原因在源码里很直接：`restart()` 只做 `_revision++` 和 `_refresh()`，而 `_setEpoch` 在 `_error` 有值时直接返回；`update()` 则会先执行 `fiber._error = undefined`。

还有一点只读了源码、没有实测：配置校验是 LOADING 阶段在 `_execute` 里先跑的 `internal/config` waterfall，所以一个停在 PENDING 的插件即使配置写错，也要等依赖到齐后才会暴露成 FAILED。

```js title="本文实验（实际运行）· exp/e04b-restart-failed.mjs"
// Q4b: can a FAILED fiber be recovered by restart() (same config), or only by update()?
import { makeRoot, log } from './lib.mjs'
const root = makeRoot()
root.logger.error = (e) => log('  [logger.error]', e?.message ?? e)
let attempts = 0
const f = root.plugin({ name: 'flaky', apply() { if (++attempts === 1) throw new Error('first attempt fails') ; log('  apply ok, attempt', attempts) } })
await f.catch?.(() => {}) ; try { await f } catch {}
log('state', f.state, 'attempts', attempts)
try { await f.restart(); log('  restart resolved') } catch (e) { log('  restart rejected:', e.message) }
log('state', f.state, 'attempts', attempts)
try { await f.update(f.config); log('  update resolved') } catch (e) { log('  update rejected:', e.message) }
log('state', f.state, 'attempts', attempts)
```

```text title="本文实验输出（实际运行，输出原样）· node e04b-restart-failed.mjs"
   2ms   [status] flaky: PENDING -> LOADING
   3ms   [logger.error] first attempt fails
   3ms   [status] flaky: LOADING -> UNLOADING
   3ms   [status] flaky: UNLOADING -> FAILED
   3ms state 3 attempts 1
   3ms   restart rejected: first attempt fails
   3ms state 3 attempts 1
   3ms   [status] flaky: FAILED -> LOADING
   3ms   apply ok, attempt 2
   3ms   [status] flaky: LOADING -> ACTIVE
   3ms   update resolved
   3ms state 2 attempts 2
```

```ts title="仓库原文 · vendor/cordis/src/fiber.ts:790-796"
  async restart() {
    const fiber = this.ctx.fiber
    fiber.assertActive()
    fiber._revision++
    fiber._refresh()
    await fiber.await()
  }
```

### 配置更新默认完整重载，拦截 `internal/update` 才能原地生效

`update(config)` 对已加载的 Fiber 先同步校验（失败时在提交前抛 `ValidationError`），发出 `internal/commit`，清掉 `_error`，然后走一次 `internal/update` waterfall，链条最后的 `next` 是 `restart`。没有监听器时就是完整的卸载再加载；PENDING 或 FAILED 的 Fiber 直接 restart。

```ts title="仓库原文 · vendor/cordis/src/fiber.ts:805-841"
  update(config: any, noSave = false): Awaitable<void> {
    const fiber = this.ctx.fiber
    fiber.assertActive()
    const loaded = fiber._runner.epoch !== INACTIVE
    const resolved = loaded ? fiber.resolveConfig(config) : undefined
    if (!noSave) fiber.context.emit('internal/commit', fiber, config)
    fiber.config = config
    fiber._error = undefined
    const configRevision = ++fiber._configRevision
    const epoch = fiber._runner.epoch
    const committed = fiber.committed
    let restarted = false
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
    const task = Promise.resolve(result).then(publish)
    // the failure is already reported by the fiber, so mark it handled here:
    // a caller that drops the result cannot turn it into an unhandled
    // rejection, while `await update()` still observes it
    task.catch(() => {})
    return task
  }
```

`e07` 对比了三种情况：没有监听器的插件完整重载；监听 `internal/update` 且不调用 `restart` 时原地更新，apply 不重跑；`restart()` 总是重载。非 global 的 `ctx.on('internal/update')` 只对当前 Fiber 生效，它本身也是一个 effect。

不过在 packages 下 2819 个非测试 `.ts` 文件里，`'internal/update'` 一处都没有出现，它只在 vendor 的 loader、group、include 中使用。dsh 插件实际的“不重启就生效”路径是 schema 里的 `.volatile()` 字段，由 loader 绕过 `internal/update` 发出 `loader/volatile-update`，条件和细节见[《dsh 的服务、事件、配置与插件实战》的配置一节](dsh_services_events_config_plugin.html)。也就是说，**每次非 volatile 的配置变更都会把后文所有卸载顺序问题重走一遍**。

```js title="本文实验（实际运行）· exp/e07-config-update.mjs"
// Q7: fiber.update(config): full reload by default vs in-place when an internal/update listener skips next().
import { makeRoot, log, sleep } from './lib.mjs'
const root = makeRoot()
let applies = 0
const reloader = { name: 'reloader', apply(ctx, config) {
  const n = ++applies
  log(`  reloader apply #${n} interval=${config.interval}`)
  ctx.effect(() => () => log(`  reloader #${n} disposed`))
} }
let inplaceApplies = 0
const inplace = { name: 'inplace', apply(ctx, config) {
  const n = ++inplaceApplies
  let current = config
  log(`  inplace apply #${n} interval=${config.interval}`)
  ctx.effect(() => () => log(`  inplace #${n} disposed`))
  ctx.on('internal/update', (next, restart) => {
    if (next.mode !== current.mode) { log(`  inplace: mode changed -> restart`); return restart() }
    log(`  inplace: hot-applied interval ${current.interval} -> ${next.interval} (no restart)`)
    current = next
  })
} }
log('=== default plugin')
const a = root.plugin(reloader, { interval: 1 }); await a
await a.update({ interval: 2 })
log(`  applies=${applies}, a.config=${JSON.stringify(a.config)}`)
log('=== plugin with internal/update listener')
const b = root.plugin(inplace, { interval: 1, mode: 'x' }); await b
await b.update({ interval: 5, mode: 'x' })
log(`  inplaceApplies=${inplaceApplies}, b.config=${JSON.stringify(b.config)}, effectiveConfig=${JSON.stringify(b.effectiveConfig)}`)
await b.update({ interval: 5, mode: 'y' })
log(`  inplaceApplies=${inplaceApplies}`)
log('=== restart() always reloads')
await a.restart()
log(`  applies=${applies}`)
```

```text title="本文实验输出（实际运行，输出原样）· node e07-config-update.mjs"
   1ms === default plugin
   3ms   [status] reloader: PENDING -> LOADING
   3ms   reloader apply #1 interval=1
   3ms   [status] reloader: LOADING -> ACTIVE
   4ms   [status] reloader: ACTIVE -> UNLOADING
   4ms   reloader #1 disposed
   4ms   [status] reloader: UNLOADING -> LOADING
   4ms   reloader apply #2 interval=2
   4ms   [status] reloader: LOADING -> ACTIVE
   4ms   applies=2, a.config={"interval":2}
   4ms === plugin with internal/update listener
   4ms   [status] inplace: PENDING -> LOADING
   4ms   inplace apply #1 interval=1
   4ms   [status] inplace: LOADING -> ACTIVE
   4ms   inplace: hot-applied interval 1 -> 5 (no restart)
   4ms   inplaceApplies=1, b.config={"interval":5,"mode":"x"}, effectiveConfig={"interval":5,"mode":"x"}
   4ms   inplace: mode changed -> restart
   4ms   [status] inplace: ACTIVE -> UNLOADING
   4ms   inplace #1 disposed
   4ms   [status] inplace: UNLOADING -> LOADING
   4ms   inplace apply #2 interval=5
   4ms   [status] inplace: LOADING -> ACTIVE
   4ms   inplaceApplies=2
   4ms === restart() always reloads
   4ms   [status] reloader: ACTIVE -> UNLOADING
   4ms   reloader #2 disposed
   4ms   [status] reloader: UNLOADING -> LOADING
   4ms   reloader apply #3 interval=2
   4ms   [status] reloader: LOADING -> ACTIVE
   4ms   applies=3
```

## `ctx.effect`：立即执行，先登记 disposer，再执行插件代码

`ctx.effect(execute, label)` 是 mixin 到 ctx 上的 `fiber.effect`。`execute` **同步立即执行**，可以返回一个 disposer 或 nullish，也可以返回 `Promise<disposer>`、同步生成器或异步生成器；返回其他值抛 `TypeError('Invalid effect')`。

入口有两道检查：先 `assertActive()`，`uid === null` 时抛 `INACTIVE_EFFECT`；**接着 `state === UNLOADING` 时也抛 `INACTIVE_EFFECT`**。后一条 JSDoc 没写。它意味着在 disposer 里、或卸载期间触发的回调里调用 `ctx.on`、`ctx.plugin`、`ctx.effect`、定时器服务都会失败。这一条只读了源码，本文没有专门的实验。

同一段代码里的 `dispose` 就是“单个 effect 内”的卸载规则：`disposables.splice(0).reverse()` 逆序遍历，一旦某个 disposer 返回 thenable，后面的都用 `task.then` 串起来，**严格串行**。

```ts title="仓库原文 · vendor/cordis/src/fiber.ts:446-470"
  effect(execute: () => Effect, label = 'anonymous'): any {
    this.assertActive()
    if (this.state === FiberState.UNLOADING) {
      throw new CordisError('INACTIVE_EFFECT')
    }

    const disposables: Disposable[] = []
    let disposing = false
    let disposalTask: void | Promise<void>
    const dispose = () => {
      if (disposing) return disposalTask
      disposing = true
      let task!: void | Promise<void>
      for (const disposable of disposables.splice(0).reverse()) {
        if (task) {
          task = task.then(() => runDisposable(disposable))
        } else {
          const result = runDisposable(disposable)
          if (isObject(result) && 'then' in result) {
            task = result as any
          }
        }
      }
      return disposalTask = task
    }
```

包装好的 disposer 在 `_execute` **之前**就 push 进 `fiber._disposables`。这样即使插件代码在 execute 里同步触发了重入的卸载，Fiber 也看得到这个 effect，不会漏掉。

```ts title="仓库原文 · vendor/cordis/src/fiber.ts:545-550"
    // Make the effect visible to a reentrant owner unload before execute()
    // runs any plugin code. Async teardown stays owner-visible until it
    // settles, allowing an outer effect to join cleanup another caller began.
    removeWrapper = this._disposables.push(wrapper)
    try {
      task = this._execute(runner)
```

### 所有权按函数身份转移

嵌套 effect 靠 runner 的 `collect` 转移所有权：它把收到的 disposer 放进本 effect 的局部列表，**同时从 `fiber._disposables` 里按函数身份删除它**，并把元数据挂到 `meta.children`。

由此有两个容易踩的坑。在 effect 里调用另一个 `ctx.effect`，却不 return 或 yield 它返回的 disposer，内层 effect 仍然是 Fiber 顶层的兄弟。yield 一个包装函数，例如 `() => inner()`，也不行，因为删除按身份匹配，原 disposer 留在顶层，照样和别人并发执行。第 4 节的 V1fix 能不能成功，就取决于这一点。

其余行为来自同一段源码：disposer 只执行一次；setup 还在进行时调用它，会等 setup 结束再清理；setup 同步抛错时已收集的部分回滚并把错误抛给调用方；异步 reject 被接住、回滚后交给 `ctx.logger.error`。异步生成器在每次 `iter.next()` 前检查 epoch，卸载后不再获取后面的资源；普通 async effect 中途无法打断，只能等 Promise 落定。

```ts title="仓库原文 · vendor/cordis/src/fiber.ts:471-484"

    const meta: EffectMeta = { label, children: [] }
    const runner: EffectRunner<boolean> = {
      execute,
      epoch: true,
      collect: (dispose) => {
        disposables.push(dispose)
        this._disposables.delete(dispose)
        if (dispose[symbols.effect]) {
          meta.children.push(dispose[symbols.effect])
        }
      },
      getOuterStack: buildOuterStack(),
    }
```

### `await` 之后：ctx API 会抛错，裸资源会泄漏

`e05` 在 apply 的 `await sleep(20)` 期间调用 `dispose()`，然后在 await 之后分别尝试 `ctx.on`、`ctx.effect`，以及创建一个裸 `setInterval`。第二段把异步连接放进 `ctx.effect(async function* …)` 作对照。

结论有四点。**`dispose()` 立即把 uid 置为 null，然后等正在运行的 apply 结束**：8ms 调用，24ms 才 resolve，期间状态一直是 LOADING。await 之后通过 ctx API 注册会同步抛 `INACTIVE_EFFECT`，effect 函数体根本不执行，所以不会泄漏。裸 `setInterval` 在卸载后的 60ms 里又触发了 10 次。异步生成器 effect 等连接建好后，马上执行 yield 出的 close，连接数回到 0。

还能看到一个细节：apply 是 async 时，插件在 await 期间一直是 LOADING；apply 同步返回、只有 effect 是异步时，插件立即进入 ACTIVE（`good` 在 85ms 已经 ACTIVE，连接 106ms 才建立）。

```js title="本文实验（实际运行）· exp/e05-effect-after-await.mjs"
// Q5: apply awaits, the plugin is disposed meanwhile, then apply registers effects / starts raw resources.
import { makeRoot, log, sleep } from './lib.mjs'
const root = makeRoot()
root.logger.error = (e) => log('  [logger.error]', e?.code ?? '', e?.message ?? e)
let leakedTicks = 0, guardedTicks = 0
const slow = {
  name: 'slow',
  async apply(ctx) {
    log('  apply: before await')
    await sleep(20)                                      // e.g. connecting to something
    log('  apply: after await, fiber.state =', ctx.fiber.state, 'uid =', ctx.fiber.uid)
    // (a) raw timer started without ctx.effect -> nobody will ever clear it
    setInterval(() => leakedTicks++, 5).unref()
    // (b) ctx.on after disposal
    try { ctx.on('custom/x', () => {}); log('  ctx.on: registered') } catch (e) { log('  ctx.on threw:', e.code ?? '', e.message) }
    // (c) ctx.effect after disposal: does it throw, and is the body executed?
    try {
      ctx.effect(() => { log('  effect body ran'); const h = setInterval(() => guardedTicks++, 5); return () => clearInterval(h) })
      log('  ctx.effect: registered')
    } catch (e) { log('  ctx.effect threw:', e.code ?? '', e.message) }
  },
}
const f = root.plugin(slow)
await sleep(5)
log('=== dispose while apply is awaiting; state =', f.state)
const d = f.dispose()
log('  dispose() called, state =', f.state)
await d
log('  dispose() resolved, state =', f.state)
await sleep(60)
log(`  after 60ms: raw interval ticks=${leakedTicks}, effect interval ticks=${guardedTicks}`)

// Pattern: acquire inside an async ctx.effect so the framework owns the half-acquired resource
let conns = 0
const good = {
  name: 'good',
  apply(ctx) {
    ctx.effect(async function* () {
      log('  good: connecting...')
      await sleep(20)
      conns++; log('  good: connected (conns=' + conns + ')')
      yield () => { conns--; log('  good: connection closed (conns=' + conns + ')') }
    }, 'connection')
  },
}
log('=== async-generator effect, disposed mid-connect')
const g = root.plugin(good); await sleep(5)
await g.dispose()
log('  dispose resolved; conns =', conns)
await sleep(40)
log('  40ms later; conns =', conns)
process.exit(0)
```

```text title="本文实验输出（实际运行，输出原样）· node e05-effect-after-await.mjs"
   1ms   [status] slow: PENDING -> LOADING
   3ms   apply: before await
   8ms === dispose while apply is awaiting; state = 1
   8ms   dispose() called, state = 1
  23ms   apply: after await, fiber.state = 1 uid = null
  24ms   ctx.on threw: INACTIVE_EFFECT cannot create effect on inactive context
  24ms   ctx.effect threw: INACTIVE_EFFECT cannot create effect on inactive context
  24ms   [status] slow: LOADING -> UNLOADING
  24ms   [status] slow: UNLOADING -> DISPOSED
  24ms   dispose() resolved, state = 4
  84ms   after 60ms: raw interval ticks=10, effect interval ticks=0
  85ms === async-generator effect, disposed mid-connect
  85ms   [status] good: PENDING -> LOADING
  85ms   good: connecting...
  85ms   [status] good: LOADING -> ACTIVE
  90ms   [status] good: ACTIVE -> UNLOADING
 106ms   good: connected (conns=1)
 106ms   good: connection closed (conns=0)
 107ms   [status] good: UNLOADING -> DISPOSED
 107ms   dispose resolved; conns = 0
 148ms   40ms later; conns = 0
```

> mcp-client 正是为这种情况写了一个监听器：看到自己的 `internal/plugin` 事件里 `uid === null`，就先关闭 transport，释放还在等回复的启动请求，避免 `dispose()` 被一直挂起的 apply 拖住。见第 6 节。

### 定时器、子进程、watcher、socket：包进 effect 才会被回收

`e06` 用两个版本比较五类资源：定时器、子进程、`fs.watch`、TCP server 和 client。leaky 版本直接创建；owned 版本每样都包进 effect。leaky 版本在插件 DISPOSED 之后，9 个句柄一个没少，子进程还活着，进程 1 秒内退不出来，以 exit 1 结束；owned 版本句柄清零，子进程收到 SIGTERM，进程以 exit 0 退出。

owned 版本里子进程的写法有个关键细节：**在 kill 之前**先执行 `const exited = once(child, 'exit')`，disposer 写成 `async () => { if (还没退出) child.kill('SIGTERM'); await exited }`，等进程真正退出。

```js title="本文实验（实际运行）· exp/e06-resources.mjs:26-49 · owned 版本"
const owned = {
  name: 'owned',
  async apply(ctx) {
    ctx.effect(() => { const h = setInterval(() => {}, 1000); return () => clearInterval(h) }, 'timer')
    ctx.effect(() => {
      child = spawn('sleep', ['30'])
      const exited = once(child, 'exit')          // subscribe BEFORE killing
      return async () => {
        if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM')
        const [code, signal] = await exited      // reach quiescence, not just request it
        log(`  child exited code=${code} signal=${signal}`)
      }
    }, 'child process')
    ctx.effect(() => { const w = watch(dir, () => {}); return () => w.close() }, 'watcher')
    await ctx.effect(async function* () {
      const server = net.createServer(s => s.on('data', () => {})).listen(0)
      const sockets = new Set(); server.on('connection', s => { sockets.add(s); s.on('close', () => sockets.delete(s)) })
      yield () => new Promise(r => { for (const s of sockets) s.destroy(); server.close(() => r()) })
      await once(server, 'listening')
      const client = net.connect(server.address().port)
      yield () => client.destroy()
    }, 'server+client')
  },
}
```

```text title="本文实验输出（实际运行，输出原样）· node e06-resources.mjs leaky / owned"
   1ms === leaky; baseline resources: []
  64ms   loaded; resources: [FSEventWrap,PipeWrap,PipeWrap,PipeWrap,ProcessWrap,TCPServerWrap,TCPSocketWrap,TCPSocketWrap,Timeout]
 116ms   disposed (state=4); resources: [FSEventWrap,PipeWrap,PipeWrap,PipeWrap,ProcessWrap,TCPServerWrap,TCPSocketWrap,TCPSocketWrap,Timeout]
 116ms   child pid 134529 still running? true
1120ms   process did NOT exit by itself within 1s -> leaked handles keep the event loop alive

   2ms === owned; baseline resources: []
  66ms   loaded; resources: [FSEventWrap,PipeWrap,PipeWrap,PipeWrap,ProcessWrap,TCPServerWrap,TCPSocketWrap,TCPSocketWrap,Timeout]
  68ms   child exited code=null signal=SIGTERM
 118ms   disposed (state=4); resources: []
 118ms   child pid 134573 still running? false
```

`e06b` 模拟一个收到 SIGTERM 后要 300ms 收尾的子进程，比较只 kill 和 kill 后等退出。只 kill 时 `dispose()` 1ms 就 resolve 了，子进程在那之后又活了约 300ms；等退出时 `dispose()` 要等到子进程真的退出才 resolve。这就是审计技能里 “Aborting or killing without awaiting settlement is a violation” 针对的情形。

```js title="本文实验（实际运行）· exp/e06b-kill-no-await.mjs"
// Q6b: "requesting" stop vs "reaching quiescence": kill() without awaiting exit.
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { makeRoot, log } from './lib.mjs'
const mode = process.argv[2] ?? 'noawait'
const root = makeRoot({ trace: false })
let child, exitedAt
const plugin = { name: 'proc', apply(ctx) {
  ctx.effect(() => {
    // child traps SIGTERM and needs ~300ms to shut down (like a server flushing state)
    child = spawn('sh', ['-c', 'trap "sleep 0.3; exit 0" TERM; while true; do sleep 0.05; done'])
    const exited = once(child, 'exit').then(r => { exitedAt = performance.now(); return r })
    return mode === 'await'
      ? async () => { child.kill('SIGTERM'); await exited }
      : () => { child.kill('SIGTERM') }
  })
} }
const f = root.plugin(plugin); await f
await new Promise(r => setTimeout(r, 100))
const t0 = performance.now()
await f.dispose()
const t1 = performance.now()
log(`  mode=${mode}: dispose() took ${Math.round(t1 - t0)}ms; child alive at dispose resolve? ${exitedAt === undefined}`)
while (exitedAt === undefined) await new Promise(r => setTimeout(r, 20))
log(`  child actually exited ${Math.round(exitedAt - t0)}ms after dispose() started`)
```

```text title="本文实验输出（实际运行，输出原样）· node e06b-kill-no-await.mjs noawait / await"
 106ms   mode=noawait: dispose() took 1ms; child alive at dispose resolve? true
 419ms   child actually exited 309ms after dispose() started

 464ms   mode=await: dispose() took 359ms; child alive at dispose resolve? false
 464ms   child actually exited 359ms after dispose() started
```

### 几乎所有注册 API 都是 effect

下表的 API 都通过 `fiber.effect` 实现，`fiber.getEffects()` 能看到它们的 label。`e02` 的输出里，`ctx.plugin()` 就作为一个 effect 出现在列表中。

| API | effect label | 说明 |
|---|---|---|
| `ctx.on` / `ctx.once` | `ctx.on("name")` | once 是带自我释放的 on |
| `ctx.on('internal/update')` | `ctx.on("internal/update")` | 存到当前 Fiber 的 `_hooks`，只对本 Fiber 生效 |
| `ctx.plugin` / `ctx.inject` | `ctx.plugin()` | 子 Fiber 是父 Fiber 的一个 effect；`inject` 就是 `plugin({ inject, apply })` |
| `ctx.provide`、`Service` 构造 | `ctx.provide("name")` | disposer 是异步的，要等 dependents 清空 |
| `ctx.accessor` / `ctx.mixin` | `ctx.accessor(...)` / `ctx.mixin(...)` | mixin 是生成器 effect，accessor 是它的子 effect |
| 定时器服务 `ctx.timeout` / `interval` / `throttle` / `debounce` | `ctx.timeout()` 等 | 需要 `inject: ['timer']`；Promise 形式在卸载时以 `Context has been disposed` reject |
| dsh 的 `ctx.tools.register` | `tools.register()` | 经 `ScopedLayers.effect`，是一个生成器 effect |

有一点特别要注意：服务方法通过 traceable 代理拿到的 `this.ctx` 是**调用方**的 ctx，所以服务内部调用 `this.ctx.effect` 时，effect 登记在调用方插件上，跟着调用方卸载。调用方追踪的机制见[《dsh 的服务、事件、配置与插件实战》](dsh_services_events_config_plugin.html)。Node 全局的 `setTimeout` / `setInterval` 不是 effect，要么自己包进 `ctx.effect`，要么改用定时器服务。

## 卸载顺序：effect 内串行，顶层并发，提供方等消费方

Fiber 层面的 `_unload()` 分三步。先是 **dependents 屏障**：只要本 Fiber 提供的任何 impl 还有 dependents，就一直等。然后对 `_disposables.clear()`（已经是逆序）的结果做 `await Promise.all(...)`，每个 disposer 先让出一次 microtask，再各自 try/catch 执行。最后从所用 impl 的 `dependents` 里删掉自己，唤醒正在等待的提供方。

所以**顶层 effect 按逆序启动，异步的那些并发执行**。子插件只是父插件的一个 `ctx.plugin()` effect，和父插件的其他 effect 同时开始清理；父插件要等 `Promise.all`，子插件的 disposer 又要等子 Fiber 完全卸载，因此父插件一定在子插件全部卸完之后才到达终态。

```ts title="仓库原文 · vendor/cordis/src/fiber.ts:734-768"
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
      if (this._runner.epoch === INACTIVE) {
        this.inertia = undefined
      } else {
        this.inertia = this._reload()
        return FiberState.LOADING
      }
    })
  }
```

### 实验：逆序启动，完成顺序只看耗时

`e02` 的父插件依次注册：A（异步 30ms）、一个子插件（内含 E1 20ms、E2 5ms）、B（异步 10ms）、C（同步），以及一个生成器 effect（yield y1、y2、y3，各 15ms）。

启动顺序是逆序的：生成器 → C → B → A；子插件的 E2、E1 多经过几层 microtask，排在 A 后面启动。完成顺序完全由耗时决定，说明兄弟之间是并发的。生成器内部 y3 → y2 → y1 严格串行。`await dispose()` 在最后一个 disposer 结束时（55ms）才 resolve。

所以“逆序”只对同步 disposer 有意义。**清理步骤之间需要先后顺序时，只能放进同一个 disposer 依次 await，或者放进同一个生成器 effect 里 yield。** 教程第 2 章第 94 行写的就是这件事。

```js title="本文实验（实际运行）· exp/e02-dispose-order.mjs"
// Q2: disposer ordering within one plugin and across parent/child; are async disposers awaited?
import { makeRoot, log, sleep } from './lib.mjs'
const root = makeRoot({ trace: false })
const asyncDisposer = (label, ms) => async () => {
  log(`  start ${label} (${ms}ms)`); await sleep(ms); log(`  end   ${label}`)
}
const child = {
  name: 'child',
  apply(ctx) {
    ctx.effect(() => asyncDisposer('child.E1', 20))
    ctx.effect(() => asyncDisposer('child.E2', 5))
  },
}
const parent = {
  name: 'parent',
  apply(ctx) {
    ctx.effect(() => asyncDisposer('parent.A (registered 1st)', 30))
    ctx.plugin(child)                        // registered 2nd
    ctx.effect(() => asyncDisposer('parent.B (registered 3rd)', 10))
    ctx.effect(() => () => log('  sync parent.C (registered 4th)'))
    // one effect, several disposers yielded in order: serialised in reverse
    ctx.effect(function* () {
      yield asyncDisposer('gen.y1', 15)
      yield asyncDisposer('gen.y2', 15)
      yield asyncDisposer('gen.y3', 15)
    }, 'generator')
  },
}
const f = root.plugin(parent)
await f
log('effects:', JSON.stringify(f.getEffects().map(e => e.label)))
log('--- parent.dispose() called')
const p = f.dispose()
log('--- dispose() returned (sync part)')
await p
log('--- await dispose() resolved; node', process.version)
```

```text title="本文实验输出（实际运行，输出原样）· node e02-dispose-order.mjs"
   2ms effects: ["anonymous","ctx.plugin()","anonymous","anonymous","generator"]
   4ms --- parent.dispose() called
   4ms --- dispose() returned (sync part)
   4ms   start gen.y3 (15ms)
   4ms   sync parent.C (registered 4th)
   4ms   start parent.B (registered 3rd) (10ms)
   4ms   start parent.A (registered 1st) (30ms)
   4ms   start child.E2 (5ms)
   4ms   start child.E1 (20ms)
  10ms   end   child.E2
  14ms   end   parent.B (registered 3rd)
  20ms   end   gen.y3
  20ms   start gen.y2 (15ms)
  25ms   end   child.E1
  36ms   end   parent.A (registered 1st)
  36ms   end   gen.y2
  36ms   start gen.y1 (15ms)
  55ms   end   gen.y1
  55ms --- await dispose() resolved; node v24.22.2
```

图 2 按这次运行的毫秒数画出每个 disposer 的起止。只有生成器的三段首尾相接，其余各条都从 4ms 同时出发。

```svg title="图 2：e02 一次运行中各 disposer 的起止时间（横轴为毫秒）" tone="light"
<svg viewBox="0 0 840 350" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="e02 卸载时间线：生成器内三段串行，其余兄弟 disposer 并发">
<defs><marker id="lf2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="#504e49"/></marker></defs>
<line x1="240" y1="30" x2="240" y2="282" stroke="#504e49" stroke-width="1" stroke-dasharray="4 3"/>
<text x="240" y="22" text-anchor="middle" font-size="11" fill="#141413">4ms dispose()</text>
<line x1="750" y1="30" x2="750" y2="282" stroke="#504e49" stroke-width="1" stroke-dasharray="4 3"/>
<text x="750" y="22" text-anchor="middle" font-size="11" fill="#141413">55ms resolve</text>
<text x="190" y="54" text-anchor="end" font-size="11" fill="#141413">生成器 effect · y3（15ms）</text>
<rect x="240" y="42" width="160" height="16" rx="3" fill="#1B365D"/>
<text x="190" y="84" text-anchor="end" font-size="11" fill="#141413">生成器 effect · y2（15ms）</text>
<rect x="400" y="72" width="160" height="16" rx="3" fill="#1B365D"/>
<path d="M 400 58 L 400 70" stroke="#504e49" stroke-width="1.2" marker-end="url(#lf2)"/>
<text x="190" y="114" text-anchor="end" font-size="11" fill="#141413">生成器 effect · y1（15ms）</text>
<rect x="560" y="102" width="190" height="16" rx="3" fill="#1B365D"/>
<path d="M 560 88 L 560 100" stroke="#504e49" stroke-width="1.2" marker-end="url(#lf2)"/>
<text x="190" y="144" text-anchor="end" font-size="11" fill="#141413">parent.C（同步）</text>
<rect x="239" y="132" width="3" height="16" fill="#5b7699"/>
<text x="190" y="174" text-anchor="end" font-size="11" fill="#141413">parent.B（10ms）</text>
<rect x="240" y="162" width="100" height="16" rx="3" fill="#5b7699"/>
<text x="190" y="204" text-anchor="end" font-size="11" fill="#141413">parent.A（30ms）</text>
<rect x="240" y="192" width="320" height="16" rx="3" fill="#5b7699"/>
<text x="190" y="234" text-anchor="end" font-size="11" fill="#141413">子插件 child.E2（5ms）</text>
<rect x="240" y="222" width="60" height="16" rx="3" fill="#9fb3cc"/>
<text x="190" y="264" text-anchor="end" font-size="11" fill="#141413">子插件 child.E1（20ms）</text>
<rect x="240" y="252" width="210" height="16" rx="3" fill="#9fb3cc"/>
<line x1="200" y1="290" x2="800" y2="290" stroke="#504e49" stroke-width="1"/>
<g font-size="10" fill="#504e49" text-anchor="middle">
<line x1="200" y1="290" x2="200" y2="295" stroke="#504e49"/><text x="200" y="307">0</text>
<line x1="300" y1="290" x2="300" y2="295" stroke="#504e49"/><text x="300" y="307">10</text>
<line x1="400" y1="290" x2="400" y2="295" stroke="#504e49"/><text x="400" y="307">20</text>
<line x1="500" y1="290" x2="500" y2="295" stroke="#504e49"/><text x="500" y="307">30</text>
<line x1="600" y1="290" x2="600" y2="295" stroke="#504e49"/><text x="600" y="307">40</text>
<line x1="700" y1="290" x2="700" y2="295" stroke="#504e49"/><text x="700" y="307">50</text>
<line x1="800" y1="290" x2="800" y2="295" stroke="#504e49"/><text x="800" y="307">60</text>
</g>
<rect x="200" y="322" width="12" height="10" fill="#1B365D"/><text x="218" y="331" font-size="11" fill="#504e49">同一个 effect 内：逆序串行</text>
<rect x="400" y="322" width="12" height="10" fill="#5b7699"/><text x="418" y="331" font-size="11" fill="#504e49">父插件顶层兄弟：并发</text>
<rect x="580" y="322" width="12" height="10" fill="#9fb3cc"/><text x="598" y="331" font-size="11" fill="#504e49">子 Fiber 内的 effect</text>
</svg>
```

> 审计技能补了一句：“Top-level plugin generator yields are not a general promise of serial sibling disposal”。apply 本身写成 `function*` 时，yield 出来的 disposer 进入的是 `fiber._disposables`，仍然并发执行；要串行，得放进一个 `ctx.effect(function* …)` 里。

### 两层规则对错误的处理也不一样

Fiber 层面每个 disposer 各自 try/catch，一个失败不影响兄弟。单个 effect 内部，列表已经被 `splice(0)` 取空，一个**同步抛错**的 disposer 会跳出 for 循环，同一个 effect 里更早注册的 disposer 就被跳过；异步 reject 也会中断后面的 `.then` 链。这是读源码得出的推论，本文没有实验验证。

它解释了审计技能为什么要求 “one cleanup error must not skip required abort, settlement, or resource release”：用生成器把清理串起来的同时，每一步都要自己兜住错误，例如下面这样。

```js title="示意（未运行）· 串行清理时每一步自己兜住错误"
ctx.effect(function* () {
  const db = openDb()
  yield async () => {
    try { await db.close() } catch (e) { ctx.logger.error(e) }
  }
  const job = startJob(db)
  yield async () => {
    try { job.abort(); await job.settled } catch (e) { ctx.logger.error(e) }
  }
}, 'db + job')
```

### 提供方等消费方：两个机制

第一个机制是 notify：提供方离开 ACTIVE 时通知消费方，消费方的 `_checkImpl` 发现依赖不可用，epoch 变成 INACTIVE，开始卸载。第二个机制是屏障：提供方的 `_unload` 被 dependents 挡住；`ctx.provide` 自己的异步 disposer 也会等 `impl.dependents` 清空，并且至少让出一次，让子插件在服务撤回前还能访问它。

`e01` 里 `provider effect #1 disposed` 出现在消费方回到 PENDING 之后，就是这个屏障在起作用。**但屏障只保护“提供这个服务的那个 Fiber 自己的 effect”**，下一节的竞态都出在这道边界之外。

```ts title="仓库原文 · vendor/cordis/src/reflect.ts:313-349"
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

## “提供方先释放资源”只在绕开 inject 依赖边时出现

先看最直接的写法：资源和 `ctx.provide` 是同一个提供方 Fiber 的两个兄弟 effect，消费方 `writer` 用 inject 依赖这个服务，disposer 里先 `await sleep(5)` 再写入 FINAL。`e03` 跑了 naive 和 fixed 两种提供方、三种场景（释放提供方 / 释放共同的父插件 / 两者同时释放），**6 种组合都没有丢写入**。下面是 naive 写法的三次输出，fixed 写法的输出顺序相同。

原因是上一节的屏障：资源关闭和服务撤回在同一个 Fiber 上，`_unload` 先等 writer 卸完，才开始执行任何 disposer。这里要纠正一个常见说法：只要资源和服务在同一个 Fiber 上、消费方写了 inject，“兄弟 effect 并发”并不会让资源先关。

```js title="本文实验（实际运行）· exp/e03-provider-race.mjs:16-46"
const naiveProvider = {
  name: 'db-provider',
  apply(ctx) {
    const db = openDb()
    ctx.effect(() => () => db.close(), 'db resource')   // sibling 1: closes resource
    ctx.provide('db', db)                                // sibling 2: service withdrawal
  },
}
const fixedProvider = {
  name: 'db-provider',
  apply(ctx) {
    ctx.effect(function* () {
      const db = openDb()
      yield ctx.effect(() => () => db.close(), 'db resource') // yield the exact disposer
      yield ctx.provide('db', db)                              // publish last => withdrawn first
    }, 'composed')
  },
}
const consumer = {
  name: 'writer',
  inject: ['db'],
  apply(ctx) {
    ctx.db.write('hello')
    ctx.effect(() => async () => {
      log('  writer: flushing final record...')
      await sleep(5)                            // async flush before the last write
      try { ctx.db.write('FINAL'); log('  writer: FINAL written OK') }
      catch (e) { log('  writer: ERROR', e.message) }
    }, 'final flush')
  },
}
```

```text title="本文实验输出（实际运行，输出原样）· node e03-provider-race.mjs naive provider / parent / both"
   2ms === mode=naive scenario=provider
   4ms   writer: flushing final record...
  10ms   writer: FINAL written OK
  10ms   db.close() begin
  12ms   db.close() done
  12ms === disposed

   2ms === mode=naive scenario=parent
   4ms   writer: flushing final record...
   9ms   writer: FINAL written OK
   9ms   db.close() begin
  11ms   db.close() done
  11ms === disposed

   2ms === mode=naive scenario=both
   4ms   writer: flushing final record...
  10ms   writer: FINAL written OK
  10ms   db.close() begin
  11ms   db.close() done
  11ms === disposed
```

### 能稳定复现的四种形状，以及修法

`e03b` 找到了四种能稳定丢写入的写法。它们的共同点是：**资源的释放不在提供方 Fiber 的屏障后面，或者使用资源的那段代码对 Fiber 不可见**。

| 形状 | 写法 | 为什么出问题 | 修法 |
|---|---|---|---|
| V1 | 父插件 `ctx.effect(() => () => db.close())`，子插件 `c.provide('db', db)` | 子 Fiber 的屏障确实在等 writer，但 `db.close` 是父插件里 `ctx.plugin()` 的兄弟 effect，同时开始 | 同一个 `ctx.effect(function* …)` 里先 `yield ctx.effect(() => () => db.close())`，再 `yield ctx.plugin(...).dispose`（必须是这个精确的 disposer）。实测通过 |
| V2 | 同一插件里，flush 和 close 是两个兄弟 effect | 顶层 effect 并发 | 合并成一个 disposer：`async () => { await flush(); await db.close() }`。实测通过 |
| V3 | 消费方不写 inject，用 `ctx.get('db')` | 没登记为 dependent，提供方不等它；提供方走后消费方仍是 ACTIVE | 补上 inject，或改成 `ctx.inject` 子 Fiber。脚本里没有 V3fix 分支，依据是 e03（writer 写了 inject，不丢）和 e08 |
| V4 | 消费方有一个不归 Fiber 管理的后台任务 | 任务对 Fiber 不可见，消费方“卸完”时任务还在跑 | `ctx.effect(() => { const task = run(); return () => task })`。实测通过 |

共用的部分：`openDb()` 关闭后再写就抛错；`writer` 是一个规矩的消费方，写了 inject，在自己的 disposer 里 flush 最后一条记录。

```js title="本文实验（实际运行）· exp/e03b-provider-race-variants.mjs:1-22"
// Q3b: where the race DOES reproduce, and the fixes.
// Usage: node e03b-provider-race-variants.mjs <V1|V1fix|V2|V2fix|V3|V3fix|V4|V4fix>
import { makeRoot, log, sleep } from './lib.mjs'
const v = process.argv[2]
const root = makeRoot({ trace: false })
function openDb() {
  let open = true
  return {
    write(x) { if (!open) throw new Error(`db closed, lost write "${x}"`); log(`  db.write(${x}) ok`) },
    async close() { log('  db.close() begin'); await sleep(1); open = false; log('  db.close() done') },
  }
}
const flushingWriter = (getDb) => async () => {
  log('  writer: flushing final record...'); await sleep(5)
  try { getDb().write('FINAL') } catch (e) { log('  writer: ERROR', e.message) }
}
const writer = {   // a well-behaved consumer: inject + final flush in its own disposer
  name: 'writer', inject: ['db'],
  apply(ctx) { ctx.effect(() => flushingWriter(() => ctx.db)) },
}
let target
switch (v) {
```

### V1：资源归父插件，服务由子插件发布

V1 里 writer 写了 inject，子 Fiber `db-service` 的屏障也确实在等它。问题是 `db.close` 挂在父插件上，和 `ctx.plugin(db-service)` 是兄弟：父插件卸载时两者同时启动，9ms 就开始关库，writer 5ms 后的写入丢了。

V1fix 把两者放进同一个生成器 effect，并且 yield 的是 `ctx.plugin(...)` 返回的 Fiber 的 **`.dispose` 本身**。`collect` 会把这个函数从父 Fiber 的顶层列表里按身份删掉，所有权转给生成器。逆序串行执行时，先撤服务（等 writer 卸完），再关库。这和审计技能给出的 workaround 一致。

```js title="本文实验（实际运行）· exp/e03b-provider-race-variants.mjs:23-36"
  case 'V1': // resource owned by parent, service published by child plugin
    target = root.plugin({ name: 'parent', apply(ctx) {
      const db = openDb()
      ctx.effect(() => () => db.close(), 'db')
      ctx.plugin({ name: 'db-service', apply(c) { c.provide('db', db) } })
    } }); root.plugin(writer); break
  case 'V1fix':
    target = root.plugin({ name: 'parent', apply(ctx) {
      ctx.effect(function* () {
        const db = openDb()
        yield ctx.effect(() => () => db.close(), 'db')
        yield ctx.plugin({ name: 'db-service', apply(c) { c.provide('db', db) } }).dispose
      }, 'composed')
    } }); root.plugin(writer); break
```

```text title="本文实验输出（实际运行，输出原样）· node e03b-provider-race-variants.mjs V1 / V1fix"
   6ms === V1: dispose parent
   9ms   db.close() begin
   9ms   writer: flushing final record...
   9ms   db.close() done
  14ms   writer: ERROR db closed, lost write "FINAL"
  35ms === end

   5ms === V1fix: dispose parent
   8ms   writer: flushing final record...
  14ms   db.write(FINAL) ok
  14ms   db.close() begin
  16ms   db.close() done
  37ms === end
```

图 3 把两种写法的结构并排画出来：虚线是 inject 建立的 dependent 关系，屏障只挡在子 Fiber `db-service` 前面。V1 的 `db.close` 在屏障外；V1fix 把它挪进同一个生成器，排在撤服务之后。

```svg title="图 3：V1 与 V1fix 中屏障保护的范围（时间取自上面的实际输出）" tone="light"
<svg viewBox="0 0 880 400" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, PingFang SC, Noto Sans SC, sans-serif" role="img" aria-label="V1 中 db.close 与子插件并发，屏障只挡住子 Fiber；V1fix 中生成器先撤服务再关库">
<defs><marker id="lf3" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#504e49"/></marker></defs>
<text x="20" y="24" font-size="13" font-weight="600" fill="#141413">V1：db.close 是父插件的顶层兄弟</text>
<rect x="20" y="40" width="410" height="200" rx="8" fill="none" stroke="#1B365D" stroke-width="1.2"/>
<text x="34" y="60" font-size="11" fill="#504e49">父插件 parent · _unload 用 Promise.all 同时启动两个顶层 effect</text>
<rect x="34" y="76" width="170" height="44" rx="6" fill="#EEF2F7" stroke="#1B365D"/>
<text x="119" y="96" text-anchor="middle" font-size="12" fill="#141413">effect：db.close()</text>
<text x="119" y="112" text-anchor="middle" font-size="11" fill="#504e49">不在任何屏障后面</text>
<rect x="240" y="76" width="176" height="44" rx="6" fill="#EEF2F7" stroke="#1B365D"/>
<text x="328" y="96" text-anchor="middle" font-size="12" fill="#141413">effect：ctx.plugin()</text>
<text x="328" y="112" text-anchor="middle" font-size="11" fill="#504e49">等子 Fiber 卸完</text>
<line x1="328" y1="121" x2="328" y2="155" stroke="#504e49" stroke-width="1.4" marker-end="url(#lf3)"/>
<rect x="240" y="158" width="176" height="64" rx="6" fill="#EEF2F7" stroke="#1B365D"/>
<text x="328" y="178" text-anchor="middle" font-size="12" fill="#141413">子 Fiber db-service</text>
<text x="328" y="194" text-anchor="middle" font-size="11" fill="#504e49">ctx.provide('db')</text>
<text x="328" y="210" text-anchor="middle" font-size="11" fill="#504e49">屏障：dependents 清空前不动</text>
<rect x="240" y="272" width="176" height="44" rx="6" fill="#EEF2F7" stroke="#1B365D"/>
<text x="328" y="292" text-anchor="middle" font-size="12" fill="#141413">writer · inject: ['db']</text>
<text x="328" y="308" text-anchor="middle" font-size="11" fill="#504e49">disposer：sleep 5ms 后写 FINAL</text>
<line x1="328" y1="271" x2="328" y2="225" stroke="#504e49" stroke-width="1.4" stroke-dasharray="5 4" marker-end="url(#lf3)"/>
<text x="232" y="252" text-anchor="end" font-size="11" fill="#504e49">登记为 dependent</text>
<text x="20" y="346" font-size="11" fill="#141413">实测：6ms dispose parent → 9ms db.close() begin</text>
<text x="20" y="362" font-size="11" fill="#141413">9ms writer 开始 flush → 14ms ERROR，FINAL 丢失</text>
<text x="460" y="24" font-size="13" font-weight="600" fill="#141413">V1fix：同一个生成器 effect 里逆序串行</text>
<rect x="460" y="40" width="410" height="200" rx="8" fill="none" stroke="#1B365D" stroke-width="1.2"/>
<text x="474" y="60" font-size="11" fill="#504e49">父插件 parent · 只有一个顶层 effect（composed）</text>
<rect x="474" y="76" width="382" height="72" rx="6" fill="#EEF2F7" stroke="#1B365D"/>
<text x="488" y="96" font-size="12" fill="#141413">① yield ctx.effect(() =&gt; () =&gt; db.close())</text>
<text x="488" y="116" font-size="12" fill="#141413">② yield ctx.plugin(db-service).dispose</text>
<text x="488" y="136" font-size="11" fill="#504e49">卸载时先 ② 后 ①，② 结束（writer 已卸完）才开始 ①</text>
<line x1="680" y1="149" x2="680" y2="155" stroke="#504e49" stroke-width="1.4" marker-end="url(#lf3)"/>
<rect x="592" y="158" width="176" height="64" rx="6" fill="#EEF2F7" stroke="#1B365D"/>
<text x="680" y="178" text-anchor="middle" font-size="12" fill="#141413">子 Fiber db-service</text>
<text x="680" y="194" text-anchor="middle" font-size="11" fill="#504e49">ctx.provide('db')</text>
<text x="680" y="210" text-anchor="middle" font-size="11" fill="#504e49">屏障：dependents 清空前不动</text>
<rect x="592" y="272" width="176" height="44" rx="6" fill="#EEF2F7" stroke="#1B365D"/>
<text x="680" y="292" text-anchor="middle" font-size="12" fill="#141413">writer · inject: ['db']</text>
<text x="680" y="308" text-anchor="middle" font-size="11" fill="#504e49">disposer：sleep 5ms 后写 FINAL</text>
<line x1="680" y1="271" x2="680" y2="225" stroke="#504e49" stroke-width="1.4" stroke-dasharray="5 4" marker-end="url(#lf3)"/>
<text x="584" y="252" text-anchor="end" font-size="11" fill="#504e49">登记为 dependent</text>
<text x="460" y="346" font-size="11" fill="#141413">实测：5ms dispose parent → 8ms writer 开始 flush</text>
<text x="460" y="362" font-size="11" fill="#141413">14ms write(FINAL) ok → 14ms db.close() begin</text>
</svg>
```

### V2：同一插件里两个兄弟 effect

V2 不涉及服务：同一个插件先注册 close、再注册 flush。逆序启动让 flush 先开始，但它一 await 就让出，close 同时启动，1ms 后库已关闭。V2fix 把两步写进同一个 async disposer，依次 await。

```js title="本文实验（实际运行）· exp/e03b-provider-race-variants.mjs:37-47"
  case 'V2': // flush and close are sibling effects of the SAME plugin
    target = root.plugin({ name: 'store', apply(ctx) {
      const db = openDb()
      ctx.effect(() => () => db.close(), 'db')
      ctx.effect(() => flushingWriter(() => db), 'flush')
    } }); break
  case 'V2fix':
    target = root.plugin({ name: 'store', apply(ctx) {
      const db = openDb()
      ctx.effect(() => async () => { await flushingWriter(() => db)(); await db.close() }, 'flush-then-close')
    } }); break
```

```text title="本文实验输出（实际运行，输出原样）· node e03b-provider-race-variants.mjs V2 / V2fix"
   6ms === V2: dispose store
   9ms   writer: flushing final record...
   9ms   db.close() begin
  10ms   db.close() done
  15ms   writer: ERROR db closed, lost write "FINAL"
  35ms === end

   5ms === V2fix: dispose store
   6ms   writer: flushing final record...
  11ms   db.write(FINAL) ok
  12ms   db.close() begin
  13ms   db.close() done
  33ms === end
```

### V3：`ctx.get` 拿到的服务，提供方不会等你

V3 的消费方不写 inject，用 `ctx.get('db')` 拿服务。它没有登记为 dependent，提供方不等它就关库；提供方没了以后，这个消费方仍是 ACTIVE（state=2），之后再卸载它，写入失败。

不先等提供方 ACTIVE 时（不带 `awaitProvider` 参数），消费方 apply 执行时提供方还在 LOADING，`ctx.get('db')` 直接返回 `undefined`，错误变成 `Cannot read properties of undefined`。也就是说，不写 inject 时，能不能拿到服务取决于加载时序。

```js title="本文实验（实际运行）· exp/e03b-provider-race-variants.mjs:48-54"
  case 'V3': // consumer reads the service with ctx.get() and no inject -> not tracked as dependent
    target = root.plugin({ name: 'provider', apply(ctx) {
      const db = openDb(); ctx.effect(() => () => db.close()); ctx.provide('db', db)
    } })
    if (process.argv[3] === 'awaitProvider') await target
    var untracked = root.plugin({ name: 'untracked', apply(ctx) { const db = ctx.get('db'); ctx.effect(() => flushingWriter(() => db)) } })
    break
```

```text title="本文实验输出（实际运行，输出原样）· node e03b-provider-race-variants.mjs V3 awaitProvider / V3"
   6ms === V3: dispose provider
   9ms   db.close() begin
  10ms   db.close() done
  31ms   untracked consumer state=2 (2=ACTIVE) after provider gone; now dispose it
  31ms   writer: flushing final record...
  38ms   writer: ERROR db closed, lost write "FINAL"
  38ms === end

   5ms === V3: dispose provider
   7ms   db.close() begin
   8ms   db.close() done
  29ms   untracked consumer state=2 (2=ACTIVE) after provider gone; now dispose it
  29ms   writer: flushing final record...
  36ms   writer: ERROR Cannot read properties of undefined (reading 'write')
  36ms === end
```

### V4：消费方的后台任务对 Fiber 不可见

V4 的消费方写了 inject，但它在 apply 里起了一个不归 Fiber 管理的后台任务。消费方本身没有 disposer 要等，屏障很快放行，提供方关库，任务 5ms 后写入失败。V4fix 把任务放进 effect，disposer 返回同一个 promise，消费方卸载就会等它跑完。仓库里 spill-local 的启动清扫用的就是这个形状，见第 6 节。

```js title="本文实验（实际运行）· exp/e03b-provider-race-variants.mjs:55-80"
  case 'V4': // consumer has in-flight work that its fiber does not own/await
    target = root.plugin({ name: 'provider', apply(ctx) {
      const db = openDb(); ctx.effect(() => () => db.close()); ctx.provide('db', db)
    } })
    root.plugin({ name: 'job', inject: ['db'], apply(ctx) {
      const db = ctx.db
      ;(async () => { log('  job: running...'); await sleep(5); try { db.write('JOB RESULT') } catch (e) { log('  job: ERROR', e.message) } })()
    } }); break
  case 'V4fix':
    target = root.plugin({ name: 'provider', apply(ctx) {
      const db = openDb(); ctx.effect(() => () => db.close()); ctx.provide('db', db)
    } })
    root.plugin({ name: 'job', inject: ['db'], apply(ctx) {
      const db = ctx.db
      ctx.effect(() => {
        const task = (async () => { log('  job: running...'); await sleep(5); try { db.write('JOB RESULT') } catch (e) { log('  job: ERROR', e.message) } })()
        return () => task   // disposer awaits the in-flight task (could also abort it first)
      }, 'job')
    } }); break
}
await sleep(2)
log(`=== ${v}: dispose ${target.name}`)
await target.dispose()
await sleep(20)
if (v === 'V3') { log(`  untracked consumer state=${untracked.state} (2=ACTIVE) after provider gone; now dispose it`); await untracked.dispose() }
log('=== end')
```

```text title="本文实验输出（实际运行，输出原样）· node e03b-provider-race-variants.mjs V4 / V4fix"
   2ms   job: running...
   4ms === V4: dispose provider
   4ms   db.close() begin
   6ms   db.close() done
   9ms   job: ERROR db closed, lost write "JOB RESULT"
  27ms === end

   2ms   job: running...
   4ms === V4fix: dispose provider
   9ms   db.write(JOB RESULT) ok
  10ms   db.close() begin
  11ms   db.close() done
  31ms === end
```

### 审计技能说的残余竞态，本次没有复现

审计技能说上面的 workaround 管不到一种情形：“Consumer disposal removes a fiber from the registry before its cleanup finishes, so service withdrawal can miss an already-disposing consumer”，建议根上下文或外层所有者卸载时，显式持有并等待消费方的卸载任务。

`e03c` 试了两种交错：消费方和 composed provider 作为同一个父插件的兄弟子插件，然后卸载父插件；先开始卸载消费方，1ms 后再卸载父插件。两种都**没有**丢写入。源码也支持这个结果：`impl.dependents` 要到消费方 `_unload` **结束时**才删除，屏障在这之前一直看得到它。

技能说的“从 registry 移除”，可能指 notify 遍历 `runtime.fibers` 的那条路径，也可能藏在根上下文释放、isolate、`[Service.init]` 生成器或 loader/HMR 路径里，这些本次都没有覆盖。所以只能说：技能声称有这个风险，本实验没有复现。

```js title="本文实验（实际运行）· exp/e03c-enclosing-owner.mjs:14-33"
const writer = { name: 'writer', inject: ['db'], apply(ctx) {
  ctx.effect(() => async () => { log('  writer: flushing...'); await sleep(5); try { ctx.db.write('FINAL') } catch (e) { log('  writer: ERROR', e.message) } })
} }
let writerFiber
const app = root.plugin({ name: 'app', apply(ctx) {
  ctx.effect(function* () {
    const db = openDb()
    yield ctx.effect(() => () => db.close(), 'db')
    yield ctx.plugin({ name: 'db-service', apply(c) { c.provide('db', db) } }).dispose
  }, 'composed provider')
  writerFiber = ctx.plugin(writer)        // consumer is a sibling child of the same owner
} })
await app; await sleep(2)
if (v === 'consumerFirst') {
  log('=== start consumer disposal, then dispose app 1ms later')
  const a = writerFiber.dispose(); await sleep(1); await Promise.all([a, app.dispose()])
} else {
  log('=== dispose app (owner of both)'); await app.dispose()
}
await sleep(10); log('=== end')
```

```text title="本文实验输出（实际运行，输出原样）· node e03c-enclosing-owner.mjs siblingConsumer / consumerFirst"
   5ms === dispose app (owner of both)
   7ms   writer: flushing...
  13ms   db.write(FINAL) ok
  13ms   db.close() begin
  15ms   db.close() done
  25ms === end

   5ms === start consumer disposal, then dispose app 1ms later
   6ms   writer: flushing...
  12ms   db.write(FINAL) ok
  13ms   db.close() begin
  14ms   db.close() done
  25ms === end
```

## 可选依赖：用 `ctx.inject` 子 Fiber 承载，`ctx.get` 只做探测

这个版本的 Cordis **没有可选 inject 的语法**。`Inject.resolve` 把数组和对象形式都规范成 `{ name: config | null }`，`_refresh` 遇到第一个缺失的 key 就判定 INACTIVE。可选依赖只有两种表达方式。

`ctx.get(name)` 是一次性探测：只有提供方处于 ACTIVE 时才返回值，不建立任何生命周期关联。`ctx.inject(deps, cb)` 就是 `ctx.plugin({ inject, apply: cb, name: cb.name })`，创建一个独立的子 Fiber，它会被登记为 dependent。

```ts title="仓库原文 · vendor/cordis/src/reflect.ts:269-279"
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

```ts title="仓库原文 · vendor/cordis/src/registry.ts:300-302"
  inject(inject: Inject, callback: Plugin.Function<void>) {
    return this.plugin({ inject, apply: callback, name: callback.name })
  }
```

教程第 3 章推荐用 `ctx.get` 在使用处探测。这个用法本身没错，但它没有提到三件事：`ctx.get` 不建立依赖关系，提供方卸载时不会等你，结果还取决于加载时序（V3）。

````md title="仓库原文 · docs/cordis-tutorial/03-services.md:80-90"
## Optional dependencies

`inject` is for hard requirements. For a capability the plugin can live without, skip `inject` and probe at the use site:

```ts ignore-check
export function apply(ctx: Context) {
  // undefined when no provider is loaded; the plugin still runs.
  const greeter = ctx.get('greeter')
  console.log(greeter?.greet('maybe') ?? 'no greeter available')
}
```
````

### 实验：子 Fiber 跟着服务进出，主插件保持 ACTIVE

`e08` 的主插件没有声明 metrics，在 apply 里调用 `ctx.inject(['metrics'], metricsBridge)`。不写 inject 直接读 `ctx.metrics` 会抛错，`ctx.get` 返回 `undefined`。

metrics 出现时子 Fiber 加载；metrics 消失时子 Fiber 卸载、回到 PENDING，**提供方等它卸完才 DISPOSED**；主插件全程 ACTIVE。主插件卸载时，停在 PENDING 的子 Fiber 走 PENDING → UNLOADING → DISPOSED。

结论：`ctx.get` 只适合“用一下就走”的同步调用。只要有长期持有的资源绑在那个服务上，或者清理时还要用到它，就用 `ctx.inject` 子 Fiber。dsh 常见的组合是 `ctx.get` 定初值、`ctx.inject` 订阅变化，`tool-bash` 的例子见[《dsh 的服务、事件、配置与插件实战》](dsh_services_events_config_plugin.html)。packages 的非测试代码里有 99 处 ``ctx.inject([``，分布在 88 个文件。

```js title="本文实验（实际运行）· exp/e08-optional-inject.mjs"
// Q8: optional dependency: main plugin stays ACTIVE; a ctx.inject() sub-fiber follows the service.
import { makeRoot, log, sleep } from './lib.mjs'
const root = makeRoot()
const main = { name: 'main', apply(ctx) {
  log('  main apply (no required deps)')
  ctx.inject(['metrics'], function metricsBridge(ctx) {
    log('  metricsBridge apply: metrics =', ctx.metrics.id)
    ctx.effect(() => () => log('  metricsBridge disposed'))
  })
  ctx.effect(() => () => log('  main disposed'))
  // reading without inject: allowed via ctx.get, but a plain property read throws
  log('  ctx.get("metrics") =', ctx.get('metrics'))
  try { ctx.metrics } catch (e) { log('  ctx.metrics without inject threw:', e.message) }
} }
const m = root.plugin(main); await m; await sleep(2)
log('=== provide metrics')
const p = root.plugin({ name: 'metrics-provider', apply(ctx) { ctx.provide('metrics', { id: 'm1' }) } }); await p; await sleep(2)
log('=== remove metrics')
await p.dispose(); await sleep(2)
log(`  main state=${m.state} (2=ACTIVE)`)
log('=== dispose main')
await m.dispose()
```

```text title="本文实验输出（实际运行，输出原样）· node e08-optional-inject.mjs"
   2ms   [status] main: PENDING -> LOADING
   3ms   main apply (no required deps)
   3ms   ctx.get("metrics") = undefined
   3ms   ctx.metrics without inject threw: cannot get property "metrics" without inject
   4ms   [status] main: LOADING -> ACTIVE
   5ms === provide metrics
   5ms   [status] metrics-provider: PENDING -> LOADING
   5ms   [status] metrics-provider: LOADING -> ACTIVE
   6ms   [status] metricsBridge: PENDING -> LOADING
   6ms   metricsBridge apply: metrics = m1
   6ms   [status] metricsBridge: LOADING -> ACTIVE
   9ms === remove metrics
   9ms   [status] metrics-provider: ACTIVE -> UNLOADING
   9ms   [status] metricsBridge: ACTIVE -> UNLOADING
   9ms   metricsBridge disposed
   9ms   [status] metricsBridge: UNLOADING -> PENDING
   9ms   [status] metrics-provider: UNLOADING -> DISPOSED
  12ms   main state=2 (2=ACTIVE)
  12ms === dispose main
  12ms   [status] main: ACTIVE -> UNLOADING
  12ms   main disposed
  12ms   [status] metricsBridge: PENDING -> UNLOADING
  12ms   [status] metricsBridge: UNLOADING -> DISPOSED
  12ms   [status] main: UNLOADING -> DISPOSED
```

subagent 是一个真实例子：manager 由 `ctx.inject(['agents'], …)` 子 Fiber 创建，清理时先比较身份再清空，避免误清掉下一次绑定写入的新实例。

```ts title="仓库原文 · packages/subagent/subagent/src/index.ts:216-232"
  protected async [Service.init](): Promise<void> {
    const ctx = this.ctx
    const catalogProjection = await ctx.sessionProjections.register(subagentCatalogProjectionDefinition)
    await ctx.sessionProjections.register(subagentTimingProjectionDefinition)
    const identityProjection = await ctx.sessionProjections.register(subagentIdentityProjectionDefinition)
    if (ctx.fiber.uid === null) return
    ctx.inject(['agents'], (childCtx: Context) => {
      const manager = new SubagentManager(childCtx, {
        identityOf: session => identityProjection.stateOf(session).identity,
        catalogOf: session => subagentCatalogProjectionDefinition.wire.view(catalogProjection.stateOf(session)),
        emitLifecycle: this.emitLifecycle,
      }, () => this.maxActiveSubagents)
      this.manager = manager
      childCtx.effect(() => () => {
        /* v8 ignore else -- one injected binding owns the slot until its fiber disposes. */
        if (this.manager === manager) this.manager = undefined
      }, 'subagents.managerBinding()')
```

## 官方审计的三条 P0，和真实插件的四种写法

生命周期规则散在四个地方：教程第 2、3、6 章，`docs/defensive-patterns/general.md`，AGENTS.md，以及 `dsh-code-review` 会调用的 `cordis-styling-nitpicking` 技能（标题是 “Cordis Ownership and Lifecycle Audit”）。这个技能只有三条 P0 规则，加一段推荐写法，每段都能对应到前面的某个机制和实验。下面按原文顺序逐段读。

### P0 单例：模块级的拥有者活得比 Fiber 长

这条针对的是“所有者”藏在模块加载、static 字段、全局 symbol 或隐藏缓存里。`_unload` 只回收 `_disposables` 和 `provisions` 能触达的状态，而 e01 显示 apply 每次重跑都会造出新实例，模块级缓存会把旧实例留下来。

关键措辞是 “Weak keys, size bounds, generation checks… do not give a module-level cache Cordis ownership”：它预先堵住了“我用了 WeakMap 所以没问题”这类辩解。所有权只能来自 Fiber，不能来自 GC 或容量上限。

```md title="仓库原文 · .agents/skills/cordis-styling-nitpicking/SKILL.md:25-29 · P0: singleton pattern"
## P0: singleton pattern

Flag one process-wide instance or mutable state owner whose identity or lifetime comes from module loading, a static field, a global symbol, a hidden cache, or a `getInstance()` accessor. Weak keys, size bounds, generation checks, and behavior-preserving memoization do not give a module-level cache Cordis ownership.

Exclude immutable scalar constants, pure frozen tables, stateless functions, and objects allocated inside an explicitly scoped owner. Recommend a Cordis service and declared or otherwise resolved consumer dependencies; use a scoped context or service-owned keyed record for narrower lifetimes.
```

### P0 未回滚的副作用：“请求停止”不等于“已经停了”

清单列得很全：监听器、注册、定时器、watcher、子 Fiber、进程、连接、临时文件、订阅、ambient patch、异步任务。对应 e05（裸定时器）、e06（五类资源）和 V4（后台任务）。

“Aborting or killing without awaiting settlement is a violation” 这句对应 e06b：只 kill 时 dispose 1ms 就返回，子进程还要再活约 300ms。第二段同时划了边界：持久写入、远端修改这类有意为之的结果不要求回滚，用来完成它们的临时资源仍然要有所有者。

```md title="仓库原文 · .agents/skills/cordis-styling-nitpicking/SKILL.md:31-35 · P0: unreversed side effect"
## P0: unreversed side effect

Flag an owner that leaves a live effect behind after disposal: listeners, registrations, timers, watchers, child fibers, processes, connections, temporary files, subscriptions, ambient patches, or asynchronous work. Aborting or killing without awaiting settlement is a violation when cleanup can return before owned work stops.

Correctly owned `ctx.on()`, `ctx.plugin()`, and `ctx.effect()` registrations already provide reversal; callers need not retain their early disposers. Unmanaged resources need an owned effect or explicit awaited cleanup. Do not demand rollback of intentional durable writes or remote mutations; temporary and live resources used by those operations still need owners.
```

### P0 清理依赖先于消费方释放：第 4 节的竞态

这一条讲的就是 V1、V2。“A provider's disposal promise finishing after consumers does not prove its resources survived their cleanup” 指出了 V1 的要害：子 Fiber 的 dispose 确实在 writer 之后结束，但资源早被兄弟 effect 关了。

“Adding `inject`, reversing registration order, or retaining a service reference does not fix that race” 和 e03 的基线结果并不矛盾：它针对的是资源释放绕开屏障的形状。V1 的 writer 本来就写了 inject，问题照样出现。技能给出的回归测试方法是“把消费方暂停在最后一次依赖使用之前，再开始卸载提供方”，e03 里的 `await sleep(5)` 就是它的简化版。

```md title="仓库原文 · .agents/skills/cordis-styling-nitpicking/SKILL.md:37-43 · P0: cleanup dependency released before consumer completion"
## P0: cleanup dependency released before consumer completion

Any service call or event delivery required to finish cleanup is a lifecycle dependency. Keep its actual resources and listeners usable until the consumer has settled, including final result publication and durable writes. A provider's disposal promise finishing after consumers does not prove its resources survived their cleanup.

The current [fiber unload](../../../vendor/cordis/src/fiber.ts) starts sibling disposers concurrently. [Service withdrawal](../../../vendor/cordis/src/reflect.ts) awaits dependent fibers inside its own disposer, while sibling resource closers can already run. Adding `inject`, reversing registration order, or retaining a service reference does not fix that race. A listener can also belong to the consumer fiber and auto-unsubscribe alongside an operation's drain.

Require explicit ownership and awaited sequencing: stop admission, settle admitted consumers while their dependencies remain usable, then drain and release the resources. Scope new-admission rejection separately from completion of accepted operations. Use an explicitly composed effect or a resource-owning parent with an awaited consumer/service child; verify the actual captured disposer identities. `ctx.effect(function* ...)` serializes the disposers it collects, but a wrapper around an independently registered effect does not transfer ownership of the original effect. Top-level plugin generator yields are not a general promise of serial sibling disposal. Check failure paths as well: one cleanup error must not skip required abort, settlement, or resource release.
```

### 推荐写法：组合成一个生成器 effect，服务最后发布

三步对应 V1fix：先获取资源并 yield 各自的 disposer，最后 `yield ctx.provide(...)`，逆序卸载时先撤服务、等消费方，再释放资源；需要准入闸门时把它的清理放在最后 yield，卸载时最先停止接收新请求。

这段有两句特别重要。“Do not yield replacement wrappers around already registered disposers” 就是第 2 节说的“所有权按函数身份转移”。“Top-level plugin yields alone leave disposers as concurrent siblings” 对应 e02。这段还诚实地写了自己的局限：它不是外层所有者卸载的完整解法，也就是上一节没能复现的那个残余竞态。完整的 provider 示例见[《dsh 的服务、事件、配置与插件实战》里的 acme-ledger](dsh_services_events_config_plugin.html)。

```md title="仓库原文 · .agents/skills/cordis-styling-nitpicking/SKILL.md:45-55 · Current workaround"
### Current workaround: function plugin with yielded effects

Use a function-style provider plugin with one `ctx.effect(function* () { ... })` to compose service publication and resource effects. Yield the exact disposers returned by the registrations so the composed effect takes ownership of them:

1. Acquire the resources and required completion listeners, then `yield` each registration's returned disposer. Register resource cleanup with `ctx.effect()` and yield that exact returned disposer too.
2. Publish the service last with `yield ctx.provide(name, service)`. Reverse disposal then withdraws the service and awaits the dependent consumers still registered with Cordis before disposing the earlier resource effects.
3. If the provider needs a separate admission gate, yield its cleanup last so disposal stops new admission before withdrawing the service. Keep accepted cleanup operations usable while consumers finish.

This orders provider withdrawal while its consumers remain registered; it is not a complete enclosing-owner shutdown fix. Consumer disposal removes a fiber from the registry before its cleanup finishes, so service withdrawal can miss an already-disposing consumer. For root or enclosing-owner disposal and independently started consumer cleanup, retain and await the actual consumer disposal tasks through an explicit owner before releasing their resources.

Put these yields inside the composed `ctx.effect()` generator, even when the plugin itself is a generator. Top-level plugin yields alone leave disposers as concurrent siblings in the current implementation. Do not yield replacement wrappers around already registered disposers: the original effects would remain independently owned and could still close early. This workaround orders the captured effects; it does not repair uncaptured consumer-owned listeners or remove the need to handle cleanup failures.
```

### 真实插件一：后台任务不阻塞启动，但卸载要等它

spill-local 启动时发起一次清扫，生成器 effect 不 await 它，而是 yield 一个 await 同一个 promise 的 disposer。注释把意图写得很清楚：服务可用性不被推迟，Fiber 卸载时又能到达静止。这正是 V4fix 的形状。

```ts title="仓库原文 · packages/spill/spill-local/src/index.ts:91-102"
    // One best-effort startup sweep, owned by the fiber. The generator body runs
    // at activation but does NOT await the sweep — it launches it and yields an
    // async disposer that awaits the SAME promise, so service availability is
    // never delayed yet a fiber unload reaches quiescence (no sweep I/O outlives
    // the fiber). Disabled (`cleanupPeriodDays === 0`) yields a no-op disposer.
    ctx.effect(function* (this: LocalSpillStore) {
      if (this.config.cleanupPeriodDays > 0) {
        const warn: WarnFn = (message) => { this.ctx.logger.warn(message) }
        this.cleanup = this.runCleanup(warn)
      }
      yield async () => { await this.cleanup }
    }.bind(this), 'spill-local cleanup sweep')
```

### 真实插件二：把外部对象的 `dispose()` 接进 Fiber

skill-filesystem 把 chokidar watcher 交给 `FileSystemSkillProvider` 管理，再用一个只 yield 异步 disposer 的生成器 effect 把它接到 Fiber 上。资源拥有者对象自己负责内部顺序，Fiber 只负责在卸载时等它。

```ts title="仓库原文 · packages/skill/skill-filesystem/src/index.ts:134-147"
export function apply(ctx: Context, config: Config = {}): void {
  let provider!: FileSystemSkillProvider
  ctx.skills.registerProvider((control) => {
    provider = new FileSystemSkillProvider(ctx, control, config)
    return provider
  })
  ctx.effect(function* () {
    yield async () => { await provider.dispose() }
  }, 'skill-filesystem watcher')
  ctx.on('fs/observed', (target, _observation, actor) => {
    if (mutationToolName(actor) === undefined) return
    provider.observeHostMutation(target.displayPath)
  })
}
```

### 真实插件三：用 yield 的顺序表达“先排空，再释放”

subagent manager 先 yield `scope.dispose`，再 yield `() => this.drain()`。逆序串行时先 drain 再释放 scope。这里 yield 的是 `scope.dispose` 本身，不是包装函数，所有权确实转给了这个生成器。

```ts title="仓库原文 · packages/subagent/subagent/src/manager.ts:183-186"
    ctx.effect(function* (this: SubagentManager) {
      yield scope.dispose
      yield () => this.drain()
    }.bind(this), 'subagents.manager()')
```

### 真实插件四：apply 还没结束就开始关

mcp-client 监听自身的 `internal/plugin` 事件，看到 `uid === null` 就关闭 transport。注释写着 “Cordis announces unload before awaiting an unfinished apply()”：这正是 e05 观察到的“dispose 先置 uid、再等 apply”。不这样做，一个卡在握手的 apply 会让 dispose 一直挂着。packages 里 `fiber.uid === null` 或 `!== null` 的检查共有 23 处，分布在 17 个文件。

```ts title="仓库原文 · packages/mcp/mcp-client/src/index.ts:195-206"
  const connection = startConnection(ctx, config, reconnect, status, oauth)
  let stopping: Promise<void> | undefined
  const dispose = (): Promise<void> => stopping ??= connection.dispose()
  // Cordis announces unload before awaiting an unfinished apply(). Closing
  // the transport here releases startup requests that are still awaiting a reply.
  // oxlint-disable-next-line typescript/no-misused-promises -- Cordis contains observer failures; the effect also awaits this promise.
  ctx.on('internal/plugin', (fiber) => {
    if (fiber !== ctx.fiber || fiber.uid !== null) return
    return dispose()
  }, { global: true })
  ctx.effect(() => dispose, 'mcp-client.connection')
  registerServerContext(ctx, config.serverName, connection)
```

### 规则与存量：有例外，但有基线

规则和现状也有对不上的地方。mcp-client 有一个模块级的 `const activeServerNames = new WeakMap<object, Set<string>>()`，按单例规则的字面意思属于疑似单例，不过它的 effect 有对称的 add/delete，本次也没有复现出可观察的缺陷。web-app 有一个模块级的 `ANNOUNCED_ROOTS` WeakSet。

defensive-patterns 另有一条 “Plugins do not read the Cordis root context”，由 `verify-cordis-root` 门禁执行：根上下文没有 runtime，读它会跳过 inject 检查，在上面注册的监听器也不会随插件卸载而移除。`scripts/cordis-root.baseline.json` 里冻结了一批存量例外，mcp-client 在其中；过程笔记解释了为什么“先禁止新增、存量进基线”：有些替换需要先设计新服务。所以准确的说法是“规则加存量例外”，不是“真实插件违反了规则”。这些判断是本文对照规则文字做出的，不是官方的审计结论。

## 文档与源码对不上的地方，以及没有解决的问题

下面三段是仓库内的文档原文，之后的表格逐条列出对应的源码事实和实验。教程第 94 行和审计技能已经更正了“逆序”的说法，但 `Disposable` 的 JSDoc 还没改。

```ts title="仓库原文 · vendor/cordis/src/fiber.ts:98-104 · Disposable JSDoc"
/**
 * Function returned by an effect to release resources during disposal.
 *
 * Disposers run in reverse registration order when the owning fiber unloads;
 * they may be async, in which case unloading awaits them.
 */
export type Disposable<T = any> = () => T
```

```ts title="仓库原文 · vendor/cordis/src/fiber.ts:430-437 · ctx.effect JSDoc"
  /**
   * Register a cleanup-aware effect on this fiber.
   *
   * `execute` runs immediately; the disposers it produces are collected and
   * run (in reverse order) either when the returned disposer is called or
   * when the fiber unloads, whichever comes first. Calling the disposer twice
   * is a no-op. Throws `CordisError('INACTIVE_EFFECT')` if the fiber is
   * already disposed, and `TypeError` if `execute` returns an invalid shape.
```

````md title="仓库原文 · docs/cordis-tutorial/02-lifecycle-and-effects.md:70-80"
Every loaded plugin instance owns a fiber that moves through these states:

```
PENDING → LOADING → ACTIVE → UNLOADING → DISPOSED
                 ↘ FAILED
```

- **PENDING** — declared, but a required service (chapter 3) is not available yet.
- **LOADING / ACTIVE** — `apply` is running / has completed.
- **FAILED** — `apply` or config validation threw.
- **UNLOADING / DISPOSED** — disposers are running / everything is torn down.
````

| 位置 | 文档怎么说 | 源码 / 实验 |
|---|---|---|
| `Disposable` JSDoc（`fiber.ts:98-104`） | “run in reverse registration order… unloading awaits them” | 只有启动是逆序的，顶层异步 disposer 并发执行（e02） |
| `ctx.effect` JSDoc 和 `docs/cordis-api/fiber.md` | 只在 “already disposed” 时抛 `INACTIVE_EFFECT` | `state === UNLOADING` 时也抛（`fiber.ts:447-450`）。只读了源码，没有实验 |
| 教程状态图（`02-lifecycle-and-effects.md:72-75`） | PENDING→LOADING→ACTIVE→UNLOADING→DISPOSED，LOADING↘FAILED | 缺了 UNLOADING→PENDING、UNLOADING→LOADING 重载、失败先经过 UNLOADING，以及 FAILED 只能靠 `update()` 恢复（e01、e04、e04b、e07） |
| 教程可选依赖（`03-services.md:80-90`） | 用 `ctx.get` 在使用处探测 | 用法没错，但没说明它不建立依赖、提供方不等它、结果取决于加载时序（V3） |

还有几个问题没有解决：

1. 审计技能说的 “service withdrawal can miss an already-disposing consumer”，本次找不到能触发它的交错。它可能在 isolate、`Service` 类的 `[Service.init]` 生成器、loader/HMR 或根上下文释放路径里。
2. apply 永远不结束时，`dispose()` 是否也永远挂起。按 e05 的行为和 mcp-client 的注释推断会挂起，没有实测。
3. 单个 effect 内某个 disposer 同步抛错会跳过其余 disposer；UNLOADING 期间 `ctx.effect` 抛 `INACTIVE_EFFECT`；FAILED 的 Fiber 上仍能新建 effect。这三点都只来自读源码。
4. V1、V2 只用函数和对象形式的插件测过，没有用 `extends Service` 的真实服务类复测；volatile 配置更新没有通过 loader 实际跑过；“换提供方触发消费方重载”只读了源码。
5. 没有运行 dsh 自带的 `scripts/cordis-core-lifecycle.spec.ts`，也没有逐字节确认 npm 包和 vendor 源码一致。源码里 `internal/status` 旁边有一条 “FIXME internal/fiber-info” 注释，这个事件名以后可能会改。

## 结论：对每个资源问一个问题

在这个版本的 Cordis 里，**依赖关系本身就是清理顺序的保证**。inject 依赖边上的东西框架都处理好了：消费方在自己的 disposer 里仍能使用服务，提供方等消费方卸完，失败的 apply 自动回滚。出问题的总是框架看不见的那部分：父插件持有的资源、并发执行的兄弟 disposer、`ctx.get` 拿到的服务、裸定时器和后台任务、只 kill 不等退出的子进程。

所以写 dsh 插件时，与其记一串“先做什么后做什么”，不如对每个资源回答一个问题：**它的释放挂在哪一个 disposer 上，这个 disposer 和使用这个资源的代码之间，是否有 inject 依赖边，或者同在一个生成器 effect 里串行执行？** 两者都没有，就是竞态。落到写法上：

- 框架管不到的资源，在 `ctx.effect` 里获取并返回 disposer；异步获取用 `async function*`，卸载时不会接着获取后面的资源
- 停止要等到静止：kill 之后 await `exit`，abort 之后 await 任务本身
- 有先后的清理放进同一个 disposer 或同一个 `ctx.effect(function* …)`，yield 注册 API 返回的**原函数**，不要包一层
- 发布服务的插件，把 `ctx.provide` 放在生成器的最后 yield；资源不要挂在发布服务的子插件之外
- 清理时还要用的服务写进 inject，可选的用 `ctx.inject` 子 Fiber，不用 `ctx.get`
- `apply` 里 await 之后检查 `ctx.fiber.uid`，或像 mcp-client 那样监听自己的卸载事件；不要在 disposer 里新建 effect
- 配置变更默认是完整重载，`apply` 必须能重复执行；不要把状态留在模块级
