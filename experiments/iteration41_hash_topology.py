"""Bounded necessary-condition search for genuinely shorter hash topologies.

The current middle suffix is three affine multiply-adds plus one XOR.
Each family here uses TWO affine instructions plus one binary instruction,
with either parallel affine arms or an affine/binary/affine chain. Arbitrary
input XOR encoding is free only because the previous stage already contains
one constant XOR. No sampled SAT model is accepted as a 32-bit identity.

Optional dependency: z3-solver==4.15.4.0, outside the project environment.
No production, simulator, input, common verifier, or Git changes.
"""
import argparse
import json
import random
import time

import z3

SOURCE_REF = "109610180033baa744a0fe80a2501e1ba2f73b00"
LEFT, RIGHT, BIAS = 0xE9F8CC1D, 0xACCF6200, 0xFD7046C5


def combine(op, left, right):
    return {"xor": lambda: left ^ right, "and": lambda: left & right,
            "or": lambda: left | right, "mul": lambda: left * right}[op]()


def search(family, width, seconds, samples):
    """UNSAT rejects this entire full32-bit family; SAT is inconclusive."""
    topology, op = family.split("_")
    assert topology in ("parallel", "serial") and 10 <= width <= 32
    start = time.monotonic()
    m1, b1, m2, b2, encoding = z3.BitVecs("m1 b1 m2 b2 encoding", width)
    solver = z3.SolverFor("QF_BV")
    mask = (1 << width) - 1
    rng = random.Random(1041)
    inputs = sorted({value & mask for value in list(range(33)) +
                     [(1 << bit) + delta for bit in range(width) for delta in (-1, 0, 1)] +
                     [rng.getrandbits(width) for _ in range(samples)]})
    for value in inputs:
        x = z3.BitVecVal(value, width)
        z = x ^ encoding
        if topology == "parallel":
            candidate = combine(op, m1*z+b1, m2*z+b2)
        else:
            candidate = m2*combine(op, m1*z+b1, z)+b2
        target = 9*((33*x+LEFT) ^ (16896*x+RIGHT))+BIAS
        solver.add(candidate == target)
    solver.set(timeout=max(1, int((seconds-(time.monotonic()-start))*1000)))
    answer = solver.check()
    status = ("proved_no_32bit_solution_in_family" if answer == z3.unsat else
              "necessary_conditions_survive_not_a_candidate" if answer == z3.sat else
              "inconclusive_solver")
    result = dict(family=family, source_ref=SOURCE_REF, z3_version=z3.get_version_string(),
                  necessary_low_bits=width, sample_constraints=len(inputs),
                  operation_count=3, baseline_operation_count=4,
                  free_stage1_xor_encoding=True, status=status,
                  reason=solver.reason_unknown() if answer == z3.unknown else None,
                  seconds=round(time.monotonic()-start, 3))
    if answer == z3.sat:
        model = solver.model()
        result["low_bit_parameters_not_a_candidate"] = {
            str(p): model.eval(p, model_completion=True).as_long()
            for p in (m1, b1, m2, b2, encoding)}
    print(json.dumps(result), flush=True)
    return result


def main():
    families = [f"{topology}_{op}" for topology in ("parallel", "serial")
                for op in ("xor", "and", "or", "mul")]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", nargs="+", choices=families, default=families)
    parser.add_argument("--width", type=int, default=16)
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--samples", type=int, default=32)
    args = parser.parse_args()
    assert args.seconds > 0 and args.samples >= 0
    for family in args.family:
        search(family, args.width, args.seconds, args.samples)


if __name__ == "__main__":
    main()
