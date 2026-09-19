# Iteration39: limited arithmetic-resource exchange

Pinned baseline: `161c60200be0aa071532f6784817a5b562e9c2c8`, 928 cycles,
scratch1440, weighted compute6745.625, load1783, flow864. No production,
common verifier, simulator, official test or Git edits by this experiment.

The original ISA offers scalar `flow.add_imm`. Replacing an address-chain
ALU with it saves0.125 weighted compute equivalents and spends one flow
slot. Flow is initially idle, but adding operations does not guarantee that
they issue in those empty cycles. An alternative is to materialize a known
setup pointer using `load.const`, exchanging one ALU slot for one load slot.
All such constants are runtime-independent addresses, not answer data.

| Candidate | Policies | Cycles | Scratch | Compute | Loads | Flow |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Input highest8 groups;6 add_imm | 3 | 931 | 1456 | 6744.875 | 1783 | 870 |
| Input highest16 groups;12 add_imm | full | 930 | 1432 | 6744.125 | 1783 | 876 |
| Input all32 groups;24 add_imm | full | 929 | 1440 | 6742.625 | 1783 | 888 |
| First8 setup increments to add_imm | 3 | 930 | 1424 | 6744.625 | 1783 | 872 |
| First16 setup increments to add_imm | full | 930 | 1432 | 6743.625 | 1783 | 880 |
| Highest16 input groups + first8 setup | 3 | 930 | 1432 | 6743.125 | 1783 | 884 |
| First8 setup increments to const loads | full | 931 | 1424 | 6744.625 | 1791 | 864 |
| First16 setup increments to const loads | full | 930 | 1424 | 6743.625 | 1799 | 864 |
| Encode root directly into retained scalar | full | 928 | 1440 | 6745.5 | 1783 | 864 |

Every row passes exact emission, physical scratch provenance, independent
frozen seeds123/456/789 and the full-word output/workspace fixture. The
three-policy screen includes the baseline928 winning policy as well as
the two historical fragment policies. Structural candidates closest to928
were rerun with the full existing policy set. None beats928.

The root-copy control replaces `encode top_nodes[0]; copy encoded root`
with a single direct encoded-root write. It removes one scalar ALU and ties
the baseline; this exact local cleanup has been considered on older graphs,
so it is not a new algorithmic result or a reason to change production.

The probe extends only its in-memory provenance access declaration for
`flow.add_imm`: one scratch read and one scratch write; its immediate is not
a scratch operand. Production `instruction_accesses` and the frozen ISA
already support this instruction. No common verifier is weakened or changed.

Reproduction, from the repo root:

```
python3 -B experiments/iteration39_compute.py --inputs 16 --full-policies
python3 -B experiments/iteration39_compute.py --inputs 32 --full-policies
python3 -B experiments/iteration39_compute.py --setup 16 --full-policies
python3 -B experiments/iteration39_compute.py --setup-loads 8 --full-policies
python3 -B experiments/iteration39_compute.py --setup-loads 16 --full-policies
python3 -B experiments/iteration39_compute.py --root-copy --full-policies
```

Counting setup constant requests found no repeated vector value broadcasts
to alias away. Setup already fuses node encoding into required scalar record
transposes. The remaining two right-child vector copies per group cost64
equivalents overall, but the current overlapping-load method lands only one
child vector directly: removing the other copy needs a different concrete
layout/lifetime construction, not deletion of a redundant instruction.

Conclusion: additional engine capacity is not automatically useful capacity.
Tiny work reductions that spend flow or load readiness can regress elapsed
time. Preserve928; do not present any of these rows as a performance win.
