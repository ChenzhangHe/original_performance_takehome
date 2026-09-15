"""Local schedule and full-word regression checks, separate from official tests.

Run: python3 verify_kernel.py
Uses the frozen simulator/reference; does not modify tests or their inputs.
"""

from collections import Counter
import argparse
import json
import random

import perf_takehome as kernel
from analyze_kernel import AnalyzedKernel, analyze
from problem import SLOT_LIMITS, VLEN
from tune_kernel import check, Input, Machine, Tree, build_mem_image, reference_kernel2
from frozen_problem import HASH_STAGES
from experiments.dataflow_check import verify_dataflow


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
        0 if index is None else tree_words[index] ^ HASH_STAGES[-1][1]
        for index in builder.workspace_node_indices
    ], label
    assert machine.cycle == len(builder.instrs)


def verify_analysis(builder, report):
    """Deferred setup remains setup, even when its priority round is > 0."""
    pre_round_slots = Counter()
    lookup_slots = 0
    for i, op in enumerate(builder.operations):
        # Round -1 also contains each group's initial input-value vload.
        if op.get("is_setup", False) or op["round"] == -1:
            engine = "alu" if i in builder.offloaded_ops else op["engine"]
            pre_round_slots[engine] += len(builder.lane_issue_cycles.get(i, [builder.issue_cycles[i]]))
        elif (op["round"] > 0 and op["engine"] == "load"
              and op["slot"][0] in ("load_offset", "vload")):
            lookup_slots += 1
    assert {e: item["slots"] for e, item in report["rounds"][-1].items()} == dict(pre_round_slots)
    assert report["lookup_load"]["slots"] == lookup_slots
    if builder.blocked_setup_deadlines:
        assert any(op.get("is_setup", False) and op["round"] > 0 for op in builder.operations)


def verify_allocator_boundaries():
    """Exercise write/write rejection, legal read/write reuse and wide spans."""
    def fixture(writer_at_end, span=1):
        builder = kernel.KernelBuilder()
        old = builder.alloc_scratch("old", 8)
        source = builder.alloc_scratch("source")
        virtual = 10000
        first = ("valu", ("vbroadcast", old, source)) if writer_at_end else ("store", ("vstore", source, old))
        second = ("valu", ("vbroadcast", virtual, source))
        entries = [first, second]
        if span == 2:
            entries.append(("valu", ("vbroadcast", virtual+8, source)))
        ops = [dict(engine=engine, slot=slot) for engine, slot in entries]
        bundle = {}
        for engine, slot in entries:
            bundle.setdefault(engine, []).append(slot)
        builder.instrs = [bundle]
        builder.allocate_node_lifetimes(ops, [(virtual, 1, len(ops), -span if span > 1 else 1)], (old,))
        return old, ops
    old, writes = fixture(True)
    assert writes[1]["slot"][1] != old, "Two writes cannot reuse a scratch word in one cycle"
    old, reads = fixture(False)
    assert reads[1]["slot"][1] == old, "Start-cycle read/end-cycle write reuse should remain available"
    old, wide = fixture(True, 2)
    assert wide[2]["slot"][1] == wide[1]["slot"][1]+8
    assert not (set(range(old, old+8)) & set(range(wide[1]["slot"][1], wide[1]["slot"][1]+16)))


def verify_extra_shapes():
    results = []
    for height, rounds, batch in ((3, 5, 32), (4, 7, 64), (6, 11, 128),
                                  (8, 12, 256), (10, 8, 256), (10, 20, 256)):
        builder = kernel.KernelBuilder()
        builder.build_kernel(height, 2**(height+1)-1, batch, rounds)
        assert not builder.blocked_lookup
        for seed in (123, 456, 789):
            cycles = check(builder, seed, height, rounds, batch)
        results.append(dict(shape=[height, rounds, batch], seeds=3, cycles=cycles))
    original = kernel.PATH_REUSE_DEPTH, kernel.DIRECT_PATH_DEPTH
    try:
        for depth in (0, 2, 3):
            kernel.PATH_REUSE_DEPTH = kernel.DIRECT_PATH_DEPTH = depth
            builder = kernel.KernelBuilder()
            builder.build_kernel(10, 2047, 256, 16)
            assert not builder.blocked_lookup
            for seed in (123, 456, 789):
                cycles = check(builder, seed)
            results.append(dict(path_depth=depth, seeds=3, cycles=cycles))
    finally:
        kernel.PATH_REUSE_DEPTH, kernel.DIRECT_PATH_DEPTH = original
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extra-shapes", action="store_true")
    args = parser.parse_args()
    verify_allocator_boundaries()
    builder = AnalyzedKernel()
    builder.build_kernel(10, 2047, 256, 16)
    verify_emission(builder)
    verify_dataflow(builder)
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
    verify_analysis(builder, report)
    report.pop("rounds")
    if args.extra_shapes:
        report["extra_shape_checks"] = verify_extra_shapes()
    print(json.dumps(dict(schedule_emission="pass", scratch_dataflow="pass", allocator_boundaries="pass", setup_accounting="pass", random_seeds=32,
                          full_word_fixtures=8, **report), indent=2))


if __name__ == "__main__":
    main()
