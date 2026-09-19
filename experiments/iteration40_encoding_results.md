# Iteration40: raw-node encoding has a concrete cost barrier

Pinned production source: `ee87c658e6247232801432d7ab5a548d324c096d`,927 cycles.
This investigation changes no kernel, common verifier, official test,
simulator, instruction semantics, data or Git state. There is no new runtime
candidate or performance claim.

## Storage and representation accounting

Depth8/9/10 currently require a gathered raw node to be XOR-encoded before
combining it with the encoded value. Across32 groups, each depth costs32
weighted compute equivalents for that encoding:96 total.

Merely switching the value state to raw does not remove work: it replaces
each node-encoding XOR with the previous hash's explicit final-constant XOR.
Moving that XOR across a round boundary can change readiness, but is not a
net arithmetic saving.

Pre-encoding the entire depth8 level likewise does not save arithmetic:

| Full-level encoding | Nodes | Setup vector XORs | Removed body vector equivalents |
| --- | ---: | ---: | ---: |
| Depth8 | 256 | 32 | 32 |
| Depth9 | 512 | 64 | 32 |
| Depth10 | 1024 | 128 | 32 |

The depth8 case additionally needs32 preprocessing vloads and32 vstores,
consumes the whole256-word index workspace, and displaces the existing
useful depth4/5 and6/7 records. Reusing that workspace after depth7 requires
barriers and restoring records for the final shallow traversal; it does not
change the zero arithmetic saving of the depth8 copy itself. These counts
do not rule out every partial or dynamic cache, but such a design must pay
for routing, tags, movement and arbitrary-input correctness explicitly.

## A wider, costed state-expression search

A genuinely shorter raw-output hash would avoid that arithmetic trade.
Unlike the earlier iteration37 templates, this search keeps all THREE final
affine instructions and their intervening XOR, and asks whether changing
their constants can absorb the final hash XOR without adding instructions.

All equations use32-bit wrapping arithmetic. Define

```
T16(y) = y XOR (y >> 16)
L = 0xe9f8cc1d
R = 0xaccf6200
B = 0xfd7046c5
C = 0xb55a4f09
Kout = T16(C) = 0xb55afa53
F(x) = 9 * ((33*x + L) XOR (16896*x + R)) + B
```

`T16` is its own inverse, so producing the raw hash output
`T16(F(x)) XOR C` without its extra constant-XOR is equivalent to producing
`F(x) XOR Kout` before the existing shift/XOR pair.

The broadest template tested is

```
G(x) = m3 * ((m1*(x XOR k) + b1) XOR (m2*(x XOR k) + b2)) + b3
G(x) == F(x) XOR Kout   for every32-bit x
```

Every `m1,m2,m3,b1,b2,b3,k` is an arbitrary32-bit constant. The `x XOR k`
costs no additional instruction in this window: stage1 already XORs a
constant, which could be replaced by its XOR with `k`. Thus a valid solution
would be a genuine raw-output opportunity, not hiding an uncounted operation.

Necessary parity restrictions are sound, not heuristic pruning. Since the
target's output bit0 changes with input bit0, `m3` must be odd and exactly
one of `m1,m2` must be odd. XOR is commutative, so orienting `m1` odd and `m2`
even loses no candidate.

### Results with Z3 4.15.4

| Template | Result | Time |
| --- | --- | ---: |
| Fixed multipliers33/16896/9; all three biases free | UNSAT on26 constraints | 0.017s |
| Above plus free stage1 XOR encoding | UNSAT on26 constraints | 0.085s |
| Above plus arbitrary odd outer multiplier | UNSAT on26 constraints | 7.783s |
| All multipliers/biases and encoding free, direct32-bit query | Timeout; inconclusive alone | 30.071s |
| All free, necessary low16-bit equations on96 inputs | **UNSAT** | **14.392s** |

A fresh final rerun reproduced the low16-bit UNSAT result in15.457s.

The last result excludes the entire broad32-bit template, not merely some
sampled parameter values. Any full32-bit solution must reduce to a solution
of these low16-bit equations. Since no parameters satisfy even the96 chosen
necessary equations, none can satisfy the universal32-bit identity. SAT at
low width would NOT have been an equivalence proof; the script labels such a
result only as a low-bit survivor, never a kernel candidate.

This is NOT a proof that ten hash operations are globally optimal. It rules
out this specific existing operation topology with arbitrary constants and
free stage1 XOR encoding. To pursue raw-output absorption next, change the
expression topology, use another intermediate representation, or identify
real cross-item reuse; tuning these constants alone cannot work.

No candidate survived to justify an implementation, so no new frozen-kernel
score or correctness suite is reported for this search.

## Reproduction

`experiments/iteration40_encoding_state.py` needs the optional pinned
`z3-solver==4.15.4.0`, installed only into a temporary directory. There is no
project or system dependency addition. For example, with that package on
`PYTHONPATH`:

```
python3 -B experiments/iteration40_encoding_state.py --seconds 30 --free-arms
python3 -B experiments/iteration40_encoding_state.py --seconds 30 --truncated 16
```

The actual package directory for this run was
`/private/tmp/perf-iteration40-encoding.1zQEZf`. The old iteration37 temporary
environment was incomplete; it was not treated as a usable solver install.
