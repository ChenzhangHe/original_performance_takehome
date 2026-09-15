"""Bounded 32-bit CEGIS for new hash/state-encoding fusion templates.

Requires z3-solver==4.15.4.0; production has no added dependency.
UNSAT excludes only the named template. Timeout is inconclusive, not failure.
No candidate is accepted merely because it passes sampled inputs.
"""
import argparse
import json
import time

import z3

MASK = (1 << 32)-1
C0, C1, C2, C3, C4 = 0x7ed55d16, 0xc761c23c, 0x165667b1, 0xd3a2646c, 0xfd7046c5


def t(x, shift):
    return x ^ z3.LShR(x, shift)


def synthesize(name, target, candidate, parameters, constraints, budget):
    start = time.monotonic()
    x = z3.BitVec('x', 32)
    solver = z3.SolverFor('QF_BV')
    solver.add(*constraints)
    samples = list(dict.fromkeys([0, 1, 2, 3, MASK] +
                                [((1 << bit)+d)&MASK for bit in (4, 9, 12, 16, 19, 24, 31) for d in (-1, 0, 1)]))
    for value in samples:
        solver.add(candidate(z3.BitVecVal(value, 32)) == target(z3.BitVecVal(value, 32)))
    counterexamples = []
    result = dict(template=name, seed_constraints=len(samples))
    for iteration in range(64):
        remaining = budget-(time.monotonic()-start)
        if remaining <= 0:
            result.update(status='inconclusive_budget')
            break
        solver.set(timeout=max(1, int(remaining*1000)))
        answer = solver.check()
        if answer == z3.unsat:
            result.update(status='proved_no_solution_in_template')
            break
        if answer == z3.unknown:
            result.update(status='inconclusive_solver', reason=solver.reason_unknown())
            break
        model = solver.model()
        values = [(param, model.eval(param, model_completion=True)) for param in parameters]
        concrete = z3.substitute(candidate(x), *values)
        verifier = z3.SolverFor('QF_BV')
        verifier.set(timeout=max(1, int((budget-(time.monotonic()-start))*1000)))
        verifier.add(concrete != target(x))
        checked = verifier.check()
        if checked == z3.unsat:
            result.update(status='proved_equivalent_candidate', parameters={str(p): v.as_long() for p, v in values})
            break
        if checked == z3.unknown:
            result.update(status='inconclusive_verifier', reason=verifier.reason_unknown())
            break
        counterexample = verifier.model().eval(x, model_completion=True).as_long()
        counterexamples.append(counterexample)
        word = z3.BitVecVal(counterexample, 32)
        solver.add(candidate(word) == target(word))
    else:
        result.update(status='inconclusive_iteration_limit')
    result.update(counterexamples=counterexamples, seconds=round(time.monotonic()-start, 3))
    print(json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=15, help='Per-template solver budget')
    args = parser.parse_args()
    assert args.seconds > 0
    print(json.dumps(dict(z3_version=z3.get_version_string(), width=32)), flush=True)
    results = []
    for free_multiplier in (False, True):
        k, b, a = z3.BitVecs('input_encoding bias multiplier', 32)
        target = lambda x: t(4097*x+C0, 19) ^ C1
        multiplier = a if free_multiplier else 4097
        candidate = lambda x: t(multiplier*(x ^ k)+b, 19)
        params = [k, b, a] if free_multiplier else [k, b]
        constraints = [a & 1 == 1] if free_multiplier else []
        results.append(synthesize('first_pair_free_input_xor_' + ('free_odd_multiplier' if free_multiplier else 'fixed_multiplier'),
                                  target, candidate, params, constraints, args.seconds))

    # Broaden the old finite bias enumeration to ALL pairs of 32-bit biases.
    # This still covers only these specified multiplier pairs, not all programs.
    for m1, m2 in ((297, 152064), (-297, 152064), (297, -152064), (-297, -152064)):
        b1, b2 = z3.BitVecs('left_bias right_bias', 32)
        target = lambda x: 9*((33*x+C2+C3) ^ (16896*x+(C2 << 9)))+C4
        candidate = lambda x: (m1*x+b1) ^ (m2*x+b2)
        results.append(synthesize(f'middle_three_mac_to_two_{m1}_{m2}', target, candidate,
                                  [b1, b2], [], args.seconds))

    # A two-round boundary, even granting free arbitrary encoding for node=0:
    # M4097(T16(M9(x))) -> T16(Ma(x XOR k)). The full transform must
    # handle node=0, so disproving this slice rejects this particular template.
    # A survivor here is NOT a complete node-dependent or two-round solution.
    for shift in (16, 19):
        k, b, a = z3.BitVecs('bridge_encoding bridge_bias bridge_multiplier', 32)
        target = lambda x: 4097*t(9*x+C4, 16)+C0
        candidate = lambda x: t(a*(x ^ k)+b, shift)
        results.append(synthesize(f'cross_round_bridge_node_zero_output_T{shift}', target, candidate,
                                  [k, b, a], [a & 1 == 1], args.seconds))
    print(json.dumps(dict(summary={status: sum(r['status'] == status for r in results)
                                  for status in sorted({r['status'] for r in results})},
                          scope='Eight named templates, NOT hash optimality or exhaustive state search')), flush=True)


if __name__ == '__main__':
    main()
