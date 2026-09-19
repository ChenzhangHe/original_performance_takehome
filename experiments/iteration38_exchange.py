"""Trade a bounded number of cached selects for padding gathers.

Pinned parent: 34742f6. The new gathers free flow capacity for complete
deep address-bias selects. All setup, movement and index arithmetic counts.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from experiments.iteration38_shallow_landing import make_source as landing_source, SOURCE_REF
from experiments.iteration38_compute import encode_depth3_padding
from experiments.dataflow_check import verify_dataflow
from tune_kernel import check
from verify_kernel import verify_emission, verify_words


def make_source(args):
    source = landing_source(argparse.Namespace(baseline=False, banks=1, contiguous=False,
        original_order=False, select_round=5, early_copy=False, setup_cap=6, full_policies=True,
        landing_start=0, landing_stop=32))
    source = encode_depth3_padding(source, repeat=not getattr(args, 'reuse_padding', False))
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)
    replace('        extra_encode_buffers = []', '''        exchange_left = vector_const(forest_values_p+n_nodes+15, "exchange_left")
        exchange_right = vector_const(forest_values_p+n_nodes+31, "exchange_right")
        exchange_exit = vector_const((-forest_values_p-n_nodes-2)&0xffffffff, "exchange_exit")
        exchange_neg4 = negative_weights[2]
        exchange_even = vector_const(0xfffffffa, "exchange_even")
        address_bases[3] = (exchange_left, exchange_right)
        direct_vectors.extend((exchange_left, exchange_right, exchange_exit,
                               exchange_even))
        extra_encode_buffers = []''')
    replace('            address_bases[4] = (vector_const(block_base+28, "block_left"),',
        '''            exchange_neg8 = negative_weights[1]
            address_bases[4] = (vector_const(block_base+28, "block_left"),''')
    replace('                depth = round_no % (forest_height + 1)', f'''                depth = round_no % (forest_height + 1)
                exchange = (round_no <= 3 and chunk_no in {tuple(args.groups)!r})''')
    replace('                        first_gather_depth = 5 if depth4_cached(round_no + 4, chunk_no) else 4',
        '''                        first_gather_depth = 3 if exchange else (5 if depth4_cached(round_no + 4, chunk_no) else 4)''')
    replace('                direct_lookup = cached_lookup and 2 <= depth <= DIRECT_PATH_DEPTH',
        '''                if exchange and depth == 3:
                    cached_lookup = False
                direct_lookup = cached_lookup and 2 <= depth <= DIRECT_PATH_DEPTH''')
    replace('                        idx_ready = select(chunk_idx, root_parity, left_base, right_base, selected)',
        '''                        if exchange:
                            left_base, right_base = exchange_left, exchange_right
                        idx_ready = select(chunk_idx, root_parity, left_base, right_base, selected)''')
    replace('                elif blocked_lookup and depth == 3:', '''                elif exchange and depth == 3:
                    node_loads = [emit("load", ("load_offset", chunk_node, chunk_idx, lane),
                                       idx_ready, block_stores) for lane in range(VLEN)]
                    node_address_reads = node_loads
                    val_ready = emit("valu", ("^", chunk_val, chunk_val, chunk_node), val_ready, node_loads)
                elif blocked_lookup and depth == 3:''')
    replace('                if blocked_lookup and depth == 4 and round_no != rounds - 1:\n                    if not self.compact_parent_index_select:',
        '''                if exchange and depth == 3:
                    idx_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, two, exchange_exit), node_address_reads)
                elif blocked_lookup and depth == 4 and round_no != rounds - 1:
                    if not self.compact_parent_index_select:''')
    replace('                        and depth >= first_gather_depth and round_no < rounds - 1',
        f'''                        and not (depth in {tuple(args.select_depths)!r} and chunk_no in {tuple(args.select_groups)!r})
                        and depth >= first_gather_depth and round_no < rounds - 1''')
    replace('                if positive_addresses:\n                    if self.compact_parent_index_select and depth in (4, 6):',
        f'''                if positive_addresses:
                    if exchange and depth in (1, 2):
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, parity_dest,
                                         exchange_neg8 if depth == 1 else exchange_neg4, chunk_idx), parity, idx_ready)
                        continue
                    if depth in {tuple(args.select_depths)!r} and chunk_no in {tuple(args.select_groups)!r}:
                        chosen_bias = select(chunk_tmp1, parity_dest, exchange_even, address_bias, parity)
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, two, chunk_tmp1),
                                         idx_ready, chosen_bias, node_address_reads)
                        continue
                    if self.compact_parent_index_select and depth in (4, 6):''')
    if getattr(args, 'parent_select', False):
        replace('        exchange_exit = vector_const((-forest_values_p-n_nodes-2)&0xffffffff, "exchange_exit")',
            '''        exchange_exit = vector_const((-forest_values_p-n_nodes-2)&0xffffffff, "exchange_exit")
        exchange_exit_even = vector_const((-forest_values_p-n_nodes-6)&0xffffffff, "exchange_exit_even")
        direct_vectors.append(exchange_exit_even)''')
        replace('''                if exchange and depth == 3:
                    idx_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, two, exchange_exit), node_address_reads)''',
            '''                if exchange and depth == 3:
                    pass  # Complete address bias is chosen after parent parity.''')
        replace('''                    if exchange and depth in (1, 2):''',
            '''                    if exchange and depth == 3:
                        chosen_bias = select(chunk_tmp1, parity_dest, exchange_exit_even, exchange_exit, parity)
                        idx_ready = emit("valu", ("multiply_add", chunk_idx, chunk_idx, two, chunk_tmp1),
                                         idx_ready, chosen_bias, node_address_reads)
                        continue
                    if exchange and depth in (1, 2):''')
    if not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):', '''        policies = ("fragment_adaptive_tail_hetero_360_220_140_140_920",
                    "balanced_adaptive_tail_hetero_360_220_140_140_920",
                    "fragment_adaptive_tail_hetero_360_220_140_140_900")
        for policy in dict.fromkeys(policies):''')
    return source


def run(args):
    module = types.ModuleType('exchange_probe')
    exec(compile(make_source(args), '<exchange_probe>', 'exec'), module.__dict__)
    class Builder(module.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
        def allocate_node_lifetimes(self, ops, uses, reusable=()):
            self.logical_slots = [op['slot'] for op in ops]
            super().allocate_node_lifetimes(ops, uses, reusable)
    builder = Builder()
    result = dict(source_ref=SOURCE_REF, **vars(args))
    stage = 'build'
    try:
        builder.build_kernel(10, 2047, 256, 16)
        stage = 'emission'
        verify_emission(builder)
        stage = 'scratch_provenance'
        verify_dataflow(builder)
        for seed in (123, 456, 789):
            stage = f'frozen_seed_{seed}'
            check(builder, seed)
        stage = 'full_word_workspace'
        verify_words(builder, 'full_word', [(i*0x9e3779b9)&0xffffffff for i in range(2047)],
                     [(i*0xabcdef01+0x80000000)&0xffffffff for i in range(256)])
    except AssertionError as error:
        result.update(rejected=stage, detail=str(error), scratch=builder.scratch_ptr)
    else:
        result.update(cycles=len(builder.instrs), scratch=builder.scratch_ptr,
                      verification='3 frozen seeds, full-word workspace, exact emission and scratch provenance')
    counts = Counter(op['engine'] for op in builder.operations)
    physical = Counter()
    for bundle in builder.instrs:
        physical.update({engine:len(slots) for engine, slots in bundle.items()})
    result.update(logical_slots=counts, slots=physical, weighted=counts['valu']+counts['alu']/8,
                  policy=builder.schedule_policy, schedule_stats=builder.schedule_stats)
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--groups', nargs='*', type=int, default=list(range(23,32)))
    parser.add_argument('--select-depths', nargs='*', type=int, default=[8,9])
    parser.add_argument('--select-groups', nargs='*', type=int, default=list(range(32)))
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--parent-select', action='store_true')
    parser.add_argument('--reuse-padding', action='store_true')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
