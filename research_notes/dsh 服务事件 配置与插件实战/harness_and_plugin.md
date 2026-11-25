# 接进 harness：工具服务的实现，以及一个自写插件（Service + 事件 + 卸载清理）的实战

> 范围：dsh 仓库 commit `4b399c3`（`4b399c39 Merge pull request #8961 … release-dsh-0.2.3-alpha.2`），对应 Cordis tutorial 第 7 章《进入 harness》。所有 `path:line` 都相对 dsh 仓库根目录。代码块标题里的行号区间由 `lab-harness/scripts/assemble-notes.py` 用 `sed -n a,bp` 逐字拷出，不是手抄的。
>
> 运行环境：node v24.22.2。从 npm 安装（`npm install --ignore-scripts`）：`@deepseek-ai/dsh-tools`、`dsh-system-prompt`、`dsh-session-projection`、`dsh-tool-executor-native`、`dsh-llm`、`dsh-brand`，版本均为 `0.2.3-alpha.2`；`@deepseek-ai/cordis@4.2.0-alpha.1`、`schemastery@3.20.0-alpha.1`、`cordis-plugin-loader@1.2.0-alpha.1`、`cordis-plugin-include@1.2.0-alpha.1`、`cordis-plugin-logger-console@1.2.0-alpha.1`，与仓库 `vendor/*/package.json` 的版本相同。用 `cmp` 核对过，npm 包里的 `src/{index,pipeline,batch,schema,events,executor}.ts` 和仓库 `packages/core/tools/src/` 下的同名文件逐字节一致，所以**实验运行的是 4b399c3 的真实 harness 代码，不是模拟**。自写插件写成 `.ts`，靠 Node 24 的 type stripping 直接运行，**没有经过 `tsc` 类型检查**。实验文件都在会话 scratchpad 的 `harness-plugin/lab-harness/` 下（下文简称 `lab-harness/`）。输出里的长路径前缀已替换为 `<lab>`。
>
> 读者已经掌握的内容（Fiber 状态机、`ctx.effect`、逆序清理、提供方与使用方卸载时的竞态、生成器 effect 里最后 provide 的写法、`.volatile()`、`internal/update`）见 `deepseek/dsh_lifecycle_and_effects.md`，本文直接引用，不再重讲。greet 工具、权限门禁、tool-logger、bundle 的介绍见 `deepseek/dsh_plugin_guide.md`。

## 问题 1：`ctx.tools.register` 怎样把 disposer 挂到调用方插件上？`ctx.tools.execute` 一步步做了什么？

### Takeaway
`register()` 本身不操作 Fiber。Cordis 的 Service tracker 让方法里的 `this.ctx` 解析成**调用方**的 context，`register()` 再通过 `ScopedLayers.effect(this.ctx, …)` 调用 `ctx.effect()`，于是这次注册成了调用方 Fiber 上的一个 effect，标签是 `tools.register()`。`execute()` 自己几乎不做事：它把整批调用交给当前作用域选中的 **executor 插件**。真正的流水线在 `ToolExecutionPipeline` 里，由 `dsh-tool-executor-native` 驱动，顺序是：参数快照，`tools/pre-execute` waterfall，审批，`guard`，`tools/execute` waterfall 包着的工具主体（`defineTool` 在主体里校验参数），输出 schema 校验与 render，`tools/post-execute` waterfall，物化并冻结，最后 emit `tools/result`。

### Cited Findings

**(a) 调用方上下文（caller context）是怎么来的**

- `ToolsService` 是 `Service` 的子类，构造时调 `super(ctx, 'tools')`，并用 `declare module` 把 `tools` 合并进 `Context` 类型 — [packages/core/tools/src/index.ts:32-36, 99-118](packages/core/tools/src/index.ts)
- `Service` 的构造函数给实例装上 tracker `{ associate: name, property: 'ctx' }`，然后调用 `reflect.provide(name, self)` — [vendor/cordis/src/service.ts:45-62](vendor/cordis/src/service.ts)

```ts title="vendor/cordis/src/service.ts:45-62"
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

- 通过 `ctx.tools` 取服务时，`reflect.get()` 返回 `getTraceable(this.ctx, value)` — [vendor/cordis/src/reflect.ts:269-271](vendor/cordis/src/reflect.ts)。返回的代理在读取 `tracker.property`（也就是 `ctx`）时给出**使用方**（use site）的 context — [vendor/cordis/src/utils.ts:170-180](vendor/cordis/src/utils.ts)。所以 greet-tool 调 `ctx.tools.register(...)` 时，方法里的 `this.ctx` 是 greet-tool 的 context，不是 ToolsService 自己的。

```ts title="vendor/cordis/src/reflect.ts:269-271"
  get(name: string, strict = true) {
    return getTraceable(this.ctx, this._getImpl(name, strict)?.value)
  }
```

```ts title="vendor/cordis/src/utils.ts:170-180"
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

- `register()` 先做几项同步校验：必须有 `output.render`；`presentationMeta` 如果提供，必须是函数；`output.schema` 必须是受支持的 JSON Schema；`timeoutMs` 必须是正的有限数；`run_code` 是保留名。校验通过后执行 `return this.layers.effect(this.ctx, layer => layer.tools.insert(name, …), { label: 'tools.register()' })` — [packages/core/tools/src/index.ts:128-153](packages/core/tools/src/index.ts)

```ts title="packages/core/tools/src/index.ts:128-153"
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
  }
```

- `ScopedLayers.effect()` 用 `scopeOf(ctx)` 选层：没有作用域时用 global 层，在 `agent.ctx` 上调用时用该 Agent 的层。它把这次修改包进 `ctx.effect(function* …)`，`yield` 出撤销函数，注册和撤销时都会调 `onChange`（对 tools 来说就是 `this.ctx.emit('tools/change')`，见 index.ts:106）。返回值就是 `ctx.effect()` 给出的 disposer — [packages/core/scope/src/store.ts:219-262](packages/core/scope/src/store.ts)

```ts title="packages/core/scope/src/store.ts:219-262"
  /**
   * Attach one synchronous layer mutation to its registration context.
   * @param ctx - context that determines both scope visibility and effect ownership.
   * @param action - atomic mutation returning its synchronous undo.
   * @param options - Cordis effect label and optional change notification.
   * @returns the exact disposer returned by `ctx.effect()`.
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

- `restrict()`、`guard()`、`setExecutor()`，以及 system-prompt 的 `section()`、`tools()`、`variable()` 都按同一个方式实现：`this.layers.effect(this.ctx, …)` 或 `this.ctx.effect(…)` — [packages/core/tools/src/index.ts:162-189, 326-351, 385-387](packages/core/tools/src/index.ts)；[packages/core/system-prompt/src/index.ts:545-564, 622-628](packages/core/system-prompt/src/index.ts)
- 实测（本文脚本 `scripts/caller-context.mjs`）：消费方插件 `acme-ledger-audit` 的 `fiber.getEffects()` 标签里有 `tools.register()` 和 `systemPrompt.section()`。卸载这个插件后，`tools.schemas()` 变成 `[]`，`acme-ledger:audit` 这个 prompt section 也从 `assemble()` 的结果里消失（完整输出见问题 5）。在 `scripts/t07-programmatic.mjs` 里卸载 greet-tool 后，输出 `schemas: []`。

**(b) `ctx.tools.execute()` 只负责分派给 executor**

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

- 没有 executor 时抛出 `ToolExecutorUnavailableError`，code 为 `TOOL_EXECUTOR_UNAVAILABLE` — [packages/core/tools/src/executor.ts:96-102](packages/core/tools/src/executor.ts)。`ToolExecutor` 接口包含 `describe()`、`execute()` 和可选的 `sections`，定义在 [packages/core/tools/src/executor.ts:63-86](packages/core/tools/src/executor.ts)。
- `dsh-tool-executor-native` 整个插件只做一件事：`ctx.tools.setExecutor(new NativeToolExecutor(ctx))`。它的 `execute` 调用 `executeToolBatch(input, this.pipeline, this.maxParallel)`，默认并发数是 10 — [packages/core/tool-executor-native/src/index.ts:7-35](packages/core/tool-executor-native/src/index.ts)

```ts title="packages/core/tool-executor-native/src/index.ts:7-35"
/** Executes visible capabilities directly and owns their complete batch settlement. */
export class NativeToolExecutor implements ToolExecutor {
  private readonly pipeline: ToolExecutionPipeline

  constructor(ctx: Context, private readonly maxParallel: () => number = () => 10) {
    this.pipeline = new ToolExecutionPipeline(ctx)
  }

  describe(tools: readonly ToolDefinition[]): ToolModelInput {
    return { schemas: tools.map(definition => projectToolSchema(definition, false)) }
  }

  execute(input: ToolBatchInput): Promise<ToolBatchResult> {
    return executeToolBatch(input, this.pipeline, this.maxParallel)
  }
}

/** Cordis plugin name. */
export const name = 'native-tool-executor'
/** Tool registration and scoped dispatch. */
export const inject = ['tools']

/**
 * Select native execution in the mounting scope.
 * @param ctx - context owning this executor's selection and lifetime.
 */
export function apply(ctx: Context): void {
  ctx.tools.setExecutor(new NativeToolExecutor(ctx))
}
```

**(c) 批调度：`executeToolBatch`**

- 把每个 `ToolCallBlock` 变成一份执行输入。`arguments` 用 `JSON.parse` 解析，空串当作 `{}`，**解析失败的 JSON 原样保留为字符串**，交给工具自己报参数错误 — [packages/core/tools/src/batch.ts:50-105](packages/core/tools/src/batch.ts)
- 每轮先用 `pipeline.executionMode()` 分类。只有 `isConcurrencySafe(args)` 恰好返回 `true` 的调用才并行；未知工具、没有声明、函数抛错，一律按独占执行 — [packages/core/tools/src/pipeline.ts:99-115](packages/core/tools/src/pipeline.ts)
- 每个调用依次经过 `prepare`、`dispatch`、`complete`，结果按模型给出的顺序连续提交：`commitReady` 只在前面的槽位都就绪时才往后推进 — [packages/core/tools/src/batch.ts:138-184](packages/core/tools/src/batch.ts)

```ts title="packages/core/tools/src/batch.ts:154-184"
  const startCall = async (index: number): Promise<void> => {
    // oxlint-disable-next-line typescript/no-non-null-assertion -- bounded index
    const call = group[index]!
    callSeqs[index] = settlement.start(call.block)
    started++
    const prepared = await pipeline.prepare(call.exec)
    throwSchedulerFailure()
    switch (prepared.kind) {
      case 'dispatch': {
        const promise = pipeline.dispatch(prepared.exec).then(
          (outcome) => {
            slots[index] = { ...outcome, exec: prepared.exec }
            return index
          },
          (error: unknown) => {
            schedulerFailure ??= { error }
            return index
          },
        )
        inFlight.set(index, promise)
        break
      }
      case 'post-result':
      case 'final-result':
        slots[index] = prepared
        break
      /* v8 ignore next -- closed-union exhaustiveness guard */
      default:
        assertNever(prepared, 'tool-call scheduler prepare result')
    }
  }
```

**(d) 流水线各阶段：`ToolExecutionPipeline`**

1. `createExecution`：用 `snapshotJsonValue(exec.arguments)` 做无损 JSON 快照，无法无损序列化时直接给出最终错误。快照经 `deepFreeze` 冻结，并挂上 `Symbol('dsh.tool.execution')` 作为 `token`。executor 的 `reject()`（如果有）在可扩展策略之前生效 — [packages/core/tools/src/pipeline.ts:117-183, 727-730](packages/core/tools/src/pipeline.ts)
2. `prepare`：
   - 调用方已经取消时，结果是 `ABORTED_BEFORE_DISPATCH`。
   - 否则运行 `tools/pre-execute` waterfall，默认决定是 `allow`。
   - 决定为 `ask` 时交给 `serviceAsk()`。它通过 `ctx.get('approval')` 按需使用审批服务；没有审批服务或没有 agent 时，降级为 deny，并带上 `APPROVAL_UNAVAILABLE`。
   - 决定为 `cancel` 时按取消处理。
   - **只有决定为 allow 时**，才运行 `this.tools.checkGuards(exec)`。
   - 任何拒绝都变成 `isError` 结果，但仍会进入 post-execute（`kind: 'post-result'`）。

   来源：[packages/core/tools/src/pipeline.ts:185-234, 441-494](packages/core/tools/src/pipeline.ts)

```ts title="packages/core/tools/src/pipeline.ts:191-234"
  async prepare(input: ToolExecutionInput): Promise<ScheduledToolPreparation> {
    const created = this.createExecution(input)
    if (created.kind !== 'ready') return created
    const exec = created.exec
    if (this.callerCancelled(exec)) {
      return { kind: 'final-result', exec, result: toolAbortedBeforeDispatchResult() }
    }
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
  }
```

3. `guard`：`checkGuards` 先遍历 global 层，再遍历调用方 Agent 作用域链上的守卫，返回第一个拒绝；返回字符串时规范成 `{ reason }`。README 对它的描述是“在其后添加单调的拥有方策略” — [packages/core/tools/src/index.ts:380-402](packages/core/tools/src/index.ts)；[packages/core/tools/README.zh.md:137](packages/core/tools/README.zh.md)
4. `dispatch`：`tools/execute` waterfall 包着 `dispatchToolBody()`。工具主体运行前，先把调用方的 signal 和包装器可能替换的 signal 融合成一个。然后**在这里重新查一次工具定义**：如果工具已经卸载，抛出 `ToolNotFoundError`，code 为 `UNKNOWN_TOOL`。之后置 `bodyInvoked = true`，执行 `await tool.execute(exec.arguments, exec)`，再调用 `createSuccessResult()` — [packages/core/tools/src/pipeline.ts:254-336](packages/core/tools/src/pipeline.ts)

```ts title="packages/core/tools/src/pipeline.ts:263-293"
  private async dispatchToolBody(exec: MutableToolRunContext): Promise<ToolExecutionResult> {
    const state = this.cancellationStates.get(exec)
    /* v8 ignore next -- only registry-minted executions reach the staged scheduler methods */
    if (state === undefined) throw new Error('tool registry scheduler invariant violated: missing cancellation state')
    const wrapperSignal = exec.signal
    const fused = fuseToolSignals(state.callerSignal, wrapperSignal)
    const signal = fused.signal

    if (isAborted(signal)) {
      fused.dispose()
      return toolAbortedBeforeDispatchResult()
    }
    exec.signal = signal
    try {
      const tool = this.resolveExecution(exec.name, exec.agent, exec.parent !== undefined, exec.toolSet)
      if (!tool) throw new ToolNotFoundError(exec.name)
      state.bodyInvoked = true
      const returned = await tool.execute(exec.arguments, exec)
      const result = this.createSuccessResult(exec, tool, returned)
      return isAborted(signal)
        ? toolAbortedResult(result)
        : result
    } catch (error: unknown) {
      return isAborted(signal) && isUnclassifiedCancellation(error, signal)
        ? toolErrorResult(error, { name: 'AbortError', code: TOOL_ABORTED })
        : toolErrorResult(error)
    } finally {
      fused.dispose()
      exec.signal = wrapperSignal
    }
  }
```

5. `createSuccessResult`：对返回值做无损快照，按 `tool.output.schema` 校验（不通过就抛 `ToolOutputError`），冻结，调用 `output.render(args, value)`，有 `presentationMeta` 的话再调用它，最后物化 — [packages/core/tools/src/pipeline.ts:557-588](packages/core/tools/src/pipeline.ts)

```ts title="packages/core/tools/src/pipeline.ts:557-588"
  /** Snapshot, validate, render, and optionally project one successful body value. */
  private createSuccessResult(exec: ToolExecution, tool: ToolDefinition, candidate: unknown): ToolExecutionSuccess {
    const detached = snapshotToolValue(tool.name, candidate)
    const violations = validateJsonSchemaValue(tool.output.schema, detached, 'value')
    if (violations.length > 0) throw new ToolOutputError(tool.name, violations)
    const value = deepFreeze(detached)
    let rendered: ContentBlock[]
    try {
      rendered = tool.output.render(exec.arguments, value)
    } catch (error: unknown) {
      throw projectionError(tool.name, 'render', error)
    }
    const content = snapshotProjection(tool.name, 'render', rendered)
    let meta: JsonValue | undefined
    if (tool.output.presentationMeta !== undefined) {
      let projected: JsonValue
      try {
        projected = tool.output.presentationMeta(exec.arguments, value)
      } catch (error: unknown) {
        throw projectionError(tool.name, 'presentationMeta', error)
      }
      meta = snapshotProjection(tool.name, 'presentationMeta', projected)
    }
    const concludesTurn = this.concludingExecutions.has(exec)
    return this.markCanonical(exec, this.materializeFinalResult({
      isError: false,
      value,
      content,
      ...meta !== undefined ? { meta } : {},
      ...concludesTurn ? { concludesTurn: true as const } : {},
    }) as ToolExecutionSuccess)
  }
```

6. `complete`：
   - 先调用可选的 `projectContent`。
   - 然后运行 `tools/post-execute` waterfall。`accept` 可以替换 `content` 或 `value`，但不能同时替换两者；替换 `value` 会重新走一遍 `createSuccessResult`。`block` 会把结果变成 `isError`，并附上 feedback。
   - 再处理取消语义，最后调用 `finish()`。

   来源：[packages/core/tools/src/pipeline.ts:347-384, 496-546](packages/core/tools/src/pipeline.ts)
7. `finish`：物化，调用 `finalizeContent`，再物化一次，然后调用 `notifyResult()`。这些都完成**之后**，才把 `finalResult` 返回给调用方 — [packages/core/tools/src/pipeline.ts:386-409](packages/core/tools/src/pipeline.ts)
8. `notifyResult`：先 `Object.freeze(exec)`，再用 `ctx.events.dispatch('emit', [scopeTarget(this.tools, exec.agent), 'tools/result', exec, result])` 取出回调，逐个放在 try/catch 里调用。同步异常和被 reject 的 promise 都只记一条 `logger.warn`，不影响结果 — [packages/core/tools/src/pipeline.ts:419-439](packages/core/tools/src/pipeline.ts)

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

- 五个事件的类型和分发模式：`tools/pre-execute`、`tools/execute`、`tools/post-execute` 是 waterfall；`tools/result`、`tools/change` 是 emit。前四个按 `exec.agent` 做作用域过滤，`tools/change` 不过滤 — [packages/core/tools/src/events.ts:6-58](packages/core/tools/src/events.ts)

```ts title="packages/core/tools/src/events.ts:42-58"
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

- `scopeTarget()` 生成一个 carrier 对象作为事件的接收方。没有作用域标签的监听器总能收到事件；带标签的监听器只收到本作用域及其**后代**作用域的事件。也就是说，事件沿作用域链向上传，不向下传 — [packages/core/scope/src/index.ts:158-185](packages/core/scope/src/index.ts)

**(e) 实测：真实流水线上的事件顺序**（`scripts/t07-programmatic.mjs`，在真实包上运行；脚本先 `await` executor 加载完成，再加载 greet-tool，原因见问题 3）

```js title="lab-harness/scripts/t07-programmatic.mjs"
// Tutorial-07 composition, programmatic, on the real published dsh packages (0.2.3-alpha.2).
// The executor is awaited before greet-tool so its fire-and-forget execute() finds one.
import { Context } from '@deepseek-ai/cordis'
import * as sessionProjection from '@deepseek-ai/dsh-session-projection'
import * as systemPrompt from '@deepseek-ai/dsh-system-prompt'
import * as tools from '@deepseek-ai/dsh-tools'
import * as nativeExecutor from '@deepseek-ai/dsh-tool-executor-native'
import * as toolLogger from '../t07/tool-logger.ts'
import * as greetTool from '../t07/greet-tool.ts'

const pick = m => m.default ?? m
const root = new Context()
await root.plugin(pick(sessionProjection))
await root.plugin(pick(systemPrompt))
await root.plugin(pick(tools))
await root.plugin(nativeExecutor)
await root.plugin(toolLogger)
await root.plugin(greetTool)
await new Promise(r => setTimeout(r, 50))

// What the model would see: the registry's schema projection and the system-prompt tools provider.
console.log('schemas:', JSON.stringify(root.tools.schemas()))
const assembly = await root.systemPrompt.assemble({})
console.log('assembly keys:', Object.keys(assembly))
console.log('assembly.tools:', JSON.stringify(assembly.tools ?? null))

// Invalid arguments: validation lives in defineTool's execute wrapper, so it runs after pre-execute/guard.
root.on('tools/pre-execute', async (exec, next) => { console.log(`[pre-execute] ${exec.name} args=${JSON.stringify(exec.arguments)}`); return next() })
root.on('tools/post-execute', async (exec, result, next) => { console.log(`[post-execute] ${exec.name} isError=${result.isError}`); return next() })
const bad = await root.tools.execute({ calls: [{ type: 'tool-call', id: 'demo-2', name: 'greet', arguments: '{"name":42}' }], signal: new AbortController().signal })
console.log('invalid args ->', JSON.stringify(bad.results[0].result))
const unknown = await root.tools.execute({ calls: [{ type: 'tool-call', id: 'demo-3', name: 'nope', arguments: '{}' }], signal: new AbortController().signal })
console.log('unknown tool ->', JSON.stringify(unknown.results[0].result))

// Unload greet-tool: its register() disposer was attached to greet-tool's fiber.
for (const f of [...root.registry.values()].find(r => r.name === 'greet-tool').fibers) await f.dispose()
console.log('after greet-tool dispose, schemas:', JSON.stringify(root.tools.schemas()))
```

```text title="node scripts/t07-programmatic.mjs（实际输出）"
[tool-logger] greet -> Hello, Cordis!
tool replied: [{"type":"text","text":"Hello, Cordis!"}]
schemas: [{"name":"greet","description":"Greet the named person.","parameters":{"type":"object","properties":{"name":{"type":"string","description":"Who to greet"}},"required":["name"]}}]
assembly keys: [ 'sections', 'contexts', 'variables', 'tools', 'systemUpdates' ]
assembly.tools: [{"name":"greet","description":"Greet the named person.","parameters":{"type":"object","properties":{"name":{"type":"string","description":"Who to greet"}},"required":["name"]}}]
[pre-execute] greet args={"name":42}
[post-execute] greet isError=true
[tool-logger] greet -> Error: invalid arguments: "name" must be a string
invalid args -> {"isError":true,"error":{"message":"invalid arguments: \"name\" must be a string","info":{"name":"ToolArgsError","code":"INVALID_ARGS"}},"content":[{"type":"text","text":"Error: invalid arguments: \"name\" must be a string"}]}
[pre-execute] nope args={}
[post-execute] nope isError=true
[tool-logger] nope -> Error: unknown tool "nope"
unknown tool -> {"isError":true,"error":{"message":"unknown tool \"nope\"","info":{"name":"ToolNotFoundError","code":"UNKNOWN_TOOL"}},"content":[{"type":"text","text":"Error: unknown tool \"nope\""}]}
after greet-tool dispose, schemas: []
```

要点：
- `[tool-logger]` 一行先于 `tool replied` 打印。这与教程第 96 行的说法一致：“`tools/result` 在结果物化过程中发出，发生在 `execute` 向调用方返回的 promise 兑现之前” — [docs/cordis-tutorial/07-into-the-harness.zh.md:96](docs/cordis-tutorial/07-into-the-harness.zh.md)。
- 参数为 `{"name":42}` 时，pre-execute 和 post-execute **都执行了**，错误是 `ToolArgsError`，code 为 `INVALID_ARGS`。
- 调用不存在的工具 `nope` 时，同样先执行 pre-execute，最后以 `ToolNotFoundError`（`UNKNOWN_TOOL`）结束。

### Inferences
- 参数校验不在流水线的前置阶段，而在 `defineTool` 包装的 `execute` 里（见问题 2）。因此 `tools/pre-execute` 监听器和 guard 看到的 `exec.arguments` **可能不合法**，甚至可能是解析失败的原始 JSON 字符串（batch.ts:98-105）。写权限门禁时，不能假定参数已经通过 schema 校验。
- `register()` 的 disposer 挂在“当前调用方”的 Fiber 上，这是 tracker 机制决定的，ToolsService 没有显式传参。如果把 `ctx.tools` 存进模块级变量，再从另一个插件的回调里调用，effect 会挂到最初取得它的那个 context 上，而不是实际发起调用的插件。
- `tools/result` 通过 `events.dispatch` 自己做了逐个监听器的异常隔离；普通 `ctx.emit` 没有这层隔离（见问题 4）。第三方插件发事件时，要自己决定是否隔离监听器异常。

### Gaps
- 以下路径只读了源码，没有实测：`ask` 转交审批服务、`tools/execute` 包装器替换 `signal`、取消路径（`ABORTED` / `ABORTED_BEFORE_DISPATCH`）。
- 没有覆盖 PTC executor（`dsh-tool-executor-ptc`，在 base bundle 里是 `disabled: true`）。

## 问题 2：`defineTool` 产出什么？工具的贡献怎样进到 system prompt？

### Takeaway
`defineTool` 做三件事：把参数 DSL 编译成以 object 为根的 JSON Schema（各属性上的 `required: true` 汇总成 `required` 数组）；把 `output.schema` 编译成值 schema；给用户的 `execute` 外面包一层“先校验参数，不合法就抛 `ToolArgsError`（`INVALID_ARGS`）”。工具的贡献有两条路进入 prompt。工具 schema 由 **ToolsService 构造时注册的 `systemPrompt.tools()` provider** 收集，中间要经过 executor 的 `describe()`。说明文字则由插件自己调用 `ctx.systemPrompt.section()` 提供。两者都是调用方 Fiber 上的 effect，插件卸载时自动撤回。

### Cited Findings
- `defineTool` 编译 `parameters` 和 `output.schema`。包装后的 `execute` 先调用 `validate(args)`，有违规就抛 `ToolArgsError`。`presentCall` / `presentResult` 只做软校验：参数不合法时返回 `undefined`，因为回放旧日志时，参数可能来自旧版 schema。`isConcurrencySafe` 遇到不合法参数时返回 `false` — [packages/core/tools/src/schema.ts:548-633](packages/core/tools/src/schema.ts)

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

- 参数根是隐式开放的 object：`{ type: 'object', properties, required? }` — [packages/core/tools/src/schema.ts:445-459](packages/core/tools/src/schema.ts)；`ToolArgsError` 的 code 是 `INVALID_ARGS` — [packages/core/tools/src/schema.ts:461-471](packages/core/tools/src/schema.ts)
- 实测得到的 greet 投影：`{"type":"object","properties":{"name":{"type":"string","description":"Who to greet"}},"required":["name"]}`（见问题 1 的输出）。
- 给模型看的投影只保留 `name`、`description`、`parameters`、`deferLoading` 四个字段 — [packages/core/tools/src/schema.ts:635-646](packages/core/tools/src/schema.ts)
- 文档对 `execute()` 约定的说明 — [docs/cookbook/adding-a-tool.zh.md:43-47](docs/cookbook/adding-a-tool.zh.md)：
  - 显式写出的对象节点必须声明 `additionalProperties`。
  - DSL 表达不了的约束（非空字符串、正数、跨字段规则等）要自己检查，并抛 `ToolArgsError`。
  - 工具主体只返回规范值，不返回内容块。
  - 只有主体抛出的 `HarnessError` 会在结果里保留 `error.name` 和 `error.code`。
- **schema 怎样进入 prompt**：`ToolsService` 构造函数里调用 `ctx.systemPrompt.tools(({ scope, toolSet }) => { const executor = this.selectExecutor(scope); const description = executor.describe(this.definitions(scope, toolSet)); … })` — [packages/core/tools/src/index.ts:111-118](packages/core/tools/src/index.ts)。native executor 的 `describe` 就是逐个调用 `projectToolSchema(definition, false)` — [packages/core/tool-executor-native/src/index.ts:15-17](packages/core/tool-executor-native/src/index.ts)
- `SystemPrompt.tools()` 把 provider 加进调用方所在作用域的层 — [packages/core/system-prompt/src/index.ts:615-628](packages/core/system-prompt/src/index.ts)。`assemble()` 遍历 global 层和作用域链上的所有 provider，用 `structuredClone` 复制参数，并按 `toolOrder` 排序 — [packages/core/system-prompt/src/index.ts:757-772, 791](packages/core/system-prompt/src/index.ts)

```ts title="packages/core/system-prompt/src/index.ts:757-772"
    // Keep all declared names for order validation, including tools restricted from this step.
    const providers = visibleLayers.flatMap(layer => [...layer.toolProviders.values()])
    const collected: ToolSchema[] = []
    const knownNames = new Set<string>()
    for (const provider of providers) {
      const result = provider(context)
      const schemas = result.schemas.map(({ name, description, parameters, deferLoading }): ToolSchema => ({
        name,
        description,
        parameters: structuredClone(parameters),
        ...deferLoading === true ? { deferLoading } : {},
      }))
      collected.push(...schemas)
      const acceptedKnownNames = result.knownNames ?? schemas.map(tool => tool.name)
      for (const name of acceptedKnownNames) knownNames.add(name)
    }
```

- **说明文字怎样进入 prompt**：`section({ name, order, text })`。`order` 必须是有限数。同名 section 由最近的作用域、最晚的注册胜出。disposer 会记下 `disposedAt`，之后组装时可以向已经采纳过该 section 的会话发出 retirement 提示 — [packages/core/system-prompt/src/index.ts:538-564](packages/core/system-prompt/src/index.ts)。`PromptSection` 的字段见 [packages/core/system-prompt/src/index.ts:65-95](packages/core/system-prompt/src/index.ts)。仓库内置的 section 顺序表（`TOOL_BASH: 1000` … `TOOLS_SDK: 5000` … `DEPLOYMENT_PERSONA_SUFFIX: 10200`）见 [packages/core/system-prompt/src/index.ts:191-227](packages/core/system-prompt/src/index.ts)
- executor 也可以带 `sections`，`setExecutor` 在同一个生成器 effect 里用 `yield this.ctx.systemPrompt.section(...)` 注册它们 — [packages/core/tools/src/index.ts:326-351](packages/core/tools/src/index.ts)
- 实测（`scripts/no-executor.mjs`）：**没有 executor 时，连 prompt 组装都会失败**。

```js title="lab-harness/scripts/no-executor.mjs"
// Without any executor plugin: registration works, but both execute() and prompt assembly fail.
import { Context } from '@deepseek-ai/cordis'
import * as sp from '@deepseek-ai/dsh-session-projection'
import * as sys from '@deepseek-ai/dsh-system-prompt'
import * as tools from '@deepseek-ai/dsh-tools'
import { defineTool } from '@deepseek-ai/dsh-tools'
const pick = m => m.default ?? m
const root = new Context()
for (const p of [sp, sys, tools]) await root.plugin(pick(p))
root.tools.register(defineTool({ name: 'noop', description: 'x', parameters: {}, output: { schema: { type: 'null' }, render: () => [] }, async execute() { return null } }))
console.log('schemas():', root.tools.schemas().map(s => s.name))
await root.systemPrompt.assemble({}).then(() => console.log('assemble ok'), e => console.log('assemble ->', e.code, e.message))
await root.tools.execute({ calls: [], signal: new AbortController().signal }).then(() => console.log('execute ok'), e => console.log('execute ->', e.code))
```

```text title="node scripts/no-executor.mjs（实际输出）"
schemas(): [ 'noop' ]
assemble -> TOOL_EXECUTOR_UNAVAILABLE The selected tool executor is unavailable. Load an executor in this composition before running the agent.
execute -> TOOL_EXECUTOR_UNAVAILABLE
```

### Inferences
- schema provider 要经过 `selectExecutor()`，所以在 4b399c3 上，“装了 tools 但没装 executor”的组合不是“工具看得见、调不了”，而是 `assemble()` 直接抛错。executor 实际上是工具子系统必需的一环。
- 第三方插件的 section `order` 只能自己选数字。`getSectionOrder()` 只接受仓库内置的名字（`PromptSectionOrderName = keyof typeof SECTION_ORDERS`，index.ts:231）。本文例子用了 `5100`，排在 `TOOLS_SDK: 5000` 之后。这是本文自己的选择，不是官方约定。

### Gaps
- 没有找到官方说明第三方 section 应该使用哪个 order 区间。
- 没有实测 `toolOrder` 配置和 `deferLoading`。

## 问题 3：工具插件要离开 PENDING，组合里必须有哪些服务？教程 07 与代码有哪些出入？

### Takeaway
依赖链是 `greet-tool → tools → systemPrompt → sessionProjections`，此外还需要一个 executor 插件，而这个依赖**没有用 inject 表达**。教程 07 的 YAML 只列了 `dsh-system-prompt` 和 `dsh-tools`。在 4b399c3 上原样运行，四个插件全部停在 PENDING，进程不输出任何内容，以 0 退出。补上 `dsh-session-projection` 后，greet-tool 能激活，但它在 `apply` 里发起、不等待结果的那次 `execute` 会因为没有 executor 而抛出 `TOOL_EXECUTOR_UNAVAILABLE`。再补上 `dsh-tool-executor-native` 后，用 Loader 加载时 greet-tool 仍然在 executor 就绪之前发起调用；本文在之前和这次的运行里都复现了这个问题。

### Cited Findings
- 教程原文：“`@deepseek-ai/dsh-tools` 会注入 `systemPrompt` 服务……缺少提供方时，工具插件会像第 6 章所述那样保持 PENDING” — [docs/cordis-tutorial/07-into-the-harness.zh.md:76-96](docs/cordis-tutorial/07-into-the-harness.zh.md)

````md title="docs/cordis-tutorial/07-into-the-harness.zh.md:76-96"
## 组合并运行

```yaml
- name: '@deepseek-ai/dsh-system-prompt'
- name: '@deepseek-ai/dsh-tools'
- name: './tool-logger.ts'
- name: './greet-tool.ts'
```

`@deepseek-ai/dsh-tools` 会注入 `systemPrompt` 服务，因为工具需要向系统提示词贡献 schema，所以组合中也要列出该服务的提供方。缺少提供方时，工具插件会像[第 6 章](06-composition-and-hmr.zh.md)所述那样保持 PENDING。

```sh
node --import tsx ../../vendor/cordis/bin.js
```

```
[tool-logger] greet -> Hello, Cordis!
tool replied: [{"type":"text","text":"Hello, Cordis!"}]
```

logger 会先触发：`tools/result` 在结果物化过程中发出，发生在 `execute` 向调用方返回的 promise 兑现之前。两个插件都不知道另一个插件存在，它们由注册表服务和事件连接。
````

- 源码里真实的 inject 声明：
  - `ToolsService.inject = ['systemPrompt']` — [packages/core/tools/src/index.ts:100](packages/core/tools/src/index.ts)
  - `SystemPrompt.inject = ['sessionProjections']`，并在 `[Service.init]` 里执行 `await this.ctx.sessionProjections.register(sectionAdmissionsDefinition)` — [packages/core/system-prompt/src/index.ts:486-487, 534-536](packages/core/system-prompt/src/index.ts)
  - `SessionProjectionRegistry` 没有 `inject`，构造时调用 `super(ctx, 'sessionProjections')` — [packages/session/session-projection/src/index.ts:282-296](packages/session/session-projection/src/index.ts)
  - `native-tool-executor` 的 `inject = ['tools']` — [packages/core/tool-executor-native/src/index.ts:24-27](packages/core/tool-executor-native/src/index.ts)
  - 官方工具 `tool-todo` 的 `inject = ['tools', 'sessionProjections']` — [packages/todo/tool-todo/src/index.ts:22-23](packages/todo/tool-todo/src/index.ts)

```ts title="packages/core/system-prompt/src/index.ts:486-495"
export class SystemPrompt extends Service {
  static inject = ['sessionProjections']
  static Config: z<Config> = z.object({
    includeHarnessIdentity: z.boolean().default(true),
    includeRuntimeContext: z.boolean().default(true),
    personaPrefix: z.string().default(''),
    personaSuffix: z.string().default(''),
    // Preserve omission because an explicit empty order lacks the rest marker.
    toolOrder: z.array(z.string()).default(undefined as unknown as string[]),
  })
```

- base bundle 里有这几行：`session-projection`、`tools`、`tool-executor-native`、`tool-executor-ptc`（disabled）、`system-prompt` — [packages/bundle/base/cordis.patch.yml:173-174, 526-543](packages/bundle/base/cordis.patch.yml)。headless 层只改 `system-prompt` 的 `personaPrefix` 等配置，不增删工具相关的行 — [packages/bundle/headless/cordis.patch.yml:9-12](packages/bundle/headless/cordis.patch.yml)

```yaml title="packages/bundle/base/cordis.patch.yml:526-543"
    # Shared tool definitions and the deployment’s native executor.
    - id: tools
      name: '@deepseek-ai/dsh-tools'

    - id: tool-executor-native
      name: '@deepseek-ai/dsh-tool-executor-native'

    - id: tool-executor-ptc
      name: '@deepseek-ai/dsh-tool-executor-ptc'
      disabled: true

    # The deployment persona is a deployment choice; plan-mode and tool plugins own
    # their own prompt sections.
    - id: system-prompt
      name: '@deepseek-ai/dsh-system-prompt'
      config:
        personaPrefix: ''
```

- capability-seams 表里，`ctx.systemPrompt`、`ctx.tools`、`ctx.sessionProjections` 都属于 `core` 类，提供方分别是 `system-prompt`、`tools`、`session-projection` — [docs/capability-seams.zh.md:783-784, 789](docs/capability-seams.zh.md)

**实测 A：教程 07 的 YAML 原样运行。** 实验文件用 `sed -n` 从教程拷出：`greet-tool.ts` 是第 12-48 行，`tool-logger.ts` 是第 58-71 行，`cordis.yml` 是第 79-82 行。启动器 `scripts/cordis-bin.mjs` 是 `vendor/cordis/bin.js` 的原样拷贝。

```yaml title="lab-harness/t07/cordis.yml"
- name: '@deepseek-ai/dsh-system-prompt'
- name: '@deepseek-ai/dsh-tools'
- name: './tool-logger.ts'
- name: './greet-tool.ts'
```

```text title="cd t07 && node ../scripts/cordis-bin.mjs; echo exit=$?（实际输出：无任何内容）"
exit=0
```

下面的 `scripts/boot-and-dump.mjs` 用同样的 Loader + Include 方式启动，然后遍历 `ctx.registry`，打印每个 Fiber 的状态和缺失的服务：

```js title="lab-harness/scripts/boot-and-dump.mjs"
// Boot a cordis.yml the same way vendor/cordis/bin.js does, then dump every fiber's state.
import { Context } from '@deepseek-ai/cordis'
import { pathToFileURL } from 'node:url'
import Loader from '@deepseek-ai/cordis-plugin-loader'

const STATES = ['PENDING', 'LOADING', 'ACTIVE', 'FAILED', 'DISPOSED', 'UNLOADING']
const ctx = new Context()
ctx.baseUrl = pathToFileURL(process.cwd()).href + '/'
await ctx.plugin(Loader)
await ctx.loader.create({ name: '@deepseek-ai/cordis-plugin-include', config: { path: './cordis.yml' } })
await new Promise(r => setTimeout(r, Number(process.env.WAIT ?? 300)))
for (const runtime of ctx.registry.values()) {
  for (const fiber of runtime.fibers) {
    const missing = Object.keys(fiber.inject).filter(n => fiber.inject[n]?.required !== false && ctx.get(n) === undefined)
    console.log(`[dump] ${String(runtime.name ?? '?').padEnd(36)} ${STATES[fiber.state]}${missing.length ? '  missing=' + missing.join(',') : ''}`)
  }
}
process.exit(0)
```

```text title="cd t07 && node ../scripts/boot-and-dump.mjs（实际输出）"
[dump] Loader                               ACTIVE
[dump] isolate                              ACTIVE
[dump] Include                              ACTIVE
[dump] tool-logger                          PENDING  missing=tools
[dump] ToolsService                         PENDING  missing=systemPrompt
[dump] greet-tool                           PENDING  missing=tools
[dump] SystemPrompt                         PENDING  missing=sessionProjections
```

**实测 B：补上 `@deepseek-ai/dsh-session-projection`**（`t07b/cordis.yml`）。greet-tool 激活了，但它 IIFE 中的 `await ctx.tools.execute(...)` 被拒绝，这个 rejection 没有被处理，进程崩溃（下面是输出的开头）：

```text title="cd t07b && node ../scripts/boot-and-dump.mjs（输出前 4 行）"
<lab>/node_modules/@deepseek-ai/dsh-tools/lib/index.js:633
		if (executor === void 0) throw new ToolExecutorUnavailableError();
		                               ^
```

**实测 C：再补上 `@deepseek-ai/dsh-tool-executor-native`**（`t07c/cordis.yml`，6 行），用 `cordis-bin.mjs` 运行，结果仍然失败：

```yaml title="lab-harness/t07c/cordis.yml"
- name: '@deepseek-ai/dsh-session-projection'
- name: '@deepseek-ai/dsh-system-prompt'
- name: '@deepseek-ai/dsh-tools'
- name: '@deepseek-ai/dsh-tool-executor-native'
- name: './tool-logger.ts'
- name: './greet-tool.ts'
```

```text title="cd t07c && node ../scripts/cordis-bin.mjs; echo exit=$?（实际输出）"
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

原因：greet-tool 和 native executor 都只声明了 `inject: ['tools']`。`tools` 一就绪，两者都可以激活，没有任何依赖规定 executor 的 `setExecutor()` 要先执行；而 greet-tool 在 `apply` 里立刻就发起了调用。

**实测 D：程序化加载，按顺序逐个 `await`**（问题 1 的 `t07-programmatic.mjs`）。输出与教程第 91-94 行一致：先是 `[tool-logger] greet -> Hello, Cordis!`，然后是 `tool replied: [{"type":"text","text":"Hello, Cordis!"}]`。

### Inferences
- 教程 07 的组合清单落后于 4b399c3 的代码，至少缺 `dsh-session-projection`（否则整条依赖链都停在 PENDING）和一个 executor 插件。本地克隆是浅克隆，无法用 `git log` 确认是哪次提交引入了 `sessionProjections` 依赖和 executor 拆分，只能说这份清单在 4b399c3 上不成立。
- 在工具插件的 `apply` 里自己调用 `execute` 只适合演示。实际的调用来自 agent loop，那时 executor 早已就绪。如果插件确实要在启动时调用工具，就得等 executor 可用。目前没有可以 inject 的“executor 就绪”服务，只能捕获 `TOOL_EXECUTOR_UNAVAILABLE` 后重试，或在组合层保证加载顺序。
- 没有 logger 插件时，Loader 的加载失败（例如模块语法错误）不会有任何提示。本文第一次运行自写插件时，provider 用了 TS 的 parameter property 语法，Node 的 strip-only 模式不支持，导入失败，唯一的表现是状态显示 `ledger=none`。加上 `@deepseek-ai/cordis-plugin-logger-console` 之后才能看到错误。

### Gaps
- 没有在 dsh 仓库里按教程原样运行（`node --import tsx ../../vendor/cordis/bin.js`，需要 `pnpm` workspace），用的是 npm 发布包。包里的源码与仓库逐字节一致，但仓库的 workspace 环境可能会额外加载别的插件，这一点没有验证。

## 问题 4：第三方插件怎样把自己的服务暴露给其他插件？怎样发自己的事件？

### Takeaway
服务方面，要点有四个：
- 服务名放在一个扁平的命名空间里，要带有辨识度的前缀。教程 03 明确要求这一点，因为 harness 已经占用了 `tools`、`llm` 等名字。
- 用 `declare module '@deepseek-ai/cordis' { interface Context { … } }` 提供类型，消费方 `inject` 这个服务。
- 可替换的能力按 Service Definition / Provider / Consumer 三种角色拆分，但不要预防性地拆包。
- 如果服务的方法要替调用方登记东西（类似 `tools.register`），需要 Service tracker，让方法里的 `this.ctx` 指向调用方。

事件方面：
- 命名用 `namespace/action`，通过 `interface Events` 合并提供类型，并在文档注释里写明分发模式。
- 普通 `ctx.emit` **不隔离监听器抛出的异常**。需要隔离时，仿照 `tools/result` 的做法，用 `ctx.events.dispatch('emit', …)` 取出回调，逐个包在 try/catch 里调用。

### Cited Findings
- 命名：“每个应用中的服务名称共用一个扁平命名空间。请为自有服务添加有辨识度的前缀或命名空间（harness 已占用 `tools` 和 `llm` 等普通名称）” — [docs/cordis-tutorial/03-services.zh.md:92-94](docs/cordis-tutorial/03-services.zh.md)
- 类型：
  - `declare module` 只在编译期做声明合并，不生成代码 — [docs/cordis-tutorial/03-services.zh.md:37-42](docs/cordis-tutorial/03-services.zh.md)
  - 事件通过 `interface Events` 合并声明，命名用 `namespace/action` — [docs/cordis-tutorial/04-events.zh.md:44](docs/cordis-tutorial/04-events.zh.md)
  - 消费方用 `import type {} from '…'` 引入这些声明合并 — [docs/cordis-tutorial/07-into-the-harness.zh.md:74](docs/cordis-tutorial/07-into-the-harness.zh.md)
- 三种角色：Service Definition 拥有 Request/Result 类型，Provider 和 Consumer 只依赖 Definition 包；文档同时要求“不要预防性拆分” — [docs/user/develop/practice/index.zh.md:58-151](docs/user/develop/practice/index.zh.md)
- 不是 `Service` 子类也能拿到调用方上下文。仓库里的 `ConversationDefinitionRegistry` 在构造函数里执行 `Object.defineProperty(this, Service.tracker, { value: { property: 'ctx' } })`，以此实现源码注释所说的 “Register one uniquely keyed Definition for the caller's lifetime” — [packages/client/ui-conversation/src/client/conversation/definition-registry.ts:10-15, 35-40](packages/client/ui-conversation/src/client/conversation/definition-registry.ts)

```ts title="packages/client/ui-conversation/src/client/conversation/definition-registry.ts:10-15"
  /** @param ctx - Context whose effects own contributed Definitions. */
  constructor(protected readonly ctx: Context) {
    Object.defineProperty(this, Service.tracker, {
      value: { property: 'ctx' },
    })
  }
```

- 隔离：`Service` 默认的事件过滤器 `[symbols.filter]` 比较 `ctx[isolate][name]`，以服务为 `this` 发出的事件只有同一隔离域里的监听器能收到 — [vendor/cordis/src/service.ts:64-66](vendor/cordis/src/service.ts)。`provide()` 按 `ctx[symbols.isolate][name]` 存取服务，同一隔离域里重复 provide 会抛出 `service "x" has been registered at <fiber>` — [vendor/cordis/src/reflect.ts:313-349](vendor/cordis/src/reflect.ts)
- 事件分发：
  - `_resolve()` 用 `thisArg[Context.filter]` 过滤监听器。
  - `emit()` 的实现就是 `for (const callback of callbacks) Reflect.apply(...)`，**没有 try/catch**。一个监听器抛错，后面的监听器都不会执行，异常还会抛回发出方。
  - `dispatch()` 只返回绑定好的回调，源码注释写明 “DSH retains callback enumeration for per-listener error containment”。

  来源：[vendor/cordis/src/events.ts:162-210](vendor/cordis/src/events.ts)

```ts title="vendor/cordis/src/events.ts:175-210"
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
```

- dsh 自己的 `tools/result` 正是用 `dispatch` 加逐个 try/catch，实现了注释里说的 “Listener failures are contained” — [packages/core/tools/src/pipeline.ts:419-439](packages/core/tools/src/pipeline.ts)；[packages/core/tools/src/events.ts:42-49](packages/core/tools/src/events.ts)
- waterfall 的规矩：只做观察的监听器必须调用 `next()`，否则会吞掉下游的默认行为 — [docs/cordis-tutorial/04-events.zh.md:136-138](docs/cordis-tutorial/04-events.zh.md)
- volatile 字段：Loader 比较配置时忽略 volatile 字段。如果只有 volatile 字段变化，Loader 把新值提交到运行中的引用，发出一次 `loader/volatile-update`，不经过 `internal/update`；直接调用 `fiber.update()` 则按原有生命周期处理 — [docs/cordis-tutorial/05-config.zh.md:70-93](docs/cordis-tutorial/05-config.zh.md)。实现位于 `Entry.update` → `_commitVolatile`，条件包括 `fiber.state === ACTIVE`，并且变化的键只有 `config` 一个 — [vendor/loader/src/config/entry.ts:155-211](vendor/loader/src/config/entry.ts)

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

### Inferences
- 第三方插件最好给服务名和事件名加同一个前缀（本文用 `acmeLedger` 和 `acme-ledger/*`），这样在 `cordis-surface`、`capability-seams` 这类清单里一眼就能看出归属。
- 如果服务的方法会替调用方登记回调或订阅，一定要让 disposer 落在调用方的 Fiber 上（tracker 加 `this.ctx.effect(...)`）。否则调用方卸载后，回调还留在服务里，引用着已经失效的插件状态。
- 事件模式的选择：
  - 只是通知：用 `emit`，并自己决定是否隔离异常。
  - 允许其他插件改写结果或否决：用 `waterfall`。
  - 要等所有监听器完成：用 `parallel`。它会把失败聚合成 `AggregateError` 抛出（events.ts:193-200）。

### Gaps
- 没有找到 dsh 对第三方前缀格式的具体规定（例如是否要求以包名为前缀），只有教程 03 的一般性要求。
- 没有实测 `ctx.isolate()` 下的服务隔离。

## 问题 5：实战插件 acme-ledger 的设计、完整源码和运行记录

### Takeaway
本文自己写了四个插件：
- `acme-ledger`：提供 `acmeLedger` 服务。它持有一个 `FileHandle` 和一个 flush 定时器，并在**一个异步生成器 effect 里最后才 provide**。
- `acme-ledger-memory`：同名服务的内存实现，用来演示更换提供方。
- `acme-ledger-audit`：消费方。它监听 `tools/result` 记账，注册 `ledger_stats` 工具和一个 prompt section，卸载时写入一条 `audit-closing`。
- `acme-ledger-tail`：观察方。它监听 `acme-ledger/flushed`，不对服务做硬依赖，只用 `ctx.inject` 让 `watch` 这一部分等待服务。

在真实的 harness 包和真实的 Cordis Loader 上，下面这些都验证通过了：
- 消费方从 PENDING 变为 ACTIVE。
- 工具结果流入账本，触发 flush 事件。
- 会抛错的监听器被隔离，不影响其他监听器。
- 只改 volatile 字段时插件不重启。
- 改 `path` 时插件重启，旧文件里保住了消费方的最后一条记录。
- 卸载提供方、更换提供方、整棵树 dispose 时，最后一条记录都没有丢。
- 结束后进程里没有残留的定时器和文件句柄。

### Cited Findings

**设计**

| 关注点 | 做法 | 依据 |
|---|---|---|
| 服务名 / 事件名 | 服务名 `acmeLedger`；事件 `acme-ledger/flushed`（emit） | 扁平命名空间要加前缀，03-services.zh.md:92-94；`namespace/action`，04-events.zh.md:44 |
| 服务类型 | 抽象类 `AcmeLedger`，加上 `declare module` 合并 `Context` / `Events` | 03-services.zh.md:37-42；practice/index.zh.md:147-151 |
| 调用方归属 | 构造函数里装上 `Service.tracker`；`append()` 用 `this.ctx.fiber.name` 给记录署名；`watch()` 用 `this.ctx.effect()` 登记订阅，订阅随调用方一起卸载 | definition-registry.ts:10-15；原理与 `tools.register` 相同（store.ts:226-262） |
| 资源与清理顺序 | 一个 `ctx.effect(async function* …)`，依次：打开文件并 `yield` 关闭函数；`yield` 最终 flush；启动定时器并 `yield` 清除函数；**最后** `yield ctx.provide(...)` | `dsh_lifecycle_and_effects.md` 中的推荐写法；cordis-styling-nitpicking SKILL.md:45-55 |
| 事件隔离 | `ctx.events.dispatch('emit', …)` 加逐个 try/catch | 仿照 pipeline.ts:419-439 |
| 配置 | `path` 是普通字段，修改会重启；`flushIntervalMs`；`tag` 标为 `.volatile()`，修改就地生效 | 05-config.zh.md:70-93；entry.ts:155-211 |
| 写入串行化 | `flush()` 串在一条 promise 链上，定时 flush 和最终 flush 不会交错 | 本文设计 |

逆序回卷时的实际顺序：
1. 撤下服务，等依赖方卸载完毕（消费方在这一步写入 `audit-closing`）。
2. 清除定时器。
3. 执行最终 flush。
4. 关闭文件。

**源码（原样，即实际运行的版本）**

```ts title="lab-harness/scripts/acme/log.ts"
// Shared relative-time logger for the acme-ledger lab.
const t0 = performance.now()
export function log(...args: unknown[]): void {
  console.log(String(Math.round(performance.now() - t0)).padStart(5), ...args)
}
```

```ts title="lab-harness/scripts/acme/acme-ledger.ts"
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

```ts title="lab-harness/scripts/acme/acme-ledger-memory.ts"
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

```ts title="lab-harness/scripts/acme/acme-ledger-audit.ts"
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

```ts title="lab-harness/scripts/acme/acme-ledger-tail.ts"
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

**组合方式**：用真实的 Loader 以程序方式创建条目（`root.loader.create/update/remove`），这样 volatile 更新走的是 `Entry.update → _commitVolatile` 这条真实路径。下面是等价的 `cordis.yml`，仅供对照，本文没有用 YAML 运行这一版：

```yaml
- name: '@deepseek-ai/dsh-session-projection'
- name: '@deepseek-ai/dsh-system-prompt'
- name: '@deepseek-ai/dsh-tools'
- name: '@deepseek-ai/dsh-tool-executor-native'
- id: acme-ledger
  name: './acme/acme-ledger.ts'
  config:
    path: ./ledger.jsonl
    flushIntervalMs: 50
    tag: v1
- name: './acme/acme-ledger-audit.ts'
- name: './acme/acme-ledger-tail.ts'
  config:
    faulty: true
```

驱动脚本：

```js title="lab-harness/scripts/run-acme.mjs"
// Drive the acme-ledger plugins on the real dsh harness packages through the real Cordis Loader.
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import LoggerConsole from '@deepseek-ai/cordis-plugin-logger-console'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { log } from './acme/log.ts'

const STATES = ['PENDING', 'LOADING', 'ACTIVE', 'FAILED', 'DISPOSED', 'UNLOADING']
const sleep = ms => new Promise(r => setTimeout(r, ms))
const dir = await mkdtemp(join(tmpdir(), 'acme-ledger-'))
const fileA = join(dir, 'a.jsonl'), fileB = join(dir, 'b.jsonl')

const root = new Context()
await root.plugin(LoggerConsole)
root.baseUrl = pathToFileURL(import.meta.dirname + '/').href
const loaderFiber = root.plugin(Loader)
await loaderFiber
const L = root.loader
const ids = {}
const create = async (key, name, config) => { ids[key] = await L.create({ name, config }) }
const fiberOf = key => L.resolve(ids[key]).fiber
const states = (...keys) => keys.map(k => `${k}=${STATES[fiberOf(k)?.state] ?? 'none'}`).join(' ')
const removeAndWait = async (key) => {
  const fiber = fiberOf(key); L.remove(ids[key])
  while (fiber.state !== 4) await sleep(1)
}
const call = async (name, args = {}) => {
  const r = await root.tools.execute({ calls: [{ type: 'tool-call', id: `c-${name}-${Date.now()}`, name, arguments: JSON.stringify(args) }], signal: new AbortController().signal })
  return r.results[0].result.content.map(b => b.text).join('')
}
const dumpFile = async (label, path) => {
  const lines = (await readFile(path, 'utf8').catch(() => '')).trim().split('\n').filter(Boolean).map(l => JSON.parse(l))
  log(`${label}: ${lines.map(e => `${e.kind}${e.tool ? ':' + e.tool : ''}@${e.owner}/${e.tag}`).join(' | ')}`)
}

log('=== 1. consumers first, no provider')
await create('tail', './acme/acme-ledger-tail.ts', { faulty: true })
await create('audit', './acme/acme-ledger-audit.ts', {})
await sleep(50)
log(states('tail', 'audit'))

log('=== 2. harness rows (session-projection, system-prompt, tools, native executor, greet)')
await create('projection', '@deepseek-ai/dsh-session-projection', {})
await create('prompt', '@deepseek-ai/dsh-system-prompt', {})
await create('tools', '@deepseek-ai/dsh-tools', {})
await create('executor', '@deepseek-ai/dsh-tool-executor-native', {})
await sleep(50)
await create('greet', '../t07/greet-tool.ts', {})
await sleep(50)
log(states('tools', 'executor', 'audit'), '(audit still waits for acmeLedger)')

log('=== 3. provider arrives')
await create('ledger', './acme/acme-ledger.ts', { path: fileA, flushIntervalMs: 50, tag: 'v1' })
await sleep(50)
log(states('ledger', 'audit', 'tail'))
log('tools visible:', root.tools.schemas().map(s => s.name).join(','))
const assembly = await root.systemPrompt.assemble({})
log('prompt sections:', assembly.sections.map(s => s.name ?? s).join(','))

log('=== 4. tool calls flow into the ledger; flush events reach tail (one tail listener throws)')
log('greet ->', await call('greet', { name: 'Ledger' }))
log('ledger_stats ->', await call('ledger_stats'))
await sleep(120)
log('ledger_stats ->', await call('ledger_stats'))

log('=== 5. volatile config update (tag only)')
const t5 = fiberOf('ledger')
await L.update(ids.ledger, { config: { path: fileA, flushIntervalMs: 50, tag: 'v2' } })
log('same fiber object:', fiberOf('ledger') === t5, states('ledger', 'audit'))
await call('greet', { name: 'Volatile' })
await sleep(100)

log('=== 6. ordinary config update (path A -> B): provider restarts, consumer restarts')
await L.update(ids.ledger, { config: { path: fileB, flushIntervalMs: 50, tag: 'v2' } })
await sleep(80)
log(states('ledger', 'audit', 'tail'))
await call('greet', { name: 'B' })
await sleep(100)
await dumpFile('file A', fileA)
await dumpFile('file B', fileB)

log('=== 7. provider swap: unload file provider, load memory provider')
await removeAndWait('ledger')
log(states('audit'), 'after file provider removed')
await dumpFile('file B', fileB)
await create('memory', './acme/acme-ledger-memory.ts', {})
await sleep(50)
log(states('memory', 'audit'))
log('ledger_stats ->', await call('ledger_stats'))
await removeAndWait('memory')
log(states('audit'))

log('=== 8. full dispose (dispose the Loader fiber that owns every entry)')
await create('ledger2', './acme/acme-ledger.ts', { path: fileB, flushIntervalMs: 50, tag: 'v3' })
await sleep(50)
log(states('ledger2', 'audit'))
const before = process.getActiveResourcesInfo().filter(r => r !== 'TTYWrap')
await loaderFiber.dispose()
await sleep(50)
const after = process.getActiveResourcesInfo().filter(r => r !== 'TTYWrap')
log('active resources before:', JSON.stringify(before))
log('active resources after :', JSON.stringify(after))
await dumpFile('file B', fileB)
log('done')
```

运行命令：

```sh
cd lab-harness && node scripts/run-acme.mjs > out/run-acme.txt
```

```text title="node scripts/run-acme.mjs（实际输出；第一列为相对毫秒）"
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

逐段解读输出：

1. **消费方等待提供方**：一开始 `audit=PENDING`，因为缺 `acmeLedger`；`tail=ACTIVE`，因为它没有顶层 `inject`。harness 各行就绪后，audit 仍是 PENDING。`acme-ledger` 一 provide，audit 立即打印 `apply (backend=file)`，`ledger_stats` 出现在 `tools.schemas()` 里，`acme-ledger:audit` 出现在 `assemble().sections` 里。第 2 段里那行 `tool replied` 来自 t07 的 greet-tool 自己发起的调用。
2. **事件流动**：`greet` 和 `ledger_stats` 的结果经 `tools/result` 记入账本。定时 flush 发出 `acme-ledger/flushed`，tail 收到。`faulty: true` 时，tail 的第一个监听器每次都抛错，异常被 `[acme-ledger] listener failed (contained)` 记录后吞掉，第二个监听器照常执行。`ledger_stats` 第一次返回 `2 appended`，第二次返回 `3 appended`，因为工具自身那次调用的 `tools/result` 要等它返回之后才入账。
3. **volatile 更新**：只改 `tag`，输出 `volatile update [["tag"]] -> tag=v2 (no restart)` 和 `same fiber object: true`。之后的记录 tag 变成 `v2`，文件 A 里能看到 `tool:greet@…/v2`。
4. **普通字段更新（path 从 A 改到 B）**：provider 重启，顺序是 `[audit] final entry appended`、`timer cleared`、最终 flush（tail 收到 `flushed 1 (final)`）、`file closed a.jsonl`，然后新实例打印 `apply path=b.jsonl`，audit 重新激活。文件 A 的最后一条是 `audit-closing`。
5. **卸载提供方、更换实现**：移除 file provider 后，audit 回到 PENDING，文件 B 的最后一条是 `audit-closing`。加载 `acme-ledger-memory` 后，audit 重新激活，`ledger_stats` 返回 `memory: …`。再移除 memory provider，它丢弃的 3 条内存记录里有 `audit-closing`，说明最后一条记录是在服务撤回之前写入的。
6. **整体 dispose**（dispose 拥有所有条目的 Loader Fiber）：`[audit] final entry appended` 仍然出现在 `timer cleared` 和 `file closed` 之前，文件 B 末尾有 `audit-closing@…/v3`。`process.getActiveResourcesInfo()` 从 `["PipeWrap","Timeout"]` 变成 `["PipeWrap"]`，`Timeout` 消失了，没有文件句柄残留；`PipeWrap` 是 stdout 被重定向到文件造成的。这一次 tail 已经和其他条目一起被卸载，所以最终 flush 没有 `[tail]` 输出。

**附加实测 1：调用方归属**（`scripts/caller-context.mjs`，直接调用 `ctx.plugin`，不经过 Loader）

```js title="lab-harness/scripts/caller-context.mjs"
// Who owns what: effects created by service methods land on the CALLER's fiber.
import { Context } from '@deepseek-ai/cordis'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import * as sessionProjection from '@deepseek-ai/dsh-session-projection'
import * as systemPrompt from '@deepseek-ai/dsh-system-prompt'
import * as tools from '@deepseek-ai/dsh-tools'
import * as nativeExecutor from '@deepseek-ai/dsh-tool-executor-native'
import * as ledger from './acme/acme-ledger.ts'
import * as audit from './acme/acme-ledger-audit.ts'
import * as tail from './acme/acme-ledger-tail.ts'

const sleep = ms => new Promise(r => setTimeout(r, ms))
const pick = m => m.default ?? m
const root = new Context()
for (const p of [sessionProjection, systemPrompt, tools]) await root.plugin(pick(p))
await root.plugin(nativeExecutor)
const dir = await mkdtemp(join(tmpdir(), 'acme-cc-'))
// Direct ctx.plugin (no Loader): the Config schema still resolves volatile refs.
const ledgerFiber = root.plugin(ledger, { path: join(dir, 'x.jsonl') })
const auditFiber = root.plugin(audit)
const tailFiber = root.plugin(tail, {})
await sleep(50)

const labels = f => f.getEffects().map(e => e.label ?? e.name ?? '?')
console.log('audit fiber effects:', JSON.stringify(labels(auditFiber)))
console.log('tail fiber effects :', JSON.stringify(labels(tailFiber)))
console.log('watchers before tail dispose:', root.acmeLedger.stats().watchers)
root.acmeLedger.append({ kind: 'from-root' })   // stamped with the caller's fiber name
await tailFiber.dispose()
console.log('watchers after tail dispose :', root.acmeLedger.stats().watchers)
console.log('tools before audit dispose:', root.tools.schemas().map(s => s.name))
await auditFiber.dispose()
console.log('tools after audit dispose :', root.tools.schemas().map(s => s.name))
const asm = await root.systemPrompt.assemble({})
console.log('sections after audit dispose:', asm.sections.map(s => s.name).join(','))
await ledgerFiber.dispose()
```

```text title="node scripts/caller-context.mjs（实际输出）"
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

要点：
- audit Fiber 的 effect 里有 `tools.register()` 和 `systemPrompt.section()`，说明 ToolsService 和 SystemPrompt 的方法把 effect 登记在了调用方身上。
- tail 的 `watch()` 订阅落在 `ctx.inject` 创建的子 Fiber 上，tail 自己的 effect 列表里只看到 `ctx.plugin()`。tail 卸载后，watchers 数量从 1 变成 0。
- 从 root 调用 `append()` 时，记录的署名是 `root`。

**附加实测 2：在 disposer 里重新读取 `ctx.acmeLedger`。** `scripts/acme/audit-lazy.ts` 由 audit 插件用 `sed` 生成，只把最终写入改成 `ctx.acmeLedger.append({ kind: "audit-closing" })`，用 `scripts/lazy-access.mjs` 运行：

```js title="lab-harness/scripts/lazy-access.mjs"
// Variant: the consumer re-reads ctx.acmeLedger inside its disposer instead of using the captured proxy.
import { Context } from '@deepseek-ai/cordis'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import * as sp from '@deepseek-ai/dsh-session-projection'
import * as sys from '@deepseek-ai/dsh-system-prompt'
import * as tools from '@deepseek-ai/dsh-tools'
import * as ledger from './acme/acme-ledger.ts'
import * as audit from './acme/audit-lazy.ts'
const pick = m => m.default ?? m
const root = new Context()
for (const p of [sp, sys, tools]) await root.plugin(pick(p))
const path = join(await mkdtemp(join(tmpdir(), 'acme-lazy-')), 'x.jsonl')
const lf = root.plugin(ledger, { path })
root.plugin(audit)
await new Promise(r => setTimeout(r, 50))
console.log('-- provider dispose')
await lf.dispose()
console.log('file:', (await readFile(path, 'utf8')).trim().split('\n').map(l => JSON.parse(l).kind).join(','))
```

```text title="node scripts/lazy-access.mjs（实际输出）"
    5 [acme-ledger] apply path=/tmp/acme-lazy-lgUftw/x.jsonl tag=v1
    7 [audit] apply (backend=file)
    8 [acme-ledger] service published
-- provider dispose
   71 [audit] final entry appended
   71 [acme-ledger] timer cleared
   71 [acme-ledger] final flush done (appended=2, flushed=2)
   72 [acme-ledger] file closed /tmp/acme-lazy-lgUftw/x.jsonl
file: audit-start,audit-closing
```

在最后 provide 的写法下，消费方的 disposer 里重新读一次 `ctx.acmeLedger` 也能拿到服务。`provide()` 的撤回步骤虽然先执行了 `delete this.store[...]`，但要等 `impl.dependents` 清空之后，才把它从 `fiber.provisions` 里删掉 — [vendor/cordis/src/reflect.ts:333-347](vendor/cordis/src/reflect.ts)。

```ts title="vendor/cordis/src/reflect.ts:333-347"
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
```

**哪些是真实运行的，哪些是模拟的**
- 真实：Cordis 4.2.0-alpha.1；Loader、Include、logger-console；`dsh-tools`、`dsh-system-prompt`、`dsh-session-projection`、`dsh-tool-executor-native` 0.2.3-alpha.2。工具调用走的是真实的 `ToolExecutionPipeline`。
- 模拟：没有 LLM、agent loop 和会话。模型发起的调用由脚本直接调用 `root.tools.execute(...)` 代替，没有 `agent`、`toolSet`、`log`，所以作用域过滤和会话日志都没有参与。`root.systemPrompt.assemble({})` 不带 session，不涉及 section admission。

### Inferences
- 在 Loader 驱动的三种卸载路径上（配置重启、单条移除、整棵树 dispose），生成器 effect 里最后 provide 的写法都保住了消费方的最后一次写入。整棵树 dispose 这次也没丢，但 lifecycle 一文引用的规则指出，外层整体卸载时这个写法“不是完整修复”。本实验的消费方清理只花了 10 ms，不能由此推出耗时更长的清理也安全。
- 异步生成器 effect 在 `await open()` 和 `yield ctx.provide()` 之间还没有发布服务，这段时间消费方保持 PENDING。这正是想要的效果：服务一旦可见，资源一定已经就绪。
- flush 的串行化很关键。撤回服务时，可能正有一次定时 flush 在进行；最终 flush 排在它之后，关闭文件又排在最终 flush 之后。

### Gaps
- 没有演示错误写法的对照组（资源 effect 加子插件 Service，会丢失最后一次写入），因为 `dsh_lifecycle_and_effects.md` 的 race.mjs 已经实测过。
- 没有测试修改 `flushIntervalMs`。它是普通字段，预期会像 `path` 一样触发重启。
- 没有测试消费方清理耗时较长、或多个消费方并发卸载时，整棵树 dispose 的表现。

## 编写清单与常见坑

### Takeaway
写一个“提供服务、发事件、持有外部资源、有消费方”的 dsh 插件，按下面的步骤做。最容易出错的地方依次是：
1. 组合里漏掉 harness 的间接依赖（sessionProjections、executor）。
2. 清理顺序不对。
3. 监听器异常没有隔离。
4. 服务方法替调用方登记东西时，没把 effect 挂在调用方身上。

### Cited Findings
**步骤**
1. 起名：服务名、事件前缀、插件名都带上同一个独有前缀（03-services.zh.md:92-94；04-events.zh.md:44）。
2. 写 Definition：抽象类或接口，加上 `declare module '@deepseek-ai/cordis' { interface Context {…}; interface Events {…} }`，并在事件的注释里写明 `@mode`，参考 tools/src/events.ts:6-58 的写法。
3. 如果方法要替调用方登记东西：装上 `Service.tracker`（或者继承 `Service`），方法里用 `this.ctx.effect(…, 'label')` 登记，并返回 disposer（definition-registry.ts:10-15；store.ts:226-262）。
4. 写 Provider：`ctx.effect(async function* () { …获取资源，并 yield 释放函数…; yield ctx.provide(name, impl) })`，服务**最后**发布。需要的话，在资源和服务之间 `yield` 一个最终 flush（参考本文的 acme-ledger.ts）。
5. 发事件：通知类事件用 `emit`，并用 `ctx.events.dispatch('emit', [...])` 取出回调逐个 try/catch（pipeline.ts:419-439；events.ts:182-210）。拦截类事件用 `waterfall`，并要求只做观察的监听器调用 `next()`（04-events.zh.md:136-138）。
6. 配置：`Config` 用 schemastery 定义。每次操作时现读的字段加 `.volatile()`，读取时用 `.get()`；`loader/volatile-update` 只作通知用（05-config.zh.md:70-93）。
7. 写 Consumer：顶层 `inject` 列出硬依赖。工具插件至少要有 `['tools']`，要提供 prompt section 就再加 `'systemPrompt'`。可选的部分用 `ctx.inject([...], cb)` 或 `ctx.get()`（03-services.zh.md:80-90）。最后一次写入放在 async disposer 里。
8. 工具：`defineTool` 声明 `parameters`、`output.schema`、`output.render`。DSL 表达不了的约束在 `execute` 里检查，不满足就抛 `ToolArgsError`；可预期的失败抛带 `code` 的 `HarnessError`（adding-a-tool.zh.md:43-47）。
9. 组合：`dsh-session-projection`、`dsh-system-prompt`、`dsh-tools`、一个 executor（`dsh-tool-executor-native`），再加上你的插件。base bundle 已经包含前四项（cordis.patch.yml:173-174, 526-543）。
10. 验证：
    - 遍历 `ctx.registry`，查看 Fiber 状态和缺失的服务。
    - 用 `fiber.getEffects()` 查看 effect 标签。
    - 卸载后用 `process.getActiveResourcesInfo()` 检查有没有残留资源。
    - 本地实验时加载 `cordis-plugin-logger-console`，否则看不到 Loader 的加载错误。

**常见坑**（每一条都在本文的实测或源码里出现过）

| 症状 | 原因 | 出处 |
|---|---|---|
| 按教程 07 的组合运行，没有输出，exit 0 | `systemPrompt` 依赖 `sessionProjections`，整条链停在 PENDING | system-prompt/src/index.ts:487；实测 A |
| 报 `TOOL_EXECUTOR_UNAVAILABLE` | 没有 executor 插件，或者 executor 比调用方晚激活 | tools/src/index.ts:358-378；executor.ts:96-102；实测 B、C |
| `systemPrompt.assemble()` 抛出 `TOOL_EXECUTOR_UNAVAILABLE` | schema provider 要经过 `selectExecutor()` | tools/src/index.ts:113-117；no-executor.mjs |
| 门禁监听器拿到的参数类型不对 | 参数校验在工具主体里，晚于 pre-execute 和 guard | schema.ts:598-602；pipeline.ts:191-234 |
| 一个监听器抛错，后面的监听器都没执行 | 普通 `ctx.emit` 不隔离异常 | cordis events.ts:207-210 |
| 调用方卸载后，订阅回调还留在服务里 | 服务方法用服务自己的 ctx 登记，或者根本没登记成 effect | definition-registry.ts:10-15；caller-context.mjs |
| 消费方的最后一次写入失败 | 资源 effect 和服务发布是两个并发执行的兄弟 effect | lifecycle 一文的 race.mjs；SKILL.md:45-55 |
| 本地 `.ts` 插件被 Loader 跳过，没有任何提示 | Node 的 strip-only 模式不支持 parameter property 等语法；没有 logger 时看不到错误 | 本文第一次运行时状态为 `ledger=none` |
| 第三方 section 的位置不好控制 | `getSectionOrder()` 只认内置名字，只能自己选数字 | system-prompt/src/index.ts:191-231 |
| `register()` 直接抛 TypeError | 缺少 `output.render`、`timeoutMs` 不合法，或者用了 `run_code` 这个名字 | tools/src/index.ts:128-147 |

### Inferences
- 本文对官方文档的修改建议（没有提交）：教程 07 的 YAML 应该补上 `@deepseek-ai/dsh-session-projection` 和 `@deepseek-ai/dsh-tool-executor-native`；演示调用不应该放在 greet-tool 的 `apply` 里，要么就等 executor 就绪后再调用。

### Gaps
- 没有用 `tsc` 检查自写插件的类型，尤其不确定 `Schema.Volatile<string>` 这个类型名是否存在、`Schema<Config>` 的写法是否正确。运行只依赖 type stripping。
- 没有在完整的 base profile（`dsh web` 或 `dsh --profile headless`）里加载这些插件。
