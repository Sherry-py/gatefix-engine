import { spawn } from 'node:child_process'
import path from 'node:path'
import type { GateFixBridgeConfig } from './config.js'
import type { GateFixContract } from './decision.js'

/**
 * Fail-closed sentinel: authorize_stdin.py did not produce a usable
 * gate_state (crashed, timed out, or returned malformed JSON). Callers treat
 * this exactly like BYPASS_TO_HUMAN's cousin — deny, never allow, because
 * GateFix's own philosophy (gate.py's safe_score/safe_repair) is that an
 * evaluator fault must never be silently read as a pass.
 */
export class GateFixBridgeError extends Error {}

/**
 * Spawn mcp_server/authorize_stdin.py, feed it one request, parse its one
 * JSON response. No MCP protocol involved — see authorize_stdin.py's module
 * docstring for why a minimal stdin/stdout contract was chosen over
 * implementing a full MCP client inside a Cordis plugin.
 */
export async function runAuthorize(
  config: GateFixBridgeConfig,
  request: { case: string; precondition_fn: string; evidence: unknown },
): Promise<GateFixContract> {
  const script = path.join(config.gatefixRoot, 'mcp_server', 'authorize_stdin.py')
  const python = config.pythonBin ?? 'python3'
  const timeoutMs = config.timeoutMs ?? 5000

  return new Promise((resolve, reject) => {
    const child = spawn(python, [script], { cwd: config.gatefixRoot })
    let stdout = ''
    let stderr = ''
    let settled = false

    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      child.kill('SIGKILL')
      reject(new GateFixBridgeError(`authorize_stdin.py timed out after ${timeoutMs}ms`))
    }, timeoutMs)

    child.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString('utf8') })
    child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString('utf8') })

    child.on('error', (err) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      reject(new GateFixBridgeError(`failed to spawn ${python} ${script}: ${err.message}`))
    })

    child.on('close', (code) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      if (code !== 0) {
        reject(new GateFixBridgeError(
          `authorize_stdin.py exited ${code}: ${stderr.trim() || '(no stderr)'}`,
        ))
        return
      }
      try {
        resolve(JSON.parse(stdout) as GateFixContract)
      } catch (err) {
        reject(new GateFixBridgeError(
          `authorize_stdin.py returned non-JSON stdout: ${(err as Error).message}`,
        ))
      }
    })

    child.stdin.write(JSON.stringify(request))
    child.stdin.end()
  })
}
