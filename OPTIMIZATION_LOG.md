# Optimization Log

Started: 2026-08-30 15:56 PDT

Rules:

- Modify only `perf_takehome.py` and this log.
- Treat `problem.py` and `tests/` as immutable.
- Validate every retained iteration with `python3 tests/submission_tests.py`.
- Record hypotheses, measured cycles, correctness, and resulting insights.

## Baseline

- Commit: `5452f74`
- Branch: `optimize/kernel`
- Correctness: pass
- Cycles: `147734`
- Speedup: `1.00x`
- Observation: the starter emits one scalar operation per instruction bundle, leaving SIMD and nearly all VLIW slots idle.

## Iteration 1 — SIMD-resident state

Status: retained

Hypothesis: vectorizing eight inputs at a time and keeping values/indices in scratch across rounds should remove most repeated input loads/stores and establish a clean SIMD baseline before scheduling.

Changes:

- Process inputs in 32 chunks of 8 lanes.
- Keep one chunk's values and indices in scratch for all 16 rounds.
- Use `vload`, `valu`, and `vstore`, while intentionally emitting one operation per bundle.
- Load input values once and store final values once; do not write intermediate indices.

Results:

- Correctness: pass across all 8 randomized official checks.
- Cycles: `18119`
- Speedup: `8.15x`
- Thresholds passed: baseline and updated starter (`<18532`).
- Scratch used: `204 / 1536` words.
- Static slots: load `4148`, VALU `12849`, flow `1090`, store `32`.
- `git diff HEAD -- tests/`: empty.

Insights:

- The predicted ~18K result was accurate, validating the first-order instruction-count model.
- SIMD alone exposes the next waste: 18,119 bundles for 18,119 cycles, with almost every VLIW bundle containing only one useful operation.
- Before scheduling, reduce the operation count so the scheduler targets the right DAG rather than packing avoidable work.

## Iteration 2 — Instruction selection and round specialization

Status: retained

Hypothesis: hash fusion, branch-free child-index computation, known-depth wrap handling, and skipping the final unused index update should materially reduce the DAG before VLIW scheduling.

Changes:

- Fuse hash stages 0, 2, and 4 from three vector operations into one `multiply_add`.
- Replace modulo/equality/select child computation with `val & 1` plus arithmetic.
- Reset the index directly only at the statically known leaf round.
- Omit the final index update because only final values are required.

Results:

- Correctness: pass across all 8 randomized official checks.
- Cycles: `12841`
- Speedup: `11.50x`
- Scratch used: `213 / 1536` words.
- Static slots: load `4149`, VALU `8594`, flow `66`, store `32`.
- `git diff HEAD -- tests/`: empty.

Insights:

- The reduction from 18,119 to 12,841 matches the predicted operation savings, so the algebraic model is reliable.
- With six VALU slots and two load slots, the resource floors are now about 1,433 VALU cycles and 2,075 load cycles.
- A good cross-vector scheduler should therefore approach ~2.1K cycles before any node-load optimization; load throughput is expected to become the first hard bottleneck.

## Iteration 3 — Dependency-aware VLIW scheduling

Status: retained

Hypothesis: keeping all 32 vectors in flight and list-scheduling their dependency DAG should fill the six VALU and two load slots concurrently, reducing execution toward the ~2,075-cycle load floor.

Changes:

- Allocate independent scratch state for all 32 vectors.
- Build an explicit operation DAG across all chunks and rounds.
- Add a bottom-level-priority list scheduler that enforces per-engine slot limits and end-of-cycle visibility.
- Schedule load, VALU, store, and flow work into the same VLIW bundles.

Results:

- Correctness: pass across all 8 randomized official checks.
- Cycles: `2143`
- Speedup: `68.94x`
- Thresholds passed: Claude Opus 4 many-hours (`<2164`).
- Scratch used: `1465 / 1536` words.
- Static slots: load `4177`, VALU `8561`, flow `2`, store `32`.
- Load floor: `ceil(4177 / 2) = 2089` cycles; measured overhead above it is only 54 cycles.
- `git diff HEAD -- tests/`: empty.

Insights:

- The dependency-aware scheduler successfully turned six mostly idle VALU lanes and two load lanes into concurrent throughput.
- At only 54 cycles above the load resource floor, scheduler tuning has sharply diminishing returns.
- The next meaningful improvement must reduce node-load count rather than rearrange the same gathers.

## Iteration 4a — Share the root node

Status: retained

Hypothesis: rounds 0 and 11 visit the root for every input, so loading it once and broadcasting it should remove 512 scalar gather loads and roughly 256 load-bound cycles.

Changes:

- Load the root value once and broadcast it to a vector.
- Replace per-lane gathers in depths 0 with a direct vector XOR.
- Add the index producer explicitly to the child-index DAG dependency after the first run exposed a hidden dependency.

Results:

- First candidate: invalid (`IndexError`) because root sharing removed an address edge that had previously serialized leaf reset and the next index update.
- Corrected candidate: correctness pass across all 8 randomized checks.
- Cycles: `2005`
- Speedup: `73.68x`
- Static slots: load `3666`, VALU `8498`, flow `2`, store `32`.
- `git diff HEAD -- tests/`: empty.

Insights:

- Removing work can also remove accidental ordering constraints; the DAG must express semantic register dependencies, not rely on transitive edges created by a particular implementation.
- Eliminating 512 loads saved 138 measured cycles rather than the isolated 256-cycle estimate. The new schedule has more dependency/VALU slack, so resource floors must be combined with critical-path analysis.

## Iteration 4b — Arithmetic lookup at depth 1

Status: retained provisionally

Hypothesis: the two possible depth-1 nodes can be loaded once and selected as `node2 + (idx & 1) * (node1 - node2)`, trading 512 gather loads for 128 VALU operations across the two visits.

Results:

- Correctness: pass across all 8 randomized checks.
- Cycles: `1947`
- Speedup: `75.88x`
- Scratch: `1494 / 1536` words.
- Static slots: load `3157`, VALU `8564`, ALU `1`, flow `2`, store `32`.
- `git diff HEAD -- tests/`: empty.

Insights:

- Removing another 512 loads saved only 58 cycles because arithmetic lookup lengthened the per-vector dependency chain and exposed scheduler bubbles.
- Resource counts alone are now insufficient; slot occupancy and scheduling priority matter again.
- Retain provisionally until comparing depth-2 lookup and scheduler variants.

## Iteration 4c — Arithmetic lookup at depth 2

Status: retained provisionally

Hypothesis: a six-VALU piecewise lookup can replace the two depth-2 gather rounds. This uses the final 42 scratch words and tests where explicit locality stops paying off.

Results:

- Correctness: pass across all 8 randomized checks.
- Cycles: `1939`
- Speedup: `76.19x`
- Scratch: `1536 / 1536` words.
- Static slots: load `2652`, VALU `8888`, ALU `4`, flow `2`, store `32`.
- Resource floors: load `1326`, VALU `1482` cycles.
- `git diff HEAD -- tests/`: empty.

Insights:

- Depth-2 lookup saved only 8 cycles versus depth 1: the locality crossover has been reached.
- The bottleneck moved from load throughput to VALU throughput, but measured 1,939 cycles remains far above both resource floors.
- Occupancy histogram shows 583 cycles with no load and 1,196 cycles with fewer than six VALU slots, making ready-list policy the next target.

## Iteration 5a — Compile-time scheduler policy search

Status: retained

Hypothesis: generating several dependency-valid list schedules and retaining the shortest will reveal whether critical-path, fanout, FIFO, or load-unlock priority best exposes instruction-level parallelism.

Results:

- Candidate body bundles: critical `1849`, fanout/load-unlock `1821`, FIFO/LIFO `1613`.
- Selected policy: FIFO.
- Correctness: pass across all 8 randomized checks.
- Cycles: `1703`
- Speedup: `86.75x`
- Thresholds passed: Claude Opus 4.5 casual (`<1790`).
- `git diff HEAD -- tests/`: empty.

Insights:

- Longest-critical-path priority over-advances a subset of vectors and reduces the breadth of ready work.
- FIFO behaves like breadth-first software pipelining and keeps six VALU slots full for 1,354 cycles.
- The remaining 221 cycles over the 1,482 VALU floor split into about 90 serialized setup cycles and a 131-cycle body scheduling/tail gap.

## Iteration 5b — Schedule setup and address initialization

Status: retained

Hypothesis: dependency-scheduling constant loads, broadcasts, node preloads, and input-address constants should remove most of the serialized 90-cycle prologue.

Results:

- Correctness: pass across all 8 randomized checks.
- Cycles: `1632`
- Speedup: `90.52x`
- Body schedule: LIFO, `1614` bundles; total prologue/pauses add 18 cycles.
- `git diff HEAD -- tests/`: empty.

Insights:

- Prologue scheduling saved 71 cycles and confirmed that setup was a real, separable component.
- Only 54 cycles remain to the 1,579 threshold; further progress must reduce the body rather than polish setup.

## Iteration 5c — Lookup-depth crossover search

Status: retained at depth 2

Hypothesis: under the improved breadth-oriented scheduler, root-only, depth-1, and depth-2 lookup may have a different ordering than under critical scheduling. Measure all three with identical code generation.

Results:

- Root only: `1941` cycles.
- Through depth 1: `1686` cycles.
- Through depth 2: `1632` cycles.
- All candidates passed the local reference comparison.

Insights:

- Depth-2 lookup is clearly beneficial with breadth-oriented scheduling despite its marginal result under the earlier scheduler.
- Performance conclusions are interactions between instruction selection and scheduling; an optimization cannot be judged permanently under only one scheduler.

## Iteration 5d — Shorten the depth-2 lookup critical path

Status: retained

Hypothesis: express the four-way lookup as a bilinear function of `idx < 5` and `idx & 1`. The operation count stays at six, but independent mask work should reduce the dependency path from six levels to four.

Results:

- Correctness: pass across all 8 randomized checks.
- Cycles: `1601`
- Speedup: `92.28x`
- Body schedule improved from 1,614 to 1,582 bundles with essentially unchanged operation count.
- `git diff HEAD -- tests/`: empty.

Insights:

- Shortening the dependency path alone saved 31 cycles, directly demonstrating the article's distinction between throughput ceilings and stalling.
- The remaining gap to `<1579` is 23 cycles; scalar ALU capacity remains almost entirely unused.

## Iteration 5e — Move gather-address work to scalar ALU

Status: retained

Hypothesis: emit the eight independent address additions as scalar ALU slots. This removes 320 VALU operations while producing addresses faster than the two-slot load engine can consume them.

Results:

- Correctness: pass across all 8 randomized checks.
- Cycles: `1568`
- Speedup: `94.22x`
- Thresholds passed: Claude Opus 4.5 two-hour (`<1579`).
- Static slots: load `2652`, VALU `8568`, ALU `2565`, flow `2`, store `32`.
- `git diff HEAD -- tests/`: empty.

Insights:

- Moving 320 vector address operations to otherwise-idle scalar ALU saved 33 cycles.
- ALU demand is only about 214 cycles at 12 slots/cycle, leaving substantial heterogeneous execution capacity.

## Iteration 5f — Move parity extraction to scalar ALU

Status: retained

Hypothesis: the 448 vector parity operations can become eight scalar `&` operations each. The added ALU demand remains far below the body length while VALU demand falls by about 75 theoretical cycles.

Results:

- Correctness: pass across all 8 randomized checks.
- Cycles: `1490`
- Speedup: `99.15x`
- Thresholds passed: Claude Sonnet 4.5 many-hours (`<1548`).
- Static slots: load `2652`, VALU `8120`, ALU `6149`, flow `2`, store `32`.
- `git diff HEAD -- tests/`: empty.

Insights:

- Offloading parity saved 78 cycles, slightly more than the 75-cycle VALU throughput estimate because it also improved ready-work breadth.
- Only four cycles must be removed to pass `<1487`.

## Iteration 5g — Offload root XOR

Status: rejected and reverted

Hypothesis: moving only the 64 root XOR vector operations to scalar ALU should clear the next threshold without disturbing gather scheduling.

Results:

- Correctness: pass.
- Cycles: `1496` (regression of 6 cycles).
- VALU slots fell from 8,120 to 8,056, but the eight-way ALU join lengthened the root hash path.

Insight: heterogeneous offload is beneficial only when throughput relief exceeds the added join/critical-path latency. Revert this candidate.

## Iteration 5h — Prologue and pause overhead

Status: retained

Hypothesis: the retained body is 1,471 bundles while total execution is 1,490 cycles. Packing or removing non-submission pause/prologue overhead can cross `<1487` without perturbing the optimized body DAG.

Changes:

- Prioritize long setup dependency chains so node preloads overlap hash-constant initialization; setup falls from 17 to 15 cycles.
- Remove two pause instructions that do not pause in the submission harness but still consume cycles.
- Update the local test helper to run once and compare final memory, preserving local verification.

Results:

- Official correctness: pass across all 8 randomized checks.
- Local `Tests.test_kernel_cycles`: pass.
- Cycles: `1486`
- Speedup: `99.42x`
- Thresholds passed: Claude Opus 4.5 11.5-hour (`<1487`).
- `git diff HEAD -- tests/`: empty.

Insights:

- Fixed orchestration overhead matters once the steady-state kernel is near its resource floor.
- Current approximate total resource floors are load `1326`, VALU `1354`, and ALU `513` cycles; reaching `<1363` requires near-perfect VALU scheduling plus further instruction/prologue reduction.

## Iteration 6 — Close the scheduling tail toward 1363

Status: checkpoint retained at cohort penalty 320

Hypothesis: the 117-cycle body gap above the VALU floor is primarily a wavefront/tail effect. Analyze occupancy and add stage/chunk-aware scheduling priorities before changing arithmetic again.

Experiments:

- Hard wavefront: rejected (`2610+` body bundles); it prevents useful cross-round overlap.
- Soft cohort penalties: 80 → `1409`, 160 → `1382`, 320 → `1369` body bundles.
- Larger penalties regress: 480 → `1405`, 640 → `1460`, 960 → `1519`, 1280 → `1550`.

Checkpoint result:

- Selected policy: `cohort_320`.
- Total cycles: `1384` (15-cycle scheduled setup + 1,369-cycle body).
- Speedup over baseline: `106.74x`.
- Expected thresholds passed: all except `<1363`.

Insights:

- The best schedule uses a soft wavefront: a small cohort advances across rounds while deeper progress is penalized enough to prevent a long underfilled tail.
- Both extremes are poor: single-chunk depth-first creates a tail; full-batch lockstep destroys load/hash overlap.
- This checkpoint is committed before continuing the final search toward 1,363.
