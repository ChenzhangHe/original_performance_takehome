# Iteration41: shorter hash topologies and exact early parity

Pinned source: `109610180033baa744a0fe80a2501e1ba2f73b00`, 924 cycles,
scratch1424. No hash change is recommended for production. Eight explicit
three-instruction families are impossible; an exact early-parity identity
works but does not improve elapsed cycles, including when combined with
the independently accepted 923-cycle input-address transform.

This investigation changes only `iteration41_hash*` experiment files. It
does not modify production, common verifiers, tests, the simulator, input
data, instruction semantics, or Git state.

## Eight genuinely shorter expression families

All arithmetic below wraps at 32 bits. Let x be the output of hash stage 1:

```
L = 0xe9f8cc1d
R = 0xaccf6200
C = 0xfd7046c5
F(x) = 9 * ((33*x + L) XOR (16896*x + R)) + C
```

The existing implementation of F costs three affine multiply-adds and one
XOR, or four instructions. The new search asks whether either of these
three-instruction topologies can implement exactly F:

```
y = x XOR k
Parallel: (m1*y + b1) OP (m2*y + b2)
Serial:   m2 * ((m1*y + b1) OP y) + b2
OP in {XOR, AND, OR, multiplication}
```

Every `m1,b1,m2,b2,k` is an arbitrary 32-bit constant, without parity or
sign restrictions. The input XOR with k is granted free because the
previous hash stage already contains a constant XOR that could be changed.
Each parallel expression costs two MACs and one binary instruction; each
serial expression costs one MAC, one binary instruction, and another MAC.
Both are distinct from iteration40's four-instruction raw-output-constant
absorption family: the target here remains F, and one instruction must go.

`iteration41_hash_topology.py` uses Z3 4.15.4 and checks 96 necessary
equations at width 16, with a 12-second limit per family. Measured results:

| Family | Result | Seconds |
| --- | --- | ---: |
| Parallel XOR | UNSAT | 0.700 |
| Parallel AND | UNSAT | 0.483 |
| Parallel OR | UNSAT | 0.384 |
| Parallel multiplication | UNSAT | 0.370 |
| Serial XOR | UNSAT | 1.464 |
| Serial AND | UNSAT | 0.255 |
| Serial OR | UNSAT | 0.253 |
| Serial multiplication | UNSAT | 1.759 |

These are exact exclusions of the eight stated 32-bit families. Truncation
to the low 16 bits commutes with addition, multiplication, XOR, AND, and OR.
Consequently every 32-bit parameter solution would induce a 16-bit solution
of every sampled necessary equation. There is no parameter assignment
satisfying even those 96 equations, so no assignment works for every 32-bit x.
The parameters were not restricted to a finite list of candidate constants.
The full-word domain is appropriate: the preceding odd affine transform,
right-xorshift, and constant XOR are each bijections on 32-bit words.

This does not exclude other expression topologies, additional intermediates,
variable shifts, division, selections, cross-round state representations,
or a shorter complete hash that does not preserve this F boundary. It is
not a global optimality proof. A satisfiable low-bit result would only be
a survivor, not an accepted 32-bit implementation; the script labels it so.

## Exact two-instruction path parity

Let z be the middle XOR result and define:

```
F = 9*z + C
p = (F XOR (F >> 16)) & 1
```

The encoded hash parity p has the exact alternative:

```
p = ((0x80048000*z + 0xa3628000) & 0xffffffff) >> 31
```

Here is the carry-aware derivation. Since C is odd, bit 0 of F equals
bit 0 of z+1. Define `H = F + 2^16*(z+1)`, modulo 2^32. The added term has zero
low 16 bits, so it produces no carry into bit 16 from below. At bit 16 it
toggles that bit of F precisely when bit 0 of z+1 is 1. Therefore bit 16 of H is p,
regardless of carries into higher bits. Expanding H gives
`H = 65545*z + 0xfd7146c5`. Moving bit 16 to bit 31 by a wrapping left shift
of 15 and folding that shift into the affine constants gives the formula
above. A logical right shift by 31 returns exactly the required 0/1 value.

`iteration41_hash_parity.py --proof-only` also checks the full 32-bit
counterexample query with Z3 4.15.4; it is UNSAT. The proof quantifies over
every 32-bit z, not generated 30-bit inputs or sampled output values.

The existing dependency path from z to p is MAC, shift, XOR, mask: four
instructions. The alternative branch is MAC then shift: two instructions.
The complete hash must still be computed, so this is a latency opportunity,
not an arithmetic saving. Per selected eight-lane group/round it adds two
vector operations and removes eight scalar mask operations: net +1 weighted
compute equivalent. The probe additionally materializes three new vector
constants with three scalar loads and three broadcasts. Thus N selected
group/round pairs cost +N+3 weighted equivalents and +3 loads, before any
independent input-address transform. All those costs are included below.

The implementation uses separate virtual vectors for the branch, full F,
and final shift arm. The original full-hash write waits for the branch's
read of z; the early next lookup cannot overwrite the old shift temporary.
All new virtual vectors are covered by physical lifetime allocation.
No copy or hazard is silently omitted. The optional late-constants control
changes only the scheduling metadata of the six new constant setup
operations to the selected consumers' actual cohort/round.

## Bounded runtime results

Rounds 13/14 are the final traversal's depths 2/3. Groups 0/1 are the last
finishing cohorts. All ordinary screens use the same three existing
fragment policies, including the accepted924 policy. The final combination
uses all 236 existing policies exactly once.

| Input addresses | Groups | Rounds | New constants | Policies | Cycles | Scratch | Weighted compute |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| Pinned | None | None | None | 3 | 924 | 1424 | 6745.625 |
| Pinned | 0 | 14 | Ordinary setup | 3 | 925 | 1464 | 6749.625 |
| Pinned | 0,1 | 13,14 | Ordinary setup | 3 | 925 | 1464 | 6752.625 |
| Pinned | 0 | 13 | Late priority | 3 | 924 | 1448 | 6749.625 |
| Pinned | 0 | 14 | Late priority | 3 | 924 | 1448 | 6749.625 |
| Pinned | 0 | 13,14 | Late priority | 3 | 924 | 1448 | 6750.625 |
| Pinned | 0,1 | 14 | Late priority | 3 | 924 | 1448 | 6750.625 |
| Pinned | 0,1 | 13,14 | Late priority | 3 | 925 | 1448 | 6752.625 |
| Independent add_imm | 0 | 14 | Late priority | 236 | 923 | 1440 | 6746.625 |

Every table row passes exact primitive emission/capacity checks, physical
scratch provenance, exhaustive workspace-address checks, frozen simulator
seeds 123/456/789, and the full-word output/workspace fixture. These checks
validate the retained shortest schedule, not every discarded policy's
allocated program. No official-test or 32-seed regression claim is made for
the rejected parity candidate.

For the ordinary-setup group0/round14 screen, the earlier branch leaves the
final gather ready at910, equal to the pinned control; its earlier hash
prefix moved two cycles later. Load contention then finishes the gather
at914 instead of913. Giving the new constants late priorities restores the
924 finish, with group0's final gather at913 and store at923. Earlier path
readiness alone does not establish an earlier finish.

The final composition calls `iteration41_transfer.make_source` with only
`inputs_immediate=True`, then applies exactly group0/round14/late-constants.
The address transform replaces eight constant loads and 24 scalar address
operations with 32 independent original-ISA `flow.add_imm` instructions.
No root alias or other transfer variant is included. Its total slots with
the parity branch are load1778, flow896, VALU5419, ALU10621, store64.
Group0's final gather is912 and final store922. The winning policy remains
`fragment_adaptive_tail_hetero_360_240_240_220_900`.

The independently accepted address transform already reaches923 cycles.
This full-policy combination only ties923 while adding hash work and
scratch, so the parity branch is rejected for integration. No further
variants were run after that final bounded combination.

## Reproduction

The optional solver is pinned to `z3-solver==4.15.4.0`, available for this
run in `/private/tmp/perf-iteration40-encoding.1zQEZf`; no project or system
dependency is added. With that directory on PYTHONPATH:

```sh
python3 -B experiments/iteration41_hash_topology.py --seconds 12
python3 -B experiments/iteration41_hash_parity.py --proof-only
```

Runtime screens need no solver dependency:

```sh
python3 -B experiments/iteration41_hash_parity.py --groups
python3 -B experiments/iteration41_hash_parity.py --groups 0 --rounds 14
python3 -B experiments/iteration41_hash_parity.py --groups 0 1 --rounds 13 14
python3 -B experiments/iteration41_hash_parity.py --groups 0 --rounds 13 --late-constants
python3 -B experiments/iteration41_hash_parity.py --groups 0 --rounds 14 --late-constants
python3 -B experiments/iteration41_hash_parity.py --groups 0 --rounds 13 14 --late-constants
python3 -B experiments/iteration41_hash_parity.py --groups 0 1 --rounds 14 --late-constants
python3 -B experiments/iteration41_hash_parity.py --groups 0 1 --rounds 13 14 --late-constants
python3 -B experiments/iteration41_hash_parity.py --groups 0 --rounds 14 --late-constants --inputs-immediate --full-policies
```
