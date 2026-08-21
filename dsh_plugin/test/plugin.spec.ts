import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import type { PreToolDecision, ToolExecution } from '@deepseek-ai/dsh-tools'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { apply } from '../src/index.js'
import { EXAMPLE_CROSS_BORDER_TRANSFER_MAPPING } from '../src/config.js'

const GATEFIX_ROOT = path.resolve(fileURLToPath(import.meta.url), '../../..')

// Minimal ToolExecution fixture: only `name` and `arguments` are read by this
// plugin (see src/index.ts's decide()); the rest of the interface exists to
// satisfy the type, matching how the real ToolRuntime dispatches
// `tools/pre-execute` (node_modules/@deepseek-ai/dsh-tools/lib/index.js,
// search "tools/pre-execute" for the real ctx.waterfall call site this test
// mirrors).
function fixture(name: string, args: unknown): ToolExecution {
  return {
    callId: 'call-1', rootCallId: 'call-1', token: 'token-1',
    name, arguments: args, signal: new AbortController().signal,
  } as unknown as ToolExecution
}

const allowNext = async (): Promise<PreToolDecision> => ({ kind: 'allow' })

describe('gatefix apply() plugin wired into a real Cordis Context', () => {
  it('passes an unmapped tool straight through to next()', async () => {
    const ctx = new Context()
    await ctx.plugin(apply, {
      gatefixRoot: GATEFIX_ROOT,
      mappings: EXAMPLE_CROSS_BORDER_TRANSFER_MAPPING,
    })

    const decision = await ctx.waterfall(
      'tools/pre-execute', fixture('read_file', { path: '/tmp/x' }), allowNext,
    )
    expect(decision).toEqual({ kind: 'allow' })
  })

  it('allows a mapped tool call when the real gatefix engine judges the evidence PASS', async () => {
    const ctx = new Context()
    await ctx.plugin(apply, {
      gatefixRoot: GATEFIX_ROOT,
      mappings: EXAMPLE_CROSS_BORDER_TRANSFER_MAPPING,
    })

    const decision = await ctx.waterfall(
      'tools/pre-execute',
      fixture('send_pii_to_risk_control_vendor', {
        destination_adequacy_decision: true,
        scc_signed: true,
        tia_completed: true,
        explicit_consent_obtained: true,
        consent_obtained_before_request: true,
        government_access_risk_assessed: true,
        residual_access_risk_level: 'low',
      }),
      allowNext,
    )
    expect(decision).toEqual({ kind: 'allow' })
  })

  it('asks (routes to human approval) a mapped tool call when evidence is insufficient — never silently denies AND never silently allows', async () => {
    const ctx = new Context()
    await ctx.plugin(apply, {
      gatefixRoot: GATEFIX_ROOT,
      mappings: EXAMPLE_CROSS_BORDER_TRANSFER_MAPPING,
    })

    const decision = await ctx.waterfall(
      'tools/pre-execute',
      fixture('send_pii_to_risk_control_vendor', {
        destination_adequacy_decision: false,
        scc_signed: false,
        tia_completed: false,
        explicit_consent_obtained: false,
        consent_obtained_before_request: false,
        government_access_risk_assessed: false,
      }),
      allowNext,
    )
    expect(decision.kind).toBe('ask')
  })

  it('fails closed (deny) when the bridge itself breaks, e.g. a bad gatefixRoot', async () => {
    const ctx = new Context()
    await ctx.plugin(apply, {
      gatefixRoot: '/nonexistent/path/to/gatefix-engine',
      mappings: EXAMPLE_CROSS_BORDER_TRANSFER_MAPPING,
    })

    const decision = await ctx.waterfall(
      'tools/pre-execute',
      fixture('send_pii_to_risk_control_vendor', {}),
      allowNext,
    )
    expect(decision.kind).toBe('deny')
  })
})
