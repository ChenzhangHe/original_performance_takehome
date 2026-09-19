"""Prove or reject costed raw-output hash-state templates.

Pinned kernel: ee87c658e6247232801432d7ab5a548d324c096d (927 cycles).
Needs z3-solver==4.15.4.0, outside production. These are NEW templates: keep
the existing three final affine instructions and XOR, but change all their
biases to absorb the final output constant. Optionally alter stage1's
existing XOR constant (free input XOR encoding for this window).

No sampled survivor counts as a candidate: full32-bit solver verification
must succeed. UNSAT proves only the explicitly named template impossible.
"""
import argparse
import json
from pathlib import Path
import random
import sys
import time

import z3

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from experiments.iteration37_state_search import synthesize

SOURCE_REF = 'ee87c658e6247232801432d7ab5a548d324c096d'
C2, C3, C4, C5 = 0x165667b1, 0xd3a2646c, 0xfd7046c5, 0xb55a4f09
MASK = (1 << 32)-1
LEFT_BIAS = (C2+C3)&MASK
RIGHT_BIAS = (C2 << 9)&MASK
# T16(y)=y^(y>>16) is its own inverse. Raw output T16(y)^C5
# is therefore equivalent to T16(y^K), K=T16(C5).
OUTPUT_MASK = C5 ^ (C5 >> 16)


def truncated_necessary_condition(width, seconds):
    """UNSAT here excludes the all-free32-bit template; SAT does not prove it."""
    assert 2 <= width <= 32
    start = time.monotonic()
    solver = z3.SolverFor('QF_BV')
    b1, b2, b3, encoding, m3, m1, m2 = z3.BitVecs('b1 b2 b3 encoding m3 m1 m2', width)
    solver.add(m3 & 1 == 1, m1 & 1 == 1, m2 & 1 == 0)
    mask = (1 << width)-1
    rng = random.Random(1040)
    samples = sorted({value & mask for value in list(range(33)) +
                      [(1 << bit)+delta for bit in range(width) for delta in (-1, 0, 1)] +
                      [rng.getrandbits(width) for _ in range(32)]})
    for value in samples:
        x = z3.BitVecVal(value, width)
        argument = x ^ encoding
        candidate = m3*((m1*argument+b1) ^ (m2*argument+b2))+b3
        target = (9*((33*x+LEFT_BIAS) ^ (16896*x+RIGHT_BIAS))+C4) ^ OUTPUT_MASK
        solver.add(candidate == target)
    solver.set(timeout=max(1, int((seconds-(time.monotonic()-start))*1000)))
    result = solver.check()
    status = ('proved_no_32bit_solution_in_all_free_template' if result == z3.unsat else
              'low_bits_satisfiable_not_a_candidate' if result == z3.sat else 'inconclusive_solver')
    print(json.dumps(dict(template='all_free_three_affines_and_stage1_encoding',
                          necessary_low_bits=width, sample_constraints=len(samples),
                          status=status, reason=solver.reason_unknown() if result == z3.unknown else None,
                          seconds=round(time.monotonic()-start, 3))), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--free-arms', action='store_true', help='Also grant arbitrary odd/even branch multipliers')
    parser.add_argument('--truncated', type=int, choices=(12, 16),
                        help='Only check a necessary low-bit condition on the all-free template')
    args = parser.parse_args()
    if args.truncated:
        truncated_necessary_condition(args.truncated, args.seconds)
        return
    print(json.dumps(dict(source_ref=SOURCE_REF, z3_version=z3.get_version_string(),
                          input_width=32, output_mask=hex(OUTPUT_MASK))), flush=True)
    results = []
    templates = [(False, False, False), (True, False, False), (True, True, False)]
    if args.free_arms:
        templates.append((True, True, True))
    for free_encoding, free_outer_multiplier, free_arm_multipliers in templates:
        b1, b2, b3, encoding, multiplier, m1, m2 = z3.BitVecs(
            'left_bias right_bias final_bias stage1_xor_encoding final_multiplier left_multiplier right_multiplier', 32)
        target = lambda x: (9*((33*x+LEFT_BIAS) ^ (16896*x+RIGHT_BIAS))+C4) ^ OUTPUT_MASK
        def candidate(x):
            argument = x ^ encoding if free_encoding else x
            scale = multiplier if free_outer_multiplier else 9
            left_scale, right_scale = (m1, m2) if free_arm_multipliers else (33, 16896)
            return scale*((left_scale*argument+b1) ^ (right_scale*argument+b2))+b3
        params = ([b1, b2, b3] + ([encoding] if free_encoding else [])
                  + ([multiplier] if free_outer_multiplier else []) + ([m1, m2] if free_arm_multipliers else []))
        constraints = [multiplier & 1 == 1] if free_outer_multiplier else []
        if free_arm_multipliers:
            # Output bit0 must vary with input bit0. The branch multipliers
            # therefore have opposite parity; XOR commutativity gives this
            # orientation without losing the swapped odd/even case.
            constraints.extend([m1 & 1 == 1, m2 & 1 == 0])
        name = ('raw_output_three_affines_free_biases' + ('_free_stage1_encoding' if free_encoding else '')
                + ('_free_odd_outer_multiplier' if free_outer_multiplier else '')
                + ('_free_arm_multipliers' if free_arm_multipliers else ''))
        results.append(synthesize(name, target, candidate, params, constraints, args.seconds))
    print(json.dumps(dict(summary={status: sum(r['status'] == status for r in results)
                                  for status in sorted({r['status'] for r in results})},
                          scope=f'{len(templates)} explicit raw-output templates, not hash optimality')), flush=True)


if __name__ == '__main__':
    main()
