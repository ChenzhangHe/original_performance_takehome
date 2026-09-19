"""Probe shallow direct child landing against the pinned 941-cycle kernel.

Reorders only the shallow records to [left,parent,right,zero], then uses
strictly serialized overlapping vloads.  Landing spans remain indivisible
16-word allocations; neither machine capacities nor tests are changed.
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
from experiments.dataflow_check import verify_dataflow
from tune_kernel import check
from verify_kernel import verify_emission, verify_words

SOURCE_REF = '34742f6253c7cb1b1b2a3ca664b6b5f2d9a29991'


def make_source(args):
    source = subprocess.run(['git', 'show', f'{SOURCE_REF}:perf_takehome.py'],
                            cwd=REPO, check=True, capture_output=True, text=True).stdout
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, (source.count(old), old[:100])
        source = source.replace(old, new)
    if not args.baseline:
        replace('enumerate([block_input[0]+parent, *children])',
                'enumerate([children[0], block_input[0]+parent, children[1]])')
        replace('for index in (parent, 2*parent+1, 2*parent+2, None)',
                'for index in (2*parent+1, parent, 2*parent+2, None)')
        replace('            self.preencoded_node_count = 64',
                '''            block_tail_bases = (vector_const(block_base+29, "shallow_tail_left"),
                                vector_const(block_base+61, "shallow_tail_right"))
            direct_vectors.extend(block_tail_bases)
            self.preencoded_node_count = 64''')
        replace('                        idx_ready = select(chunk_idx, root_parity, left_base, right_base, selected)',
                '''                        if blocked_lookup and round_no > forest_height:
                            left_base, right_base = block_tail_bases
                        idx_ready = select(chunk_idx, root_parity, left_base, right_base, selected)''')
        replace('                elif self.compact_deep_landing and depth == 6:',
                '''                elif blocked_lookup and depth == 4 and round_no != rounds - 1:
                    block_use_start = len(ops)
                    block_left = block_children_base + 3*offset
                    block_right = block_left + 2*VLEN
                    pending = None
                    node_loads = []
                    block_children_ready = []
                    node_address_reads = []
                    for lane in range(VLEN):
                        read_buffer = block_left+lane
                        loaded = emit("load", ("vload", read_buffer, chunk_idx+lane),
                                      idx_ready[lane] if isinstance(idx_ready, list) else idx_ready,
                                      block_stores, pending)
                        node_address_reads.append(loaded)
                        parent = emit("alu", ("^", chunk_val+lane, chunk_val+lane, read_buffer+1), loaded, val_ready)
                        child = emit("alu", ("+", block_right+lane, read_buffer+2, readonly_zero), loaded)
                        pending = [parent, child]
                        node_loads.append(parent)
                        block_children_ready.extend((loaded, child))
                    val_ready = [*node_loads, *pending]
                elif self.compact_deep_landing and depth == 6:''')
        replace('                    if self.compact_deep_landing and depth == 7:',
                '                    if depth == 5 or (self.compact_deep_landing and depth == 7):')
        if args.banks > 1:
            start = source.index('                elif blocked_lookup and depth == 4 and round_no != rounds - 1:')
            end = source.index('                elif self.compact_deep_landing and depth == 6:', start)
            source = source[:start] + f'''                elif blocked_lookup and depth == 4 and round_no != rounds - 1:
                    block_use_start = len(ops)
                    block_left = block_children_base + 8*batch_size + chunk_count*BLOCKED_READ_BANKS*VLEN + {2*args.banks+1}*offset
                    block_right = block_left + {2*args.banks}*VLEN
                    pending = [None] * {args.banks}
                    node_loads = []
                    block_children_ready = []
                    node_address_reads = []
                    landings = []
                    for lane in range(VLEN):
                        bank = {'lane // '+str(8//args.banks) if args.contiguous else 'lane % '+str(args.banks)}
                        read_buffer = block_left + 2*VLEN*bank + lane
                        loaded = emit("load", ("vload", read_buffer, chunk_idx+lane),
                                      idx_ready[lane] if isinstance(idx_ready, list) else idx_ready,
                                      block_stores, pending[bank])
                        node_address_reads.append(loaded)
                        parent = emit("alu", ("^", chunk_val+lane, chunk_val+lane, read_buffer+1), loaded, val_ready)
                        child = emit("alu", ("+", block_right+lane, read_buffer+2, readonly_zero), loaded)
                        pending[bank] = [parent, child]
                        node_loads.append(parent)
                        block_children_ready.extend((loaded, child))
                        if bank:
                            landings.append((lane, read_buffer, loaded))
                    for lane, source_buffer, loaded in landings:
                        copied = emit("alu", ("+", block_left+lane, source_buffer, readonly_zero), loaded, pending[0])
                        block_children_ready.append(copied)
                    val_ready = [*node_loads, *[dep for group in pending for dep in group]]
                    node_pool_uses.extend((block_left+2*VLEN*bank, block_use_start, len(ops), -2)
                                          for bank in range(1, {args.banks}))
''' + source[end:]
        if args.original_order:
            assert args.banks > 1 and not args.contiguous
            replace('enumerate([children[0], block_input[0]+parent, children[1]])',
                    'enumerate([block_input[0]+parent, *children])')
            replace('for index in (2*parent+1, parent, 2*parent+2, None)',
                    'for index in (parent, 2*parent+1, 2*parent+2, None)')
            replace('''            block_tail_bases = (vector_const(block_base+29, "shallow_tail_left"),
                                vector_const(block_base+61, "shallow_tail_right"))
            direct_vectors.extend(block_tail_bases)
''', '')
            replace('''                        if blocked_lookup and round_no > forest_height:
                            left_base, right_base = block_tail_bases
''', '')
            start = source.index('                elif blocked_lookup and depth == 4 and round_no != rounds - 1:')
            end = source.index('                elif self.compact_deep_landing and depth == 6:', start)
            reader = source[start:end]
            reader = reader.replace('block_left = block_children_base +', 'block_left = 1 + block_children_base +')
            reader = reader.replace('block_right = block_left +', 'block_right = block_left - 1 +')
            reader = reader.replace('read_buffer = block_left +', 'read_buffer = block_left - 1 +')
            reader = reader.replace('read_buffer+1), loaded, val_ready)', 'read_buffer), loaded, val_ready)')
            reader = reader.replace('source_buffer, readonly_zero)', 'source_buffer+1, readonly_zero)')
            reader = reader.replace('(block_left+2*VLEN*bank,', '(block_left-1+2*VLEN*bank,')
            source = source[:start] + reader + source[end:]
            replace('node_pool_uses.append((block_left, block_use_start, len(ops), -2))',
                    'node_pool_uses.append((block_left - (1 if depth == 5 else 0), block_use_start, len(ops), -2))')
        if args.select_round != 5:
            replace('''                elif blocked_lookup and (depth == 5 or (compact_deep and depth == 7)):
                    selected''',
                    f'''                elif blocked_lookup and (depth == 5 or (compact_deep and depth == 7)):
                    if depth == 5:
                        emit_context["round"] = {args.select_round}
                    selected''')
            replace('''                    if depth == 5 or (self.compact_deep_landing and depth == 7):''',
                    '''                    emit_context["round"] = round_no
                    if depth == 5 or (self.compact_deep_landing and depth == 7):''')
        if args.early_copy:
            start = source.index('                elif blocked_lookup and depth == 4 and round_no != rounds - 1:')
            end = source.index('                elif self.compact_deep_landing and depth == 6:', start)
            reader = source[start:end]
            old = '                        child = emit("alu", ("+", block_right+lane, read_buffer+2, readonly_zero), loaded)'
            assert reader.count(old) == 1
            reader = reader.replace(old, '''                        emit_context["round"] = round_no-1
                        child = emit("alu", ("+", block_right+lane, read_buffer+2, readonly_zero), loaded)
                        emit_context["round"] = round_no''')
            source = source[:start] + reader + source[end:]
    if args.setup_cap != 4:
        replace('op["round"] = min(4, needed[i])', f'op["round"] = min({args.setup_cap}, needed[i])')
    if args.landing_start or args.landing_stop != 32:
        assert not args.baseline and not args.original_order and args.banks == 1
        predicate = f'{args.landing_start} <= chunk_no < {args.landing_stop}'
        replace('                elif blocked_lookup and depth == 4 and round_no != rounds - 1:',
                f'                elif blocked_lookup and depth == 4 and round_no != rounds - 1 and ({predicate}):')
        replace('                    block_left = block_children_base + 3*offset',
                '                    block_left = block_children_base + 8*batch_size + chunk_count*BLOCKED_READ_BANKS*VLEN + 3*offset')
        replace('                    if depth == 5 or (self.compact_deep_landing and depth == 7):',
                f'                    if (depth == 5 and {predicate}) or (self.compact_deep_landing and depth == 7):')
        replace('''                            copied = [emit("alu", ("^", chunk_val+lane, chunk_val+lane, read_buffer),
                                           loaded, val_ready)]''',
                '''                            copied = [emit("alu", ("^", chunk_val+lane, chunk_val+lane, read_buffer + (1 if depth == 4 else 0)),
                                           loaded, val_ready)]''')
        replace('for j, dest in enumerate((block_left, block_right), 1))',
                'for j, dest in (((0, block_left), (2, block_right)) if depth == 4 else enumerate((block_left, block_right), 1)))')
        # The non-fused fallback is not part of this fixed scored candidate,
        # but keep the transformed record semantics correct there as well.
        replace('for j, dest in enumerate((chunk_node, block_left, block_right))]',
                'for j, dest in (((1, chunk_node), (0, block_left), (2, block_right)) if depth == 4 else enumerate((chunk_node, block_left, block_right)))]')
    if not args.full_policies:
        replace('        for policy in dict.fromkeys(policies):',
                '''        policies = ("fragment_adaptive_tail_hetero_360_220_140_140_920",
                    "fragment_adaptive_tail_hetero_360_220_140_140_900")
        for policy in dict.fromkeys(policies):''')
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-policies', action='store_true')
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--select-round', type=int, default=5)
    parser.add_argument('--banks', type=int, choices=(1, 2, 4), default=1)
    parser.add_argument('--contiguous', action='store_true')
    parser.add_argument('--original-order', action='store_true')
    parser.add_argument('--early-copy', action='store_true')
    parser.add_argument('--setup-cap', type=int, default=4)
    parser.add_argument('--landing-start', type=int, default=0)
    parser.add_argument('--landing-stop', type=int, default=32)
    args = parser.parse_args()
    module = types.ModuleType('shallow_landing_probe')
    exec(compile(make_source(args), '<shallow_landing_probe>', 'exec'), module.__dict__)
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
                          verification='three frozen seeds, full-word workspace, exact emission and scratch provenance')
    counts = Counter(op['engine'] for op in builder.operations)
    physical = Counter()
    for bundle in builder.instrs:
        physical.update({engine:len(slots) for engine, slots in bundle.items()})
    result.update(logical_slots=counts, slots=physical,
                  weighted=counts['valu']+counts['alu']/8, policy=builder.schedule_policy,
                  schedule_stats=builder.schedule_stats)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
