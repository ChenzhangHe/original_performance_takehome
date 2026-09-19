# Iteration41: lane fusion and FMA fairness controls

Pinned parent: `109610180033baa744a0fe80a2501e1ba2f73b00`, 924 cycles.
These probes alter legal issue choices and dependency granularity, not the
ISA, input values or arithmetic identities. No variant below beats its
appropriate control, so none is included in production.

## Gather input XORs lane by lane

The baseline waits for all gathered/encoded node lanes before the vector
input XOR. Emit eight independent scalar value XORs, each depending on its
own node lane and value producer. The first hash MAC still waits for all
eight lanes. The optional early decode moves the representation XOR from
the raw node to the previous value, retaining the previous parity/address
read barrier. Both forms preserve total weighted arithmetic.

| Variant | Policies | Cycles | Scratch | Physical VALU / ALU |
| --- | ---: | ---: | ---: | --- |
| Final-round gathers, all groups | 3 | 925 | 1424 | 5411 / 10677 |
| Raw depth8/9/10 gathers | 236 | 924 | 1464 | 5420 / 10605 |
| Raw gathers plus early value decode | 3 | 925 | 1448 | 5416 / 10637 |
| All common gathers plus early raw-value decode | 236 | 924 | 1448 | 5416 / 10637 |
| Final-round gathers, groups0/1 only | 3 | 924 | 1424 | 5415 / 10645 |

Every row retains work6745.625, load1783, flow864 and store64. More explicit
scalar operations do not necessarily mean more total offload: the rest of
the scheduler reacts to the changed ALU queue. Narrower dependencies alone
do not prove an elapsed gain.

```
python3 -B experiments/iteration41_gather_fusion.py --scope final
python3 -B experiments/iteration41_gather_fusion.py --scope raw --full-policies
python3 -B experiments/iteration41_gather_fusion.py --scope raw --early-decode
python3 -B experiments/iteration41_gather_fusion.py --scope all --early-decode --full-policies
python3 -B experiments/iteration41_gather_fusion.py --scope final --groups 2
```

## Reserve a VALU slot for a pipeline-feeding binary

FMA-first can delay an ordinary binary that feeds a subsequent FMA. Test a
specific fairness rule only when all six leading ready operations are FMAs:
give the sixth VALU slot to the highest-priority eligible binary with a direct
FMA successor. Keep all capacity/dependency checks and stable order otherwise.
The second control requires the feeder to have waited four cycles first.

| Rule | Address control | Policies | Cycles | Scratch |
| --- | --- | ---: | ---: | ---: |
| Immediate feeder reservation | Original924 | 236 | 924 | 1424 |
| Reservation after four-cycle wait | Original924 | 3 | 925 | 1424 |
| Immediate feeder reservation | Independent flow addresses923 | 236 | 923 | 1480 |
| Reservation after four-cycle wait | Independent flow addresses923 | 3 | 923 | 1424 |

The first row has VALU5415 / ALU10645 and unchanged work6745.625. The two
independent-address rows have VALU5416 / ALU10613 and work6742.625, versus
the simpler923 control's VALU5414 / ALU10629 and scratch1416. Equal runtime
with extra scheduling logic/storage is not adopted. The full-policy
immediate-address/fairness row selects
`fragment_adaptive_tail_hetero_360_220_140_140_900`, not the control's policy.

```
python3 -B experiments/iteration41_fma_fairness.py --enabled --full-policies
python3 -B experiments/iteration41_fma_fairness.py --enabled --age 4
python3 -B experiments/iteration41_fma_fairness.py --enabled --inputs-immediate --full-policies
python3 -B experiments/iteration41_fma_fairness.py --enabled --age 4 --inputs-immediate
```

Every measured row passes exact primitive emission and dependency/capacity
checks, physical scratch provenance, frozen seeds123/456/789, exhaustive
workspace addresses and a full32-bit output/workspace fixture. Three-policy
screens are not full-search results. The immediate-address variant uses the
original `flow.add_imm` ISA; its provenance declaration reads only the source
scratch operand and writes only the destination, never the immediate.
