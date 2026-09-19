# Iteration40: reserve VALU eligibility for fused multiply-add

Pinned source: `ee87c658e6247232801432d7ab5a548d324c096d`, 927 cycles,
scratch1432. The accepted candidate is **924 cycles**, scratch1424. It adds
only a stable ready-queue sort in fragmented policies: ready `multiply_add`
operations precede binary vectors when filling VALU. The original scalar
ALU cannot issue fused multiply-add, so binary work can use otherwise idle
scalar slots while these operations receive VALU capacity. No operation,
dependency, lane, machine capacity, or simulator semantics changes.

The final minimal form promotes only `multiply_add`; also promoting
`vbroadcast` gives925. Preserve the original ordering within each class.
Scope the production switch `COMPACT_FMA_PRIORITY` to the compact flow
exchange graph, and preserve every existing policy. Turning it off restores
the exact pinned927 schedule. The winning policy remains
`fragment_adaptive_tail_hetero_360_240_240_220_900`.

| Candidate | Policies | Cycles | Scratch | VALU | ALU | Longest partial delay |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Pinned control | 3 | 927 | 1432 | 5430 | 10525 | 51 |
| Promote FMA and broadcast | 236 | 925 | 1424 | 5414 | 10653 | 53 |
| Promote only FMA, accepted | 236 | 924 | 1424 | 5417 | 10629 | 36 |
| Complete any partial next cycle, no promotion | 3 | 928 | 1448 | 5442 | 10429 | 1 |
| Complete partial within4 cycles, no promotion | 3 | 927 | 1448 | 5430 | 10525 | 4 |
| FMA+broadcast promotion and next-cycle completion | 236 | 924 | 1448 | 5417 | 10629 | 1 |
| Only-FMA promotion and next-cycle completion | 236 | 924 | 1424 | 5419 | 10613 | 1 |
| FMA+broadcast promotion and priority-based completion | 236 | 924 | 1440 | 5413 | 10661 | 12 |
| Only-FMA promotion and narrow shallow store deps | 3 | 925 | 1424 | 5416 | 10637 | 52 |
| Only-FMA promotion and scalarize all broadcasts | 3 | 930 | 1440 | 5400 | 10765 | 48 |
| Only-FMA promotion and scalarize constant broadcasts | 3 | 930 | 1440 | 5399 | 10773 | 41 |
| Only-FMA promotion and scalarize runtime broadcasts | 3 | 924 | 1456 | 5415 | 10645 | 52 |

All table rows pass exact primitive emission, physical scratch provenance,
frozen seeds123/456/789, the full-word output/workspace fixture and exhaustive
workspace address checks. Weighted compute remains6745.625; loads1783,
flow864 and stores64 remain unchanged. Scalar broadcast candidates use
eight original-ISA scalar `| source,source` copies before constructing the
setup DAG, retaining all inferred hazards. Even reducing physical VALU to
5400 or5399 does not give900 elapsed cycles; those candidates take930.

Other bounded offload selection tests did not win: choosing minimum bottom
level takes930; preferring scalar-only consumers takes930; maximum bottom
level takes928. Combining FMA+broadcast promotion with scalar-consumer
preference takes926. Combining it with maximum bottom level is rejected
at1592 scratch words, regardless of its928 unallocated schedule. Disabling
new partial starts after870 with FMA+broadcast promotion takes926. No
partial completion, broadcast rewrite, or shallow-store dependency change
is included in production.

The accepted schedule offloads358 vectors, up from345. This transfers13
vector operations, or104 scalar lanes, to ALU. Physical VALU5417 still has
an optimistic903-cycle floor; the weighted900 floor still omits startup
and dependency losses. A partial can remain outstanding36 cycles, yet
forcing completion within1 cycle does not improve the924 finish.

Reproduce the accepted and disabled-source controls:

```
python3 -B experiments/iteration40_balance.py --valu-fma --fma-mode only_fma --full-policies
python3 -B experiments/iteration40_balance.py --full-policies
python3 -B experiments/iteration40_balance_acceptance.py
```

The acceptance script separately builds pinned-probe and production
versions with the switch on and off. It compares every emitted instruction,
logical slot, dependency, first/final lane issue, scratch size and every one
of236 policy timings, then performs the strict and frozen correctness checks
on each build. Production integration is limited to the feature flag, its
compact-graph gate, and the stable FMA eligibility sort. No Git, tests,
verifier, ISA or simulator changes were made by this subtask.
