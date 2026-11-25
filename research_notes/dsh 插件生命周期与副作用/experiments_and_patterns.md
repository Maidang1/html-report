# dsh / Cordis 插件生命周期与副作用：实测实验与真实插件模式

环境与来源（全部为本研究员实际执行）：
- 日期：2026-11-23；Node `v24.22.2`，npm `11.19.1`，Linux arm64。
- Cordis：`npm install @deepseek-ai/cordis@4.2.0-alpha.1`（npm 上的最新版本，与 dsh 仓库 `vendor/cordis/package.json` 的 `"version": "4.2.0-alpha.1"` 一致）。安装位置 `/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/66d83128-a37c-4163-ae6b-f001409e9960/scratchpad/lifecycle-lab/npm/`。
- dsh 仓库：`git clone --depth 1 https://github.com/deepseek-ai/deepseek-harness.git`，commit `4b399c39a6d1479cd84ca591b4d7c88e24d38a96`（2026-11-22 16:50:28 +0800），位于 `/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/66d83128-a37c-4163-ae6b-f001409e9960/scratchpad/lifecycle-lab/dsh`。（scratchpad 里已有的几个 clone 目录只有损坏的 `.git`，没有复用。）
- 实验脚本目录：`/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/66d83128-a37c-4163-ae6b-f001409e9960/scratchpad/lifecycle-lab/exp/`（以下简写为 `EXP/`）。共用的辅助模块是 `EXP/lib.mjs`：创建 `new Context()` 根上下文，并监听 `internal/status` 事件，把每次状态变化打印成 `[status] 名字: 旧 -> 新`。状态编号对照 `FiberState`：0 PENDING、1 LOADING、2 ACTIVE、3 FAILED、4 DISPOSED、5 UNLOADING。
- 每个实验的运行方式都是 `cd EXP && node <script> [args]`，输出原样贴出，左侧一列是相对进程启动的毫秒数。
- 标注约定：**[实测]** = 我运行并观察到的结果；**[文档]** = 从 dsh 文档或技能文件里读到的说法；**[源码]** = 从 cordis 源码读到的内容（深入解读 fiber.ts 由另一位研究员负责，这里只引用解释现象所需的几行）。
- GitHub 链接都固定在上面那个 commit：`https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/<path>`（下文写作 `GH/<path>`）。
- 已经读过 `deepseek/dsh_services_events_config_plugin.md`。那篇已经讲过：服务 Impl 记录、inject/check、五种分发模式、waterfall、volatile 的五个条件、cordis.yml 协调、HMR、acme-ledger 三种卸载路径。下面尽量不重复，只在需要时用一句话带过。

---

## Q1 依赖消失再回来：`inject: ['store']` 的消费方经历哪些状态

### Takeaway
[实测] 提供方卸载时，消费方先走 `ACTIVE → UNLOADING → PENDING`。它的 disposer 运行时**仍然能读到 `ctx.store`**，而且提供方自己的 effect 要等消费方卸载完才清理。提供方重新出现后，消费方走 `PENDING → LOADING → ACTIVE`，**`apply` 整个重跑一遍**，拿到的是新的服务实例。只有 `ctx.dispose()` 才会让它进入 DISPOSED。

### Cited Findings
- [实测] 脚本 `EXP/e01-dep-reappear.mjs`，命令 `node e01-dep-reappear.mjs`，完整输出：
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
  - 先加载的消费方停在 PENDING（state=0）。初始 PENDING 不会触发 `internal/status`，因为状态没有变化。
  - 依赖消失后，消费方回到的是 **PENDING**，而不是 DISPOSED 或 FAILED。
  - 提供方的 `provider effect #1 disposed` 打印在消费方 `UNLOADING -> PENDING` **之后**。
  - 第二次 apply 读到的是 `store#2`，说明重新执行了一次 apply，没有复用旧闭包。
- [源码] 提供方的顺序由 `_unload()` 开头这段保证：`while ([...this.provisions].some(impl => impl.dependents.size)) await ...`，注释写着 "Dependents are notified when this fiber leaves ACTIVE, but may still access its services during their own teardown, so the effects of this fiber are only disposed after every dependent has unloaded." — [GH/vendor/cordis/src/fiber.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts)
- [文档] 教程把 PENDING 定义为 "declared, but a required service is not available yet"，并说 PENDING 是 "why does my plugin print nothing?" 的常见答案 — [GH/docs/cordis-tutorial/02-lifecycle-and-effects.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/02-lifecycle-and-effects.md)

### Inferences
- 消费方的 apply 必须写成可以重复执行。apply 闭包里缓存的服务引用，每次重跑都要重新获取。模块级缓存会在重跑之间残留旧实例，这和仓库的单例禁令是同一个问题（见 Q9）。
- 这里有两个容易混淆的问题。"消费方 disposer 里还能不能用服务"，在 inject 跟踪到的依赖关系上，Cordis 已经替你保证了。真正的竞态出在**不经过这条依赖边**的资源释放上，见 Q3。

### Gaps
- 没有测"提供方被替换"（两个提供方先后出现）的情形，另一篇已经覆盖了"换提供方会让消费方重跑"。

---

## Q2 disposer 的顺序：同一插件内部、父子插件之间；异步 disposer 会不会被等待

### Takeaway
[实测] **同一个 Fiber 的顶层 effect 按注册的逆序"启动"，但会并发执行。** 异步 disposer 之间不会互相等待。子插件只是父插件的一个兄弟 effect（`ctx.plugin()`），和父插件自己的 effect 同时开始清理。**只有在同一个 `ctx.effect(function* …)` 里 yield 出来的多个 disposer，才会按逆序串行执行。** `await fiber.dispose()` 会等所有异步 disposer（包括子插件的）结束才 resolve。

### Cited Findings
- [实测] 脚本 `EXP/e02-dispose-order.mjs`。父插件依次注册：A（异步 30ms）、子插件（子插件内有 E1 20ms、E2 5ms）、B（异步 10ms）、C（同步）、一个生成器 effect（yield y1/y2/y3，各 15ms）。命令 `node e02-dispose-order.mjs`，输出：
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
  - 启动顺序是逆序：generator → C → B → A。子插件的 E2、E1 虽然注册在 A 之后，启动却排在 A 之后，因为子 Fiber 的卸载多经过了几层 microtask。
  - 结束顺序完全由耗时决定（E2 9ms → B 14ms → …），证明兄弟之间是并发的。
  - 生成器内部严格串行：y3 结束后 y2 才开始，y2 结束后 y1 才开始。
  - `dispose()` 的同步部分立即返回，所有 disposer 都在后续 tick 中开始；`await dispose()` 在最后一个 disposer 结束（51ms）时才 resolve。
  - `getEffects()` 返回 effect 的 label 列表，`ctx.plugin()` 也作为一个 effect 出现在其中。
- [源码] `_unload()` 用 `await Promise.all(this._disposables.clear().map(async (dispose) => { … await runDisposable(dispose) … }))` 执行所有顶层 disposer。单个 effect 内部的 `dispose` 则用 `disposables.splice(0).reverse()` 加 `task.then(...)` 串成链 — [GH/vendor/cordis/src/fiber.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts)
- [文档] 教程原文："disposers start in reverse registration order, but multiple **async** disposers run concurrently. If teardown steps must run in sequence, keep them in one disposer and await them there." 另外还说 "`fiber.dispose()` resolves after all of the plugin's cleanup — including async disposers — has finished, and recursively unloads any child plugins it mounted." — [GH/docs/cordis-tutorial/02-lifecycle-and-effects.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/02-lifecycle-and-effects.md)
- [文档] 审计技能也写明："The current fiber unload starts sibling disposers concurrently." — [GH/.agents/skills/cordis-styling-nitpicking/SKILL.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md)

### Inferences
- "逆序"只对同步 disposer 有意义：同步 disposer 在启动时就执行完了，所以它们确实按逆序完成。只要 disposer 里有 `await`，注册顺序就不能保证完成顺序。
- 需要顺序的清理有两种写法：放进同一个 disposer 里依次 await，或者放进同一个生成器 effect，按"先获取的资源先 yield"的顺序 yield。

### Gaps
- 没有测 disposer 抛错的情况。[源码] `_unload` 用 try/catch 把每个 disposer 的错误交给 `ctx.logger.error`，不影响其他 disposer，这一点没有实测。

---

## Q3 "提供方先释放资源"的竞态：能否复现，怎么修

### Takeaway
[实测] **最直接的写法不会出问题。** 资源和 `ctx.provide` 是同一个提供方 Fiber 的两个兄弟 effect，消费方通过 `inject` 依赖它。无论卸载提供方、卸载共同的父插件，还是两者同时卸载，6 种组合都没丢写入。原因是 `_unload` 会先等依赖方卸载完。**能稳定复现竞态的是"资源的清理不经过 inject 依赖边"的 4 种形状：**
- V1：资源由父插件持有，服务由子插件发布；
- V2：同一插件里 flush 和 close 是两个兄弟 effect；
- V3：消费方用 `ctx.get()` 读服务，没有写 inject；
- V4：消费方有一个不归 Fiber 管的后台任务。
对应的修法分别是：用生成器 effect yield 精确的 disposer（V1）、合并成一个顺序 await 的 disposer（V2）、补上 inject（V3）、把后台任务包进 effect 并在 disposer 里等它完成（V4）。修复后全部通过。

### Cited Findings
- [实测] 基线，脚本 `EXP/e03-provider-race.mjs`，命令 `for m in naive fixed; do for s in provider parent both; do node e03-provider-race.mjs $m $s; done; done`。消费方的 disposer 先 `await sleep(5)`，再写 `FINAL`。naive 写法的输出（fixed 写法的三种情况结果相同）：
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
- [实测] 能复现的形状，脚本 `EXP/e03b-provider-race-variants.mjs`，命令 `node e03b-provider-race-variants.mjs <V>`：
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
  V3 用 `node e03b-provider-race-variants.mjs V3 awaitProvider` 运行（先等提供方 ACTIVE，再加载消费方）：
  ```
     5ms === V3: dispose provider
     8ms   db.close() begin
     9ms   db.close() done
    30ms   untracked consumer state=2 (2=ACTIVE) after provider gone; now dispose it
    30ms   writer: flushing final record...
    37ms   writer: ERROR db closed, lost write "FINAL"
  ```
  各形状的写法：
  - V1：父插件里 `ctx.effect(() => () => db.close())`，再 `ctx.plugin({ apply(c) { c.provide('db', db) } })`。子插件的 `_unload` 确实在等 writer，但父插件的 `db.close` 是 `ctx.plugin()` 的兄弟 effect，第 7ms 就开始关闭了。
  - V1fix：
    ```js
    ctx.effect(function* () {
      const db = openDb()
      yield ctx.effect(() => () => db.close(), 'db')
      yield ctx.plugin({ name: 'db-service', apply(c) { c.provide('db', db) } }).dispose
    }, 'composed')
    ```
    yield 的必须是 `ctx.plugin(...)` 返回的 Fiber 的 `.dispose`，也就是注册在父 Fiber 上的那个精确 disposer。[源码] effect 的 `collect` 会执行 `this._disposables.delete(dispose)`，把它从父 Fiber 的顶层列表里移走，所有权转给这个生成器。
  - V2fix：`ctx.effect(() => async () => { await flush(); await db.close() })`，合并成一个 disposer。
  - V3：消费方没有 inject，用 `ctx.get('db')` 取服务，不会被登记为 dependent。提供方关闭资源后，消费方仍然是 ACTIVE。
  - V4fix：`ctx.effect(() => { const task = run(); return () => task })`，disposer 返回正在运行的任务，卸载时会等它完成。
- [实测] 附带现象：V3 如果不先 `await` 提供方（`node e03b-provider-race-variants.mjs V3`），消费方 apply 执行时提供方还处于 LOADING，`ctx.get('db')` 直接返回 `undefined`，最后报 `writer: ERROR Cannot read properties of undefined (reading 'write')`。也就是说，不写 inject 时，能否拿到服务取决于加载时序。
- [实测] 审计技能声称的"workaround 管不到的地方"，我试了两种交错方式都**没能复现**。脚本 `EXP/e03c-enclosing-owner.mjs`：一种是消费方和 composed provider 是同一个父插件的兄弟子插件，然后卸载父插件；另一种是先开始卸载消费方，1ms 后再卸载父插件。两种都写入成功：
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
- [文档] 审计技能的 P0 规则 "cleanup dependency released before consumer completion"："Service withdrawal awaits dependent fibers inside its own disposer, while sibling resource closers can already run. Adding `inject`, reversing registration order, or retaining a service reference does not fix that race." 官方给出的 workaround 是："Use a function-style provider plugin with one `ctx.effect(function* () { ... })` … Publish the service last with `yield ctx.provide(name, service)` … Do not yield replacement wrappers around already registered disposers … Top-level plugin yields alone leave disposers as concurrent siblings." 它还说明了局限："Consumer disposal removes a fiber from the registry before its cleanup finishes, so service withdrawal can miss an already-disposing consumer. For root or enclosing-owner disposal … retain and await the actual consumer disposal tasks through an explicit owner." — [GH/.agents/skills/cordis-styling-nitpicking/SKILL.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md)
- [文档] defensive-patterns 中对应的规则："Dispose must reach quiescence, not just request it … resources and listeners needed for completion or final persistence must survive until consumers settle." — [GH/docs/defensive-patterns/general.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/defensive-patterns/general.md)

### Inferences
- 文章里可以这样表述："提供方先释放"的根因是**资源的释放挂在一条和 inject 无关的 disposer 上**。可能是父 Fiber 的兄弟 effect，也可能是同一 Fiber 的兄弟 effect，或者消费方根本没有登记为依赖方。只要资源关闭和服务撤回在同一 Fiber 上，Cordis 4.2.0-alpha.1 的 `_unload` 就会先等依赖方。我的最小基线实验与技能中"inject 不能修复"的说法并不矛盾：技能指的是 V1/V2 这类形状，在那些形状里补 inject 确实没有用（V1 的 writer 已经写了 inject）。
- 官方的生成器 workaround 在 V1 形状里有效（实测）。审计技能列出的残余风险（消费方先进入卸载、外层所有者卸载）在我的两种交错里没有复现。写文章时应该说明"技能声称存在，本实验未复现"，不要断言它一定会发生或一定不会发生。
- 审计技能要求"把消费方暂停在最后一次依赖使用之前，再开始卸载"作为回归测试。上面的 `await sleep(5)` 就是这个手法的简化版。

### Gaps
- 技能说"service withdrawal can miss an already-disposing consumer"，我没有找到能触发它的交错。可能需要 isolate、Service 类的 `[Service.init]` 生成器，或者 loader/HMR 路径，本次没有覆盖。
- 没有用 dsh 真实的 Service 类（`extends Service`）重复 V1/V2，只用了函数和对象形式的插件。

---

## Q4 `apply()` 同步抛错或异步 reject：最终状态、已注册的 effect 是否被清理、如何恢复

### Takeaway
[实测] 两种失败的结果一样：`LOADING → UNLOADING → FAILED`。**抛错之前注册的 effect（定时器、`ctx.on` 监听）都会被清理**：定时器在失败后 20ms 内 0 次触发，事件也收不到了。`await ctx.plugin(...)` 会 reject，错误同时交给 `ctx.logger.error`。FAILED 的 Fiber **用 `restart()` 无法恢复**（会再次 reject 同一个错误，而且根本不重跑 apply），**只能用 `update(config)` 恢复**，传同一份 config 也可以。

### Cited Findings
- [实测] 脚本 `EXP/e04-apply-fails.mjs`，命令 `node e04-apply-fails.mjs`。插件先 `ctx.effect(setInterval)`、`ctx.on('custom/ping')`，然后抛错（sync），或者 `await sleep(3)` 后抛错（async）：
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
  在 FAILED 状态下执行 `root.parallel('custom/ping')`，没有输出 "got ping"，说明监听器已经移除。恢复后同样的 ping 能收到。
- [实测] 脚本 `EXP/e04b-restart-failed.mjs`，命令 `node e04b-restart-failed.mjs`：
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
- [源码] `_setEpoch` 中的注释："a failed fiber only recovers through update(), which clears _error"；`update()` 里有 `fiber._error = undefined` — [GH/vendor/cordis/src/fiber.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts)
- [文档] 教程："FAILED — `apply` or config validation threw." — [GH/docs/cordis-tutorial/02-lifecycle-and-effects.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/02-lifecycle-and-effects.md)

### Inferences
- apply 失败相当于"回滚"：在 apply 中途抛错，不会留下半注册的状态，前提是资源都通过 effect 获取。**直接 `setInterval` 而没有包进 effect 的资源不在回滚范围内**（Q5 和 Q6 展示了这种泄漏）。
- 要实现"失败后重试"，必须调用 `update`。仅仅 `restart()` 会直接拿到上一次的错误。

### Gaps
- 没有测 Config schema 校验失败（属于另一篇的配置主题）。

---

## Q5 apply 中 `await` 期间插件被卸载，之后再注册 effect：会不会泄漏

### Takeaway
[实测] **`dispose()` 会等正在执行的 apply 结束。** 插件在 apply 的 `await` 期间停留在 LOADING，`uid` 已经被设成 `null`。await 之后再调 `ctx.effect()` 或 `ctx.on()`，会**同步抛出 `INACTIVE_EFFECT: cannot create effect on inactive context`**，effect 函数体不会执行，所以通过 effect 申请的资源不会泄漏。**但在 await 之后直接创建的原生资源（裸 `setInterval`）会泄漏**：卸载后 60ms 内又触发了 11 次。正确做法是把异步获取过程放进 `ctx.effect(async function* …)`：卸载会等连接建立完，再执行 yield 出来的关闭函数，最终连接数回到 0。

### Cited Findings
- [实测] 脚本 `EXP/e05-effect-after-await.mjs`，命令 `node e05-effect-after-await.mjs`：
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
  - `dispose()` 在第 11ms 调用，在第 28ms apply 结束之后才 resolve。
  - 对比：如果 apply 返回 Promise，插件在 await 期间一直是 LOADING（slow 的情况）；如果 apply 是同步的，只是 effect 异步，插件会立即进入 ACTIVE（good 在 92ms 就 ACTIVE 了，此时连接还没建立）。
- [源码] `effect()` 开头是 `this.assertActive(); if (this.state === FiberState.UNLOADING) throw new CordisError('INACTIVE_EFFECT')`。异步生成器在每次 `iter.next()` 之前会检查 `runner.epoch !== oldEpoch` — [GH/vendor/cordis/src/fiber.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts)
- [源码·真实插件] subagent 在 `[Service.init]` 里 await 了几次 `sessionProjections.register` 之后，用 `if (ctx.fiber.uid === null) return` 提前返回，再调用 `ctx.inject(...)` — [GH/packages/subagent/subagent/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/subagent/subagent/src/index.ts)（约 L216–L222）。全仓库 packages 的非测试源码里，`fiber.uid === null` 或 `fiber.uid !== null` 一共出现 23 次，分布在 17 个文件（我的 grep 统计）。
- [源码·真实插件] mcp-client 处理"apply 还在等待初始连接时插件被卸载"：注释写着 "Cordis announces unload before awaiting an unfinished apply(). Closing the transport here releases startup requests that are still awaiting a reply."，具体做法是 `ctx.on('internal/plugin', (fiber) => { if (fiber !== ctx.fiber || fiber.uid !== null) return; return dispose() }, { global: true })` — [GH/packages/mcp/mcp-client/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/mcp/mcp-client/src/index.ts)（约 L192–L205）

### Inferences
- 有三种可以写进文章的防护：
  1. 在 await 之后检查 `ctx.fiber.uid === null` 并提前返回（subagent 的做法）；
  2. 把异步获取放进 `ctx.effect(async function* …)`，让框架接管只获取了一半的资源；
  3. 监听自身 Fiber 的 `internal/plugin` 事件（此时 uid 为 null），主动中止正在等待的启动请求，避免 `dispose()` 被一个长时间挂起的 apply 拖住（mcp-client 的做法）。
- 只要 apply 里有 await，就必须确认 await 之后的每一个副作用都经过 ctx 的 API，或者先检查 uid。

### Gaps
- 没有测 apply 一直不结束（永久挂起）时 `dispose()` 是否也会永远挂起。按观察到的"dispose 会等 apply 结束"推断是会的，mcp-client 的注释也暗示了这一点，但我没有实测。

---

## Q6 定时器、子进程、文件监听、socket：用 ctx.effect 正确清理，不包会泄漏什么

### Takeaway
[实测] 不包进 effect 时，Fiber 已经 DISPOSED，但 `process.getActiveResourcesInfo()` 里的 9 个句柄（Timeout、ProcessWrap 加 3 个 PipeWrap、FSEventWrap、TCPServerWrap、2 个 TCPSocketWrap）一个都没少，子进程还在运行，进程 1 秒内无法自行退出。包进 effect 后，卸载结束时句柄清零，子进程已退出，进程正常退出（exit 0）。另外，**只调用 `kill()` 不等退出**，`dispose()` 1ms 就 resolve 了，子进程 307ms 后才真正退出；如果 await 退出，`dispose()` 会持续 307ms，resolve 时子进程已经不在了。

### Cited Findings
- [实测] 脚本 `EXP/e06-resources.mjs`，命令 `LAB_TMP=<lab>/tmp node e06-resources.mjs leaky; node e06-resources.mjs owned`：
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
  owned 版本中各资源的写法：
  - 定时器：`ctx.effect(() => { const h = setInterval(...); return () => clearInterval(h) })`。
  - 子进程：在 effect 里 `spawn`，**在 kill 之前**先 `const exited = once(child, 'exit')`，disposer 是 `async () => { if (未退出) child.kill('SIGTERM'); await exited }`。
  - 文件监听：`ctx.effect(() => { const w = fs.watch(dir); return () => w.close() })`。
  - server 和 client：放在一个 `ctx.effect(async function* …)` 里。先 yield "销毁全部已接受的 socket 并等待 `server.close` 回调"，listen 完成后再 yield `client.destroy`。逆序清理时会先关客户端，再关服务端。外层的 `await ctx.effect(...)` 会等异步 setup 完成。
- [实测] 脚本 `EXP/e06b-kill-no-await.mjs`。子进程用 `trap "sleep 0.3; exit 0" TERM` 模拟一个收到 SIGTERM 后需要 300ms 收尾的进程：
  ```
   109ms   mode=noawait: dispose() took 1ms; child alive at dispose resolve? true
   422ms   child actually exited 307ms after dispose() started
   417ms   mode=await: dispose() took 307ms; child alive at dispose resolve? false
   418ms   child actually exited 307ms after dispose() started
  ```
- [文档] defensive-patterns："Await child exit after killing or aborting … The judged condition is whether teardown awaits actual quiescence or only requests it." — [GH/docs/defensive-patterns/general.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/defensive-patterns/general.md)；审计技能 P0 "unreversed side effect"："Aborting or killing without awaiting settlement is a violation when cleanup can return before owned work stops." — [GH/.agents/skills/cordis-styling-nitpicking/SKILL.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md)
- [源码·真实插件] 仓库里大多数长期运行的定时器并不是每个单独包一层 effect，而是**挂在一个拥有者对象上，由这个对象的 `close()`/`dispose()` 统一清理**：
  - `ssh` 的 `this.heartbeat = setInterval(...)` 加 `.unref()` — [GH/packages/ssh/ssh/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/ssh/ssh/src/index.ts)
  - gateway 的 `heartbeatTimer` — [GH/packages/api/gateway/src/stream-server.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/api/gateway/src/stream-server.ts)
  - claude-code-mods 的 `every()`：用 `this.active` 集合跟踪，`close()` "Cancel every scheduled timer, refuse new ones, and settle once the callbacks already running have finished" — [GH/packages/experimental/claude-code-mods/src/engine.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/experimental/claude-code-mods/src/engine.ts)
  - plugin-market 在 `try/finally` 里执行 `clearInterval` — [GH/packages/boot/plugin-market/src/repository-cache.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/boot/plugin-market/src/repository-cache.ts)

  我的 grep 统计：packages 非测试源码中有 13 处 `setInterval(`，分布在 12 个文件；`.unref()` 出现 29 次，分布在 22 个文件。
- [源码·真实插件] skill-filesystem 的文件监听（chokidar）由 `FileSystemSkillProvider` 管理，它有 `lifecycle = new AbortController()` 和 `async dispose()`（先 abort，再逐个 `await this.closeWatcher(...)`）。插件入口把它接进 Fiber 的方式是 `ctx.effect(function* () { yield async () => { await provider.dispose() } }, 'skill-filesystem watcher')` — [GH/packages/skill/skill-filesystem/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/skill/skill-filesystem/src/index.ts)（L140–L142，L298，L350–L360）

### Inferences
- `.unref()` 只能让泄漏的定时器不再阻止进程退出，并不会让它停止运行。dsh 里 `.unref()` 和拥有者的 close 是同时使用的，不能互相替代。文章可以点出：e06 leaky 版本即使 unref 了定时器，它仍然会在已卸载的插件里继续触发（参照 e05 的 11 次 tick）。
- 实践中常见的模式是"一个资源拥有者对象，加一个 effect 把它的 `dispose()` 接到 Fiber 上"，而不是每个原生资源各写一个 effect。

### Gaps
- 没有在 Windows/macOS 上测（`fs.watch` 的句柄类型和子进程信号语义会不同）。没有测 dsh 的 `subprocess-local` 运行器的真实清理路径。

---

## Q7 配置更新：重新加载还是原地更新

### Takeaway
[实测] 默认情况下，`fiber.update(newConfig)` 等于**完整重新加载**：`ACTIVE → UNLOADING → LOADING → ACTIVE`，旧 effect 被清理，apply 重跑。插件在**自己的 ctx** 上监听 `internal/update`、并且不调用第二个参数（restart/next）时，就是**原地更新**：不卸载，apply 不重跑，`fiber.config` 和 `effectiveConfig` 都换成新值。在监听器里 `return restart()` 又会回到完整重载。`fiber.restart()` 总是重载。dsh 自己的插件代码（packages/）里**没有一处**监听 `internal/update`；原地更新由 vendor 的 loader、group、include 和 schema 的 `.volatile()` 字段负责。

### Cited Findings
- [实测] 脚本 `EXP/e07-config-update.mjs`，命令 `node e07-config-update.mjs`：
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
  监听器写法：`ctx.on('internal/update', (next, restart) => { if (需要重载) return restart(); current = next })`。
- [源码] `events.ts` 对 `internal/update` 有特殊处理：非 global 的监听器会注册到 `this.fiber._hooks['internal/update']`，也就是只对自己的 Fiber 生效，并且本身也是一个 effect（label `ctx.on("internal/update")`）。`update()` 的注释写着 "a listener may veto the restart, in which case there is nothing to await" — [GH/vendor/cordis/src/events.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/events.ts)，[GH/vendor/cordis/src/fiber.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts)
- [文档] vendor/cordis README："`internal/update(config, next)` either retains the instance or awaits the restart returned by `next()`." — [GH/vendor/cordis/README.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/README.md)
- [源码] `internal/update` 的使用者只出现在 vendor：`vendor/loader/src/index.ts:143`、`vendor/loader/src/config/group.ts:120`、`vendor/include/src/index.ts:284`。对 packages 非测试源码（2819 个 .ts 文件）执行 grep `'internal/update'`，结果为 0。
- [文档] volatile 路径（另一篇已经详细讲过，这里只引用一句）："A volatile-only change … Loader commits the new values into the running references and emits one instance-local `loader/volatile-update` … bypassing the target plugin's `internal/update` … Direct `fiber.update()`, code replacement, and dependency replacement retain their lifecycle" — [GH/docs/cordis-tutorial/05-config.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/05-config.md)

### Inferences
- 在 dsh 插件里，"不重启就应用新配置"的推荐做法是 schema 的 `.volatile()` 字段（由 loader 实现），而不是插件自己去拦 `internal/update`。直接调用 `fiber.update()` 时不会走 volatile 的捷径，见上面 05-config 的引文。
- 对生命周期这个主题来说，"重新加载"的意思是：所有 effect 都会被清理一次，apply 重跑一次。所以配置变更也会触发 Q2/Q3 讨论的那些清理顺序问题。

### Gaps
- 没有通过 loader 和 cordis.yml 实测 volatile 更新（属于另一篇的范围，也需要 loader 包）。

---

## Q8 可选依赖：`ctx.inject()`

### Takeaway
[实测] 主插件不在 `inject` 里声明 `metrics`，也能正常 ACTIVE。在 apply 里写 `ctx.inject(['metrics'], cb)`，会创建一个**子 Fiber**。服务出现时，这个子 Fiber 从 `PENDING` 进入 `ACTIVE`；服务消失时回到 `PENDING`，自己的 effect 被清理，主插件全程保持 ACTIVE。主插件卸载时，子 Fiber 从 `PENDING → UNLOADING → DISPOSED`。没有声明 inject 时，`ctx.get('metrics')` 返回 `undefined`，而直接读属性 `ctx.metrics` 会抛错。

### Cited Findings
- [实测] 脚本 `EXP/e08-optional-inject.mjs`，命令 `node e08-optional-inject.mjs`：
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
- [源码] `inject(inject, callback) { return this.plugin({ inject, apply: callback, name: callback.name }) }`，JSDoc 说明："the callback is unloaded and re-run whenever a required service changes" — [GH/vendor/cordis/src/registry.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/registry.ts)
- [源码·真实插件] packages 非测试源码中有 99 处 `ctx.inject([`，分布在 88 个文件（grep 统计）。例子：
  - subagent 在 `[Service.init]` 里写 `ctx.inject(['agents'], (childCtx) => { const manager = new SubagentManager(childCtx, …); this.manager = manager; childCtx.effect(() => () => { if (this.manager === manager) this.manager = undefined }, 'subagents.managerBinding()') })`。这段把"有 agents 服务时才存在的 manager"绑定到子 Fiber 的生命周期上，并且清理时用身份比较，防止误清掉新的绑定 — [GH/packages/subagent/subagent/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/subagent/subagent/src/index.ts)（L222–L232）
  - mcp-client 写 `ctx.inject(['mcpResources'], …)` 和 `ctx.inject(['systemPrompt'], …)` — [GH/packages/mcp/mcp-client/src/server-context.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/mcp/mcp-client/src/server-context.ts)（L37–L40）
- [实测] Q3 的 V3 说明：用 `ctx.get()` 代替 inject 得到的可选依赖**不会被登记为 dependent**，提供方卸载时不会等它，而且能不能拿到服务取决于加载时序。

### Inferences
- 文章可以用 Q3 的 V3 和这里的实验做对比：可选依赖应该写成 `ctx.inject` 子 Fiber，而不是 `ctx.get` 加判空。前者让"依赖出现或消失"这件事拥有自己的生命周期，后者在清理时没有任何保障。
- 子 Fiber 回调里如果要把对象写到外层实例的字段上，清理时也要做身份比较，参照 subagent 的 `if (this.manager === manager)`。

### Gaps
- 没有测带 intercept 配置的对象形式 inject（`{ name: config }`）。

---

## Q9 真实插件的生命周期模式，以及审计规则：哪些遵守了，哪些有违反迹象

### Takeaway
[源码] 本次查看的 5 个包（web、spill-local、skill-filesystem、subagent、mcp）都用了审计技能推荐的写法：资源先有一个拥有者，再通过 `ctx.effect(function* …)` yield 出能被 await 的 disposer。registry 的 `register()` 用 effect 实现，并返回 disposer。仓库的 `verify-cordis-root` 门禁**以基线的方式保留了**已有的根上下文读取（11 个 `src/` 文件，其中有 mcp-client）。mcp-client 里还有一个模块级 `WeakMap`，按审计技能的字面规则属于疑似单例。这两点都只是"规则与现状的差异"，不是我实际复现出来的缺陷。

### Cited Findings
- [文档] 规则来源：
  - AGENTS.md："**Registrations are effects**: every contribution goes through `ctx.effect()` / `ctx.on()`; a registry's `register()` returns the disposer" — [GH/AGENTS.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/AGENTS.md)
  - 审计技能的三条 P0：singleton pattern、unreversed side effect、cleanup dependency released before consumer completion。其中单例规则写明 "Weak keys, size bounds, generation checks, and behavior-preserving memoization do not give a module-level cache Cordis ownership." — [GH/.agents/skills/cordis-styling-nitpicking/SKILL.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md)
  - defensive-patterns 的 "Plugins do not read the Cordis root context"（由 `pnpm run verify-cordis-root` 门禁执行）、"Shared state has one owner"、"Dispose must reach quiescence" — [GH/docs/defensive-patterns/general.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/defensive-patterns/general.md)
- [源码] **spill-local**：启动时的清扫"由 Fiber 拥有，但不阻塞激活"。注释原文："The generator body runs at activation but does NOT await the sweep — it launches it and yields an async disposer that awaits the SAME promise, so service availability is never delayed yet a fiber unload reaches quiescence"。代码是 `ctx.effect(function* () { this.cleanup = this.runCleanup(warn); yield async () => { await this.cleanup } }.bind(this), 'spill-local cleanup sweep')` — [GH/packages/spill/spill-local/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/spill/spill-local/src/index.ts)（L91–L102）。这和我在 Q3 V4fix 里用的形状一样（disposer 返回正在运行的任务）。
- [源码] **skill-filesystem**：`ctx.skills.registerProvider(...)` 是 registry 注册（属于 effect）；watcher 的生命周期由 provider 对象拥有，并用 `yield async () => { await provider.dispose() }` 接进 Fiber。`ctx.on('fs/observed', …)` 自动撤销 — [GH/packages/skill/skill-filesystem/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/skill/skill-filesystem/src/index.ts)（L134–L148）
- [源码] **web**：`registerProvider` 用 `this.ctx.effect(function* () { store.set(...); emitChanged(); yield () => { store.delete(...); emitChanged() } }, 'web.registerProvider()')`，然后 `return () => void dispose()`。注释写着 "ctx.effect's disposer returns Promise<void>; our disposer API is synchronous fire-and-forget"。因为它是服务方法，`this.ctx` 是调用方的上下文（审计技能 workflow 第 2 步："in a traceable service method, `this.ctx` is the caller's context"），所以这个注册属于调用方插件 — [GH/packages/web/web/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/web/web/src/index.ts)（L150–L165）
- [源码] **subagent**：
  - `SubagentManager` 写的是 `ctx.effect(function* () { yield scope.dispose; yield () => this.drain() }.bind(this), 'subagents.manager()')`。逆序执行时会先 `drain()` 掉正在运行的子 agent，再 `scope.dispose`，也就是用 yield 顺序表达"先排空使用方，再释放作用域" — [GH/packages/subagent/subagent/src/manager.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/subagent/subagent/src/manager.ts)（L183–L186）
  - `registerProvider` 先 yield 回滚函数，再 `emit('subagent/provider-added')`，注释写着 "A throwing added-listener unwinds the yielded rollback" — [GH/packages/subagent/subagent/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/subagent/subagent/src/index.ts)（L544–L557）
- [源码] **mcp-manager**：`* [Service.init]()` 是一个生成器，第一个 yield 是 `async () => { this.stop(); await this.tail; for (...) await this.unmount(name, record) }`（先停止接收新请求，再排空队列，最后逐个卸载）。`register()` 用 `this.ctx.effect(() => { if (this.stopped) throw …; …; return () => { …; if (!this.stopped) return this.entriesChanged(...) } }, 'mcpManager.register()')` — [GH/packages/mcp/mcp-manager/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/mcp/mcp-manager/src/index.ts)（L182–L188，L244–L259）
- [源码] **mcp-client**（与规则有出入的地方）：
  - 模块级的 `const activeServerNames = new WeakMap<object, Set<string>>()`（L57）。effect 里执行 `const owner = scopeOf(ctx) ?? ctx[Context.root]`（L167），disposer 里执行 `names.delete(...)` — [GH/packages/mcp/mcp-client/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/mcp/mcp-client/src/index.ts)
  - `scripts/cordis-root.baseline.json` 一共 27 个条目，其中 11 个是 `src/` 文件：config-editor、hmr、web-app、auto-review、inspector、authentication-token、**mcp-client**、agent-preset-registry、sdk/server、settings、typert/protocol — [GH/scripts/cordis-root.baseline.json](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/scripts/cordis-root.baseline.json)
  - 对应的过程笔记说明了为什么先冻结、再逐步替换："Replace every existing read before enforcing the rule. Rejected because several replacements need new services … enforcing first stops new reads while those designs are reviewed separately." — [GH/.agents/notes/implemented/process/2026-11-13-no-new-cordis-root-reads.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/notes/implemented/process/2026-11-13-no-new-cordis-root-reads.md)
  - 另一个包（web-app，同样在基线中）里也有一个模块级的 `const ANNOUNCED_ROOTS = new WeakSet<Context>()`，在 `packages/bundle/web-app/src/index.ts` L49 — [GH/packages/bundle/web-app/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/bundle/web-app/src/index.ts)
- [源码] 规模统计（grep，范围是 packages 下 `*/src/*.ts`，排除 test、spec，共 2819 个文件）：
  - `.effect(`：662 处 / 326 个文件；
  - 生成器 effect（`.effect(function* ` 或 `.effect(async function* `）：55 处 / 40 个文件；
  - `ctx.inject([`：99 处 / 88 个文件；
  - `'internal/update'`：0 处。

### Inferences
- 真实插件里反复出现的写法可以归纳成 4 条，都可以在文章里用前面的实验佐证：
  1. "资源拥有者对象加一个 yield 出来的异步 disposer"：skill-filesystem、spill-local，对应 Q6；
  2. "registry 注册写成生成器 effect：先 set，yield 回滚，最后 emit"：web、subagent；
  3. "用 yield 顺序表达先排空、再释放"：subagent manager、mcp-manager，对应 Q2 和 Q3 V1fix；
  4. "await 之后检查 `fiber.uid`，或者监听自己的 `internal/plugin` 来中止启动"：subagent、mcp-client，对应 Q5。
- mcp-client 的 `activeServerNames` 符合技能对单例的字面定义（模块级、用弱引用做键也不算有所有权）。这是我对照规则文字得出的判断，**不是**官方审计结论，也没有复现出可观察到的缺陷。它的 effect 有对称的 add/delete，基本行为是可以回滚的。
- 根上下文读取的现状是"新增会被门禁拦下，已有的冻结在基线里"。文章应该把它说成"规则加存量例外"，不要说成"真实插件违反了规则"。

### Gaps
- 没有对这 5 个包做完整的 P0 审计（审计技能要求逐条追踪 await 链和失败交错），只做了模式层面的阅读。
- 没有运行 dsh 自带的 `scripts/cordis-core-lifecycle.spec.ts` 等 vitest 用例（需要完整的 `pnpm install`）。我的所有结论都来自独立安装的 npm 包 `@deepseek-ai/cordis@4.2.0-alpha.1`，没有验证 vendor 源码和 npm 发布产物是否逐字节一致，只核对了版本号相同。
