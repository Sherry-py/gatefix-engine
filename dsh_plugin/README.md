# gatefix-dsh-plugin

A [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (Cordis)
`tools/pre-execute` hook plugin that routes a tool call through GateFix's
4D-CQ commit gate *before* it runs, instead of leaving authorization to the
model's own judgment.

Built 2026-08-14, the day after DeepSeek Harness's v0.1 developer-preview
open-source release (2026-08-13, MIT, Cordis plugin architecture). The main
repo README previously said: *"没有已验证的对接，接口已预留，待协议公开即可
适配"* ("no verified integration — the interface is reserved, ready to adapt
once the protocol is public"). This plugin is that adaptation, done for real
against the published `tools/pre-execute` contract
(`docs/cookbook/extension-cookbook.md#a-hook-plugin-permission-gate-example`
in the deepseek-harness repo), not a placeholder.

## What it does

Every tool call a harness agent makes flows through Cordis's
`tools/pre-execute` waterfall before it's allowed to run
(`docs/tool-execution-pipeline.md`). This plugin listens there. For tool
names it has been configured to care about, it:

1. Spawns `mcp_server/authorize_stdin.py` in your `gatefix-engine` checkout,
   handing it the tool call's arguments as GateFix evidence.
2. Gets back the real 4D-CQ judgment — the exact same `authorize()` function
   GateFix's MCP server exposes, same deterministic scoring, same audit log
   (`gate_audit_log.jsonl`), same fail-closed behavior on internal error.
3. Maps the result to Cordis's `PreToolDecision`:

   | GateFix `gate_state` | `PreToolDecision` | What happens |
   |---|---|---|
   | `PASS` | `{ kind: 'allow' }` | tool runs |
   | `ESCALATE` | `{ kind: 'ask', reason }` | routed to `ctx.approval` — a human decides |
   | `BYPASS_TO_HUMAN` | `{ kind: 'ask', reason }` | same — a human decides |
   | anything else / bridge error / timeout | `{ kind: 'deny', reason }` | fail closed, never guessed at |

   `AUTO_REPAIR` never reaches this table: GateFix's own
   `resolve_precondition()` already runs its repair-retry loop to convergence
   inside `authorize()` — this plugin only ever sees a final `PASS` /
   `ESCALATE` / `BYPASS_TO_HUMAN`.

A tool name with no configured mapping passes straight through (`next()`),
unaffected. This is the same scoping GateFix's MCP server documents for
`authorize()` itself: a gate that judges the commits it has coded
preconditions for, not a generic judge of arbitrary tool calls.

## Setup

```bash
cd dsh_plugin
npm install
npm run build   # emits lib/, what a cordis.yml `name:` entry actually loads
npm test        # 12 tests: pure decision-mapping, real-subprocess bridge,
                 # and a full real-Cordis-Context tools/pre-execute dispatch
```

Requires `python3` on `PATH` (or set `pythonBin` in config) able to import
this repo's `mcp_server/` and `preconditions/` packages — i.e. run from a
`gatefix-engine` checkout with no extra setup beyond what `engine.py run`
already needs.

### Wiring into a cordis.yml composition

See [`example.cordis.yml`](example.cordis.yml) for a full entry. Minimal
version:

```yaml
- id: gatefix-gate
  name: 'gatefix-dsh-plugin'
  config:
    gatefixRoot: /absolute/path/to/gatefix-engine
    mappings:
      - toolName: send_pii_to_risk_control_vendor
        case: cross_border_transfer
        preconditionFn: score_cross_border_transfer
```

### Programmatic usage

```ts
import { Context } from '@deepseek-ai/cordis'
import { apply, EXAMPLE_CROSS_BORDER_TRANSFER_MAPPING } from 'gatefix-dsh-plugin'

const ctx = new Context()
await ctx.plugin(apply, {
  gatefixRoot: '/absolute/path/to/gatefix-engine',
  mappings: EXAMPLE_CROSS_BORDER_TRANSFER_MAPPING,
})
```

## Scope — what's actually mapped today

Only the two `cross_border_transfer` case commits ship as the example
mapping (`EXAMPLE_CROSS_BORDER_TRANSFER_MAPPING` in `src/config.ts`):
`send_pii_to_risk_control_vendor` and `send_pii_to_ticket_archival_vendor`.
That case was deliberately built as the repo's first **agent-executed**
scenario (see `commits/cross_border_transfer_commits.yaml`'s header comment)
— it has a natural tool-call shape. `sydney_move` (human-executed, physical
handover) and `pharmacy_dispensing` (human operating automation equipment)
do not, and are not mapped here.

## What this does not do yet (honest boundaries, not omissions)

- **Evidence = raw tool arguments, 1:1, no renaming.** `preconditions/cross_border_transfer.py`'s
  score functions expect specific field names
  (`destination_adequacy_decision`, `scc_signed`, ...). A harness tool's
  `parameters` schema has to use those same names for a mapping to produce a
  meaningful judgment. Field renaming/derivation is unbuilt — see
  `src/config.ts`'s `ToolMapping` doc comment.
- **No MCP protocol involved.** This intentionally bypasses `mcp_server/server.py`'s
  MCP/stdio transport entirely, talking to a new, minimal
  `mcp_server/authorize_stdin.py` instead (one JSON request in, one JSON
  response out) — see that file's module docstring for why. Wiring this
  through DeepSeek Harness's native MCP-server support instead would expose
  `authorize()` as a *model-callable tool* the agent could choose to invoke,
  not a mandatory pre-execution gate — a materially weaker guarantee, and not
  what this plugin does.
- **Not exercised against a running `dsh` agent loop end to end.** Tests
  (`test/plugin.spec.ts`) dispatch `tools/pre-execute` directly on a
  minimally-booted `Context` with a hand-built `ToolExecution` fixture and a
  real subprocess call — proven correct at that boundary, not proven inside
  a full `dsh` session with a live model calling real tools.
- **One fixed per-call timeout** (`timeoutMs`, default 5000ms), not
  per-mapping.
