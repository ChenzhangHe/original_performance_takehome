"""Reproducible local tuning; validate every candidate with the frozen simulator.

Example:
    python tune_kernel.py --hash-alu-chunks 0 4 8 12 --alu-root-chunks 24 28 32
Does not modify the kernel, simulator, tests, or input data.
"""

import argparse
from collections import Counter
from itertools import product
import json
from pathlib import Path
import random
import sys
import time

import perf_takehome as kernel

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
from frozen_problem import Input, Machine, Tree, build_mem_image, reference_kernel2


def check(builder, seed, height=10, rounds=16, batch=256):
    random.seed(seed)
    forest = Tree.generate(height)
    inp = Input.generate(forest, batch, rounds)
    memory = build_mem_image(forest, inp)
    machine = Machine(memory, builder.instrs, builder.debug_info())
    machine.enable_pause = False
    machine.enable_debug = False
    machine.run()
    for expected in reference_kernel2(memory):
        pass
    base = expected[6]
    assert machine.mem[base : base + batch] == expected[base : base + batch], seed
    assert machine.cycle == len(builder.instrs)
    return machine.cycle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("HASH_ALU_CHUNKS", "ALU_ROOT_CHUNKS", "ALU_INDEX_CHUNKS"):
        parser.add_argument("--" + name.lower().replace("_", "-"), type=int, nargs="+", default=[getattr(kernel, name)])
    parser.add_argument("--seeds", type=int, nargs="+", default=[123])
    args = parser.parse_args()
    for hash_chunks, root_chunks, index_chunks in product(args.hash_alu_chunks, args.alu_root_chunks, args.alu_index_chunks):
        kernel.HASH_ALU_CHUNKS = hash_chunks
        kernel.ALU_ROOT_CHUNKS = root_chunks
        kernel.ALU_INDEX_CHUNKS = index_chunks
        start = time.perf_counter()
        builder = kernel.KernelBuilder()
        builder.build_kernel(10, 2047, 256, 16)
        elapsed = time.perf_counter() - start
        for seed in args.seeds:
            cycles = check(builder, seed)
        slots = Counter()
        for bundle in builder.instrs:
            slots.update({engine: len(ops) for engine, ops in bundle.items()})
        print(json.dumps(dict(hash_chunks=hash_chunks, root_chunks=root_chunks, index_chunks=index_chunks,
                              cycles=cycles, scratch=builder.scratch_ptr, policy=builder.schedule_policy,
                              slots=dict(slots), checked_seeds=args.seeds, build_seconds=round(elapsed, 3))), flush=True)


if __name__ == "__main__":
    main()
