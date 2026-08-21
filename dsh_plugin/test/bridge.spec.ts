import { describe, expect, it } from 'vitest'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { runAuthorize, GateFixBridgeError } from '../src/bridge.js'

// Real subprocess, real gatefix-engine, real cross_border_transfer preconditions
// — no mocking. This is the test that proves the Node <-> Python contract
// actually works, not just that the TS types compile.
const GATEFIX_ROOT = path.resolve(fileURLToPath(import.meta.url), '../../..')

describe('runAuthorize', () => {
  it('routes PASS-shaped evidence to gate_state PASS', async () => {
    const result = await runAuthorize({ gatefixRoot: GATEFIX_ROOT, mappings: [] }, {
      case: 'cross_border_transfer',
      precondition_fn: 'score_cross_border_transfer',
      evidence: {
        destination_adequacy_decision: true,
        scc_signed: true,
        tia_completed: true,
        explicit_consent_obtained: true,
        consent_obtained_before_request: true,
        government_access_risk_assessed: true,
        residual_access_risk_level: 'low',
      },
    })
    expect(result.gate_state).toBe('PASS')
  })

  it('routes insufficient evidence to gate_state ESCALATE, not PASS', async () => {
    const result = await runAuthorize({ gatefixRoot: GATEFIX_ROOT, mappings: [] }, {
      case: 'cross_border_transfer',
      precondition_fn: 'score_cross_border_transfer',
      evidence: {
        destination_adequacy_decision: false,
        scc_signed: false,
        tia_completed: false,
        explicit_consent_obtained: false,
        consent_obtained_before_request: false,
        government_access_risk_assessed: false,
      },
    })
    expect(result.gate_state).toBe('ESCALATE')
  })

  it('rejects with GateFixBridgeError when the precondition_fn is unknown (fail closed)', async () => {
    await expect(runAuthorize({ gatefixRoot: GATEFIX_ROOT, mappings: [] }, {
      case: 'cross_border_transfer',
      precondition_fn: 'score_does_not_exist',
      evidence: {},
    })).rejects.toBeInstanceOf(GateFixBridgeError)
  })

  it('rejects when the python interpreter itself cannot be found', async () => {
    await expect(runAuthorize(
      { gatefixRoot: GATEFIX_ROOT, pythonBin: 'python3-does-not-exist-anywhere', mappings: [] },
      { case: 'cross_border_transfer', precondition_fn: 'score_cross_border_transfer', evidence: {} },
    )).rejects.toBeInstanceOf(GateFixBridgeError)
  })
})
