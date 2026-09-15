"""Three-level, eight-word subtree records on the pinned 954-cycle graph.

Research only: constructs a changed builder in memory, never edits production.
Full-word workspace checks include every runtime-generated coefficient.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tune_kernel import check, Machine, Tree, Input, build_mem_image, reference_kernel2
from verify_kernel import verify_emission

SOURCE_REF = "2d5bc94853f290e21856da7d6705b585078a39f9"


def make_source(mode, banks, full_policies=False, window=0):
    source = subprocess.run(["git", "show", f"{SOURCE_REF}:perf_takehome.py"],
                            cwd=REPO, check=True, capture_output=True, text=True).stdout

    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (old[:100], source.count(old))
        source = source.replace(old, new)

    replace("BLOCKED_ENCODE_DEPTH6 = True", "BLOCKED_ENCODE_DEPTH6 = False")
    replace("BLOCKED_READ_BANKS = 4", f"BLOCKED_READ_BANKS = {banks}")
    start = source.index("            block_input = [self.alloc_scratch")
    end = source.index("            # Record address B=4*A4", start)
    source = source[:start] + '''            block_input = [self.alloc_scratch(f"block_input_{i}", VLEN) for i in range(7)]
            block_output = self.alloc_scratch("block_output", VLEN)
            block_store_addr = self.alloc_scratch("block_store_addr")
            self.add("load", ("const", block_store_addr, block_base))
            for parent_start in (0, 8):
                addresses = [22+parent_start, 38+2*parent_start, 46+2*parent_start]
                addresses.extend(70+4*parent_start+8*j for j in range(4))
                for dest, address in zip(block_input, addresses):
                    self.add("load", ("vload", dest, self.scratch_const(address)))
                for parent in range(8):
                    fields = [block_input[0]+parent]
                    fields.extend(block_input[1+(2*parent+j)//8]+(2*parent+j)%8 for j in range(2))
                    fields.extend(block_input[3+(4*parent+j)//8]+(4*parent+j)%8 for j in range(4))
                    for j, scalar in enumerate(fields):
                        self.add("alu", ("^", block_output+j, scalar, final_xor_const))
                    # The unused padding lane stays zero; coefficients below
                    # are modulo 2**32 and operate on encoded node values.
                    if THREE_LEVEL_MODE == "coefficient":
                        # [LL, LR, RL, RR] -> [RR, LR-RR, RL-RR, LL-LR-RL+RR].
                        self.add("alu", ("-", block_output+3, block_output+3, block_output+4))
                        self.add("alu", ("-", block_output+5, block_output+5, block_output+6))
                        self.add("alu", ("-", block_output+4, block_output+4, block_output+6))
                        self.add("alu", ("-", block_output+3, block_output+3, block_output+5))
                    self.add("store", ("vstore", block_store_addr, block_output))
                    if parent_start != 8 or parent != 7:
                        self.add("alu", ("+", block_store_addr, block_store_addr, address_step))
''' + source[end:]
    replace('extra_delta = n_nodes + 1 if self.blocked_encode_depth6 else 0', 'extra_delta = 0')
    replace('vector_const((73-block_base+extra_delta) & 0xFFFFFFFF, "block_exit_bias")',
            'vector_const((141-block_base) & 0xFFFFFFFF, "block_exit_bias")')
    replace('            block_neg4 = negative_weights[2]', '            block_neg4 = vector_const(0xfffffff8, "block_neg8")')
    replace('vector_const((-4*(1 << shift))', 'vector_const((-8*(1 << shift))')
    replace('vector_const(block_base+28, "block_left")', 'vector_const(block_base+56, "block_left")')
    replace('vector_const(block_base+60, "block_right")', 'vector_const(block_base+120, "block_right")')
    replace('            self.preencoded_node_count = 64', '            self.preencoded_node_count = 128')
    replace('for index in (parent, 2*parent+1, 2*parent+2, None)',
            'for index in (parent, 2*parent+1, 2*parent+2, *range(4*parent+3, 4*parent+7), None)')
    replace('self.workspace_layout = "parent_children_stride4"',
            'self.workspace_layout = "three_level_" + THREE_LEVEL_MODE')
    replace('            block_right = block_left + VLEN', '''            block_right = block_left + VLEN
            grand_base = block_children_base + 2*batch_size + chunk_count*BLOCKED_READ_BANKS*VLEN
            grand = [grand_base+(chunk_no*4+j)*VLEN for j in range(4)]''')
    replace('for j, dest in enumerate((block_left, block_right), 1)',
            'for j, dest in enumerate((block_left, block_right, *grand), 1)')
    start = source.index('                elif blocked_lookup and depth == 5:')
    end = source.index('                else:\n                    encoded_node', start)
    source = source[:start] + '''                elif blocked_lookup and depth == 5:
                    selected = select(chunk_node, block_parity, block_left, block_right,
                                      block_parity_ready, block_children_ready)
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)
                    node_pool_uses.extend((dest, block_use_start, len(ops), 1) for dest in (block_left, block_right))
                    if THREE_LEVEL_MODE == "coefficient":
                        # Physical coefficient order is [cross, p4, p5, constant].
                        ga = emit("valu", ("multiply_add", grand[0], grand[0], block_parity, grand[2]),
                                  block_parity_ready, block_children_ready)
                        gb = emit("valu", ("multiply_add", grand[1], grand[1], block_parity, grand[3]),
                                  block_parity_ready, block_children_ready)
                    else:
                        ga = select(grand[0], block_parity, grand[0], grand[2], block_parity_ready, block_children_ready)
                        gb = select(grand[1], block_parity, grand[1], grand[3], block_parity_ready, block_children_ready)
                    node_pool_uses.extend((dest, block_use_start, len(ops), 1) for dest in grand[2:])
                elif blocked_lookup and depth == 6:
                    if THREE_LEVEL_MODE == "coefficient":
                        selected = emit("valu", ("multiply_add", chunk_node, grand[0], depth5_parity, grand[1]),
                                        ga, gb, depth5_parity_ready)
                    else:
                        selected = select(chunk_node, depth5_parity, grand[0], grand[1], ga, gb, depth5_parity_ready)
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)
                    node_pool_uses.extend((dest, block_use_start, len(ops), 1) for dest in grand[:2])
''' + source[end:]
    replace('and not (blocked_lookup and depth == 5)', 'and not (blocked_lookup and depth in (5, 6))')
    replace('parity_dest, neg2, chunk_idx), parity, index_base_ready)',
            'parity_dest, negative_weights_original4, chunk_idx), parity, index_base_ready)')
    replace('            block_neg4 = vector_const(0xfffffff8, "block_neg8")',
            '            negative_weights_original4 = negative_weights[2]\n            block_neg4 = vector_const(0xfffffff8, "block_neg8")\n            direct_vectors.append(block_neg4)')
    old = 'depth < retained_depth or (blocked_lookup and depth == 4)'
    assert source.count(old) == 2
    source = source.replace(old, 'depth < retained_depth or (blocked_lookup and depth in (4, 5))')
    replace('                    if blocked_lookup and depth == 5:\n                        index_base_ready = idx_ready',
            '''                    if blocked_lookup and depth == 5:
                        depth5_parity, depth5_parity_ready = parity_dest, parity
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest, neg2, chunk_idx), parity, idx_ready)
                        continue
                    if blocked_lookup and depth == 6:
                        index_base_ready = idx_ready''')
    if window:
        replace('        for chunk_no in range(chunk_count):\n            emit_context.update(chunk=chunk_no, round=0 if reverse_inputs else -1, local_seq=0)',
                '        record_retired = {}\n        for chunk_no in range(chunk_count-1, -1, -1):\n            emit_context.update(chunk=chunk_no, round=0 if reverse_inputs else -1, local_seq=0)')
        replace('                                      block_stores, pending[bank])',
                f'                                      block_stores, pending[bank], record_retired.get(chunk_no+{window}))')
        replace('                    node_pool_uses.extend((dest, block_use_start, len(ops), 1) for dest in grand[:2])',
                '                    record_retired[chunk_no] = selected\n                    node_pool_uses.extend((dest, block_use_start, len(ops), 1) for dest in grand[:2])')
    if not full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        policies = ("fragment_adaptive_tail_hetero_360_220_140_140_920",
                    "fragment_adaptive_tail_hetero_360_220_140_140_900")
        for policy in dict.fromkeys(policies):''')
    return source


def full_word_check(builder, mode):
    tree_words = [(i*0x9e3779b9) & 0xffffffff for i in range(2047)]
    inputs = [(i*0xabcdef01+0x80000000) & 0xffffffff for i in range(256)]
    original = build_mem_image(Tree(10, tree_words), Input([0]*256, inputs, 16))
    expected = original.copy()
    for expected in reference_kernel2(expected):
        pass
    machine = Machine(original, builder.instrs, builder.debug_info())
    machine.enable_pause = machine.enable_debug = False
    machine.run()
    indices, values = original[5:7]
    assert machine.mem[values:] == expected[values:]
    assert machine.mem[:indices] == original[:indices]
    assert machine.mem[indices+128:values] == original[indices+128:values]
    records = []
    for parent in range(15, 31):
        nodes = [parent, 2*parent+1, 2*parent+2, *range(4*parent+3, 4*parent+7)]
        record = [tree_words[i] ^ 0xb55a4f09 for i in nodes]+[0]
        if mode == "coefficient":
            ll, lr, rl, rr = record[3:7]
            record[3:7] = [(ll-lr-rl+rr)&0xffffffff, (lr-rr)&0xffffffff, (rl-rr)&0xffffffff, rr]
        records.extend(record)
    assert machine.mem[indices:indices+128] == records
    assert machine.cycle == len(builder.instrs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('select', 'coefficient'), default='select')
    parser.add_argument('--banks', type=int, choices=(1, 2, 4), default=4)
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--window', type=int, default=0, help='Bound live subtree records with real load dependencies')
    args = parser.parse_args()
    module = types.ModuleType('three_level_probe')
    module.THREE_LEVEL_MODE = args.mode
    assert 0 <= args.window <= 32
    exec(compile(make_source(args.mode, args.banks, args.full_policies, args.window), '<three_level_probe>', 'exec'), module.__dict__)
    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
    builder = Builder()
    result = dict(source_ref=SOURCE_REF, **vars(args))
    try:
        builder.build_kernel(10, 2047, 256, 16)
    except AssertionError as error:
        if str(error) != 'Out of scratch space':
            raise
        result.update(rejected='scratch', required=builder.scratch_ptr)
    else:
        verify_emission(builder)
        for seed in (123, 456, 789):
            check(builder, seed)
        full_word_check(builder, args.mode)
        result.update(cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                      verification='three frozen seeds, full-word workspace, exact emission')
    counts = Counter(op['engine'] for op in builder.operations)
    result.update(logical_slots=counts, weighted=counts['valu']+counts['alu']/8,
                  policy=builder.schedule_policy)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
