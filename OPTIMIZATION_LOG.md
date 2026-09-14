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

## Iteration 7 — Final threshold search

### 7a. Partial VALU-to-ALU offload

- Parameterized root XOR and child-index addition offload by chunk count.
- Best coarse combination: root XOR offloaded for 28 chunks; index addition offloaded for all 32 chunks.
- Result before scheduler retuning: `1373` cycles.
- Insight: partial offload can outperform both all-vector and all-scalar choices because it balances issue widths without maximizing join latency on every stream.

### 7b. Remove stale fused-hash constants

- Removed vector constants for shift values 12 and 3, which became unused after `multiply_add` fusion.
- Scratch use fell from `1536` to `1519` words.
- Setup remained load-bound, so cycle count did not change directly.

### 7c. Merge setup and kernel DAGs

- Generalized scratch read/write analysis for load, store, ALU, VALU, and flow slots.
- Folded setup operations into the global dependency graph so constants and shallow-node preprocessing can overlap the early kernel wavefront.
- Result: `1372` cycles.

### 7d. Input-address engine experiment

- Replaced 31 load-engine address constants with flow-engine `add_imm`, then searched mixed load/flow splits.
- Full flow regressed to `1373`; the 1-wide flow chain delayed initial vloads.
- Best choice remains loading all addresses directly. This candidate was rejected.

### 7e. Fine-grained soft-wavefront search

- Searched cohort penalties from 200 through 460 in increments of 10.
- Best body schedules: penalty 230 → `1360`, 240/250 → `1361`.
- Selected policy: `cohort_230`.

Results:

- Official submission tests: `9 / 9` pass.
- Correctness: pass across all 8 randomized checks.
- Local `Tests.test_kernel_cycles`: pass.
- Cycles: `1360`.
- Speedup: `108.63x`.
- Thresholds passed: every published threshold, including Claude Opus 4.5 improved harness (`<1363`).
- `git diff HEAD -- tests/`: empty.

Key insight: the last 24-cycle improvement came primarily from finding the correct software-pipeline wavefront, not from reducing arithmetic. A narrow change in cohort penalty moved the schedule from 1,372 to 1,360 cycles with identical semantic work.

### 7f. Fine neighborhood and contiguous top-node load

- Searched every cohort penalty from 221 through 249. Penalties 226–233 all reproduce `1360`; 230 remains the canonical selection.
- Re-swept nearby root/index ALU-offload combinations. `root=28`, `index=32` remains best at `1360` before memory cleanup.
- Observed that the root plus depth-1/depth-2 nodes occupy seven contiguous memory words but were initialized using seven scalar node loads and several address constants.
- Allocated one eight-word scratch block and loaded the entire top-tree prefix with one `vload`.

Final v2 result:

- Official submission tests: `9 / 9` pass.
- Correctness: pass across all 8 randomized checks.
- Local `Tests.test_kernel_cycles`: pass.
- Cycles: `1354`.
- Speedup: `109.11x`.
- Scratch used: `1515 / 1536` words.
- `git diff HEAD -- tests/`: empty.
- `git diff --check`: pass.

Final insight: after scheduling brought the kernel near its load floor, a small data-layout observation—seven adjacent tree nodes—was worth more than another scheduler tweak. One vector load removed eleven load-engine slots and six measured cycles.

## Iteration 8 — September 3: move beyond the gather-load floor

Starting checkpoint: `a37a926`, 1,354 cycles, 1,515 scratch words.
Recounted issue slots: ALU 10,181; VALU 7,614; load 2,640; store 32.
The load floor alone is 1,320 cycles, so scheduling cannot produce a large gain.

### 8a. Pool shallow-lookup temporaries — rejected

Experiment: alias the hash's second temporary with the dead node value, then
share the extra shallow-lookup temporary among chunks. Add explicit RAW/WAR/WAW
edges in round-major order to make the sharing safe.

Pool size / cycles: 8 / 1,490; 16 / 1,423; 24 / 1,391; 28 / 1,373;
32 / 1,354. The first candidate passed the local reference comparison.
Rejected all pooled variants: memory savings introduce cross-chunk waiting.

### 8b. Reclaim scratch without cross-chunk sharing — retained prerequisite

- Put input addresses in each chunk's node buffer before traversal.
- Reconstruct output addresses there with flow `add_imm` after the final hash.
- Replace the forest-address broadcast with a scalar constant used by ALU.
- Replace the depth-2 comparison's broadcast constant with scalar ALU comparisons.

Depth-2-only result: 1,356 cycles; local reference comparison passed.
This tiny regression is a prerequisite for the next experiment: enough space
for eight more tree-node/coefficient vectors, without serializing chunks.

### 8c. Depth-3 hybrid register lookup — retained

Load nodes 7–14 with one contiguous `vload`, reusing the setup prefix buffer
after its earlier consumers finish. Represent the lower four nodes directly
and the upper four as bilinear coefficients. Use three flow `vselect`s for
the lower half, three VALU multiply-adds for the upper half, and a final flow
selection; scalar ALU builds the masks. This fits the existing three temporary
vectors per chunk and does not require inter-chunk scratch sharing.

- Local reference comparison: passed.
- Cycles: 1,323, with `cohort_430`.
- Scratch: 1,534 / 1,536 words.
- Slots: ALU 13,258; VALU 7,748; load 2,132; flow 288; store 32.
- Removes 512 gather loads, with four net extra setup load slots.

Insight: a pure memory reduction is not sufficient. The lookup deliberately
splits its work across ALU, flow, and VALU; otherwise the replacement arithmetic
would simply become a larger bottleneck. VALU is now the leading resource floor
(ceil(7,748 / 6) = 1,292 cycles), not load (1,066 cycles).

### 8d. Partial hash offload and implicit root indices

Offload only the constant-XOR arm of hash stage 1 to scalar ALU for the first
N chunks. With the hybrid lookup, N = 0 / 4 / 8 / 12 / 16 / 20 produced
1,323 / 1,316 / 1,314 / 1,309 / 1,320 / 1,360 cycles. Local correctness passed.

Remove the leaf's explicit index-zero operation and rebuild the root's child
index as `1 + parity`, instead of doubling a known zero. Removes 96 VALU slots
in the submission shape; the 12-chunk hash-offload candidate reaches 1,296.
The implicit index is never read before it is reconstructed.

### 8e. Move depth-2 lookup to flow

Use four raw node vectors, scalar masks, and three `vselect`s rather than
bilinear interpolation. Retune the hash offload because the VALU/ALU balance
changed: N = 0 / 4 / 8 / 12 gives 1,267 / 1,269 / 1,279 / 1,320 cycles.
Retain the lookup, but undo the now-excessive hash offload.

### 8f. Share only a short-lived depth-3 intermediate

Reuse the dead setup prefix buffer for the upper-right pair of the depth-3
selection tree. Its lifetime ends as soon as the upper quartet is selected;
unlike 8a, it does not span the entire lookup. Add explicit dependencies from
all setup operations and between each shared writer and the preceding reader.
Order reuse by round and descending chunk number, matching the cohort priority.
The scheduler now computes bottom levels using an actual topological ordering,
since these new dependencies can point to operations emitted later.

This enables seven flow selections with no interpolation FMAs. Result: 1,242
cycles; official submission suite passed 9/9. Root/hash offload tuning then
reached 1,238 (root 32 chunks, hash 4 chunks).

Insight: scratch sharing is not categorically bad. The length of the shared
value's lifetime and the direction of its dependency chain decide whether it
creates pipeline stalls. The same eight-word setup buffer is used successively
for shallow-node loading, depth-3 loading, and the short-lived selection result.

### 8g. Depth-1 flow selection and final tuning

Replace the depth-1 interpolation FMA with flow `vselect`, keeping its parity
mask vectorized. Remove the now-unused scalar delta. Sweep root/hash offload
and index-addition placement using `tune_kernel.py`; every candidate is checked
against the frozen simulator and reference, not just counted statically.

- Root/hash/index = 32/2/32: 1,231 cycles, retained.
- 32/6/28 and 32/6/32 also reach 1,231; prefer the lower offload count.
- Moving index additions back to VALU: 32/2/24 gives 1,244; 32/2/28 gives 1,235.
- Extend the cohort scan to include every penalty from 221 through 280;
  retained policy is `cohort_260`.
- Cache scheduling priorities and keep only the best schedule in memory;
  this reduces host build overhead, independently of simulated cycles.
- Remove the old mixed input-address-generation switch: after buffer reuse,
  deriving later addresses from chunk 0 would need additional anti-dependencies.

Final candidate: 1,231 cycles, 1,530 / 1,536 scratch words. Slots: load 2,132;
VALU 7,036; ALU 13,568; flow 736; store 32. Resource floors are 1,066 / 1,173 /
1,131 / 736 / 16 cycles respectively. The remaining 58-cycle gap above the VALU
floor is a scheduling/latency target, not proof that this lower bound is attainable.

Overall: 1,354 → 1,231 cycles (123 fewer, 9.08% reduction), 120.01x the original
147,734-cycle baseline. No simulator or submission-test changes.

Final verification completed:

- Official `tests/submission_tests.py`: 9/9 pass, consistently 1,231 cycles.
- Frozen simulator/reference: 32 additional seeds (0–31) pass on the scored shape.
- Extra `(height, rounds, batch)` shapes `(3,1,8)`, `(3,4,16)`, `(3,9,64)`,
  `(4,12,64)`, `(10,22,256)`: all pass seeds 0, 1, and 123.
- `git diff origin/main -- tests/ problem.py`: empty.
- `git diff --check`: pass.
- Changes remain local on `optimize/kernel-v2`; no commit or push in this round.

Reproduce final tuning/validation:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/submission_tests.py
PYTHONDONTWRITEBYTECODE=1 python3 tune_kernel.py --hash-alu-chunks 2 6 --alu-index-chunks 24 28 32
git diff origin/main -- tests/ problem.py
git diff --check
```

## Iteration 9 — September 5: address representation and encoded hashes

Starting checkpoint: `563b40b`, 1,231 cycles, 1,530 scratch words.

### 9a. Profile the remaining gap

At 1,231 cycles, total slots and resource floors were:

- load 2,132 → 1,066 cycles; VALU 7,036 → 1,173 cycles;
- ALU 13,568 → 1,131 cycles; flow 736 → 736 cycles.

VALU occupancy was 99.5% from cycles 100–1,130, but only 57.3% in the last
100 cycles. The remaining gap is therefore a combination of work count and an
underfilled software-pipeline tail.

Two read-only dependency/scheduling probes were rejected:

- Relax the hash dependency on 384 index FMAs: 1,233 cycles (8 seeds pass).
- Switch directly to critical-path priority in the tail: no useful gain; most
  switch points regress substantially. Dependency freedom is useful only if
  the scheduler can exploit it.

### 9b. Keep tree addresses rather than logical indices — retained

Represent traversal state as `address = forest_values_p + index`. For all
non-root updates:

```text
address_next = 2 * address - 6 + parity
```

This removes 2,048 scalar address additions in eight gather rounds. Adjust all
shallow-lookup masks and thresholds for the shifted representation, and rebuild
the root child as address `8 + parity`. Preserve the root value in one scalar
instead of an eight-word broadcast, making room for the new vector bias.

- Correctness: local frozen reference passes.
- Address-only checkpoint: 1,224 cycles.
- Scratch: 1,534 / 1,536 before later encoding cleanup.

The direct gain is modest because VALU remains the bottleneck. The main value
is freeing enough ALU capacity to move hash work off VALU. Rebalancing the
stage-1 constant-XOR arm across 18 chunks reaches 1,205 cycles. Moving an equal
amount from the final hash stage is better: 1,202 cycles.

### 9c. Carry an encoded hash between rounds — retained

The final hash stage is:

```text
hash = x ^ (x >> 16) ^ C
```

Carry `encoded = hash ^ C = x ^ (x >> 16)` internally. Pre-XOR the register-
resident shallow nodes with `C`; XOR gathered nodes with `C` using freed scalar
ALU capacity. Since `C` is odd, update child addresses using inverted parity:

```text
root child  = 9 - (encoded & 1)
next address = 2 * address - 5 - (encoded & 1)
```

Round 0 uses the raw root; later root rounds use the encoded root. Initially
decoding every final output with eight scalar XORs produced 1,193 cycles.

On the last round, emit the ordinary three-operation final hash stage instead;
the output is already decoded and 256 scalar output XORs disappear. This uses
an eight-word final-constant vector but still fits scratch.

### 9d. Shared-buffer and gather-XOR alternatives — rejected

- Two depth-3 shared selection buffers instead of one: 1,194 versus 1,193.
  Removing one serialization chain does not offset its changed ready-work order.
- Offload gather/node XORs lane-by-lane and reduce final-stage offload by an
  equal resource amount: 1,216 / 1,215 / 1,204 for representative mixes;
  the original vector gather XOR is retained.

### 9e. Two-phase tail scheduler — retained

Run the existing soft cohort pipeline initially, then switch late in execution
to a laggard policy that prioritizes lower-numbered, less-advanced chunks.
Critical-path and round-first tail policies regress; explicitly catching up the
laggards shortens the underfilled tail.

Search cohort penalty 200–240 and switch cycles 1,000–1,040. Best retained
policy is `tail_laggard_224_1034`.

### 9f. Final-round decoding and mask rebalance — retained

Instead of decoding 32 output vectors with 256 scalar XORs, emit the ordinary
three-VALU final hash only in the last round. Earlier rounds remain encoded.
This moves 256 slots off the near-saturated ALU in exchange for only 32 VALU
slots. Retune the two-phase scheduler.

Finally move the six depth-2/depth-3 bit masks for one chunk from scalar ALU to
VALU. Zero / one / two chunks give 1,176 / 1,172 / 1,176 cycles respectively;
one chunk best balances the issue engines. Moving an entire chunk's 14 parity
masks to VALU ties at 1,172 and is rejected as needless complexity.

Additional rejected checks:

- Add a third scheduling phase after the laggard phase: no candidate improves
  1,172; returning to critical, round-first, or cohort priority all tie/regress.
- Move gathered-node XOR from VALU to per-lane ALU: 1,177 for one chunk.
- Encode gathered nodes with VALU instead of per-lane ALU: ties at 1,172, both
  with and without the six-mask transfer; retain the smaller transfer.

Final iteration-9 candidate:

- Cycles: 1,172 (59 fewer than 1,231; 4.79%; 126.05x baseline).
- Scratch: 1,534 / 1,536 words.
- Selected policy: `tail_laggard_240_1038`.
- Static slots/floors: load 2,133/1,067; VALU 6,594/1,099;
  ALU 13,281/1,107; flow 736/736; store 32/16.
- Official `tests/submission_tests.py`: 9/9 pass, consistently 1,172 cycles.
- Frozen simulator/reference: 32 additional seeds pass on the scored shape.
- Extra `(height, rounds, batch)` shapes `(3,1,8)`, `(3,4,16)`, `(3,9,64)`,
  `(4,12,64)`, and `(10,22,256)` pass seeds 0, 1, and 123.
- `git diff origin/main -- tests/ problem.py`: empty; `git diff --check`: pass.

## Iteration 11 — September 8: heterogeneous pipeline pacing

Starting checkpoint: `3b742f9`, 1,168 cycles, 1,522 scratch words.

### 11a. Per-engine cohort penalties — retained

The cohort scheduler previously applied one round penalty to every issue
engine. Capture the same operation DAG and search independent penalties for
load, VALU, ALU, and flow, plus the compute-only laggard switch point. Every
candidate schedule is checked against the frozen simulator before retention.

Random search first reached 1,158 cycles with `(300, 240, 240, 300, 900)`.
Coordinate descent improved this to:

```text
load penalty = 360
VALU penalty = 240
ALU penalty  = 240
flow penalty = 220
laggard switch = cycle 900 (VALU, ALU, flow only)
```

Result: 1,156 cycles; 16 frozen-reference seeds pass during tuning. Retain the
policy as `tail_hetero_360_240_240_220_900` while preserving the earlier
policies as fallbacks for other shapes.

Insight: load must run much farther ahead than arithmetic to sustain gathers,
while flow should stay closer to the arithmetic wavefront. A single cohort
penalty hid this difference.

### 11b. Initialization and resource-rebalancing checks — rejected

- Change setup pseudo-chunk priority: setup chunks 0 / 8 / 16 / 24 / 28 give
  1,233 / 1,218 / 1,165 / 1,162 / 1,161; values 30–32 tie at 1,156.
- Prioritize setup by bottom-level criticality: 1,159.
- Generate input addresses through flow from one persistent base: 1,163.
- Add a second depth-3 shared buffer: ties at 1,169 before heterogeneous tuning.
- Move a whole comparison class to VALU and compensate with hash offload:
  thresholds 12 / 16 / 18 / 20 give 1,184 / 1,179 / 1,179 / 1,177.
- Move bit masks to VALU for 0 / 1 / 2 / 3 / 4 chunks: 1,163 / 1,156 /
  1,160 / 1,162 / 1,160; retain one chunk.

Final iteration-11 candidate: 1,156 cycles, 12 fewer than `3b742f9`, with
scratch unchanged at 1,522 / 1,536 words. Relative to the original baseline,
speedup is 127.80x.

Final verification:

- Official `tests/submission_tests.py`: 9/9 pass, consistently 1,156 cycles.
- Frozen simulator/reference: 32 additional seeds pass on the scored shape.
- Five extra `(height, rounds, batch)` shapes pass seeds 0, 1, and 123.
- `git diff origin/main -- tests/ problem.py`: empty; `git diff --check`: pass.
- Retain only the winning tail candidate after the search; official suite host
  runtime returns from about 10 seconds to 3.8 seconds without changing cycles.

## Iteration 10 — September 7: engine-specific tail scheduling

Starting checkpoint: `aaa4080`, 1,172 cycles.

### 10a. Apply laggard priority only to compute engines — retained

The previous tail phase reordered every engine. Search subsets of engines and
switch points while preserving the same instruction DAG. The best subset is
VALU + ALU + flow; load and store retain the normal cohort ordering. Switching
at cycle 980 initially reaches 1,169 cycles and passes 8 frozen-reference seeds.

Fine search over pre-tail cohort penalties 230–250 and switch points 960–1,000
finds a broad 1,168-cycle plateau. Retain the canonical policy
`tail_compute_245_960`; keep the older all-engine tail policy as a fallback for
other input shapes.

Insight: the memory pipeline already has the right request order. Reordering
loads to chase a lagging chunk disrupts useful prefetch overlap; only compute
engines should drain the laggard's now-ready work.

### 10b. Alias vector constants with their scalar lane — retained

For a new vector constant, load its value directly into lane 0 of the allocated
eight-word vector, then broadcast in place. Lane 0 remains the scalar alias.
This removes one scratch word per unique vector constant with identical setup
instruction count and no cycle change.

- Scratch: 1,534 → 1,522 words.
- Cycles remain 1,168 after final scheduler tuning.

Rejected uses of the freed scratch:

- Move one complete comparison class from ALU to VALU and compensate with hash
  offload: thresholds 12 / 16 / 18 / 20 yield 1,184 / 1,179 / 1,179 / 1,177.
- Add a second shared depth-3 selection buffer: ties at 1,169 before final tuning.

Final iteration-10 candidate: 1,168 cycles, four fewer than `aaa4080` and
126.48x the original 147,734-cycle baseline.

Final verification:

- Official `tests/submission_tests.py`: 9/9 pass, consistently 1,168 cycles.
- Frozen simulator/reference: 32 additional seeds pass on the scored shape.
- Five extra `(height, rounds, batch)` shapes pass seeds 0, 1, and 123.
- `git diff origin/main -- tests/ problem.py`: empty; `git diff --check`: pass.

## Iteration 12 — September 8: independently timed engine tails

Starting checkpoint: `4ddbe5f`, 1,156 cycles.

### 12a. Fine-grained ALU-to-VALU index transfer — rejected

The aggregate issue floors suggest that moving roughly five final index updates
from scalar ALU to VALU should improve resource balance. Test the parity mask,
the final subtraction, and both together, first by count and then at every
individual chunk position. Each complete index update removes 16 ALU slots and
adds two VALU slots.

The transfer consistently ties or regresses: under the 1,156-cycle scheduler,
the first 1–12 transferred chunks produce 1,157–1,161 cycles; under the later
1,154-cycle scheduler they produce 1,155–1,159. Several isolated chunks tie,
but none improves. The apparent aggregate ALU surplus is not available on the
critical dependency chain; adding even one VALU operation can delay a gather or
hash stage. Retain the scalar index update.

### 12b. Joint heterogeneous-penalty search — retained

The previous iteration tuned one engine penalty at a time. Search 3,000 joint
combinations around that result while keeping the instruction DAG fixed. This
finds `tail_hetero_290_200_190_230_890` at 1,154 cycles. A further 5,000-point
local search finds a broad 1,154 plateau but no lower result.

### 12c. Independent tail switch points — retained

The heterogeneous policy still switched VALU, ALU, and flow into laggard mode
on the same cycle. Add a `tail_multi` policy with one transition point per
compute engine. A coarse grid reaches 1,153 cycles; two local joint searches
reach the retained policy:

```text
load / VALU / ALU / flow penalties = 290 / 190 / 195 / 260
VALU / ALU / flow tail switches    = 975 / 780 / 800
```

Final result: **1,152 cycles**, four fewer than `4ddbe5f`, with the operation
mix and scratch allocation unchanged. Speedup over the original 147,734-cycle
baseline is 128.24x.

Insight: the issue engines finish their useful wavefront phases at very
different times. ALU and flow should begin pulling lagging chunks forward near
cycle 800, while VALU must preserve the normal pipelined order until roughly
cycle 975 so it does not delay hashes and gathers. One global tail transition
concealed this scheduling opportunity.

Final verification:

- Built-in tests: 3/3 pass, consistently 1,152 cycles.
- Official `tests/submission_tests.py`: 9/9 pass, consistently 1,152 cycles.
- Frozen simulator/reference: 32 additional seeds pass on the scored shape.
- Extra `(height, rounds, batch)` shapes `(3,5,32)`, `(4,7,64)`,
  `(6,11,128)`, `(8,12,256)`, `(10,8,256)`, and `(10,20,256)` pass.
- Scratch remains 1,522 / 1,536 words; `git diff --check` passes.

## Next-stage roadmap

The resource budget, structural opportunities, and staged plan for moving from
1,152 cycles toward approximately 900 are documented in
[`ROADMAP_900.md`](ROADMAP_900.md).

### Roadmap review — 2026-09-09

Replanned around resource tradeoffs and gather readiness. The accepted kernel
remains at 1,152 cycles. In-memory diagnostics produced 1,158 for hash-stage
2/3 fusion alone and 1,142 with some index arithmetic migrated to VALU. Both
categories passed three scored-shape reference seeds; full acceptance testing
and kernel implementation are pending. The revised roadmap brings load design
forward, includes constant/scratch overheads, and replaces speculative cycle
milestones with experiment deliverables and acceptance gates.

## Iteration 13 — fuse hash stages 2/3 and rebalance index work

Date: 2026-09-09. Parent commit: `684dcbd` (kernel baseline `d6f0289`).

Implemented the two-affine-arm rewrite from the revised roadmap. The two MACs
read the same stage input independently; their XOR produces the stage-3 result.
Constants derive from `HASH_STAGES` with 32-bit modular biases. Stages 2 and 3
no longer allocate their old constants unnecessarily, while shared constants
such as multiplier 9 remain available.

Fusion alone measured 1,158 cycles in the earlier diagnostic. Setting
`ALU_INDEX_CHUNKS = 23` moves nine chunks' non-root index subtractions to VALU,
using the capacity freed by fusion. The previously sampled thresholds 16, 20,
23, 24, and 26 all scheduled at 1,142; 23 balances the compute resource floors
at 1,032 VALU and 1,035 ALU cycles. This final configuration received the full
verification below.

| Metric | Previous | Accepted | Delta |
| --- | ---: | ---: | ---: |
| Cycles | 1,152 | 1,142 | -10 |
| load slots | 2,133 | 2,134 | +1 |
| VALU slots | 6,594 | 6,191 | -403 |
| ALU slots | 13,281 | 12,417 | -864 |
| flow slots | 736 | 736 | 0 |
| store slots | 32 | 32 | 0 |
| Scratch words | 1,522 | 1,530 | +8 |

Accounting: fusion removes 512 body VALU slots; new constant setup adds one
load and one broadcast; moving 108 vector-equivalent index subtractions to
VALU adds 108 VALU slots and removes 864 ALU slots. Overall cycle reduction is
0.87%, with 129.36x speedup over the original 147,734-cycle baseline.

First gather moves from cycle 81 to 77, last gather from 1,138 to 1,127.
Fourteen cycles remain after the last gather. The static load floor remains
1,067, higher than both compute floors: gather readiness and issue capacity
are the next investigation. Scratch has only six words left, making explicit
lifetime planning necessary for additional vector constants.

Verification:

- Built-in tests: 3/3 pass, including trace and scored-shape execution.
- Official submission tests: 9/9 pass at 1,142 cycles.
- Frozen simulator/reference: 32 seeds (1000 through 1031) pass at 1,142.
- Extra `(height, rounds, batch)` shapes `(3,5,32)`, `(4,7,64)`, `(6,11,128)`,
  `(8,12,256)`, `(10,8,256)`, `(10,20,256)` each pass seeds 123, 456, 789.
- `tests/` and `problem.py` are unchanged; `git diff --check` passes.

Next: implement the roadmap's readiness diagnostic and screen depth-4 lookup
costs including setup, engine balance, and live scratch before changing lookup.

## Iteration 14 — pair-linear shallow lookup without shared selection storage

Parent: `634e669`. Result: **1,126 cycles**, down 16. Added
`analyze_kernel.py` to recover actual issue times and dependency-ready waits
from emitted operation identities, with engine/round counts and gather timing.
Baseline loads were full for 1,066 cycles, confirming issue capacity pressure.

Depth 2 selects the slope and intercept of an adjacent encoded-node pair and
uses one MAC. Depth 3 computes a value for each quartet from its selected
coefficients, then selects the correct half. This uses two MACs and five
selects rather than one MAC and six selects; critically, it needs no shared
vector or cross-chunk serialization. Setup reuses a dead scalar temporary.

Slots: load 2,135 (+1), VALU 6,377 (+186), ALU 10,947 (-1,470), flow 544
(-192), store 32. Scratch 1,531 (+1). The ALU delta includes 18 setup operations.
First/last gather: 75 / 1,106; drain: 19 cycles. Winning policy remains
`tail_hetero_360_240_240_220_900`. Index thresholds 23 and 28 both give 1,126;
32 gives 1,127, so keep 23.

Validation: official 9/9; built-in 3/3; frozen-reference seeds 1000--1031;
all six previously recorded extra shapes with seeds 123, 456, 789. Extra-shape
correctness is preserved, though `(8,12,256)` increases from 797 to 813 cycles:
the engine split is tuned for the scored shape. Tests and simulator unchanged.
Next: parity reuse, then a concrete depth-4 lookup storage/capacity experiment.

## Iteration 15 — parity reuse and post-schedule node allocation

Parent `5e53ff3`. Result **1,124 cycles**, scratch **1,371 words** (160 fewer).
Reuse encoded root parity with reversed depth-1 selection operands, eliminating
64 VALU masks. Input addresses and output-store addresses use index storage
before its first / after its last index use. Virtual node buffers are assigned
physical vectors after scheduling, by lookup lifetime, preserving all issue
cycles. Twelve physical vectors replace 32 private vectors. Read-at-start,
write-at-end semantics permit reuse when a previous last read coincides with
the next first write. The simulator validates those same-cycle transitions.

Slots: load 2,135; VALU 6,313; ALU 10,947; flow 544; store 32. First/last
gather 74/1,103; drain 20. All official 9, built-in 3 tests pass; frozen seeds
1000--1031 and six extra shapes with seeds 123,456,789 pass. Scratch across
extra shapes remains within the 1,536-word limit.

Rejected experiments: moving input address generation to flow saved 32 loads
but regressed from 1,124 to 1,127 cycles. Fixed shared node pools added ordering
edges: all-depth pooling measured 1,581--1,225 cycles for 4--16 banks; even
shallow-only pooling measured 1,412--1,218. Replaced this approach with lifetime
coloring of the already-selected schedule, which introduces no new waits.
Next: use the newly freed storage for a depth-4 cache candidate.

## Iteration 16 — selectively cache depth 4 in round 4

Parent `419414a`. Result **1,098 cycles**, down 26. Cache the 16 encoded
depth-4 nodes as adjacent-pair coefficients, then evaluate four quartets with
four MACs and eleven vector selects per lookup. Apply this only to chunks
0--7 in round 4 of the scored shape. Other shapes retain the generic path,
avoiding unbudgeted cache live ranges. Node coloring now supports three
virtual lookup vectors and still preserves issue cycles.

Slots: load 2,081 (-54: 64 gather loads removed, 10 setup loads added), VALU
6,361 (+48), ALU 11,371 (+424), flow 632 (+88), store 32. Scratch 1,507.
Gather first/last: 78/1,077; drain 20. Static floors: load 1,041, VALU 1,061,
ALU 948, flow 632. VALU now exceeds the load floor.

Screening: round-4 coverage 4/8 chunks gave 1,114/1,098; larger coverage
exceeded scratch for the chosen schedules. Round-15 coverage 4/8/24/32 gave
1,162/1,202/1,239/1,237. Covering both rounds gave 1,146/1,170 at 4/8 chunks.
Several other candidates exceeded scratch. This rejects uniform final-round
caching: it can create a 140-cycle drain despite saving 256 gather loads.

Validation: official 9/9, built-in 3/3, frozen seeds 1000--1031; all six extra
shapes with seeds 123,456,789 pass. Simulator/tests unchanged. Next: reduce
node storage further and rebalance computation before expanding cache coverage.

## Iteration 17 — alias gathered nodes with hash temporaries; expand early cache

Parent `1d8bbfc`. Result **1,090 cycles**, down 8. Deep gathered values now use
the chunk's hash `tmp2`: the input XOR consumes the node before the hash uses
that temporary. This needs no cross-chunk dependency. Only cached lookups use
the node lifetime allocator. The recovered storage permits increasing cached
round-4 coverage from 8 to 10 chunks.

Slots: load 2,065, VALU 6,369, ALU 11,467, flow 654, store 32. Scratch 1,499.
Floors: load 1,033, VALU 1,062, ALU 956, flow 654. First/last gather 78/1,069;
drain 20. VALU issues at full capacity for 1,023 cycles. Compared with this
session's 1,142-cycle starting point, the accepted improvements total 52 cycles
(4.55%). Original-baseline speedup is 135.54x.

Coverage sweep with aliasing: 8/10/12/16/20/24/28/32 cached chunks gave
1,098/1,090/1,090/1,090/1,103/1,107/1,101/1,111 cycles. Choose the smallest
coverage attaining 1,090, avoiding extra lookup work and leaving more engine
capacity for future changes.

Rejected compute-rebalancing experiments:

- Offloading the hash-stage-1 XOR arm to scalar ALU across 4/8/12 chunks did
  not improve the cache-8 baseline; joint index thresholds were also checked.
- Selecting the index bias through flow and then using one MAC removes index
  subtraction work but lengthens its dependency path. At cache coverage 8,
  4 flow-index chunks gave 1,096, while larger coverage gave 1,099--1,110.
- Joint cache coverage 16/24/32, flow-index coverage 8/16/24, and hash offload
  0/8 produced no improvement over 1,090 (best 1,102). Removed the unused
  flow-index implementation rather than retaining a failed default path.

Final verification: official 9/9 at 1,090; built-in 3/3; frozen seeds
1000--1031; all six previous extra shapes with seeds 123,456,789. Tests and
simulator unchanged; `git diff --check` passes. The readiness report runs on
the final physically allocated instruction stream.

Paused after this iteration at the user's request. Next investigation should
target VALU work or dependency-aware changes to the hash/index transition;
more uniform caching or scalar offload alone did not help. No unvalidated
candidate is left enabled.

## Analysis 18 — fold the root-to-depth-2 address transition (2026-09-10)

Accepted kernel remains `cea30d1`, 1,090 cycles. The following were in-memory
diagnostics only; no kernel implementation or full acceptance is claimed.

Let p0 and p1 be the encoded parity bits of the root and depth-1 hashes.
The current absolute address calculation is:

```text
A1 = 9 - p0
A2 = 2*A1 - 5 - p1 = 13 - 2*p0 - p1
```

Depth-1 node selection already needs only p0. Keep p0 in index storage instead
of materializing A1. After selecting that node, use flow to choose the next
base (11 for p0=1, otherwise 13), writing it back to index storage. This base
can be selected before the depth-1 hash completes. After p1 is available,
subtract it from the chosen base to obtain A2. Preserve the dependency from
the depth-1 node selection to the overwrite of p0, and from base selection to
the final subtraction. Other depths retain ordinary address representation.

This removes 512 scalar root-subtraction instructions and 64 depth-1 MACs,
adding 64 flow selects and two vector constants (two loads/two broadcasts).
The extra constant storage is partly offset by changed node live ranges.

| In-memory configuration | Cycles | Scratch |
| --- | ---: | ---: |
| Root folding, cache 10, no hash ALU offload | 1,087 | 1,507 |
| Root folding, cache 16, no hash ALU offload | 1,076 | 1,531 |
| Root folding, cache 24, no hash ALU offload | 1,097 | 1,531 |

Also sampled hash ALU offload 4/8 for each of these cache coverages: none beat
1,076. Best configuration passes frozen-reference seeds 123 and 1000--1031.
Root folding at cache 10 additionally passes seeds 123,456,789 on shapes
(10,16,256), (3,5,32), (4,7,64), and (10,20,256). These checks do not replace
official/built-in tests and full extra-shape validation of the cache-16 version.

Best candidate slots: load 2,019, VALU 6,331, ALU 11,243, flow 784, store 32.
Static floors: load 1,010, VALU 1,056, ALU 937, flow 784. Only 20 cycles
separate observed execution from the largest aggregate bound. A 900-cycle
capacity budget still requires removing at least 219 loads, 931 VALU slots,
and 443 ALU slots, with only 116 flow slots of headroom. These are necessary
capacity conditions and exclude startup/drain constraints.

Separate diagnostic: on the accepted kernel, allow the ordinary doubled-index
MAC to start after lookup consumption rather than waiting for the entire hash.
It is correct for three scored-shape seeds but gives 1,091 cycles. Do not apply
it without evidence of a useful combination or a better schedule.

Next implementation candidate: clean root-transition folding plus cache-16,
remove obsolete root-address setup where possible, then complete all acceptance
checks. Next structural questions: carry only the path information needed by
cached rounds, or prepare future lookup coefficients while hashing, with their
conversion and live-storage costs explicitly budgeted. Neither is implemented.

## Iteration 18 — implement root transition folding at 1,076 cycles

Parent `cea30d1`. Implemented Analysis 18's root parity representation and
early selection of the depth-2 address base. Cache coverage is 16 chunks in
round 4. The old root address constant remains in setup because its value 9
also supplies a hash multiplier. Result **1,076 cycles**, down 14; scratch
1,531. Slots: load 2,019, VALU 6,331, ALU 11,243, flow 784, store 32.
Gather first/last 78/1,059; drain 16. VALU is full for 1,048 cycles.

Full acceptance: official 9/9, built-in 3/3, frozen seeds 1000--1031, and all
six supported extra shapes with seeds 123,456,789 pass. Extra-shape cycles are
90,152,401,794,602,1626 in the previously documented order; the 20-round case
increases by one cycle. No simulator/test changes. Next: round-specific
compute offload, because prefix-wide hash offload previously delayed startup.

## Iteration 19 — select depth-3 coefficients before one MAC

Parent `06351d0`. Result **1,066 cycles**, down 10. Depth 3 now selects the
final slope and intercept first, then computes one MAC: six selects replace
five selects/two MACs. Across 64 chunk-rounds this trades +64 flow for -64
VALU. Node lifetime allocation can reuse depth-4 coefficient vectors after
their last scheduled use, without moving issue times. Scratch is 1,531.

Slots: load 2,019; VALU 6,267; ALU 11,243; flow 848; store 32. Gather first/
last 78/1,046; drain 19. VALU floor 1,045, full for 1,038 cycles.
Official 9/9, built-in 3/3, 32 frozen scored seeds and six extra shapes x three
seeds pass. Some extra shapes run more slowly; correctness remains intact.

Rejected round-specific hash XOR offload: tested 8/16/24/32 chunks on rounds
5--10, rounds 4--10 plus 15, and rounds 0,1,11,12. None improved 1,076 before
the coefficient rewrite; results ranged from 1,079 to 1,176. Next: permute
coefficient tables by absolute-address low bits to replace repeated comparisons.

## Iteration 20 — low-bit coefficient tables and hash-temporary coloring

Parent `02e563a`. Result **1,061 cycles**, scratch **1,269 words**. Permute
depth-3 pair coefficients by `(address >> 1) & 3` and depth-4 coefficients by
`(address >> 1) & 7`. The absolute address itself remains the MAC input, so
no normalization or index conversion is introduced. Depth 3 uses two masks;
depth 4 uses three shared masks, fourteen selects, and one MAC.

Fixed per-chunk hash temporaries made these candidates exceed scratch. Extend
post-schedule lifetime allocation to tmp1/tmp2 on each chunk-round, where
every value is written before use and no value crosses round boundaries.
Persistent storage is now only indices/values plus setup/cache data. This
preserves issue times and allows the new lookup to fit without serialization.

Slots: load 2,021 (+2), VALU 6,219 (-48), ALU 10,219 (-1,024), flow 896
(+48), store 32. Gather first/last 78/1,046; drain 14. Official 9/9, built-in
3/3, 32 frozen seeds and six extra shapes x three seeds pass. Compared to
iteration 19 scratch falls by 262 words.

Post-rewrite static offload sweep (hash chunks 0/4/8/12/16 and ALU index
chunks 23/32) bottoms at 1,059, but larger offloads regress despite lower
aggregate compute bounds. This motivates scheduling vector work on ALU only
when a full eight-lane issue group can fit without delaying its completion.

## Iteration 21 — adaptive vector-to-ALU issue, with 20 cached chunks

Parent `c964263`. Result **1,047 cycles**, down 14. After normal issue choices,
adaptive policies can issue a ready binary vector instruction as eight ALU
lanes if all eight fit in the same cycle. The logical instruction remains
intact for dependency tracking and lifetime allocation; scalar lanes are
materialized only afterward. The report now uses logical operation issue
times and weights scalarized vectors by eight when reporting physical slots.

The selected schedule offloads 116 vector operations. Cache coverage sweep
16/18/20/22/24/26/28/32 gives 1,058/1,050/1,047/1,058/1,073/1,090/1,119/1,175.
Choose 20. Slots: load 1,989, VALU 6,107, ALU 11,211, flow 952, store 32;
scratch 1,197. First/last gather 78/1,033; drain 13. Resource floors are
load 995, VALU 1,018, ALU 935, flow 952.

Full acceptance: official 9/9, built-in 3/3, frozen seeds 1000--1031, six
extra shapes x three seeds; simulator and tests unchanged. Adaptive selection
is data-independent and uses only DAG readiness and issue capacity. Next:
evaluate index representation changes and then retune the changed schedule.

## Iteration 22 — preserve I/O addresses and retune issue priorities

Parent `34f4c99`. Result **1,040 cycles**, scratch **1,261 words**. Keep the
32 input addresses alive until output stores instead of rebuilding them on
the flow engine. Add three policies selected by a bounded scheduling sweep.
The winning policy is `adaptive_tail_hetero_360_220_140_140_900`.

Slots: load 1,989; VALU 6,112; ALU 11,171; flow 920; store 32. First/last
gather 78/1,028; drain 11. Official 9/9, built-in 3/3, frozen seeds
1000--1031 and six extra shapes x three seeds pass; tests/simulator unchanged.
Extra-shape cycles: 92,153,396,769,600,1624. Cache coverage 18/20/22/24
gives 1,048/1,040/1,044/1,058 with the new schedule; retain 20.

Rejected preliminary negative-address representation `S=5-A`: transitions
become `S'=2*S+p`, but decoding gather addresses and new coefficient setup
constants give 1,052 versus the then-current 1,047. It passed three seeds
on four shapes in an in-memory prototype. Investigate cheaper setup before
discarding the representation. Pure scheduling cannot remove the current
995-cycle load floor; reaching 900 requires fewer loads as well as compute.

## Iteration 23 — negative indices plus shared-mask quartet interpolation

Parent `dbc6964`. Result **1,037 cycles**, scratch **1,357 words**. Use
`S=5-A (mod 2^32)` beyond the root parity representation. Since the encoded
hash parity p is inverted, `A'=2*A-5-p` becomes `S'=2*S+p`, one MAC.
For pair start A0, prepare `D=F0-F1, E=F0+(A0-5)*D`; then `F[A]=S*D+E`.
Reuse existing positive A0 setup constants instead of loading negative ones.
Gather addresses are decoded lane-wise only when needed, into the node temp.

Depth-4 lookup shares three low-bit masks and evaluates four quartets before
the final selection tree: 11 selects plus four MACs instead of 14 selects
plus one MAC. This permits 24 cached chunks. Negative indices alone give
1,041 at 20 chunks; positive-index quartets alone bottom at 1,051 in the
16/20/24/28/32 sweep. Together, coverage 20/22/23/24/25/26/28/32 gives
1,041/1,037/1,037/1,037/1,040/1,039/1,043/1,084 (20/28/32 measured before
removing two unused setup constants). Retain 24, which reduces load pressure.
Cache rotations and odd-stride permutations all regress: 1,046--1,076.

Final slots: load 1,957; VALU 6,094; ALU 11,289; flow 904; store 32.
First/last gather 79/1,015; drain 21. Selected policy:
`adaptive_tail_hetero_360_220_220_140_750`, 96 adaptive vector offloads.
Official 9/9, built-in 3/3, frozen seeds 1000--1031, and six extra shapes
x three seeds pass. Extra-shape cycles: 92,154,394,770,604,1626.
Tests and simulator unchanged. Remove obsolete ALU-index tuning option;
reject legacy positive-address lookup switches instead of silently producing
wrong results under the new representation.

Key constraint: combined compute work is `6094 + 11289/8 = 7505.125`
vector-equivalents. Even perfect allocation across 6 VALU plus 12 scalar
ALU slots needs at least `ceil(7505.125/7.5)=1001` cycles. This optimistic
bound ignores MAC restrictions and dependencies. At 900 the capacity is
6750: about 755 vector-equivalents must disappear, alongside at least
157 load slots and four flow slots. Scheduling alone cannot close this gap.

## Planning update — path-bit reuse and operation-reduction gates (2026-09-10)

Documentation only; implementation remains `d81dfe0` at **1,037 cycles**.
Recounted compute by purpose: hash/final decode 5,152; parity/index 832;
input XOR/node encoding 744; lookup MAC/masks 488; address decode 232;
setup 57.125, totaling 7,505.125 vector-equivalents.

Confirmed by exhaustive shallow-path enumeration: predicates S&2, S&4,
S&8 equal earlier encoded parity bits under S'=2*S+p. Direct reuse could
delete 2,112 scalar mask operations (264 vector-equivalents), before added
copies/storage costs. No candidate kernel or cycle improvement is claimed.
The 179 free scratch words make parity lifetime allocation a key constraint.

The active plan at the top of `ROADMAP_900.md` now orders the next work:
(A) retain/reuse parity, incrementally across depths 2, 3 and cached 4;
(B) lookup directly from path bits and delay full address construction,
counting all reconstruction costs without double-counting A;
(C) bounded, fully equivalent hash-expression search. A one-instruction
hash reduction would save 512 body operations, but no such identity is known.
Each experiment must report net work and the remaining load/flow budget,
then pass full acceptance before an implementation is committed as a gain.
No kernel, simulator or test files changed in this planning update; runtime
tests are not rerun for documentation-only changes.

## Iteration 24 — retain parity producers across shallow lookups

Parent implementation `d81dfe0`; plan `31ac567`. Result **1,017 cycles**,
scratch **1,453**. Each retained parity producer has a unique logical vector
and its lifetime includes all later lookup consumers. Root lookup reads that
vector directly; no copy is inserted. Select predicates use earlier 0/1
parity instead of extracting 0/2, 0/4 or 0/8 from S. Original dependencies
are preserved, so early preselection is not mixed into this experiment.

At the original 24 cached chunks, reuse depths 0/2/3/4 give
1,037/1,033/1,022/1,021 cycles and scratch 1,357/1,389/1,429/1,453.
Each passes frozen seeds 123/456/789. Full depth-4 reuse deletes exactly
2,112 scalar masks with no added copies or setup work: weighted work falls
from 7,505.125 to 7,241.125. This confirms plan A's gross savings in the DAG.

Cache coverage sweep 20/22/23/24/25/26/27/28 gives
1,035/1,027/1,023/1,021/1,017/1,017/1,024/1,029. Retain 26, reducing
load pressure relative to the tied 25. Final slots: load 1,941; VALU 5,971;
ALU 10,193; flow 926; store 32. Weighted compute 7,245.125; first/last
gather 74/1,001, drain 15. Selected policy
`adaptive_tail_hetero_360_220_220_140_750`, 227 adaptive vector offloads.

Official 9/9, built-in 3/3 and frozen seeds 1000--1031 pass on the final
26-chunk candidate. The six extra shapes x seeds 123/456/789 pass at
88,152,382,765,598,1622 cycles; cache coverage does not affect these shapes.
Tests/simulator unchanged. Add reproducible path-reuse/cache-coverage sweeps
to the tuning helper. Next: direct parity interpolation before experimenting
with delayed index reconstruction, keeping their costs separate.

## Iteration 25 — interpolate with parity and select coefficients early

Parent `fa086d7`. Result **1,013 cycles**, scratch **1,468**. Use
`F1+p*(F0-F1)` at depths 2/3/cached 4. Setup stores D,F1 in place and swaps
the vector references, requiring one subtraction per pair instead of four
scalar operations; no physical swap/copy is emitted. Retain the newest
parity until interpolation, in addition to the earlier lookup predicates.
Coefficient selection can start before the complete index update; the MAC
explicitly waits for its parity operand. Overwrite dependencies remain.

At 26 cached chunks, direct interpolation alone at depths 2/3/4 gives
1,017/1,018/1,018, not an improvement. With early coefficient selection,
the same settings give 1,018/1,016/1,016. Full-depth early lookup at cache
coverage 22/23/24/25/26/27 gives 1,020/scratch-overflow/1,014/1,013/1,016/1,018.
The 23-chunk candidate is rejected; retain 25. All non-overflow candidates
in these sweeps pass seeds 123/456/789. An initial copy-based coefficient
setup also regressed (1,018/1,018/1,020) and was replaced by reference swaps.

Final slots: load 1,948; VALU 5,964; ALU 10,191; flow 915; store 32.
Weighted work 7,237.875 (down 7.25: 5.25 setup plus 2 from lower cache
coverage). First/last gather 65/1,000, drain 12. Selected policy
`adaptive_tail_hetero_360_240_240_220_900`, 230 vector offloads. Official
9/9, built-in 3/3, seeds 1000--1031 and six extra shapes x three seeds pass.
Extra-shape cycles: 73,152,362,754,592,1616. Tests/simulator unchanged.

Address-update work is NOT removed in this commit. For the scored shape,
reconstructing S at the first gather at depth 4 requires the same one base
select plus three compute operations as incremental updates; depth 5 needs
one select plus four compute operations. Merely postponing those operations
does not save work. Full omission would need a traversal ending entirely in
cached lookups; the scored final depth-4 round still gathers every group.

## Hash search diagnostic — first bounded template pass

Add `hash_fusion_probe.py`, a standalone read-only candidate filter. For
stages 0/1 and stages 4/5 with raw output, test whether T(m*x+b)^c can be
rewritten as T(k*x+d), where T(x)=x^(x>>shift). Inverting T and evaluating
x=0,1 uniquely determines k,d; x=2 is a counterexample in both cases.
This rules out those particular affine-absorption templates, not arbitrary
shorter instruction sequences.

For fused stages 2/3/4, test 7,040 two-affine-arm/XOR templates using the
original, scaled and negated multipliers and a bounded derived constant set.
All are rejected against the 4,238-input deterministic test pool (evaluation
stops at the first counterexample for each candidate). No survivor or new
Hash identity is claimed. Any future survivor still requires a full 32-bit
equivalence proof and the normal kernel acceptance checks.

## Iteration 26 — reserve an ALU vector group before scalar issue

Parent `d0148c2`. Result **1,004 cycles**, scratch **1,460**, down 33 from
the 1,037-cycle starting point of this work session. After VALU issue, a
balanced policy can reserve eight ALU slots for one ready binary vector
operation before issuing scalar work. It does so only when the scalar
ready queue has at most 12 entries and cycle >=60. The existing policies
remain fallbacks; add only seven balanced tail-policy candidates. At most
one vector group is reserved per cycle, with dependencies and register
lifetimes still tracked as one logical operation until materialization.

At 25 cached chunks, backlog thresholds 4/8/10/11/12/13/14/16/24/32/64
give 1,013/1,008/1,007/1,007/1,008/1,008/1,009/1,010/1,013/1,012/1,016.
Threshold 12 with cache coverage 24/25/26/27 gives 1,012/1,008/1,005/1,013.
Threshold 10/cache 26 gives 1,006. At threshold 12/cache 26, reservation
start cycles 0/40/60/80 give 1,005/1,005/1,004/1,006. Retain 12/26/60;
the final policy is `balanced_adaptive_tail_hetero_360_220_140_140_900`.
Sweep candidates pass frozen seed 123 (the initial threshold sweep also
passes 456 and 789). Stop this bounded search here.

Final slots: load 1,940; VALU 5,883; ALU 10,855; flow 926; store 32.
315 vector operations execute on ALU. ALU full-issue cycles rise from 380
in iteration 25 to 762; this is scheduling improvement, not deleted work.
Weighted compute is 7,239.875 (+2 from returning to 26 cached chunks).
First/last gather 64/992, drain 11. Resource floors: load 970, VALU 981,
ALU 905, flow 926; optimistic combined compute floor 966.

Final official 9/9, built-in 3/3, frozen seeds 1000--1031 and six extra
shapes x seeds 123/456/789 pass. Extra-shape cycles: 73,152,358,754,592,1616.
Tests/simulator unchanged. The intermediate 1,005 candidate also passed the
same acceptance suite. Tuning helper exposes backlog/start parameters.
Remaining necessary deficits at 900: 489.875 compute equivalents, 140 load
slots and 26 flow slots, before startup/dependency/drain costs. Next work
needs a new state/expression transformation or cheaper lookup; do not resume
an unbounded priority sweep or report the target as achieved.

## Iteration 27 — final-round single-group cache and index liveness

Parent `4442b25`. Intermediate result **1,001 cycles**, scratch **1,428**;
combined with iteration 28 below in the next accepted commit. Keep depth-4
coefficients available for group 0 in the final round. Its entire second
traversal now uses direct cached lookup, so omit the base select and three
index updates, not just the final gather. Other groups retain their gathers.
The final lookup selects D/E with 14 flow operations and performs one MAC
after the newest parity arrives; the first traversal retains its 11-flow,
four-MAC quartet implementation. Reclaim each group's physical index vector
only after its actual last scheduled read/write, using the existing interval
colorer. Non-direct lookup modes still retain indices when needed.

Compared with iteration 26: eight load slots and four compute equivalents
disappear; flow increases by 13. Intermediate slots: load 1,932; VALU 5,883;
ALU 10,823; flow 939; store 32. First/last gather 64/988, drain 12. Official
9/9, built-in 3/3, frozen seeds 1000--1031 and six extra shapes x three seeds
pass at this intermediate stage.

Rejected structural experiments, kept out of the accepted kernel:

- Direct positive addresses: accumulate weighted path bits into the first
  real gather address, then prepare 2*A-5 during the hash and subtract parity
  afterward. Weighted work falls from 7,239.875 to 7,181.875, but six extra
  setup loads and changed readiness leave the best at 1,004. Cache coverage
  24/25/26/27/28/29/30 gives 1,012/1,008/1,004/1,006/1,018/1,027/1,036;
  coverage 20 exceeds scratch. Moving root base selection to MAC, using a
  single-MAC depth-4 coefficient tree, or scalarizing early address MACs
  also fails to beat 1,004. Scalar address MACs were tried both after all
  lane loads and after each lane's own load. Lower work did not imply a
  faster schedule on this graph.
- Final quartet caching of 2/4/6 groups gives 1,004/1,011/1,028. The final
  single-MAC coefficient tree gives 1,001/1,002/1,013/1,022 for 1/2/3/4
  groups at first-round coverage 26. More final caching overloads flow.
- Moving a one-group final cache from group 0 to groups 1/2/3/4/31 gives
  1,003/1,004/1,004/1,004/1,012. Increasing first-round coverage to 27/28
  with final group 0 gives 1,008/1,019. Retain first-round 26/final-round 1.
- Root-base MACs with the negative index representation and final cache
  do not improve 1,001. Reordering path bits and holding four partial
  coefficients earlier also regresses (1,005 for one final group). All six
  bit orders in the serial coefficient trees were checked: four tie 1,001,
  the other two give 1,002 and 1,006. Keep the original order; moving work
  earlier can compete with other useful flow operations.

Main prototype sweeps used frozen seeds 123/456/789; some fine-grained
positive-address coverage checks used seed 123 only. No rejected candidate
is presented as having completed full acceptance.

## Iteration 28 — remove unread setup constants after DAG construction

Final accepted result **998 cycles**, scratch **1,436**, down six cycles
from the start of this session. Inspection found 11 constant-load operations
whose destination words are never read: old threshold/mask constants with
values 4,8,12,18,20,24,26,28,32,34,36. The compiler pass does NOT hardcode
this list: it checks reads in the completed operation graph and removes only
unread, dependency-free constant loads. It does not delete memory loads,
stores, or constants whose scratch words are read after an overwrite.
Remap dependency IDs and half-open temporary lifetime ranges together.

Final slots: load 1,921; VALU 5,882; ALU 10,831; flow 939; store 32.
The change in VALU/ALU distribution is scheduling, not new arithmetic.
Weighted work remains 7,235.875. Selected policy:
`balanced_adaptive_tail_hetero_360_220_220_140_750`, 315 vector offloads.
First/last gather 59/985, drain 12. Floors: load 961, VALU 981, ALU 903,
flow 939; optimistic combined compute floor 965.

Final official 9/9, built-in 3/3, frozen seeds 1000--1031 and six extra
shapes x seeds 123/456/789 pass. Extra-shape cycles: 73,150,350,751,590,1614.
The report exposes the number of pruned constant loads; the tuner exposes
final-round cache coverage. Tests/simulator unchanged. The code still needs
at least 485.875 fewer compute equivalents, 121 fewer load slots and 39
fewer flow slots to fit the ideal 900-cycle capacities. The target is not
achieved. Add a dead-code audit after future representation rewrites, and
continue counting setup costs and readiness alongside body work.

## Iteration 29 — short input-address chains and scalar parity constants

Parent `9466e38`. Accepted result **995 cycles**, scratch **1,421**.
Instead of loading all 32 input addresses as constants, load 16 independent
even-group anchors and derive each following address with one ALU addition
of VLEN. Addresses remain live through output stores. This adds 16 scalar
operations but deletes 16 address loads; the previously dead scalar constant
8 becomes live, so the NET deletion is 15 loads. There is no runtime-input
specialization, memory rewriting, or simulator change.

All active parity operations can use the same scalar constant 1 rather than
eight broadcast copies. Use `emit_scalar_rhs` and omit that broadcast for
the direct path. Keep the vector alias in non-direct experimental modes.
This removes one setup VALU operation and seven fixed scratch words. Alone
it ties the 998-cycle parent at scratch 1,429; combined with paired anchors
it preserves the 995-cycle result and reduces scratch from 1,428 to 1,421.

Bounded startup experiments against the parent, with frozen seed 123:

| Address strategy | Group / chain sizes | Cycles |
| --- | --- | --- |
| flow add_imm chains | 2 / 4 / 8 / 16 | 1,012 / 1,014 / 1,019 / scratch overflow |
| ALU chains | 2 / 4 / 8 / 16 / 32 | 995 / 1,000 / 1,008 / 1,029 / scratch overflow |
| one-hop ALU fan from each group anchor | 2 / 4 / 8 / 16 / 32 | 995 / 999 / 1,001 / 1,004 / 1,015 |
| interleaved ALU chains, number of anchors | 2 / 4 / 8 / 16 / 32 | 1,024 / 1,017 / 1,010 / 998 / 998 |

Longer chains remove more load slots but delay input readiness; some also
increase live scratch beyond 1,536. One-hop fans require more offset
constants. Neither raw load counts nor depth alone predicts the winner.
On the paired-anchor + scalar-one graph, lowering parity as VALU gives
1,032, lowering gather-address decoding as VALU gives 1,000, and doing both
gives 1,031. Moving gathered-node encoding into scalar input XOR followed
by vector constant XOR ties 995. Reject all four alternatives. No scheduler
priority sweep was added. The tuner now exposes `--input-address-chain-length`;
1 disables chaining, 2 is the accepted default.

Accepted slots: load **1,906**, VALU **5,870**, ALU **10,935**, flow **939**,
store **32**. Weighted compute **7,236.875**, one ABOVE the parent: +16/8
from address additions, -1 from the omitted broadcast. This is a measured
resource-balance improvement, not a net arithmetic reduction. 326 vector
operations offload to ALU. Selected policy remains an existing candidate,
`balanced_adaptive_tail_hetero_360_220_140_140_900`. DCE removes 10 constants.
First/last gather **60/983**, drain **11**, gather slots **1,832**.
Floors: load 953, VALU 979, ALU 912, flow 939; optimistic combined compute
floor 965. At 900 the necessary deficits remain 486.875 compute equivalents,
106 loads and 39 flow operations, excluding dependencies and startup costs.

Acceptance: official **9/9**, built-in **3/3**, frozen seeds **1000--1031**,
and six extra shapes x seeds **123/456/789** pass. Extra-shape cycles in
the established order: **72,150,350,751,590,1614**. Alternate (PATH,DIRECT)
modes (0,0)/(2,2)/(3,3) also pass three seeds at 1,028/1,022/1,006 cycles.
Those are compatibility checks, not tuning claims; some experimental modes
regress relative to their old schedules. Tests and `problem.py` remain
unchanged relative to `origin/main`.

Public calibration: the live community boards show **869** as the current
leader, Paradigm's tenth entry is **900**, and `@zartbotF` is at **908**.
The separate VLIW With Indices leader is **899**, not directly comparable
to our no-final-index output. Record sources, timestamp, and limitations in
`LEADERBOARD_NOTES.md`. No board submission or login was performed. The
current DAG's floor is not a lower bound for all possible algorithms.

## Iteration 30 — runtime node pre-encoding, including its full cost

Parent `95ca48d`. Accepted result **994 cycles**, scratch **1,432**. The
gain is only one elapsed cycle. The structural result is a net deletion of
**83.75 vector-equivalent compute operations**, not a large speedup.

For the scored shape, copy tree nodes 15..126 (depths 4--6, 112 words) into
the first 112 words of the input-index array, XOR-encoding each vector at
runtime with the hash's final constant. The kernel already does not output
final indices. This workspace is not the forest or the input-value array;
no runtime input value is known or precomputed by the Python builder.
All computation and copying executes as ordinary simulator instructions.
Other shapes keep the previous implementation. `NODE_PREENCODE_DEPTH = 0`
turns the entire transformation off and reproduces 995 cycles.

For an original node address A=7+n and S=5-A, its copied address is
`(5 + n_nodes - 15) - S = 7 + n_nodes + (n - 15)`. Thus address rebasing
changes only the subtraction constant, not the per-gather operation count.
Each copied-level gather waits for every store in that level to commit,
because the actual selected node is runtime-dependent. Two scratch vector
buffers are reused only after their previous stores, and their storage can
join the node pool after the copies' last scheduled use.

### Measured body and setup budget

- 37 depth-4 gathers (6 first traversal, 31 final traversal), 32 depth-5
  gathers and 32 depth-6 gathers no longer execute per-lane node encoding:
  remove **808 scalar XORs = 101 vector-equivalents**.
- Add 14 vector loads, 14 vector XORs, 14 vector stores, three scalar
  constant loads, and 26 scalar pointer updates. Net compute deletion:
  `101 - 14 - 26/8 = 83.75`. Net loads **+17**, stores **+14**, flow unchanged.
- Whole-program slots: load **1,923**, VALU **5,864**, ALU **10,313**, flow
  **939**, store **46**. 346 binary vector operations offload to ALU. Weighted
  compute **7,153.125**, versus the parent's 7,236.875.
- Floors: load **962**, VALU **978**, ALU **860**, flow **939**; optimistic
  combined compute **954**. First/last gather **61/983**, drain **10**,
  gather slots **1,832**. The load count and startup offset explain why
  removing substantial scalar work translates to only one elapsed cycle.

Preprocessing alone ties 995 at depths 4--6. A metadata-only setup pass
propagates each body's first consuming round backward through the DAG and
uses that round, capped at 4, as setup priority. It does not delete or relax
dependencies. This yields 994 with the same existing policy,
`balanced_adaptive_tail_hetero_360_220_140_140_900`. The setup prefix boundary
is adjusted after constant DCE; all removed constants precede the runtime
copy sequence. DCE still removes 10 loads. No new scheduler policy is kept.

### Rejected experiments in this session

All following results are probes, not accepted alternate kernels. Initial
direct-address, logical-hazard and preencoding-range probes used seeds
123/456/789; later combination sweeps used seed 123 unless stated otherwise.

| Experiment | Configurations | Cycles |
| --- | --- | --- |
| Direct positive addresses on the new baseline | first cache 26 / 25 / 27 | 997 / 999 / 1,001 |
| Rebuild scratch hazards using logical lifetimes | cache 26 / 25 / 27 | 996 / 998 / 1,001 |
| Runtime preencoding, old setup priorities | depths 4--5 / 4--6 / 4--7 / 5--7 / 6--7 | 997 / 995 / 998 / 999 / 1,000 |
| Preencode 4--5, increase first cache | 27 / 28 | 1,007 / 1,015 |
| Preencode 4--6, increase first cache | 27 / 28 | 1,004 / 1,013 |
| Preencode 4--7, increase first cache | 27 / 28 / 29 | 1,002 / 1,013 / 1,024 |
| Static hash-arm ALU lowering, preencode 4--5 | 4 / 8 / 12 groups | 998 / 996 / 996 |
| Static hash-arm ALU lowering, preencode 4--6 | 4 / 8 / 12 groups | 999 / 999 / 996 |
| Independent flow-generated copy addresses, first-use setup | depths 4--5, 2 / 4 buffers | 996 / 996 |
| Same independent copy addresses | depths 4--6, 2 / 4 buffers | 1,006 / 1,007 |

The direct-address probe lowers compute to 7,179.875 but still loses: extra
constant loads and changed readiness matter. Logical-hazard reconstruction
removes redundant explicit edges (104,012 -> 64,269 at cache 26), but improves
neither operation count nor cycles. These are not proofs that every address
representation or dependency scheduler is exhausted.

Joint ALU/VALU assignment was also tried: reserve a ready binary vector for
ALU BEFORE choosing VALU slots, with scalar queue limits 4/12/24. On the
parent, always reserving gives 1,006/998/1,000; reserving only when VALU has
more than six candidates gives 1,006/998/999. With preencoding 4--6 these
become 1,004/997/1,001 and 1,004/997/1,001. It shifts more work to ALU but
does not shorten the load-critical execution. Combining queue limit 12 with
cache 27 gives 1,007 (always) / 1,003 (demand); cache 28 gives 1,013 in both.

Depth-4 interpolation mixes trade MACs for extra flow operations. Full
coefficient-first trees on the lowest 4/8/12 cached groups give
1,001/1,011/1,023; the highest 4/8/12 give 1,004/1,015/1,026. Using two
coefficient-first octets gives 998/1,007/1,014 (low groups) and
1,002/1,008/1,016 (high groups). Changing only the lower octet gives
995/998/1,005 and 998/1,002/1,002. All fail to beat 995; keep the quartet
lookup, not any of these mixed forms.

Setup first-use priorities alone on the parent give 999/995/995/996 for
`(round lead, cap)=(0,4)/(1,4)/(2,4)/(1,2)`. With preprocessing these give
994/998/998/994. Retain the simpler zero-lead/cap-4 rule only when
preprocessing is enabled. This is a bounded causal test, not an expanded
priority-coefficient search.

### Acceptance and reproduction

Official **9/9**, built-in **3/3**, frozen seeds **1000--1031**, six extra
shapes x **123/456/789**, and alternative (PATH,DIRECT)=(0,0)/(2,2)/(3,3)
x three seeds pass. Extra-shape cycles remain **72,150,350,751,590,1614**;
alternate modes give **1,021/1,015/1,005**. Four full-32-bit boundary-pattern
fixtures also pass, checking exact workspace encodings, final values, and
unchanged forest/header/index-tail contents. The tuning helper now checks
untouched memory outside the declared workspace on every candidate.

Reproduce the accepted A/B with:

```sh
python3 tune_kernel.py --node-preencode-depth 0 6 --seeds 123 456 789
python3 tests/submission_tests.py
python3 analyze_kernel.py
```

The report exposes preencoded node count. Official tests and `problem.py`
remain unchanged. Necessary 900-cycle deficits now are 403.125 compute
equivalents, 123 loads, and 39 flow operations. The load deficit is WORSE
than the parent despite the arithmetic reduction. No leaderboard upload or
new leaderboard research was performed during this iteration.

## Iteration 31 — consumer-aware input readiness and fragmented scalar issue

Date: 2026-09-13. Parent `0c89047`. Accepted **988 cycles**, scratch
**1,440**, down six cycles (about 0.60%). Official tests and `problem.py`
are unchanged. Neither accepted change deletes arithmetic or memory work.

### Two independent limitations, measured together

1. Input-address initialization retained the dummy scheduling cohort -1,
   although each anchor feeds a specific group's root-round load. Attribute
   the address to that group and round 0. All data and hazard dependencies
   remain intact. Root inputs can overlap setup sooner: the first gather
   moves from cycle **61 to 55**. This change alone gives **990** cycles.
   Attributing the same addresses to round -1 instead gives 992.
2. Adaptive ALU offload previously needed eight free scalar slots in ONE
   cycle. Add a bounded family of fragment-enabled versions of the existing
   tail policies. Issue as many lanes as fit, keep at most one unfinished
   vector, and release its logical consumers only after the final lane.
   A partial vector does not preempt ready scalar instructions. Original
   whole-vector policies remain fallbacks. Fragments alone give **993**;
   combined with the round-0 input metadata they give **988**. Round--1
   input metadata plus fragments gives 991.

Both switches apply only to the scored (height,batch,rounds)=(10,256,16)
shape. Other shapes retain their prior candidate family and input metadata.

| Input consumer priority | Fragment issue | Cycles | Scratch |
| --- | --- | ---: | ---: |
| Off | Off | 994 | 1,432 |
| Off | On | 993 | 1,440 |
| On | Off | 990 | 1,408 |
| On | On | **988** | **1,440** |

Accepted policy: `fragment_adaptive_tail_hetero_360_220_140_140_900`.
Slots: load **1,923**, VALU **5,842**, ALU **10,489**, flow **939**, store
**46**. Relative to the parent, 22 additional vector operations move to ALU
(VALU -22, ALU +176). Total offloaded vector operations **368**, of which
**224** span multiple cycles. The longest spans **27 cycles**; treating it
as a single final-cycle register use would be incorrect. Allocation now
protects every logical vector from first touch through final completion.

The builder verifies that every offloaded lane issues exactly once, that
consumers begin strictly after their producers finish, and that all final
bundles satisfy engine capacities. `analyze_kernel.py` now uses actual
per-lane issue times for ALU counts, waits and per-round timelines, rather
than attributing all eight lanes to the vector's last issue cycle.

Weighted compute remains **7,153.125**; its optimistic floor remains 954.
Per-engine floors: load **962**, VALU **974**, ALU **875**, flow **939**.
First/last gather **55/977**, drain **10**, gather slots **1,832**. At 900,
the necessary deficits still are **403.125 compute equivalents, 123 loads,
39 flow operations**, before startup/dependencies. This win reduces exposed
startup and improves placement; it is not a new route around those budgets.

### Bounded rejected experiments

These probes preceded the final input-priority combination unless stated
otherwise. Lookup-balance probes used seed 123; the remaining groups used
123/456/789. A scratch overflow is a rejection, not a speed result.

| Experiment | Configurations | Cycles |
| --- | --- | --- |
| Replace depth-2 pair selection with two MACs and one select | Lowest 8 / 16 / 32 groups, first cache 26 | 994 / 995 / 1,000 |
| Analogous depth-3 two-MAC form | Lowest 8 / 16 / 32 groups | 999 / 996 / scratch overflow |
| Extra first cache with arithmetic lookup | d2=16, cache27 / d3=16, cache27 / d3=32, cache28 / d2=d3=16, cache28 | 996 / 997 / overflow / 1,002 |
| Remove redundant direct-lookup index barrier | Default cache | 994 |
| Direct positive addresses plus preencoding | Depth6, first cache 26 / 25 / 27 | 995 / 999 / 998 |
| Same direct addresses, other copied ranges | Depth5 / depth7, cache26 | 995 / 1,001 |
| Reuse depth-4 cache loads for workspace copy | Scalar setup encoding, depths6 / 5 / 7 | 996 / 997 / 998 |
| Same shared copy, vector setup encoding | Depth6 / 5 | 996 / 996 |
| Require at least four free lanes to start a fragment | Do not preempt / finish partial first | 995 / 996 |
| Always complete a partial before scalar issue | One-lane minimum | 995 |
| Allocate fragment lanes before VALU selection | Always / only VALU queue >6 | 1,002 / 1,004 |
| Same early allocation, cache27 | VALU queue >6 | 1,008 |
| Fragments with more first-round caching | Cache27 / 28 | 1,000 / 1,011 |
| Fragments plus direct addresses/preencoding | Cache26 / 27 / 28 | 996 / 999 / 1,009 |
| Fragments plus shared cache/workspace copies | Cache26 / 27 | 993 / 1,000 |
| Fragments plus d2 arithmetic on 8 groups | Cache27 | 995 |
| Shift equal gather savings to final traversal | First/final cache 25/2, 24/3, 23/4 | 1,005 / 1,022 / 1,017 |
| Fragments with one extra final cached group | First/final 26/2 | 997 |

Direct addresses lower compute to 7,098.125 at depth6/cache26, but add six
loads and move the first gather to 64: they still lose. Sharing depth-4
setup loads removes two loads and 2.25 compute equivalents, but extends
setup dependencies and does not improve elapsed time. Arithmetic lookup
frees flow at the cost of compute and sometimes excessive live storage.
Aggressive pre-VALU fragment allocation offloads 530 vectors, but raises
ALU demand to 11,785 slots and loses at 1,002 cycles. These are concrete
counterexamples to optimizing only aggregate counts or offload volume.

### Acceptance and reproduction

Official **9/9**, built-in **3/3**, frozen seeds **1000--1031**, six extra
shapes x **123/456/789**, and alternative (PATH,DIRECT)=(0,0)/(2,2)/(3,3)
x three seeds pass. Extra-shape cycles stay **72,150,350,751,590,1614**;
alternate modes give **1,011/1,002/994**. They are compatibility checks.
Preencoding depths 0/4/5/7 also pass three seeds with both new switches
enabled, at **995/989/988/994**. Depth 5 ties the default's elapsed cycles
with eight fewer loads but 22 more compute equivalents; retain depth 6
because neither setting dominates both budgets.

Add permanent local `verify_kernel.py`: reconstruct every emitted primitive
slot from the scheduled DAG and actual lane times, compare exact multisets
after register renaming, check producer/consumer timing and capacities, run
32 reference seeds, and check eight full-32-bit fixtures (four bit patterns,
four random seeds). Fixtures verify output values, exact workspace contents,
and unchanged header/forest/index tail. This is separate from official tests.

```sh
python3 tune_kernel.py --input-address-consumer-priority 0 1 --alu-fragment-issue 0 1 --seeds 123 456 789
python3 tests/submission_tests.py
python3 perf_takehome.py
python3 verify_kernel.py
python3 analyze_kernel.py
```

The first command reproduces the full 994/993/990/988 causal matrix. With
both switches off, `NODE_PREENCODE_DEPTH=0` still restores the earlier
995-cycle graph. Do not interpret disabling preencoding alone, while these
new switches remain enabled, as an exact historical-baseline reproduction.
No leaderboard research, submission, simulator change, or runtime input
precomputation was performed in this iteration.

## Iteration 32 — direct addresses with derived constants and smaller preprocessing

Date: 2026-09-13. Parent `3f2f4e2`. Accepted **987 cycles**, scratch
**1,473**. This improves elapsed cycles by only one (about 0.10%), but also
removes **10 load slots**, **30.625 vector-equivalent compute operations**,
and **8 stores**. No simulator or official test changes.

### Why the previously rejected address representation now wins

Let p be encoded-hash parity. For an ordinary tree address A, the next
address is `A'=2*A-5-p`. Construct the first gather address directly from
retained path bits: at depth g its all-zero-parity base is `2^(g+1)+5`;
subtract `p0*2^(g-1)`, `p1*2^(g-2)`, ..., `p(g-1)`. The first base uses the
existing root-parity select; subsequent weighted terms use MACs and the
last term uses per-lane subtraction. Cached node lookup still uses retained
parities, not the address bits of this ahead-of-time base.

After a gather, prepare `2*A+bias` while hashing. This overwrite waits for
ALL eight loads to read their addresses. Subtract each parity lane afterward,
and let its next load depend on that lane's address. Final/leaf rounds omit
unused address updates, as before.

For the copied workspace offset d=`n_nodes-15`:

- Within the copied range: `a'=2*a-5-d-p`.
- Leaving it: `A'=2*a-5-2*d-p`.
- Outside it: `A'=2*A-5-p`.

Memory dependencies on every store in the selected copied level remain
explicit. Only scored shapes with direct lookup through depth 4 use this
representation. Generic shapes and alternate lookup modes retain S=5-A.

The old direct-address probe loaded almost every base/weight separately.
Instead, derive -2 from an untouched zero word and the existing scalar 2;
double it to -4/-8; derive -5 from zero and the scalar 5. Only the depth-4
right base needs a new immediate load. Build its left base with -8, build
32 from 8<<2, derive the copied bias as `32-right4`, and derive the exit
bias as `2*copied_bias+5`. Both depth-5 bases follow by MAC from depth 4.
All these operations execute at runtime using ordinary simulator slots.
The depth-4-only option uses the EXIT bias for its depth-5 bases and is
separately checked; it must not point into uncopied workspace.

With copied depths 4--6, this version reaches 989 at first cache 26 and
988 at cache 27. Reducing the copy to depths **4--5** gives **987** at cache
26; cache 27 regresses to 990. Retain the original first/final cache counts
26/1, not a wider cache or any new scheduling policy.

### Full accounting, including setup

- Direct construction deletes one compute equivalent at each first gather:
  32 groups in the first traversal and 31 in the second, **63** total.
- New address setup replaces two old broadcast bases with 12 vector
  operations and three scalar operations: net **+10.375** equivalents.
- Dropping depth-6 preprocessing removes eight copy XORs and 16 scalar
  pointer updates, but restores 32 gathered-node encodings: net **+22**.
- Net: `-63 + 10.375 + 22 = -30.625` compute equivalents. Derived address
  setup saves two net loads; the smaller copy saves eight more.

Accepted slots: load **1,913**, VALU **5,814**, ALU **10,468**, flow **939**,
store **38**. Weighted compute **7,122.5** (optimistic floor **950**), versus
7,153.125 (floor 954). Other floors: load **957**, VALU **969**, ALU **873**,
flow **939**. First/last gather **54/976**, drain **10**, gathers **1,832**.
Necessary deficits at 900: **372.5 compute equivalents, 113 loads, 39 flow**.
The aggregate improvements must not be presented as a similar time speedup.

Selected existing policy:
`fragment_balanced_adaptive_tail_hetero_360_220_220_140_750`.
398 vector operations offload to ALU; 56 span multiple cycles, max span 25.
DCE removes 11 constants, including the now-unused gather-decode constant.
Address constants join the existing lifetime reuse pool after their last
scheduled reads; scratch falls **1,497 -> 1,473** at unchanged 987 cycles.
This is still 33 more scratch words than the parent. Workspace shrinks from
112 to **48** index words; the other 208 index words and entire forest remain
untouched. No input-dependent work is performed in the Python builder.

### Rejected probes in this turn

All probes below checked frozen seeds 123/456/789. A tie is not counted as
a performance improvement. Earlier address tests were rerun because input
readiness and fragment issue changed the graph in iteration 31.

| Experiment | Configurations | Cycles |
| --- | --- | --- |
| Reuse cache depth-4 loads for preprocessing | Scalar encoding, copy depths6 / 5 / 7 | 988 / 989 / 995 |
| Same shared copy, vector encoding | Depths6 / 5 | 991 / 990 |
| Full direct addresses, separately loaded constants | Depth6, cache26 / 25 / 27 | 990 / 992 / 989 |
| Same separate constants | Depth5 / 7, cache26 | 989 / 995 |
| Tail-only direct addresses | Derived / immediate constants, cache26 / 27 | 990 / 995 for both |
| Tail-only base MAC, derived constants | Cache26 / 27 | 990 / 989 |
| Tail base MAC, remove unused left base, more first cache | Depth6, cache28 / 29; depth5, cache28 / 29 | 990 / 997; 990 / 998 |
| Replace setup loads by one exact ALU expression | Maximum synthesis depth1 / 2 / 3 | 990 / 991 / 990 |
| Extra final caches on highest groups, retaining group0 | First26, final2 / 3 / 4 | 996 / 1,009 / 1,022 |
| Same high-group final caches | First24, final3 / 4 | 988 / 1,000 |
| Spread final caches across middle/high groups | First26, final3 / 4 | 1,006 / 1,012 |
| Setup first-use priority cap | 5 / 6 | 990 / 992 |
| Address anchors inherit their highest consumer group | Existing cap4 | 990 |
| Scalarize index FMAs in rounds13/14 | Lowest8 / 16 / 32 groups | 988 / 989 / 988 |
| Scalarize other index FMAs | Round9 all32; rounds2/3/13/14 lowest8; rounds4--9 lowest8 | 988 / 988 / 988 |
| Release proven parity RAW edges per completed vector lane | Parent; full direct cache26 / 27; tail-direct; scalar-tail | 988; 990 / 989; 990; 988 |
| Longer input address chains on parent | 3 / 4 / 6 / 8 | 990 / 992 / 990 / 995 |
| Shared copies on new derived-address/depth5 candidate | Scalar / vector encoding | 988 / 988 |
| Longer input chain on new candidate | Chain3 | 988 |
| Derived direct addresses with copy depth4 only | Correct exit to original depth5 addresses | 988 |

Tail-only addresses remove 27 net compute equivalents but still take 990.
The standalone constant synthesizer removes 7--9 loads but increases setup
dependencies and does not win. Scalarizing index FMAs mostly displaces
existing binary-operation offload. Per-lane parity release changes 3,584
eligible edges and can reduce scratch, but does not improve cycles; do not
relax the accepted kernel's whole-vector completion barriers on this basis.
These probes are kept out of the implementation.

### Acceptance and reproduction

Official **9/9**, built-in **3/3**, frozen seeds **1000--1031**, eight full-word
fixtures, exact primitive-emission reconstruction, and memory-boundary checks
pass. Six extra shapes x seeds123/456/789 remain at **72,150,350,751,590,1614**.
Alternate (PATH,DIRECT)=(0,0)/(2,2)/(3,3) x three seeds pass at
**1,013/1,003/994** with the new depth-5 preprocessing default.

Direct-address A/B across preencoding depths 0/4/5/6/7 passes three seeds.
With direct addresses OFF: **995/989/988/988/994**. With them ON:
**994/988/987/989/994**. Only the 987 default is the accepted performance
claim; the others are configuration checks. Lifetime reuse is checked again
after enabling the address constants in the reuse pool.

```sh
python3 tune_kernel.py --node-preencode-depth 5 6 --direct-gather-addresses 0 1 --seeds 123 456 789
python3 tests/submission_tests.py
python3 perf_takehome.py
python3 verify_kernel.py
python3 analyze_kernel.py
```

The explicit parent reproduction is `--direct-gather-addresses 0
--node-preencode-depth 6` with the other defaults. No leaderboard query or
submission was performed. The next target remains structural body reduction
with setup, flow and live-storage costs included, not another unconstrained
priority sweep.

## Iteration 33 — parent/children records and copy-consumer fusion

Date: 2026-09-13. Parent `fa65372`. Accepted **970 cycles**, scratch
**1,475 / 1,536**, versus 987 / 1,473. Intermediate verified checkpoints:
**979 -> 978 -> 977 -> 970**. No changes to the simulator or official tests.

### Insight: spend one load on useful work in two rounds

The previous depth-4 cache removed loads at the price of four MACs and
eleven selects per cached group. Instead, preprocess the 16 depth-4 parents
and 32 depth-5 children into runtime records:

`[parent ^ C, left_child ^ C, right_child ^ C, 0]`, C=`0xB55A4F09`.

Two records fit in one eight-word vstore. Preparation uses six vloads and
six vector XORs, 48 scalar field copies, eight stores, and explicit address
setup/update operations. ALL their emitted costs are included in the result.
The 64-word record array occupies only the unused input-index workspace.
Nothing is computed from runtime tree/input values in the Python builder.

Each item does one vload at its parent record. Only the first three words
are consumed; the final record's eight-word read extends into untouched
index workspace but never into input values. Four independent virtual read
buffers per vector group prevent the eight loads from forming a single
load-copy-load chain. Each bank's next write waits for all three consumers
of its old contents. Children remain live across the parent hash, and its
retained parity selects the next node with one vselect and NO depth-5 load.
Read-buffer and child-vector lifetimes are colored by actual issue times.

The first version copied all three fields, then XORed the parent vector
into the input. Fuse the parent copy with that XOR: each parent buffer word
is XORed directly into its input lane. Hash consumers wait for all lanes
and all pending buffer reads. This deletes one vector operation per group,
**32 compute equivalents**, and improves **977 -> 970** at identical
load/flow/store counts. Two child copies per item remain; do not describe
the implementation as transpose-free.

### Address and lookup co-design

Let A4 be the ordinary forest address, W the index workspace base and p4/p5
the encoded parities. A record address is `B=4*A4+W-88`. The depth-6 forest
address is `A6=B+(73-W)-2*p4-p5`. Construct B from weighted earlier path bits;
prepare its exit bias after its loads read the address; incorporate p4 by
MAC and p5 by subtraction. This avoids a full independent depth-5 address.
All arithmetic is modulo 2^32. The child record slot is left when p4=1 and
right when p4=0, because the encoded hash parity inverts the raw parity.

Removing the first traversal's large depth-4 cache frees flow capacity.
Spend part of it to replace the depth-2/3 interpolation MACs with pure
selection: **128 body MACs disappear**, at **128 extra selects**, plus six
scalar pair-difference operations disappear from setup. Order each select
tree by the EARLIEST available path bit first; only its last select waits
for the newest bit. Keeping the opposite order increases the critical path.

Finally cache depth 4 for the **highest seven groups (25--31)** in round 15.
Their earlier cohort progress permits coefficient selection to overlap
remaining deep gathers. Applying this cache to the lowest groups created
a long tail and lost badly. The highest-group experiment that failed on the
987 graph is not a contradiction: the record layout and pure selects have
changed both the operation and flow budgets.

### Full accounting and bounds

| Metric | Parent | Accepted | Delta |
| --- | ---: | ---: | ---: |
| Cycles | 987 | 970 | -17 |
| Weighted compute (VALU + ALU/8) | 7,122.5 | 6,938 | -184.5 |
| Load slots | 1,913 | 1,827 | -86 |
| VALU slots | 5,814 | 5,638 | -176 |
| ALU slots | 10,468 | 10,400 | -68 |
| Flow slots | 939 | 891 | -48 |
| Store slots | 38 | 40 | +2 |
| Scratch words | 1,473 | 1,475 | +2 |

The individual ALU/VALU counts include adaptive offloading; their weighted
sum is the stable compute comparison. Combined compute floor falls
**950 -> 926**. Accepted per-engine floors: load 914, VALU 940, ALU 867,
flow 891. Necessary aggregate reductions for 900 are still **188 compute
equivalents and 27 loads**. Flow has only nine slots of aggregate margin.
The ten-operation hash is unchanged; no shorter identity was found or used.

The new schedule has 256 record vloads and 1,480 scalar gathers: **1,736
lookup-load slots**, first/last **68/959**, drain 10. A scalar-gather-only
report would misleadingly show first=95 and omit all 256 record loads.
`analyze_kernel.py` now reports the combined metric separately. Its
fixed-first-time conditional finish bound is 936, so removing only the
27 aggregate excess loads is not a sufficient plan for 900.

Chosen EXISTING policy: `fragment_adaptive_tail_hetero_360_220_140_140_900`.
There are 358 offloaded vectors, 240 fragmented vectors, max span 14, and
11 pruned constants. Reclaim new address constants after their last use;
at the pre-fusion 977 checkpoint this lowered scratch 1,491 -> 1,483
without changing cycles. No new scheduler priority family was introduced.

### Bounded experiments and rejected alternatives

Every measured candidate below passed frozen seeds 123/456/789. Early
prototypes were isolated from the accepted implementation; they are not
claimed as speedups. Constants and scratch coloring differ between rows
where explicitly noted, so use the final CLI matrix for exact reproduction.

| Experiment | Configuration | Cycles |
| --- | --- | ---: |
| First blocked layout, one serial read buffer | Keep interpolation, no final cache | 1,000 |
| Same serial prototype | Pure depth-2 / depth-2+3 lookup | 1,013 / 1,016 |
| Direct parent landing buffers, lean setup | Four shared banks, pure lookup, no final cache | 1,016 |
| Same, earlier path bits first | No final cache | 1,011 |
| Wrong final-cache placement | Lowest eight groups, four parent banks, early bits | 1,075 |
| Parallel read buffers, early bits | Four buffers, no final cache | 1,000 |
| Parallel read buffers, high final groups | Four buffers, eight final groups | 979 |
| Same high final cache | Six / seven / nine / ten groups | 978 / 977 / 985 / 989 |
| Direct parent landing versus copied-parent buffers | Four shared parent banks, eight high final groups | 982 |
| Read buffer count before fusion | Two / four / eight, seven final groups | 979 / 977 / 977 |
| Static hash-arm ALU offload before fusion | 4 / 8 / 12 / 16 groups | 978 / 973 / 975 / 978 |
| Parent-copy/XOR fusion | Six / seven / eight final groups, four read buffers | 974 / 970 / 979 |
| Fusion plus static hash offload | Eight hash groups; six / seven / eight final groups | 976 / 972 / 975 |
| Read buffer count after fusion | Two / four / eight, seven final groups | 977 / 970 / 974 |

Static hash offload at eight groups helped the intermediate version but
regressed the fused version. Keep `HASH_ALU_CHUNKS=0`; do not add the two
improvements as if their time gains were independent. The lowest-final-cache
and serialized-buffer regressions demonstrate why operation counts alone
were not enough.

### Acceptance and reproduction

The new path is enabled only for the scored shape (height10, batch256,
rounds16), with direct retained lookup through depth4. Its controls are
`BLOCKED_LOOKUP=True`, `BLOCKED_READ_BANKS=4`,
`BLOCKED_FINAL_CACHE_CHUNKS=7`, `BLOCKED_FUSE_PARENT_XOR=True`.
The old depth-4 coverage and linear-preencoding settings apply to the
fallback; they do not expand the blocked workspace. Generic and alternate
path configurations retain their earlier behavior.

Official **9/9**, built-in **3/3**, 32 frozen-reference seeds, eight full-word
fixtures, exact emitted-instruction reconstruction, and exact record/padding
and memory-boundary checks pass. The expanded local verifier also checks six
extra shapes x three seeds (72/150/350/751/590/1614 cycles) and alternate
path depths0/2/3 x three seeds (1013/1003/994). A first generic check caught
an overly broad new cache-count assertion; it was scoped to blocked shapes
and the extra-shape suite rerun. No official test was edited.

```sh
# Exact parent and final implementation: 987 / 970.
python3 tune_kernel.py --blocked-lookup 0 1 --seeds 123 456 789
# Isolate copy-consumer fusion on the new graph: 977 / 970.
python3 tune_kernel.py --blocked-fuse-parent-xor 0 1 --seeds 123 456 789
# Bounded accepted-neighborhood checks.
python3 tune_kernel.py --blocked-final-cache-chunks 6 7 8 --seeds 123 456 789
python3 tune_kernel.py --blocked-read-banks 2 4 8 --seeds 123 456 789
python3 tests/submission_tests.py
python3 perf_takehome.py
python3 verify_kernel.py --extra-shapes
python3 analyze_kernel.py
```

Next target the remaining two child-copy vectors per group, or another
joint layout/lookup reduction, with full setup and storage accounting.
Seventy elapsed cycles still separate this implementation from 900. No
leaderboard lookup, submission, hash shortcut, or test/simulator change.

## Iteration 34 — setup copy fusion; reject serialized child landing

Date: 2026-09-13. Parent `1d27efc`. Accepted **969 cycles**, scratch **1,475**.
The large body experiments below did not win. The accepted change is a
small, exact reduction of initialization work; do not present it as a major
breakthrough toward 900.

### Accepted: perform encoding during the required transpose

The 970 implementation loads six vectors of raw parent/child data, XORs
each vector with C, then copies the 48 fields into record order with scalar
add-zero instructions. Every source field is transposed exactly once.
Change those 48 copies to scalar XOR-C instructions and delete the six
separate vector XORs. Record contents, padding, workspace boundaries, body
instructions, and hashing are unchanged. Setup hazard inference provides
the source-buffer and constant dependencies as before.

The A/B is **970 -> 969**. Accepted slots: load1,827, VALU5,631, ALU10,408,
flow891, store40. Compared with the parent this is -7 VALU and +8 ALU slots:
one more surviving binary vector is adaptively offloaded. The stable net
work saving is **six vector equivalents**, 6,938 -> **6,932**. Scratch and
load/flow/store counts are unchanged. The optimistic combined compute
floor falls **926 -> 925**, and necessary 900-cycle deficits become
**182 compute equivalents, 27 loads**; flow fits by only nine slots.

Selected policy remains `fragment_adaptive_tail_hetero_360_220_140_140_900`.
There are 359 offloaded vectors, 240 fragmented vectors, maximum span14,
and 11 pruned constants. Combined lookup traffic is unchanged at1,736
slots; first/last moves **68/959 -> 67/958**, drain10. At the observed first
time, the conditional finish bound is935. No new body algorithm or shorter
hash was accepted this iteration.

### Experiment A: let the left child land directly in scratch

Reorder each runtime record to `[left_child, parent, right_child, padding]`.
Load lane i into offset i of a contiguous 16-word scratch span. In ascending
lane order, later vloads preserve the already-written left children. XOR
the parent directly into the input and copy only the right child. The final
traversal's addresses need +1 because it reads the parent, not the left
child. Two new vector bases account for two extra loads and two compute
equivalents. Net reduction: **32 - 2 = 30 compute equivalents**.

This requires a probe-only allocator supporting atomic, contiguous two-vector
intervals. Ordinary independent eight-word coloring would be incorrect:
the unaligned vload writes across the boundary. Protect the whole interval
through the depth-5 select. Consumers of each old buffer must finish before
it is overwritten. These dependencies serialize the loads and the default
prototype takes **981**, despite work falling to6,908.

The simulator reads beginning-of-cycle scratch and commits writes at cycle
end. A narrower second probe lets old-buffer ALU readers issue in the SAME
cycle as the next vload. It retains a positive dependency between successive
overlapping vloads and reserves all required ALU readers before permitting
an overwrite. Same-cycle edges are remapped during DCE and checked against
the final schedule; strict RAW dependencies remain unchanged. This improves
981 -> **974**, not below970. Applying that mechanism to the unchanged old
four-bank record layout yields **972**, so it is also rejected independently.
Neither this scheduler nor its allocator is in `perf_takehome.py`.

| Child-landing variant | Final cache | Cycles | Scratch | Weighted compute |
| --- | ---: | ---: | ---: | ---: |
| Strict reader-before-overwrite | 6 | 985 | 1,443 | 6,910 |
| Strict reader-before-overwrite | 7 | 981 | 1,451 | 6,908 |
| Strict reader-before-overwrite | 8 | 982 | 1,451 | 6,906 |
| Same-cycle WAR handling | 6 | 977 | 1,475 | 6,910 |
| Same-cycle WAR handling | 7 | 974 | 1,467 | 6,908 |
| Same-cycle WAR handling | 8 | 978 | 1,467 | 6,906 |
| Same-cycle WAR + 8 hash-ALU groups | 7 | 976 | 1,499 | 6,908 |
| Same-cycle mechanism, original layout, 2 buffers | 7 | 975 | 1,483 | 6,938 |
| Same-cycle mechanism, original layout, 4 buffers | 7 | 972 | 1,443 | 6,938 |

These valid prototypes check three frozen seeds and exact primitive emission.
They do not have the production version's full acceptance claim. Their code
is preserved in `experiments/iteration34_child_landing.py`, pinned to970.

### Experiment B: move the record pair deeper

Prepare 32 parent/children records for depths5/6 in128 workspace words,
plus a separate16-word encoded depth-4 copy. This frees deeper gather work,
but uses14 setup vloads,18 stores, larger pointer/bias setup and additional
live cached values. Address transitions into records and back to depth7
are explicit. No runtime data is precomputed by Python.

| First/final depth-4 cache groups | Result |
| --- | --- |
| 0 / 0 | 998 cycles, scratch1,334; load1,901, weighted compute6,920.125, flow800 |
| 0 / 7 | 979 cycles, scratch1,452; load1,847, weighted compute6,925.125, flow891 |
| 8 / 0 | Rejected: requires1,548 scratch words, limit1,536 |
| 8 / 7 | Rejected: requires1,652 scratch words, limit1,536 |

The two feasible versions pass three frozen seeds and primitive-emission
checks. Overflow cases have NO valid performance result; the scratch limit
was not increased. See `experiments/iteration34_deeper_block.py`.

### Other bounded setup probes

- Fuse the raw-root copy with encoding too: weighted work6,931.875, but
  **970 cycles**, so keep the old root setup.
- Share depth-4 cache loads with record preparation: populate parents in
  eight persistent record buffers before transforming cache coefficients,
  then fill children and store. Saves two extra load slots with unchanged
  work6,932, but takes **973**, scratch1,467. Not accepted.
- Accepted setup fusion with six/seven/eight final cached groups gives
  **973 / 969 / 974**; keep seven.

### Acceptance and reproduction

Official **9/9**, built-in **3/3**, frozen seeds1000--1031, eight full-word
fixtures, primitive-emission reconstruction, exact records/padding and
memory boundaries pass. The six extra shapes and three alternate path-depth
configurations each pass seeds123/456/789 at their unchanged cycle counts.
The new setup flag applies only when blocked lookup is active; fallback
paths and official tests/simulator are unchanged.

```sh
python3 tune_kernel.py --blocked-fuse-setup-xor 0 1 --seeds 123 456 789
python3 tests/submission_tests.py
python3 perf_takehome.py
python3 verify_kernel.py --extra-shapes
python3 experiments/iteration34_child_landing.py --zero-war
python3 experiments/iteration34_deeper_block.py --first-cache 0 --final-cache 7
```

Next require a non-serial child-consumer layout or another transformation
that reduces BOTH work and the live/readiness cost. Repeating the same
direct-child or larger-depth cache variants is not a justified plan. There
are still69 elapsed cycles to900; this iteration establishes no complete
route. No leaderboard lookup or external benchmark submission was performed.

## Iteration 35 — restore startup readiness, then reorder the tail

Date: 2026-09-13 (PDT). Parent `468f713`. Accepted **955 cycles**, scratch
**1,459/1,536**. Versus969: **-14 cycles (-1.44%)**, -16 scratch words,
but only **one** compute equivalent and one load removed. Most of the gain
comes from readiness, not less body work.

### Insight 1: initialization inherits a use deadline, not blanket urgency

The non-blocked preencoding path already called
`prioritize_setup_by_first_use`. The blocked path set `preencode=False`,
so it skipped that call and left all setup at round-1/cohort32. Even the
coefficients used only in the final round competed with early work. Reverse
propagation through the existing DAG supplies the first consuming round;
cap its scheduling priority at round4. No operations or dependencies change
in this probe. **969 -> 960**, and first combined lookup **67 -> 54**.

Use an explicit setup-prefix boundary, adjusted for pruned constants. Keep
an immutable `is_setup` tag for analysis rather than treating the scheduling
round as semantic identity. Otherwise setup vloads retagged to positive
rounds would be incorrectly counted as node lookups. `verify_analysis`
checks pre-round slot accounting (setup plus initial input loads) and
combined lookup count independently of priority. The old fallback setup
semantics are tagged too; its schedule is unchanged.

### Insight 2: early bits help only when the graph can exploit them

The final cached depth-4 lookup has14 selects and one MAC. The old coefficient
tree began with p2, which arrives after p0/p1. Reverse its three-bit table
coordinate and build four quartets using p0 then p1; select their slope and
intercept using p2, then do the existing MAC when p3 arrives. Temporary
quartets use the already allocated virtual-node region and are lifetime
colored normally. No extra select, MAC or relaxed dependency is introduced.

On the old initialization schedule this ties969; with setup deadlines it
improves960 ->959. This is not a generally faster tree independent of context.

### Insight 3: remove a vestigial weight, then measure the whole schedule

The blocked address accumulation always first gathers at depth4 (or skips
the final gather). Depth1 uses -16, depth2 uses -8, and the dedicated depth3
step uses -4. The generated -32 scalar load and broadcast have no body
consumer. Conservative constant DCE cannot delete the scalar because its
own unused broadcast reads it. Omit this unused pair specifically on the
blocked path; do not broaden DCE or remove fallback weights.

This deletes one load and one VALU equivalent. Alone it regresses969 ->971;
with setup deadlines it gives959; with deadlines AND the early tree it
gives **955**. Less aggregate work does not imply fewer elapsed cycles.

### Complete causal A/B

All rows use the same four record read banks, seven final cached groups,
fused setup/parent XORs and unchanged scheduler-policy candidates. Every
row passes seeds123/456/789. Flags are independently reversible.

| Setup deadlines | Early tail tree | Drop unused weight | Cycles | Scratch | Weighted compute |
| --- | --- | --- | ---: | ---: | ---: |
| off | off | off | 969 | 1,475 | 6,932 |
| off | off | on | 971 | 1,451 | 6,931 |
| off | on | off | 969 | 1,435 | 6,932 |
| off | on | on | 975 | 1,427 | 6,931 |
| on | off | off | 960 | 1,499 | 6,932 |
| on | off | on | 959 | 1,475 | 6,931 |
| on | on | off | 959 | 1,499 | 6,932 |
| on | on | on | **955** | **1,459** | **6,931** |

Accepted slots: load1,826, VALU5,604, ALU10,616, flow891, store40. Relative
to969 this is -27 VALU/+208 ALU (-1 net equivalent), -1 load; the selected
policy is still `fragment_adaptive_tail_hetero_360_220_140_140_900`.
Offloaded385, fragmented253, maximum fragment span10; conservative DCE
still prunes11 constants, in addition to the omitted weight at generation.
Weighted compute6,931 leaves optimistic floor925 and necessary900 deficits
of181 compute equivalents and26 loads. Flow has only nine spare slots.

Body lookup count is unchanged:256 record vloads +1,480 narrow gathers.
Combined first/last **54/944**, drain10, conditional finish bound922.
Narrow gathers alone start80; they must not stand in for all lookup traffic.
The runtime records still contain48 encoded nodes and16 padding zeros in64
index words; remaining192 index words, forest and header remain untouched.

### Bounded rejected/tied probes

These are pinned to969 in `experiments/iteration35_setup_deadlines.py`.
Feasible rows pass three seeds and exact emission; scratch overflows have
no valid cycle result and never use a relaxed simulator limit.

| Variant | Cycles | Scratch |
| --- | ---: | ---: |
| Deadlines only, cap3/4/5 | 960 | 1,499 |
| Deadlines only, inherit highest consuming cohort | 960 | 1,491 |
| Deadlines only, parent hash no longer waits for child copies | 960 | 1,499 |
| Deadlines only, cap15 | rejected | 1,603 required |
| All accepted changes, cap6 | 955 | 1,459 |
| All accepted changes, cap10 | 960 | 1,515 |
| All accepted changes, cap15 | rejected | 1,595 required |
| All accepted changes, inherit consuming cohort | 959 | 1,499 |
| All accepted changes, final cache6 | 962 | 1,459 |
| All accepted changes, final cache8 | 961 | 1,459 |

The cache8 row has load1,818, work6,929 and flow904: cheaper load/compute,
but already beyond the900 flow budget. The parent/child-overlap dependency
relaxation did not improve the deadline-only graph, so it is not integrated.

Retest the earlier direct-child landing idea on the **new955 graph**, not
just its old970 parent. Strict WAR gives **963**, scratch1,467; same-cycle
WAR gives **961**, scratch1,531. Both have load1,828, weighted work6,901,
flow891/store40 and pass three seeds plus emission. The net30-equivalent
saving still loses to the load/read-buffer serialization. No speculative
allocator or same-cycle scheduler enters the accepted kernel. The existing
probe now accepts an explicit `--source-ref` while preserving its pinned
1d27efc default.

### Acceptance and reproduction

Official9/9 and built-in3/3 pass at955. Full local acceptance passes32 frozen
seeds, eight full-32-bit fixtures, exact primitive reconstruction, dependency
and capacity checks, setup accounting, exact workspace/padding and untouched
memory. Six extra shapes and path depths0/2/3 each retain their old cycle
counts across three seeds. No official test/simulator changes.

```sh
python3 tune_kernel.py --blocked-setup-deadlines 0 1 --blocked-early-tail-select 0 1 --blocked-drop-unused-weight 0 1 --seeds 123 456 789
python3 tests/submission_tests.py
python3 perf_takehome.py
python3 verify_kernel.py --extra-shapes
python3 experiments/iteration35_setup_deadlines.py --early-tail --drop-weight
python3 experiments/iteration35_setup_deadlines.py --early-tail --drop-weight --cap 15
# On the clean iteration35 checkout; default source remains the old970 graph:
python3 experiments/iteration34_child_landing.py --source-ref working-tree
python3 experiments/iteration34_child_landing.py --source-ref working-tree --zero-war
```

Next require a body-work reduction that preserves parallel load readiness.
The smaller startup does not close the aggregate181-compute/26-load deficit.
There are55 elapsed cycles to900, not a validated complete route. No
leaderboard query or external submission was performed in this iteration.

## Iteration 36 — reduce depth-6 encoding work; one elapsed cycle gained

Date: 2026-09-13 (PDT). Parent `3d24666`. Accepted **954 cycles**, scratch
**1,477/1,536**. The larger body experiments did not beat955. The accepted
combination deletes20.25 compute equivalents but improves elapsed time by
only **one cycle (0.105%)**. Do not characterize this as breaking the900
barrier or as a large scheduling gain.

### Accepted: one deeper encoded level, shorter load setup, later drain

Append a runtime XOR-C copy of nodes63..126 (depth6) to the existing64-word
record workspace. Eight vector loads/XORs/stores replace256 scalar node
XORs in the32 depth-6 groups. Two source buffers retain normal inferred
RAW/WAR hazards. The record and depth-6 store barriers are separate: forcing
depth4 to wait for unrelated deeper copies would squander startup overlap.

For the copy, `delta=n_nodes+1=2048`. Modify the existing record exit bias
from `73-W` to `73-W+delta`, so depth6 already holds the copied address.
Then use `-5-2*delta` in the existing affine index update to return to the
original depth-7 forest. No extra per-gather conversion is emitted. Since
only one level is copied, no within-copy transition bias is needed.

This alone removes21.25 compute equivalents, adds11 loads/eight stores,
and ties955. Change input-address generation from16 forward two-address
chains to eight descending four-address chains: anchor the highest group
in each chain, derive lower addresses by subtracting8, and give the input
vloads their consuming root round0 metadata. This removes eight more load
slots at a cost of eight ALU additions/subtractions (one equivalent). It
also advances the first combined lookup54 ->53, but alone still ties955.
The forward/generic path remains unchanged, including its chain-length
control; the blocked reverse chain has its own zero-to-disable switch.

Finally, compare drain-phase start920 with900 on the changed graph. The
new phase saves one cycle only with depth-6 encoding enabled. Production
retains every original policy, adding the later phase as another bounded
candidate; no instructions or dependencies are relaxed. The chosen policy
is `fragment_adaptive_tail_hetero_360_220_140_140_920`.

### Complete accepted A/B

All eight rows pass frozen seeds123/456/789. The tail setting adds a policy,
not a mandate to use it; unchanged graphs retain the earlier winner on ties.

| Depth-6 copy | Reverse chain length | Added tail phase | Cycles | Loads | Weighted compute | Scratch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| off | 0 | 900 | 955 | 1,826 | 6,931 | 1,459 |
| off | 0 | 920 | 955 | 1,826 | 6,931 | 1,459 |
| off | 4 | 900 | 955 | 1,818 | 6,932 | 1,459 |
| off | 4 | 920 | 955 | 1,818 | 6,932 | 1,459 |
| on | 0 | 900 | 955 | 1,837 | 6,909.75 | 1,477 |
| on | 0 | 920 | 954 | 1,837 | 6,909.75 | 1,477 |
| on | 4 | 900 | 955 | 1,829 | 6,910.75 | 1,477 |
| on | 4 | 920 | **954** | **1,829** | **6,910.75** | **1,477** |

Accepted slots: load1,829, VALU5,599, ALU10,494, flow891, store48. Versus
955: -5 VALU/-122 ALU, +3 loads, +8 stores and +18 scratch. The256 removed
node XORs are partly offset by nine vector setup operations,14 pointer
updates and eight extra address-chain ALU operations; adaptive offloading
also changes the split. Net work is `-5-122/8=-20.25` equivalents.

Optimistic combined compute floor drops925 ->922. Necessary900 deficits
become160.75 compute equivalents and29 loads (the load deficit worsens by3).
Per-engine floors are load915, VALU934, ALU875, flow891, store24. None is a
proof that other algorithms cannot reach900. Fifty-four elapsed cycles remain.

Body lookup traffic remains256 record vloads +1,480 scalar gathers. Combined
first/last53/943, drain10, conditional finish bound921; narrow gathers start80.
Offloaded399, fragmented274, maximum fragment span31 (up from10), with exact
emission/dependency and full-memory checks passing. Pruned constants remain11.
The128-word workspace contains112 encoded nodes plus16 padding zeros; the
remaining128 index words and entire forest/header remain unchanged.

### Rejected body experiment A: independent child-landing chains

Split the earlier left-child landing span into two or four independent
chains, merging only the left lanes from non-primary banks. Each bank is
protected as an atomic contiguous16-word interval in the PROBE allocator.
Secondary left copies wait for the primary bank's old readers as well as
their source load. Strict dependencies remain unless the explicitly named
same-cycle WAR probe is enabled.

With two or more interleaved banks, keep the original `[parent,left,right,0]`
record order: place left lane0 at buffer offset1 and space successive loads
in a bank by at least two words. This preserves previous left values while
avoiding two tail-address constants. The unaligned left-vector read is
included in the full contiguous allocation span. This is a valid lower-work
layout, but its merging and overwrite dependencies still cost too much.

| Banks / layout / dependency rule | Cycles | Scratch | Loads | Weighted compute |
| --- | ---: | ---: | ---: | ---: |
| 1 / left-first / strict (control) | 963 | 1,467 | 1,828 | 6,901 |
| 2 / left-first / strict | 961 | 1,491 | 1,828 | 6,917 |
| 2 / left-first / same-cycle WAR | 959 | 1,491 | 1,828 | 6,917 |
| 2 / left-first / contiguous lane partitions | 961 | 1,491 | 1,828 | 6,917 |
| 4 / left-first / strict | 957 | 1,515 | 1,828 | 6,925 |
| 2 / original record order / strict | 961 | 1,483 | 1,826 | 6,915 |
| 4 / original record order / strict | 957 | 1,515 | 1,826 | 6,923 |
| 4 / original record order / same-cycle WAR | 959 | 1,523 | 1,826 | 6,923 |
| 4 / original order / strict / final cache6 | 960 | 1,515 | 1,834 | 6,925 |

All rows pass three seeds and primitive emission. On the UNCHANGED955 layout,
same-cycle WAR with two/four read banks both takes960, so that mechanism is
also rejected independently. No new allocator or same-cycle dependencies
enter the production kernel. See `iteration36_banked_landing.py` and the
existing iteration34 probe with `--source-ref 3d24666 --base-layout --zero-war`.

### Rejected body experiment B: broader encoding and changed index arithmetic

| Extra encoded levels / setup variant (forward chains, tail900) | Cycles | Scratch | Loads | Weighted compute |
| --- | ---: | ---: | ---: | ---: |
| depth6, including an unused within-copy bias | 956 | 1,509 | 1,838 | 6,910.75 |
| depth6, unused bias omitted | 955 | 1,477 | 1,837 | 6,909.75 |
| depth6, reuse the record-input buffers | 958 | 1,501 | 1,837 | 6,909.75 |
| depth6, one buffer instead of two | 955 | 1,477 | 1,837 | 6,909.75 |
| depth6, setup deadline cap6 | 955 | 1,477 | 1,837 | 6,909.75 |
| depths6/7, full remaining workspace | 963 | 1,533 | 1,854 | 6,898.75 |
| depth7 only, unused bias omitted | 958 | 1,477 | 1,846 | 6,920.75 |

The depth7-only prototype retaining the unused bias FAILED seed123. No cycle
result is accepted for it; its failure is preserved as a correctness rejection
in the probe, not silently treated as a valid slower row. The omitted-bias
variant passes three seeds, one full-word workspace fixture and a traced
check of each node lookup's expected address/value. It is still slower than955
and was not integrated. This rejection is not a claim that its cause has
been fully diagnosed.

With the valid depth6 copy, final cache8/9/10 gives962/969/974, with flow
904/917/930. More cache is again rejected. On the depth6+reverse-chain graph,
probe tail starts860/880/900/920/940 give960/960/955/954/955. These probes
replace one candidate policy; production adds920 while retaining900.

Use parity to select a vector bias -6/-5, then do a single index MAC, instead
of preparing2*A-5 and subtracting parity. Across one deep level this removes
32 vector-equivalent subtractions, with one new bias broadcast: net -31
compute equivalents, but +32 flow slots and +1 load. The final MAC now waits
for the parity AND bias select. Depth6 or depth9 across32 groups both takes
976 (flow923, work6,900). Restricting depth9 to eight groups takes960,
flow899, work6,924. Even spare aggregate flow capacity does not make those
new dependent selects free. See `iteration36_index_select.py`.

### Other bounded readiness probes

- Move XOR-C from the gathered raw node onto the previous encoded input value,
  after previous parity readers, but independently of the current load. This
  shortens the gather-to-input-XOR chain without changing total work. Across
  depths6..10 it takes958 with adaptive vector XOR or957 with scalar XOR.
  Depth10 only takes958 for either; depths9..10 take958; depth6 only takes959.
  No early-decode change is accepted (`iteration36_early_decode.py`).
- Fuse the raw-root encoding with its required copy: one fewer ALU operation,
  but still955. Let root XORs adapt between vector/scalar engines: first
  traversal only957, both traversals960 (with root-copy fusion). Rejected.
- Four FORWARD address chains:963, load1,818. Reverse four-address chains:
  958 with old pre-round input priority,955 with round0 priority. Reverse
  two-address chains take959. Reverse eight-address chains plus round0 take
  958, despite load1,814. Input round0 alone ties955. Only the costed four-step
  reverse version is accepted in combination with deeper encoding.

### Acceptance, reproducibility and next direction

Official9/9, built-in3/3, 32 frozen seeds, eight full-word fixtures, exact
emission, strict dependency/capacity checks, setup accounting and workspace
boundaries pass at954. The six extra shapes and three alternate path depths
each retain their previous cycle counts across three seeds. Tests and
simulators are unchanged. Six iteration36 scripts pin their source to955
and report valid timings only after frozen checks; they are not imported
by the production kernel.

```sh
python3 tune_kernel.py --blocked-encode-depth6 0 1 --blocked-reverse-input-chain-length 0 4 --blocked-tail-start 900 920 --seeds 123 456 789
python3 tests/submission_tests.py
python3 perf_takehome.py
python3 verify_kernel.py --extra-shapes
python3 experiments/iteration36_banked_landing.py --banks 4 --original-order
python3 experiments/iteration36_extra_encoding.py --first 6 --last 6 --drop-unused-bias --reverse-chains --tail-start 920
python3 experiments/iteration36_index_select.py --depth 9 --groups 8
```

Next demand both a full work budget and a readiness argument for another
body transformation. A compressed two-level record is only a hypothesis:
count any extra address MAC, merge/copy, flow and live scratch before treating
fewer gathers as progress. Do not repeat the rejected bank, extra-cache,
early-decode or index-select variants unchanged. No leaderboard query or
external benchmark submission this iteration.
