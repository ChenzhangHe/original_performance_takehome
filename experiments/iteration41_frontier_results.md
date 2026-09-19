# Iteration41: promote the complete consumer frontier

Parent: `109610180033baa744a0fe80a2501e1ba2f73b00`, **924 cycles**,
scratch1424. No production, simulator, common verifier, or git state was
modified by these probes. There is no new performance record in this branch.

The previous single-lane feedback rotated which of eight gathers finished
last. This experiment explicitly handles the whole vector consumer frontier.
The four variants are structural controls, not a parameter sweep.

## Find the frontier from the actual schedule DAG

Follow latest producers backward from the final store. For a late gathered
value, infer its RAW successors from the original logical read/write sets,
find the first vector consumer, then collect all same-round/group load
ancestors and intermediate scalar encoders. Address-overwrite WAR edges are
not treated as loaded-value consumers. This analysis changes no real edges.

For the924 kernel this dynamically identifies group0, round10:

- Eight load operations852..859 all have addresses ready at816.
- They issue two per cycle at825,826,827,828.
- Scalar encoding860..867 feeds input XOR868, then the first hash MAC869.
- With exclusive use of both load ports, the eight-load packet could finish
  at819; its actual finish828 therefore has9cycles of packet scheduling delay.

The ideal819 is a **packet-local conditional bound**, not nine globally free
cycles. Other groups are using those same ports and still need to finish.
The final-round eight-load packet has only its intrinsic four-cycle width;
it is not selected as an avoidable three-cycle single-lane wait.

## Four measured candidates

Each actual candidate, including regressions, was emitted and checked with
exact slot/dependency reconstruction, scratch provenance, frozen seeds
123/456/789, and a full32-bit workspace/output fixture. Scores do not silently
fall back to the parent; `--keep-baseline` is false for these controls.

| Candidate | Policies | Cycles | Scratch | Packet issues after change | Input XOR first | First MAC |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| Parent | existing | 924 | 1424 | 825..828 | 830 | 832 |
| Eight loads receive360priority bonus | three | 925 | 1424 | 816..819 | 827 | 828 |
| Loads plus eight scalar encoders receive bonus | three | 925 | 1424 | — | — | — |
| Entire join frontier plus first hash MAC receive bonus | **236** | 925 | 1424 | 816..819 | 822 | 823 |
| Entire join frontier, consumer deadline4cycles earlier | **236** | 924 | 1424 | 824..827 | 829 | 830 |

The entry variant reaches the packet-local load optimum and advances both
its input XOR and first MAC substantially, yet it is one cycle slower
globally. The negative result is not a failure to advance the whole packet.
All variants leave the6745.625 weighted computation total unchanged.

## Distinguish readiness from fragmentation

The parent round10 input XOR is ready at830 and begins immediately at830.
It is scalar-offloaded with lane times
`[830,831,831,831,831,831,831,831]`, completing831. Therefore this particular
parent operation has **zero ready wait**, plus one cycle of fragment drain;
it is not a long VALU-starvation example. Scalar encoders issue immediately
when their loads become available, with the final pair at829.

Advancing only the load packet changes the situation. Encoders now finish
at820 and the XOR is ready821, but it waits until827 and runs as one VALU
operation: six cycles of new ready wait. Giving the complete entry frontier
priority cuts this wait to one cycle (VALU822, MAC823), still without a
global win. A scheduling change can expose a new compute bottleneck after
removing the previous load wait.

Conclusion: keep924. A frontier is the correct analysis unit for the gather,
but removing its local wait is not sufficient for elapsed progress. New
attempts should account for displaced work and downstream issue capacity,
not assume packet-local delay converts directly into total-cycle savings.

```sh
python3 -B experiments/iteration41_frontier.py --passes 0 --inspect
python3 -B experiments/iteration41_frontier.py --scope loads --inspect
python3 -B experiments/iteration41_frontier.py --scope bridge
python3 -B experiments/iteration41_frontier.py --scope entry --full-policies --inspect
python3 -B experiments/iteration41_frontier.py --scope join --mode deadline --full-policies
```
