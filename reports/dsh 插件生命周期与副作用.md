# 一个 epoch 字符串驱动 dsh 插件生死

在 Cordis 4.2.0-alpha.1 下，dsh 插件的整个生命周期由一个叫 epoch 的字符串驱动。它的格式是 `"<revision>:<依赖 impl uid>:…"`，只要有一个必需依赖不可用就变成哨兵值 `__INACTIVE__`。epoch 从 INACTIVE 变成真实值时，Fiber 加载（LOADING）。其他任何变化都会先卸载（UNLOADING），包括依赖消失、restart、更换提供方、配置更新。apply 抛错时，已注册的 effect 同样被回滚，然后停在 FAILED，这个状态只能靠 `update()` 恢复。`ctx.effect` 会同步立即执行，而且在执行之前就把 disposer 登记到所属 Fiber 上。`ctx.on`、`ctx.plugin`、`ctx.provide`、定时器服务、`ctx.tools.register` 本质上都是 effect。卸载有两层规则。**同一个 effect 内收集的 disposer 按逆序串行执行；一个 Fiber 的多个顶层 effect 按逆序启动，但用 `Promise.all` 并发执行**。提供方 Fiber 会先等所有依赖它的 Fiber 卸载完，再释放自己的资源。因此"提供方先释放资源"的竞态只在资源释放绕开 inject 依赖边时出现，实测复现了 4 种形状，4 种都能修。可选依赖应该写成 `ctx.inject` 子 Fiber，不应该用 `ctx.get` 加判空。官方的 Disposable JSDoc、`ctx.effect` JSDoc 和教程里的状态图都和源码有出入，审计技能声称的一个残余竞态在本次实验中没有复现。

**依据标注**：**[实测]** 表示用 npm 安装的 `@deepseek-ai/cordis@4.2.0-alpha.1`（与 dsh 仓库 `vendor/cordis` 版本号一致）在 Node v24.22.2 上实际运行过的脚本；**[源码]** 表示读 deepseek-harness commit `4b399c3`（2026-11-22）的代码；**[文档]** 表示读该 commit 的教程、AGENTS.md 或技能文件。脚本都在 `/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/66d83128-a37c-4163-ae6b-f001409e9960/scratchpad/lifecycle-lab/exp/` 下，共用的 `lib.mjs` 会监听 `internal/status` 事件，把每次状态变化打印成 `[status] 名字: 旧 -> 新`，左列是距进程启动的毫秒数。输出都按原样粘贴，没有改动。实验用的是 npm 包，没有逐字节核对它和 vendor 源码是否一致。

## 六个状态由三个事实推导，epoch 决定往哪走

**[源码]** `FiberState` 是一个 const enum，依次是 PENDING=0、LOADING=1、ACTIVE=2、FAILED=3、DISPOSED=4、UNLOADING=5。注意 DISPOSED 的数值比 UNLOADING 小（[fiber.ts L169-L184](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L169-L184)）。其中四个状态由 `_getState()` 推导，不单独存储：`uid === null` 为 DISPOSED，否则 `_error` 有值为 FAILED，否则 epoch 不是 INACTIVE 为 ACTIVE，剩下的都是 PENDING。LOADING 和 UNLOADING 只在一次过渡（inertia）进行中被显式设置（[fiber.ts L602-L607](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L602-L607)）。epoch 由 `_refresh()` 计算：以 `_revision` 开头，为 inject 里的每个名字拼接 `:impl.uid`，缺任何一个就整体变成 INACTIVE。这里的"可用"由 `_checkImpl` 判断，要求提供方 Fiber 处于 ACTIVE，并且可选的 `check` 不抛错、不返回 false（[fiber.ts L625-L650](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L625-L650)）。驱动过渡的是 `_setEpoch`。epoch 没变时什么也不做。**`_error` 有值时也什么都不做**，源码注释是 "a failed fiber only recovers through update()"。已经有过渡在进行时，只记下目标值，等当前过渡结束后再对齐。INACTIVE 变成真实值时走 `_reload()`，其余情况一律走 `_unload()`（[fiber.ts L652-L668](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L652-L668)）。因为 epoch 里包含 impl uid，提供方换成新实例时，即使服务名不变，所有消费方也都会重启。每次状态变化都会触发 `internal/status(fiber, oldState)`，第二个参数是旧状态。进入或离开 ACTIVE 时，还会对本 Fiber 提供的所有服务调用 `notify`，让消费方重新检查依赖（[fiber.ts L609-L623](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L609-L623)）。

把源码路径和实测输出放在一起，可以得到下面这张完整的转换表。它比教程里那条单线的 `PENDING → LOADING → ACTIVE → UNLOADING → DISPOSED` 图丰富得多（[02-lifecycle-and-effects.md L68-L80](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/02-lifecycle-and-effects.md#L68-L80)）：

| 触发 | 状态序列 | 依据 |
|---|---|---|
| 依赖齐全，首次加载 | PENDING → LOADING → ACTIVE | 实测 e01 |
| 必需依赖消失 | ACTIVE → UNLOADING → **PENDING**（Fiber 仍保留在注册表中） | 实测 e01 |
| 依赖恢复 | PENDING → LOADING → ACTIVE，**apply 整个重跑** | 实测 e01 |
| restart / 换提供方 / 默认的配置更新 | ACTIVE → UNLOADING → LOADING → ACTIVE | 实测 e07；源码 |
| apply 同步抛错或异步 reject | LOADING → UNLOADING → FAILED（会先回滚 effect） | 实测 e04 |
| FAILED 状态下 `update(config)` | FAILED → LOADING → ACTIVE | 实测 e04/e04b |
| FAILED 状态下 `restart()` | 不变，直接以原错误 reject | 实测 e04b |
| 释放一个 PENDING 的 Fiber | PENDING → UNLOADING → DISPOSED | 实测 e08 |
| `dispose()` | ACTIVE → UNLOADING → DISPOSED | 实测 e01 |

### 依赖消失时消费方回到 PENDING，而且在自己的 disposer 里仍能使用服务

**[实测]** `e01-dep-reappear.mjs` 先加载声明了 `inject: ['store']` 的消费方，再加载提供方，然后释放提供方，再重新加载它：

```
   2ms --- load consumer first (no provider yet)
   9ms consumer state = 0
   9ms --- load provider
   9ms   [status] provider: PENDING -> LOADING
   9ms provider apply #1
  10ms   [status] provider: LOADING -> ACTIVE
  10ms   [status] consumer: PENDING -> LOADING
  10ms consumer apply run=1, sees store#1
  10ms   [status] consumer: LOADING -> ACTIVE
  16ms --- dispose provider
  16ms   [status] provider: ACTIVE -> UNLOADING
  16ms   [status] consumer: ACTIVE -> UNLOADING
  16ms consumer run=1 disposer: store reachable? store#1
  17ms   [status] consumer: UNLOADING -> PENDING
  17ms provider effect #1 disposed
  17ms   [status] provider: UNLOADING -> DISPOSED
  22ms consumer state = 0
  23ms --- reload provider
  23ms   [status] provider: PENDING -> LOADING
  23ms provider apply #2
  23ms   [status] provider: LOADING -> ACTIVE
  23ms   [status] consumer: PENDING -> LOADING
  23ms consumer apply run=2, sees store#2
  23ms   [status] consumer: LOADING -> ACTIVE
  28ms consumer state = 2 runs = 2
  28ms --- dispose consumer
  28ms   [status] consumer: ACTIVE -> UNLOADING
  28ms consumer run=2 disposer: store reachable? store#2
  28ms   [status] consumer: UNLOADING -> DISPOSED
  29ms node v24.22.2
```

这段输出有三点值得注意。第一，最初的 PENDING 没有触发 status 事件，因为状态没有变化。第二，提供方的 `provider effect #1 disposed` 出现在消费方回到 PENDING **之后**，说明提供方先等消费方卸载完。第三，第二次 apply 拿到的是 `store#2`，说明 apply 闭包是重新创建的，必须写成可以重复执行。**[源码]** 消费方能在 disposer 里继续使用服务，是因为 `fiber.committed` 在卸载期间保留了"Required implementations retained through dependent teardown"，`locate()` 在 store 中的条目已经删除后仍然可以通过它解析服务（[fiber.ts L225-L226](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L225-L226)，[reflect.ts L147-L166](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L147-L166)）。**[文档]** 教程也说 PENDING 是正常状态，一个缺少提供方的插件会"waits forever, printing nothing"，而且不会让 Node 事件循环保持存活（[06-composition-and-hmr.md L23-L109](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/06-composition-and-hmr.md#L23-L109)，[03-services.md L72](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/03-services.md#L72)）。

### apply 抛错会先回滚再进入 FAILED，restart 无法把它拉回来

**[源码]** `_reload()` 捕获到错误后，如果 epoch 没有变化，就设置 `_error` 并把 epoch 改成 INACTIVE。这个新 epoch 和旧值不同，所以失败的加载**也会走一次 `_unload`**（[fiber.ts L703-L732](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L703-L732)）。**[实测]** `e04-apply-fails.mjs` 中的插件先注册一个包在 effect 里的 setInterval 和一个 `ctx.on('custom/ping')`，然后同步抛错，或者 `await sleep(3)` 之后抛错：

```
   1ms === sync
   4ms   [status] p-sync: PENDING -> LOADING
   4ms   [logger.error] p-sync boom (sync)
   4ms   [status] p-sync: LOADING -> UNLOADING
   4ms   p-sync: interval effect disposed
   4ms   [status] p-sync: UNLOADING -> FAILED
   4ms   await fiber rejected: p-sync boom (sync)
   4ms   state=3
  25ms   interval ticks during 20ms after failure: 0
  26ms   effects still registered: []
  26ms   --- recover via fiber.update({ ok: true })
  26ms   [status] p-sync: FAILED -> LOADING
  26ms   p-sync: apply ok with config {"ok":true}
  26ms   [status] p-sync: LOADING -> ACTIVE
  26ms   state=2
  26ms   p-sync: got ping
  ...
  26ms === async
  26ms   [status] p-async: PENDING -> LOADING
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
```

在 FAILED 状态下发出 `custom/ping` 没有任何输出，说明监听器已经被回滚。`e04b-restart-failed.mjs` 则表明，**`restart()` 不会重跑 apply，只是用原来的错误 reject**。传给 `update()` 的配置即使和原来一样，也能恢复：

```
   3ms   [status] flaky: UNLOADING -> FAILED
   3ms state 3 attempts 1
   4ms   restart rejected: first attempt fails
   4ms state 3 attempts 1
   4ms   [status] flaky: FAILED -> LOADING
   4ms   apply ok, attempt 2
   4ms   [status] flaky: LOADING -> ACTIVE
   4ms   update resolved
   4ms state 2 attempts 2
```

**[源码]** 原因有两个。`restart()` 只做 `_revision++` 然后调用 `_refresh()`，而 `_setEpoch` 在 `_error` 有值时直接返回。`update()` 则会执行 `fiber._error = undefined`（[fiber.ts L790-L796](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L790-L796)，[L805-L841](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L805-L841)）。另外，config 的校验是在 LOADING 阶段由 `_runner.execute` 先跑的 `internal/config` waterfall 完成的（[fiber.ts L272-L290](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L272-L290)）。所以一个停在 PENDING 的插件即使配置写错，也要等依赖到齐后才会暴露成 FAILED。这一点来自读源码，没有实测。

### 配置更新默认是完整重载，拦截 `internal/update` 才能原地更新

**[源码]** `update(config)` 先同步校验（失败时在任何提交之前就抛出 `ValidationError`），再发出 `internal/commit`，然后清掉 `_error`。如果 Fiber 已加载，就走一次 `internal/update` waterfall，链条最后的 `next` 是 `restart`；如果是 PENDING 或 FAILED，就直接 restart（[fiber.ts L805-L841](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L805-L841)）。非 global 的 `ctx.on('internal/update')` 会被转存到当前 Fiber 的 `_hooks` 里，它本身也是一个 effect（[events.ts L139-L154](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/events.ts#L139-L154)）。**[实测]** `e07-config-update.mjs` 中，没有监听器的插件会完整重载；监听器不调用 `restart` 时是原地更新，`restart()` 则总是重载：

```
   1ms === default plugin
   4ms   [status] reloader: PENDING -> LOADING
   4ms   reloader apply #1 interval=1
   4ms   [status] reloader: LOADING -> ACTIVE
   5ms   [status] reloader: ACTIVE -> UNLOADING
   5ms   reloader #1 disposed
   5ms   [status] reloader: UNLOADING -> LOADING
   5ms   reloader apply #2 interval=2
   5ms   [status] reloader: LOADING -> ACTIVE
   5ms   applies=2, a.config={"interval":2}
   5ms === plugin with internal/update listener
   5ms   [status] inplace: PENDING -> LOADING
   5ms   inplace apply #1 interval=1
   5ms   [status] inplace: LOADING -> ACTIVE
   5ms   inplace: hot-applied interval 1 -> 5 (no restart)
   5ms   inplaceApplies=1, b.config={"interval":5,"mode":"x"}, effectiveConfig={"interval":5,"mode":"x"}
   5ms   inplace: mode changed -> restart
   5ms   [status] inplace: ACTIVE -> UNLOADING
   5ms   inplace #1 disposed
   5ms   [status] inplace: UNLOADING -> LOADING
   6ms   inplace apply #2 interval=5
   6ms   [status] inplace: LOADING -> ACTIVE
   6ms   inplaceApplies=2
   6ms === restart() always reloads
   6ms   [status] reloader: ACTIVE -> UNLOADING
   6ms   reloader #2 disposed
   6ms   [status] reloader: UNLOADING -> LOADING
   6ms   reloader apply #3 interval=2
   6ms   [status] reloader: LOADING -> ACTIVE
   6ms   applies=3
```

监听器的写法是 `ctx.on('internal/update', (next, restart) => { if (需要重载) return restart(); current = next })`。不过 **[源码]** 在 packages 下 2819 个非测试 .ts 文件里，`'internal/update'` 一处都没有出现。它只在 vendor 的 loader、group、include 中使用。dsh 插件实际的"不重启就生效"路径是 schema 里的 `.volatile()` 字段：loader 会绕过 `internal/update`，发出 `loader/volatile-update`；直接调用 `fiber.update()` 时仍然会走完整的生命周期（[05-config.md L70-L95](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/05-config.md#L70-L95)）。这意味着每次非 volatile 的配置变更，都会把下文讨论的所有卸载顺序问题重新走一遍。

## ctx.effect 立即执行，先登记 disposer 再执行，所有权可以转移

**[源码]** `ctx.effect` 是通过 mixin 挂到 ctx 上的 `fiber.effect(execute, label = 'anonymous')`（[reflect.ts L256](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L256)）。`execute` 会**同步立即执行**。它可以返回一个 disposer 或 nullish，也可以返回 Promise<disposer>、同步生成器或异步生成器。返回非对象会抛 `TypeError('Invalid effect')`，生成器 yield 出非函数值也会抛 TypeError（[fiber.ts L384-L428](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L384-L428)）。入口处有两道检查：先调用 `assertActive()`，`uid === null` 时抛 `INACTIVE_EFFECT`；**接着在 `state === UNLOADING` 时也抛 `INACTIVE_EFFECT`**（[fiber.ts L447-L450](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L447-L450)）。包装好的 disposer 在 `_execute` **之前**就已经 push 进 `fiber._disposables`，这样即使在 execute 运行插件代码之前发生了重入的卸载，也能看到它（[fiber.ts L545-L550](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L545-L550)）。

嵌套 effect 的所有权转移只认精确的身份。内层 runner 的 `collect` 会把收集到的 disposer 放进本 effect 的局部列表，**同时从 `fiber._disposables` 中删除这个函数**，并把它的元数据挂成 `meta.children`（[fiber.ts L472-L484](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L472-L484)）。所以，在 effect 里调用了另一个 `ctx.effect`，却不 return 或 yield 它返回的 disposer，内层 effect 仍然是 Fiber 顶层的兄弟。yield 一个包装函数（`() => inner()`）也不行，因为删除时按函数身份匹配，原 disposer 留在原地。disposer 本身只会执行一次（runner.epoch 置为 false）。setup 还在进行中时调用它，会等 setup 完成后再清理。effect 的返回值可以 await，结果是一个 disposer 而不是 void（[fiber.ts L495-L588](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L495-L588)）。setup 同步抛错时，已经收集的部分会被回滚，然后把错误抛给调用方。异步 reject 被 `task.catch` 接住，回滚后交给 `ctx.logger.error`，不会变成未处理的 rejection（[fiber.ts L551-L576](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L551-L576)）。异步生成器在每次调用 `iter.next()` 之前都会检查 `runner.epoch !== oldEpoch`，一旦在 setup 中途被卸载，就不再继续获取后面的资源。普通的 async effect 中途无法打断，它的 disposer 只能等 Promise 落定。

### await 之后注册 effect 会抛 INACTIVE_EFFECT，裸资源则会泄漏

**[实测]** `e05-effect-after-await.mjs` 在 apply 的 `await` 期间调用 `dispose()`，然后在 await 之后分别尝试 `ctx.on`、`ctx.effect`，以及创建一个裸 `setInterval`。第二段对照组把异步连接放进 `ctx.effect(async function* …)`：

```
   3ms   [status] slow: PENDING -> LOADING
   6ms   apply: before await
  10ms === dispose while apply is awaiting; state = 1
  11ms   dispose() called, state = 1
  26ms   apply: after await, fiber.state = 1 uid = null
  27ms   ctx.on threw: INACTIVE_EFFECT cannot create effect on inactive context
  27ms   ctx.effect threw: INACTIVE_EFFECT cannot create effect on inactive context
  27ms   [status] slow: LOADING -> UNLOADING
  28ms   [status] slow: UNLOADING -> DISPOSED
  28ms   dispose() resolved, state = 4
  89ms   after 60ms: raw interval ticks=11, effect interval ticks=0
  89ms === async-generator effect, disposed mid-connect
  89ms   [status] good: PENDING -> LOADING
  92ms   good: connecting...
  92ms   [status] good: LOADING -> ACTIVE
  96ms   [status] good: ACTIVE -> UNLOADING
 112ms   good: connected (conns=1)
 113ms   good: connection closed (conns=0)
 113ms   [status] good: UNLOADING -> DISPOSED
 113ms   dispose resolved; conns = 0
 153ms   40ms later; conns = 0
```

结论有四点。**`dispose()` 会把 uid 立即设为 null，然后等正在运行的 apply 结束**，所以 dispose 在第 11ms 调用、第 28ms 才 resolve。await 之后通过 ctx API 注册的东西会同步抛错，effect 函数体根本不会执行，因此不会泄漏。裸 `setInterval` 在插件卸载后的 60ms 里又触发了 11 次。异步生成器 effect 会等连接建立完，再执行 yield 出来的 close，连接数最终回到 0。另外还能看到，apply 是 async 时，插件在 await 期间一直是 LOADING；apply 同步返回、只有 effect 是异步时，插件立即进入 ACTIVE（good 在第 92ms 已经 ACTIVE，但连接还没建立）。

**[实测]** `e06-resources.mjs` 比较了包进 effect 和不包进 effect 的定时器、子进程、`fs.watch`、TCP server 和 client。leaky 版本在插件 DISPOSED 之后，9 个句柄一个都没少，子进程还活着，进程退不出来。owned 版本的句柄全部清零，进程以 exit 0 退出：

```
   1ms === leaky; baseline resources: []
  61ms   loaded; resources: [FSEventWrap,PipeWrap,PipeWrap,PipeWrap,ProcessWrap,TCPServerWrap,TCPSocketWrap,TCPSocketWrap,Timeout]
 112ms   disposed (state=4); resources: [FSEventWrap,PipeWrap,PipeWrap,PipeWrap,ProcessWrap,TCPServerWrap,TCPSocketWrap,TCPSocketWrap,Timeout]
 112ms   child pid 122059 still running? true
1113ms   process did NOT exit by itself within 1s -> leaked handles keep the event loop alive
exit=1

   1ms === owned; baseline resources: []
  66ms   loaded; resources: [FSEventWrap,PipeWrap,PipeWrap,PipeWrap,ProcessWrap,TCPServerWrap,TCPSocketWrap,TCPSocketWrap,Timeout]
  67ms   child exited code=null signal=SIGTERM
 118ms   disposed (state=4); resources: []
 118ms   child pid 122072 still running? false
exit=0
```

owned 版本里，子进程的写法有一个关键细节：**在调用 kill 之前**先执行 `const exited = once(child, 'exit')`，disposer 写成 `async () => { if (还没退出) child.kill('SIGTERM'); await exited }`。`e06b-kill-no-await.mjs` 模拟一个收到 SIGTERM 后需要 300ms 收尾的子进程，说明了不等待退出的后果：

```
 109ms   mode=noawait: dispose() took 1ms; child alive at dispose resolve? true
 422ms   child actually exited 307ms after dispose() started
 417ms   mode=await: dispose() took 307ms; child alive at dispose resolve? false
 418ms   child actually exited 307ms after dispose() started
```

这正是 **[文档]** defensive-patterns 中 "Dispose must reach quiescence, not just request it" 和审计技能里 "Aborting or killing without awaiting settlement is a violation" 所针对的问题（[general.md L223-L227](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/defensive-patterns/general.md#L223-L227)，[SKILL.md L31-L35](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L31-L35)）。只做 kill，`dispose()` 1ms 就 resolve 了，子进程实际还会再活 307ms。

### 几乎所有注册 API 都是 effect，label 可以用来识别

**[源码]** 下表中的 API 都通过 `fiber.effect` 实现，`fiber.getEffects()` 可以看到它们的 label（[events.ts L267-L311](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/events.ts#L267-L311)，[reflect.ts L313-L430](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L313-L430)，[vendor/timer/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/timer/src/index.ts#L1-L54)，[tools/src/index.ts L128-L153](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/core/tools/src/index.ts#L128-L153)，[scope/src/store.ts L219-L266](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/core/scope/src/store.ts#L219-L266)）：

| API | effect label | 说明 |
|---|---|---|
| `ctx.on` / `ctx.once` | `ctx.on("name")` | once 是带自我释放的 on |
| `ctx.on('internal/update')` | `ctx.on("internal/update")` | 存到当前 Fiber 的 `_hooks`，只对本 Fiber 生效 |
| `ctx.plugin` / `ctx.inject` | `ctx.plugin()` | 子 Fiber 是父 Fiber 的一个 effect；`inject` 就是 `plugin({inject, apply})` |
| `ctx.provide`、`Service` 构造 | `ctx.provide("name")` | disposer 是异步的，要等 dependents 清空 |
| `ctx.accessor` / `ctx.mixin` | `ctx.accessor(...)` / `ctx.mixin(...)` | mixin 是生成器 effect，accessor 是它的子 effect |
| 定时器服务 `ctx.timeout/interval/throttle/debounce` | `ctx.timeout()` 等 | 需要 `inject: ['timer']`；Promise 形式在卸载时以 `Context has been disposed` reject |
| dsh `ctx.tools.register` | `tools.register()` | 经 `ScopedLayers.effect`，是一个生成器 effect |

e02 的输出里，`ctx.plugin()` 本身就作为一个 effect 出现在 `getEffects()` 的列表中。**[源码]** 有一点特别要注意：服务方法通过 traceable 代理拿到的 `this.ctx` 是**调用方**的 ctx，所以服务内部调用 `this.ctx.effect` 时，effect 登记在调用方插件上（[utils.ts L170-L180](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/utils.ts#L170-L180)）。**[文档]** 仓库约定 "Registrations are effects… a registry's `register()` returns the disposer"（[AGENTS.md L135](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/AGENTS.md#L135)）。Node 的全局 `setTimeout`/`setInterval` 不是 effect，要么自己包进 `ctx.effect`，要么改用定时器服务。

## 卸载顺序：单个 effect 内串行，顶层 effect 并发，提供方等消费方先走

**[源码]** 卸载有两套规则。单个 effect 内部，`dispose()` 用 `disposables.splice(0).reverse()` 逆序执行，遇到 thenable 后就用 `task.then` 把后面的串起来，**严格串行**（[fiber.ts L452-L470](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L452-L470)）。Fiber 层面，`_unload()` 先执行 `while (provisions 里任何 impl 还有 dependents) await`，然后对 `_disposables.clear()`（已逆序）的结果做 `await Promise.all(map(async d => { await Promise.resolve(); … runDisposable(d) }))`，每个 disposer 各自 try/catch 并记录日志。最后从所用 impl 的 `dependents` 里删掉自己，唤醒正在等待的提供方（[fiber.ts L734-L768](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L734-L768)）。所以，**顶层 effect 按逆序启动，异步的那些并发执行**。子插件只是父插件的一个 `ctx.plugin()` effect，和父插件自己的其他 effect 同时开始清理。父插件的 `_unload` 会等 `Promise.all`，子插件的 disposer 又会等子 Fiber 完全卸载，因此父插件一定在子插件全部卸载之后才到达终态。

**[实测]** `e02-dispose-order.mjs` 中，父插件依次注册了：A（异步 30ms）、一个子插件（内部有 E1 20ms、E2 5ms）、B（异步 10ms）、C（同步），以及一个生成器 effect（yield y1/y2/y3，每个 15ms）：

```
   2ms effects: ["anonymous","ctx.plugin()","anonymous","anonymous","generator"]
   3ms --- parent.dispose() called
   3ms --- dispose() returned (sync part)
   4ms   start gen.y3 (15ms)
   4ms   sync parent.C (registered 4th)
   4ms   start parent.B (registered 3rd) (10ms)
   4ms   start parent.A (registered 1st) (30ms)
   4ms   start child.E2 (5ms)
   4ms   start child.E1 (20ms)
   9ms   end   child.E2
  14ms   end   parent.B (registered 3rd)
  19ms   end   gen.y3
  19ms   start gen.y2 (15ms)
  25ms   end   child.E1
  34ms   end   parent.A (registered 1st)
  35ms   end   gen.y2
  35ms   start gen.y1 (15ms)
  51ms   end   gen.y1
  51ms --- await dispose() resolved; node v24.22.2
```

启动顺序是逆序的：generator → C → B → A。子插件的 E2/E1 多经过了几层 microtask，所以排在 A 后面启动。完成顺序则完全由各自的耗时决定，说明兄弟之间是并发的。生成器内部 y3 → y2 → y1 严格串行。`await dispose()` 在最后一个 disposer 完成（51ms）时才 resolve。这说明"逆序"只对同步 disposer 有意义。**清理步骤之间需要先后顺序时，只能把它们放进同一个 disposer 依次 await，或者放进同一个生成器 effect 里 yield**。**[文档]** 教程第 94 行写的就是这一点（"disposers start in reverse registration order, but multiple async disposers run concurrently"，[02-lifecycle-and-effects.md L94](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/02-lifecycle-and-effects.md#L94)）。审计技能进一步提醒："Top-level plugin generator yields are not a general promise of serial sibling disposal"（[SKILL.md L41-L43](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L41-L43)）。原因是 apply 本身写成 `function*` 时，yield 出来的东西进入 `fiber._disposables`，仍然会被并发执行。

两层规则对错误的处理也不一样。**[源码]** Fiber 层面每个 disposer 各自 try/catch，一个失败不影响其他兄弟。单个 effect 内部，列表已经 `splice(0)` 取空，所以一个同步抛错的 disposer 会跳出 for 循环，**同一个 effect 里更早注册的 disposer 就会被跳过**，异步 reject 也会中断后续的 `.then` 链。这是读源码得出的推论，本次没有实测。它解释了审计技能为什么要求"one cleanup error must not skip required abort, settlement, or resource release"：用生成器把清理串起来的同时，每一步也要自己兜住错误。

提供方和消费方之间的顺序靠两个机制保证。**[源码]** 第一，提供方离开 ACTIVE 时调用 notify，消费方 `_checkImpl` 发现依赖缺失，epoch 变成 INACTIVE，于是开始卸载。第二，提供方的 `_unload` 被 dependents 屏障挡住，同时 `ctx.provide` 自己的异步 disposer 也会等 `impl.dependents` 清空，并且至少让出一次（[reflect.ts L313-L349](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L313-L349)）。e01 中 `provider effect #1 disposed` 出现在消费方回到 PENDING 之后，就是这个屏障在起作用。**但屏障只保护"提供这个服务的那个 Fiber 自己的 effect"**。下一节的竞态就出在这里。

## "提供方先释放资源"只在绕开 inject 依赖边时出现

**[实测]** 先看最直接的写法：资源和 `ctx.provide` 是同一个提供方 Fiber 的两个兄弟 effect，消费方通过 inject 依赖这个服务，消费方的 disposer 先 `await sleep(5)` 再写入 FINAL。`e03-provider-race.mjs` 跑了 naive 和 fixed 两种写法、三种场景（释放提供方 / 释放父插件 / 两者都释放），6 种组合都没有丢失写入。下面是 naive 写法的输出：

```
   2ms === mode=naive scenario=provider
   3ms   writer: flushing final record...
   9ms   writer: FINAL written OK
   9ms   db.close() begin
   9ms   db.close() done
   9ms === disposed

   2ms === mode=naive scenario=parent
   4ms   writer: flushing final record...
  10ms   writer: FINAL written OK
  10ms   db.close() begin
  11ms   db.close() done
  11ms === disposed

   2ms === mode=naive scenario=both
   4ms   writer: flushing final record...
   9ms   writer: FINAL written OK
   9ms   db.close() begin
  11ms   db.close() done
  11ms === disposed
```

原因是上一节说的 dependents 屏障：资源关闭和服务撤回在同一个 Fiber 上，`_unload` 会先等消费方卸载完。能稳定复现竞态的是资源释放**不经过这条依赖边**的四种形状（`e03b-provider-race-variants.mjs`）：

```
   5ms === V1: dispose parent
   7ms   db.close() begin
   8ms   writer: flushing final record...
   8ms   db.close() done
  13ms   writer: ERROR db closed, lost write "FINAL"

   4ms === V1fix: dispose parent
   6ms   writer: flushing final record...
  12ms   db.write(FINAL) ok
  12ms   db.close() begin
  13ms   db.close() done

   4ms === V2: dispose store
   6ms   writer: flushing final record...
   6ms   db.close() begin
   7ms   db.close() done
  11ms   writer: ERROR db closed, lost write "FINAL"

   4ms === V2fix: dispose store
   6ms   writer: flushing final record...
  12ms   db.write(FINAL) ok
  12ms   db.close() begin
  13ms   db.close() done

   2ms   job: running...
   3ms === V4: dispose provider
   3ms   db.close() begin
   5ms   db.close() done
   9ms   job: ERROR db closed, lost write "JOB RESULT"

   3ms   job: running...
   4ms === V4fix: dispose provider
  10ms   db.write(JOB RESULT) ok
  10ms   db.close() begin
  12ms   db.close() done
```

V3 需要先等提供方进入 ACTIVE（`node e03b-provider-race-variants.mjs V3 awaitProvider`）：

```
   5ms === V3: dispose provider
   8ms   db.close() begin
   9ms   db.close() done
  30ms   untracked consumer state=2 (2=ACTIVE) after provider gone; now dispose it
  30ms   writer: flushing final record...
  37ms   writer: ERROR db closed, lost write "FINAL"
```

| 形状 | 写法 | 为什么出问题 | 修法（实测通过） |
|---|---|---|---|
| V1 | 父插件 `ctx.effect(() => () => db.close())`，子插件 `c.provide('db', db)` | 子 Fiber 的屏障确实在等 writer，但 `db.close` 是父插件里 `ctx.plugin()` 的兄弟 effect，第 7ms 就开始关闭 | 在同一个 `ctx.effect(function* …)` 里先 `yield ctx.effect(() => () => db.close())`，再 `yield ctx.plugin(...).dispose`（必须是这个精确的 disposer） |
| V2 | 同一插件里，flush 和 close 是两个兄弟 effect | 顶层 effect 并发 | 合并成一个 disposer：`async () => { await flush(); await db.close() }` |
| V3 | 消费方没写 inject，用 `ctx.get('db')` | 没有登记为 dependent，提供方不会等它；提供方走后消费方仍是 ACTIVE | 补上 inject，或者改成 `ctx.inject` 子 Fiber |
| V4 | 消费方有一个不归 Fiber 管理的后台任务 | 任务对 Fiber 不可见 | `ctx.effect(() => { const task = run(); return () => task })` |

V1fix 中，yield 的必须是 `ctx.plugin(...)` 返回的 Fiber 的 `.dispose`。**[源码]** `collect` 会执行 `_disposables.delete(dispose)`，把这个 disposer 从父 Fiber 的顶层列表里移走，所有权转给生成器。生成器逆序串行执行时，先撤回服务（等消费方卸载完），再关闭 db。这和审计技能给出的 workaround 一致："Publish the service last with `yield ctx.provide(name, service)`… Do not yield replacement wrappers around already registered disposers"（[SKILL.md L45-L55](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L45-L55)）。审计技能说"Adding `inject`, reversing registration order, or retaining a service reference does not fix that race"，这和基线实验并不矛盾。它针对的是 V1、V2 这类形状：V1 的 writer 本来就写了 inject，问题照样出现。V3 还有一个附带现象 **[实测]**：如果不先等提供方 ACTIVE，消费方 apply 执行时提供方还在 LOADING，`ctx.get('db')` 直接返回 `undefined`，输出是 `writer: ERROR Cannot read properties of undefined (reading 'write')`。也就是说，不写 inject 时，能否拿到服务取决于加载时序。

**[文档]** 审计技能还说了 workaround 管不到的情形："Consumer disposal removes a fiber from the registry before its cleanup finishes, so service withdrawal can miss an already-disposing consumer. For root or enclosing-owner disposal… retain and await the actual consumer disposal tasks through an explicit owner."（[SKILL.md L41-L55](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L41-L55)）。**[实测]** `e03c-enclosing-owner.mjs` 试了两种交错：一种是消费方和 composed provider 作为同一个父插件的兄弟子插件，然后卸载父插件；另一种是先开始卸载消费方，1ms 后再卸载父插件。两种都**没有**复现：

```
   5ms === dispose app (owner of both)
   7ms   writer: flushing...
  12ms   db.write(FINAL) ok
  12ms   db.close() begin
  13ms   db.close() done

   4ms === start consumer disposal, then dispose app 1ms later
   6ms   writer: flushing...
  11ms   db.write(FINAL) ok
  12ms   db.close() begin
  13ms   db.close() done
```

**[源码]** 也支持实验结果：`impl.dependents` 要到消费方 `_unload` **结束时**才删除，屏障在这之前一直看得到它。技能说的"从 registry 移除"，可能指的是 notify 遍历 `runtime.fibers` 那条路径，也可能是根上下文释放、isolate、`[Service.init]` 生成器或 loader/HMR 路径。这些本次都没有覆盖。所以只能说"技能声称有这个风险，本实验没有复现"，既不能说它一定发生，也不能说它不存在。

## 可选依赖要用子 Fiber 承载，不能用 ctx.get 探测

**[源码]** 这个版本的 Cordis **没有可选 inject 的语法**。`Inject.resolve` 把数组和对象形式都规范成 `{ name: config | null }`，`_refresh` 遇到第一个缺失的 key 就判定 INACTIVE（[registry.ts L63-L89](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/registry.ts#L63-L89)，[fiber.ts L639-L650](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L639-L650)）。可选依赖有两种表达方式。`ctx.get(name)` 是一次性探测：只有提供方处于 ACTIVE 时才返回服务，不会建立任何生命周期关联（[reflect.ts L269-L279](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L269-L279)）。`ctx.inject(deps, cb)` 实际上就是 `ctx.plugin({ inject, apply: cb, name: cb.name })`，会创建一个独立的子 Fiber（[registry.ts L300-L302](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/registry.ts#L300-L302)）。**[文档]** 教程推荐用 `ctx.get` 在使用处探测："`inject` is for hard requirements. For a capability the plugin can live without, skip `inject` and probe at the use site"（[03-services.md L80-L90](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/03-services.md#L80-L90)）。

**[实测]** `e08-optional-inject.mjs`：主插件没有声明 metrics，在 apply 里调用 `ctx.inject(['metrics'], metricsBridge)`：

```
   2ms   [status] main: PENDING -> LOADING
   3ms   main apply (no required deps)
   3ms   ctx.get("metrics") = undefined
   3ms   ctx.metrics without inject threw: cannot get property "metrics" without inject
   4ms   [status] main: LOADING -> ACTIVE
   6ms === provide metrics
   6ms   [status] metrics-provider: PENDING -> LOADING
   7ms   [status] metrics-provider: LOADING -> ACTIVE
   7ms   [status] metricsBridge: PENDING -> LOADING
   7ms   metricsBridge apply: metrics = m1
   7ms   [status] metricsBridge: LOADING -> ACTIVE
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

子 Fiber 会随服务的出现和消失，在 ACTIVE 和 PENDING 之间切换，并清理自己的 effect，主插件全程保持 ACTIVE。**它还被登记为 dependent，所以提供方会等它卸载完**。结合 V3 的结果，教程推荐的 `ctx.get` 只适合"用一下就走"的同步调用。只要有长期持有的资源绑定在那个服务上，或者清理时还要用到它，就应该用 `ctx.inject` 子 Fiber。**[源码]** packages 非测试代码中有 99 处 `ctx.inject([`，分布在 88 个文件。subagent 是一个典型例子：`ctx.inject(['agents'], (childCtx) => { const manager = …; this.manager = manager; childCtx.effect(() => () => { if (this.manager === manager) this.manager = undefined }, 'subagents.managerBinding()') })`，清理时用身份比较，避免误清掉新绑定（[subagent/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/subagent/subagent/src/index.ts#L216-L232)）。

## 官方审计的三条 P0 规则，真实插件基本照做

**[文档]** 生命周期规则写在四个地方：教程第 2、3、6 章，`docs/defensive-patterns/general.md`，AGENTS.md，以及 `dsh-code-review` 会调用的 `cordis-styling-nitpicking` 技能（"Cordis Ownership and Lifecycle Audit"）。这个技能只有三条 P0 规则（[dsh-code-review/SKILL.md L37](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/dsh-code-review/SKILL.md#L37)），每一条都能对应到上文的某个机制。**单例**：禁止从模块加载、static、全局 symbol 或隐藏缓存产生进程级的可变拥有者，而且明确写了 "Weak keys, size bounds, generation checks… do not give a module-level cache Cordis ownership"（[SKILL.md L25-L29](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L25-L29)）。对应的机制是 `_unload` 只回收 `_disposables` 和 `provisions` 能触达的状态，加上 e01 看到的 apply 重跑会拿到新实例，模块级缓存会把旧实例留下来。**未回滚的副作用**：监听器、定时器、子进程、连接、临时文件和异步任务在卸载后不能继续存活，kill 之后不等退出也算违规（[SKILL.md L31-L35](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L31-L35)），对应 e05、e06、e06b。**清理依赖在消费方完成前被释放**：要求"stop admission, settle admitted consumers while their dependencies remain usable, then drain and release"（[SKILL.md L37-L43](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L37-L43)），对应 e03b。技能给出的回归测试方法是"把消费方暂停在最后一次依赖使用之前，再开始卸载提供方"，e03 里的 `await sleep(5)` 就是它的简化版。defensive-patterns 还有一条"Plugins do not read the Cordis root context"，由 `verify-cordis-root` 门禁执行，理由是根上下文没有 runtime，读它会跳过 inject 检查，在上面注册的监听器也不会随插件卸载而移除（[general.md L124-L151](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/defensive-patterns/general.md#L124-L151)）。

**[源码]** 本次阅读的 5 个包里有四种反复出现的写法，每一种都有对应的实验。第一种是**资源拥有者对象，加一个 yield 出来的异步 disposer**。skill-filesystem 把 chokidar watcher 交给 `FileSystemSkillProvider` 管理，再用 `ctx.effect(function* () { yield async () => { await provider.dispose() } }, 'skill-filesystem watcher')` 接到 Fiber 上（[skill-filesystem/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/skill/skill-filesystem/src/index.ts#L134-L148)）。spill-local 启动清扫任务后不 await 它，而是 yield 一个 await 同一个 promise 的 disposer，注释写着"service availability is never delayed yet a fiber unload reaches quiescence"（[spill-local/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/spill/spill-local/src/index.ts#L91-L102)），形状和 V4fix 一样。第二种是**把 registry 注册写成生成器 effect：先写入，yield 回滚函数，最后 emit**。web 的 `registerProvider`（[web/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/web/web/src/index.ts#L150-L165)）和 subagent 的 `registerProvider` 都是这样，后者的注释说"A throwing added-listener unwinds the yielded rollback"。第三种是**用 yield 的顺序表达"先排空，再释放"**。subagent manager 写的是 `yield scope.dispose; yield () => this.drain()`，逆序执行时先 drain 再释放 scope（[manager.ts L183-L186](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/subagent/subagent/src/manager.ts#L183-L186)）。mcp-manager 的 `[Service.init]` 生成器 yield 出的 disposer 依次执行 `stop()`、`await this.tail`、逐个 unmount（[mcp-manager/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/mcp/mcp-manager/src/index.ts#L182-L188)）。第四种是**在 await 之后检查 `fiber.uid`，或者监听自己的释放事件**。packages 中 `fiber.uid === null` 或 `fiber.uid !== null` 共出现 23 次，分布在 17 个文件。mcp-client 监听自身的 `internal/plugin` 事件，看到 `uid === null` 时就关闭 transport，注释写着 "Cordis announces unload before awaiting an unfinished apply()"（[mcp-client/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/mcp/mcp-client/src/index.ts#L192-L205)），用来避免 e05 中看到的"dispose 被一直挂起的 apply 拖住"。在 packages 的 2819 个文件里，effect 调用共 662 处，分布在 326 个文件，其中生成器 effect 有 55 处，分布在 40 个文件。

规则和现状也有对不上的地方。mcp-client 有一个模块级的 `const activeServerNames = new WeakMap<object, Set<string>>()`，按单例规则的字面意思属于疑似单例。不过它的 effect 有对称的 add/delete，本次也没有复现出可观察的缺陷。web-app 有一个模块级的 `ANNOUNCED_ROOTS` WeakSet。`scripts/cordis-root.baseline.json` 中有 27 个条目，11 个是 `src/` 文件，mcp-client 在其中（[cordis-root.baseline.json](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/scripts/cordis-root.baseline.json)）。过程笔记解释了为什么采取"先禁止新增、存量冻结进基线"的做法：一些替换需要先设计新服务（[2026-11-13-no-new-cordis-root-reads.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/notes/implemented/process/2026-11-13-no-new-cordis-root-reads.md)）。所以准确的说法是"规则加上存量例外"，而不是"真实插件违反了规则"。这些判断是我对照规则文字做出的，不是官方的审计结论。

### 文档与源码的四处不一致，以及仍未解决的问题

| 位置 | 文档怎么说 | 源码 / 实测 |
|---|---|---|
| `Disposable` JSDoc（[fiber.ts L98-L104](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L98-L104)） | "run in reverse registration order… unloading awaits them" | 只有启动是逆序的，顶层异步 disposer 并发执行（e02）。教程 L94 和审计技能已经更正了这一点 |
| `ctx.effect` JSDoc 和 API 页（[fiber.ts L430-L442](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L430-L442)，[cordis-api/fiber.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-api/fiber.md#L8-L37)） | 只在 "already disposed" 时抛 INACTIVE_EFFECT | 处于 UNLOADING 状态时也会抛（L447-L450）。在 disposer 里或卸载期间的回调里调用 `ctx.on`、`ctx.plugin`、`ctx.timeout` 都会失败。这一点只读了源码，没有实测 |
| 教程状态图（[02-lifecycle-and-effects.md L68-L80](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/02-lifecycle-and-effects.md#L68-L80)） | PENDING→LOADING→ACTIVE→UNLOADING→DISPOSED，LOADING↘FAILED | 缺了 ACTIVE→UNLOADING→PENDING、重启循环、失败时先经过 UNLOADING，以及 FAILED 只能靠 `update()` 恢复（e01/e04/e04b） |
| 教程可选依赖（[03-services.md L80-L90](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/03-services.md#L80-L90)） | 用 `ctx.get` 在使用处探测 | 用法本身没错，但没有说明它不建立依赖关系、提供方不会等它、结果取决于加载时序（e03b V3） |

还有几个问题没有解决。第一，审计技能说的"service withdrawal can miss an already-disposing consumer"，本次找不到能触发它的交错，可能藏在 isolate、`Service` 类的 `[Service.init]` 生成器、loader/HMR 或根上下文释放路径里。第二，apply 永远不结束时，`dispose()` 是否也会永远挂起。按 e05 的行为和 mcp-client 的注释推断会挂起，但没有实测。第三，单个 effect 内某个 disposer 同步抛错会跳过其余 disposer，以及在 FAILED 的 Fiber 上还能新建 effect，这两点都只来自读源码。第四，V1/V2 只用函数和对象形式的插件测过，没有用 `extends Service` 的真实服务类复测；volatile 配置更新也没有通过 loader 实际跑过。第五，没有运行 dsh 自带的 `scripts/cordis-core-lifecycle.spec.ts`，也没有逐字节确认 npm 包和 vendor 源码一致。源码里 `internal/status` 旁边有一条 "FIXME internal/fiber-info" 注释，这个事件名以后可能会改。

## Conclusion

在这个版本的 Cordis 里，**依赖关系本身就是清理顺序的保证**。inject 依赖边上的东西，框架都已经处理好了：消费方在自己的 disposer 里仍能使用服务，提供方会等消费方卸载完，失败的 apply 会自动回滚。出问题的总是框架看不见的那部分：父插件持有的资源、并发执行的兄弟 disposer、通过 `ctx.get` 拿到的服务、裸定时器和后台任务，以及只 kill 不等退出的子进程。所以写 dsh 插件时，与其记一串"先做什么后做什么"的规则，不如对每个资源回答一个问题：**它的释放挂在哪一个 disposer 上，这个 disposer 和使用这个资源的消费方之间，是否有 inject 依赖边，或者同在一个生成器里串行执行**。两者都没有，就是竞态。

这也说明审计技能和教程是互补的。教程讲的是 Cordis 能保证什么，审计技能讲的是这些保证管不到的地方。不过它们有一个共同的缺口：都没有把"顶层并发、单个 effect 内串行"和"按函数身份转移所有权"这两条底层规则讲完整，而 V1fix 能不能修好，正取决于这两条。审计技能声称的那个残余竞态，最好的处理方式是写成一个可以复现的 spec 测试，确认它在哪条路径上发生。

---

> **2026-11-23 更正（写文章时复跑实验）**：上文把 V3（使用方用 `ctx.get` 不声明 `inject`）的修法记为“实测通过”，但实验脚本里没有 V3fix 用例，带该参数运行会直接报错，所以这一条只有 e03 / e08 的间接证据。另外 web 插件 `registerProvider` 的实际顺序是“写入 → emit → yield 回滚”，不是“写入 → yield 回滚 → emit”。以 `deepseek/dsh_lifecycle_and_effects.md` 为准。
