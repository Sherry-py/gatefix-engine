# Two-arm sandbox experiment: governed vs ungoverned commit execution

Date: 2026-07-31
Status: approved for local-only scope (E2B deferred)

## TL;DR

Right now, nothing in this repo stops `key_to_agent` from being called before
the house is empty, or `bond_claim_confirm` from being called before the keys
are handed over — there is no code path that could even notice, because the
only two things that exist today are a static evidence yaml (hand-typed
claims about what happened) and an `engine.py` that judges those claims once,
in isolation. This spec adds a third thing: a small in-process world model
that commits actually execute against, so an ordering violation becomes an
observable event instead of an untestable hypothetical. It changes nothing in
`gate.py` or `engine.py`. E2B, an LLM planner, and randomized trials were all
considered and rejected for this delivery — reasons below, not just the
decision.

## The gap, concretely

Take the real dependency: the bond can't be claimed back until the keys are
confirmed handed over. Today, `commits/sydney_move_commits.yaml` lists
`bond_claim_confirm` after `key_to_agent`, but that ordering is narrative —
`engine.py::run_case` and `agent/gated_loop.py::make_case_reason_fn` both just
iterate the yaml list top to bottom. If a caller invoked `resolve_precondition`
for `bond_claim_confirm` first, with `evidence/sydney_move_evidence.yaml`'s
`bond_claim_confirm` entry untouched, it would score exactly the same as it
does today — nothing reads "was `key_to_agent` actually executed first."
Ordering, as a judgment, is currently un-falsifiable: there's no second
action for it to be checked against, only a single evidence dict per commit.

That's the reviewer gap in concrete form. It doesn't show up as "the model
judged wrong" — it shows up as "the codebase has no mechanism that could
catch this," which is a stronger and more useful thing to fix.

**This is a constructed scenario, not a historical one — stated explicitly so
it isn't mistaken for the latter.** Running the real evidence through
`engine.py run --case=sydney_move` today shows `key_to_agent` = PASS (the
keys really were handed over, after `AUTO_REPAIR` upgraded
`key_count_source` from `"memory"` to `"agent_email"`) and
`bond_claim_confirm` = ESCALATE — but for a reason that has nothing to do
with ordering: `refund_account_name` ("第三方/Third party") doesn't match
`client_name` ("委托人本人/Client"), a Relevance problem `engine.py` already
catches correctly today, unrelated to this spec. Nothing in the real case
history involves a bond refund being confirmed before the keys were handed
over. The ordering violation this experiment demonstrates is a deliberately
constructed adversarial proposal — built to exercise the new `requires:`
mechanism specifically — not a near-miss that actually happened. Presenting
it as "what happened" instead of "what this mechanism is built to catch"
would be exactly the kind of overclaim this repo's "如实说明现状" convention
exists to prevent.

## Boundary invariant (unchanged from the earlier design conversation)

```
Outside the sandbox (GateFix's contribution, unchanged):
  planner/reason_fn  -> proposes the next commit
  gate (gate.py / engine.py / resolve_precondition) -> PASS / AUTO_REPAIR / ESCALATE
                                                        (+ new: sequence precondition)

Inside the sandbox (execution stage only):
  the action that was authorized -> really executes -> mutates observable state
```

The sandbox does not simulate or replace the commit judgment logic. It gives
the existing judgment logic a place to actually be enforced against, and a
place to read ground truth back from, instead of trusting a hand-authored
evidence yaml. If this boundary gets blurred — if `SandboxWorld` starts
deciding anything, instead of just recording what was decided — the
theoretical contribution has quietly moved from GateFix into the execution
environment, which is exactly the framing this spec exists to avoid.

## Alternatives considered and rejected

These were live options during design, not strawmen — each is rejected for a
stated reason, so a future reader doesn't have to re-litigate them from
scratch.

- **E2B now, not later.** Rejected for this delivery because there is no
  account/API key provisioned yet, and because the `requires`/`SandboxWorld`
  logic is the part actually under theoretical dispute — proving it works
  in-process first means the eventual E2B swap is a backend change, not a
  design change. Consequence: this delivery can't yet claim the execution
  happened somewhere the agent doesn't control (a real sandbox process); it
  only proves the mechanism, not the isolation property. That gap is real and
  is the reason E2B is a punted follow-up, not a dropped idea (see Drawbacks).
- **A real LLM planner for the ungoverned arm.** Rejected because it would
  make the headline result non-reproducible and would reintroduce exactly the
  token-cost/nondeterminism questions this repo has deliberately opted out of
  everywhere else (README: "no LLM call needed — the decision logic itself is
  the point"). A fixed, named, deterministic wrong-order script is a weaker
  claim ("this specific mistake is not caught") but a claim that can actually
  be checked into CI and re-run identically forever. That trade — external
  validity for reproducibility — is made consistently with the rest of the
  repo, not as a one-off exception.
- **Randomized shuffling with repeated trials, reporting a violation rate.**
  Rejected for this delivery for the same reason as the LLM planner:
  statistical claims need a stated N, a stated seed policy, and a stated
  confidence treatment, none of which this repo's existing tests do anywhere
  else. A single named, explainable violation is weaker evidence but it's
  evidence the reviewer can trace by hand, which matters more at this stage
  than a distribution would.
- **Folding the new cross-commit check into the existing per-commit `O`
  score** instead of adding a separate hard precondition. Rejected because it
  would conflate two different things that happen to share a letter:
  `key_to_agent`'s existing `O` field asks "was cleaning done before power-off,
  within this one commit's evidence" — an intra-commit temporal claim scored
  0–1 like the other three dimensions. The new check asks "did a different,
  earlier commit actually execute" — a cross-commit fact that is either true
  or false, not a quality gradient. Scoring it into `O` would make a commit
  with unmet dependencies still capable of scoring above `tau_pass` on
  Relevance/Coverage/Robustness alone. Keeping it a separate hard gate (same
  tier as the existing `bypass_to_human` branch) means "prerequisite not met"
  can never be outvoted by unrelated evidence quality.
- **A synthetic 3–4-commit case instead of the full 7-commit `sydney_move`.**
  Considered for a cleaner headline number, rejected because the paper's
  standing claim is that this framework was pulled out of a real case, not
  built for a demo — reusing the real 7 commits (including the one that
  doesn't fit neatly, `friend_compensation`, itself a merge of a promise and
  its eventual payout — see the case-data note below) costs more code but
  keeps that claim intact instead of quietly narrowing it.

## Drawbacks and open risks (stated, not hidden)

- **One handpicked violation is a proof of possibility, not a rate.** The
  ungoverned arm's fixed reordering was chosen because it reproduces the
  exact example already discussed ("bond refund before key handover") — a
  skeptical reader can fairly call this cherry-picked. This spec doesn't
  claim otherwise. What it claims is narrower: that the mechanism exists and
  catches this instance. A frequency claim needs the randomized-trials
  design that was explicitly rejected above for this delivery; if the paper
  ends up needing a rate, that's a follow-up spec, not a retrofit onto this
  one.
- **In-process `SandboxWorld` doesn't yet prove agent-independence.** Nothing
  today stops a caller from directly mutating `SandboxWorld._state` and
  bypassing `execute()` — there is no process boundary. The "ground truth
  independent of agent self-report" claim is true relative to the *evidence
  yaml* (which this replaces) but not yet true relative to a fully adversarial
  agent that could reach into the World object itself. E2B, or even just a
  separate OS process talking over a pipe, is what actually closes that gap.
  Calling this delivery's `read_state()` "ground truth" is accurate for the
  comparison it's used for here; it would be an overclaim outside that
  comparison.
- **`requires:` is authored by us, not derived.** The dependency graph in the
  next section is asserted from re-reading the real case, but nothing
  mechanically checks it against `bindings.yaml` or the evidence files — a
  wrong `requires` entry would silently produce a wrong-but-confident
  demonstration. Worth a second pass by whoever owns the case facts before
  this ships in the paper, not just a code review.

## Scope of this delivery

Decided in scope:
- A `SandboxWorld` interface + local in-process implementation (no E2B, no
  new dependency).
- A new cross-commit Ordering mechanism (`requires:` field per commit),
  enforced as a hard precondition, separate from the existing per-commit
  4D-CQ evidence scoring.
- A two-arm harness (`GovernedArm` / `UngovernedArm`) reusing the existing
  `sydney_move` case (all 7 commits) and existing judgment code
  (`agent/gated_loop.py::resolve_precondition`, `GatedAgentLoop`) unchanged.
- A deterministic (non-random, no LLM) adversarial ordering for the
  ungoverned arm.
- A comparison report read from `SandboxWorld.read_state()` after both arms
  run.
- Regression tests asserting the governed end-state stays consistent and the
  ungoverned end-state reaches a specific contradictory state.

Decided against (not revisited without a new reason — see Alternatives above):
- Randomized/statistical trials.
- A real LLM planner.
- Folding `requires` into the existing `O` score.

Punted, not dropped (real follow-up work, tracked so it isn't lost):
- E2B sandbox backend. `SandboxWorld.execute()`/`read_state()` keep their
  signatures; only the internals move from an in-memory dict to calls into a
  process running inside an E2B sandbox. This is what would close the
  agent-independence gap named in Drawbacks.
- Wiring the same `world.execute` call into `agent/langgraph_loop.py`'s
  `executor` node and into `mcp_server/server.py` as a new tool/resource —
  same slot, not implemented this round.
- Validating the `requires:` graph against `bindings.yaml`/evidence by
  whoever owns the case facts (named in Drawbacks).

Unaffected either way:
- `gate.py`'s scoring formulas and `engine.py`'s CLI — no changes.

## Why the sandbox doesn't simulate real-world channels

`bindings/sydney_move_bindings.yaml` already documents that this case has
zero API on its critical path — every commit is executed by a human over
WhatsApp, WeChat/微信, Xiaohongshu/小红书, email, or an NSW government web
form. The sandbox's mock world does not attempt to imitate any of these
channels. It represents the channel-independent semantic fact the channel
was carrying ("the keys were physically handed to the agent," "the bond
refund was confirmed") as a piece of observable state, decoupled from
whichever app or phone call produced it in reality. This is the same
abstraction level `bindings.yaml` already uses ("这份文件记录的是权在谁的账户域"),
just made executable and stateful instead of narrative-only.

## Case-data correction: expectation_setting merged into friend_compensation

Made during this review, upstream of the sandbox work but affecting every
commit count in this spec: `commits/sydney_move_commits.yaml` used to carry
`expectation_setting` and `friend_compensation` as two independent commits.
They aren't independent — they're two stages of the same real event. The
client promised a friend a specific split of resale proceeds
(`expectation_setting`, gated by `expectation_gate` on whether that promise
had feasibility evidence behind it); the resale plan then fell through
(items couldn't sell in time and had to be given away free to whoever could
haul everything in one trip), so the payout became a direct cash-plus-goods
settlement instead (`friend_compensation`, `bypass_to_human`). Modeling them
as two unrelated commits double-counted one real decision as two, and
`sydney_move` drops from 8 commits to 7 as a result — every "8 commits" /
"8-commit" reference elsewhere in this repo (README, the two pre-existing
mechanism diagrams, tests) was updated in the same pass, not left stale.

The merged `friend_compensation` keeps both gate mechanisms rather than
picking one: `precondition_fn: score_expectation_setting` runs as an
internal pre-check (promise stage) whose PASS/ESCALATE result is folded into
`notes` for the audit trail, but the commit's route is unconditionally
`BYPASS_TO_HUMAN` regardless of what the pre-check scores — the payout
decision is inherently human judgment either way. This is deliberately *not*
exposed through `mcp_server/server.py`'s `authorize()`: a bypass_to_human
commit's precondition_fn is excluded from `list_precondition_functions` and
`authorize()` now raises if called on it directly, so an external MCP client
can never mistake "the promise-stage pre-check passed" for "this commit is
authorized" — see `tests/test_mcp_server.py::test_authorize_rejects_bypass_to_human_precondition_fn`,
the regression test for exactly that gap.

## Cross-commit dependency graph (confirmed against the real case)

```
discard_items ----------------\
                                +--> key_to_agent --------\
physical_handover -------------/                          |
                                                            +--> bond_claim_confirm
discard_items --> key_to_building_manager -----------------/

friend_compensation        (independent — two-stage promise+payout, see above)
air_freight_dispatch       (independent)
```

Concretely, `commits/sydney_move_commits.yaml` gains:

```yaml
key_to_building_manager:
  requires: [discard_items]

key_to_agent:
  requires: [discard_items, physical_handover]

bond_claim_confirm:
  requires: [key_to_building_manager, key_to_agent]
```

No other commits gain a `requires` field (absent = no dependency, existing
commits unaffected). This graph is asserted, not derived — see the last
Drawbacks item.

## Component design

### `world/sydney_move_world.py` (new)

```python
class OrderingViolation(Exception):
    """Raised by SandboxWorld.execute() when a commit's `requires` are not
    yet satisfied in world state. Never swallowed internally — the caller
    (governed or ungoverned arm) decides what to do with it."""

class SandboxWorld:
    def __init__(self, commits_by_id: dict):
        self._commits_by_id = commits_by_id
        self._state: dict = {}

    def execute(self, commit_id: str, payload: dict | None = None) -> dict:
        """Checks commits_by_id[commit_id]['requires'] against self._state.
        Any unsatisfied requirement -> raise OrderingViolation(commit_id, missing).
        Otherwise records self._state[commit_id] = {"executed": True,
        "payload": payload or {}, "seq": <monotonic counter>} and returns it."""

    def read_state(self) -> dict:
        """Read-only snapshot. Independent of anything the agent claims about
        itself — this is what the comparison report and the regression tests
        read from. (Independent of agent self-report, not yet independent of
        a fully adversarial agent — see Drawbacks.)"""
```

This is the only new "world" component. It replaces nothing in `gate.py` /
`engine.py`; it is a new, separate module those files don't import.
Consequence: the four existing deployment shapes (CLI, agent loop, LangGraph,
MCP server) keep working exactly as before whether or not `world/` exists —
this delivery can't regress any of them by construction, not just by
intention.

### `commits/sydney_move_commits.yaml` (modified)

Add the three `requires:` fields above. No other fields change. `engine.py`
and the existing `resolve_precondition` evidence-based scoring are unaffected
by this field (they don't read it) — it is read only by the new sequence
precondition check described next.

### Sequence precondition check (new gate branch)

A new function, called before the existing 4D-CQ scoring for any commit that
declares `requires:`:

```python
def check_sequence_precondition(world: SandboxWorld, commit: dict) -> Optional[GateResult]:
    """Returns a blocking GateResult if requires aren't satisfied yet, else None
    (caller proceeds to the existing resolve_precondition path unchanged)."""
```

This is a hard gate: unmet `requires` always blocks, regardless of what the
per-commit evidence yaml says — see the rejected alternative above for why
it isn't folded into the `O` score instead. It runs at the same tier as the
existing `bypass_to_human` branch already sitting outside the 4D-CQ scoring
in `resolve_precondition`. Consequence: a commit can now be blocked for a
reason that has nothing to do with its own evidence quality, which is new —
worth flagging explicitly in the Gate Record's `notes` field so a reader of
`gate_record.jsonl` doesn't mistake an ORDERING block for a low `Q` score.

### `agent/two_arm_experiment.py` (new)

**Both arms are given the identical proposed plan.** This was a correction
made during review: an earlier draft had `GovernedArm` walk the commits in
correct declared order and `UngovernedArm` walk a reordered sequence — which
meant the two arms differed in two variables at once (planner behavior *and*
gate presence), so a governed "block" couldn't be attributed to the new
Sequence mechanism specifically; it might just as easily have been the
pre-existing evidence-based ESCALATE on `bond_claim_confirm` (the real
account-name-mismatch issue, unrelated to ordering). Fixed by holding the
proposed plan constant and toggling only the gate:

- `make_adversarial_reason_fn()`: one fixed, shared `reason_fn` used by
  *both* arms. It proposes commits in this order: `discard_items`,
  `physical_handover`, `bond_claim_confirm`, `key_to_building_manager`,
  `key_to_agent`, `friend_compensation`, `air_freight_dispatch` —
  `bond_claim_confirm` deliberately moved to position 3, before either
  key-handover commit, so it violates the dependency graph the moment it's
  proposed. Not derived from anything in the real case; a named, fixed
  adversarial test input (see "constructed scenario" note above).
- `GovernedArm`: wraps the existing `GatedAgentLoop` from
  `agent/gated_loop.py`, using the shared `reason_fn`. `tool_fn` is upgraded
  from "print a string" to "call `world.execute(commit_id, payload)`".
  `gate_fn` gains the sequence precondition check ahead of the existing
  `resolve_precondition` call. Unchanged: everything else in `gated_loop.py`.
  When `bond_claim_confirm` is proposed at position 3, the sequence
  precondition check fires immediately (its `requires` aren't met yet) and
  blocks — this is now the thing actually being tested, not a side effect of
  walking commits in a safe order.
- `UngovernedArm`: same shared `reason_fn`, same `world.execute` tool call,
  but skips `gate_fn` entirely (calls `world.execute` unconditionally). When
  `world.execute` raises `OrderingViolation` at position 3, the arm logs it
  and treats the commit as "attempted" — the ungoverned arm has no gate to
  stop it, so this represents an agent that plows through a real-world
  irreversible action despite the dependency not being met. This is the
  concrete failure mode being measured: a wrong, irreversible commit that
  actually happens, not just a bad judgment.
- A `run_comparison()` entry point that runs both arms against separate
  `SandboxWorld` instances and prints a report (see below).

Because `GatedAgentLoop.run()` already returns immediately on the first
blocked commit (existing behavior, unchanged), the governed arm never
attempts positions 4–7 either — both arms are stopped by the same proposed
plan; only one of them is stopped *before execution* and the other isn't.

### Comparison report

```
governed:   proposed order: discard_items, physical_handover, bond_claim_confirm, ...
            halted at step 3 (bond_claim_confirm) — BLOCKED[ORDERING]:
            requires key_to_building_manager, key_to_agent (neither executed yet)
            World end state: key_to_building_manager=not_executed,
                              key_to_agent=not_executed, bond_claim_confirm=not_executed
            -> consistent (the proposal was rejected before it could execute)

ungoverned: same proposed order, no gate — 7/7 steps attempted, step 3
            (bond_claim_confirm) executed despite unmet requires
            World end state: key_to_building_manager=not_executed,
                              key_to_agent=not_executed, bond_claim_confirm=executed
            -> contradictory (bond confirmed refunded while keys were never
               handed over) — wrong-commit ground truth read directly from
               World.read_state(), not asserted by the harness

Note: this is a constructed adversarial proposal built to exercise the new
requires: mechanism, not a record of what happened in the real case (see
"The gap, concretely" above). The real bond_claim_confirm friction — a
refund-account-name mismatch — is a separate, already-handled issue; running
`python engine.py run --case=sydney_move` shows it as ESCALATE today,
unrelated to this experiment.
```

### Tests: `tests/test_two_arm_experiment.py` (new)

- `test_governed_arm_blocks_on_ordering_before_execution`: asserts the
  governed arm halts with `route == "ORDERING"` at the proposed
  `bond_claim_confirm` step and that
  `world.read_state()["bond_claim_confirm"]["executed"]` is falsy — i.e. the
  block happens *before* `world.execute` is ever called for that commit.
- `test_ungoverned_arm_reaches_contradictory_state`: asserts the ungoverned
  arm's `world.read_state()` shows `bond_claim_confirm` executed while
  `key_to_agent` and `key_to_building_manager` are not — the regression
  target that must keep failing for governed and keep "succeeding" (i.e.
  demonstrating the wrong-commit) for ungoverned.
- `test_both_arms_receive_the_same_proposed_plan`: asserts `GovernedArm` and
  `UngovernedArm` are constructed with the same `reason_fn` instance/output —
  guards against the exact bug this section was rewritten to fix (arms
  silently drifting to different proposed plans, which would make the
  comparison uncontrolled again without any test noticing).
- `test_world_raises_ordering_violation_on_unmet_requires`: unit test on
  `SandboxWorld.execute` directly, independent of either arm.

## Status / next step

Design approved by the case owner (dependency graph confirmed against the
real facts, delivery scope confirmed as local-only). Next step on approval of
this revision: `writing-plans` to turn this into an implementation plan.
