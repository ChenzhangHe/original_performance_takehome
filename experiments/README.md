# Isolated optimization probes

These scripts read `perf_takehome.py` from the fixed **1d27efc** (970-cycle)
commit, transform that source **in memory**, and run the frozen simulator.
They do not edit the kernel, tests, simulator, inputs, or git history. Run
them from a checkout containing that commit. Their results are historical
comparisons against 970, not against the latest default.

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
setup-copy fusion is reproduced with
`python3 tune_kernel.py --blocked-fuse-setup-xor 0 1 --seeds 123 456 789`
(970 / 969), followed by `python3 verify_kernel.py --extra-shapes`.
