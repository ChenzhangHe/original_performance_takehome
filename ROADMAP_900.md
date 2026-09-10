# Roadmap toward 900 cycles: measure, rebalance, remove gathers

Latest: iteration 20 reaches **1,061 cycles**, scratch **1,269**, with permuted
low-bit coefficient lookup and lifetime allocation for hash temporaries.
Acceptance checks pass; details and rejected experiments are in the log.

Latest: iteration 19 reaches **1,066 cycles**, scratch 1,531, by selecting
depth-3 coefficients before one MAC. Full acceptance checks pass. Next is
low-bit coefficient-table permutation to reduce mask-generation work.

2026-09-10 accepted update: iteration 18 implements root-to-depth-2 address
folding and 16 cached round-4 chunks at **1,076 cycles**, scratch **1,531**.
Full official/built-in tests, 32 additional frozen-reference seeds, and all
supported extra shapes pass. See Analysis/Iteration 18 in the log for the
proof and remaining 900-cycle deficits. Optimization work has resumed.

Previous accepted implementation: iteration 17, **1,090 cycles**, with **1,499
scratch words**. Ten chunks use round-4 depth-4 caching; gathered nodes reuse
hash temporaries. Pair-linear shallow lookup, parity reuse, node lifetime
allocation, and readiness reporting are implemented. Work is paused after this
iteration at the user's request. Current floors are VALU 1,062 and load 1,033;
further work must address compute costs and readiness together.
Detailed evidence
is in `OPTIMIZATION_LOG.md`; historical budgets below identify their baselines.

Revised 2026-09-09. Accepted kernel baseline: `d6f0289`, **1,152 cycles**,
scored shape `(height=10, nodes=2047, batch=256, rounds=16)`.
Iteration 1 is now implemented and fully checked at **1,142 cycles**, using
index threshold 23. Current slots: load 2,134, VALU 6,191, ALU 12,417,
flow 736, store 32; scratch 1,530. Historical baseline tables below remain
unchanged for comparison. See iteration 13 in `OPTIMIZATION_LOG.md`.

Documentation commit `684dcbd` did not change the kernel. Approximately 900
is an exploratory target; the current plan does not yet prove it attainable.

## Evidence and corrections

| Engine | Baseline slots | Capacity/cycle | Static floor | Capacity at 900 |
| --- | ---: | ---: | ---: | ---: |
| load | 2,133 | 2 | 1,067 | 1,800 |
| VALU | 6,594 | 6 | 1,099 | 5,400 |
| ALU | 13,281 | 12 | 1,107 | 10,800 |
| flow | 736 | 1 | 736 | 900 |
| store | 32 | 2 | 16 | 1,800 |

Scratch is 1,522 / 1,536 words. The explicit DAG's unit-latency longest path
is 334 operations, excluding resource contention.

The first gather issues at cycle 81 and the last at 1,138. Keeping that first
issue time, 2,048 gathers require a completion boundary of at least
`81 + 2048/2 = 1105`. This is conditional on observed timing, not a universal
lower bound. Startup and post-gather work matter alongside aggregate capacity.

Corrections to the original roadmap:

- Load reduction must begin before targeting sub-1,000 execution: the unchanged
  load count alone requires 1,067 cycles.
- Hash fusion saves 512 body VALU slots, but the diagnostic adds one setup
  load, one broadcast, and eight scratch words.
- Replacing 42 group-round gathers is only a necessary capacity calculation
  under unchanged setup costs; startup, drain, and added computation remain.
- Scratch sharing and scheduling have no guaranteed cycle savings. Measure
  their effects for a concrete candidate.

## Diagnostics already performed

These were in-memory transformations, not changes to the accepted kernel.

| Candidate | Cycles | load | VALU | ALU | flow | Scratch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Accepted baseline | 1,152 | 2,133 | 6,594 | 13,281 | 736 | 1,522 |
| Hash fusion only | 1,158 | 2,134 | 6,083 | 13,281 | 736 | 1,530 |
| Fusion + index threshold 16 | 1,142 | 2,134 | 6,275 | 11,745 | 736 | 1,530 |

Both diagnostic configurations above passed reference comparisons for seeds
123, 456, and 789 on the scored shape. Full acceptance testing is pending.
Index thresholds 20, 23, 24, and 26 also generated 1,142-cycle schedules but
were not individually executed in those seed checks. Threshold 23 has
6,191 VALU and 12,417 ALU slots. Equal cycles across this sweep suggest another
limiting constraint; they do not prove equivalence after future changes.

The fusion of zero-based hash stages 2 and 3 is:

```text
x2 = 33*x + C2
x3 = (x2 + C3) XOR (x2 << 9)
   = (33*x + C2 + C3) XOR ((33 << 9)*x + (C2 << 9))
```

All arithmetic is modulo 2^32. Two independent MACs and an XOR replace four
body operations. The old shift constant 9 is also another stage's multiplier;
removing that shift does not remove the constant from the whole program.

## Iteration 1: land fusion with resource rebalancing

Status: complete. Threshold 23 balances the compute floors at 1,032 VALU
cycles and 1,035 ALU cycles. The next action is iteration 2's readiness report
and depth-4 cost screening.

Implement the diagnostic cleanly, reproduce 1,142 cycles, and select the
index-engine split using execution time and resource headroom. Complete the
acceptance checks below. Do not accept the fusion-only 1,158-cycle version
as a performance improvement.

Deliverable: a verified improvement and exact setup/body/scratch deltas.
Resolve any discrepancy with the diagnostic before adding another change.

## Iteration 2: measure readiness and screen depth-4 designs early

Add a diagnostic report outside the timed kernel, with per-round engine counts,
first/last issue, ready-but-not-issued work, and dependency waits. Separate
first-gather startup, the gather interval, and post-gather drain. Derive
resource bounds over release/deadline intervals where practical; distinguish
proven bounds from observed timing.

Build complete cost tables for gather, coefficient selection plus MAC,
arithmetic lookup, and mixtures by round/chunk. Count setup, conversions,
masks, live scratch, and dependency depth.

A plain 16-node coefficient lookup selects two coefficients from eight pairs:
14 binary vector selects per lookup. Replacing 42 gathers this way adds
588 flow slots, exceeding the roughly 292 slots available after the estimated
shallow-lookup savings. Reject that plain implementation for the 900 budget;
screen other arithmetic/selection mixes.

Deliverable: a feasible resource tradeoff or a quantified rejection, before
investing in a large scratch allocator.

## Iteration 3: reduce shallow lookup costs

Test depth-2 and depth-3 pair-linear lookup separately:

```text
D = F[k+1] - F[k]
E = F[k] - address(k)*D
F[A] = A*D + E, for A in this pair
```

Initial combined body estimates relative to current shallow lookup:
-1,488 ALU, -128 flow, +122 VALU slots. Coefficient setup is additional and
must be measured. Test depth-1 parity reuse separately: potentially 64 fewer
vector masks, contingent on correct encoded-parity polarity and lifetime.

Rebalance engines after each change. Do not blindly retain earlier index
migration decisions when the available ALU budget changes. Evaluate shallow
lookup with the screened depth-4 design if its main value is freeing flow.

Fusion-only counts plus these body estimates yield about 6,205 VALU,
11,793 ALU, and 608 flow slots before new setup: floors of 1,035, 983, and
608 cycles. Load still requires 1,067. Approximately 805 VALU slots remain
above the 900-cycle capacity before adding depth-4 work. This gap is unresolved.

Deliverable: measured benefit or a demonstrated enabling tradeoff for the
next combined candidate; keep speculative variants separate from the baseline.

## Iteration 4: reduce gathers with targeted scratch reuse

Implement the best screened design on a subset of depth-4 group-rounds, then
vary coverage and engine mix. Depth 4 occurs in rounds 4 and 15; optimize
these independently because their downstream work differs.

Fusion emits 2,134 loads. Capacities at 1,000 and 900 require removing at least
134 and 334 loads: at least 17 and 42 eight-load gathers with unchanged setup.
Replacing all 64 depth-4 gathers removes 512 loads, leaving 1,622 before new
setup. This is a search range, not proof that the compute budget fits.

Allocate scratch for the chosen design's actual live intervals. Start with
dead setup/cache vectors and local temporary reuse. Add explicit WAR/WAW
ordering and measure cross-chunk serialization. Preserve enough concurrently
active chunks to hide latency.

Deliverable: reduced execution time including startup/drain, not just fewer
loads or a smaller scratch footprint.

## Iteration 5: shorten dependencies and close compute deficits

Test preparing the next sibling pair or coefficients while the current hash
runs. The current index determines the candidate pair; only its final choice
needs the new parity. Verify preparation costs, readiness, and live storage.
This is a hypothesis, not an established cheap lookup implementation.

Then evaluate individually:

- per-round raw versus encoded hash state, including every conversion;
- relative/path-bit indices during cached rounds, counting conversion back to
  absolute addresses before gather;
- further hash/constant folding and selected index work on spare engines;
- advancing index arithmetic past its actual last reader instead of waiting
  unnecessarily for the full hash, while preserving overwrite hazards.

Deliverable: measured closure of the remaining compute budgets, or a precise
remaining gap. Fitting loads alone does not establish 900-cycle feasibility.

## Iteration 6: tune scheduling around the new graph

Use critical slack, engine backlog, and readiness to target specific bubbles.
Retune after substantial graph changes with bounded parameter searches.
Speculative child loads are candidates only where load capacity and timing
allow them.

Stop scheduler searches that cease improving the observed bottleneck. There
is no universal 10--20-cycle gap guarantee: aggregate and longest-path lower
bounds can both be loose.

## Record and acceptance protocol

Record the hypothesis, parent commit, exact configuration, setup/body slot
deltas, cycles, scratch, startup/readiness/drain, correctness, and next limiting
factor. Label diagnostics separately from accepted improvements. Preserve
failed findings without accumulating failed code in the accepted kernel.

Before accepting an implementation:

1. Pass built-in tests and all 9 official submission tests.
2. Compare with the frozen reference across at least 32 additional scored-shape
   seeds.
3. Validate previously supported extra shapes; preserve compatibility by
   default and explicitly justify any intended specialization.
4. Confirm `tests/` and `problem.py` are unchanged and `git diff --check` passes.
5. Confirm cycles for the final configuration; broaden or repeat testing only
   when further changes or unresolved concerns justify it.

Document and commit accepted performance improvements to the user's fork
under the established commit/push workflow.

The [Jalapeno discussion](https://zartbot.github.io/blog/arch/jalapeno/en.html)
motivates measuring dependency-driven waiting as well as throughput. For every
experiment, ask both: how many instructions disappeared, and when did the
next gather become ready?
