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
    indices_base = expected[5]
    workspace_words = getattr(builder, "preencoded_node_count", 0)
    assert 0 <= workspace_words <= batch
    # Runtime preprocessing may use only its declared index workspace. The
    # header, entire forest, and remaining index words must stay untouched.
    assert machine.mem[:indices_base] == expected[:indices_base], seed
    assert machine.mem[indices_base + workspace_words : indices_base + batch] == inp.indices[workspace_words:], seed
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
        "NODE_PREENCODE_DEPTH",
        "INPUT_ADDRESS_CONSUMER_PRIORITY", "ALU_FRAGMENT_ISSUE",
        "DIRECT_GATHER_ADDRESSES",
        "BLOCKED_LOOKUP", "BLOCKED_READ_BANKS", "BLOCKED_FINAL_CACHE_CHUNKS",
        "BLOCKED_FUSE_PARENT_XOR",
        "BLOCKED_FUSE_SETUP_XOR",
        "BLOCKED_SETUP_DEADLINES", "BLOCKED_EARLY_TAIL_SELECT", "BLOCKED_DROP_UNUSED_WEIGHT",
    ):
        parser.add_argument("--" + name.lower().replace("_", "-"), type=int, nargs="+", default=[getattr(kernel, name)])
    parser.add_argument("--seeds", type=int, nargs="+", default=[123])
    args = parser.parse_args()
    for hash_chunks, bit_mask_chunks, path_depth, cache_chunks, direct_depth, backlog, reserve_start, final_cache_chunks, address_chain, preencode_depth, input_priority, fragment_issue, direct_addresses, blocked, read_banks, blocked_final_cache, fuse_parent, fuse_setup, setup_deadlines, early_tail, drop_weight in product(
        args.hash_alu_chunks, args.bit_mask_valu_chunks, args.path_reuse_depth,
        args.depth4_cache_chunks, args.direct_path_depth, args.alu_vector_backlog,
        args.alu_vector_reserve_start,
        args.depth4_final_cache_chunks,
        args.input_address_chain_length,
        args.node_preencode_depth,
        args.input_address_consumer_priority, args.alu_fragment_issue,
        args.direct_gather_addresses,
        args.blocked_lookup, args.blocked_read_banks, args.blocked_final_cache_chunks,
        args.blocked_fuse_parent_xor,
        args.blocked_fuse_setup_xor,
        args.blocked_setup_deadlines, args.blocked_early_tail_select, args.blocked_drop_unused_weight,
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
        kernel.NODE_PREENCODE_DEPTH = preencode_depth
        kernel.INPUT_ADDRESS_CONSUMER_PRIORITY = bool(input_priority)
        kernel.ALU_FRAGMENT_ISSUE = bool(fragment_issue)
        kernel.DIRECT_GATHER_ADDRESSES = bool(direct_addresses)
        kernel.BLOCKED_LOOKUP = bool(blocked)
        kernel.BLOCKED_READ_BANKS = read_banks
        kernel.BLOCKED_FINAL_CACHE_CHUNKS = blocked_final_cache
        kernel.BLOCKED_FUSE_PARENT_XOR = bool(fuse_parent)
        kernel.BLOCKED_FUSE_SETUP_XOR = bool(fuse_setup)
        kernel.BLOCKED_SETUP_DEADLINES = bool(setup_deadlines)
        kernel.BLOCKED_EARLY_TAIL_SELECT = bool(early_tail)
        kernel.BLOCKED_DROP_UNUSED_WEIGHT = bool(drop_weight)
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
                              node_preencode_depth=preencode_depth,
                              input_address_consumer_priority=bool(input_priority),
                              alu_fragment_issue=bool(fragment_issue),
                              direct_gather_addresses=bool(direct_addresses),
                              blocked_lookup=builder.blocked_lookup,
                              blocked_read_banks=read_banks,
                              blocked_final_cache_chunks=blocked_final_cache,
                              blocked_fuse_parent_xor=bool(fuse_parent),
                              blocked_fuse_setup_xor=builder.blocked_setup_xor_fused,
                              blocked_setup_deadlines=builder.blocked_setup_deadlines,
                              blocked_early_tail_select=builder.blocked_early_tail_select,
                              blocked_unused_weight_pruned=builder.blocked_unused_weight_pruned,
                              cycles=cycles, scratch=builder.scratch_ptr, policy=builder.schedule_policy,
                              slots=dict(slots), checked_seeds=args.seeds, build_seconds=round(elapsed, 3))), flush=True)


if __name__ == "__main__":
    main()
