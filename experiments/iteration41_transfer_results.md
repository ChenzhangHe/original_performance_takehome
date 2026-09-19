# Iteration41: direct input addresses on flow

Pinned baseline `109610180033baa744a0fe80a2501e1ba2f73b00`:924 cycles,
scratch1424, weighted compute6745.625, load1783, flow864.

**A validated923-cycle candidate uses32 original-ISA `flow.add_imm`
instructions to materialize input addresses independently from readonly
zero.** Replace eight load.const chain anchors and24 scalar ALU chain
increments. These addresses depend only on the input layout, not input
values. The change removes24 scalar ALUs (3 compute equivalents) and eight
loads, adds32 flow instructions, and breaks the address chains. Root/body
hash and all lookup records remain unchanged.

| Candidate | Policies | Cycles | Scratch | Compute | Load | Flow |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Control | 236, prior acceptance | 924 | 1424 | 6745.625 | 1783 | 864 |
| Direct root encoding; delete one copy | 3 | 924 | 1424 | 6745.5 | 1783 | 864 |
| Retain root buffer; delete both copies | 236 | 924 | 1430 | 6745.375 | 1783 | 864 |
| Transfer24 address increments to flow; retain chains | 236 | 924 | 1408 | 6742.625 | 1783 | 888 |
| Root alias plus24 flow increments | 3 | 924 | 1414 | 6742.375 | 1783 | 888 |
| Direct root encoding plus24 flow increments | 3 | 924 | 1408 | 6742.5 | 1783 | 888 |
| Materialize all32 addresses with flow.add_imm | 236 | 923 | 1416 | 6742.625 | 1775 | 896 |
| All32 immediate addresses plus root alias | 3 | 923 | 1422 | 6742.375 | 1775 | 896 |
| All32 immediate addresses plus direct root encoding | 3 | 923 | 1416 | 6742.5 | 1775 | 896 |
| Only8 anchors use flow immediates; retain24 ALUs | 3 | 927 | 1464 | 6745.625 | 1775 | 872 |

Every measured row passes exact primitive emission/capacity/dependencies,
physical scratch provenance, frozen seeds123/456/789, full-word final values,
exact workspace contents and workspace address checks. The local provenance
adapter adds only the original add_imm operands: destination write at1 and
source read at2. Its immediate operand is not a scratch read. No common
verifier, simulator, tests or production file was changed by these probes.

The923 candidate retains the existing winning scheduling policy
`fragment_adaptive_tail_hetero_360_240_240_220_900`. Flow starts at0 and ends
at896, with only one hole within that inclusive span. Combined lookup first/
last is51/912, followed by the unchanged10-cycle drain. Its VALU5414 and
ALU10629 preserve the measured6742.625 weighted count. The physical VALU
floor remains903; the aggregate weighted floor remains900. This is a
one-cycle elapsed improvement, not a demonstrated900 schedule.

The root alias variant retains raw root in original top-node lane0 and
encoded root in otherwise-unused lane7. Moving later depth3 setup into a
new8-word buffer removes two root copies but adds six net static scratch
words. It ties elapsed performance, so it need not accompany the address
change.

Before implementation, the remaining512 right-child scalar copies were
costed. Removing them by a second gather would add256 loads per level,
already pushing total2039 and a1020 load floor after only one level. One
contiguous vload cannot directly land both compact child vectors when
source fields are two words apart and destination vectors are eight words
apart; widening all records enough would exceed256 workspace words. Those
obviously negative forms were rejected without implementing or timing them.
This argument does not rule out every alternative record representation.

After the resource-plus-tail audit identified the new graph's910 flow
bound, the opposite hybrid was tested: put only the eight original anchors
on flow and keep24 ALU increments. This retains first flow0/lookup51 while
lowering flow896→872, but ends flow901/lookup916 and takes927. The other two
screen policies take931; no full search was claimed for this rejected case.
Removing a necessary resource-bound obstruction does not guarantee a faster
schedule; its chain costs and compute pressure still matter.

The minimal32-immediate form was subsequently integrated behind
`COMPACT_INPUT_IMMEDIATE`, gated by compact flow exchange. The production
on/off comparison passes exact instruction/logical-slot/dependency/lane/
allocation equivalence and all236 policy timings against the pinned probes.
The shared local dataflow verifier now models only add_imm's actual source
and destination, with positive and wrong-source/wrong-destination fixtures.
Both logical and physical write sets confirm the zero source never changes.
Fresh production official9/9, built-in3/3 and comprehensive regression pass.

Reproduce:

```
python3 -B experiments/iteration41_transfer.py --inputs-immediate --full-policies
python3 -B experiments/iteration41_transfer.py --inputs-flow --full-policies
python3 -B experiments/iteration41_transfer.py --root alias --full-policies
python3 -B experiments/iteration41_transfer.py --anchor-immediate
python3 -B experiments/iteration41_transfer_acceptance.py
```
