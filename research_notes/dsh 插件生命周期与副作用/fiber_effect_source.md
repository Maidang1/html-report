# Cordis plugin lifecycle in dsh: Fiber state machine, `ctx.effect`, disposal order (source-code mechanics)

Snapshot: deepseek-harness `main` @ `4b399c39a6d1479cd84ca591b4d7c88e24d38a96` (committed 2026-11-22 16:50 +0800), shallow clone at `/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/66d83128-a37c-4163-ae6b-f001409e9960/scratchpad/dsh-lifecycle/deepseek-harness`. Vendored Cordis is `@deepseek-ai/cordis` **4.2.0-alpha.1** ([package.json](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/package.json#L4)); on npm, `latest` = 4.1.0 and `next` = 4.2.0-alpha.1 (`npm view @deepseek-ai/cordis`, run 2026-11-23). Timer plugin: `@deepseek-ai/cordis-plugin-timer` 1.3.0-alpha.1 (vendor/timer).

URL prefix used below: `B = https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96`. All line numbers are for that commit. Source file sizes: fiber.ts 842, reflect.ts 458, events.ts 375, registry.ts 337, utils.ts 297, context.ts 152, service.ts 125 lines.

Overlap note: the existing article `deepseek/dsh_services_events_config_plugin.md` already covers Impl/provide/notify/check, `getTraceable` caller tracking for `ctx.tools.register`, `ctx.isolate`, waterfall veto semantics, config validation timing, volatile fields and loader `id` diffing. Below, those topics appear only where the lifecycle mechanics need them.

## 1. Fiber states, transitions, epoch/revision, emitted events

### Takeaway
There are six states in a `const enum`: PENDING=0, LOADING=1, ACTIVE=2, FAILED=3, DISPOSED=4, UNLOADING=5. The state is derived from three facts: `uid === null` gives DISPOSED, `_error` set gives FAILED, `epoch !== INACTIVE` gives ACTIVE, and anything else is PENDING. LOADING and UNLOADING are set explicitly while a transition (`inertia`) is in flight. The "epoch" is a string `"<revision>:<impl uid>:<impl uid>…"`. Any change to it (restart, a different provider) drives unload and then reload. Every state change emits `internal/status(fiber, oldState)`.

### Cited Findings
- Enum definition and order: `PENDING, LOADING, ACTIVE, FAILED, DISPOSED, UNLOADING`, with no explicit initialisers, so the values are 0–5 in that order. DISPOSED (4) comes *before* UNLOADING (5) numerically. The JSDoc gives the meanings: PENDING means waiting for required services, LOADING means the callback is running, ACTIVE means loaded and providing, FAILED means the callback or config threw, UNLOADING means disposers are running, and DISPOSED means removed and unable to restart. — [fiber.ts L169-L184](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L169-L184)
- A new fiber starts at `state = FiberState.PENDING`. The root fiber (runtime `null`) is constructed directly as ACTIVE with epoch `'0'`, and its `dispose` is `restart()`. — [fiber.ts L222](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L222), [L348-L360](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L348-L360)
- State derivation: `_getState()` returns DISPOSED if `uid === null`, then FAILED if `_error`, then ACTIVE if `_runner.epoch !== INACTIVE`, else PENDING. `INACTIVE` is the sentinel string `'__INACTIVE__'`. — [fiber.ts L602-L607](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L602-L607), [L206](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L206)
- `_updateState(cb)` sets `state = cb() ?? _getState()`. If the state changed, it emits `internal/status(this, oldState)` ("FIXME internal/fiber-info"). Only when the move is into or out of ACTIVE does it also `reflect.notify()` every service impl owned by this fiber, so consumers re-check. — [fiber.ts L609-L623](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L609-L623); event typed as `'internal/status'(fiber: Fiber, oldValue: FiberState)` — [events.ts L343](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/events.ts#L343)
- How the epoch is computed: `_refresh()` starts from `` `${this._revision}` `` and appends `':' + impl.uid` for each name in `inject`. If any injected name has no usable impl in `_store`, the epoch becomes `INACTIVE`. — [fiber.ts L639-L650](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L639-L650)
- Dependency readiness is `_checkImpl(name)`. It uses `reflect._getImpl(name, strict=true)`, which accepts an impl only if its provider fiber is ACTIVE, then calls an optional `check`. A throwing or false `check` counts as missing. — [fiber.ts L625-L637](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L625-L637), [reflect.ts L273-L279](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L273-L279)
- Transition driver `_setEpoch(epoch)`:
  - It does nothing if the epoch is unchanged.
  - It **does nothing if `_error` is set** ("a failed fiber only recovers through update()").
  - Otherwise it stores the target epoch. If a transition is already in flight (`inertia`), it returns, and the in-flight transition reconciles later.
  - Otherwise, going from INACTIVE to a real epoch starts `_reload()` (state LOADING). Every other change starts `_unload()` (state UNLOADING). This includes real→INACTIVE and real→different real, so a restart or provider swap unloads first.
  — [fiber.ts L652-L668](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L652-L668)
- `_reload()`:
  - It snapshots `committed = {...this._store}` and adds itself to `impl.dependents` of every impl it uses that belongs to another fiber.
  - It awaits one microtask, then runs the plugin body (`_execute(this._runner)`) only if the epoch is still unchanged.
  - On a throw or rejection it logs the error. If the epoch is still the same, it sets `_error = reason` and `epoch = INACTIVE`.
  - It finishes with `_updateState`. If the epoch is unchanged, inertia clears and the state is derived (ACTIVE). Otherwise (superseded, or failed and now INACTIVE) it chains into `_unload()` and sets UNLOADING.
  — [fiber.ts L703-L732](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L703-L732)
- `_unload()` ends with `_updateState`. If the target epoch is INACTIVE, inertia clears and the state is derived (PENDING, FAILED, or DISPOSED). Otherwise it chains `_reload()` and sets LOADING. — [fiber.ts L759-L767](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L759-L767)
- The plugin body is `_runner.execute`. It **resolves config first** through the `internal/config` waterfall, which does schema validation. Then it calls `new runtime.callback(ctx, config)` and runs `initHooks` and `[symbols.init]()` for class plugins, or calls `runtime.callback(ctx, config)` for function and object plugins. Its return value is processed as an `Effect`, so a plugin can return a disposer, a promise, or a (async) generator. — [fiber.ts L272-L290](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L272-L290), [L384-L428](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L384-L428)
- `restart()` does `_revision++` then `_refresh()`, so the epoch string changes and goes through unload and then reload. It awaits `fiber.await()`. — [fiber.ts L790-L796](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L790-L796)
- `fiber.await()` loops `while (this.inertia) await this.inertia` and then rethrows `_error`. `ctx.plugin()` returns a wrapped fiber whose `then` is `fiber.await()`, so `await ctx.plugin(p)` rejects with the startup error. — [fiber.ts L776-L782](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L776-L782), [registry.ts L330-L335](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/registry.ts#L330-L335)
- Construction order in `new Fiber` (child):
  1. Register the disposer on the parent as `parent.fiber.effect(..., 'ctx.plugin()')`.
  2. Emit `internal/plugin(fiber)` while the fiber is still PENDING. A sync throw from an observer disposes the fiber and rethrows.
  3. Only then call `_checkImpl` for each inject name and `_refresh()`. This is skipped if the fiber was already disposed or the parent is UNLOADING.
  — [fiber.ts L292-L347](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L292-L347)
- Dispose path (the `ctx.plugin()` effect disposer):
  1. Set `uid = null`.
  2. Emit `internal/plugin` again, with each observer error contained and logged.
  3. Remove the fiber from `runtime.fibers`, deleting the runtime when it was the last fiber.
  4. `_setEpoch(INACTIVE)`. If the fiber was PENDING (no inertia was started), explicitly set UNLOADING and run `_unload()` so pre-activation effects are drained.
  5. Await inertia to quiescence.
  6. `_updateState(() => {})`, which derives DISPOSED.
  — [fiber.ts L292-L325](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L292-L325), [L149-L167](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L149-L167)
- `assertActive()` throws `CordisError('INACTIVE_EFFECT')` ("cannot create effect on inactive context") when `uid === null`. — [fiber.ts L201-L204](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L201-L204), [L379-L382](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L379-L382)
- Lifecycle events: `internal/plugin(fiber)` fires at creation and at disposal, `internal/status(fiber, oldValue)` on each state change, `internal/service(name, value)` from `reflect.notify`, and also `internal/update`, `internal/commit`, `internal/config`. — [events.ts L338-L374](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/events.ts#L338-L374), [reflect.ts L371-L375](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L371-L375)
- The official tutorial's diagram is `PENDING → LOADING → ACTIVE → UNLOADING → DISPOSED` with `LOADING ↘ FAILED`. — [02-lifecycle-and-effects.md L68-L80](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/02-lifecycle-and-effects.md#L68-L80)

### Inferences
- The tutorial diagram is a simplification. From the source, the transitions are:
  - Required impl lost while ACTIVE: ACTIVE → UNLOADING → **PENDING**, not DISPOSED. The fiber stays registered and reloads when the service returns.
  - Restart or provider replacement: ACTIVE → UNLOADING → LOADING → ACTIVE.
  - `apply` throws: LOADING → UNLOADING → FAILED. The failed load *still unloads*, so effects registered before the throw are disposed. This follows from `_reload` setting `epoch = INACTIVE`, which differs from `oldEpoch`, which triggers `_unload`.
  - Disposing a PENDING fiber: PENDING → UNLOADING → DISPOSED.
  - FAILED → (PENDING/LOADING) only through `update()`, which clears `_error` and restarts.
  - Missing-to-available dependency changes are ignored while FAILED.
- Config validation runs inside the LOADING step, so a PENDING plugin's config error only shows up once its deps arrive. The existing article also notes this.
- Because the epoch embeds impl uids, replacing a provider with a new instance gives a new `impl.uid`, so every consumer restarts even when the service name is unchanged.
- Since `internal/status` fires through `this.context.emit`, an observer sees UNLOADING and LOADING as separate events during a restart. The second argument is the *old* state; the new state is read from `fiber.state`.
- `internal/plugin` fires twice per fiber life (create and dispose). Observers tell the two apart by `fiber.uid === null` or `fiber.state`.

### Gaps
- I did not run the transitions to capture actual `internal/status` sequences. That belongs to the experiments track.
- The `FIXME internal/fiber-info` comment suggests the event name may change. There is no roadmap evidence either way.

## 2. `ctx.effect` / `ctx.fiber.effect`: signature, sync/async, ownership, inactive fibers, labels

### Takeaway
`ctx.effect` is a mixin of `fiber.effect(execute, label = 'anonymous')`. `execute` runs **synchronously and immediately**. It may return a disposer, `null`/`undefined`, a Promise of a disposer, a sync generator, or an async generator of disposers. A wrapper disposer is pushed to the owning fiber's `_disposables` *before* `execute` runs. Calling `ctx.effect` throws `INACTIVE_EFFECT` once the fiber is disposed (`uid === null`) **or while it is UNLOADING**. Labels become `EffectMeta` trees visible through `fiber.getEffects()`.

### Cited Findings
- `ctx.effect` is installed as a mixin of `fiber.effect` (`this.mixin('fiber', ['runtime', 'effect'])`). The `Context` interface extends `Pick<Fiber,'effect'>`. — [reflect.ts L256](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L256), [fiber.ts L9-L14](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L9-L14)
- Overloads:
  - `effect(execute: () => SyncEffect, label?): Disposable<Promise<void>>`
  - `effect(execute: () => Effect, label?): AsyncDisposable<Promise<void>>`
  
  The implementation defaults `label = 'anonymous'`. — [fiber.ts L443-L446](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L443-L446)
- Accepted shapes are `Disposable | Iterable<Disposable> | Promise<Disposable> | AsyncIterable<Disposable>`. — [fiber.ts L113-L123](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L113-L123)
- `_execute` dispatch:
  - A function is collected.
  - A nullish result is a no-op.
  - A non-object throws `TypeError('Invalid effect')`.
  - A thenable gets `.then(safeCollect)`.
  - A sync iterator is drained synchronously, collecting each yielded value.
  - An async iterator is drained in an async loop that **stops early if `runner.epoch !== oldEpoch`**, meaning disposal happened mid-setup.
  - Yielding a non-function, non-nullish value throws `TypeError`.
  — [fiber.ts L384-L428](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L384-L428)
- Guards at entry: `assertActive()` (uid not null), then `if (this.state === FiberState.UNLOADING) throw new CordisError('INACTIVE_EFFECT')`. — [fiber.ts L447-L450](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L447-L450)
- Ownership registration: `removeWrapper = this._disposables.push(wrapper)` happens **before** `this._execute(runner)`. The comment says this is "visible to a reentrant owner unload before execute() runs any plugin code". — [fiber.ts L545-L550](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L545-L550)
- Ownership transfer for nested effects: the inner runner's `collect` pushes the disposer into the effect's local `disposables` and **deletes it from `fiber._disposables`**. If the disposer carries `symbols.effect` metadata (that is, it is another effect's wrapper), it is attached as a child in `meta.children`. — [fiber.ts L472-L484](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L472-L484)
- Disposer semantics:
  - The wrapper is single-shot: `if (!runner.epoch) return …`, then it sets `epoch = false`.
  - If it is called while `execute` is still running, it waits on a setup barrier.
  - If an async setup is in flight, it waits for `task` and *then* disposes (`disposeAfter`).
  - Otherwise it calls `dispose()`.
  — [fiber.ts L495-L542](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L495-L542)
- Internal `dispose()` takes all collected disposers with `disposables.splice(0).reverse()` and runs them **serially in reverse order**. Once one returns a thenable, the later ones are chained with `task.then(...)`, so async disposers inside one effect are awaited one after another. — [fiber.ts L452-L470](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L452-L470)
- Setup failure:
  - A sync throw from `execute` marks `setupFailed`, runs `finalizeDisposal(dispose)` (rolling back anything already collected, for example earlier generator yields), logs async cleanup errors, and **rethrows to the caller**.
  - An async setup rejection is caught (`task?.catch`), the collected disposers are disposed, and errors are logged through `ctx.logger.error`, which prevents an unhandled rejection.
  — [fiber.ts L551-L576](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L551-L576)
- Awaitable disposer: `wrapper.then` resolves, after the async setup `task` settles, to `disposeAsync` (a function). So `const stop = await ctx.effect(async () => …)` yields a disposer, not `void`. — [fiber.ts L578-L588](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L578-L588), [L94-L96](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L94-L96)
- `effectInertia` (a WeakMap) lets an owner's unload join a cleanup that another caller already started: `runDisposable` returns the in-flight promise. — [fiber.ts L140-L147](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L140-L147), [L543](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L543)
- `EffectMeta { label, children }`. `getEffects()` maps `fiber._disposables` to their `symbols.effect` meta, which lists only live, top-level effects of that fiber. — [fiber.ts L125-L131](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L125-L131), [L596-L600](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L596-L600)
- The official JSDoc says it "Throws `CordisError('INACTIVE_EFFECT')` if the fiber is already disposed" and mentions "run (in reverse order)". It does not mention the UNLOADING guard. — [fiber.ts L430-L442](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L430-L442); generated API page repeats it: [docs/cordis-api/fiber.md L8-L37](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-api/fiber.md#L8-L37)

### Inferences
- **Creating an effect inside a disposer, or in any callback that fires while the fiber is UNLOADING, throws.** This covers `ctx.on`, `ctx.plugin`, `ctx.timeout`, and `tools.register`, because they all go through `fiber.effect`.
- Effects *can* be created on a PENDING or FAILED fiber, since only `uid` and UNLOADING are checked. The constructor comment confirms that `internal/plugin` observers register effects on PENDING fibers. Effects added to a FAILED fiber (for example by a late async callback from the failed `apply`) would live until the next unload or dispose. This is a source-only inference; I did not test it.
- Async `apply` and dispose: dispose sets `uid = null` immediately, but `_reload` awaits the whole async `apply`. Any `ctx.on`/`ctx.effect` that `apply` reaches after its `await` throws `INACTIVE_EFFECT`. The rejection is then logged, but not recorded as FAILED because the epoch changed. Unload proper starts only after `apply` settles. This is a source inference.
- If you call `ctx.effect` inside another effect's `execute` but do **not** return or yield the inner disposer, the inner effect stays a separate sibling in `fiber._disposables`. Ownership moves to the outer effect only through return or yield (the `collect` → `_disposables.delete`).
- The `async function*` form re-checks `runner.epoch` between yields, so a disposal during setup stops further acquisition. A plain `async` effect cannot be interrupted, and its disposer waits for the promise to settle.

### Gaps
- No test was run on throwing inside an UNLOADING-state callback. The other researcher can confirm the exact error text in the logs.

## 3. Which ctx APIs are effects under the hood

### Takeaway
Nearly every registration API goes through `fiber.effect` with a recognisable label:

| API | Label |
|---|---|
| `ctx.on` / `ctx.once` | `ctx.on("name")` |
| `ctx.on('internal/update')` | `ctx.on("internal/update")` |
| `ctx.plugin` / `ctx.inject` (a child fiber) | `ctx.plugin()` |
| `ctx.provide` and `Service` constructors | `ctx.provide("name")` |
| `ctx.accessor` / `ctx.mixin` | `ctx.accessor(...)` / `ctx.mixin(...)` |
| Timer service (`ctx.timeout`, `ctx.interval`, `ctx.setTimeout`, `ctx.throttle`, `ctx.debounce`) | `ctx.timeout()` etc. |
| dsh `ctx.tools.register` | `tools.register()`, via `ScopedLayers.effect` |

Because of traceable proxies, a service method's `this.ctx.effect` lands on the **caller's** fiber.

### Cited Findings
- `ctx.on` calls `assertActive()`, wraps the listener with `reflect.bind`, lets `internal/listener` intercept, then calls `register()`. That does `this.ctx.fiber.effect(() => { hooks.push/unshift; return () => unregister })`. — [events.ts L267-L311](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/events.ts#L267-L311)
- `ctx.once` is `ctx.on` with a self-disposing wrapper. — [events.ts L321-L327](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/events.ts#L321-L327)
- `ctx.on('internal/update', …)` (non-global) is redirected to the per-fiber `fiber._hooks['internal/update']` list as `fiber.effect(() => hooks.push(listener), 'ctx.on("internal/update")')`. — [events.ts L139-L145](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/events.ts#L139-L145)
- `ctx.plugin` → `new Fiber` → `parent.fiber.effect(..., 'ctx.plugin()')`. `ctx.inject(deps, cb)` is literally `this.plugin({ inject, apply: cb, name: cb.name })`. — [fiber.ts L292](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L292), [registry.ts L300-L302](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/registry.ts#L300-L302)
- `ctx.provide(name, value, check)` is `this.ctx.fiber.effect(..., 'ctx.provide("name")')`. Its disposer is **async**:
  1. Remove the impl from the store and `notify`.
  2. `await` until `impl.dependents` is empty (`impl.release`), and yield at least once even when there are no dependents.
  3. Only then `provisions.delete(impl)`.
  — [reflect.ts L313-L349](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L313-L349)
- `Service` constructor calls `self.ctx.reflect.provide(name, self, this[symbols.check])`, so a `Service` subclass is an effect on the fiber that constructed it. — [service.ts L45-L62](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/service.ts#L45-L62)
- `accessor` and `mixin`: `mixin` is a generator effect that `yield`s each `self.accessor(...)` disposer, so the accessors become children of the mixin effect. — [reflect.ts L385-L430](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L385-L430)
- Timer service: `TimerService extends Service` and mixes `timeout, interval, throttle, debounce, setTimeout, setInterval` onto ctx. `setTimeout` and `setInterval` are `@deprecated` aliases of `timeout` and `interval`. Each uses `this.ctx.effect(() => { const timer = setTimeout(...); return () => clearTimeout(timer) }, 'ctx.timeout()')`. The promise form rejects with `Error('Context has been disposed')` on dispose. — [vendor/timer/src/index.ts L1-L54](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/timer/src/index.ts#L1-L54)
- Consumers must `inject: ['timer']` and use `ctx.timer`. Calls after disposal are inert. — [docs/upgrade-guide/v0.2.1-alpha.2/cordis-timer-wrappers/guide.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/upgrade-guide/v0.2.1-alpha.2/cordis-timer-wrappers/guide.md)
- dsh `ToolsService.register()` returns `this.layers.effect(this.ctx, layer => layer.tools.insert(...), { label: 'tools.register()' })`. — [packages/core/tools/src/index.ts L128-L153](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/core/tools/src/index.ts#L128-L153)
- `ScopedLayers.effect(ctx, action, opts)` calls `ctx.effect(function* () { …; yield () => { undo(); …; onChange() }; onChange() })`. It derives both scope visibility and effect ownership from the supplied ctx, and returns "the exact disposer returned by `ctx.effect()`". — [packages/core/scope/src/store.ts L219-L266](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/core/scope/src/store.ts#L219-L266)
- Traceable proxies hand the **use site** ctx to a service's `ctx` property; per the source comment, the use site "governs intercept, isolate and effects". — [utils.ts L170-L180](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/utils.ts#L170-L180) (detail already in the existing services article)
- Repo convention: "**Registrations are effects**: every contribution goes through `ctx.effect()` / `ctx.on()`; a registry's `register()` returns the disposer". — [AGENTS.md L135](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/AGENTS.md#L135)
- Tutorial list of built-in effects: `ctx.on`, `ctx.plugin`, service registrations, `ctx.tools.register`. — [02-lifecycle-and-effects.md L84-L92](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/02-lifecycle-and-effects.md#L84-L92)

### Inferences
- A `ctx.timeout(cb, ms)` disposer is consumed by itself when the timer fires (it calls `dispose()` before `cb()`), so finished timers don't accumulate in `_disposables`. This is from reading the source.
- Raw `setTimeout`/`setInterval` (Node globals) are *not* effects. They need wrapping in `ctx.effect` or replacing with `ctx.timeout`/`ctx.interval` (which requires `inject: ['timer']`). The tutorial's own example wraps raw timers in `ctx.effect`.

### Gaps
- I did not enumerate every dsh registry's `register()` to confirm each returns an effect disposer. The convention in AGENTS.md asserts it.

## 4. Disposal order

### Takeaway
There are two different orderings:

1. **Within one `ctx.effect`:** collected disposers run in reverse (LIFO) and **serially**, with each async disposer awaited before the next. A throwing disposer aborts the remaining ones.
2. **Across a fiber's top-level effects at unload:** `_disposables.clear()` returns them reversed, but `_unload` passes them to `Promise.all`. Each is started after a microtask, and errors are caught and logged individually. So they *start* in LIFO order but **async ones run concurrently**.

Child fibers are just parent effects, so they unload concurrently with the parent's siblings, and the parent's unload waits for them. Before a provider fiber runs any disposer, it waits until all fibers depending on its provisions have finished unloading. This consumer-first barrier applies only to provisions owned by *that* fiber.

### Cited Findings
- `DisposableList.clear()` returns `[...map.values()].reverse()`. Insertion order comes from a Map keyed by an incrementing serial number. — [utils.ts L5-L41](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/utils.ts#L5-L41)
- `_unload()`:
  - First, `while ([...this.provisions].some(impl => impl.dependents.size)) await new Promise(r => this._release = r)` ("Dependents … may still access its services during their own teardown, so the effects of this fiber are only disposed after every dependent has unloaded").
  - Then `await Promise.all(this._disposables.clear().map(async dispose => { try { await composeError(async info => { await Promise.resolve(); …; await runDisposable(dispose) }) } catch (reason) { this.ctx.logger.error(reason) } }))`.
  - Then for each committed impl it calls `impl.dependents.delete(this)`. If dependents become empty, it calls `impl.release?.()` and `impl.ctx.fiber._release?.()` to wake waiting providers.
  - Then `committed = undefined`.
  — [fiber.ts L734-L768](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L734-L768)
- Within-effect order is serial reverse: see `dispose()` in [fiber.ts L455-L470](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L455-L470).
- Consumers keep using a departing provider during their own teardown: `fiber.committed` is documented as "Required implementations retained through dependent teardown". `locate()` resolves a service through `fiber.committed[name]` even after the store entry is gone. — [fiber.ts L225-L226](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L225-L226), [reflect.ts L147-L166](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L147-L166)
- Provider leaving ACTIVE notifies its impls (`_updateState`, [fiber.ts L616-L622](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L616-L622)). Consumers `_checkImpl` → missing → `_refresh` → INACTIVE → `_unload`. Meanwhile the provider's `_unload` blocks on the dependents barrier.
- Child fiber disposal is an async effect on the parent. It sets `uid=null`, then awaits its own inertia to quiescence. — [fiber.ts L292-L325](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L292-L325)
- `RegistryService.delete(plugin)` disposes every fiber of a runtime and does not await them. — [registry.ts L258-L267](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/registry.ts#L258-L267)
- `Disposable` JSDoc: "Disposers run in reverse registration order when the owning fiber unloads; they may be async, in which case unloading awaits them." — [fiber.ts L98-L104](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L98-L104)
- The tutorial adds the caveat explicitly: "disposers start in reverse registration order, but multiple **async** disposers run concurrently. If teardown steps must run in sequence, keep them in one disposer and await them there." — [02-lifecycle-and-effects.md L94](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/02-lifecycle-and-effects.md#L94)
- The primer says the same: "If teardown order matters, keep the related work in one effect so disposal unwinds in the intended sequence." — [cordis-primer.md L45](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-primer.md#L45)
- The audit skill: "The current fiber unload starts sibling disposers concurrently. Service withdrawal awaits dependent fibers inside its own disposer, while sibling resource closers can already run. Adding `inject`, reversing registration order, or retaining a service reference does not fix that race." Also: "`ctx.effect(function* ...)` serializes the disposers it collects, but a wrapper around an independently registered effect does not transfer ownership." And: "Top-level plugin generator yields are not a general promise of serial sibling disposal." — [cordis-styling-nitpicking/SKILL.md L41-L43](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L41-L43)
- `fiber.dispose()` "resolves after all of the plugin's cleanup — including async disposers — has finished, and recursively unloads any child plugins it mounted." — [02-lifecycle-and-effects.md L66](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/02-lifecycle-and-effects.md#L66)

### Inferences
- **Error behaviour differs by level.** At fiber level each disposer is isolated, and one failure is logged without stopping siblings. Inside one composed effect, a synchronously throwing disposer escapes the `for` loop. The list was already `splice(0)`'d and `disposing=true`, so the remaining (earlier-registered) disposers in that effect are **skipped**. An async rejection likewise skips the rest of the `.then` chain. This comes from reading [fiber.ts L455-L470](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L455-L470) and is the reason the skill says "one cleanup error must not skip required abort, settlement, or resource release". It is worth confirming by experiment.
- **Top-level plugin generator.** `apply` may itself be `function*`, since `_execute` accepts iterables. Its yields are collected into `fiber._disposables`, which `_unload` runs concurrently. Only yields *inside* a `ctx.effect(function* …)` are serialised. This matches the skill's warning.
- **Provider vs consumer** has two levels:
  - (a) When the provider *fiber* unloads (dependency lost, restart, dispose), the `_unload` barrier waits for all consumers of its own provisions to finish unloading before any provider disposer runs.
  - (b) A `Service` mounted as a *child* plugin (`ctx.plugin(GreeterService)`) belongs to the child fiber. When the parent unloads, the parent's other sibling disposers (connections, files) run concurrently with that child, and the child's barrier does not protect them. This is the race the skill describes.
  - The skill's workaround puts the resources and `yield ctx.provide(...)` (last) in one `ctx.effect(function* …)`. Reverse serial disposal then withdraws the service first (awaiting dependents inside the provide disposer, [reflect.ts L340-L343](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L340-L343)) and only then closes the resources.
- The parent's `_unload` awaits `Promise.all`, and a child disposer awaits the child's full unload, so a parent never reaches PENDING/DISPOSED before its children are fully unloaded. Children start unloading at the same time as the parent's own disposers, though.

### Gaps
- The skill claims "Consumer disposal removes a fiber from the registry before its cleanup finishes, so service withdrawal can miss an already-disposing consumer." From source, `impl.dependents` is only cleared at the *end* of a consumer's `_unload`, which suggests the barrier still sees it. The miss probably concerns `notify` iterating `runtime.fibers` (from which the disposing fiber was removed) or the root-disposal case. I could not settle this from reading alone, so it needs an experiment.

## 5. Config updates: reload vs in-place update

### Takeaway
`fiber.update(config)` on a loaded fiber does four things:

1. Validates the candidate synchronously, throwing `ValidationError` before any commit.
2. Emits `internal/commit` for persistence unless `noSave` is set.
3. Clears `_error`.
4. Runs the `internal/update` waterfall, whose final `next` is `restart()`.

If no per-fiber `ctx.on('internal/update')` listener intercepts, the fiber restarts (UNLOADING → LOADING). A listener that does not call `next()` keeps the instance and handles the update in place; `effectiveConfig` is then published. A PENDING or FAILED fiber skips the waterfall and restarts directly.

### Cited Findings
- `update(config, noSave=false)` steps:
  1. `assertActive()`.
  2. `loaded = epoch !== INACTIVE`.
  3. `resolved = loaded ? resolveConfig(config) : undefined`. This is the `internal/config` waterfall plus the schema check, and it throws.
  4. `if (!noSave) emit('internal/commit', fiber, config)`.
  5. `fiber.config = config`, `_error = undefined`, `++_configRevision`.
  6. `result = loaded ? waterfall(fiber, 'internal/update', resolved, restart) : restart()`.
  7. `publish()` sets `_effectiveConfig = resolved` only if no restart happened and nothing else changed (uid, configRevision, epoch, committed).
  8. The returned promise is marked handled.
  — [fiber.ts L805-L841](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L805-L841)
- Per-fiber update chain: a global, prepended `internal/update` listener walks `this._hooks['internal/update']` in order, then calls the outer `next`, which is `restart`. — [events.ts L147-L154](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/events.ts#L147-L154); hooks registered as effects at [events.ts L139-L145](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/events.ts#L139-L145); typed `'internal/update'(this: Fiber, resolvedConfig, next)` at [events.ts L352](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/events.ts#L352)
- `internal/config` default handler: `fiber.runtime ? resolveConfig(fiber.runtime, config) : config`. `resolveConfig` rejects async Standard Schema validation (`'Async config validation is not supported'`) and throws `ValidationError` on issues. — [events.ts L156-L159](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/events.ts#L156-L159), [fiber.ts L80-L92](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L80-L92)
- `rawConfig` setter retains input without triggering an update. `effectiveConfig` is "the object passed to plugin activation or the latest successful update that retained the instance". — [fiber.ts L670-L688](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L670-L688)
- Repo rule: "**Waterfall listeners MUST call `next()`** to delegate; returning without it short-circuits the chain". — [AGENTS.md L138](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/AGENTS.md#L138)
- Loader-level volatile updates bypass `internal/update` and emit `loader/volatile-update`. Otherwise the loader goes through `fiber.update(config, true)`. — [05-config.md L70-L95](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/05-config.md#L70-L95) (already detailed in the existing article)

### Inferences
- For the lifecycle article: an `internal/update` listener that returns without `next()` is the only in-place reconfiguration path at the Cordis level. In that case no disposers run and `apply` is not re-run, so the plugin must reconfigure its own effects. The volatile path is the loader's equivalent.
- Because `ctx.on('internal/update')` is itself an effect, it disappears on unload and is re-registered by the next `apply`.

### Gaps
- None material for this track.

## 6. Optional dependencies

### Takeaway
This vendored Cordis has **no optional-inject syntax**. `Inject.resolve` turns array or object forms into `{ name: interceptConfig | null }`, and `_refresh` treats every key as required. Optional dependencies are expressed in two ways:

- (a) `ctx.get(name)`: a non-tracking probe that returns undefined when the provider is absent or not ACTIVE.
- (b) `ctx.inject(deps, cb)`: a nested child fiber that sits PENDING or ACTIVE independently of the parent, and is unloaded and reloaded with the dependency.

### Cited Findings
- `Inject.resolve` maps an array to `null`-valued keys and an object to `inject[name] ?? null`, following the prototype for decorated classes. — [registry.ts L63-L89](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/registry.ts#L63-L89)
- `_refresh` breaks to INACTIVE on the first missing key, with no optional flag. — [fiber.ts L639-L650](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L639-L650)
- `ctx.inject(deps, cb)` → `ctx.plugin({ inject, apply: cb, name: cb.name })`. The `@Inject(name)` method decorator uses `ctx.inject` from an `initHooks` entry, so the decorated method runs as its own child fiber. — [registry.ts L37-L60](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/registry.ts#L37-L60), [L300-L302](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/registry.ts#L300-L302)
- Accessing an undeclared service through `ctx.foo` from a plugin fiber throws `cannot get property "foo" without inject`. A declared-but-unavailable one throws `cannot get required service "foo" in inactive context`. — [reflect.ts L177-L206](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L177-L206)
- `ctx.get(name, strict=true)` returns the value only if the provider fiber is ACTIVE. — [reflect.ts L269-L279](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L269-L279)
- Tutorial: "`inject` is for hard requirements. For a capability the plugin can live without, skip `inject` and probe at the use site", using `ctx.get('greeter')`. — [03-services.md L80-L90](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/03-services.md#L80-L90)
- Tutorial: `inject` "is not a one-shot boot check… every dependent plugin is unloaded too, and loads again when the service returns." — [03-services.md L74-L78](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/03-services.md#L74-L78)

### Inferences
- `ctx.get` gives a snapshot with no lifecycle coupling. If the provider later disappears, nothing unloads the caller's resources tied to that service, and a retained reference may be stale. `ctx.inject(deps, block)` is the lifecycle-safe optional form. Its effects belong to the child fiber and are torn down when the dependency goes, while the parent keeps running.
- Readers coming from upstream Cordis docs may expect an `{ required: false }`-style optional inject. No such handling exists in `_refresh` at this commit, so I report it as absent. I did not check upstream Cordis.

### Gaps
- I did not check upstream cordisjs (non-vendored) for an optional-inject syntax to compare against.

## 7. Official guidance and the mechanism behind each rule

### Takeaway
The lifecycle rules come from four places: tutorial ch.2/3/6, the API doc, `defensive-patterns/general.md`, and the `cordis-styling-nitpicking` skill ("Cordis Ownership and Lifecycle Audit"), which `dsh-code-review` invokes. The audit has exactly three P0 rules: singleton state, unreversed side effect, and cleanup dependency released before consumer completion. Each maps to a concrete source mechanism.

### Cited Findings
- The audit skill's title is "Cordis Ownership and Lifecycle Audit". Its scope starts from exported plugin entrypoints and traces "module initialization, registered services, listeners, effects, disposers". It does not report defects in vendored code. — [SKILL.md L1-L8](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L1-L8)
- `dsh-code-review` step 4 applies that skill and checks its "three P0 rules: singleton state, unreversed effects, and cleanup dependencies released before consumers". — [dsh-code-review/SKILL.md L37](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/dsh-code-review/SKILL.md#L37)
- Workflow step 2: "in a traceable service method, `this.ctx` is the caller's context". Step 4: "reverse start order and dependency registration alone do not establish completion order". — [SKILL.md L17-L23](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L17-L23)
- **P0 singleton.** It flags process-wide mutable owners from module load, statics, global symbols, hidden caches, or `getInstance()`. "Weak keys, size bounds, generation checks… do not give a module-level cache Cordis ownership." The fix is a Cordis service plus dependencies. — [SKILL.md L25-L29](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L25-L29)
  - Mechanism: only state reachable from a fiber's `_disposables` or `provisions` is reclaimed by `_unload`. Module state outlives restart and HMR. This is my inference, consistent with [fiber.ts L734-L768](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L734-L768).
  - Related doc rules: "Shared state has one owner" and "A new active/current singleton is refused". — [general.md L87-L122](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/defensive-patterns/general.md#L87-L122)
- **P0 unreversed side effect.** It flags "listeners, registrations, timers, watchers, child fibers, processes, connections, temporary files, subscriptions, ambient patches, or asynchronous work" left live after disposal. "Aborting or killing without awaiting settlement is a violation." "Correctly owned `ctx.on()`, `ctx.plugin()`, and `ctx.effect()` registrations already provide reversal; callers need not retain their early disposers." — [SKILL.md L31-L35](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L31-L35)
  - Mechanism: the wrapper is pushed into `fiber._disposables` before `execute` ([fiber.ts L548](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L548)) and is drained by `_unload`.
- **P0 cleanup dependency released before consumer completion.** "Any service call or event delivery required to finish cleanup is a lifecycle dependency." It requires "stop admission, settle admitted consumers while their dependencies remain usable, then drain and release the resources". It also notes that "A listener can also belong to the consumer fiber and auto-unsubscribe alongside an operation's drain." — [SKILL.md L37-L43](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L37-L43)
  - Mechanism: concurrent `Promise.all` in `_unload` ([fiber.ts L742](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L742)) and the provide disposer's dependents await ([reflect.ts L340-L343](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L340-L343)).
- **Workaround: function plugin with yielded effects.** Put everything in one `ctx.effect(function* () {...})`:
  - `yield` the *exact* disposers returned by registrations.
  - `yield ctx.provide(name, service)` **last**.
  - Yield an admission gate's cleanup after that, if needed.
  
  Caveats: it "is not a complete enclosing-owner shutdown fix". "Do not yield replacement wrappers around already registered disposers." "Top-level plugin yields alone leave disposers as concurrent siblings." — [SKILL.md L45-L55](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L45-L55)
  - Mechanism: `collect` deletes the exact disposer identity from `fiber._disposables` ([fiber.ts L476-L478](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L476-L478)). A wrapper function has a different identity, so the original stays a concurrent sibling.
- Regression-test recipe: pause the consumer before its last dependency use, start provider disposal, verify resources stay usable, release, and assert the terminal result and that disposal stays pending. — [SKILL.md L59](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/.agents/skills/cordis-styling-nitpicking/SKILL.md#L59)
- defensive-patterns "Dispose must reach quiescence, not just request it": "Await child exit after killing or aborting… resources and listeners needed for completion or final persistence must survive until consumers settle." — [general.md L223-L227](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/defensive-patterns/general.md#L223-L227)
  - Mechanism: `fiber.dispose()` awaits inertia to quiescence ([fiber.ts L317-L319](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L317-L319)). A disposer that returns before its work stops breaks this guarantee.
- defensive-patterns "Plugins do not read the Cordis root context": a root read skips the `inject` check "because the root fiber has no runtime", and "a listener or accessor registered there is not removed when the plugin is disposed". This is enforced by the `verify-cordis-root` gate. — [general.md L124-L151](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/defensive-patterns/general.md#L124-L151)
  - Mechanism: the root fiber's effects are owned by the root fiber ([fiber.ts L348-L360](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L348-L360)), and the root bypasses the `internal/get` check ([reflect.ts L193-L195](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/reflect.ts#L193-L195)).
- defensive-patterns "Contain callback exceptions in the dispatcher". — [general.md L229-L242](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/defensive-patterns/general.md#L229-L242)
  - Cordis does this itself for dispose notifications (`emitPluginDisposed` catches and logs each observer, [fiber.ts L149-L167](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L149-L167)) and for each fiber-level disposer ([fiber.ts L742-L752](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/vendor/cordis/src/fiber.ts#L742-L752)).
- Tutorial ch.6: a plugin whose `inject` has no provider "waits forever, printing nothing… PENDING is a legitimate state". The tutorial shows diagnosis by iterating `ctx.registry.values()` → `runtime.fibers` and checking `fiber.state === FiberState.PENDING`. HMR = unload + load. — [06-composition-and-hmr.md L23-L109](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/06-composition-and-hmr.md#L23-L109)
- A PENDING fiber "does not keep Node's event loop alive", so a composition with nothing else running exits 0 silently. — [03-services.md L72](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/docs/cordis-tutorial/03-services.md#L72)

### Inferences
- Three statements in the official docs are imprecise against the source, and the rewrite should flag them:
  - The `Disposable` JSDoc says "reverse order… unloading awaits them", which hides concurrency. The tutorial line 94 and the skill correct it.
  - The `ctx.effect` JSDoc omits the UNLOADING guard.
  - The tutorial's state diagram omits ACTIVE→UNLOADING→PENDING, restart loops, and FAILED→restart through `update()`.

### Gaps
- I did not read the Chinese (`.zh.md`) versions to check whether the translations match. The English sources are canonical (the `.i18n.yaml` files indicate the zh versions are generated).
