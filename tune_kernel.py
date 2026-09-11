"""Reproducible local tuning; validate every candidate with the frozen simulator.

Example:
    python tune_kernel.py --hash-alu-chunks 0 4 8 12
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
    for name in (
        "HASH_ALU_CHUNKS", "BIT_MASK_VALU_CHUNKS", "PATH_REUSE_DEPTH",
        "DEPTH4_CACHE_CHUNKS", "DIRECT_PATH_DEPTH", "ALU_VECTOR_BACKLOG",
        "ALU_VECTOR_RESERVE_START",
        "DEPTH4_FINAL_CACHE_CHUNKS",
        "INPUT_ADDRESS_CHAIN_LENGTH",
    ):
        parser.add_argument("--" + name.lower().replace("_", "-"), type=int, nargs="+", default=[getattr(kernel, name)])
    parser.add_argument("--seeds", type=int, nargs="+", default=[123])
    args = parser.parse_args()
    for hash_chunks, bit_mask_chunks, path_depth, cache_chunks, direct_depth, backlog, reserve_start, final_cache_chunks, address_chain in product(
        args.hash_alu_chunks, args.bit_mask_valu_chunks, args.path_reuse_depth,
        args.depth4_cache_chunks, args.direct_path_depth, args.alu_vector_backlog,
        args.alu_vector_reserve_start,
        args.depth4_final_cache_chunks,
        args.input_address_chain_length,
    ):
        kernel.HASH_ALU_CHUNKS = hash_chunks
        kernel.BIT_MASK_VALU_CHUNKS = bit_mask_chunks
        kernel.PATH_REUSE_DEPTH = path_depth
        kernel.DEPTH4_CACHE_CHUNKS = cache_chunks
        kernel.DIRECT_PATH_DEPTH = direct_depth
        kernel.ALU_VECTOR_BACKLOG = backlog
        kernel.ALU_VECTOR_RESERVE_START = reserve_start
        kernel.DEPTH4_FINAL_CACHE_CHUNKS = final_cache_chunks
        kernel.INPUT_ADDRESS_CHAIN_LENGTH = address_chain
        start = time.perf_counter()
        builder = kernel.KernelBuilder()
        builder.build_kernel(10, 2047, 256, 16)
        elapsed = time.perf_counter() - start
        for seed in args.seeds:
            cycles = check(builder, seed)
        slots = Counter()
        for bundle in builder.instrs:
            slots.update({engine: len(ops) for engine, ops in bundle.items()})
        print(json.dumps(dict(hash_chunks=hash_chunks,
                              bit_mask_chunks=bit_mask_chunks,
                              path_reuse_depth=path_depth, depth4_cache_chunks=cache_chunks,
                              direct_path_depth=direct_depth,
                              alu_vector_backlog=backlog, alu_vector_reserve_start=reserve_start,
                              depth4_final_cache_chunks=final_cache_chunks,
                              input_address_chain_length=address_chain,
                              cycles=cycles, scratch=builder.scratch_ptr, policy=builder.schedule_policy,
                              slots=dict(slots), checked_seeds=args.seeds, build_seconds=round(elapsed, 3))), flush=True)


if __name__ == "__main__":
    main()
