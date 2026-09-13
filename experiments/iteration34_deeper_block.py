"""Move two-level records to depths 5/6; include all runtime layout costs."""
import argparse
from collections import Counter
from pathlib import Path
import json
import subprocess
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tune_kernel import check
from verify_kernel import verify_emission


def make_module(first_cache):
    source = subprocess.run(['git', 'show', '1d27efc:perf_takehome.py'], cwd=REPO,
                            check=True, capture_output=True, text=True).stdout
    def replace(old, new):
        nonlocal source
        assert old in source, old[:100]
        source = source.replace(old, new, 1)
    replace('cache_depth4 = BLOCKED_FINAL_CACHE_CHUNKS > 0',
            'cache_depth4 = FIRST_CACHE > 0 or BLOCKED_FINAL_CACHE_CHUNKS > 0')
    replace('''                return (round_no == rounds - 1
                        and chunk_no >= chunk_count - BLOCKED_FINAL_CACHE_CHUNKS)''',
            '''                return ((round_no == 4 and chunk_no < FIRST_CACHE)
                        or (round_no == rounds-1 and chunk_no >= chunk_count-BLOCKED_FINAL_CACHE_CHUNKS))''')
    start = source.index('        if blocked_lookup:\n            # Transpose the runtime')
    end = source.index('        # Fold initialization', start)
    source = source[:start] + '''        if blocked_lookup:
            block_base = forest_values_p + n_nodes
            linear_delta = block_base + 128 - 22
            block_input = [self.alloc_scratch(f"block_input_{i}", VLEN) for i in range(3)]
            block_output = self.alloc_scratch("block_output", VLEN)
            block_store_addr = self.alloc_scratch("block_store_addr")
            linear_addr = self.alloc_scratch("block_linear_addr")
            self.add("load", ("const", linear_addr, block_base+128))
            for address in (22, 30):
                addr = self.scratch_const(address)
                self.add("load", ("vload", block_input[0], addr))
                self.add("valu", ("^", block_input[0], block_input[0], final_xor_vec))
                self.add("store", ("vstore", linear_addr, block_input[0]))
                if address == 22:
                    self.add("alu", ("+", linear_addr, linear_addr, address_step))
            self.add("load", ("const", block_store_addr, block_base))
            for parent_start in (0, 8, 16, 24):
                addresses = (38+parent_start, 70+2*parent_start, 78+2*parent_start)
                for dest, address in zip(block_input, addresses):
                    addr = self.scratch_const(address)
                    self.add("load", ("vload", dest, addr))
                    self.add("valu", ("^", dest, dest, final_xor_vec))
                for pair in range(4):
                    for member in range(2):
                        parent = pair*2 + member
                        children = [block_input[1+(2*parent+j)//VLEN]+(2*parent+j)%VLEN for j in (0, 1)]
                        for j, scalar in enumerate([block_input[0]+parent, *children]):
                            self.add("alu", ("+", block_output+4*member+j, scalar, readonly_zero))
                    self.add("store", ("vstore", block_store_addr, block_output))
                    if parent_start != 24 or pair != 3:
                        self.add("alu", ("+", block_store_addr, block_store_addr, address_step))
            block_exit_bias = vector_const((137-block_base) & 0xFFFFFFFF, "block_exit_bias")
            block_enter_bias = vector_const((block_base-172-8*linear_delta) & 0xFFFFFFFF, "block_enter_bias")
            eight = vector_const(8, "block_eight")
            block_neg4 = negative_weights[2]
            plain_weights = {1: neg2, 2: block_neg4, 3: vector_const(0xFFFFFFF8, "plain_neg8")}
            block_weights = {1: plain_weights[3], 2: vector_const(0xFFFFFFF0, "block_neg16"),
                             3: vector_const(0xFFFFFFE0, "block_neg32")}
            address_bases[4] = (vector_const(29+linear_delta, "linear_left"), vector_const(37+linear_delta, "linear_right"))
            address_bases[5] = (vector_const(block_base+60, "block_left"), vector_const(block_base+124, "block_right"))
            direct_vectors.extend((block_exit_bias, block_enter_bias, eight, plain_weights[3],
                                   block_weights[2], block_weights[3], *address_bases[4], *address_bases[5]))
            self.preencoded_node_count = 144
            self.workspace_node_indices = [index for parent in range(31, 63)
                                           for index in (parent, 2*parent+1, 2*parent+2, None)] + list(range(15, 31))
            self.workspace_layout = "parent_children_depth56"

''' + source[end:]
    replace('        block_children_base = path_bits_base + rounds * batch_size',
            '        linear_stores = block_stores[:2]\n        block_children_base = path_bits_base + rounds * batch_size')
    replace('''                        if first_gather_depth > min(forest_height, rounds - 1 - round_no):''',
            '''                        negative_weights = block_weights if first_gather_depth == 5 else plain_weights
                        if first_gather_depth > min(forest_height, rounds - 1 - round_no):''')
    replace('''                if blocked_lookup and depth == 4 and round_no != rounds - 1:
                    chunk_node = chunk_tmp1''',
            '''                if blocked_lookup and depth == 5:
                    chunk_node = chunk_tmp1''')
    replace('                elif blocked_lookup and depth == 4 and round_no != rounds - 1:',
            '                elif blocked_lookup and depth == 5:')
    replace('''                elif blocked_lookup and depth == 5:
                    selected = select(chunk_node, block_parity''',
            '''                elif blocked_lookup and depth == 6:
                    selected = select(chunk_node, block_parity''')
    replace('extra_ready = (block_stores if blocked_lookup and encoded_node else',
            'extra_ready = (linear_stores if blocked_lookup and encoded_node else')
    replace('''                if blocked_lookup and depth == 4 and round_no != rounds - 1:
                    index_base_ready = emit("valu", ("+", chunk_idx, chunk_idx, block_exit_bias), node_address_reads)''',
            '''                if blocked_lookup and depth == 4 and round_no != rounds-1 and not cached_lookup:
                    index_base_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, eight, block_enter_bias), node_address_reads)
                elif blocked_lookup and depth == 5:
                    index_base_ready = emit("valu", ("+", chunk_idx, chunk_idx, block_exit_bias), node_address_reads)''')
    replace('and not (blocked_lookup and depth == 5)', 'and not (blocked_lookup and depth == 6)')
    source = source.replace('if depth < retained_depth or (blocked_lookup and depth == 4):',
                            'if depth < retained_depth or (blocked_lookup and depth == 5):')
    replace('''                    if blocked_lookup and depth == 3:
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest, block_neg4, chunk_idx), parity, idx_ready)
                        continue
                    if blocked_lookup and depth == 4:
                        block_parity, block_parity_ready = parity_dest, parity
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest, neg2, chunk_idx), parity, index_base_ready)
                        continue
                    if blocked_lookup and depth == 5:
                        index_base_ready = idx_ready''',
            '''                    if blocked_lookup and depth == 4:
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest, block_neg4, chunk_idx),
                                         parity, idx_ready if cached_lookup else index_base_ready)
                        continue
                    if blocked_lookup and depth == 5:
                        block_parity, block_parity_ready = parity_dest, parity
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest, neg2, chunk_idx), parity, index_base_ready)
                        continue
                    if blocked_lookup and depth == 6:
                        index_base_ready = idx_ready''')
    mod = types.ModuleType('deeper_block')
    source = source.replace('"Out of scratch space"', 'f"Out of scratch space: {self.scratch_ptr}"')
    mod.FIRST_CACHE = first_cache
    exec(compile(source, '<deeper_block>', 'exec'), mod.__dict__)
    return mod


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--first-cache', type=int, default=8)
    parser.add_argument('--final-cache', type=int, default=7)
    args = parser.parse_args()
    m = make_module(args.first_cache)
    m.BLOCKED_FINAL_CACHE_CHUNKS = args.final_cache
    class B(m.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
    b = B()
    try:
        b.build_kernel(10, 2047, 256, 16)
    except AssertionError as error:
        if str(error).startswith('Out of scratch space:'):
            print(json.dumps(dict(first_cache=args.first_cache, final_cache=args.final_cache,
                                  rejected=str(error), limit=1536)))
            return
        raise
    verify_emission(b)
    for seed in (123, 456, 789):
        check(b, seed)
    slots = Counter()
    for bundle in b.instrs:
        slots.update({e:len(v) for e,v in bundle.items()})
    print(json.dumps(dict(first_cache=args.first_cache, final_cache=args.final_cache, cycles=len(b.instrs),
                          scratch=b.scratch_ptr, slots=slots, weighted=slots['valu']+slots['alu']/8,
                          policy=b.schedule_policy)))


if __name__ == '__main__':
    main()
