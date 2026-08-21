/**
 * One tool-name -> GateFix precondition mapping. `evidenceFrom` deliberately
 * has one shape today: the model-supplied tool `arguments` ARE the evidence
 * dict, passed through unchanged. GateFix's precondition functions expect
 * named boolean/string fields (see preconditions/<case>.py); a harness tool's
 * `parameters` schema has to use those same field names for this mapping to
 * produce a meaningful judgment. Renaming/derivation is out of scope for this
 * first adapter — see dsh_plugin/README.md "What this does not do yet".
 */
export interface ToolMapping {
  /** Exact DeepSeek Harness tool name this mapping intercepts. */
  readonly toolName: string
  /** GateFix case (selects commits/bindings/evidence/preconditions/<case>.*). */
  readonly case: string
  /** precondition_fn registered in preconditions/<case>.py's REGISTRY. */
  readonly preconditionFn: string
}

/**
 * Worked example mapping for the one GateFix case actually built for
 * agent-executed tool calls (cross_border_transfer — see its commits YAML:
 * "agent 执行、数据合规"). sydney_move and pharmacy_dispensing are
 * human-executed/physical scenarios and do not have a natural tool-call
 * shape, so they are intentionally not mapped here.
 *
 * Tool names below are illustrative, not a claim that a harness ships tools
 * with these exact names — a real deployment supplies its own ToolMapping[]
 * naming its actual tools. See dsh_plugin/README.md for how to author one.
 */
export const EXAMPLE_CROSS_BORDER_TRANSFER_MAPPING: readonly ToolMapping[] = [
  {
    toolName: 'send_pii_to_risk_control_vendor',
    case: 'cross_border_transfer',
    preconditionFn: 'score_cross_border_transfer',
  },
  {
    toolName: 'send_pii_to_ticket_archival_vendor',
    case: 'cross_border_transfer',
    preconditionFn: 'score_send_pii_to_archival_vendor',
  },
]

export interface GateFixBridgeConfig {
  /** Absolute path to the gatefix-engine checkout (contains mcp_server/authorize_stdin.py). */
  readonly gatefixRoot: string
  /** Python interpreter to spawn. Defaults to 'python3'. */
  readonly pythonBin?: string
  /** Per-call timeout in ms before failing closed. Defaults to 5000. */
  readonly timeoutMs?: number
  readonly mappings: readonly ToolMapping[]
}
