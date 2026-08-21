import type { PreToolDecision } from '@deepseek-ai/dsh-tools'

/** The subset of authorize_stdin.py's JSON response this plugin reads. */
export interface GateFixContract {
  readonly gate_state: string
  readonly route: string
  readonly human_readable: string
  readonly reason_code: string
}

/**
 * PASS -> allow. ESCALATE and BYPASS_TO_HUMAN both map to `ask`, which Cordis
 * routes through `ctx.approval` (see docs/subsystems/approval.md) — a human
 * decides. AUTO_REPAIR never reaches here: authorize_stdin.py's underlying
 * resolve_precondition() already runs GateFix's own repair-retry loop to
 * convergence and only ever returns PASS/ESCALATE/BYPASS_TO_HUMAN (see
 * mcp_server/server.py's authorize() docstring). Anything else is an unknown
 * gate_state — fail closed rather than guess.
 */
export function toPreToolDecision(contract: GateFixContract): PreToolDecision {
  switch (contract.gate_state) {
    case 'PASS':
      return { kind: 'allow' }
    case 'ESCALATE':
    case 'BYPASS_TO_HUMAN':
      return { kind: 'ask', reason: contract.human_readable }
    default:
      return {
        kind: 'deny',
        reason: `gatefix returned unrecognized gate_state=${contract.gate_state}; failing closed`,
      }
  }
}
