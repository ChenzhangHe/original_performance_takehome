# Isolated optimization probes

Iteration34 scripts default to **1d27efc** (970 cycles); the iteration35
deadline script pins **468f713** (969 cycles). They read `perf_takehome.py`
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
python3 tune_kernel.py --blocked-fuse-setup-xor 0 1 --blocked-setup-deadlines 0 --blocked-early-tail-select 0 --blocked-drop-unused-weight 0 --seeds 123 456 789
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
python3 tune_kernel.py --blocked-setup-deadlines 0 1 --blocked-early-tail-select 0 1 --blocked-drop-unused-weight 0 1 --seeds 123 456 789
python3 verify_kernel.py --extra-shapes
```

The 955 kernel changes neither record layout nor hashing. The log records
the non-additive A/B, including slower candidates with fewer instructions.
