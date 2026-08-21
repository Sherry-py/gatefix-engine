import type { Context } from '@deepseek-ai/cordis'
import type { PreToolDecision, ToolExecution } from '@deepseek-ai/dsh-tools'
import type { GateFixBridgeConfig, ToolMapping } from './config.js'
import { runAuthorize } from './bridge.js'
import { toPreToolDecision } from './decision.js'

export type { GateFixBridgeConfig, ToolMapping } from './config.js'
export { EXAMPLE_CROSS_BORDER_TRANSFER_MAPPING } from './config.js'
export { toPreToolDecision, type GateFixContract } from './decision.js'
export { runAuthorize, GateFixBridgeError } from './bridge.js'

/**
 * Decide one tool execution against GateFix, given a config. Exported
 * separately from `apply()` so it is testable without booting a Cordis
 * Context — see test/plugin.spec.ts for the full ctx.tools.execute()
 * integration test and test/decide.spec.ts for this function alone.
 *
 * Unmapped tool names pass through untouched: this gate only judges the
 * commits it has coded preconditions for (today: the two cross_border_
 * transfer commits) — same scoping GateFix's own MCP server documents for
 * authorize(), not a generic judge of arbitrary tool calls.
 */
export async function decide(
  config: GateFixBridgeConfig,
  exec: Pick<ToolExecution, 'name' | 'arguments'>,
): Promise<PreToolDecision> {
  const mapping = config.mappings.find((m: ToolMapping) => m.toolName === exec.name)
  if (!mapping) return { kind: 'allow' }

  try {
    const contract = await runAuthorize(config, {
      case: mapping.case,
      precondition_fn: mapping.preconditionFn,
      evidence: exec.arguments,
    })
    return toPreToolDecision(contract)
  } catch (err) {
    return {
      kind: 'deny',
      reason: `gatefix gate adapter failed, failing closed: ${(err as Error).message}`,
    }
  }
}

export const name = 'gatefix-gate'

/**
 * Cordis plugin entry: `apply(ctx, config)`, the shape the Cordis loader
 * expects from a `name: 'gatefix-dsh-plugin'` / `config: {...}` cordis.yml
 * entry (matches e.g. @deepseek-ai/dsh-bash-local's own apply signature) —
 * see dsh_plugin/README.md for a worked cordis.yml entry. Programmatic
 * callers use `ctx.plugin(apply, config)` the same way.
 */
export function apply(ctx: Context, config: GateFixBridgeConfig) {
  ctx.on('tools/pre-execute', async (exec, next): Promise<PreToolDecision> => {
    const decision = await decide(config, exec)
    if (decision.kind !== 'allow') return decision
    return next()
  })
}

export default apply
