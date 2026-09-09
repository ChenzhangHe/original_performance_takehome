# Roadmap: 1,152 cycles toward approximately 900

This document is the design baseline for optimization work after commit
`d6f0289`. The current kernel is correct at **1,152 cycles**. The numbers below
separate measured facts from estimates; none of the projected cycle counts are
claims of achieved performance.

## Why scheduling alone is no longer enough

The measured instruction-slot mix at 1,152 cycles is:

| Engine | Slots | Capacity/cycle | Static floor | Slots available at 900 | Required reduction |
| --- | ---: | ---: | ---: | ---: | ---: |
| load | 2,133 | 2 | 1,067 | 1,800 | 333 (15.6%) |
| VALU | 6,594 | 6 | 1,099 | 5,400 | 1,194 (18.1%) |
| ALU | 13,281 | 12 | 1,107 | 10,800 | 2,481 (18.7%) |
| flow | 736 | 1 | 736 | 900 | none; 164 slots of headroom |
| store | 32 | 2 | 16 | 1,800 | none |

The maximum static resource floor is already 1,107 cycles, only 45 cycles
below the observed result. Better scheduling can close some of that gap, but it
cannot reach 900. Reaching that neighborhood requires simultaneous reductions
in load, VALU, and ALU work, followed by another scheduling pass.

This is the central insight from the current measurements: optimize the
program being scheduled, not just the scheduler.

## Principles for the next phase

The useful ideas from the [Jalapeno write-up](https://zartbot.github.io/blog/arch/jalapeno/en.html)
are broader than any individual trick:

- A pipeline may be stalled even when no engine appears saturated, because a
  long dependency chain is gating the next wave of work.
- End-to-end critical-path latency matters in addition to aggregate throughput.
- Algebraic rewrites, locality, prefetching, and exposed instruction-level
  parallelism should be considered together.
- This workload is fixed enough that the kernel generator can behave like a
  specialized compiler: choose representations and implementations per round,
  then autotune the resulting schedule.

The public article reports a result within 2% of the leading submission but
does not publish its exact score or implementation. The public 1,076-cycle
[reference repository](https://github.com/lianghongkey/original_performance_takehome)
and its [discussion](https://github.com/anthropics/original_performance_takehome/issues/44)
are useful evidence that structural improvements beyond scheduling are
available. Treat them as sources of questions and invariants, not code to copy.

## Highest-confidence opportunity: fuse hash stages 2 and 3

For the input `x` to hash stage 2:

```text
x2 = (x + C2) + (x << 5)
   = 33*x + C2

x3 = (x2 + C3) XOR (x2 << 9)
   = (33*x + C2 + C3) XOR ((33 << 9)*x + (C2 << 9))
```

All arithmetic is modulo `2^32`. Computing the two affine expressions and
their XOR takes three VALU operations instead of four. With 32 chunks and 16
rounds, this should remove exactly **512 VALU slots**. Before any other change,
the VALU floor would fall from 1,099 to approximately 1,014 cycles.

This should be the next isolated implementation. Verify the algebra against
the reference for random vectors, then run all existing correctness checks and
measure the new engine mix before changing anything else.

## Pair-linear lookup for cached depths 2 and 3

For adjacent table entries `F[k]` and `F[k+1]`, define:

```text
D = F[k+1] - F[k]
E = F[k] - address(k)*D
F[A] = A*D + E       for A in this pair
```

The identity holds modulo `2^32`. It changes a lookup into selection of a
coefficient pair followed by one MAC. Applied to the current depth-2/depth-3
path, the initial estimate is:

- about 1,488 fewer ALU slots;
- about 128 fewer flow slots;
- about 122 additional VALU slots.

The estimate must be confirmed from emitted instructions. It is attractive
because hash fusion creates VALU headroom while ALU is currently the largest
static floor. Reuse the previous round's parity at depth 1 as a separate small
experiment; the current temporary remains live long enough to potentially
remove 64 VALU mask operations.

## Scratch-memory and lifetime redesign

The current kernel uses 1,522 of 1,536 scratch words. `idx` and `val` need to
remain live for the full batch, but node values and hash temporaries are much
shorter-lived. Build an explicit live-interval table and allocate scratch as a
register allocator would:

- share node and temporary pools between wave-separated chunks;
- reuse input-address storage as node storage after its last use;
- reclaim dead cached/setup vectors in later rounds;
- reserve a small tail pool instead of permanent private temporaries for every
  group.

The goal is to free hundreds of words for depth-4 lookup structures. Because
the current DAG mostly models explicit data dependencies, alias reuse must add
the corresponding WAR and WAW ordering edges; accidental aliasing can otherwise
produce a schedule that looks fast but is incorrect.

## The load target and depth-4 lookup

The kernel emits 2,048 deep-gather loads plus 85 setup/input loads. A 900-cycle
schedule can issue at most 1,800 load slots, so at least 333 slots must
disappear. Each group-round gather costs eight loads; without setup savings,
that means replacing at least **42 group-round gathers**.

Depth 4 occurs in two rounds, or 64 group-rounds. It is the natural next target,
but replacing every gather with one uniform method is unlikely to balance all
engines. Generate several exact implementations and choose between them per
group and per round:

- ordinary gather;
- coefficient selection plus MAC;
- flow-free delta/MAC chains;
- coefficient broadcasts placed after earlier scratch regions die;
- scalar ALU work only where a measured tail has spare ALU issue slots.

This is a constrained search problem. A candidate is useful only if its total
resource floors and dependency-critical path improve, not merely its load
count.

## Representation and index-update choices

The current globally encoded hash state saves a final XOR in each hash but
requires scalar XOR conversion for raw gathered nodes. Once the instruction
mix changes, reevaluate the representation independently for cached-node and
raw-gather rounds. A small dynamic program can choose state encodings and
constant folds per round using measured engine costs.

The index update has another trade: flow `vselect` can choose bias constants
from the parity bit and replace some arithmetic. Flow has 164 slots of present
headroom, potentially more after pair-linear lookup. Use that transformation
selectively and preserve the absolute-address representation so gather-address
arithmetic is not reintroduced elsewhere.

## Prefetching and scheduling

Speculatively loading both children may shorten a dependency chain but consumes
additional load slots. Do not add it while load count is above the target
floor. Reconsider it only after structural load reductions create real issue
headroom.

After each meaningful DAG rewrite, retune scheduling using:

- earliest/latest start times and critical slack;
- engine backlog and deadline-aware priorities;
- per-round mixed implementation selection;
- scratch/register-pressure constraints;
- automated searches over structural choices and scheduler thresholds.

The scheduler goal is to finish within roughly 10--20 cycles of the new static
and dependency lower bounds. If it is already that close, return to operation
count and critical-path reduction.

## Iteration plan

### Phase A: 1,152 toward approximately 1,070

1. Implement and measure hash-stage 2/3 fusion.
2. Implement pair-linear depth-2/depth-3 lookup in isolation.
3. Test depth-1 parity reuse in isolation.
4. Recompute slot counts, resource floors, critical path, and schedule.

### Phase B: approximately 1,070 toward 1,000

1. Add explicit scratch live intervals and safe temporary sharing.
2. Search round-specific raw/encoded state representations.
3. Move selected work onto engines with measured headroom.

### Phase C: approximately 1,000 toward 930--900

1. Eliminate at least 42 group-round gathers, mainly through mixed depth-4
   lookup strategies and any available setup-load reductions.
2. Use reclaimed scratch for coefficients only during the intervals in which
   they are needed.
3. Jointly autotune implementation choices and schedule.

### Phase D: close the remaining tail

1. Add speculative prefetch only where the post-rewrite load trace has slack.
2. Use critical-slack scheduling to drain lagging chunks.
3. Sweep integer thresholds only after structural choices stabilize.

These ranges are navigation aids, not promised intermediate scores.

## Experiment and acceptance protocol

Keep every structural experiment isolated and record:

- exact load, VALU, ALU, flow, and store slot counts;
- each engine's static floor and the overall maximum;
- measured cycles and change from the previous accepted commit;
- scratch high-water mark;
- correctness results and any shapes on which specialization is intentional.

Accept a candidate only when:

1. it passes the built-in tests and all 9 official submission tests;
2. it passes the frozen simulator/reference across at least 32 additional
   scored-shape seeds;
3. `tests/` and `problem.py` are unchanged;
4. its benefit survives a clean repeated benchmark.

Do not spend another iteration on scheduler-only changes unless either the
structural floor has improved or the observed schedule remains more than about
30 cycles above its current lower bound. Revert failed experiments rather than
letting them accumulate in the kernel.

The immediate next implementation is the isolated hash-stage 2/3 fusion. It
has a proof, a precise expected slot saving, no additional scratch requirement,
and directly attacks the current VALU floor.
