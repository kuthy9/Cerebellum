# Cerebellum 重构设计：可靠、可观察、可恢复的业务流程编排运行时

- 日期：2026-10-01
- 状态：已实现（v0.1.0）
- 定位：作品集 / 展示项目（demo 流畅、架构清晰、视觉精致、无外部依赖也能完整演示）

## 1. 目标与范围

### 1.1 一句话

用户知道自己要完成什么业务流程；Cerebellum 负责把 **数据（PostgreSQL）、LLM、SaaS/REST API 和人工审批** 组织成一个**可靠、可观察、可恢复**的 workflow。

### 1.2 旗舰示例

客户提交退款申请 → 从 PostgreSQL 查询订单 → 检查退款资格（确定性规则 + AI 判断）→ 金额超过阈值（默认 $500）或高风险需人工审批 → 通过后调用退款 API → 调用失败则创建人工处理任务。

### 1.3 必须具备

| 能力 | 落点 |
|---|---|
| Workflow definition | 声明式 YAML（§3）+ 自然语言生成草稿 `cerebellum new`（§8.2） |
| Connectors | `postgres`（psycopg / SQLite 沙盒）、`rest`（httpx）（§5） |
| Structured AI | Claude 原生 structured outputs + 本地 JSON Schema 校验 + mock（§6） |
| Validation | 加载期校验、`validate` 规则 step、`expect` 行数校验、AI 输出 schema 校验 |
| Human-in-the-loop | `approval` step，进程可退出的持久挂起（§4.5） |
| Failure recovery | 错误分类、指数退避重试、fallback、崩溃续跑、幂等键（§4.4、§4.6） |
| Tracing | 事件溯源，span 瀑布（§4.2） |
| Evals 看板 | 运行指标 + 离线 eval 套件（§8.1） |
| CLI | 定义 / 启动 / 恢复 / 审批 / 观察（§7） |
| Dashboard | `cerebellum ui`，科技极简风（§9） |

### 1.4 非目标（本期明确不做）

多用户与鉴权、分布式 worker、Redis、OpenTelemetry 导出、cron/webhook 触发器、saga 补偿事务、浅色主题、REST/Postgres 以外的 connector、workflow 定义迁移工具。

### 1.5 对现有代码的处理（已获批准）

- **吸收并重写**：DAG 就绪队列调度（`cerebellum.py::_IntegratedRunner`）、节点 FSM（`state_machine.py`）、checkpoint/resume 思路、预算上限。
- **删除**：`src/engine.py`（重复的旧 Graph）、`src/message_bus.py`、`src/scenarios.py`（研究/交易 demo）、`src/cerebellum.py`、`src/state_machine.py`（迁入新包后删除）、`requirements.txt`（改用 `pyproject.toml`）。
- 不保留向后兼容；依赖从 networkx 改为标准库 `graphlib`。

## 2. 架构

```
Cerebellum/
├── pyproject.toml              # pip install -e ".[dev]" → `cerebellum` 命令；extra: [postgres]
├── Makefile                    # make test / make ui / make demo
├── src/cerebellum/
│   ├── spec/        # Pydantic 模型、YAML 加载、引用/DAG 校验、表达式（Jinja2 沙盒）
│   ├── runtime/     # 事件存储 + 投影、调度器、执行器、FSM、lease、时钟抽象
│   ├── steps/       # query / http / ai / validate / approval / task
│   ├── connectors/  # base + registry、postgres、rest
│   ├── ai/          # provider 接口、anthropic、mock、pricing
│   ├── evals/       # 套件加载、批量执行、断言、对比
│   ├── server/      # FastAPI：REST + SSE + 静态 UI；内置 worker
│   ├── sandbox/     # 本地 mock 退款 API（失败注入、幂等）
│   ├── cli/         # Typer + Rich
│   ├── templates/refund/  # 旗舰示例（随包分发）：workflow.yaml、seed.sql、evals.yaml、inputs/*.json
│   └── config.py    # 集中配置：环境变量 + 默认值
├── ui/              # React + Vite + TS + Tailwind + @xyflow/react + TanStack Query
└── tests/
```

**数据流**：CLI / UI → runtime → step → connector / AI provider；每一步追加事件到 SQLite；server 读取投影并以 SSE 推送。

**依赖**：pydantic、pyyaml、jinja2、jsonschema、typer、rich、fastapi、uvicorn、httpx、anthropic、python-dotenv；可选 `psycopg[binary]`；开发：pytest、pytest-asyncio、ruff。Python ≥ 3.11。

**配置**（`config.py` 集中声明，全部可由环境变量覆盖，支持 `.env`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `CEREBELLUM_HOME` | `./.cerebellum` | 数据目录（`cerebellum.db`） |
| `CEREBELLUM_MODEL` | `claude-opus-5-5` | 默认模型 |
| `CEREBELLUM_MOCK` | 未设置 | `1` 强制 mock；未设置时无凭据则自动 mock |
| `CEREBELLUM_PRICING_FILE` | 未设置 | 覆盖内置价格表（JSON） |
| `ANTHROPIC_API_KEY` 等 | — | 由 Anthropic SDK 自行解析凭据 |

## 3. Workflow 定义（YAML）

### 3.1 顶层结构

| 字段 | 必填 | 说明 |
|---|---|---|
| `name`, `version`, `description` | name 必填 | |
| `params` | 否 | 业务参数，可被 `--param k=v` 覆盖 |
| `input` | 否 | 触发数据 schema：`{field: {type, required, enum?}}`，启动时校验 |
| `connectors` | 否 | 连接声明；**仅此处**允许 `${VAR}` / `${VAR:-default}` 环境变量插值，缺失且无默认值即加载失败 |
| `limits` | 否 | `budget_usd`、`max_parallel`（默认 8） |
| `steps` | 是 | 主流程 step 列表 |
| `fallbacks` | 否 | 仅在被 `on_failure.fallback` 引用时执行的 step |
| `output` | 否 | run 结束时渲染的业务结论映射，如 `decision` |

### 3.2 Step 通用字段

`id`、`type`、`needs`（依赖列表）、`when`（条件）、`timeout`（默认 `30s`）、`retry: {max, backoff: fixed|exponential, base, max_delay}`、`on_failure: {fallback: <id>}`、`description`。时长格式：`500ms`、`30s`、`5m`、`24h`。

### 3.3 Step 类型

| type | 关键字段 | 输出 |
|---|---|---|
| `query` | `connector`、`sql`、`params`、`expect: one\|many\|none\|any` | `one` → 行 dict；其余 → 行列表；写语句 → `{rowcount}`（带 `RETURNING` 的写语句按查询处理，返回行并应用 `expect`；`WITH … UPDATE/INSERT/DELETE` 是写语句；开头的注释不影响判断） |
| `http` | `connector`、`method`、`path`、`body`、`headers`、`query` | `{status, body, headers(白名单)}`；自动带 `Idempotency-Key: <run_id>:<step_id>` |
| `ai` | `prompt`、`system`、`output_schema`、`model`、`effort`、`max_repairs`（默认 2）、`mock` | 校验通过的 JSON 对象 |
| `validate` | `rules: [{expr, message}]` | `{passed: true}`；任一规则不满足即不可重试失败，错误含全部未通过的 message |
| `approval` | `title`、`show`（要展示的 step id）、`timeout`、`on_timeout: approve\|reject` | `{approved, by, comment, decided_at, auto}` |
| `task` | `title`、`assignee`、`payload` | `{task_id}`，写入人工任务收件箱 |

### 3.4 表达式与模板（Jinja2 SandboxedEnvironment）

- 上下文只有：`input`、`params`、`steps.<id>.{output,status,error,attempts}`、`run.{id,cost_usd}`；fallback step 额外可用 `failure.{step,error}`。
- 字符串整体为单个 `{{ expr }}` 时返回原生类型；否则按字符串渲染。
- **条件类**（`when`、`rules.expr`、eval `assert`）：使用 ChainableUndefined，缺失值为假，不报错。
- **取值类**（`params`、`body`、`prompt` 等）：使用 StrictUndefined，引用缺失即不可重试失败。
- `query` 的 SQL 文本本身不做模板渲染，只能通过 `params` 绑定。

### 3.5 控制流语义

- `when` 为假 → step `skipped`，output 为 null，下游正常调度。
- 审批被跳过 = 不需要审批；审批被拒 → run `rejected`，未完成的下游 `cancelled`。
- step 重试耗尽：配置了 fallback → 执行 fallback；fallback 成功则原 step 记 `recovered`，其 output 替换为 fallback 的 output，下游继续，run 最终为 `needs_attention`；未配置或 fallback 也失败 → step `failed`，下游 `cancelled`，run `failed`。
- 下游可通过 `steps.x.status == 'succeeded'` 区分正常路径与 fallback 路径。

### 3.6 加载期校验（`cerebellum validate`）

Pydantic 结构校验；id 全局唯一（含 fallbacks）；`needs` / `fallback` / `show` 引用存在；主流程 step 的表达式、模板与审批 `show` 只能引用自身（`show` 不能引用自身）、其 `needs` 中（直接或传递）的 step，以及这些上游 step 所属的 fallback（fallback 与 workflow 级 `output` 可引用任意 step）；run 的 `input` 只接受 `input:` 中声明的键；主流程无环；fallback 不得出现在主流程 `needs` 中；所有表达式可编译；step 引用的 connector 已声明且类型匹配；`output_schema` 是合法 JSON Schema（加载时为每个 object 自动补 `additionalProperties: false`）。错误带 YAML 路径（如 `steps[3].when`）。

### 3.7 退款示例

见 `src/cerebellum/templates/refund/workflow.yaml`（`cerebellum init` 复制到 `./workflows/refund/`，`demo` 直接使用包内副本），与设计讨论中的示例一致：`fetch_order(query)` → `policy_check(validate)` → `assess_request(ai)` → `manager_approval(approval, when 金额 > params.approval_threshold 或 risk == high)` → `issue_refund(http, when eligible, retry 3, fallback open_manual_case)` → `mark_refunded(query UPDATE, when issue_refund 成功)`；`fallbacks: open_manual_case(task)`；`output.decision ∈ {refunded, denied, rejected, manual}`。

## 4. 运行时

### 4.1 状态

**Step**：`pending → running → succeeded | failed | skipped | cancelled | waiting | recovered`，其中 `running → retrying → running` 循环；`waiting`（审批中）→ `succeeded`（批准）| `failed`（拒绝）。FSM 强制合法转换，非法转换抛 `InvalidTransition`。

**Run**：`running`、`waiting_approval`、`succeeded`、`failed`、`rejected`、`needs_attention`。

### 4.2 存储（SQLite，WAL）

| 表 | 用途 |
|---|---|
| `workflows(hash PK, name, version, source_yaml, created_at)` | 定义快照；run 固定引用 hash，resume 使用当时的定义 |
| `runs(run_id PK, workflow_hash, status, input, params, output, cost_usd, created_at, updated_at, lease_owner, lease_until, eval_run_id)` | run 投影 |
| `events(seq PK AUTOINCREMENT, run_id, step_id, span_id, parent_span_id, type, ts, data)` | **只追加**，事实来源 |
| `step_states(run_id, step_id, status, attempts, output, error, started_at, ended_at, cost_usd)` | step 投影 |
| `approvals(id, run_id, step_id, status, title, context, requested_at, expires_at, decided_at, decided_by, comment)` | 审批投影 |
| `tasks(id, run_id, step_id, title, assignee, payload, status, created_at, resolved_at)` | 人工任务 |
| `eval_runs`、`eval_results` | 阶段 3 |

事件与投影在**同一事务**内写入。事件类型：`run.started|suspended|resumed|completed|failed`（`run.failed`：引擎自身出错，取消在途 step，错误为 `engine error: …`，修复后可 `resume`）、`step.started|retrying|succeeded|failed|skipped|cancelled|waiting|recovered|reset`（`cancelled` 也可跟在运行中或重试中的 step 之后；`reset`：崩溃或停止后 resume 时把被中断的 attempt 记为 `interrupted`）、`approval.requested|decided|expired`、`task.created|resolved`、`llm.call`、`connector.call`。

**Tracing**：每次 step attempt 是一个 span；`llm.call` / `connector.call` 是其子 span（含耗时、token、成本、状态码、是否 mock）。敏感 header（`Authorization`、名称含 token/key/secret/password 的字段）在写入前替换为 `***`。

### 4.3 调度

- 就绪队列 + `asyncio`，按 `graphlib` 拓扑关系解锁下游，受 `max_parallel` 限制。
- step 执行流程：求值 `when` → 渲染模板 → 带 `timeout` 执行 → 输出校验 → 写事件。
- 预算：每次 AI 调用后累计成本，超过 `limits.budget_usd` → 当前 step 不可重试失败，run `failed`。
- 时钟与 sleep 通过 `Clock` 接口注入，测试使用假时钟。

### 4.4 错误分类与重试

| 类别 | 例子 | 处理 |
|---|---|---|
| 可重试 | 连接/读超时、5xx、429、AI 输出不符 schema（修复重试）、step 超时 | 按 `retry` 退避重试 |
| 不可重试 | 其他 4xx、`validate` 失败、`expect` 不符、模板引用缺失、AI refusal、预算超限 | 直接进入 fallback / 失败 |

退避：`exponential` 为 `base × 2^(n-1)`，上限 `max_delay`（默认 30s），生产带 ±10% 抖动，测试关闭抖动。

### 4.5 Human-in-the-loop：持久挂起与恢复

1. 执行到 `approval` 且 `when` 为真：写 `approvals` 行与 `approval.requested` 事件，step 进入 `waiting`；其他独立分支继续。
2. 没有可执行 step 且存在等待中的审批 → run `waiting_approval`，释放 lease，**进程正常退出**。CLI 打印下一步命令。
3. `approve` / `reject`（CLI 或 UI）→ 写 `approval.decided` → 抢 lease → 续跑（CLI 可 `--no-resume`）。
4. 超时：server 内置 worker 每 30s 扫描，CLI `resume` 时也检查；过期按 `on_timeout` 处理并记 `approval.expired`（`by: system`、`auto: true`）。未设 `timeout` 则无限等待。超时后才到达的人工决定被拒绝（CLI 报错，API 409），改为应用 `on_timeout` 并续跑。
5. 用 Claude API 启动的 run 不会在 mock AI 上续跑：CLI 的 `approve` / `reject` / `resume` 退出码 1（`--no-resume` 仍可记录决定），mock 模式的 dashboard 对决定/续跑返回 409，其 worker 扫描跳过这类 run（每个 run 只记一次日志）。

### 4.6 Lease 与崩溃恢复

- 执行进程以原子 `UPDATE runs SET lease_owner=?, lease_until=? WHERE run_id=? AND (lease_until IS NULL OR lease_until < now)` 抢占 lease（30s），执行中每 10s 续期。
- 进程被杀 → run 停留在 `running` 且 lease 过期，`cerebellum runs` 标记为 **stale**。
- `resume`：保留 `succeeded/skipped/recovered` 的输出；崩溃时 `running/retrying` 的 step 重置为 `pending`（保留 attempt 计数）。语义为**至少执行一次**，http step 的幂等键保证外部副作用只发生一次。

### 4.7 Run ID

`r_` + 8 位十六进制。

## 5. Connectors

- 接口：`Connector.open() / close() / health()`；`SqlConnector.query(sql, params) / execute(sql, params)`；`HttpConnector.request(method, path, *, json, headers, query, idempotency_key)`。通过 `@register_connector("<type>")` 注册。
- **postgres**：DSN 为真实地址 → `psycopg` 异步连接（需 `pip install ".[postgres]"`），`:name` 参数转为 `%(name)s`；DSN 为 `sandbox` → 标准库 `sqlite3`（`asyncio.to_thread`）于 `CEREBELLUM_HOME/sandbox_<connector>.db`，首次创建时执行 connector 配置中的 `seed`（相对 workflow 文件的 SQL 路径，两库兼容的 SQL 子集）。另附 `docker-compose.yml` 供有 Docker 的用户起真实 Postgres。
- **rest**：`httpx.AsyncClient`，`base_url`、默认 `headers`、`timeout`；错误按 §4.4 分类。
- **沙盒退款 API**（`cerebellum.sandbox`，FastAPI）：`POST /refunds`（按 Idempotency-Key 去重，同 key 返回同一退款）、`GET /refunds/{id}`、`GET /health`；失败注入 `never | first:N（每个 Idempotency-Key 的前 N 次请求返回 503）| always | rate:P`。`cerebellum ui` / `demo` 默认进程内启动（端口 8787，`--no-sandbox` 关闭），`cerebellum run --sandbox` 在本次执行期间启动；端口已被占用且 `/health` 表明是 Cerebellum 沙盒时直接复用。

## 6. Structured AI

- **Provider 接口**：`generate(model, system, prompt, schema, effort) -> AIResult(output, usage, cost_usd, mock, raw)`。
- **Anthropic provider**：`anthropic.AsyncAnthropic`；`output_config={"format": {"type": "json_schema", "schema": ...}}`；默认开启服务端 refusal fallback（`fallbacks: "default"`，beta `server-side-fallback-2026-07-01`）；先检查 `stop_reason`：`refusal` → 不可重试失败并记录类别，`max_tokens` → 可重试。解析后再用 `jsonschema` 校验，不通过则把校验错误追加到对话中修复重试（`max_repairs`）。成本 = usage × 价格表（`ai/pricing.py`，可由 `CEREBELLUM_PRICING_FILE` 覆盖）。
- **Mock provider**：显式 `mock:` 规则列表，首个 `when` 为真（或无 `when`）的 `output` 胜出；无规则时按 schema 生成确定性最小合法值。输出同样经 schema 校验；`mock: true`，成本 0，模拟 50–300ms 延迟（测试中由假时钟消除）。
- **选择逻辑**：`CEREBELLUM_MOCK=1` 或 `--mock` → mock；否则能解析到 Anthropic 凭据 → 真实；都没有 → mock 并在 CLI/UI 显著提示。
- 每次调用写 `llm.call` 事件（model、system、prompt、output、usage、cost、latency、attempt、mock）。

## 7. CLI（Typer + Rich）

| 命令 | 作用 |
|---|---|
| `init [dir]` | 生成 `workflows/refund/`（workflow、seed、evals、inputs）与 `.env.example` |
| `new "<描述>" -o <file>` | 阶段 3：LLM 生成 YAML 草稿 |
| `validate <wf>` / `show <wf>` | 校验；终端渲染 DAG 树 |
| `run <wf> --input <json\|@file> [--param k=v] [--mock] [--sandbox]` | 启动并实时刷新；挂起时退出并提示 |
| `runs [--status s]` / `status <run>` / `trace <run>` | 列表（含 stale 标记）/ step 表 / 终端瀑布 |
| `approvals` / `approve <run> [step] --by --comment [--no-resume]` / `reject ...` | 审批（run 只有一个待审批时可省略 step） |
| `resume <run>` | 续跑 |
| `tasks` / `tasks resolve <id> --by --note` | 人工任务 |
| `connectors check <wf>` | 健康检查 |
| `sandbox [--port] [--fail]` | 单独运行沙盒 API |
| `ui [--port 7400] [--host 127.0.0.1] [--workflows <dir>] [--no-sandbox] [--open]` | dashboard |
| `eval <suite> [--mock] [--min-pass 0.9]` | 阶段 3 |
| `demo` | 一键演示 5 个场景并打印 dashboard 链接 |

**demo 场景**：① $120 自动退款；② $899 进入 `waiting_approval`，留给用户在 UI 批准；③ 支付 API `first:2` → 重试成功；④ 支付 API `always` → fallback 人工任务 → `needs_attention`；⑤ 理由含 fraud → AI 判定不合格 → `denied`。

**终端视觉**：单色 + 青色强调；细线分隔；等宽对齐；状态符号 `● 成功 ◐ 运行/等待 ○ 待执行 ✕ 失败 ↻ 重试 ⤳ fallback ⊘ 跳过 ⏸ 挂起`，不使用 emoji；执行中 Rich Live 原地刷新。退出码：成功 0、失败 1、挂起 3、校验错误 2。

## 8. Evals 与自然语言生成（阶段 3）

### 8.1 Eval 套件

- 结构：`suite`、`workflow`、`defaults: {approval, sandbox, mock}`、`cases: [{id, input, approval?, sandbox?, expect: {dotted.path: value}, assert: [expr]}]`。可断言路径：`status`、`output.*`、`steps.<id>.{status,output.*}`、`tasks.count`、`run.cost_usd`、`run.duration_s`。
- 每条 case 产生一次真实 run（`eval_run_id` 标记，可在 UI 下钻 trace）；审批按 case 自动决策（`approved|rejected`，记录 `by: eval`）。
- case 顺序执行（沙盒失败模式是进程级设置，按 case 切换，保证结果确定）。
- 结果：通过率、每条 case 的期望/实际/失败断言、成本与耗时、AI schema 首次通过率与修复重试次数；与上一次同套件、同 AI 模式（mock / Claude）的已完成 eval 对比，标出回归。
- 同一 `CEREBELLUM_HOME` 同时只允许一个 `eval`（`eval.lock`）；运行中的 eval 定期写心跳，超过 `CEREBELLUM_LEASE_SECONDS` 无心跳显示为 stale，下一次同套件 eval 将其记为 errored。`cerebellum evals prune [--keep 10]` 只删除旧 eval 的沙盒目录，保留数据库中的历史。
- `--min-pass` 未达标退出码 1。示例套件约 15 条 case。

### 8.2 `cerebellum new`

上下文：Pydantic 导出的 workflow JSON Schema、step 类型说明、可用 connector 类型、退款示例。输出 YAML → loader 校验 → 失败带错误重试（≤3 次）→ 写文件，首行注释「AI 生成草稿，运行前请审阅」。无凭据时报错退出，不提供模板替代。

## 9. Dashboard（`cerebellum ui`）

### 9.1 页面

1. **Overview `/`**：KPI 条（24h run 数、成功率、平均耗时、总成本、待审批、待处理任务、retry/fallback 次数）+ 最近 run 列表（SSE 实时）。
2. **Run 详情 `/runs/:id`**：左 DAG（React Flow；节点按状态着色、运行中边流动、fallback 虚线）；右 step 检视器（渲染后输入、输出 JSON、错误、attempts、AI prompt/响应、connector 请求/响应）；底部 trace 瀑布；操作：Resume、就地审批。
3. **Approvals `/approvals`**：审批卡片（`show` 上下文、金额高亮、评论、批准/拒绝；审批人名存 localStorage）。
4. **Tasks `/tasks`**：人工任务收件箱，可标记处理。
5. **Evals `/evals`**（阶段 3）：套件通过率趋势、成本/延迟趋势、case 明细与回归标记。
6. **Workflows `/workflows`**：`--workflows` 目录（默认当前目录，深度 ≤ 3）中能通过 loader 的 YAML 与历史 run 中出现过的 workflow；YAML 与 DAG 预览；「New run」对话框（JSON 输入，预填 `inputs/` 示例）。

### 9.2 视觉规范（科技极简，深色）

近黑背景、发丝边框、无阴影无渐变；Inter（UI）+ JetBrains Mono（ID/数字/JSON）；单一青色强调色；降饱和状态色（成功绿、等待琥珀、失败红、fallback 紫、跳过灰）；小号大写标签；DAG 画布点阵网格；所有颜色为 CSS token。

### 9.3 Server API

`GET /api/runs`、`GET /api/runs/{id}`、`GET /api/runs/{id}/events`、`POST /api/runs`（`{workflow, input, params}`）、`POST /api/runs/{id}/resume`、`GET /api/approvals`、`POST /api/approvals/{id}/decision`、`GET /api/tasks`、`POST /api/tasks/{id}/resolve`、`GET /api/metrics?window=24h`、`GET /api/workflows`、`GET /api/evals`、`GET /api/evals/{id}`、`GET /api/stream`（SSE；按 `seq` 每 500ms 轮询事件表，跨进程可见）。

内置 worker：处理 UI 触发的启动/续跑、审批超时扫描。

### 9.4 安全与分发

默认仅绑定 `127.0.0.1`、无鉴权（本地工具，README 写明）；`--host` 非本机地址时警告。前端构建产物提交至 `src/cerebellum/server/static/`，运行时无需 Node；`make ui` 重新构建。

## 10. 测试与验证

- TDD；`pytest` + `pytest-asyncio`；`ruff`。
- 单元：loader（合法/非法 fixture）、表达式两种 undefined 语义、FSM、事件投影、调度（并行、`when`、取消下游、`max_parallel`）、执行器（错误分类、假时钟退避、fallback、预算）、审批挂起/恢复/超时、lease 与崩溃恢复、幂等键。
- Connector：SQLite 沙盒真实测试；Postgres 测试 `@pytest.mark.postgres`，仅当 `CEREBELLUM_TEST_PG_DSN` 存在时运行；REST 通过 `httpx.ASGITransport` 进程内调用沙盒 app。
- AI：mock provider；Anthropic provider 用桩客户端（schema 修复重试、refusal、max_tokens）；真实调用测试 `@pytest.mark.live` 默认跳过。
- CLI 端到端：`CliRunner` 跑 run → waiting → approve → succeeded，以及 fallback 场景。
- Server：`TestClient` 覆盖 API 与 SSE。
- UI：vitest 覆盖纯数据转换（事件 → 瀑布 span、图布局输入）；浏览器实际走查。
- 统一入口：`make test`（pytest + ruff + vitest）、`make demo`。

## 11. 分阶段交付与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| 1. Runtime + CLI | spec、runtime、steps、connectors、AI（真实+mock）、沙盒、CLI（除 `new`/`eval`）、`demo`、删除旧代码、README 重写 | `make test` 通过；`cerebellum demo` 五个场景结果符合 §7；崩溃后 `resume` 不重复退款 |
| 2. Dashboard | server API + SSE + worker、React UI 6 页中的 1–4、6 | 浏览器中完成：查看 trace 瀑布、批准 demo 场景 ② 并看到续跑、处理人工任务、新建 run |
| 3. Evals + NL | eval 套件/存储/对比、`cerebellum eval`、Evals 页面、`cerebellum new` | 示例套件 mock 模式全部通过；人为改坏阈值后出现回归标记；`new` 在有凭据时生成可通过 validate 的 YAML |

## 12. 风险

- **SQLite 并发写**：CLI 与 server 同时写入同一 DB；以 WAL + 短事务 + `busy_timeout` 缓解。
- **至少一次语义**：非 http step（如 `query` 写语句）崩溃重放可能重复执行；示例中的 UPDATE 为幂等写法，文档注明。
- **提交构建产物**：仓库体积略增，换取开箱即用。
- **Claude API 形态变化**：provider 隔离在 `ai/anthropic_provider.py`，桩测试覆盖解析逻辑。
