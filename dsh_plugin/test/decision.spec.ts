import { describe, expect, it } from 'vitest'
import { toPreToolDecision } from '../src/decision.js'

describe('toPreToolDecision', () => {
  it('PASS -> allow', () => {
    expect(toPreToolDecision({
      gate_state: 'PASS', route: 'PASS', human_readable: 'ok', reason_code: 'X',
    })).toEqual({ kind: 'allow' })
  })

  it('ESCALATE -> ask, carrying the human-readable reason for the approval prompt', () => {
    expect(toPreToolDecision({
      gate_state: 'ESCALATE', route: 'ESCALATE',
      human_readable: 'no adequacy decision, needs DPO review', reason_code: 'X',
    })).toEqual({ kind: 'ask', reason: 'no adequacy decision, needs DPO review' })
  })

  it('BYPASS_TO_HUMAN -> ask, same as ESCALATE (both mean "a human decides")', () => {
    expect(toPreToolDecision({
      gate_state: 'BYPASS_TO_HUMAN', route: 'BYPASS_TO_HUMAN',
      human_readable: 'evaluator fault', reason_code: 'EVALUATOR_FAULT',
    })).toEqual({ kind: 'ask', reason: 'evaluator fault' })
  })

  it('unknown gate_state -> deny, fails closed rather than guessing', () => {
    const decision = toPreToolDecision({
      gate_state: 'SOMETHING_NEW', route: 'SOMETHING_NEW', human_readable: 'x', reason_code: 'X',
    })
    expect(decision.kind).toBe('deny')
  })
})
