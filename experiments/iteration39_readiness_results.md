# Iteration39: bootstrap a cohort, not one operation

Fixed parent: `161c60200be0aa071532f6784817a5b562e9c2c8`, 928 cycles.
The probe never changes simulator capacities, slots, or machine semantics.
Every score below passes exact emission, scratch provenance, frozen seeds
123/456/789, and an independent full-32-bit workspace/output fixture.

## Accepted candidate

`--bootstrap-dag 2 --bootstrap-critical --full-policies`: **927 cycles**,
scratch **1432**, original winning policy
`fragment_adaptive_tail_hetero_360_240_240_220_900`.

For each of the highest two groups, find its first round1 node vselect.
Collect the complete ancestor DAG; compute each ancestor's longest remaining
path to these targets. After each existing ready-queue sort, stably prioritize
this ancestor set, then its target distance. Unrelated operations retain their
original order, including the existing tail policy. All instruction slots and
dependency edges are unchanged; logical work stays6745.625 equivalents.

Production gates this on the scored compact flow-exchange path through
`COMPACT_STARTUP_GROUPS=2`; zero restores928/1440. The target search explicitly
ignores setup and non-vselect flow instructions. Generic paths are unchanged.

Reproduce the prototype and exact production/control check:

```sh
python3 -B experiments/iteration39_readiness.py --bootstrap-dag 2 --bootstrap-critical --full-policies --compare-production
python3 -B experiments/iteration39_readiness.py --inspect
python3 -B experiments/iteration39_readiness.py --bootstrap-dag 2 --bootstrap-critical --inspect
```

## What the trace actually says

The928 baseline's twenty flow holes are sixteen early holes at62..131 plus
four at898..901. Its last dependency chain is group0 round13 hash, final
round14 select at902, round14 hash, address MAC913, last round15 gather917,
then the strict final hash/store chain through927. The last ten cycles have
no avoidable ready-queue waits in that chain. Merely reprioritizing the final
drain cannot remove them; the late group's earlier computation must advance.

The earliest baseline flow at19 is **not a hardware lower bound**. Its
critical input address constant is ready at0 but issued7, input vload8,
root input XOR9, hash MAC10, terminal XOR17, parity18, select19.

A single bootstrap DAG ordered by target distance starts its input constant
at0, vload2, root XOR3, MAC4, parity12, select13. Yet this screen is930,
and full policies give929: the early start increases later flow holes.
Prioritizing only input loads is worse still: it postpones the root/constant
setup chain and first flow moves to20 (930 total).

The accepted two-group bootstrap starts flow at14 and finishes it at901.
There are24 holes instead of20: advancing startup by5 saves only1 end-to-end
cycle because four new holes appear. Last gather moves917→916 and the same
ten-cycle drain gives927. This is an actual one-cycle gain, not a claim that
five startup cycles were recovered globally.

## Bounded controls, not a large parameter sweep

Three-policy screening includes the accepted928 parent policy and both
existing140/140 tail policies at900/920. `Full` means the entire existing
production policy set, not just those three.

| Probe | Policies | Cycles | Scratch | Observation |
| --- | --- | ---: | ---: | --- |
| Parent | three | 928 | 1440 | Trace/control |
| Flow-distance urgency <=3 during startup through135 | three | 929 | 1448 | Shifted readiness did not help |
| Same, distance <=6 | three | 929 | 1448 | No improvement |
| Generic bottom-level critical priority after850 | three | 943 | 1440 | Disrupts useful cohort order |
| Same after800 | three | 959 | 1440 | Earlier is worse |
| Low4 groups compute priority +2 positions fromround11 | three | 928 | 1440 | Tie |
| Same +4 | three | 929 | 1480 | Regression |
| Release parent hash from independent last-child copy | three | 928 | 1440 | Tie |
| Explicit lane terminal-XOR/parity additionally atdepth3 | full | 929 | 1408 | Lower scratch, no time gain |
| Move round11 root XOR into round10 terminal left arm | full | 930 | 1504 | Same work; shorter local chain is not enough |
| Prioritize only first1/2 input-load groups | three | 930 | 1488 | Root setup delayed; first flow20 |
| One whole startup DAG, existing internal order | three | 931 | 1448 | Too little critical-path awareness |
| Two whole startup DAGs, existing internal order | three | 929 | 1464 | Same issue |
| One startup DAG, target-distance order | full | 929 | 1416 | First-flow13 screen is not end-to-end win |
| Two startup DAGs, target-distance order | full | **927** | **1432** | Accepted |

The cross-root rewrite uses `(x ^ root_encoded) ^ (x >>16)` at the preceding
leaf, then skips the next root XOR. Both original arms and root input remain
runtime values; no data-dependent specialization is involved. It is a valid
but rejected dependency rearrangement, not an operation-count reduction.

Next useful scheduling evidence is an arrival curve of ready flow/load work
over the whole cohort, not merely an earlier first issue. The unchanged work
budget is still almost full at900, so additional true computation removal
remains important even though this measured scheduling improvement is real.
