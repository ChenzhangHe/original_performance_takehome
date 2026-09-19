# Iteration 38 readiness experiments

Pinned source: `34742f6253c7cb1b1b2a3ca664b6b5f2d9a29991` (941 cycles).
Only the independent probe is edited; no production, simulator, test, or shared
validator changes are made by this experiment.

## Best result: 940 cycles, 1440 scratch

```
python3 experiments/iteration38_readiness.py --early-shallow --setup-deadline-cap 6 --full-policies
```

This retains the entire original policy set and selects
`fragment_adaptive_tail_hetero_360_220_140_140_920`. It passes frozen seeds
123/456/789, a full-32-bit tree/input fixture and exact workspace contents,
primitive-slot emission/capacity validation, and physical scratch provenance.

Both changes affect scheduling metadata only, retaining every original
operation and dependency:

1. Give the two child copies in the shallow record reader `round_no - 1`
   priority, restoring `round_no` immediately afterward. Finishing copies
   releases the four read buffers for later record lanes sooner.
2. In `prioritize_setup_by_first_use`, cap the setup deadline at 6 instead of 4.
   The new compact deep table first feeds round 6; the old universal cap was
   inherited from the previous record/cache layout. This reduces premature
   setup and live scratch, not computation.

The result preserves 1648 loads, 64 stores, 896 flow slots and 6833.625 weighted
compute equivalents. It does not establish a lower arithmetic bound or a
route to 900 by scheduling alone.

## Bounded A/B results

Unless explicitly marked full, rows use the two existing fragment tail
policies ending at 900 and 920. Every timed row passes all four validation
categories above.

| Candidate | Cycles | Scratch | Conclusion |
| --- | ---: | ---: | --- |
| Parent hash no longer waits on unrelated child copies | 941 | 1480 | No improvement; do not integrate dependency deletion |
| Above + deep child copy priority 5 | 941 | 1472 | Scratch only |
| Above + shallow copies one priority round earlier | 940 | 1464 | Full policies reproduce 940 |
| Shallow copy priority only, all original dependencies | 940 | 1464 | Same win with smaller change |
| Setup deadline cap 6 only | 942 | 1464 | Regresses alone; use only with shallow priority |
| Shallow priority + deep child priority 5 + child release | 940 | 1472 | Worse scratch |
| Shallow priority + setup cap 6, all original dependencies | 940 | 1440 | Full policies reproduce; preferred |
| Shallow priority + child release + lane tail only depths 7/8/9 | 941 | 1440 | Regresses |
| Shallow priority + child release + lane tail depths 5/7/8/9 | 940 | 1440 | No additional win |
| Preferred + force unfinished fragment completion after age 1 | 941 | 1448 | Regresses |
| Preferred + force unfinished fragment completion after age 4 | 941 | 1448 | Regresses |
| Preferred + force unfinished fragment completion after age 12 | 940 | 1480 | No win, larger scratch |

Fragment completion experiments reserve real scalar slots and wait until all
lanes finish before unlocking consumers; they never permit a relaxed hazard
or larger engine capacity. Advancing a stalled fragment displaces useful
scalar chains, so preventing long fragment spans is not automatically faster.

For the preferred production candidate, preserve all dependencies and the
existing fragment mechanism. Only introduce the two priority changes above.
