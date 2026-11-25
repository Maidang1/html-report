# dsh 插件解剖与开发教程（从 apply(ctx) 到工具、工具调用钩子、事件监听）

**Snapshot.** Repo `github.com/deepseek-ai/deepseek-harness`, main @ `4b399c39a6d1479cd84ca591b4d7c88e24d38a96` (committed 2026-11-22 16:50 +0800), cloned 2026-11-23. All `path:line` citations below are against this commit; GitHub URLs use `https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/<path>#L<a>-L<b>`. Abbreviated below as `[repo:path#Lx-Ly]`.

**Versions (developer preview).** In-repo `apps/cli/package.json` = `@deepseek-ai/dsh` `0.2.3-alpha.2` [repo:apps/cli/package.json#L2-L4]. npm dist-tags on 2026-11-23: `latest` = `next` = `0.2.2-rc.5`, `alpha` = `0.2.3-alpha.2` (`npm view @deepseek-ai/dsh dist-tags`). Vendored Cordis in repo = `4.2.0-alpha.1` [repo:vendor/cordis/package.json#L4], schemastery `3.20.0-alpha.1` [repo:vendor/schemastery/package.json#L4]; npm `@deepseek-ai/cordis` latest = `4.1.0`, and `@deepseek-ai/dsh@0.2.2-rc.5` depends on `@deepseek-ai/cordis ~4.1.0` and `@deepseek-ai/schemastery ~3.19.0`. So the published `latest` and main lag each other by one minor of Cordis.

**What was RUN vs READ.**
- READ: all docs and source cited.
- RUN (failed): full source build. `pnpm install` (pnpm 11.28.5 via corepack; host Node v24.22.2, while `package.json` engines require `^24.22.3 || >=26.13.1` [repo:package.json#L7-L9]) aborted with `ERR_PNPM_ENOSPC` on a 3.9 GB tmpfs (the monorepo resolves 1444 packages including `@deepseek-ai/libreoffice-kit-wasm`). So `pnpm run build` / `pnpm dsh web --patch …` was NOT executed, and the Web-UI end-to-end tutorial is NOT verified by me.
- RUN (succeeded): a minimal host built from published npm packages at the same version as main (`npm install @deepseek-ai/dsh-tools@0.2.3-alpha.2 @deepseek-ai/dsh-system-prompt@0.2.3-alpha.2 @deepseek-ai/dsh-tool-executor-native@0.2.3-alpha.2` → 20 packages, 13 MB, pulls cordis 4.2.0-alpha.1). Scripts at `/tmp/claude-1000/-Users-bytedance-codes-open-source-html-report/66d83128-a37c-4163-ae6b-f001409e9960/scratchpad/verify/run.mjs` and `run2.mjs`, run with `node run.mjs` / `node run2.mjs`. They mount `dsh-session-projection` → `dsh-system-prompt` → `dsh-tools` → `dsh-tool-executor-native` on a bare `new Context()`, then a user plugin with a `Config`, a `greet` tool, and one listener on each tool-pipeline stage, and drive `root.tools.execute({ calls, signal })` directly (no model, no API key). Outputs are quoted verbatim below as "[RUN]".

---

## 1. What a dsh plugin module is (name / inject / Config / apply) and the microkernel claim

### Takeaway
A dsh plugin is a plain Cordis plugin: a function, an object with `apply(ctx, config)`, or a class (usually a `Service`), optionally carrying `name`, `inject`, `Config` (any Standard-Schema validator; dsh uses its fork of Schemastery), plus less-documented `provide`/`intercept`. `apply` runs only after every `inject`ed service exists, and config is validated synchronously before `apply`. "Everything is a plugin" is the stated architecture — the model adapter, the tool registry, the session log and the agent loop itself are plugins.

### Cited Findings
- Tutorial definition: "a plugin is a TypeScript module that exports an `apply` function. The framework calls `apply` when loading the plugin and passes a `ctx` context object through which the plugin registers capabilities"; minimal shape is `export const name = 'my-plugin'` + `export function apply(ctx: Context) {}` and "That is the complete configuration." — [repo:docs/user/develop/basic/index.md#L15-L29](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/index.md#L15-L29)
- Three forms documented: function module, object form (`export default { name, inject, apply }`), class form (`export default class MyService extends Service { static inject = ['tools']; constructor(ctx) { super(ctx, 'myService') } }`); "Function form is sufficient in most cases. Use class form when the plugin provides a service to other plugins" — [repo:docs/user/develop/basic/index.md#L105-L138](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/index.md#L105-L138)
- Authoritative type (`Plugin.Base`): `name?` ("Display name used for fiber diagnostics and logger names"), `Config?: StandardSchemaV1<any, T>` ("Standard-schema validator applied to config before the plugin starts"), `inject?` ("Services the plugin requires; it only loads while all are available"), `provide?: string | string[]` ("Service name(s) the plugin provides (read by `Service` and by loaders)"), `intercept?: Dict<boolean>`. Function plugin = `(ctx, config)`, Constructor = `new (ctx, config)`, Object = `apply(ctx, config)` — [repo:vendor/cordis/src/registry.ts#L97-L132](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/registry.ts#L97-L132)
- `inject` may be an array or a name→config map (`Inject.resolve` normalizes array/object/class-inherited forms) — [repo:vendor/cordis/src/registry.ts#L65-L86](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/registry.ts#L65-L86)
- Config validation is synchronous Standard Schema: `runtime.Config['~standard'].validate(config)`; an async validator throws `TypeError('Async config validation is not supported')`; issues throw `ValidationError` — [repo:vendor/cordis/src/fiber.ts#L72-L91](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/fiber.ts#L72-L91)
- Schema library: docs say "Export a `Config` type and a same-named Schemastery schema", `import Schema from '@deepseek-ai/schemastery'`, and "Do not export a plain object as `Config`; it does not implement the Standard Schema interface required by Cordis." — [repo:docs/user/develop/basic/config.md#L7-L45](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/config.md#L7-L45). In-repo convention is `import z from '@deepseek-ai/schemastery'` (198 occurrences in `packages/**/*.ts`; 22 use `import Schema from …`), e.g. `ToolsService` [repo:packages/core/tools/src/index.ts#L3](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/index.ts#L3) and `tool-todo`'s `export const Config: z<Config> = z.object({ allowParallelInProgress: z.boolean().required() })` [repo:packages/todo/tool-todo/src/index.ts#L22-L43](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/todo/tool-todo/src/index.ts#L22-L43). Note: `zod` is also imported in ~180 places, but for data schemas, not plugin `Config` (my grep; not exhaustively classified).
- Fiber states: `PENDING` (waiting for required services), `LOADING` (callback running), `ACTIVE`, `FAILED` ("the callback or its config threw"), `DISPOSED`, `UNLOADING`; enum numeric order is PENDING=0, LOADING=1, ACTIVE=2, FAILED=3, DISPOSED=4, UNLOADING=5 — [repo:vendor/cordis/src/fiber.ts#L170-L185](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/fiber.ts#L170-L185)
- [RUN] `run2.mjs`: plugin `{ inject: ['tools'], apply }` mounted before `dsh-tools` → `state before tools mounted: 0` (PENDING, apply not called); after mounting tools → `[apply] runs now; ctx.tools = object`, `state after tools mounted: 2`; after `tf.dispose()` of the tools fiber → `[effect] cleanup`, `state after tools disposed: 0`; after re-mounting tools → apply runs again, `state ... 2`. — scratch `verify/run2.mjs`
- [RUN] `run.mjs`: object plugin with `Config: Schema.object({ greeting: Schema.string().default('Hello') })` mounted with `{ greeting: 'Hi there' }` printed `[apply] tools ready? true config= {"greeting":"Hi there"}`; mounting with `{ greeting: 42 }` rejected the `await root.plugin(...)` with `ValidationError invalid config:\n  - $.greeting expected string but got 42 (at greeting)`.
- Module shapes in the wild: `dsh-tool-executor-native` is a namespace-style module (`export const name = 'native-tool-executor'`, `export const inject = ['tools']`, `export function apply`), whereas `dsh-tools`, `dsh-system-prompt`, `dsh-session-projection` export a `default` Service class (observed via `import()` in RUN; source [repo:packages/core/tool-executor-native/src/index.ts#L27](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tool-executor-native/src/index.ts#L27)). Passing a namespace object that has only `default` to `ctx.plugin()` directly fails with `invalid plugin, expect function or object with an "apply" method, received object` [RUN] — the cordis.yml loader handles module resolution itself (not tested here).
- Microkernel claim, docs/architecture.md: "Every part of the product is a plugin, including the model adapter, the tool registry, the session log, and the agent loop itself, so each is replaceable from configuration. There is no privileged core to patch: you extend dsh by mounting a plugin beside the others, and registrations are effects that unwind when their plugin unloads." — [repo:docs/architecture.md#L7-L11](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/architecture.md#L7-L11). README: "built on an **everything-is-a-plugin** architecture and powered by Cordis" and citation title "DeepSeek Harness: Everything is a Plugin" — [repo:README.md#L7](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/README.md#L7), [#L65](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/README.md#L65)
- The checkable form of the claim is the "feature → mechanism map": "Every product feature maps to a listener on a documented extension point … No row modifies the loop." Rows include: hook system (user+project) → listeners on `agent/created`, `agent/pre-step`, `tools/pre-execute`, `tools/post-execute`, `agent/turn-stopping`, bridged by `dsh-hooks-claude-code` / `dsh-hooks-codex`; `/goal` → `ctx.goals` + `dsh-goal-round-driver`; `/loop` → `turn/end` session event + `followup()`; compaction → `ctx.compaction` + `dsh-compaction-basic` on serial `agent/pre-step` and `agent/request-error`; built-in tools → `ctx.tools.register()` (`dsh-tool-*` families bash, fs, web, subagent, todo); tool deadline/retry → `tools/execute`; permission/AskUserQuestion → `ask` from `tools/pre-execute` + `ctx.approval`; plan mode → `@deepseek-ai/dsh-plan-mode`; MCP → "one plugin per server: discover tools → `ctx.tools.register()`"; model adapters → `registerAdapter` (`dsh-llm-deepseek`, `dsh-llm-pi-ai`); hot reload → "every registration is a `ctx.effect` → vendored HMR just works" — [repo:docs/cookbook/extension-cookbook.md#L141-L175](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/extension-cookbook.md#L141-L175)
- A "native hook" is "an ordinary Cordis plugin on an interception point; it needs no external protocol." — [repo:docs/cookbook/extension-cookbook.md#L13](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/extension-cookbook.md#L13)
- Even `dsh-tools` is itself a Service plugin: `class ToolsService extends Service { static inject = ['systemPrompt'] … super(ctx, 'tools') }`, and its own `Config` uses `z.never()` to reject removed fields (`mode`, `maxParallelSubCalls`) — [repo:packages/core/tools/src/index.ts#L99-L118](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/index.ts#L99-L118)

### Inferences
- For the article: `name` is optional metadata (diagnostics/logger name), `inject` is the only thing that gates *when* `apply` runs, and `Config` must be a synchronous Standard Schema — Schemastery is the house choice; other Standard-Schema libraries (e.g. zod v3.24+/v4) should type-check against `StandardSchemaV1` but I did not run one.
- Because `ToolsService` injects `systemPrompt`, and `systemPrompt` injects `sessionProjections` [repo:packages/core/system-prompt/src/index.ts#L487](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/system-prompt/src/index.ts#L487), a plugin that `inject: ['tools']` transitively waits for that chain; in the shipped `web` profile it is all present.

### Gaps
- `intercept` and `provide` semantics are not explained in the user tutorials; not researched in depth (belongs to the services article).

---

## 2. The official tutorial path, in order (files, how to run, verbatim code)

### Takeaway
Official order: README "Run from source" → `docs/user/develop/basic/index.md` (first plugin, `scratch-plugin/`, `cordis.yml` overlay, `pnpm dsh web --patch`) → `basic/tool.md` (`greet` tool) → `basic/config.md` → `basic/publish.md` (other researcher) → `framework/index.md` (lifecycle) / `framework/service.md` / `framework/events.md` → cookbooks `extension-cookbook.md` and `adding-a-tool.md`. The tutorial is short; most of the contract lives in the cookbook and `packages/core/tools/README.md`.

### Cited Findings
- Run from source (verbatim):
  ```sh
  git clone https://github.com/deepseek-ai/deepseek-harness.git
  cd deepseek-harness
  pnpm install
  pnpm run build
  pnpm dsh web
  ```
  "`pnpm run build` prepares the repository artifacts. `pnpm dsh web` uses those built artifacts without rebuilding." — [repo:README.md#L29-L41](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/README.md#L29-L41). Binary install alternative: `npx @deepseek-ai/dsh web`, Web UI on `http://127.0.0.1:3080` — [repo:README.md#L20-L26](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/README.md#L20-L26)
- Toolchain: `"packageManager": "pnpm@11.28.5"`, `"engines": { "node": "^24.22.3 || >=26.13.1" }` — [repo:package.json#L7-L9](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/package.json#L7-L9). pnpm uses "its strict symlinked linker so undeclared dependencies fail instead of relying on hoisting"; `pnpm install` also installs worktree-local Lefthook hooks — [repo:docs/development.md#L26-L36](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/development.md)
- Step 1 — scratch dir: `mkdir -p scratch-plugin/src` from the repo root — [repo:docs/user/develop/basic/index.md#L7-L13](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/index.md#L7-L13)
- Step 2 — `scratch-plugin/src/my-plugin.ts` (verbatim):
  ```ts
  import type { Context } from '@deepseek-ai/cordis'

  export const name = 'hello-plugin'

  export function apply(ctx: Context) {
    // Required dependencies are ready before apply runs.
    console.log('[hello-plugin] plugin loaded!')
  }
  ```
  — [repo:docs/user/develop/basic/index.md#L31-L44](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/index.md#L31-L44)
- Step 3 — `scratch-plugin/cordis.yml` (verbatim):
  ```yaml
  - insert:
      - id: hello
        name: '/absolute/path/to/deepseek-harness/scratch-plugin/src/my-plugin.ts'
  ```
  "The plugin path must be absolute. A patch file contributes configuration but does not change the profile directory from which the loader resolves module paths." Run: `pnpm dsh web --patch ./scratch-plugin/cordis.yml`; open `http://127.0.0.1:3080`; "The terminal prints `[hello-plugin] plugin loaded!` during startup." — [repo:docs/user/develop/basic/index.md#L46-L64](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/index.md#L46-L64)
- Step 4 — tool tutorial, replace `my-plugin.ts` with (verbatim):
  ```ts
  import type { Context } from '@deepseek-ai/cordis'
  import { defineTool } from '@deepseek-ai/dsh-tools'

  export const name = 'greet-tool'
  export const inject = ['tools']

  export function apply(ctx: Context) {
    ctx.tools.register(defineTool({
      name: 'greet',
      description: 'Greet someone by name.',
      parameters: {
        name: { type: 'string', required: true, description: 'The name to greet' },
      },
      output: {
        schema: { type: 'string' },
        render: (_args, value) => [{ type: 'text', text: value }],
      },
      async execute(args) {
        return `Hello, ${args.name}!`
      },
    }))
  }
  ```
  "`inject` makes Cordis wait for the tool registry. `defineTool` infers and validates `args` from `parameters`; `execute` returns the canonical value declared by `output.schema`, and `output.render` converts that value to model-facing content." Then ask the Web UI "`Use the greet tool to greet Ada.`" and the model receives `Hello, Ada!` — [repo:docs/user/develop/basic/tool.md#L7-L46](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/tool.md#L7-L46)
- Step 5 — config tutorial (verbatim code):
  ```ts
  import type { Context } from '@deepseek-ai/cordis'
  import Schema from '@deepseek-ai/schemastery'

  export const name = 'my-plugin'

  export interface Config {
    greeting: string
    maxRetries: number
    verbose?: boolean
  }

  export const Config: Schema<Config> = Schema.object({
    greeting: Schema.string().default('Hello'),
    maxRetries: Schema.number().default(3),
    verbose: Schema.boolean().default(false),
  })

  export function apply(ctx: Context, config: Config) {
    console.log(config.greeting)  // User value or schema default.
  }
  ```
  with YAML `config: { greeting: 'Hi there', maxRetries: 5 }` under the inserted row; design rules "anything that two deployments may want to set differently [must] be a configuration field" and "Fail loudly on invalid configuration"; "A configuration edit hot-replaces the plugin" — [repo:docs/user/develop/basic/config.md#L7-L100](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/config.md#L7-L100)
- Tool authoring reference minimal shape (cookbook, verbatim excerpt): `read_file` tool with `parameters: { path: { type: 'string', required: true, … }, limit: { type: 'number' } }` and `async execute(args, exec) { return readFile(args.path, { encoding: 'utf8', signal: exec.signal }) }`; "Registration is effect-based: disposing the plugin fiber unregisters the tool. Schemas flow into the system-prompt assembly automatically." — [repo:docs/cookbook/adding-a-tool.md#L7-L39](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/adding-a-tool.md#L7-L39). Production reference: "`packages/shell/tool-bash` is the production-grade three-package example" — [#L5](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/adding-a-tool.md#L5)
- Extension cookbook explicitly says its snippets "omit imports and helper implementations and are not copy-paste-complete" — [repo:docs/cookbook/extension-cookbook.md#L5](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/extension-cookbook.md#L5)
- Optional overlays live under `apps/cli/config/examples/`; shipped profile layers via `packages/bundle/*/cordis.patch.yml` — [repo:docs/cookbook/extension-cookbook.md#L137-L139](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/extension-cookbook.md#L137-L139)
- The tutorials also point to a no-API-key path: "[Cordis tutorial] — the plugin framework underneath, built from a scratch directory with no API key" — [repo:docs/user/develop/basic/index.md#L144](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/index.md#L144)

### Inferences
- A self-contained "no Web UI, no API key" verification is possible by mounting `dsh-session-projection` + `dsh-system-prompt` + `dsh-tools` + an executor on a bare `Context` and calling `ctx.tools.execute()` (what `run.mjs` does). This is a good sidebar for the article, as the full source build needs several GB of disk.

### Gaps
- Not verified end-to-end: `pnpm dsh web --patch` with the scratch plugin (build failed for disk space).
- Possible resolution pitfall (unverified): the tool tutorial does a *value* import `import { defineTool } from '@deepseek-ai/dsh-tools'` from `scratch-plugin/src/` at the repo root, but the root `package.json` does not declare `@deepseek-ai/dsh-tools` and there are no hoisting settings in `pnpm-workspace.yaml` / no `.npmrc`, so plain Node resolution from `scratch-plugin/` may not find it under pnpm's strict linker. Whether the dsh loader resolves it another way was not tested. The first tutorial only uses `import type`, which is erased.

---

## 3. Tool registration API and contracts (`ctx.tools.register`, `defineTool`, results, errors)

### Takeaway
`ctx.tools.register(definition, { when? }) → disposer`; a definition must carry `output: { schema, render, presentationMeta? }`; `defineTool` adds typed args, argument validation (inside `execute`) and output typing. `execute` returns one canonical JSON value; throwing yields an `isError` result. There is no per-tool "permission" flag — approval is a `tools/pre-execute` decision (`ask`) answered by `ctx.approval`.

### Cited Findings
- Signature and checks: `register(definition: ToolDefinition, options: ToolRegistrationOptions = {}): () => void`; throws `TypeError` if `output` is missing or `render` is not a function (`tool "${name}" must declare output { schema, render, presentationMeta? }`), validates `output.schema` with `assertSupportedJsonSchema`, requires `timeoutMs` to be a positive finite number, and refuses the reserved name `run_code`; "Scoped tools shadow globals; duplicates within one layer … fail"; registration goes through `this.layers.effect(this.ctx, …, { label: 'tools.register()' })` — [repo:packages/core/tools/src/index.ts#L120-L152](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/index.ts#L120-L152)
- `ToolDefinition extends ToolSchema` with: `output: ToolOutputDefinition` (`schema: JsonSchemaNode`, pure `render(args, value): ContentBlock[]`, optional `presentationMeta`), `execute(args, exec: ToolRunContext): Promise<unknown>`, optional `projectContent`, `finalizeContent`, `timeoutMs` ("Enforced by `@deepseek-ai/dsh-tool-call-timeout-policy` (a `tools/execute` wrapper); it is NEVER sent to the model"), `isConcurrencySafe(args)` (only `true` opts into parallel), `presentCall`, `presentResult` — [repo:packages/core/tools/src/contracts.ts#L16-L103](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/contracts.ts#L16-L103)
- Conditional tools: `register(definition, { when })` where `when({ agent, modelSelection, signal })` is evaluated once per model step; false omits the tool from description, PTC bindings and execution; a thrown condition fails step preparation as `TOOL_ACTIVATION_FAILED` — [repo:packages/core/tools/README.md#L66-L70](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/README.md#L66-L70); types [repo:packages/core/tools/src/contracts.ts#L105-L150](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/contracts.ts#L105-L150)
- Schema DSL: `string`, `number`, `integer`, `boolean`, `null`, `array`, `object`, author-only `json`, exact-one `oneOf`; `InferValue` exact through 16 container levels; a raw `JsonSchemaNode` is the wire-level counterpart (subagents, workflows, MCP) — [repo:packages/core/tools/README.md#L64](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/README.md#L64). "Raw JSON-Schema `ToolDefinition`s are also accepted by `ctx.tools.register()` directly (that is how MCP-sourced tools arrive)" — [repo:docs/cookbook/extension-cookbook.md#L9](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/extension-cookbook.md#L9)
- `execute()` contract rules (cookbook): args validated by `defineTool` before the user body (types, required, literals, exact-one unions, nested); still hand-check non-empty strings / positive numbers / cross-field rules and reject with `ToolArgsError`; "Raw JSON-Schema tools registered directly own their input validation"; do not mutate the definition after registration — to hot-swap, dispose and re-register; `arguments` frozen, identity fields immutable, only a `tools/execute` wrapper may replace `exec.signal`; "Declare and return one canonical JSON value … Do not return content blocks from the body"; "Throwing or returning an invalid value means `isError`" and "Throw a `HarnessError` subclass with a stable `code` for every expected failure; a thrown `HarnessError` is the only tool-body outcome that keeps `error.name` and `error.code` on the result"; "Honor `exec.signal`" (aborts become `ABORTED`); `exec.agent.inject({ content, source: { kind: 'my-plugin' } })` appends context for the NEXT request and "is not a wake-up"; declare that `kind` through `MessageSourceMap` because session format V4 rejects `{ kind: 'plugin', plugin: '<name>' }` — [repo:docs/cookbook/adding-a-tool.md#L41-L50](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/adding-a-tool.md#L41-L50)
- Result shape: `ToolExecutionResult = ToolExecutionSuccess | ToolExecutionFailure`; success `{ isError: false, value: JsonValue, content, meta?, additionalContexts?, concludesTurn?: true }` ("`value` … deliberately omitted from durable events"); failure `{ isError: true, error: { message, info?: { name, code, reason? } }, content, meta?, additionalContexts? }` — [repo:packages/core/tools/src/contracts.ts#L270-L364](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/contracts.ts#L270-L364)
- `ToolRunContext` extends `ToolExecution` with `deferContext(UserMessage)` and `concludeTurn()` (mark a successful result terminal for the turn) — [repo:packages/core/tools/src/contracts.ts#L237-L262](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/contracts.ts#L237-L262)
- Error classes/codes: `ToolNotFoundError` (`UNKNOWN_TOOL`), `ToolContextUnavailableError` (missing calling Agent / injected service / open turn), `ToolOutputError` (`INVALID_TOOL_OUTPUT`), `ToolActivationError` (`TOOL_ACTIVATION_FAILED`), `TOOL_ABORTED = 'ABORTED'`, `TOOL_ABORTED_BEFORE_DISPATCH` — [repo:packages/core/tools/src/contracts.ts#L139-L337](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/contracts.ts#L139-L337); `ToolArgsError` lives in `schema.ts` [repo:packages/core/tools/src/schema.ts#L462-L468](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/schema.ts#L462-L468), and argument validation is inside `defineTool`'s generated `execute` (`if (violations.length > 0) throw new ToolArgsError(violations)`) [repo:packages/core/tools/src/schema.ts#L555-L603](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/schema.ts#L555-L603)
- Permission / approval semantics: `PreToolDecision` `ask` "runs only after an approval service returns `allowed-once` and otherwise denies"; input rewriting is deliberately excluded "because arguments are already logged and presented" — [repo:packages/core/tools/src/contracts.ts#L366-L379](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/contracts.ts#L366-L379). Implementation: `serviceAsk` reads `ctx.get('approval')` opportunistically; missing service → deny `APPROVAL_UNAVAILABLE` ("requires approval (not yet supported)"); no agent → deny; `allowed-once` → allow; rejected → `the user rejected tool "…"` (`APPROVAL_REJECTED`); cancelled → `APPROVAL_CANCELLED` — [repo:packages/core/tools/src/pipeline.ts#L440-L492](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L440-L492)
- Restriction and inspection: `ctx.tools.restrict({ allow?, deny? })` masks global tools for one agent scope; `get(name, scope)`, `schemas(scope)` — [repo:packages/core/tools/README.md#L86-L88](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/README.md#L86-L88); one executor must be mounted (`dsh-tool-executor-native` or PTC), otherwise `ToolExecutorUnavailableError` (`TOOL_EXECUTOR_UNAVAILABLE`) — [repo:packages/core/tools/README.md#L75-L84](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/README.md#L75-L84), [repo:packages/core/tools/src/executor.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/executor.ts)
- PTC mode: every visible tool becomes `await tools.<name>(args)` resolving to the canonical value after policy; failures reject with `ToolCallError` — so design `output.schema` as a programmatic API — [repo:docs/cookbook/adding-a-tool.md#L62-L66](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/adding-a-tool.md#L62-L66)
- UI: `presentCall`/`presentResult` return `card`-tagged intents (`generic`, `terminal`, `diff`, `read`, `search`, `web`) and must be pure (live + replay); but "The built-in Web Client does not consume `presentCall` or `presentResult`" — a Web card is registered as a Client plugin in the `tool.call.toolview` keyed slot — [repo:docs/cookbook/adding-a-tool.md#L68-L98](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/adding-a-tool.md#L68-L98)
- Long-running work: gate `run_in_background` in config, use `ctx.jobs.start({ kind, label, owner: exec.agent, run })`, return `{ kind: 'background', jobId }` — [repo:docs/cookbook/adding-a-tool.md#L52-L56](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/adding-a-tool.md#L52-L56)
- [RUN] Results observed: `greet(Ada)` → `{"isError":false, content:[{"type":"text","text":"Hi there, Ada!"}]}`; body throws `new Error('kaboom')` → `isError true … "Error: kaboom"`, `error {"message":"kaboom"}` (plain Error → no `info`, consistent with "only HarnessError keeps name/code"); `arguments: '{}'` → `{"isError":true,"error":{"message":"invalid arguments: missing required property \"name\"","info":{"name":"ToolArgsError","code":"INVALID_ARGS"}}, …}`; after plugin `fiber.dispose()` → `schemas()` is `[]` and calling `greet` gives `ToolNotFoundError` / `UNKNOWN_TOOL`. Args seen by the body were frozen (`frozen? true`).

### Inferences
- "Permission" in dsh is not a tool attribute; a tool plugin stays policy-free and a separate hook plugin decides (`allow/deny/ask/cancel`). The approval UI is reached only if an `approval` service is mounted (the Web profile has one; a bare host turns `ask` into deny).

### Gaps
- Exact `ParameterSchemaSpec` fields beyond the README list (e.g. `enum`, `minLength`) not enumerated here; see `packages/core/tools/src/schema.ts`.

---

## 4. Tool-call pipeline extension points: names, order, powers, how to reject

### Takeaway
Verified order (source + RUN): `tools/pre-execute` (waterfall) → `ctx.tools.guard()` guards (only if pre-execute allowed) → `tools/execute` (waterfall around the body; arg validation happens inside here) → tool body → `projectContent` → `tools/post-execute` (waterfall) → `finalizeContent` → `tools/result` (emit, frozen, observe-only). Reject with `{ kind: 'deny', reason }` in pre-execute, a returned string from a guard, or `{ kind: 'block', feedback }` in post-execute; all become `isError` results, and denials still flow through post-execute and result. This is already covered in depth in `deepseek/dsh_services_events_config_plugin.md` §"一次工具调用经过的扩展点" and §"参数校验不在流水线前段" — the plugin article should summarize and link.

### Cited Findings
- README order statement: "`tools/pre-execute` (extensible allow/deny/ask) → registered monotonic guards → `tools/execute` (around-dispatch wrappers) → `tools/post-execute` (inspect/replace, attach context) → definition-owned `finalizeContent` → the observe-only `tools/result` event. Only the `tools/execute` view may replace the required signal" — [repo:packages/core/tools/README.md#L112](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/README.md)
- Event declarations with modes (`@mode waterfall` for pre-execute / execute / post-execute; `@mode emit` for `tools/result` and `tools/change`), signatures: `'tools/pre-execute'(exec: ToolExecution, next: () => Promise<PreToolDecision>)`, `'tools/execute'(exec: ToolDispatchExecution, next: () => Promise<ToolExecutionResult>)`, `'tools/post-execute'(exec, result: Readonly<ToolExecutionResult>, next: () => Promise<PostToolDecision>)`, `'tools/result'(exec, result): undefined`; all four call events are "Scope-filtered dispatch … agent-scoped listeners receive only that agent's calls"; `tools/change` is unfiltered — [repo:packages/core/tools/src/events.ts#L6-L57](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/events.ts#L6-L57)
- Source of order: `prepare()` runs `this.ctx.waterfall(carrier, 'tools/pre-execute', exec, () => Promise.resolve({ kind: 'allow' }))`; `ask` → `serviceAsk`; `cancel` → `ABORTED_BEFORE_DISPATCH`; `const denial = decision.kind === 'allow' ? this.tools.checkGuards(exec) : decision`; a denial becomes `{ content: [{ type: 'text', text: \`Error: ${denialReason}\` }], isError: true, error: { message, info? } }` with stage `post-result` — [repo:packages/core/tools/src/pipeline.ts#L185-L233](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L185-L233). "A `post-result` receives post-execute; a `final-result` bypasses it." — [#L32-L35](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L32-L35). `tools/execute` waterfall at [#L310](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L310); body dispatch fuses caller signal back in [#L254-L295](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L254-L295); `projectContent` then `postExecute` then `finish` [#L368-L382](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L368-L382); `finish` → `applyFinalContent` (finalizeContent) → `notifyResult` [#L384-L418](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L384-L418)
- `tools/result` dispatch: `Object.freeze(exec)` then `ctx.events.dispatch('emit', …)`, each callback in its own try; sync throws and async rejections are logged via `this.ctx.logger.warn(\`tool "${toolName}" (${callId}): tools/result observer failed: …\`)` and do not affect the outcome — [repo:packages/core/tools/src/pipeline.ts#L420-L438](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L420-L438)
- Guards: `ctx.tools.guard(guard: ToolGuard): () => void`, effect-owned; `checkGuards` iterates the global layer then the agent's scope chain and returns the first denial (string → `{ reason }`) — [repo:packages/core/tools/src/index.ts#L380-L404](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/index.ts#L380-L404). `ToolGuard = (execution: Readonly<ToolExecution>) => string | ToolGuardDenial | undefined` — synchronous; "Because guards have no allow result, listener ordering cannot turn a denial back into permission." — [repo:packages/core/tools/src/contracts.ts#L402-L418](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/contracts.ts#L402-L418)
- Post decision: `{ kind: 'accept'; content? }` | `{ kind: 'accept'; value }` | `{ kind: 'block'; feedback; info?; additionalContexts? }`; runtime enforces "accept decision cannot replace both value and content" and "cannot replace the value of a failed result" (TypeErrors) — [repo:packages/core/tools/src/contracts.ts#L381-L389](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/contracts.ts#L381-L389), [repo:packages/core/tools/src/pipeline.ts#L508-L531](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L508-L531)
- Selection rule from docs: "Use `tools/pre-execute` for extensible allow/deny/ask policy …, `ctx.tools.guard()` for a final monotonic deny that later listeners cannot undo, `tools/execute` to wrap dispatch with a deadline, retry, or metrics collection, `tools/post-execute` to replace presentation content or the returned value, block the result, or attach model-facing context, and `tools/result` to observe the immutable normalized outcome. A content replacement leaves programmatic access to `value` intact; confidentiality policy blocks or replaces the value." — [repo:docs/cookbook/adding-a-tool.md#L58-L60](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/adding-a-tool.md#L58-L60)
- Official permission-gate example (verbatim):
  ```ts
  import type { Context } from '@deepseek-ai/cordis'
  import type { PreToolDecision, ToolExecution } from '@deepseek-ai/dsh-tools'

  declare function isAllowed(exec: ToolExecution): Promise<boolean>

  export const name = 'permission-gate'

  export function apply(ctx: Context) {
    ctx.on('tools/pre-execute', async (exec, next): Promise<PreToolDecision> => {
      if (!(await isAllowed(exec))) {
        return { kind: 'deny', reason: 'Denied by policy.' }
      }
      return next()
    })
  }
  ```
  — [repo:docs/cookbook/extension-cookbook.md#L11-L33](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/extension-cookbook.md#L11-L33)
- Real built-in `tools/execute` wrapper — `packages/guard/timeout-policy` (81 lines): `name = 'timeout-policy'`, `inject = ['tools']`; reads `ctx.tools.get(exec.name, exec.agent)?.timeoutMs`; if none, `return next()`; otherwise `using d = deadline(exec.signal, timeoutMs, TOOL_TIMEOUT)`, swaps `exec.signal = d.signal`, `await next()`, replaces the result with a `ToolTimeoutError`/`TOOL_TIMEOUT` failure if its own timer fired, and restores `exec.signal = upstream` in `finally` — [repo:packages/guard/timeout-policy/src/index.ts#L25-L81](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/guard/timeout-policy/src/index.ts#L25-L81)
- Real built-in pre/post hooks — `dsh-hooks-claude-code` maps PreToolUse to `{ kind: 'deny', reason, info: { name: HOOK_ERROR_NAME, code: HOOK_DENIED } }` / `{ kind: 'ask', reason? }` / `next()`, and PostToolUse to `{ kind: 'block', feedback, info, additionalContexts? }`, otherwise it *delegates first* (`const downstream = await next()`) and folds its context onto the downstream decision "so a later listener can still block/replace" — [repo:packages/experimental/hooks-claude-code/src/index.ts#L246-L282](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/experimental/hooks-claude-code/src/index.ts#L246-L282). Other in-repo `tools/pre-execute` users: `hooks-codex`, `auto-review`, `liveshare/fork-enforcement`, `deliverables/workspace-changes`, `tool-jobs`; `ctx.tools.guard(` users: `subagent/structured.ts`, `liveshare/fork-tool-guard.ts`, `creation-recommendations`, `timeline` (grep).
- [RUN] Order trace for an allowed call (verbatim):
  ```
  === call greet(Ada)
    1 [pre-execute] greet {"name":"Ada"}
    2 [guard] greet
    3 [execute wrapper] before
    [execute body] args= {"name":"Ada"} frozen? true
    3 [execute wrapper] after isError= false
    4 [post-execute] isError= false value= "Hi there, Ada!"
    5 [tools/result] frozen? true content= [{"type":"text","text":"Hi there, Ada!"}] error= undefined
  ```
  pre-execute deny (`Mallory`): `1 [pre-execute]` → `4 [post-execute] isError= true` → `5 [tools/result] … "Error: Mallory is blocked by policy."` (guard and execute skipped). Guard deny (`Eve`): `1` → `2 [guard]` → `4` → `5 … "Error: Eve denied by guard"`. Post-execute `{ kind: 'accept', value: 'HI THERE, SHOUT!' }` → final content `[{"type":"text","text":"HI THERE, SHOUT!"}]` (the value replacement is re-rendered through `output.render`). Invalid args `{}`: `1` → `2` → `3 before` → (no body log) → `3 after isError= true` → `4` → `5` with `ToolArgsError`/`INVALID_ARGS`.

### Inferences
- Pitfall for gate authors: pre-execute listeners and guards see *unvalidated* model arguments (validation happens inside `defineTool`'s `execute`), so a gate must defensively read `exec.arguments` (the services article reports that unparseable JSON even arrives as a raw string; I did not re-test that case).
- Denials are visible to post-execute observers (stage `post-result`), so a post-execute listener should check `result.isError` before assuming the tool ran.
- `tools/result` is the right place for logging/audit but not for anything that must complete before the next step (it is not awaited).

### Gaps
- Relative ordering among multiple listeners on the same waterfall (registration order, scope chain) is covered in the services article §"顺序、thisArg 和作用域过滤"; not re-verified here.

---

## 5. Event listening (`ctx.on`), session/UI events, logging, automatic cleanup

### Takeaway
`ctx.on(name, handler)` is the only listening API a plugin author needs; listeners are effects removed on unload. For session activity listen to the Cordis event `session/event` and switch on `event.type` — `turn/*`, `step/*`, `tool/call`, `tool/result`, `compaction/*` are *session-record types*, not Cordis events. Live tokens come from `agent/assistant-stream`. `ctx.logger` is Cordis's built-in logger service; output needs an exporter plugin (e.g. vendored `logger-console`). Cordis 4 has no `ctx.on('dispose')` — use `ctx.effect()`.

### Cited Findings
- "Harness Cordis events use `namespace/action` names, including `agent/pre-step`, `agent/request-error`, `tools/result`, and `session/event`. … `turn/*`, `step/*`, `tool/call`, `tool/result`, and `compaction/*` are durable session-event types, not same-named Cordis events. To observe them, listen to `session/event` and inspect `event.type`." — [repo:docs/user/develop/framework/events.md#L102-L106](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/framework/events.md#L102-L106)
- `'session/event'(this: Scoped<Session>, session: Session, event: SessionEvent): void`, `@mode emit`; agent-scoped listeners only receive events from sessions entered through that agent's context; sibling `session/flush` is `@mode parallel` — [repo:packages/core/session/src/index.ts#L78-L93](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/session/src/index.ts#L78-L93). Real listeners: `session-title` [repo:packages/session/session-title/src/index.ts#L347](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/session/session-title/src/index.ts#L347), `session-telemetry` coordinator [#L104](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/session/session-telemetry/src/coordinator.ts#L104), `session-telemetry-otel` [#L159](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/session/session-telemetry-otel/src/index.ts#L159)
- Official logging-plugin example (verbatim):
  ```ts
  import type { Context } from '@deepseek-ai/cordis'
  import '@deepseek-ai/dsh-tools'

  export const name = 'tool-logger'

  export function apply(ctx: Context) {
    ctx.on('tools/result', (exec, result) => {
      console.log(`[tool] ${exec.name}(${JSON.stringify(exec.arguments)})`)
      const text = result.content
        .map(block => block.type === 'text' ? block.text : '')
        .join('')
      console.log(`[tool result] ${text.slice(0, 100)}`)
    })
  }
  ```
  (the side-effect import `import '@deepseek-ai/dsh-tools'` is there to pull in the `Events` declaration merging for typing) — [repo:docs/user/develop/framework/events.md#L119-L138](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/framework/events.md#L119-L138)
- UI plugin pattern: `ctx.on('agent/assistant-stream', ({ frame }) => { if (frame.type === 'chunk' && frame.chunk.type === 'text-delta') render(frame.chunk.text) })` and input via `ctx.agents.get(id)?.followup(createUserMessage({ content: [{ type: 'text', text }], source: { kind: 'user' } }))` with `inject = ['agents']`; protocol driver listens to `session/event` and expands `assistant/message` / `assistant/attempt` with `expandAssistantStream` — [repo:docs/cookbook/extension-cookbook.md#L35-L92](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/extension-cookbook.md#L35-L92)
- Typed custom events via declaration merging on `@deepseek-ai/cordis`'s `Events` interface — [repo:docs/user/develop/framework/events.md#L83-L100](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/framework/events.md#L83-L100). Event modes (emit/bail/serial/waterfall; "A waterfall listener **must call `next()`**") — [#L23-L81](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/framework/events.md#L23-L81) — covered in depth by the existing services article; link rather than repeat.
- Automatic cleanup: "Anything registered through `ctx`—event listeners, tools, or timers—is cleaned up when the plugin unloads"; use `ctx.effect(() => { …; return () => cleanup })` for explicit resources — [repo:docs/user/develop/basic/index.md#L66-L85](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/index.md#L66-L85); tracked set: `ctx.on`, `ctx.tools.register`, `ctx.llm.registerAdapter`, `ctx.effect` — [repo:docs/user/develop/framework/index.md#L40-L63](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/framework/index.md#L40-L63) (disposal order belongs to the lifecycle article `deepseek/dsh_lifecycle_and_effects.md`)
- Logger: `Context` constructor sets `this.logger = new LoggerService(self)`; doc comment "The logging service. Call `ctx.logger(name)` for a named logger." — [repo:vendor/cordis/src/context.ts#L27-L28](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/context.ts), [#L83](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/context.ts#L83); levels `'error' | 'info' | 'warn' | 'debug'` — [repo:vendor/cordis/src/logger.ts#L7-L8](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/logger.ts#L7-L8); dsh itself uses `this.ctx.logger.warn(...)` directly (pipeline.ts L426). Console output requires an exporter: vendored `logger-console` "Console exporter for the built-in Cordis logger service" with `levels`, `colors`, `showDiff` config — [repo:vendor/logger-console/README.md](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/logger-console/README.md)
- Built-in framework events in Cordis 4 are `internal/plugin`, `internal/status`, `internal/service`, `internal/update`, `internal/commit`, `internal/config`, `internal/listener`, `internal/dispatch` … — no `dispose`/`ready` events — [repo:vendor/cordis/src/events.ts#L330-L360](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/events.ts#L330-L360)
- [RUN] `ctx.logger.info('hello from ctx.logger')` in a bare host (no exporter) printed nothing and did not throw; `ctx.on('dispose', …)` never fired on `fiber.dispose()`, while `ctx.effect` cleanup did print `[effect] cleanup`.

### Inferences
- Authors coming from Koishi/older Cordis will reach for `ctx.on('ready')` / `ctx.on('dispose')`; in dsh's Cordis 4 the idiom is "do setup in `apply`, teardown via the disposer returned from `ctx.effect`".
- For a plugin-side log line that shows up in `pnpm dsh web` output, `ctx.logger` is the intended API (fiber/plugin-named), whereas the tutorials themselves use `console.log` for simplicity.

### Gaps
- Which exporter/levels the `web` profile configures for `ctx.logger` was not checked (bundle/profile is the other researcher's track).
- Full list of `agent/*` Cordis events and their modes is in the generated `cordis-surface` regions of `docs/subsystems/*.md`; not enumerated here.

---

## 6. Pitfalls and docs-vs-source discrepancies

### Takeaway
Most pitfalls are contract-level (unvalidated args at the gate, waterfall `next()`, HarnessError for stable codes, pure presenters, must mount an executor). Several doc statements drift from source/behaviour: service-loss state (PENDING, not DISPOSED), absolute vs relative plugin path, and the `cordis.yml` "complete configuration" claim.

### Cited Findings
- Doc says on lost dependency "the plugin unloads automatically (ACTIVE → DISPOSED) and loads again when the service returns" — [repo:docs/user/develop/framework/index.md#L38](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/framework/index.md#L38). Source comment says `DISPOSED` = "the fiber was removed and cannot restart" [repo:vendor/cordis/src/fiber.ts#L170-L176](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/fiber.ts#L170-L176), and [RUN] showed state going ACTIVE(2) → PENDING(0) → ACTIVE(2), with effect cleanup and re-run of `apply`. So the doc's "DISPOSED" is inaccurate; the fiber returns to PENDING.
- Path contradiction: `basic/index.md` says "The plugin path must be absolute. A patch file … does not change the profile directory from which the loader resolves module paths" [#L56](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/index.md#L56), but `basic/config.md`'s YAML uses `name: './src/my-plugin.ts'` for the same `scratch-plugin/cordis.yml` row [#L34-L43](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/config.md#L34-L43). Follow the absolute path.
- Tutorial's "Three plugin forms" class example has no `Config`, and `basic/index.md` never mentions `Config`; the `apply(ctx, config)` second parameter only appears in `config.md` — [repo:docs/user/develop/basic/config.md#L29](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/basic/config.md#L29)
- Waterfall contract: "A waterfall listener **must call `next()`**. Omitting it short-circuits the pipeline by design" — [repo:docs/user/develop/framework/events.md#L79-L81](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/user/develop/framework/events.md#L79-L81). Corollary from the hooks-claude-code example: delegate with `await next()` before adding context, so later listeners can still block [repo:packages/experimental/hooks-claude-code/src/index.ts#L268-L270](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/experimental/hooks-claude-code/src/index.ts#L268-L270)
- `tools/execute` wrappers "may change only `exec.signal` … must still restore their signal and reach quiescence" — [repo:packages/core/tools/src/events.ts#L19-L29](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/events.ts#L19-L29)
- Async gates "must observe `exec.signal`; the registry rechecks cancellation after they settle but never abandons their promise" — [repo:packages/core/tools/src/events.ts#L8-L14](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/events.ts#L8-L14)
- `ask` without an `approval` service degrades to deny (`APPROVAL_UNAVAILABLE`, message "requires approval (not yet supported)") — [repo:packages/core/tools/src/pipeline.ts#L457-L462](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/pipeline.ts#L457-L462)
- Presenters must be pure ("NO I/O, NO reading session state, NO clock/random"); `defineTool` soft-validates the display path and returns `undefined` on malformed args — [repo:docs/cookbook/adding-a-tool.md#L86-L90](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/adding-a-tool.md#L86-L90)
- Do not mutate a registered definition; hot-swap by disposing and re-registering — [repo:docs/cookbook/adding-a-tool.md#L44](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/adding-a-tool.md#L44)
- `agent.inject()` is not a wake-up; legacy `{ kind: 'plugin', plugin: '<name>' }` source is rejected by session format V4 — [repo:docs/cookbook/adding-a-tool.md#L50](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/adding-a-tool.md#L50)
- `run_code` is a reserved tool name; `timeoutMs` must be positive finite; output declaration is mandatory even for raw JSON-Schema tools — [repo:packages/core/tools/src/index.ts#L128-L147](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/packages/core/tools/src/index.ts#L128-L147)
- `system-prompt/assemble` is an "expert cooperative whole-assembly transform"; prefer `ctx.tools.restrict()` for tool filtering — [repo:docs/cookbook/extension-cookbook.md#L145](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/docs/cookbook/extension-cookbook.md#L145)
- Async `Config` validators are rejected (`Async config validation is not supported`) — [repo:vendor/cordis/src/fiber.ts#L80-L86](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c3/vendor/cordis/src/fiber.ts#L80-L86)
- The existing article `deepseek/dsh_services_events_config_plugin.md` (same commit 4b399c3) already documents: argument validation sits inside `tools/execute`; `tools/result` is emitted before `execute()`'s promise resolves and is not awaited; a Cordis tutorial step (07) fails on 4b399c3 because no executor is in any `inject` ("没有 executor，连 system prompt 都组装不了"), plus a 28-item pitfall list — link to it instead of re-deriving (local file, sections at lines ~445, ~823, ~974, ~1433).

### Inferences
- Minimum practical composition for a tool plugin outside the Web profile: `dsh-session-projection` + `dsh-system-prompt` + `dsh-tools` + one executor (`dsh-tool-executor-native`); `inject: ['tools']` alone does not guarantee an executor exists.
- The `ctx.on('dispose')` absence is a likely stumbling block for readers who know Koishi; worth one sentence.

### Gaps
- Chinese docs (`*.zh.md`) were only spot-checked (config.zh.md matches the Schemastery guidance); full EN/ZH parity not checked.
- `pnpm dsh web --patch` behaviour, `publish.md`, bundles/profiles/install — out of scope (other researcher) and not run.
