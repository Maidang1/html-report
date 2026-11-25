# dsh 插件配置、组合（cordis.yml / Loader / Include）与 HMR：源码笔记（dsh @ 4b399c3）

范围：Cordis 教程第 5 章「配置」、第 6 章「组合与 HMR」所涉及的真实实现。已有文章（`deepseek/dsh_plugin_guide.md`、`deepseek/dsh_lifecycle_and_effects.md`）讲过的 Fiber 生命周期、`ctx.effect`、`fiber.update()` 和 volatile 基础，这里不再重复，重点放在 Loader、Include、HMR 的实现细节。

引用约定：所有 `path:line` 都相对 dsh 仓库根（commit `4b399c3`）。代码摘录由脚本 `sed -n a,bp` 原样复制，构建脚本是 `scratchpad/lab-config/notes/build.py`。

源码地图（commit 4b399c3）：
- Cordis 核心：`vendor/cordis/src/fiber.ts`、`events.ts`、`registry.ts`（`@deepseek-ai/cordis` 4.2.0-alpha.1）
- Loader：`vendor/loader/src/{index,config/entry,config/group,config/tree,config/utils,config/diff,config/isolate}.ts`（`@deepseek-ai/cordis-plugin-loader` 1.2.0-alpha.1，上游为 `@cordisjs/plugin-loader` 1.0.0-rc.9，见 `vendor/README.md` 的 Manifest 表）
- Include：负责读 cordis.yml、处理 `!!js` 标签和 patch，源码在 `vendor/include/src/{index,patch,journal}.ts`
- Group：`vendor/group/src/index.ts` 只有 3 行，作用是转导出 Loader 内的 `Group`
- dsh HMR：`packages/boot/hmr/src/{index,watch-config,module-files,package-manifest,error}.ts`（`@deepseek-ai/dsh-hmr` 0.2.3-alpha.2）。`vendor/hmr`（`@deepseek-ai/cordis-plugin-hmr`）是上游 HMR 的 vendored 副本。dsh 的 bundle 实际挂载的是 `@deepseek-ai/dsh-hmr`（`packages/bundle/base/cordis.patch.yml:27-31`）
- 配置层叠：`packages/boot/app-boot/src/{profile,profile-context,index}.ts`

实验环境：在 `scratchpad/lab-config/` 里用 `npm i --ignore-scripts --legacy-peer-deps` 安装了与仓库版本一致的已发布包：`@deepseek-ai/cordis@4.2.0-alpha.1`、`cordis-plugin-loader@1.2.0-alpha.1`、`cordis-plugin-include@1.2.0-alpha.1`、`cordis-plugin-group@1.2.0-alpha.1`、`cordis-plugin-timer@1.3.0-alpha.1`、`schemastery@3.20.0-alpha.1`、`dsh-hmr@0.2.3-alpha.2`，以及 dsh-hmr 运行时需要 import 的 `dsh-app-boot`、`dsh-home-paths`、`dsh-launch-environment`、`dsh-error`（均为 0.2.3-alpha.2）。安装后用 grep 确认，已发布 lib 里包含 `equalExceptVolatile`/`_commitVolatile`（Loader）和 `_assignIds`/`applyEntryPatches`（Include），与 4b399c3 源码属于同一代实现。Node 版本为 v24.22.2。下文所有「实验」输出都是真实运行结果，路径前缀替换成了 `<lab-config>`。FiberState 是 `const enum`（`vendor/cordis/src/fiber.ts:177-184`），发布产物里没有导出，所以实验脚本按源码顺序自己写了一份状态名数组。

---

## Q1 插件怎样声明和接收配置：Config 的发现、校验、默认值和失败表现

### Takeaway
插件的 schema 就是插件对象上的 `Config` 属性，在 `registry.plugin()` 创建 runtime 时读取（`vendor/cordis/src/registry.ts:326`）。校验由 `internal/config` waterfall 的内置监听器完成（`vendor/cordis/src/events.ts:156-159`）。它调用 Standard Schema 的 `validate`，不支持异步校验（`fiber.ts:80-92`），默认值由 Schemastery 补齐。校验失败时抛出 `ValidationError`，fiber 进入 FAILED，`apply` 不会运行。fiber 来自 Loader 条目时，Loader 会改写错误消息，标出 `cordis.yml` 的文件行列号（`vendor/loader/src/index.ts:107-120`）。

### Cited Findings
- **Config 怎么被发现**：`RegistryService.plugin()` 第一次遇到某个 callback 时创建 runtime，并读取 `Config: plugin.Config`（`vendor/cordis/src/registry.ts:322-330`）。这里的 plugin 指 Loader `unwrapExports()` 之后的结果：先取 `exports.default ?? exports`，再处理一次 esbuild 的 `__esModule` 互操作（`vendor/loader/src/index.ts:229-237`）。所以能被识别的写法有两种：不写 default，用具名导出 `Config` 加 `apply`，这时命名空间对象本身就是插件；或者 default 导出一个带 `static Config` 的类或对象。

`vendor/cordis/src/registry.ts:316-334`

```ts
  plugin(plugin: Plugin, config?: any, getOuterStack = buildOuterStack()) {
    // check if it's a valid plugin
    const callback = this.resolve(plugin)
    if (!callback) throw new Error('invalid plugin, expect function or object with an "apply" method, received ' + typeof plugin)
    this.ctx.fiber.assertActive()

    let runtime = this._internal.get(callback)
    if (!runtime) {
      let name = plugin.name
      if (name === 'apply') name = undefined
      runtime = { name, callback, fibers: new DisposableList(), Config: plugin.Config }
      this._internal.set(callback, runtime)
    }

    const fiber = new Fiber(this.ctx, config, Inject.resolve(plugin.inject), runtime, getOuterStack)
    const wrapped = Object.create(fiber) as Fiber & PromiseLike<Fiber>
    wrapped.then = (onFulfilled, onRejected) => {
      return fiber.await().then(onFulfilled, onRejected)
    }
```

`vendor/loader/src/index.ts:229-237`

```ts
  /** Normalize ESM/CJS/default export shapes before applying a plugin. */
  unwrapExports(exports: any) {
    if (isNullable(exports)) return exports
    exports = exports.default ?? exports
    // https://github.com/evanw/esbuild/issues/2623
    // https://esbuild.github.io/content-types/#default-interop
    if (!exports.__esModule) return exports
    return exports.default ?? exports
  }
```

- **校验入口**是 `resolveConfig(runtime, config)`：runtime 没有 Config 时原样返回配置；有 Config 时调用 `runtime.Config['~standard'].validate(config)`。如果它返回 Promise，就抛 `TypeError('Async config validation is not supported')`；如果有 issues，就抛 `ValidationError`（`vendor/cordis/src/fiber.ts:80-92`）。`resolveConfig` 由 EventsService 构造函数登记的 `internal/config` 监听器调用（`vendor/cordis/src/events.ts:156-159`）。

`vendor/cordis/src/fiber.ts:80-92`

```ts
export function resolveConfig(runtime: Plugin.Runtime, config: any) {
  if (!runtime.Config) return config
  // TODO: async validation
  const result = runtime.Config['~standard'].validate(config)
  if ('then' in result) {
    throw new TypeError('Async config validation is not supported')
  }
  if (result.issues) {
    throw new ValidationError(result.issues)
  } else {
    return result.value
  }
}
```

`vendor/cordis/src/events.ts:156-159`

```ts
    this.on('internal/config', (fiber, next) => {
      const config = next()
      return fiber.runtime ? resolveConfig(fiber.runtime, config) : config
    })
```

- **校验时机是激活时**：`_runner.execute` 调用 `this.resolveConfig(this.config)` 并把结果存进 `_effectiveConfig`，然后执行 `new runtime.callback(ctx, config)` 或 `runtime.callback(ctx, config)`（`fiber.ts:275-288`）。所以 PENDING（依赖未就绪）的插件**还没有校验配置**。`Fiber.resolveConfig()` 的实现就是跑一遍 `internal/config` waterfall（`fiber.ts:698-701`），注释写明此时钩子用到的 required injection 必须可用（`fiber.ts:690-697`）。

`vendor/cordis/src/fiber.ts:272-291`

```ts
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

`vendor/cordis/src/fiber.ts:690-701`

```ts
  /**
   * Evaluate and validate a candidate using this fiber's `internal/config` hooks and runtime schema.
   * Does not publish the result, replace raw input, or commit running volatile references.
   * Required injections must be available when a hook uses them.
   * @param config Raw candidate input.
   * @returns The candidate's resolved config, retaining the schema's result identity.
   * @throws {ValidationError} when the runtime schema rejects the candidate; hook errors propagate.
   */
  resolveConfig(config: unknown): unknown {
    const fiber = this.ctx.fiber
    return fiber.context.waterfall('internal/config', fiber, () => config)
  }
```

- **`ValidationError` 的格式**：消息是 `invalid config:` 后跟若干行 `  - <message> (at <dotted path>)`（`fiber.ts:19-36`）。`ValidationError.is()` 通过全局 symbol `Symbol.for('ValidationError')` 判断，因此另一份 Cordis 副本抛出的同类错误也能识别（`fiber.ts:16, 43-45, 68-70`）。

`vendor/cordis/src/fiber.ts:19-36`

```ts
export class ValidationError extends TypeError {
  name = 'ValidationError'

  /**
   * Build the aggregated message from schema issues.
   *
   * @param issues — the standard-schema issues, one message line each.
   */
  constructor(public issues: readonly StandardSchemaV1.Issue[]) {
    super(`invalid config:\n` + issues.map(issue => {
      if (issue.path?.length) {
        return `  - ${issue.message} (at ${ValidationError.formatPath(issue.path)})`
      } else {
        return `  - ${issue.message}`
      }
    }).join('\n'))
  }
```

- **Loader 会改写错误位置**：Loader 用 `prepend: true` 登记一个 `internal/config` 监听器，包在内置校验外层。它捕获到 `ValidationError`，并且确认这次校验的 source 就是条目的 `options.config` 时，调用 `entry.traceValidation(error)`：每条 issue 换成 `cordis.yml` 的 `file:line:col`，stack 改用条目链重写（`vendor/loader/src/index.ts:107-120`；`vendor/loader/src/config/entry.ts:229-236`；`relocate`/`formatIssues` 见 `config/utils.ts:80-96`）。运行时通过 `fiber.update()` 直接传入的配置没有写在文件里，所以不改写（`index.ts:107-109` 注释）。

`vendor/loader/src/index.ts:104-131`

```ts
    // the source config each entry fiber last resolved
    const sources = new WeakMap<Fiber, any>()

    // Registered before the built-in validation listener, so validation
    // failures can be traced to the config source. A config passed in at
    // runtime is not written anywhere yet, so its failures are left as is.
    ctx.on('internal/config', (fiber, next) => {
      if (!fiber.entry || fiber.parent.fiber?.entry === fiber.entry) return next()
      try {
        return next()
      } catch (error) {
        if (ValidationError.is(error) && sources.get(fiber) === fiber.entry.options.config) {
          fiber.entry.traceValidation(error)
        }
        throw error
      }
    }, { prepend: true })

    // Registered after the built-in validation listener, so interpolation runs
    // on the source config and validation sees the interpolated result.
    ctx.on('internal/config', (fiber, next) => {
      const config = next()
      if (!fiber.entry || fiber.parent.fiber?.entry === fiber.entry) return config
      sources.set(fiber, config)
      // Tree carriers preserve the expressions owned by their child entries.
      if (fiber.runtime?.callback[EntryGroup.key]) return config
      return interpolate(fiber.ctx, config)
    })
```

- **失败后的状态**：`_reload()` 捕获执行期异常（包括校验异常），先 `ctx.logger.error(reason)`，再设置 `_error`，并把 epoch 置为 INACTIVE。之后 `_getState()` 看到 `_error` 就返回 FAILED（`fiber.ts:603-608, 707-729`）。FAILED 不会自动恢复：`_setEpoch` 遇到 `_error` 直接 return，注释写的是「a failed fiber only recovers through update(), which clears _error」（`fiber.ts:655-656`），`_error` 由 `fiber.update` 在 `fiber.ts:812` 清除。`vendor/README.md` V25 也写道：「Failed fibers require an explicit update after correcting their input or dependencies」。
- **对 ACTIVE fiber 调用 `fiber.update()` 时校验是同步的**：`loaded` 为真时先执行 `fiber.resolveConfig(config)`，非法配置当场同步抛错，抛错发生在 `internal/commit`（持久化）之前（`fiber.ts:805-811`）。
- **实验 E1：默认值、`!!js`、失败表现，以及 default 导出导致 Config 丢失**。脚本和输入如下：

`lab-config/scripts/e1/demo.mjs`

```js
import Schema from '@deepseek-ai/schemastery'
export const name = 'config-demo'
export const Config = Schema.object({
  greeting: Schema.string().default('Hello'),
  targets: Schema.array(String).default(['world']),
})
export function apply(ctx, config) {
  console.log('[demo] apply config =', JSON.stringify(config))
}
```

`lab-config/scripts/e1/default-only.mjs`

```js
import Schema from '@deepseek-ai/schemastery'
// default export function + named Config: Config is lost by unwrapExports
export const Config = Schema.object({ n: Schema.number().required() })
export default function apply(ctx, config) { console.log('[default-only] apply config =', JSON.stringify(config)) }
```

`lab-config/scripts/e1/ok.yml`

```yaml
- id: a
  name: ./demo.mjs
  config:
    targets: [alpha, beta]
- id: b
  name: ./demo.mjs
  config:
    greeting: !!js process.env.DEMO_GREETING + ' / ' + typeof loader
- id: c
  name: ./default-only.mjs
```

`lab-config/scripts/e1/bad.yml`

```yaml
- id: bad
  name: ./demo.mjs
  config:
    targets: not-an-array
```

命令：`cd lab-config && node scripts/e1/run.mjs ok.yml`，输出：

```text
[demo] apply config = {"greeting":"Hello","targets":["alpha","beta"]}
[demo] apply config = {"greeting":"Hi-from-env / object","targets":["world"]}
[default-only] apply config = undefined
entry c2ec78cb:a: state=ACTIVE
entry c2ec78cb:b: state=ACTIVE
entry c2ec78cb:c: state=ACTIVE
```

命令：`node scripts/e1/run.mjs bad.yml`，输出（打印的是 `fiber.await()` 重新抛出的错误）：

```text
entry 2928a2d0:bad: state=FAILED
  ValidationError: invalid config:
  - $.targets expected array but got not-an-array (at targets, <lab-config>/scripts/e1/cordis.yml:4:14)
  stack:
  - $.targets expected array but got not-an-array (at targets, <lab-config>/scripts/e1/cordis.yml:4:14)
    at bad (<lab-config>/scripts/e1/cordis.yml:1:3)
```

  结论有四点。(1) 未提供的 `greeting`、`targets` 由 schema 默认值补齐。(2) `ValidationError` 的 issue 带上了 `cordis.yml:4:14`，stack 帧为 `at bad (cordis.yml:1:3)`，即条目所在行。(3) 出错条目状态为 FAILED，进程不退出，同一文件里的其他条目照常运行。(4) **`default-only.mjs` 用 `export default function apply` 搭配具名 `export const Config`，Config 被丢掉了**：`apply` 收到 `undefined`，必填字段 `n` 也没有被校验。原因是 `unwrapExports` 取的是 `exports.default`（`index.ts:232`）。
- 教程第 5 章的说法与源码一致：「Cordis 本身接受任意 Standard Schema 验证器，因此将普通对象导出为 `Config` 无法工作」（`docs/cordis-tutorial/05-config.zh.md:34`）。失败输出格式见 `05-config.zh.md:63-68`。教程中「本教程的启动器打印错误后以状态码 1 退出」描述的是教程启动器自己的行为，Loader 本身不会退出。

### Inferences
- 校验推迟到激活时才做，所以 PENDING 插件的配置错误要等依赖到齐后才会暴露。如果一个插件「没有报错」，先确认它是否根本没有激活。
- 默认值只有写在 schema 的 `.default()` 里才可靠。patch 层会整块替换 `config`（见 Q5），写在 bundle patch 里的「默认值」会被用户层整个覆盖。

### Gaps
- Schemastery 的具体报错文案（例如 `$.targets expected array but got not-an-array`）来自 `vendor/schemastery/src/index.ts`，没有逐条核对各类型的报错模板。

---

## Q2 Volatile 字段：Loader 里的实现路径，`loader/volatile-update` 什么时候触发、什么时候改为重启

### Takeaway
`Entry.update()` 先在原始配置层面比较新旧 options。只有同时满足以下条件才走 volatile 路径：唯一变化的键是 `config`；fiber 处于 ACTIVE；context 原型没有变化；`equalExceptVolatile(旧, 新, schema)` 为真，也就是除 schema 标记为 volatile 的固定路径外，其余部分没有差异。走这条路径时，`_commitVolatile()` 先用 `fiber.resolveConfig()` 完整解析一次候选配置（含 `!!js` 插值和校验），确认普通字段的有效值仍然深相等，再把新值写进运行中的引用，最后只向这一个 fiber 发出一次 `loader/volatile-update(paths)`（`vendor/loader/src/config/entry.ts:155-211`）。不满足条件时走普通流程：partial-dispose，然后 `_patchContext`，最终调用 `fiber.update(config, true)`，也就是重启。

### Cited Findings
- 判定逻辑（`entry.ts:155-171`）：

`vendor/loader/src/config/entry.ts:137-171`

```ts
  /** Merge new options, restart as needed, and persist through the parent tree. */
  async update(options: Partial<EntryOptions>, create = false, force = false) {
    const legacy = { ...this.options }

    // step 1: update options
    if (create) {
      this.options = options as EntryOptions
    } else {
      Entry.merge(this.options, options)
    }
    sortKeys(this.options)

    // step 2: execute
    if (this.disabled) {
      await this.fiber?.dispose()
      return
    }

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

- 提交与通知（`entry.ts:173-211`）分几种情况：
  - 有效配置里没有 volatile 引用：直接返回 true，视为已处理，不重启。
  - 候选配置解析失败：记一条 warn 并返回 true，运行中的引用保持不变。
  - 普通字段的有效值变了（例如 `!!js` 求值结果变了）：返回 false，退回普通流程。
  - 其余情况：只有值确实变化的路径才进入 `paths`；如果全部等值，就不发通知。

  事件通过 `Context.filter` 只派发给所属的 fiber（`entry.ts:202-205`）。监听器抛出的异常会被吞掉并记录日志，理由是「every value is already committed」（`entry.ts:206-209`）。

`vendor/loader/src/config/entry.ts:173-211`

```ts
  /**
   * Parse a volatile-only raw config change and commit its values into the running fiber's references.
   * An invalid candidate is logged and leaves the running references unchanged; the raw config stays retained for the next activation.
   * @returns `false` when an ordinary effective value changed, so the caller applies the ordinary update lifecycle.
   */
  private _commitVolatile(): boolean {
    const fiber = this.fiber!.ctx.fiber
    const refs = volatileEntries(fiber.effectiveConfig)
    if (!refs.length) return true
    const raw = this.options.config
    let candidate: unknown
    try {
      candidate = fiber.resolveConfig(raw)
    } catch (error) {
      this.ctx.logger.warn('volatile config update failed for %C', this.options.id)
      this.ctx.logger.warn(error)
      return true
    }
    if (!deepEqual(fiber.effectiveConfig, candidate, true)) {
      this.ctx.logger.debug('ordinary config values of %C changed with its volatile values; applying the ordinary update', this.options.id)
      return false
    }
    const paths = refs.flatMap(({ path, ref }) => {
      const source = path.reduce<unknown>((value, key) => Reflect.get(value as object, key), candidate) as Volatile<unknown>
      if (deepEqual(ref.get(), source.get(), true)) return []
      updateVolatile(ref, source)
      return [path]
    })
    if (!paths.length) return true
    const self: Context = Object.create(fiber.ctx)
    self[Context.filter] = (owner: Context) => owner.fiber === fiber
    try {
      fiber.ctx.emit(self, 'loader/volatile-update', paths)
    } catch (error) {
      // A listener failure must not fail the entry update; every value is already committed.
      this.ctx.logger.warn(error)
    }
    return true
  }
```

- 原始配置的比较在 `vendor/loader/src/config/diff.ts:17-45`，规则如下：
  - 遇到 `schema.meta.volatile`，直接视为相等。
  - 只沿 `type === 'object'` 且带 `dict` 的 schema 递归。
  - 缺失的对象节点按 schema 默认值比较。
  - `!!js` 表达式节点、未知字段、非 Schemastery schema，一律严格比较原值。

  比较过程不执行钩子，也不做校验。

`vendor/loader/src/config/diff.ts:17-45`

```ts
function equal(a: unknown, b: unknown, schema: Schema | undefined, ancestors: Set<Schema>): boolean {
  if (schema?.meta?.volatile) return true
  if (schema?.type !== 'object' || !schema.dict || ancestors.has(schema)) return deepEqual(a, b, true)
  const left = a ?? schema.meta?.default
  const right = b ?? schema.meta?.default
  if (!isRecord(left) || !isRecord(right)) return deepEqual(left, right, true)
  const { dict } = schema
  ancestors.add(schema)
  try {
    return Object.keys({ ...left, ...right }).every(key =>
      equal(left[key], right[key], Object.hasOwn(dict, key) ? dict[key] : undefined, ancestors),
    )
  } finally {
    ancestors.delete(schema)
  }
}

/**
 * Compare two raw configs, treating schema-declared volatile fields at fixed object paths as equal and absent objects as their schema default.
 * Schema backedges, expressions, unknown fields and opaque values keep strict raw equality; an absent or non-Schemastery schema compares everything raw.
 * @param previous - previous raw config.
 * @param next - next raw config.
 * @param schema - the plugin's config schema.
 * @returns Whether the configs differ at most in volatile fields, without evaluating expressions, validating config or modifying inputs.
 * @internal
 */
export function equalExceptVolatile(previous: unknown, next: unknown, schema: Plugin.Runtime['Config']): boolean {
  return isSchemastery(schema) ? equal(previous, next, schema, new Set()) : deepEqual(previous, next, true)
}
```

- 事件声明为 `'loader/volatile-update'(paths: readonly (readonly string[])[]): void`，`@mode emit`（`vendor/loader/src/index.ts:29-34`）。教程原文：「通知使用普通 `emit`，不等待资源重新配置完成」「不进入目标插件的 `internal/update`」（`docs/cordis-tutorial/05-config.zh.md:93`）。
- 以下情况不走 volatile 路径（依据 `05-config.zh.md:93` 和 `entry.ts:159-164`）：
  - 直接调用 `fiber.update()`：这条路径在 Cordis 核心，不经过 `Entry.update`。
  - 代码替换（HMR）和依赖替换。
  - 同一次编辑同时改了 `disabled`、`inject`、`isolate` 等其他键。
  - fiber 不在 ACTIVE 状态（例如 PENDING、FAILED）。

  `vendor/README.md` V22 的说法是：「a changed ordinary effective value falls back to the ordinary remount, and an invalid candidate is logged while the raw config stays retained for the next activation」。
- `rawConfig` 的 setter 只更新原始配置和修订号，不触发更新（`vendor/cordis/src/fiber.ts:670-679`）。volatile 路径会先执行 `this.fiber.rawConfig = this.options.config`（`entry.ts:163`），所以之后重启时用的是新的原始值。
- **实验 E2**（`lab-config/scripts/e2/run.mjs`）：插件 `scripts/e2/probe.mjs` 的 `level` 字段带 `.volatile()`。

`lab-config/scripts/e2/probe.mjs`

```js
import Schema from '@deepseek-ai/schemastery'
export const name = 'probe'
export const Config = Schema.object({
  label: Schema.string().default('?'),
  level: Schema.number().default(1).volatile(),
  mode: Schema.string().default('fast'),
})
export function apply(ctx, config) {
  const tag = `${config.label}`
  console.log(`  [${tag}] apply mode=${config.mode} level=${config.level.get()}`)
  ctx.on('loader/volatile-update', (paths) => console.log(`  [${tag}] volatile-update paths=${JSON.stringify(paths)} level=${config.level.get()}`))
  ctx.effect(() => () => console.log(`  [${tag}] dispose`))
}
```

命令：`node scripts/e2/run.mjs`。脚本按步骤改写 cordis.yml，每步之后调用 `include.refresh()`，相当于 HMR 发现文件变化后执行的动作。输出：

```text
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

  step 1 只改了 `keep` 的 volatile 字段（`level: 1→5`）：没有 dispose 和 apply，只收到一次 `volatile-update paths=[["level"]]`，`.get()` 读到 5。step 2 同时改了 `level` 和普通字段 `mode`：插件先 dispose 再 apply，没有收到 volatile-update。
- **实验 E3**（`scripts/e3/run.mjs`）给 volatile 字段写入非法值 `level: "oops"`。输出摘录（完整输出见 `lab-config/out/e3.txt`）：

```text
--- tree.update(p, {config:{label:p, level:"oops"}})  (volatile field gets invalid value)
  <log warn include> volatile config update failed for %C p
  <log warn include> invalid config:
  p state=ACTIVE effective level still=1
--- direct fiber.update with invalid ordinary value (active fiber)
  threw synchronously: ValidationError:   - $.mode expected string but got 42 (at mode)
```

  非法候选值只触发了 warn，fiber 保持 ACTIVE，引用里仍是旧值 1。但这次运行时编辑（`tree.update`）已经经由 Include 的 journal 写回文件，磁盘上的 `cordis.yml` 变成了 `level: oops`（见 `out/e3.txt` 末尾）。下次激活时，插件会因为这个值进入 FAILED。

### Inferences
- volatile 路径的提交是同步完成的，而 `loader/volatile-update` 是不等待的 emit。插件如果要基于新值重建资源（例如连接池），需要自己串行化并处理失败，Loader 既不会等待，也不会回滚。
- 「仅 volatile 变化」是在**原始文本**层面判断的。如果 volatile 字段写成 `!!js`，只要原始节点（`{__jsExpr}`）没变，就不算变化：环境变量变了而文件没变时，既不会通知，也不会重启（另见 Q3 和 E2 step 3）。

### Gaps
- 没有做实验验证「volatile 字段放在数组、union 内部会被拒绝」时 schema 给出的报错，这部分只引用了教程的描述（`05-config.zh.md:95`）。

---

## Q3 `!!js`：在哪里解析、在哪里求值、对哪些字段有效、能访问什么上下文

### Takeaway
`!!js` 是 Include 定义的 YAML 自定义标签，解析结果为 `{ __jsExpr: string }`（`vendor/include/src/index.ts:23-37`）。只有两处会求值：
1. 条目的 `config`：由 Loader 的第二个 `internal/config` 监听器在**激活或解析候选配置时**调用 `interpolate(fiber.ctx, config)` 递归求值，时间在内置校验之前（`vendor/loader/src/index.ts:122-131`）。
2. 条目的 `disabled`：每次挂载决策（`Entry.update`/`refresh`）时，用条目在 loader 一侧的 ctx 求值（`entry.ts:92-116`）。

求值器是 `new Function('ctx','expr','with (ctx) { return eval(expr) }')`（`config/utils.ts:3-9`）。因此表达式里可以写 `ctx.xxx`，也可以直接写 ctx 上的属性名（例如 `loader`），`process` 等全局对象同样可用。`name`、`id`、`inject`、`isolate` 等其他元数据不会被求值，`validateOptions` 只允许 `disabled` 取表达式（`utils.ts:107-110, 134`）。

### Cited Findings
- YAML 标签定义：

`vendor/include/src/index.ts:23-37`

```ts
const JsExpr: ScalarTag = {
  tag: 'tag:yaml.org,2002:js',
  identify: isJsExpr,
  resolve: (source) => ({ __jsExpr: source }),
  // quoted like any other string
  stringify: (item, ctx, onComment, onChompKeep) => {
    const scalar = new Scalar((item.value as any).__jsExpr)
    return stringifyString(Object.assign(scalar, { type: item.type }), ctx, onComment, onChompKeep)
  },
}

const yamlOptions: SchemaOptions = {
  schema: 'core',
  customTags: [JsExpr],
}
```

- 求值与插值：

`vendor/loader/src/config/utils.ts:3-27`

```ts
// eslint-disable-next-line no-new-func
/** Evaluate a JavaScript expression against a loader context scope. */
export const evaluate = new Function('ctx', 'expr', `
  with (ctx) {
    return eval(expr)
  }
`) as ((ctx: object, expr: string) => any)

/** Recursively replace YAML `!!js` expression nodes with evaluated values. */
export function interpolate(ctx: object, value: any) {
  if (isJsExpr(value)) {
    return evaluate(ctx, value.__jsExpr)
  } else if (!value || typeof value !== 'object') {
    return value
  } else if (Array.isArray(value)) {
    return value.map(item => interpolate(ctx, item))
  } else {
    return valueMap(value, item => interpolate(ctx, item))
  }
}

/** Return true when a value is a serialized loader JavaScript expression. */
export function isJsExpr(value: any): value is JsExpr {
  return value instanceof Object && typeof value.__jsExpr === 'string'
}
```

- waterfall 的顺序：
  - Loader 的第一个监听器用 `prepend: true` 登记，位于最外层，负责改写错误位置。
  - EventsService 构造时登记了内置校验监听器，它最早登记。
  - Loader 的第二个监听器登记得最晚，位于 waterfall 最内层：`next()` 拿到原始配置后先做插值。

  所以实际执行顺序是插值、校验、错误改写，注释原文是「interpolation runs on the source config and validation sees the interpolated result」（`vendor/loader/src/index.ts:122-124`）。
- 「树载体」（Group/Include）不做插值：`if (fiber.runtime?.callback[EntryGroup.key]) return config`（`index.ts:128-129`），而 `Include` 和 `Group` 都声明了 `static readonly [EntryGroup.key] = true`（`vendor/include/src/index.ts:241-242`；`vendor/loader/src/config/group.ts:114-116`）。因此子行里的表达式要等到子行自己激活时，才在**子插件自己的 ctx** 上求值，这时可以用它 inject 的服务。primer 原文：「Loader 在声明的注入激活后，基于该插件上下文（`ctx.serviceName`）插值条目的 `config`……Include 会保留嵌套行表达式，直到目标行激活」（`docs/cordis-primer.zh.md:45`）。`vendor/README.md` V15 也写道：「nested `!!js` expressions wait for their own injections」。
- `disabled: !!js` 由 `_resolveDisabled` 调用 `this.evaluate(...)` 求值，实际执行 `evaluate(this.ctx, expr)`。这里的 `this.ctx` 是 Entry 构造时 `loader.ctx.extend({[Entry.key]: this})` 得到的上下文（`entry.ts:58-61, 104-116`）。原始节点保留在 options 里，写回文件时仍是 `!!js` 形式（`entry.ts:105-106` 注释）。`disabled` getter 会沿父条目链逐层向上求值，group 条目本身永远不算 disabled（`entry.ts:92-102`）。
- 生产配置中的例子：base bundle 用 `disabled: !!js "!ctx.get('profileContext')"` 控制 plugin-manager 行和 hmr 行（`packages/bundle/base/cordis.patch.yml:20-31`）。

`packages/bundle/base/cordis.patch.yml:15-31`

```yaml
- insert:
    - id: tool-plugin-manager
      name: '@deepseek-ai/dsh-plugin-manager/tools'
      disabled: true

    - id: plugin-manager
      name: '@deepseek-ai/dsh-plugin-manager'
      disabled: !!js "!ctx.get('profileContext')"

    - id: timer
      name: '@deepseek-ai/cordis-plugin-timer'

    # Profile configuration reloads by default; module roots are opt-in.
    - id: hmr
      name: '@deepseek-ai/dsh-hmr'
      disabled: !!js "!ctx.get('profileContext')"
      config:
```

- 实验 E1：`greeting: !!js process.env.DEMO_GREETING + ' / ' + typeof loader` 的结果是 `Hi-from-env / object`。这说明 `process` 可用，而且裸标识符 `loader` 经 `with(ctx)` 解析到了 `ctx.loader`。
- 实验 E2 step 3 和 step 4：step 3 只把环境变量改成 `GATE=on`，文件不动，然后调用 `include.refresh()`，`gated` 条目**没有**挂载。原因是 `Include._read()` 发现文件内容与缓存相同，直接 return（`vendor/include/src/index.ts:411`）。step 4 改了文件内容，整棵树重新协调，`gated` 的 `disabled` 重新求值为 false，于是被挂载（输出中的 `[gated] apply mode=y`）。
- 每次协调时，`EntryGroup.update()` 会对新列表里的**每个** id 调用 `create()`，也就是 `entry.update(options, true, force=true)`（`group.ts:26-37, 86-104`）。所以只要文件变了，即使某一行的文本没变，它的 `disabled` 表达式也会重新求值；不过 config 没变的行不会重启。原因是 `pending` 为空时，`_patchContext([])` 只更新 context 原型，不调用 `fiber.update`（`entry.ts:164-167, 118-129`）。

### Inferences
- `!!js` 在 loader 进程里以完整权限执行任意 JS（`with` + `eval`）。patch 文件因此等同于代码：用 `--patch` 加载不可信文件，就是执行不可信代码。
- `disabled: !!js` 不是响应式的，只在协调时求值。如果门控条件依赖运行期状态（例如某个服务是否存在），状态变化不会自动生效，需要再触发一次协调。

### Gaps
- 没有做实验验证在 `config` 的 `!!js` 中引用插件 inject 的服务，这一结论只来自源码和 primer。

---

## Q4 Loader 的条目模型，以及 cordis.yml 编辑后的 diff

### Takeaway
Loader 本身是一个 `EntryTree`。它的根树只在内存里，`commit` 是空操作（`vendor/loader/src/index.ts:77, 200-202`）。`cordis:include` 或 `@deepseek-ai/cordis-plugin-include` 条目会创建一棵子树：从 YAML/JSON 文件读入条目列表，应用 patch，再交给 `root.update(list)`（`vendor/include/src/index.ts:664-680`）。

`EntryGroup.update()` 把新旧列表分别按 `id` 建成 map 来比较（`group.ts:86-104`）：
- 新列表里有的 id 一律调用 `create()`，由 `Entry.update` 内部按键比较，决定是不做事、提交 volatile 还是重启。
- 只在旧列表里出现的 id 调用 `remove()`。

没有 `id` 的行由 Include 在读取时分配一个 8 位十六进制的随机 id。**如果某行与上一次读取时某个匿名行深相等，就沿用那个 id**（`vendor/include/src/index.ts:615-662`）。所以编辑相邻行不会让匿名行重启；只有编辑匿名行本身，它才会拿到新 id，走删除再创建。这与教程第 6 章的说法矛盾，详见下文。

### Cited Findings
- **EntryOptions 字段**：`id`、`name`、`config`、`group`、`disabled`、`inject`（`entry.ts:9-23`）。`isolate.ts` 又扩展了 `intercept` 和 `isolate`（`vendor/loader/src/config/isolate.ts:5-14`）。`validateOptions` 的规则如下（`vendor/loader/src/config/utils.ts:119-162`）：
  - `name` 必填，且为字符串。
  - `id` 为字符串，允许包含 `:`（dsh 本地修改 V27）。
  - `group` 为布尔值；`group: true` 时 `config` 必须是数组。
  - `disabled` 为布尔值或 `!!js`。
  - `inject` 为数组或对象。
  - `isolate` 的每个值必须是 `true` 或字符串。

  不合法的行以 `EntryValidationError` 拒绝，同 id 的旧条目会被销毁（`group.ts:22-28, 50-66`）。
- **按 id 做 diff**：

`vendor/loader/src/config/group.ts:22-37`

```ts
  /**
   * Create or replace the entry for `options`. Invalid options reject with `EntryValidationError` and dispose any entry already registered under their id.
   * @returns The full entry id.
   */
  async create(options: Omit<EntryOptions, 'id'>) {
    const issues = validateOptions(options, EntryTree.sep)
    if (issues.length) throw this._reject(options as EntryOptions, issues)
    const id = this.tree.ensureId(options)
    const entry: Entry = this.tree.store[id] ??= new Entry(this.ctx.loader)
    // Entry may be moved from another group,
    // so we need to update the parent reference.
    entry.parent = this
    // Use `create: true` to replace existing entry.options.
    await entry.update(options, true, true)
    return entry.id
  }
```

`vendor/loader/src/config/group.ts:86-104`

```ts
  async update(config: EntryOptions[]) {
    const oldConfig = this.data as EntryOptions[]
    this.data = config
    const oldMap = Object.fromEntries(oldConfig.map(options => [options?.id, options]))
    // non-objects are rejected by `create`
    const newMap = Object.fromEntries(config.map(options => [options?.id ?? Symbol('anonymous'), options]))

    // update inner plugins
    const ids = Reflect.ownKeys({ ...oldMap, ...newMap }) as string[]
    await Promise.all(ids.map(async (id) => {
      if (newMap[id]) {
        await this.create(newMap[id]).catch((error) => {
          this.ctx.logger.error(error)
        })
      } else {
        this.remove(id)
      }
    }))
  }
```

  `newMap` 由 `Object.fromEntries` 构建，同一列表中出现重复 id 时**后者覆盖前者**（见 Q5 实验 E6）。options 没有 id 时，key 是 `Symbol('anonymous')`（`group.ts:91`）。不过 Include 已经提前分配好 id，这个分支只在其他调用方传入无 id 的列表时才会走到。
- **id 生成**：`EntryTree.ensureId` 在没有 id 时生成 `Math.random().toString(16).slice(2, 10)`，并避开已有 id（`vendor/loader/src/config/tree.ts:125-139`）。Include 自己的 `_assignIds` 会尽量沿用旧 id：

`vendor/include/src/index.ts:615-662`

```ts
  /**
   * Entries the file leaves anonymous get an id here, before the loader sees
   * them. One that looks exactly like an entry we named on the previous read
   * keeps that id, unless it is already explicitly owned, so editing a
   * neighbor does not restart it; anything else gets a fresh id. Assigned
   * ids stay in memory until the next write.
   */
  private _assignIds(data: EntryOptions[]) {
    const used = new Set<string>()
    const collect = (entries: EntryOptions[]) => {
      for (const options of entries) {
        if (options.id) used.add(options.id)
        if (options.group && Array.isArray(options.config)) collect(options.config)
      }
    }
    collect(data)
    for (const patch of this.config.patches ?? []) {
      if (patch.insert) collect(patch.insert)
    }

    const previous = this.anonymous
    this.anonymous = new Map()
    const assign = (entries: EntryOptions[], parent: string | null) => {
      for (const options of entries) {
        if (!options.id) {
          let id: string | undefined
          for (const [candidate, info] of previous) {
            if (info.parent !== parent || used.has(candidate) || !deepEqual(info.options, options)) continue
            id = candidate
            previous.delete(candidate)
            break
          }
          if (!id) {
            do {
              id = Math.random().toString(16).slice(2, 10)
            } while (used.has(id) || this.store[id])
          }
          used.add(id)
          this.anonymous.set(id, { parent, options: structuredClone(options) })
          const rest = { ...options }
          for (const key of Object.keys(options)) delete options[key]
          Object.assign(options, { id }, rest)
        }
        if (options.group && Array.isArray(options.config)) assign(options.config, options.id)
      }
    }
    assign(data, null)
  }
```

- **完整 id 带树前缀**：`Entry.id` 等于父树所属条目的 id 加 `:` 再加本地 id（`entry.ts:84-90`）。在 E1、E3 的输出里，条目显示为 `<include 随机 id>:a`。dsh 给根 Include 固定了 `id: 'include'`，理由是「a random id would make startup diagnostics unstable」（`packages/boot/app-boot/src/index.ts:786-800`）。group 的子行与 group 在**同一棵树**里，所以 E3 中 `pa` 显示为 `…:pa`，而不是 `…:ga:pa`；只有 Include 会开出新的树。
- **Entry.update 的三个分支**（摘录见 Q2，`entry.ts:137-171, 250-276`）：
  - 条目 disabled：`await this.fiber?.dispose()` 后返回。
  - 已有 fiber：计算 `changes`，要么走 volatile 路径，要么走 `loader/partial-dispose` 加 `_patchContext(changes)`。
  - 还没有 fiber：调用 `init()` 导入模块，再 `registry.plugin()`。

  `_patchContext` 先让 isolate 插件通过 `loader/patch-context` waterfall 重建隔离映射；只有 `config` 变化或条目是 group 时，才调用 `fiber.update(config, true)`，其中 `noSave=true` 表示不回写（`entry.ts:118-129`）。
- **条目上的 `inject`**：Loader 在 `internal/plugin` 中执行 `Inject.resolve(fiber.entry!.options.inject, fiber.inject)`，把 `entry.options.inject` 合并进 fiber 的 inject（`vendor/loader/src/index.ts:149-155`，旁边有一行 `// FIXME merge config`）。也就是说，不改插件代码，只在 yml 里就能给插件加依赖。E3 中的 `needs` 插件本身没有声明 inject，条目上写了 `inject: [nobody]`，结果一直停在 PENDING。
- **`isolate` 与 group**：`loader/patch-context` 的处理函数按以下顺序工作（`vendor/loader/src/config/isolate.ts:96-156`）：
  1. 为条目生成新的 isolate symbol 映射。值为 `true` 时使用条目私有的 `LocalRealm`，symbol 后缀是 `#<id>`；值为字符串时使用按标签共享的 `GlobalRealm`，后缀是 `@<label>`。
  2. 计算服务 diff。
  3. 替换 ctx 上 `Context.isolate`/`Context.intercept` 的原型。
  4. 调用 `next()` 重载 fiber。
  5. 必要时把服务实现迁移到新 symbol。
  6. 调用 `reflect.notify`。

  `partial-dispose` 时，不再被任何条目引用的全局 realm 会被回收（`isolate.ts:158-175`）。
- **实验 E3**（`lab-config/scripts/e3/cordis.init.yml`）：两个 group 都设置了 `isolate: { store: true }`，各有一个 provider 和一个 consumer。输出：

```text
  [provider A] provided store
  [provider B] provided store
  [consumer ca] sees store.tag=A
  [consumer cb] sees store.tag=B
```

- **自释放会回写 `disabled: true`**：插件自己调用 `ctx.fiber.dispose()` 时，Loader 的 `internal/plugin` 监听器会先排除 8 种「不属于自释放」的情况；剩下的才设置 `entry.options.disabled = true` 并 `commit`（`vendor/loader/src/index.ts:157-195`）。E3 中的 `selfdispose.mjs` 在 10ms 后自释放，最后磁盘上的 `cordis.yml` 里多了 `disabled: true`（见 `out/e3.txt`）。

`vendor/loader/src/index.ts:157-195`

```ts
      // 2. handle self-dispose
      // We only care about `ctx.fiber.dispose()`, so we need to filter out other cases.

      // case 1: fiber is created
      if (fiber.uid) return

      // case 2: fiber is not tracked by loader
      if (!fiber.entry) return

      // case 3: fiber is a child plugin under the entry (not the entry's root fiber)
      if (fiber.parent.fiber?.entry === fiber.entry) return

      // case 4: fiber is disposed on behalf of plugin deletion (such as plugin hmr)
      // self-dispose: ctx.fiber.dispose() -> fiber / runtime dispose -> delete(plugin)
      // plugin hmr: delete(plugin) -> runtime dispose -> fiber dispose
      if (!ctx.registry.has(fiber.runtime!.callback)) return

      // case 5: the entry's tree is being disposed
      if (!fiber.entry.parent.tree.ctx.fiber.uid) return

      const { entry } = fiber
      this.showLog(entry, 'unload')

      // case 6: the entry's parent is being unloaded, which takes the entry with it
      // (the parent may still be registered, e.g. when it has lost a dependency)
      if (fiber.parent.fiber.state === FiberState.UNLOADING) return

      // case 7: the entry is being removed by the loader (`EntryGroup.remove`
      // unregisters it before disposing the fiber)
      if (entry.parent.tree.store[entry.options.id] !== entry) return

      // case 8: fiber is disposed by loader behavior
      // such as inject checker, config file update, ancestor group disable
      if (entry.disabled) return

      const legacy = { ...entry.options }
      entry.options.disabled = true
      entry.parent.tree.commit({ id: entry.options.id, group: entry.parent, options: entry.options, legacy })
    })
```

- **运行时编辑会回写文件**：以下操作都会调用 `tree.commit(change)`：`EntryTree.create/update/remove`；以及未带 noSave 的 `fiber.update()` 触发的 `internal/commit`（`index.ts:133-141`），这条路径会先用 schema 的 `simplify` 解开 volatile 引用。Include 把这些变更记入 journal，与磁盘文件协调后写回；文件编辑与 journal 冲突时，以文件为准（`vendor/include/src/index.ts:362-368, 457-464, 472-554`）。`docs/upgrade-guide/v0.2.2-alpha.2/cordis-loader-entry-trees/guide.zh.md` 第 14-18 行记录了这一机制，还提到一点：对 patch 插入行（也就是 bundle 行）的运行时修改，会写进父级的 patch；dsh 根 Include 的父级是内存中的 Loader，**所以 bundle 行自释放时写入的 `disabled: true` 在重启后不会保留**。
- **实验 E2：匿名行 id 的稳定性（与教程矛盾）**。step 0 中匿名行拿到 id `d105eab2`。step 1 只改相邻行，它的 id 仍是 `d105eab2`，也没有 dispose 和 apply。step 2 改了它自己的 `mode`，它换成新 id `b06fcc51`，先 dispose 再 apply。而 `docs/cordis-tutorial/06-composition-and-hmr.zh.md:59` 写的是：「不带该字段的 Cordis 配置项在每次读取时都会获得一个新生成的 id，所以只要配置文件发生任何编辑，即使自身文本未变，它也会被视为先删除再添加并重新挂载。」4b399c3 的 `_assignIds` 注释（`vendor/include/src/index.ts:615-621`）写明「One that looks exactly like an entry we named on the previous read keeps that id … so editing a neighbor does not restart it」，实验结果也与之一致，所以教程这句已经过时。但结论没有变：仍然需要显式写 `id`。没有 id 的行只要改动自身配置，就会被删除重建，失去 volatile 更新和原地更新的机会；patch 层也无法按 id 定位它。
- E2 结束时，磁盘文件里没有出现匿名行的 id。这是因为这些 id「stay in memory until the next write」（`index.ts:620`），要等到发生一次 journal 写入才会落盘（升级指南 `guide.zh.md:17`：「Include 会把生成的 id 写回原本没有 id 的文件行」）。

### Inferences
- 修改某一行的 `name`、`inject`、`isolate` 或 `disabled`，都会让 `changes` 里出现非 config 的键，于是走 `_patchContext`。但 `_patchContext` 只在 `diff.includes('config')` 或条目是 group 时才调用 `fiber.update`（`entry.ts:122`）。只改 `inject`/`isolate` 时，重载来自 isolate 处理函数里的 `next()`，以及 `Inject` 变化后的依赖刷新。只改 `name` 时，源码里没有找到明确的重新导入路径（见 Gaps）。

### Gaps
- 只改条目的 `name` 而不改 config 时，`Entry.update` 是否会重新导入模块？`_patchContext` 不会重新 `import`，`init()` 也只在没有 fiber 时执行。读源码的判断倾向于「不会换成新模块」，但没有做实验验证，仍是待验证点。

---

## Q5 dsh 怎样层叠配置（bundle → profile → home → `--patch` → 派生 patch）

### Takeaway
在 profile 模式下，dsh 把各层**拼成一份扁平的 patch 列表**，顺序为：各 bundle 的 patch（按 `dsh.profile.bundles` 的顺序）、profile 目录的 `cordis.patch.yml`、`$DSH_HOME/cordis.patch.yml`、命令行 `--patch` 文件（按 argv 顺序），最后可能再加一条禁用遥测的 patch（`packages/boot/app-boot/src/profile-context.ts:75-86`）。

这份列表作为根 Include（`id: include`，`name: cordis:include`）的 `config.patches`（`app-boot/src/index.ts:791-801`），由 Include 的 `applyPatches` 叠加到文件数据上（`vendor/include/src/patch.ts:107-180`）。dump 和推导使用同一个算法：`composeEntries = applyEntryPatches([], layers.flat())`（`app-boot/src/profile.ts:747-762`）。

合并规则：
- 按 id 定位的覆盖 patch **逐键整值替换**，`config` 不做深合并。
- 后面的 `insert` 如果与已有行 id 相同，运行时会**整行替换**原来的行，因为 `EntryGroup.update` 中后者覆盖前者。
- `name` 与目标不一致的覆盖会被跳过。
- 目标不存在时只给一条 warn。

### Cited Findings
- 层的顺序（源码）：

`packages/boot/app-boot/src/profile-context.ts:75-86`

```ts
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

  `ProfileContext.overlayPaths` 的注释写道：「Absolute command-line overlay files in argv order, read on every composition and applied above profile and home patches」（`profile-context.ts:22-23`）。profile 模块的头注释描述了同样的顺序（`packages/boot/app-boot/src/profile.ts:1-14`）。
- 合并函数：

`packages/boot/app-boot/src/profile.ts:747-762`

```ts
/**
 * Compose patch layers into the effective entry list over an empty root —
 * the same single `applyEntryPatches` call the boot include makes, so flag
 * derivation and config dumps see exactly what mounts.
 * @param layers - patch lists in application order.
 * @param warn - sink for skipped-patch diagnostics; defaults to silent (boot repeats them).
 * @returns the composed entry list.
 */
export function composeEntries(
  layers: readonly PatchOptions[][], warn: (message: string) => void = () => {},
): EntryOptions[] {
  return applyEntryPatches([], structuredClone(layers.flat()), (message: string, ...args: unknown[]) => {
    let index = 0
    warn(message.replace(/%C/g, () => JSON.stringify(args[index++])))
  }).data
}
```

`vendor/include/src/patch.ts:134-180`

```ts
  mount(data, FILE)

  patches?.forEach((patch, index) => {
    const { id, insert, name, ...overrides } = patch
    const target = id ? entries.get(id) : undefined

    if (insert) {
      if (id && !target) return warn('patch insert: entry %C not found', id)
      if (target && !target.group) return warn('patch insert: entry %C is not a group', id)
      const clone = structuredClone(insert)
      if (target) {
        if (!Array.isArray(target.config)) target.config = []
        target.config.push(...clone)
      } else {
        data.push(...clone)
      }
      mount(clone, { type: 'embedded', index, key: 'insert' })
      return
    }

    if (!id) return warn('patch: id is required for non-insert patches')
    if (!target) return warn('patch: entry %C not found', id)
    if (name && name !== target.name) {
      return warn('patch: name mismatch for %C (expected %C, got %C), skipping', id, target.name, name)
    }

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
    if ('config' in overrides && target.group && Array.isArray(target.config)) {
      // ids go on the patch itself, before the tree takes its own copy
      ensureIds(target.config, id => entries.has(id))
      target.config = structuredClone(target.config)
      mount(target.config, { type: 'embedded', index, key: 'config' })
    }
  })
```

  overrides 的处理是逐键执行 `target[key] = value`（`patch.ts:165-169`），所以 `config` 会被整块替换。如果覆盖 patch 替换的是某个 group 的 `config`，旧的子行先被 unmount，新的子行再 mount，之后仍然可以按 id 继续 patch（`patch.ts:160-177`）。`insert` 带上 `id` 时，可以插入到对应的 group 里（`patch.ts:140-151`）。
- 仓库里多处写明「整块替换」：`packages/boot/app-boot/README.md:210`（「A user patch replaces the whole matched config — an id-targeted patch does not deep-merge」）、base bundle 的文件头（`packages/bundle/base/cordis.patch.yml:1-13`）、升级指南 `docs/upgrade-guide/v0.2.2-rc.1/subagent-interrupt-default/guide.md:26`（「A patch replaces the whole `config`」）。
- 根 Include 的挂载：

`packages/boot/app-boot/src/index.ts:786-801`

```ts
  // Pinned id: the bootstrap include is app glue, not a config row, and its
  // id appears in Loader failure chains — a random id would make startup
  // diagnostics unstable across runs (and snapshot fixtures).
  // The launcher's own copy is prepared here: compatibility decisions must be made before the root
  // Include imports anything, and they change no profile patch layer, manifest, or bundle list.
  const prepared = prepareProfilePatches(ctx, [...patches], pathToFileURL(dirname(absoluteConfigPath)).href + '/', binName)
  const includeConfig: Include.Config = {
    path: pathToFileURL(absoluteConfigPath).href,
    ...prepared.length > 0 ? { patches: prepared } : {},
  }
  const rootInclude: EntryOptions = {
    id: 'include',
    name: 'cordis:include',
    config: includeConfig,
  }
  const includeId = await ctx.loader.create(rootInclude)
```

- **实验 E5**（`lab-config/scripts/e5-layers.mjs`，调用方式与 `composeEntries` 相同）：

`lab-config/scripts/e5-layers.mjs`

```js
// Compose patch layers exactly like app-boot composeEntries (profile.ts:755-762): applyEntryPatches([], layers.flat())
import { applyEntryPatches, parseEntryYaml } from '@deepseek-ai/cordis-plugin-include'
const bundleBase = parseEntryYaml(`
- insert:
    - id: x
      name: pkg-x
      config: { a: 1, b: 2 }
    - id: grp
      name: cordis:group
      group: true
      config:
        - id: child
          name: pkg-child
          config: { deep: 1 }
`)
const bundleMode = parseEntryYaml(`
- id: x
  config: { b: 3 }          # replaces whole config, a is gone
- id: child                 # child rows of a group are addressable by id
  config: { deep: 2 }
`)
const profile = parseEntryYaml(`
- id: x
  disabled: !!js "process.platform === 'win32'"
- id: x
  name: other-name          # name mismatch -> skipped with warning
  config: { z: 9 }
- id: missing
  config: {}
- insert:
    - id: x                 # inserting a duplicate id
      name: pkg-x2
`)
const warn = (fmt, ...args) => { let i = 0; console.log('  warn:', fmt.replace(/%C/g, () => JSON.stringify(args[i++]))) }
const { data } = applyEntryPatches([], structuredClone([...bundleBase, ...bundleMode, ...profile]), warn)
console.log(JSON.stringify(data, null, 1))
```

命令：`node scripts/e5-layers.mjs`，输出：

```text
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

  结论：
  - `x` 的 `config` 从 `{a:1,b:2}` 变成了 `{b:3}`，`a` 丢失。
  - `disabled: !!js` 原样保留为 `{__jsExpr}`，留到运行时求值。
  - group 的子行 `child` 可以被顶层 patch 按 id 覆盖。
  - `name` 不匹配的 patch 和找不到目标的 patch 都只产生 warn。
  - 同 id 的 `insert` 让 `data` 里**同时出现两行 `x`**。
- **实验 E6**（`lab-config/scripts/e6/run.mjs`）：根 Include 读一个空文件 `[]`，patches 依次是 bundle insert、按 id 覆盖、同 id 再次 insert。命令：`node scripts/e6/run.mjs`，输出：

```text
  [x] apply name=p2.mjs config={"c":4}
entries: include include:x
```

  运行时只创建了**一个**条目 `include:x`，加载的是后插入的 `p2.mjs`，config 为 `{c:4}`；前一行连同被覆盖成的 `{b:3}` 整行消失。可见后面的层用 insert 写同一个 id 时会整行替换，而按 id 的覆盖 patch 只替换它写出的那些键。
- profile 的 HMR 重组：`watchConfig` 监视 profile patch、home patch、各个 overlay 文件，以及 profile 的 `package.json`。任一文件变化后，`reconcileProfile()` 用 `readProfilePatches` 重新读取所有层（`packages/boot/hmr/src/index.ts:367-414, 937-1015`），然后 `recomposeProfile` 执行 `entry.update({ config: { ...includeConfig, patches: prepared } })`（`packages/boot/app-boot/src/index.ts:407`）。Include 的 `internal/update` 监听器接住这次更新，只调用 `_scheduleApply()`，不会重启 Include 自身（`vendor/include/src/index.ts:284-290`）。
- `--dump-config` 输出的是解析后的条目列表 YAML（`packages/boot/app-boot/src/config-schema/document.ts:163`）。`vendor/README.md` V11 说明，导出 `applyEntryPatches` 是为了让「`dsh --dump-config` composes entries with the same algorithm that mounting uses」。

### Inferences
- 「五层」只是通俗说法，层数并不固定：每个 bundle 算一层，每个 bundle 还可以声明多个 patch 文件（见 `profile.ts:48-71`）；之后是 profile 层、home 层、`--patch` 层（可以有多个），最后还有代码派生的 patch（例如禁用遥测）。
- 覆盖某个 bundle 行的 `config` 时，必须把想保留的字段全部重写一遍。插件作者应当把默认值放在 schema 里，而不是写在 bundle patch 里。

### Gaps
- 「flag-derived patches」（`profile.ts:13`）具体在哪里生成（cmdline 包）没有展开阅读。

---

## Q6 HMR：`@deepseek-ai/dsh-hmr` 怎样监视文件、去抖、定位插件、使依赖失效、替换与回滚

### Takeaway
`@deepseek-ai/dsh-hmr` 是一个服务名为 `hmr` 的 `Service`，带 `@Inject('loader')` 和 `@Inject('timer')`（`packages/boot/hmr/src/index.ts:211-226`）。它要求 Loader 能拿到 Node 内部的 ESM loader（通过 `--expose-internals` 或 `node-addon-require-builtin`），否则构造函数直接抛错（`index.ts:307-314`；`vendor/loader/src/internal.ts:107-143`）。

它有两条监视路径：

(a) **模块根**（`config.root`）：用 chokidar 监视，经 `ctx.debounce(..., config.debounce)` 合并 change 事件（默认 100ms）。每批变更按类型处理（`index.ts:436-489`）：
- 属于 externals（进程入口的静态依赖图）的文件，调用 `loader.exit()` 请求重启整个进程。
- 已在 ESM 缓存中的文件，放进 stash，等待部分重载。
- 某个 Include 的配置文件，调用 `include.refresh()`。
- 其他文件，发出 `hmr/change` 事件。

(b) **profile 配置文件**：`watchConfig` 精确监视单个文件，变化后由 `reconcileProfile()` 重组整个 profile（`index.ts:367-414`，`watch-config.ts`）。

部分重载 `partialReload()` 的流程（`index.ts:590-763`）：
1. 沿 ESM link 图把「accepted」状态向上传播到依赖它的模块。
2. 找出入口模块受影响的 Loader 条目插件。
3. 把相关模块从 ESM 和 CJS 缓存中逐出（保留备份）。
4. 重新 import。
5. 调用 `registry.delete(旧插件)`，再在原来的父 ctx 上用原配置执行 `registry.plugin(新实现)`。

任何一步失败，都会回滚缓存并把旧插件挂回去。

dsh 的 base bundle 默认 `root: []`，也就是模块热替换需要主动开启，默认只有配置热重载（`packages/bundle/base/cordis.patch.yml:27-31`）。acp-app bundle 直接禁用了 hmr（`packages/bundle/acp-app/cordis.patch.yml:22-23`）。

### Cited Findings
- 配置与依赖：

`packages/boot/hmr/src/index.ts:211-226`

```ts
@Inject('loader')
@Inject('timer')
class Hmr extends Service {
  /** Cordis-compatible watcher defaults. */
  static Config: z<HmrConfig> = z.object({
    base: z.string(),
    root: z.array(String).role('table').default(['.']),
    ignored: z.array(String).role('table').default([
      '**/node_modules',
      '**/.*',
      'cache',
      'data',
    ]),
    debounce: z.natural().role('ms').default(100),
  })
```

`packages/boot/hmr/src/index.ts:307-314`

```ts
  constructor(ctx: Context, public config: HmrConfig) {
    super(ctx, 'hmr')
    this.ownerContext = ctx
    if (!this.ctx.loader.internal) {
      throw new Error('--expose-internals is required for HMR service')
    }
    this.internal = this.ctx.loader.internal
    this.baseDir = fileURLToPath(new URL(config.base || '.', ctx.baseUrl))
```

- 去抖由 timer 服务提供：`debounce` 基于 `_schedule` 实现，每次调用都先 `clearTimeout` 再重新 `setTimeout`。定时器登记为一个 `ctx.effect`，随所属 fiber 一起释放（`vendor/timer/src/index.ts:110-125, 145-150`）。教程第 6 章指出，没有挂载 timer 时，HMR「就会永远停在 PENDING，而且不发出任何提示」（`docs/cordis-tutorial/06-composition-and-hmr.zh.md:42`）。

`vendor/timer/src/index.ts:145-150`

```ts
  /** Return a debounced function whose timer is disposed with the current fiber. */
  debounce<F extends (...args: any[]) => any>(callback: F, delay: number): Scheduled<F> {
    return this._schedule('ctx.debounce()', (args) => {
      return setTimeout(callback, delay, ...args)
    })
  }
```

- 主 watcher 与按批分类处理：

`packages/boot/hmr/src/index.ts:436-489`

```ts
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

  补充三点：主 watcher 设置了 `ignoreInitial: true`，启动时不会触发刷新（`vendor/README.md` V9）；它只监听 `'change'` 事件，不监听 `add` 和 `unlink`（`index.ts:489`）；所有重载都经 `runExclusive` 串行执行，不允许嵌套（`index.ts:264-276`）。
- **依赖传播**（`analyzeChanges`，`index.ts:529-588`）：初始时 accepted 集合是 stash 中的 URL，declined 集合是 externals。对每个待定模块，查看它 link 到的子模块：只要有一个子模块属于 accepted，它就是 accepted；如果子模块全部属于 declined 或被排除（`node:`、`/node_modules/`），它就是 declined。如此迭代，直到集合不再变化。结果是：一个模块被修改后，所有依赖它的模块（node_modules 内的除外）都会一起失效。
- **从变更文件定位插件**（`partialReload`，`index.ts:590-651`）：遍历 `loader.entries()`，按树的 baseUrl 收集 `entry.options.name` 与 `entry.moduleNamespace`，解析出每个插件的入口 URL。入口模块的依赖闭包里只要包含 accepted 模块，该插件就进入 `reloads`。`Entry.moduleNamespace` 是 dsh 本地新增的字段（V23），用于把条目对应到 Node 缓存中的命名空间。

`packages/boot/hmr/src/index.ts:597-651`

```ts
    // Build a map of plugin names per config tree URL.
    // Plugin entry files are treated as atomic reload units.
    const nameMap = new Map<string, EntryNamespaces>()
    for (const entry of this.ctx.loader.entries()) {
      const baseUrl = entry.parent.tree.ctx.baseUrl
      if (baseUrl === undefined) throw new Error('HMR entry tree has no base URL')
      const names = nameMap.get(baseUrl) ?? new EntryNamespaces(this.internal, moduleUrls)
      names.add(entry.options.name, entry.moduleNamespace)
      nameMap.set(baseUrl, names)
    }

    // Find each plugin's loaded URL and check if it needs reload.
    for (const [baseUrl, names] of nameMap) {
      for (const [name, moduleNamespace] of names) {
        try {
          const { url } = await names.resolve(name, baseUrl, moduleNamespace)
          if (this.declined.has(url)) continue
          const job = this.esmJob(url)
          const plugin = this.ctx.loader.unwrapExports(job?.module?.getNamespace()) as Plugin | undefined
          if (!job || !plugin) continue
          pending.set(job, plugin)
          this.declined.add(url)
        } catch (err) {
          this.ctx.logger.warn(err)
        }
      }
    }

    // Check each pending plugin's dependency tree for accepted files
    for (const [job, plugin] of pending) {
      this.declined.delete(job.url)
      const dependencies = [...await loadDependencies(job, this.declined)]
      this.declined.add(job.url)

      if (dependencies.length === 0) {
        pending.delete(job)
        continue
      }
      if (!dependencies.some(dep => this.accepted.has(dep))) continue
      dependencies.forEach(dep => this.accepted.add(dep))

      const runtime = this.ctx.registry.get(plugin)
      if (ReloadModules.include(reloads, plugin, job, runtime)) continue
      reloads.set(plugin, {
        filename: job.url,
        runtime,
        modules: new ReloadModules().add(job, plugin),
      })
    }

    // Re-export roots of a replaced runtime must not retain bindings to its old modules.
    for (const [job, plugin] of pending) {
      if (ReloadModules.include(reloads, plugin, job, this.ctx.registry.get(plugin))) this.accepted.add(job.url)
    }
```

- **缓存逐出与回滚**：`evict()` 从 `internal.loadCache` 中整条删除对应 URL，同时删除 `require.cache` 中的对应项，并返回一个恢复函数（`index.ts:758-790`）。删除用的是 `Map.prototype.delete`，因为 Node 的 `LoadCache.delete` 只清一个类型槽位。

`packages/boot/hmr/src/index.ts:765-790`

```ts
  private evict(urls: Iterable<string>): () => void {
    const esmBackup = new Map<string, unknown>()
    const cjsBackup = new Map<string, NodeJS.Module>()
    const cache = commonJsCache()
    const list = [...urls]
    // CommonJS-only files have no loadCache entry to restore.
    const cached = new Set(this.esmUrls())
    for (const url of list) {
      if (cached.has(url)) {
        esmBackup.set(url, Map.prototype.get.call(this.internal.loadCache, url))
        Map.prototype.delete.call(this.internal.loadCache, url)
      }
    }
    for (const filepath of list.filter(url => url.startsWith('file:')).map(url => fileURLToPath(url))) {
      const module = cache[filepath]
      if (module !== undefined) {
        cjsBackup.set(filepath, module)
        Reflect.deleteProperty(cache, filepath)
      }
    }
    return () => {
      for (const [url, job] of esmBackup) Map.prototype.set.call(this.internal.loadCache, url, job)
      for (const [filepath, module] of cjsBackup) cache[filepath] = module
    }
  }
```

- **替换流程**（`index.ts:653-762`）：
  1. 先 import 全部新模块。任何一个失败，就执行 `handleError` 加 `rollback()` 并中止，旧插件不受影响。
  2. 对每个插件执行 `registry.delete(旧插件)`，等待旧 fiber 完成 dispose。
  3. 调用 `previousFiber.fiber.parent.registry.plugin(implementation, previousFiber.config)`，在**原父 ctx 上、用原来的原始配置**启动新实现，并把 `entry.fiber` 指向新的 wrapper。
  4. 替换失败时，回滚缓存，删除新 runtime，把旧插件重新挂回去。
  5. 全部完成后，`replacements.updateEntries(loader)` 更新 `entry.moduleNamespace`，并发出 `hmr/reload`（`index.ts:757-762`）。

  用 `registry.delete` 停止插件时，Loader 的 `internal/plugin` 自释放判定会命中 case 4（`!ctx.registry.has(fiber.runtime!.callback)`），所以这次停止**不会**被记成 `disabled: true`（`vendor/loader/src/index.ts:169-172`）。
- **profile 配置热重载**：

`packages/boot/hmr/src/index.ts:383-414`

```ts
      yield () => { unsubscribe(); started.resolve(false); return Promise.resolve() }
      const manifestPath = join(profile.dir, 'package.json')
      // A file named by several layers or repeated `--patch` options, under any spelling, is watched and compared once.
      const canonicalFiles = new Set<string>()
      const patchFiles = [profile.patchPath, join(profile.home, PROFILE_PATCH_FILENAME), ...profile.overlayPaths].filter((filename) => {
        const canonical = canonicalPath(filename)
        if (canonicalFiles.has(canonical)) return false
        canonicalFiles.add(canonical)
        return true
      })
      let lastInputs: string | undefined
      let lastBundles = JSON.stringify(profile.startedBundles)
      const refresh = async (manifestOnly: boolean): Promise<void> => {
        const bundles = JSON.stringify(readProfileManifest('dsh', profile.dir).dsh?.profile?.bundles ?? [])
        if (manifestOnly && bundles === lastBundles) return
        const inputs = JSON.stringify([bundles, ...patchFiles.map((filename) => {
          try { return readFileSync(filename, 'utf8') }
          catch (error) {
            if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null
            throw error
          }
        })])
        if (inputs === lastInputs) return
        const { warnings, pending, restartReason } = await this.reconcileProfile()
        if (restartReason !== undefined) this.ctx.logger.warn('restart required: %s', restartReason)
        if (pending.length > 0) this.ctx.logger.warn('changed modules keep running their previous code until a restart: %o', pending)
        lastInputs = inputs
        lastBundles = bundles
        for (const diagnostic of warnings) this.ctx.logger.warn(diagnostic)
      }
      for (const filename of patchFiles) await this.watchConfig(filename, () => refresh(false))
      await this.watchConfig(manifestPath, () => refresh(true))
```

  `watch-config.ts` 为每个文件单独开一个 chokidar watcher（`packages/boot/hmr/src/watch-config.ts:9-25, 38-78`）：
  - 文件不存在时，向上找到第一个存在的目录作为 root，并用 depth 限定监视范围。
  - 开启 `awaitWriteFinish: true`，绕过 chokidar 对 change 事件 50ms 的节流。
  - 监听 `add`、`change`、`unlink` 三种事件。
  - 用一个 dirty 标志串行执行 refresh。

  `reconcileProfile()` 先比较已加载模块与磁盘文件（`ModuleFiles`），再由 `planReplacement` 判断哪些变更可以替换。以下两类变更不能替换，会留作 pending，并打印「changed modules keep running their previous code until a restart」（`index.ts:403-405, 829-872`）：
  - 变更模块被宿主的静态依赖图引用，原因记为 `host-module`。
  - 插件不是由 Loader 条目启动的，原因记为 `unmanaged-plugin`。
- **HMR 不能热替换的内容**（来自源码和注释）：
  - 进程入口的静态依赖（externals），这类变更会走 `loader.exit()`（`index.ts:460-462, 474-477`）。
  - `node_modules` 中的模块，它们不参与依赖传播（`index.ts:541`）。
  - 被宿主静态图 import 的模块，以及不是由 Loader 条目启动的插件，这两类留作 pending（`index.ts:849-857`）。
  - 没有 Node 内部 loader 时，整个服务无法构造（`index.ts:310-312`）。
  - profile 模式下，「Installation mappings and local overrides still require a restart」（`packages/boot/app-boot/README.md:205`）。
- **实验 E4**（`lab-config/scripts/e4/run.mjs`）：`app/hello.mjs` import 了 `app/dep.mjs`；hmr 配置为 `root: ['.', '../run.mjs'], debounce: 200`；`loader.exit` 被替换为一个打印函数。命令：`node --expose-internals scripts/e4/run.mjs`，输出：

```text
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

  各步骤的结论：
  - A：只改依赖文件 `dep.mjs`，入口 `hello.mjs` 跟着重载，`other` 没有受影响。依赖向上传播生效，替换以插件为单位。
  - B：200ms 窗口内连写 3 次，只重载了一次，拿到最后的值 `v3c`，去抖生效。
  - C：修改一个未被加载的文件，只发出 `hmr/change`。
  - D：修改 `cordis.yml`，HMR 找到对应的 Include 并调用 `refresh()`；按 id diff 后只重启了 `hello`，它的 config 变成 `{n:2}`。
  - E：写入语法错误后，日志出现 warn，旧实例仍是 ACTIVE；导入失败时没有执行任何 dispose。
  - G：修复后再次重载，但 `msg` 仍是 `v3c`。原因是 `dep.mjs` 这次没有变化，而只有 accepted 模块会被逐出，所以它沿用了缓存。
  - F：修改进程入口 `run.mjs`，它属于 externals，触发了 `loader.exit()`。

### Inferences
- 替换后的新 fiber 拿到的是**原始配置**（Loader 条目的 `options.config`），所以其中的 `!!js` 会在新实例激活时重新求值。插件模块顶层保存的状态（单例、缓存）会随模块被逐出而丢失；放在其他未改动模块里的状态则会保留。
- 主 watcher 只把 `change` 事件放进批处理，新建文件不会触发任何动作。被已加载模块 import 的新文件，要等到 import 它的那个文件被保存时才会生效。
- 修改配置文件（步骤 D）时没有打印 `reload plugin` 日志，因为 `Loader.showLog` 要求 `tree.enableLogs` 为真（`vendor/loader/src/index.ts:210-213`），而实验中的 Include 没有开启它。

### Gaps
- 没有在真实的 `dsh --profile` 进程里运行 profile 模式的 `reconcileProfile`（这需要完整的 profile、pnpm 和 app-boot 环境），相关结论只来自源码阅读。
- 没有逐行对比 `vendor/hmr`（上游 `@cordisjs/plugin-hmr` 的副本）与 dsh-hmr 的差异。V14 只说明 Include 构造函数中删除了 `ctx.hmr.watch(...)`，因为「neither HMR provider exposes `watch()`; both find each Include by scanning loader entries for its `filename`」（`vendor/README.md:41`）。

---

## Q7 诊断 PENDING 插件，以及其他组合问题的排查手段

### Takeaway
PENDING 是合法状态，不报错也不打日志（`docs/cordis-tutorial/06-composition-and-hmr.zh.md:61-63`）。可用的排查手段有：
- 遍历 `ctx.registry` 或 `ctx.loader.entries()`，查看各 fiber 的 `fiber.state`。
- 调用 app-boot 的 `captureStartupComposition`，它列出缺失的服务，以及各 provider 的可见性（`same-scope`/`other-scope`）。
- 用 `Loader.getTasks()`/`await()` 等待整棵树稳定。
- 设置 Loader 的 intercept `await`，让依赖 loader 的插件等到所有条目加载完成后再激活。

### Cited Findings
- 教程中的诊断插件遍历 `ctx.registry.values()` 和每个 runtime 的 `fibers`，筛选出 `FiberState.PENDING`（`06-composition-and-hmr.zh.md:67-83`）。实验 E3 用 `ctx.loader.entries()` 打印状态，看到 `needs state=PENDING`（条目上写了 `inject: [nobody]`）、`sd state=DISPOSED`（自释放），group 条目本身为 ACTIVE。
- `captureStartupComposition(ctx)` 把非 active 的行排在前面。对每个缺失的服务，它列出 `ctx.reflect.store` 里同名实现所属的条目 id、状态，以及与消费者是否处于同一个 isolate scope（`packages/boot/app-boot/src/startup-diagnostics.ts:68-104`）。「服务明明存在，但因为 isolate 而不可见」导致的 PENDING，正好可以用它来诊断。

`packages/boot/app-boot/src/startup-diagnostics.ts:74-104`

```ts
export function captureStartupComposition(ctx: Context): StartupCompositionDiagnostic {
  const entries = [...ctx.loader.entries()]
  const rows = entries.map(entry => ({ entry, state: pluginState(ctx, entry) }))
  // Inactive rows precede healthy rows so bounded reports retain the failure context.
  rows.sort((a, b) => Number(a.state.state === 'active') - Number(b.state.state === 'active'))
  const services: StartupServiceState[] = []
  let omittedServices = 0
  const store = ctx.reflect.store
  for (const { entry, state } of rows) {
    const fiber = entry.fiber
    if (fiber === undefined) continue
    for (const service of state.missing) {
      if (services.length === 256) { omittedServices++; continue }
      const scope = fiber.ctx[Context.isolate][service]
      const providers: StartupServiceState['providers'][number][] = []
      for (const key of Object.getOwnPropertySymbols(store)) {
        const impl = store[key]
        if (impl?.name !== service) continue
        providers.push({
          entry: impl.ctx.fiber.entry?.id ?? 'unknown', state: stateName(impl.ctx.fiber.state),
          visibility: key === scope ? 'same-scope' : 'other-scope',
        })
      }
      services.push({ consumer: state.id, service, providers })
    }
  }
  return {
    plugins: rows.slice(0, 256).map(row => row.state), services,
    omittedPlugins: Math.max(0, rows.length - 256), omittedServices,
  }
}
```

- `Loader.getTasks()` 返回各条目的 `_initTask` 或 `fiber.inertia`；`await()` 循环等待，直到没有待办任务（`vendor/loader/src/config/tree.ts:109-123`）。如果 intercept 配置了 `await: true` 且还有任务未完成，`Loader[Service.check]` 返回 false，依赖 `loader` 的插件就会停在 PENDING，直到树稳定（`vendor/loader/src/index.ts:65-69, 204-208`）。每个条目 init 完成时，如果已经没有其他任务，就执行 `reflect.notify(['loader'])`（`entry.ts:257-260`）。
- 找不到模块时，`EntryTree.import` 抛出 `ModuleNotFoundError`，`Entry._reportImport` 把错误位置指向 `name` 字段在文件中的位置（`tree.ts:30-42, 263-272`；`entry.ts:238-248`）。导入失败的条目没有 fiber，因为 `_init` 会直接 return（`entry.ts:263-270`）。
- 默认情况下，Loader 和 Include 的日志只写进 Cordis logger 的环形缓冲；没有 exporter 时，控制台上什么都看不到（`vendor/cordis/src/logger.ts:212-224`）。所以教程要求挂载 `@deepseek-ai/cordis-plugin-logger-console`（`06-composition-and-hmr.zh.md:42`）。本次实验的早期版本里，provider 报出的错误 `cannot set property "store" without provide` 也是加上 `ctx.logger.exporter(...)` 之后才看到的。
- 配置文件本身的错误（YAML 解析失败、顶层不是数组、条目不是对象）以 `ConfigFileError` 报告，带有 stage（read/parse/validate）和行列号（`vendor/include/src/index.ts:93, 142-156, 404-454`）。Include 读取失败时只发一条 warn，正在运行的树保持不变（`index.ts:378-391` 注释：「Half-written files are routine while the user is editing」）。

### Inferences
- 建议的排查顺序：
  1. 确认日志 exporter 已挂载。
  2. 确认条目有 fiber；导入失败的条目没有 fiber。
  3. 查看 fiber 状态：PENDING 时检查 inject 和 isolate scope；FAILED 时查看 `fiber.await()` 抛出的错误。
  4. 在 profile 模式下，用 `--dump-config` 核对最终的组合结果。

### Gaps
- 没有核对 dsh CLI 是否有现成的子命令可以输出 `captureStartupComposition` 的结果。目前只确认了它会在启动失败的路径上被调用：函数注释写的是「before a failed startup disposes its plugins」（`startup-diagnostics.ts:1`）。

---

## Q8 插件作者容易踩的坑（配置、组合、HMR）

### Takeaway
大部分坑出自四个事实：Config 只从插件对象上读取；patch 层会整块替换 `config`；FAILED 状态不会自愈；HMR 和 volatile 都只在特定条件下生效。

### Cited Findings
1. **`export default function` 搭配具名 `export const Config` 时，Config 会被静默丢弃**：插件收到的是 `undefined` 或未经校验的原始配置（E1，`vendor/loader/src/index.ts:232`；`registry.ts:326`）。正确写法有两种：不用 default，导出 `Config` 加 `apply`；或者 default 导出一个带 `static Config` 的类或对象。
2. **导出普通对象作为 `Config` 不起作用**，必须是 Standard Schema（`05-config.zh.md:34`），而且 schema 不能是异步的（`fiber.ts:84-86`）。
3. **默认值要写在 schema 里**：patch 按 id 覆盖时，`config` 会被整块替换，不做深合并（E5、`patch.ts:165-169`、`app-boot/README.md:210`）。用户层一旦覆盖你的行，bundle patch 里写的其他字段就没了。
4. **FAILED 不会自动恢复**：修好配置文件后，要靠文件协调触发 update；只修好依赖不够，必须显式 update（`fiber.ts:655-656`；`vendor/README.md` V25）。
5. **PENDING 状态下配置还没有校验**，所以错误会延迟暴露（`fiber.ts:275-277`）。
6. **volatile 更新只在以下条件全部满足时生效**：只有 config 变化、fiber 为 ACTIVE、context 没变、除 volatile 字段外原值相等、解析后普通字段的有效值相等。非法的 volatile 值只触发一条 warn，却会被写回文件（E3）。`loader/volatile-update` 是不等待的 emit，监听器抛出的异常会被吞掉（`entry.ts:155-211`）。
7. **不要长期缓存 volatile 的值**，每次使用时都调用 `.get()`（`05-config.zh.md:91`）。
8. **`!!js` 只在 `config` 和 `disabled` 中求值**。`disabled: !!js` 只在树协调时求值，环境变化本身不会触发重新求值（E2 step 3，`include/src/index.ts:411`）。`!!js` 能执行任意代码，patch 文件要当作代码对待。
9. **每一行都写显式 `id`**：匿名行一改自身配置就会被删除重建（E2 step 2），patch 层也无法定位它。在 4b399c3 上，编辑相邻行不会让匿名行重启，教程 `06-composition-and-hmr.zh.md:59` 的说法已经过时。
10. **后面层里同 id 的 `insert` 会整行替换前面层的行**，`name` 也一并替换（E6）。按 id 覆盖时，如果写了与目标不一致的 `name`，整个 patch 会被跳过，只留下一条 warn（E5）。
11. **插件自释放（`ctx.fiber.dispose()`）会把 `disabled: true` 写回用户文件**（E3，`vendor/loader/src/index.ts:188-194`）。对 bundle 插入行，这个修改写进的是内存中的父级 patch，重启后丢失（升级指南 `cordis-loader-entry-trees/guide.zh.md:17`）。
12. **条目上的 `inject` 会叠加到插件自己声明的 inject 上**：yml 里的一个笔误就能让插件永久停在 PENDING，而且没有任何提示（E3 中的 `needs`）。
13. **isolate 之后，服务只在同一个 realm 内可见**：`ctx.isolate(name)` 创建的上下文不会回退去使用外层插件 inject 的同名服务（`docs/upgrade-guide/v0.2.2-rc.2/cordis-root-and-intercept-metadata/guide.zh.md:20`）。用 `captureStartupComposition` 可以看到位于 `other-scope` 的 provider。
14. **HMR 相关**：
    - 必须挂载 timer，否则 HMR 会静默停在 PENDING。
    - 需要 `--expose-internals` 或 native addon。
    - dsh 默认 `root: []`，不对模块做热替换。
    - 修改进程入口的静态依赖会导致整个进程重启。
    - `node_modules` 里的包不参与依赖传播。
    - 模块顶层的副作用（定时器、连接、全局注册）在模块被逐出时不会被清理，必须放进 `apply` 或 `ctx.effect`。
    - 新建文件不会触发重载，因为主 watcher 只监听 `change`。
    - 出现语法错误时旧实例继续运行（E4 步骤 E），修复之后才会被替换。
15. **日志静默**：没有 logger exporter 时，Loader、Include、HMR 的错误和警告都不会显示（`vendor/cordis/src/logger.ts:212-224`）。

### Inferences
- 归纳成一条作者准则：插件要能用任意合法的 config 重新执行 `apply`，并在 `ctx.effect` 中完整清理资源。做到这一点后，配置变更、HMR 替换、isolate 变更都可以统一看作「卸载再加载」。唯一的例外是 volatile 字段，需要插件自己处理。

### Gaps
- 没有覆盖 client 侧的 `@deepseek-ai/dsh-client-hmr`（`packages/bundle/web-app/cordis.patch.yml:233-234`）。
