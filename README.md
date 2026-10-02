<div align="center">

# Cerebellum

**Describe the business process. Cerebellum makes it reliable, observable and recoverable.**

[![Python](https://img.shields.io/badge/python-3.11+-blue?style=flat-square)](pyproject.toml)
[![Version](https://img.shields.io/badge/version-0.2.0-purple?style=flat-square)](src/cerebellum/_version.py)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

Cerebellum runs business workflows that combine **data** (PostgreSQL), **LLMs** (Claude with
JSON-Schema outputs), **SaaS / REST APIs** and **people**. You declare the process in YAML.
Cerebellum schedules it, validates every step, pauses for human approval, retries and falls back
when systems misbehave, records a full trace, and resumes exactly where it stopped — even after
the process is killed.

```text
customer asks for a refund
 1  fetch_order        query      PostgreSQL
 2  policy_check       validate   deterministic rules
 3  assess_request     ai         Claude · structured output · repaired if invalid
 4  manager_approval   approval   only when amount > $500 or risk is high
 5  issue_refund       http       payments API · retry ×3 · idempotency key
       ⤳ open_manual_case  task   when the API keeps failing
 6  mark_refunded      query      write back to the order
```

## Quickstart

```bash
make install            # python3 -m venv .venv && pip install -e ".[dev]"
.venv/bin/cerebellum demo
```

No API key, database or Docker is needed: the demo uses a SQLite sandbox seeded with orders, a
local mock payments API with fault injection, and an offline mock AI. It runs five scenarios —
automatic refund, human approval, flaky API retried, API outage handed to a person, suspicious
request denied by the AI — and leaves one run waiting for you:

```bash
cerebellum approve <run-id> manager_approval --by you --sandbox
cerebellum trace <run-id>
cerebellum tasks
```

Use Claude instead of the mock by setting `ANTHROPIC_API_KEY` (or `ant auth login`) and running
`cerebellum demo --live`.

## Define a workflow

```yaml
name: refund_request
params: {approval_threshold: 500}
input:
  order_id: {type: string, required: true}
  amount: {type: number, required: true}
connectors:
  orders_db: {type: postgres, dsn: "${ORDERS_DSN:-sandbox}", seed: seed.sql}
  payments:  {type: rest, base_url: "${PAYMENTS_URL:-http://127.0.0.1:8787}"}
steps:
  - id: fetch_order
    type: query
    connector: orders_db
    sql: SELECT id, amount, status FROM orders WHERE id = :order_id
    params: {order_id: "{{ input.order_id }}"}
    expect: one
  - id: manager_approval
    type: approval
    needs: [fetch_order]
    when: "input.amount > params.approval_threshold"
    title: "Refund {{ input.amount }} for {{ input.order_id }}"
  - id: issue_refund
    type: http
    needs: [manager_approval]
    connector: payments
    method: POST
    path: /refunds
    body: {order_id: "{{ input.order_id }}", amount: "{{ input.amount }}"}
    retry: {max: 3, base: 500ms}
    on_failure: {fallback: open_manual_case}
fallbacks:
  - {id: open_manual_case, type: task, title: "Refund {{ input.order_id }} by hand", assignee: finance-ops}
```

The full example lives in [`src/cerebellum/templates/refund/workflow.yaml`](src/cerebellum/templates/refund/workflow.yaml);
`cerebellum init` copies it into your project.

| Step type  | Does                                                    | Output                          |
|------------|---------------------------------------------------------|---------------------------------|
| `query`    | Parameterised SQL (`:name` binds only, never templated) | row / rows / `{rowcount}` ¹     |
| `http`     | REST call with `Idempotency-Key: <run>:<step>`          | `{status, body, headers}`       |
| `ai`       | Claude structured output validated by JSON Schema       | the validated object            |
| `validate` | Deterministic business rules                            | `{passed, checked}`             |
| `approval` | Pauses the run until a human decides                    | `{approved, by, comment, ...}`  |
| `task`     | Opens a manual task in the inbox                        | `{task_id, title, assignee}`    |

¹ A write returns `{rowcount}`; a write with `RETURNING` returns its rows like a `SELECT` (and
`expect` applies). `WITH … UPDATE/INSERT/DELETE` counts as a write, and leading comments are ignored.

Every step accepts `needs`, `when`, `timeout`, `retry` and `on_failure`. Expressions are
sandboxed Jinja over `input`, `params`, `steps.<id>.{output,status,error}` and `run`.

- A main-flow step may reference only itself, the steps in its `needs` (directly or
  transitively) and the fallbacks of those steps — in expressions, templates and an approval's
  `show` list. Anything else is a validation error such as `step 'check' is not upstream of
  'audit'`. Fallbacks and the workflow `output` may reference any step.
- Run input is strict: keys not declared under `input:` are rejected (a workflow without
  `input:` accepts only `{}`), and so are `NaN`, `Infinity`, numbers out of range such as
  `1e400`, and values nested more than 100 levels deep, anywhere in input or param overrides
  (whole workflow and eval-suite files have the same depth limit). `--param k=v` values are JSON (`5`, `true`,
  `"x"`, `[1]`); anything else, including `no`, `010` and `NaN`, stays text.
- Conditions (`when`, validation rules, eval `assert`) are lenient: a missing value is false.
  Value templates (`params`, `body`, `prompt`, …) are strict: a missing value fails the step, so
  write optional inputs as `{{ input.reason | default('') }}` rather than `{{ input.reason }}`.
- `a.b` always reads the key `b`, so data keys such as `items` or `get` never turn into dict
  methods; `.get()`, `.items()` and friends are therefore not available on data.
- SQL text is never templated: values reach a `query` step only through `params` binds. On
  PostgreSQL, a `:name` inside a comment, a dollar-quoted string or an `E'…'` string is text, not a
  bind, and a literal `%` needs no escaping.

## Reliability semantics

- **Event-sourced state.** Every state change is appended to an event log in SQLite together with
  its projection, in one transaction. Traces, resume and the CLI all read the same source.
- **Failure classification.** Timeouts, connection errors, 5xx, 429 and invalid AI output are
  retried with exponential backoff; 4xx, rule violations, missing rows, template errors, refusals
  and budget overruns fail immediately.
- **Fallbacks.** When retries run out, the step's fallback runs; the step becomes `recovered`, its
  output is the fallback's, downstream continues, and the run ends `needs_attention`.
- **Human approval without a waiting process.** The run suspends (`waiting_approval`), the process
  exits, and `approve` / `reject` resumes it later. Overdue approvals apply `on_timeout`
  (recorded as `approval.expired`, decided by `system`); a decision that arrives after the
  timeout is refused (CLI error, API 409) and the run continues from the `on_timeout` outcome.
- **Crash-safe resume.** A lease marks the process driving a run. If it dies, `cerebellum resume`
  re-runs only unfinished steps; HTTP side effects are deduplicated by the idempotency key. In the
  trace, an attempt cut short by a crash or shutdown ends as `interrupted` and a cancelled one as
  `cancelled`.
- **Engine errors fail loudly.** If the engine itself hits an unexpected error, it cancels the
  steps in flight and fails the run (`run.failed`, error `engine error: …`); `resume` continues it
  once the cause is fixed.
- **One AI provider per run.** A run started with the Claude API never continues on the mock AI:
  `approve` / `reject` / `resume` exit 1 without credentials (`--no-resume` still records the
  decision), and a mock dashboard answers 409 and leaves such runs' overdue approvals for a
  process with credentials.
- **Budget cap.** `limits.budget_usd` stops the run when AI spend crosses the limit.
- **Timeouts free the sandbox.** When a step on the SQLite sandbox (`dsn: sandbox`) times out, its
  statement is interrupted (an unfinished write rolls back), so a statement that never ends does
  not block later steps. Seeding is never interrupted.

## Dashboard

```bash
cerebellum ui            # http://127.0.0.1:7400 — also starts the sandbox payments API
```

- **Overview** — runs, success rate, latency, cost, pending approvals, open tasks, retries and fallbacks over 24 h; the run list updates live.
- **Run detail** — the workflow DAG coloured by step status (running edges animate, fallbacks are dashed), a step inspector (output, errors, attempts, AI prompts and responses, HTTP and SQL calls) and the trace waterfall; resume failed runs and decide approvals in place.
- **Approvals** — decision cards with the context the step chose to `show`; your name is remembered in the browser and shared by every approval and task form.
- **Tasks** — the manual-task inbox that fallbacks fill.
- **Evals** — every suite's pass-rate, cost and duration trends; per eval run, each case's failed expectations (expected vs actual), regression marks and a link to its run.
- **Workflows** — YAML under `--workflows` (3 levels deep) plus every workflow runs were started from; YAML and DAG preview; start a run from a sample input. Files are re-parsed only when they change; a workflow whose connector `${VAR}`s are unset (with no default) is not listed, so set them before `cerebellum ui`.

The demo refunds orders A1001–A1005, so a second refund of those is (correctly) refused by the policy check; start your own runs with the untouched orders A1008–A1012.

The dashboard reads the same SQLite store as the CLI, so runs started or approved in a terminal appear live. Its JSON API and event stream send a stored `NaN` or infinite number (for example from a query result or AI output) as `null`; the CLI shows the stored value. It binds to `127.0.0.1` and has no authentication — it is a local tool. On any loopback host (`localhost`, `127.0.0.1`, `::1` in any spelling) it answers only requests addressed to this machine, against DNS rebinding; `--host` with any other address prints a warning. `--port` must be 1–65535, and a server that cannot start (port in use, unknown host) exits 1. When it falls back to the mock AI, a banner says whether that was requested (`--mock` / `CEREBELLUM_MOCK`) or caused by missing credentials. The UI ships prebuilt inside the Python package; `make ui` rebuilds it (Node 20.19+), and the page is served with `Cache-Control: no-cache`, so a reload picks up the rebuild.

## Evals

An eval suite pins what a workflow must do, case by case. Each case is a real run (tagged with the
eval id, so its trace is one click away in the dashboard); approvals are decided by `eval`, the
sandbox payments API is switched to the case's fault mode, and the finished run is checked.

```yaml
suite: refund_regression
workflow: workflow.yaml            # relative to this file
defaults: {approval: approved, sandbox: never}
cases:
  - id: large_refund_rejected_by_a_human
    input: {order_id: A1008, amount: 640}
    approval: rejected
    expect:
      status: rejected
      output.decision: rejected
      steps.issue_refund.status: cancelled
  - id: flaky_payments_api_is_retried
    input: {order_id: A1009, amount: 89.9}
    sandbox: "first:2"
    expect: {status: succeeded, steps.issue_refund.attempts: 3}
    assert: ["run.cost_usd < 0.05"]
```

```bash
cerebellum eval workflows/refund/evals.yaml --mock        # exit 1 below --min-pass (default 0.9)
```

`expect` paths: `status`, `error`, `output.*`, `steps.<id>.{status,output.*,attempts,error}`,
`tasks.{count,open}`, `approvals.count`, `run.{id,cost_usd,duration_s}`; `assert` takes conditions
over the same values. Connectors on `dsn: sandbox` get fresh databases per eval run
(`.cerebellum/evals/<id>/`), so results repeat; `eval` prints where every connector points and
warns in yellow when one is a real database or API (a set `ORDERS_DSN` / `PAYMENTS_URL`), because
`--mock` only replaces the AI, and notes when it reuses a sandbox payments API another process
(such as `cerebellum ui`) started. Each eval run is compared with the previous completed run of
the same suite and the same AI mode (mock or Claude), and newly failing cases are flagged as
regressions; eval case runs are records and cannot be resumed or approved (`cerebellum runs
--evals` lists them). Manual tasks opened by eval cases are closed by `eval`, and eval runs stay
out of the Overview metrics. The packaged suite (15 cases) is copied by `cerebellum init`.

One `eval` runs at a time per `CEREBELLUM_HOME` (a second one exits 1). A running eval writes a
heartbeat; one silent for `CEREBELLUM_LEASE_SECONDS` shows as stale and is recorded as errored
by the next eval of its suite. `cerebellum evals prune [--keep 10]` deletes the sandbox
directories of all but each suite's newest eval runs; their results stay in the history.

## Draft a workflow from a description

```bash
cerebellum new "When an invoice over 10k arrives, check the vendor in PostgreSQL, ask finance to approve, then post it to the ERP API" -o workflows/invoice.yaml
```

Claude gets the workflow JSON Schema, a step guide and the refund example; its YAML goes through
the normal loader, and any issues are sent back for a repair (up to 3 attempts). The file starts
with a comment marking it as an AI draft: review it before running. `new` needs Anthropic
credentials; there is no offline fallback.

## CLI

| Command | Purpose |
|---|---|
| `cerebellum init [dir]` | Scaffold the refund example (workflow, seed, inputs, evals) and `.env.example` |
| `cerebellum validate <wf>` / `show <wf>` | Check a definition / print its steps |
| `cerebellum run <wf> -i @input.json [--param k=v] [--sandbox] [--mock]` | Start a run (exit 3 while waiting for approval) |
| `cerebellum runs [--evals]` / `status <run>` / `trace <run>` | Observe runs, steps and span waterfalls (`status` needs no connector variables) |
| `cerebellum approvals` / `approve <run>` / `reject <run> [--no-resume]` | Human-in-the-loop decisions (hints add `--sandbox` when the run's payments API is the local sandbox) |
| `cerebellum resume <run>` | Continue after a crash, a failure or an approval |
| `cerebellum tasks [resolve <id>]` | Manual tasks opened by fallbacks |
| `cerebellum connectors check <wf>` | Health-check every connector |
| `cerebellum sandbox [--fail first:2]` | Run the mock payments API in the foreground |
| `cerebellum ui [--port 7400] [--workflows dir] [--no-sandbox] [--open]` | Local dashboard (runs, traces, approvals, tasks, workflows) |
| `cerebellum eval <suite> [--mock] [--min-pass 0.9] [--no-sandbox]` | Run an eval suite and compare with the previous run |
| `cerebellum evals prune [--keep 10]` | Delete old eval runs' sandbox directories |
| `cerebellum new "<description>" -o <file> [--force]` | Draft a workflow YAML with Claude (validated, repaired) |
| `cerebellum demo [--live]` | Five end-to-end refund scenarios (resets the `orders_db` sandbox database and says so) |

Exit codes: `0` success (also `needs_attention`: recovered by a fallback), `1` the run failed or was
rejected, an eval stayed below `--min-pass`, or a command could not complete (including an
unusable pricing file, setting or Cerebellum database, reported in one line), `2` invalid
workflow, input, eval suite or option, `3` the run is waiting for a human approval.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables the Claude provider (otherwise mock AI) |
| `CEREBELLUM_MODEL` | `claude-opus-5-5` | Default model for `ai` steps |
| `CEREBELLUM_MOCK` | unset | `1`/`true`/`yes`/`on` forces the mock AI (`0`/`false`/`no`/`off` does not; anything else is an error) |
| `CEREBELLUM_HOME` | `./.cerebellum` | Run history and sandbox databases |
| `CEREBELLUM_SANDBOX_HOST` / `CEREBELLUM_SANDBOX_PORT` | `127.0.0.1` / `8787` | Address of the local payments sandbox |
| `CEREBELLUM_LEASE_SECONDS` | `30` | How long a run's lease (and an eval's heartbeat) lasts without renewal (crash detection) |
| `CEREBELLUM_PRICING_FILE` | unset | JSON `{"<model>": {"input": n, "output": n, "cache_read": n, "cache_write": n}}` in USD per million tokens (cache prices optional; numbers from 0 to 1,000,000) |
| `CEREBELLUM_UI_HOST` / `CEREBELLUM_UI_PORT` | `127.0.0.1` / `7400` | Dashboard bind address |
| `CEREBELLUM_WORKER_INTERVAL` | `30` | Seconds between the dashboard's approval-timeout sweeps |
| `CEREBELLUM_STREAM_POLL` | `0.5` | Seconds between the dashboard's event-stream polls |
| `ORDERS_DSN` | `sandbox` | Real PostgreSQL DSN for the example (`pip install -e ".[postgres]"`, `docker compose up -d`) |
| `PAYMENTS_URL` / `PAYMENTS_TOKEN` | sandbox | Payments API used by the example |

Ports must be integers from 1 to 65535, `CEREBELLUM_LEASE_SECONDS` and `CEREBELLUM_STREAM_POLL`
finite numbers above 0, and `CEREBELLUM_WORKER_INTERVAL` 0 or more (`0` turns the sweep off). A
bad value stops every command with exit code 1 and a message naming the variable. Values are
trimmed, and an empty `CEREBELLUM_*` value means the default.

## Architecture

```
 CLI (Typer + Rich) ──┐   Dashboard (cerebellum ui) ──┐
                      ▼                                ▼
 server/    FastAPI API · SSE stream · worker · prebuilt UI
 spec/      YAML → validated Workflow (pydantic, sandboxed Jinja, JSON Schema)
 runtime/   Engine · scheduler · retry/fallback · approvals · leases · event store (SQLite)
 steps/     query · http · ai · validate · task
 connectors/ postgres (psycopg | SQLite sandbox) · rest (httpx)      ai/ Claude · mock
 sandbox/   mock payments API with fault injection and idempotency
 evals/     suites · runner (one run per case) · checks · baseline comparison
 authoring.py  cerebellum new: description → validated YAML draft
 ui/        React + Vite + Tailwind + React Flow source (built into server/static)
```

The design notes (in Chinese) are in
[`docs/superpowers/specs/2026-10-01-workflow-runtime-design.md`](docs/superpowers/specs/2026-10-01-workflow-runtime-design.md).

## Development

```bash
make install
make test        # ruff format --check, ruff check, pytest (+ vitest once `make ui-install` ran)
make ui          # rebuild the dashboard (npm ci + vite build)
make demo
make eval        # the packaged eval suite with the mock AI
```

Optional suites: `CEREBELLUM_TEST_PG_DSN=postgresql://… pytest -m postgres` and
`pytest -m live` (real Claude calls).

## License

MIT
