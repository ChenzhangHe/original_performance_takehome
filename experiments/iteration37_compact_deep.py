"""Fit stride-4 depth4/5 + stride-3 depth6/7 records in 256 index words.

Address conversion uses the inverse of 3 modulo 2**32, never integer division.
Reads may include unused words beyond a record, but only three fields are used.
No forest or value preprocessing writes; all 256 workspace words are verified.
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
from tune_kernel import check
from verify_kernel import verify_emission, verify_words
from iteration37_lane_tail import make_source as lane_source
from dataflow_check import verify_dataflow, guard_write_reuse

SOURCE_REF = '2d5bc94853f290e21856da7d6705b585078a39f9'


def make_source(args):
    if args.lane_tail:
        source = lane_source([5, 6, 7, 8, 9], 32, True)
    else:
        source = subprocess.run(['git', 'show', f'{SOURCE_REF}:perf_takehome.py'], cwd=REPO,
                                check=True, capture_output=True, text=True).stdout
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)
    replace('extra_delta = n_nodes + 1 if self.blocked_encode_depth6 else 0', 'extra_delta = 0')
    replace('vector_const((73-block_base+extra_delta) & 0xFFFFFFFF, "block_exit_bias")',
            'vector_const((73-2*block_base) & 0xFFFFFFFF, "block_exit_bias")')
    start = source.index('            if self.blocked_encode_depth6:\n                self.preencoded_node_count += 64')
    end = source.index('\n        # Fold initialization into the same DAG', start)
    source = source[:start] + '''            if self.blocked_encode_depth6:
                self.preencoded_node_count += 192
                self.workspace_node_indices.extend(index for parent in range(63, 127)
                                                   for index in (parent, 2*parent+1, 2*parent+2))
                self.workspace_layout = "stride4_depth45_stride3_depth67"
                # D6=3*A6+W-146. Entry after depth4 uses
                # D6=3*B4+(73-2*W)-6*p4-3*p5.
                # A8=(4/3)*D6-(4/3)*(W-146)-15-2*p6-p7.
                deep_three = vector_const(3, "deep_three")
                deep_neg6 = vector_const(0xfffffffa, "deep_neg6")
                deep_neg3 = vector_const(0xfffffffd, "deep_neg3")
                deep_scale = vector_const(0xaaaaaaac, "deep_four_over_three")
                extra_exit_bias = vector_const((-0xaaaaaaac*(block_base-146)-15)&0xffffffff,
                                              "deep_exit_bias")
                direct_vectors.extend((deep_three, deep_neg6, deep_neg3, deep_scale, extra_exit_bias))
                deep_src = [self.alloc_scratch(f"deep_src_{i}") for i in range(3)]
                for pointer, address in zip(deep_src, (70, 134, 142)):
                    self.add("load", ("const", pointer, address))
                double_step = self.scratch_const(16)
                self.add("alu", ("+", block_store_addr, block_store_addr, address_step))
                for parent_start in range(0, 64, 8):
                    for buffer, pointer in zip(block_input, deep_src):
                        self.add("load", ("vload", buffer, pointer))
                    for vector_no in range(3):
                        for lane in range(8):
                            parent, field = divmod(vector_no*8+lane, 3)
                            if field == 0:
                                scalar = block_input[0]+parent
                            else:
                                child = 2*parent+field-1
                                scalar = block_input[1+child//8]+child%8
                            self.add("alu", ("^", block_output+lane, scalar, final_xor_const))
                        self.add("store", ("vstore", block_store_addr, block_output))
                        if parent_start != 56 or vector_no != 2:
                            self.add("alu", ("+", block_store_addr, block_store_addr, address_step))
                    if parent_start != 56:
                        for i, pointer in enumerate(deep_src):
                            self.add("alu", ("+", pointer, pointer, address_step if i == 0 else double_step))
''' + source[end:]
    replace('            assert len(extra_level_stores) == 8', '            assert len(extra_level_stores) == 24')
    replace('            block_right = block_left + VLEN', '''            block_right = block_left + VLEN
            deep_base = block_children_base+2*batch_size+chunk_count*BLOCKED_READ_BANKS*VLEN
            deep_left = deep_base+2*offset
            deep_right = deep_left+VLEN''')
    old_start = source.index('                elif blocked_lookup and depth == 4 and round_no != rounds - 1:\n                    block_use_start')
    old_end = source.index('                else:\n                    encoded_node', old_start)
    # Reuse the proven two-level reader, giving the deep records distinct
    # virtual buffers/lifetimes and their own setup-store barrier.
    reader = source[old_start:old_end]
    deep_reader = reader.replace('depth == 4', 'depth == 6').replace('depth == 5', 'depth == 7')
    for old, new in (('block_use_start', 'deep_use_start'), ('block_children_ready', 'deep_children_ready'),
                     ('block_parity_ready', 'deep_parity_ready'), ('block_parity', 'deep_parity'),
                     ('block_left', 'deep_left'), ('block_right', 'deep_right'),
                     ('block_stores', 'extra_level_stores')):
        deep_reader = deep_reader.replace(old, new)
    deep_reader = deep_reader.replace('block_children_base + 2*batch_size', 'deep_base + 2*batch_size')
    deep_reader = deep_reader.replace('block_children_base+2*batch_size', 'deep_base+2*batch_size')
    if args.window:
        deep_reader = deep_reader.replace('extra_level_stores, pending[bank])',
                                          f'extra_level_stores, pending[bank], deep_retired.get(chunk_no+{args.window}))')
        deep_reader = deep_reader.replace('                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)',
                                          '                    deep_retired[chunk_no] = selected\n                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)')
    if args.select_round != 7:
        deep_reader = deep_reader.replace('                elif blocked_lookup and depth == 7:\n',
                                          f'                elif blocked_lookup and depth == 7:\n                    emit_context["round"] = {args.select_round}\n')
        deep_reader = deep_reader.replace('                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)',
                                          '                    emit_context["round"] = round_no\n                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, selected)')
    source = source[:old_end]+deep_reader+source[old_end:]
    if args.window:
        replace('        for chunk_no in range(chunk_count):\n            emit_context.update(chunk=chunk_no, round=0 if reverse_inputs else -1, local_seq=0)',
                '        deep_retired = {}\n        for chunk_no in range(chunk_count-1, -1, -1):\n            emit_context.update(chunk=chunk_no, round=0 if reverse_inputs else -1, local_seq=0)')
    replace('index_base_ready = emit("valu", ("+", chunk_idx, chunk_idx, block_exit_bias), node_address_reads)',
            'index_base_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, deep_three, block_exit_bias), node_address_reads)')
    replace('and not (blocked_lookup and depth == 5)', 'and not (blocked_lookup and depth in (5, 7))')
    replace('("multiply_add", chunk_idx, chunk_idx, two, bias), node_address_reads',
            '("multiply_add", chunk_idx, chunk_idx, deep_scale if depth == 6 else two, bias), node_address_reads')
    replace('parity_dest, neg2, chunk_idx), parity, index_base_ready)',
            'parity_dest, deep_neg6, chunk_idx), parity, index_base_ready)')
    old = 'depth < retained_depth or (blocked_lookup and depth == 4)'
    assert source.count(old) == 2
    source = source.replace(old, 'depth < retained_depth or (blocked_lookup and depth in (4, 6))')
    replace('                    if blocked_lookup and depth == 5:\n                        index_base_ready = idx_ready',
            '''                    if blocked_lookup and depth == 5:
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest, deep_neg3, chunk_idx), parity, idx_ready)
                        continue
                    if blocked_lookup and depth == 6:
                        deep_parity, deep_parity_ready = parity_dest, parity
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest, neg2, chunk_idx), parity, index_base_ready)
                        continue
                    if blocked_lookup and depth == 7:
                        index_base_ready = idx_ready''')
    if args.index_select:
        if 4 in args.index_select:
            replace('                deep_three = vector_const(3, "deep_three")',
                    '''                deep_entry_even = vector_const((73-2*block_base-6)&0xffffffff, "deep_entry_even")
                direct_vectors.append(deep_entry_even)
                deep_three = vector_const(3, "deep_three")''')
            replace('                    index_base_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, deep_three, block_exit_bias), node_address_reads)',
                    f'''                    if chunk_no >= {args.index_groups}:
                        index_base_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, deep_three, block_exit_bias), node_address_reads)''')
        if 6 in args.index_select:
            replace('                direct_vectors.extend((deep_three, deep_neg6, deep_neg3, deep_scale, extra_exit_bias))',
                    '''                deep_exit_even = vector_const((-0xaaaaaaac*(block_base-146)-17)&0xffffffff, "deep_exit_even")
                direct_vectors.extend((deep_three, deep_neg6, deep_neg3, deep_scale, extra_exit_bias, deep_exit_even))''')
        replace('and not (blocked_lookup and depth in (5, 7))',
                f'and not (blocked_lookup and (depth in (5, 7) or (depth in {tuple(args.index_select)!r} and chunk_no < {args.index_groups})))')
        replace('                if positive_addresses:\n                    if blocked_lookup and depth == 3:',
                f'''                if positive_addresses:
                    if blocked_lookup and depth in {tuple(args.index_select)!r} and chunk_no < {args.index_groups}:
                        if depth == 4:
                            block_parity, block_parity_ready = parity_dest, parity
                            even_bias, odd_bias, scale = deep_entry_even, block_exit_bias, deep_three
                        elif depth == 6:
                            deep_parity, deep_parity_ready = parity_dest, parity
                            even_bias, odd_bias, scale = deep_exit_even, extra_exit_bias, deep_scale
                        else:
                            even_bias, odd_bias, scale = deep_neg6, address_bias, two
                        chosen_bias = select(chunk_tmp1, parity_dest, even_bias, odd_bias, parity)
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, scale, chunk_tmp1), idx_ready, chosen_bias)
                        continue
                    if blocked_lookup and depth == 3:''')
    if args.drop_unused_biases:
        assert set(args.index_select) == {4, 6} and args.index_groups == 32
        replace('                deep_neg6 = vector_const(0xfffffffa, "deep_neg6")', '                deep_neg6 = None')
        # Both parent updates now select their complete bias, so no separate
        # -6 or -2 parity multiplier is read anywhere in this variant.
        replace('            neg2 = vector_const(0xFFFFFFFE, "path_neg2")', '            neg2 = None')
        replace('            direct_vectors = [neg2, address_bias, negative_weights[2]]',
                '            direct_vectors = [address_bias, negative_weights[2]]')
        replace('(deep_three, deep_neg6, deep_neg3, deep_scale, extra_exit_bias, deep_exit_even)',
                '(deep_three, deep_neg3, deep_scale, extra_exit_bias, deep_exit_even)')
    if not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        policies = ("fragment_adaptive_tail_hetero_360_220_140_140_920",
                    "fragment_adaptive_tail_hetero_360_220_140_140_900")
        for policy in dict.fromkeys(policies):''')
    return guard_write_reuse(source) if args.safe_reuse else source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--banks', type=int, choices=(1, 2, 4, 8), default=4)
    parser.add_argument('--cache', type=int, default=7)
    parser.add_argument('--lane-tail', action='store_true')
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--window', type=int, default=0, help='Maximum stride-3 record readers per dependency chain window')
    parser.add_argument('--select-round', type=int, default=7, help='Priority metadata only; true path-bit dependencies stay intact')
    parser.add_argument('--index-select', type=int, nargs='*', default=[], choices=(4, 6, 8, 9))
    parser.add_argument('--index-groups', type=int, default=32)
    parser.add_argument('--dataflow', action='store_true', help='Check physical scratch provenance against logical program order')
    parser.add_argument('--safe-reuse', action='store_true', help='Forbid same-cycle reuse when the former lifetime ends with a write')
    parser.add_argument('--drop-unused-biases', action='store_true')
    args = parser.parse_args()
    module = types.ModuleType('compact_deep_probe')
    exec(compile(make_source(args), '<compact_deep_probe>', 'exec'), module.__dict__)
    module.BLOCKED_READ_BANKS = args.banks
    module.BLOCKED_FINAL_CACHE_CHUNKS = args.cache
    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
        def allocate_node_lifetimes(self, ops, uses, reusable=()):
            self.logical_slots = [op['slot'] for op in ops]
            super().allocate_node_lifetimes(ops, uses, reusable)
    builder = Builder()
    result = dict(source_ref=SOURCE_REF, **vars(args))
    try:
        builder.build_kernel(10, 2047, 256, 16)
    except AssertionError as error:
        if str(error) != 'Out of scratch space':
            raise
        result.update(rejected='scratch', required=builder.scratch_ptr)
    else:
        stage = 'emission'
        try:
            verify_emission(builder)
            if args.dataflow:
                stage = 'scratch_dataflow'
                verify_dataflow(builder)
            for seed in (123, 456, 789):
                stage = f'correctness_seed_{seed}'
                check(builder, seed)
            stage = 'full_word_workspace'
            verify_words(builder, 'full_word', [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                         [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
        except AssertionError as error:
            result.update(rejected=stage, detail=str(error))
        else:
            result.update(cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                          verification='three frozen seeds, full-word workspace, exact emission' +
                          (' and scratch provenance' if args.dataflow else ''))
    counts = Counter(op['engine'] for op in builder.operations)
    result.update(logical_slots=counts, weighted=counts['valu']+counts['alu']/8,
                  policy=builder.schedule_policy)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
