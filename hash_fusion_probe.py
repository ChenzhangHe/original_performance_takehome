"""Reject or flag short hash-fusion templates; never alters the kernel.

This is a bounded candidate filter, NOT a proof of hash optimality. A concrete
counterexample disproves a candidate. Any survivor still needs a full 32-bit
equivalence proof before use. Run: python3 -B hash_fusion_probe.py
"""

import json
import random

from problem import HASH_STAGES


MASK = (1 << 32) - 1


def affine(x, multiplier, bias):
    return (x * multiplier + bias) & MASK


def xor_shift(x, shift):
    return x ^ (x >> shift)


def inverse_xor_shift(x, shift):
    result = x
    for amount in range(shift, 32, shift):
        result ^= x >> amount
    return result


def affine_absorption(multiplier, bias, constant, shift, samples):
    """Can T(m*x+b)^c become T(k*x+d), saving a constant-XOR?

    T is invertible. Values at x=0 and x=1 uniquely determine k,d, so a
    counterexample rules out this entire single-affine-before-T template.
    """
    def target(x):
        return xor_shift(affine(x, multiplier, bias), shift) ^ constant

    candidate_bias = inverse_xor_shift(target(0), shift)
    candidate_multiplier = (inverse_xor_shift(target(1), shift) - candidate_bias) & MASK
    for x in samples:
        candidate = xor_shift(affine(x, candidate_multiplier, candidate_bias), shift)
        expected = target(x)
        if candidate != expected:
            return dict(status="rejected", multiplier=candidate_multiplier,
                        bias=candidate_bias, counterexample=x,
                        expected=expected, actual=candidate)
    return dict(status="unproven_survivor", multiplier=candidate_multiplier,
                bias=candidate_bias)


def main():
    rng = random.Random(1037)
    samples = list(dict.fromkeys(
        list(range(64)) + [MASK] +
        [((1 << bit) + delta) & MASK for bit in range(32) for delta in (-1, 0, 1)] +
        [rng.getrandbits(32) for _ in range(4096)]
    ))
    first = affine_absorption(4097, HASH_STAGES[0][1], HASH_STAGES[1][1], 19, samples)
    last = affine_absorption(9, HASH_STAGES[4][1], HASH_STAGES[5][1], 16, samples)
    print(json.dumps(dict(window="stages_0_1", result=first)))
    print(json.dumps(dict(window="stages_4_5_raw_output", result=last)))

    # Existing stages 2/3/4: two MACs, XOR, then MAC by 9. Try absorbing
    # the last MAC into two affine arms followed by XOR (three instructions).
    left_bias = (HASH_STAGES[2][1] + HASH_STAGES[3][1]) & MASK
    right_bias = (HASH_STAGES[2][1] << 9) & MASK
    final_bias = HASH_STAGES[4][1]
    targets = [affine(affine(x, 33, left_bias) ^ affine(x, 16896, right_bias),
                      9, final_bias) for x in samples]
    multipliers = sorted({m & MASK for m in (33, 16896, 297, 152064,
                                             -33, -16896, -297, -152064)})
    bases = {0, 1, MASK, 1 << 31, left_bias, right_bias, final_bias}
    bases.update(stage[1] for stage in HASH_STAGES)
    biases = set()
    for b in bases:
        for scale in (1, 9, -1, -9):
            for offset in (0, final_bias, -final_bias):
                biases.add((scale * b + offset) & MASK)
    survivors = []
    count = 0
    for m1 in multipliers:
        for m2 in multipliers:
            for b1 in sorted(biases):
                # x=0 forces the other intercept; no independent bias sweep.
                b2 = b1 ^ targets[0]
                count += 1
                if all((affine(x, m1, b1) ^ affine(x, m2, b2)) == target
                       for x, target in zip(samples, targets)):
                    survivors.append((m1, b1, m2, b2))
    print(json.dumps(dict(window="stages_2_3_4", candidates=count,
                          samples=len(samples), unproven_survivors=survivors)))


if __name__ == "__main__":
    main()
