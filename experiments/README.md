# Isolated optimization probes

Iteration34 scripts default to **1d27efc** (970 cycles); the iteration35
deadline script pins **468f713** (969 cycles); iteration36 scripts pin
**3d24666** (955 cycles). Iteration37 kernel probes pin **2d5bc94** (954).
They read `perf_takehome.py`
from those commits, transform that source **in memory**, and run the frozen simulator.
They do not edit the kernel, tests, simulator, inputs, or git history. Run
them from a checkout containing those commits. Their default results are
historical comparisons, not automatic measurements of the latest kernel.
The child-landing probe also accepts an explicit `--source-ref` (including
`working-tree`) for retesting it on a changed graph.

Each valid result checks seeds 123/456/789 and exact primitive emission.
These are bounded research checks, not the full acceptance suite. A scratch
overflow is reported as a rejection; the simulator limit is never increased.
The speculative allocator and same-cycle scheduling code are NOT imported
by the production kernel.

```sh
# Reorder records so the left child lands directly in a contiguous vector.
python3 experiments/iteration34_child_landing.py                    # 981
python3 experiments/iteration34_child_landing.py --zero-war         # 974
# Apply the same-cycle buffer mechanism to the unchanged parent layout.
python3 experiments/iteration34_child_landing.py --zero-war --base-layout  # 972

# Move records to depths 5/6, retain a linear encoded depth-4 copy.
python3 experiments/iteration34_deeper_block.py --first-cache 0 --final-cache 7  # 979
python3 experiments/iteration34_deeper_block.py --first-cache 8 --final-cache 7  # scratch rejection: 1652 > 1536
```

See iteration 34 in `OPTIMIZATION_LOG.md` for the full parameter table and
why these lower-work variants were not accepted. The actual accepted
setup-copy fusion's historical970/969 comparison requires the later
iteration35 switches disabled:

```sh
python3 tune_kernel.py --blocked-fuse-setup-xor 0 1 --blocked-setup-deadlines 0 --blocked-early-tail-select 0 --blocked-drop-unused-weight 0 --blocked-encode-depth6 0 --blocked-reverse-input-chain-length 0 --blocked-tail-start 900 --seeds 123 456 789
```

Iteration35 isolates setup urgency, tail selection order and a dead weight:

```sh
python3 experiments/iteration35_setup_deadlines.py                          # 960
python3 experiments/iteration35_setup_deadlines.py --early-tail --drop-weight  # 955
python3 experiments/iteration35_setup_deadlines.py --early-tail --drop-weight --cap 15  # scratch rejection: 1595 > 1536

# Remeasured on the iteration35 kernel, not the old970 source:
python3 experiments/iteration34_child_landing.py --source-ref working-tree            # 963
python3 experiments/iteration34_child_landing.py --source-ref working-tree --zero-war # 961

# All eight production combinations, followed by full acceptance:
python3 tune_kernel.py --blocked-setup-deadlines 0 1 --blocked-early-tail-select 0 1 --blocked-drop-unused-weight 0 1 --blocked-encode-depth6 0 --blocked-reverse-input-chain-length 0 --blocked-tail-start 900 --seeds 123 456 789
python3 verify_kernel.py --extra-shapes
```

The historical955 kernel changes neither record layout nor hashing. The log records
the non-additive A/B, including slower candidates with fewer instructions.

## Iteration36: deeper encoding and rejected body reductions

All six scripts below use the fixed955 source. They do not modify the
production kernel. Unless noted otherwise, results check three frozen seeds
and exact primitive emission; encoding, early-decode and index-select probes
also check a full-word workspace fixture. This is narrower than full acceptance.

```sh
# Independent landing chains; original record order, atomic probe-only spans.
python3 experiments/iteration36_banked_landing.py --banks 4 --original-order  # 957
python3 experiments/iteration36_banked_landing.py --banks 2 --original-order  # 961

# Accepted combination, independently reproduced from955; 954 cycles.
python3 experiments/iteration36_extra_encoding.py --first 6 --last 6 --drop-unused-bias --reverse-chains --tail-start 920
# Lower work, but slower overall: 963 cycles.
python3 experiments/iteration36_extra_encoding.py --first 6 --last 7
# Known invalid prototype: correctness rejection, NOT a timing result.
python3 experiments/iteration36_extra_encoding.py --first 7 --last 7

python3 experiments/iteration36_early_decode.py --scalar              # 957
python3 experiments/iteration36_root_issue.py --vectors 1 --fuse      # 957
python3 experiments/iteration36_reverse_addresses.py --reverse --chain 4 --input-round 0 # 955
python3 experiments/iteration36_index_select.py --depth 9 --groups 8 # 960

# Full production A/B and acceptance. All original scheduling policies remain.
python3 tune_kernel.py --blocked-encode-depth6 0 1 --blocked-reverse-input-chain-length 0 4 --blocked-tail-start 900 920 --seeds 123 456 789
python3 verify_kernel.py --extra-shapes
```

The new default is954, one cycle faster than955, with20.25 fewer compute
equivalents but three more loads. See iteration36 in the log for its complete
accounting and rejected variants. The experiments' tail-start option REPLACES
one candidate; production ADDS the920 candidate and retains the old900 one.

## Iteration37: compact records, stronger validation, 941 cycles

The iteration37 kernel probes transform the fixed954 source in memory.
Without `--full-policies`, they normally screen just the old900/920 fragment
policies; those timings must not be confused with a full scheduler search.
Do not apply older `--source-ref working-tree` textual transforms blindly
to the new compact reader: use the explicit historical commit instead.

```sh
# Eight-word three-level record: valid, but slow (1031 cycles).
python3 experiments/iteration37_three_level.py --window 2 --banks 2 --full-policies

# Optional research dependency ONLY: z3-solver==4.15.4.0 in an isolated env.
# Eight precise templates are UNSAT; this is not a hash-optimality proof.
python3 experiments/iteration37_state_search.py --seconds 12

# Lane-level terminal XOR/parity consumers: ties954 on the old graph.
python3 experiments/iteration37_lane_tail.py --full-policies
# Analytical routing cost screen, not a kernel timing.
python3 experiments/iteration37_frontier_budget.py

# Compact stride-3 records + early parent bias selection + lane tails: 944.
python3 experiments/iteration37_compact_deep.py --cache 0 --index-select 4 6 --lane-tail --full-policies --safe-reuse --dataflow --drop-unused-biases
# Direct left-child landing on the NEW load-light graph: 941.
python3 experiments/iteration37_deep_landing.py --full-policies

# Full current-kernel A/B and acceptance, no Z3 dependency.
python3 tune_kernel.py --compact-deep-landing 0 --compact-lane-tail 0 1 --compact-parent-index-select 0 1 --seeds 123 456 789
python3 tune_kernel.py --blocked-compact-deep 0 --seeds 123 456 789
python3 verify_kernel.py --extra-shapes
```

`dataflow_check.py` checks physical scratch provenance against original
logical producers and rejects same-cycle write/write collisions. Production
now fixes the allocator boundary, and its local verifier uses this checker.
The compact probe retains the old allocator by default for reproducing the
failure: always use `--safe-reuse --dataflow` for accepted comparisons. For
example the lane-tail + parent-select combination without safe reuse fails
seed123; no timing from that failed run is reported as valid. Scratch
overflows likewise remain rejections, not hypothetical scores.

The deep-landing probe verifies its full contiguous16-word span, three frozen
seeds and full-word workspace. Production additionally passes the complete
32-seed/eight-fixture suite, official tests, extra shapes and allocator
boundary regressions. See iteration37 of the log for budgets and every
important negative result. Kernel code does not import experiment modules.

## Iteration38: resource exchange, 928 cycles

All iteration38 source transforms pin `34742f6` (941 cycles). They compose
in memory; no production kernel imports these probes. Every reported valid
candidate checks three frozen seeds, exact emission, scratch provenance,
and a full32-bit workspace fixture. Production has the broader acceptance
suite. Complete policy sets matter: the winning structure was missed by
three-policy screening.

```sh
# 940, work6803.625: shallow left-child landing + setup cap6.
python3 experiments/iteration38_shallow_landing.py --setup-cap 6 --full-policies
# 940 on old layout, unchanged work: independent metadata-only control.
python3 experiments/iteration38_readiness.py --early-shallow --setup-deadline-cap 6 --full-policies
# Padding-only control: +1 compute equivalent, no lookup changes.
python3 experiments/iteration38_compute.py --reuse-padding --full-policies
# Resource exchange: 930 with all policies, work6745.625.
python3 experiments/iteration38_exchange.py --groups 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 --parent-select --reuse-padding --full-policies
# Accepted scheduling priority on that graph: 928.
python3 experiments/iteration38_exchange_readiness.py --groups 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 --variants select_late --full-policies

# Production controls and complete acceptance.
python3 tune_kernel.py --compact-flow-exchange 0 --compact-shallow-landing 0 --compact-setup-deadline-cap 4 --seeds 123 456 789  # 941
python3 tune_kernel.py --compact-flow-exchange 0 --seeds 123 456 789  # 940
python3 tune_kernel.py --compact-deep-select-delay 0 1 --seeds 123 456 789  # 930/928
python3 verify_kernel.py --extra-shapes
```

`--groups` in the exchange probes is an explicit list of vector group IDs,
not data-dependent membership. Only their first traversal uses depth3
gathers. Node values and addresses are computed by emitted runtime code;
there is no answer precomputation. The group choice affects readiness:
16 high groups are faster than16 middle groups despite identical counts.
Do not equate the new aggregate compute floor900 with an achieved900 score.
