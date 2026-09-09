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
