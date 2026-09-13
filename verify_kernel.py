"""Local schedule and full-word regression checks, separate from official tests.

Run: python3 verify_kernel.py
Uses the frozen simulator/reference; does not modify tests or their inputs.
"""

from collections import Counter
import json
import random

from analyze_kernel import AnalyzedKernel, analyze
from problem import SLOT_LIMITS, VLEN
from tune_kernel import check, Input, Machine, Tree, build_mem_image, reference_kernel2
from frozen_problem import HASH_STAGES


def verify_emission(builder):
    """Reconstruct every primitive slot from logical operations and lane times.

    This catches dropped/duplicate lanes, renamed-address mismatches, early
    consumers, and capacity overruns after cross-cycle lifetime allocation.
    """
    reconstructed = [Counter() for _ in builder.instrs]
    for op_id, op in enumerate(builder.operations):
        first = builder.issue_first_cycles[op_id]
        last = builder.issue_cycles[op_id]
        assert all(first > builder.issue_cycles[d] for d in op["deps"])
        if op_id in builder.offloaded_ops:
            times = builder.lane_issue_cycles[op_id]
            assert len(times) == VLEN and min(times) == first and max(times) == last
            opcode, dest, left, right = op["slot"]
            for lane, cycle in enumerate(times):
                reconstructed[cycle][("alu", (opcode, dest + lane, left + lane, right + lane))] += 1
        else:
            assert first == last
            reconstructed[first][(op["engine"], op["slot"])] += 1
    for cycle, bundle in enumerate(builder.instrs):
        assert all(len(slots) <= SLOT_LIMITS[engine] for engine, slots in bundle.items())
        actual = Counter((engine, slot) for engine, slots in bundle.items() for slot in slots)
        assert actual == reconstructed[cycle], cycle


def verify_words(builder, label, tree_words, input_words):
    forest = Tree(10, tree_words)
    inp = Input([0] * 256, input_words, 16)
    original = build_mem_image(forest, inp)
    machine = Machine(original, builder.instrs, builder.debug_info())
    machine.enable_pause = machine.enable_debug = False
    machine.run()
    expected = original.copy()
    for expected in reference_kernel2(expected):
        pass
    indices, values = original[5:7]
    workspace = builder.preencoded_node_count
    assert machine.mem[values:values + 256] == expected[values:values + 256], label
    assert machine.mem[:indices] == original[:indices], label
    assert machine.mem[indices + workspace:values] == original[indices + workspace:values], label
    assert machine.mem[indices:indices + workspace] == [
        word ^ HASH_STAGES[-1][1] for word in tree_words[15:15 + workspace]
    ], label
    assert machine.cycle == len(builder.instrs)


def main():
    builder = AnalyzedKernel()
    builder.build_kernel(10, 2047, 256, 16)
    verify_emission(builder)
    for seed in range(1000, 1032):
        check(builder, seed)
    patterns = (("zero", 0), ("ones", 0xFFFFFFFF),
                ("high", 0x80000000), ("alternating", 0xAAAAAAAA))
    for label, word in patterns:
        verify_words(builder, label,
                     [word if i % 2 == 0 else word ^ 0xFFFFFFFF for i in range(2047)],
                     [(word + i * 0x9E3779B9) & 0xFFFFFFFF for i in range(256)])
    for seed in range(2026, 2030):
        rng = random.Random(seed)
        verify_words(builder, f"full_word_seed_{seed}",
                     [rng.getrandbits(32) for _ in range(2047)],
                     [rng.getrandbits(32) for _ in range(256)])
    report = analyze(builder)
    report.pop("rounds")
    print(json.dumps(dict(schedule_emission="pass", random_seeds=32,
                          full_word_fixtures=8, **report), indent=2))


if __name__ == "__main__":
    main()
