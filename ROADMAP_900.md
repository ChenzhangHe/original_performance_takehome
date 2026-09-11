# Roadmap toward 900 cycles: measure, rebalance, remove gathers

Latest accepted implementation: iteration 29, **995 cycles**, scratch
**1,421**, parent `9466e38`. Paired input-address anchors remove 15 net
load slots; scalar parity constants remove one setup broadcast. Official,
built-in, 32-seed and six-extra-shape acceptance passes. Longer address
chains, wider one-hop address fans, and vectorizing parity/address decoding
were tested and rejected. See the log for measured results.

Current work: **7,236.875** compute equivalents, **1,906** loads and **939**
flow operations. This is one MORE compute equivalent than iteration 28,
but better load/compute balance lowers elapsed cycles by three. Necessary
deficits at 900 remain **486.875 compute equivalents, 106 loads, 39 flow**,
before startup and dependencies. First/last gather: 60/983; drain: 11.

### Public target calibration — checked 2026-09-10 PDT

The community leader is now **869**, not approximately 1,000. Both
[Paradigm's board](https://www.paradigm.xyz/puzzles/anthropic-challenge)
and the [VLIW board's Without Indices category](https://vliw-challenge.fly.dev/)
show 869. Paradigm's tenth entry is 900 and `@zartbotF` is twelfth at 908.
VLIW's separate With Indices leader is 899; our kernel does not output final
indices, so that category is not a valid direct comparison. These are
community judge results, not Anthropic's unpublished best-human result.
See [LEADERBOARD_NOTES.md](LEADERBOARD_NOTES.md) for timestamp and sources.

Our local 995 needs 95 fewer cycles to reach 900 and 126 to reach 869.
No solution was submitted to either board. The current compute floor of
965 is a property of OUR emitted work, not a lower bound on the problem.
Do not use it to argue that the published 869 is impossible.

Next prioritize **joint lookup/representation and instruction selection**:
seek net body-operation deletions, explicitly budget setup and live storage,
then choose which engine executes the surviving work. Corsix's
[analysis](https://www.corsix.org/content/anthropics-compiler-challenge)
emphasizes balancing ALU/VALU, load and flow in each cycle, not only in
aggregate; our paired-anchor win and long-chain regressions illustrate that
distinction. The diagram's hash fusion is already present in our kernel;
do not count it as a new opportunity. Keep hash search bounded and require
full 32-bit equivalence for any proposed identity. Small initialization wins
alone do not constitute a route to 900.

The previous status entries below are historical checkpoints.

Latest accepted implementation: iterations 27/28, **998 cycles**, scratch
**1,436**. This session improves 1,004 -> 1,001 -> 998. A single group uses
the final-round depth-4 cache, allowing its entire second traversal's index
construction to disappear. Dead index storage is reused after its last
access. A conservative post-build pass removes 11 unread constant loads.
Full official, built-in, 32-seed and six-extra-shape acceptance passes.

Current work is 7,235.875 compute equivalents, 1,921 loads and 939 flow
operations. Necessary reductions at 900 are still 485.875 compute
equivalents, 121 loads and 39 flow operations, before dependency/startup
costs. Sub-1,000 is achieved; 900 is not. Direct positive-address construction,
root MACs, larger final caches and early-bit lookup variants were tested and
rejected; see the log before repeating them. Future structural changes must
include a post-build dead-code audit, not just a body-instruction budget.

Latest accepted implementation: iteration 26, **1,004 cycles**, scratch
**1,460**. This session improves 1,037 -> 1,017 -> 1,013 -> 1,004.
Path-parity reuse, direct interpolation/early lookup and bounded ALU issue
reservation are accepted; full correctness checks pass. Hash probing found
no valid shorter expression in its limited templates. Older status below
is historical; operation-count claims must use the latest baseline.

The bounded scheduling pass is complete. Current work: compute 7,239.875
vector-equivalents, load 1,940, flow 926. Reaching 900 still necessarily
requires removing 489.875 compute equivalents, 140 load slots and 26 flow
slots; these reductions alone do not guarantee the target. Prioritize a
different state/expression transformation or lower-cost lookup, accounting
for all setup and conversions. Preserve `hash_fusion_probe.py` as a narrow
rejection tool, not a proof that the ten-instruction Hash is optimal.

Latest accepted implementation: iteration 25, **1,013 cycles**, scratch
**1,468**. A is complete; B now uses direct parity interpolation with early
coefficient selection. It preserves incremental index updates: delaying
their reconstruction alone offers no operation-count saving on this scored
shape. Weighted compute is 7,237.875 (optimistic floor 966), load 1,948
(floor 974), flow 915. Full acceptance passes. C's first bounded template
pass found no shorter Hash; see `hash_fusion_probe.py` and the log.

Next: examine issue balance on this changed DAG with a bounded experiment,
then seek a different expression/state transformation or lower-cost lookup.
At 900 the remaining necessary deficits are 487.875 compute equivalents,
148 load slots and 15 flow slots. Do not count early lookup as eliminated
address computation or treat the rejected Hash templates as a minimality proof.

Latest accepted implementation: iteration 24, **1,017 cycles**, scratch
**1,453**. Plan A below is implemented: retained parity eliminates all
2,112 scalar masks at the old cache coverage with no copies. Retuning to
26 cached chunks yields weighted work 7,245.125, load 1,941, flow 926.
Full acceptance passes. Next is plan B, starting with direct interpolation
separately from address reconstruction. Earlier baselines below are historical.

Current accepted result: iteration 23, **1,037 cycles**, scratch **1,357**.
Negative indices plus shared-mask quartet interpolation allow 24 cached
round-4 chunks. Full acceptance passes. Floors: load 979, VALU 1,016,
ALU 941, flow 904. Earlier "current" paragraphs below are historical.

### Next gate: reduce the DAG, not just issue time

The optimistic combined compute bound is now 1,001 cycles:
`ceil((6094 + 11289/8)/(6 + 12/8))`. Reaching 900 needs at least about 755
fewer vector-equivalent ALU/VALU operations, 157 fewer load slots, and four
fewer flow slots, even before dependency/startup costs. These are necessary,
not sufficient, reductions. Stop broad priority sweeps until a structural
candidate improves this budget. Evaluate any tree pre-encoding/preprocessing
including all its setup loads and stores; evaluate cache changes against
both compute and flow costs. Final-round preselection may shorten the tail
but cannot by itself lower the aggregate compute bound to 900.

## Active next experiments — operation reduction (2026-09-10)

Implementation baseline: `d81dfe0`, **1,037 cycles**. The plan below supersedes
the older experiment ordering later in this file. It is a documentation-only
update at that time. Subsequent execution status is recorded at the top of
this file and in the numbered optimization log; A is now implemented.

### Measured compute budget

One vector-equivalent means one vector operation or eight scalar operations.
This is a throughput accounting unit, not a claim that all operations can
move between engines. In particular, MACs require VALU. Flow/load/store are
budgeted separately.

| Purpose | Vector-equivalent work |
| --- | ---: |
| Hash body (512 x 10) and final decode (32) | 5,152 |
| Parity extraction and index updates | 832 |
| Input XOR and gathered-node encoding | 744 |
| Lookup MACs and masks | 488 |
| Gather-address decoding | 232 |
| Setup | 57.125 |
| Total | 7,505.125 |

The ideal 900-cycle compute capacity is 6,750, leaving a necessary reduction
of **755.125** vector-equivalents. Moving instructions between engines does
not reduce this total; count setup, copies, spills and reconstruction too.

### A. Reuse path parity instead of extracting index bits — first priority

Let p0, p1, ... be encoded-hash parity bits within one root-to-leaf traversal.
The mathematical index representation follows S0=-2 and S(d+1)=2*S(d)+pd
(mod 2^32); the implementation currently keeps only parity at the root.
For the shallow levels, bit j of Sd equals p(d-1-j). Thus the predicates
currently obtained with S&2, S&4 and S&8 are already available parity bits.
The existing masks have values 0/2, 0/4 or 0/8; saved 0/1 parity is equivalent
for vselect's zero/nonzero predicate. Preserve the coefficient-table order.

All paths at depths 2, 3 and 4 (4, 8 and 16 paths respectively) were enumerated
and this predicate identity passed. This proves the local identity, not a
scheduled kernel's correctness or speed.

Potential deletions under current cache coverage:

- Depth 2: 64 group-rounds x one mask x eight lanes = 512 scalar operations.
- Depth 3: 64 group-rounds x two masks x eight lanes = 1,024 operations.
- Cached depth 4: 24 group-rounds x three masks x eight lanes = 576 operations.
- Total: **2,112 scalar operations = 264 vector-equivalents**, before any
  additional storage/copy costs. This is not a promised cycle reduction.

Implementation order: depth 2 only, then depth 3, then cached depth 4. Give
parity producers explicit logical versions and retain only required values
until their last consumer. Extend lifetime allocation across these uses;
do not reserve three permanent parity vectors for every group or introduce
copies when renaming can preserve the producer instead. Current scratch has
only 179 words free (1,357/1,536); measure peak live storage for each step.

Gate: verify net operation deletions, correct dependencies and coefficient
selection, scratch <=1,536, and full acceptance before adopting a faster
candidate. A lower work count with worse cycles is diagnostic evidence,
not an accepted performance improvement.

### B. Lookup from path bits; materialize addresses only when needed

For adjacent encoded node values F0 (lower address) and F1 (higher address),
the latest encoded parity p selects the lower address when p=1. Therefore
the selected value is F1+p*(F0-F1). Earlier parity bits select the pair.
This may remove the shallow lookup's need for a complete S value and simplify
coefficient setup. Construct the full address/index at the first consumer
that actually needs it, including the transition from cached to gathered
levels and the second root traversal.

No net savings are claimed yet. Count delayed address reconstruction,
retained parity storage and conversions; do not count A's mask deletions
again. Retain this experiment only if it lowers total work or demonstrates
a separately measured scheduling benefit without correctness regressions.

### C. Bounded search for a shorter hash expression

The body already uses ten vector instructions per group-round. Saving one
more instruction across all 512 instances would remove 512 vector-equivalents.
No such rewrite has been found. Search small expression windows first:
stage 0 plus stage 1; fused stages 2/3 plus stage 4; and the boundary between
the final hash stage and the next round's input XOR.

Use the actual supported ISA and 32-bit modular arithmetic. Require an
algebraic proof or solver-backed equivalence over all 32-bit inputs before
acceptance, then run the full frozen-reference tests. Include constants,
setup and boundary-round exceptions in the savings calculation. Bound each
search and record exhausted windows rather than running open-ended tuning.

A's gross 264 plus a hypothetical 512 from C would exceed the compute deficit
only narrowly. Both net savings are unproven, and the 157-load/four-flow-slot
deficits plus startup, dependencies and drain remain. This does not establish
that 900 is attainable. After a structural gain, remeasure the budgets and
only then retune cache coverage and scheduling. Any tree preprocessing must
include all loads/stores and respect the input/output contract.

### Reporting and acceptance for each experiment

Record parent commit, hypothesis, body/setup operation deltas, net weighted
work, per-engine floors, cycles, scratch, first/last gather and drain. Run
official 9/9 and built-in 3/3 tests, frozen seeds 1000--1031 on the scored
shape, and the six extra shapes x seeds 123/456/789 listed in the log. Keep
tests and simulator unchanged. Commit/push validated speed improvements to
the user's fork; document unsuccessful experiments without retaining broken
or slower candidates in the accepted kernel.

## Historical baselines and earlier roadmap

Current accepted result: iteration 22, **1,040 cycles**, scratch **1,261**.
Persistent I/O addresses remove 32 flow operations; retuned adaptive issue
policies improve overlap. Full acceptance passes. Floors: load 995, VALU
1,019, ALU 931, flow 920. Next experiments must reduce operation counts;
the load floor alone rules out 900 for the present DAG.

Latest: iteration 21 reaches **1,047 cycles**, scratch **1,197**, with adaptive
whole-vector ALU issue and 20 cached round-4 chunks. Full acceptance passes.
Current floors: load 995, VALU 1,018, ALU 935, flow 952. Flow also exceeds
the 900-cycle budget now, so larger uniform cache coverage is not the answer.

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
