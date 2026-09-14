"""Try independent child-landing chains against the pinned 955-cycle graph."""
import argparse
from collections import Counter
from pathlib import Path
import builtins
import json
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from experiments import iteration34_child_landing as landing
from tune_kernel import check
from verify_kernel import verify_emission


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--banks', type=int, choices=(1, 2, 4), default=2)
    parser.add_argument('--zero-war', action='store_true')
    parser.add_argument('--contiguous', action='store_true')
    parser.add_argument('--cache', type=int, default=7)
    parser.add_argument('--original-order', action='store_true')
    args = parser.parse_args()
    assert not args.original_order or (args.banks >= 2 and not args.contiguous)

    def compile_banked(source, filename, mode):
        if args.original_order:
            source = source.replace('enumerate([children[0], block_input[0]+parent, children[1]])',
                                    'enumerate([block_input[0]+parent, *children])', 1)
            source = source.replace('for index in (2*parent+1, parent, 2*parent+2, None)',
                                    'for index in (parent, 2*parent+1, 2*parent+2, None)', 1)
            source = source.replace('''            block_tail_bases = (vector_const(block_base+29, "block_tail_left"),
                                vector_const(block_base+61, "block_tail_right"))
            direct_vectors.extend(block_tail_bases)
''', '', 1)
            source = source.replace('''                        if blocked_lookup and round_no > forest_height:
                            left_base, right_base = block_tail_bases
''', '', 1)
        old = '            block_left = block_children_base + 3*offset\n            block_right = block_left + 2*VLEN'
        assert old in source
        source = source.replace(old, f'''            block_left = block_children_base + {int(args.original_order)} + {2*args.banks+1}*offset
            block_right = block_left - {int(args.original_order)} + {2*args.banks}*VLEN''', 1)
        start = source.index('                elif blocked_lookup and depth == 4 and round_no != rounds - 1:')
        end = source.index('                elif blocked_lookup and depth == 5:', start)
        source = source[:start] + f'''                elif blocked_lookup and depth == 4 and round_no != rounds - 1:
                    block_use_start = len(ops)
                    pending = [None] * {args.banks}
                    prior_loads = [None] * {args.banks}
                    node_loads = []
                    block_children_ready = []
                    node_address_reads = []
                    landings = []
                    for lane in range(VLEN):
                        bank = {'lane // '+str(8//args.banks) if args.contiguous else 'lane % '+str(args.banks)}
                        read_buffer = block_left - {int(args.original_order)} + 2*VLEN*bank + lane
                        loaded = emit("load", ("vload", read_buffer, chunk_idx+lane),
                                      idx_ready[lane] if isinstance(idx_ready, list) else idx_ready,
                                      block_stores, {'prior_loads[bank]' if args.zero_war else 'pending[bank]'})
                        {'ops[loaded]["same_cycle_deps"] = pending[bank] or []' if args.zero_war else ''}
                        prior_loads[bank] = loaded
                        node_address_reads.append(loaded)
                        parent = emit("alu", ("^", chunk_val+lane, chunk_val+lane, read_buffer+{1-int(args.original_order)}),
                                      loaded, val_ready)
                        child = emit("alu", ("+", block_right+lane, read_buffer+2, readonly_zero), loaded)
                        pending[bank] = [parent, child]
                        node_loads.append(parent)
                        block_children_ready.extend((loaded, child))
                        if bank:
                            landings.append((lane, read_buffer, loaded))
                    for lane, source_buffer, loaded in landings:
                        copied = emit("alu", ("+", block_left+lane, source_buffer+{int(args.original_order)}, readonly_zero),
                                      loaded, pending[0])
                        block_children_ready.append(copied)
                    val_ready = [*node_loads, *[dep for group in pending for dep in group]]
                    node_pool_uses.extend((block_left-{int(args.original_order)}+2*VLEN*bank, block_use_start, len(ops), -2)
                                          for bank in range(1, {args.banks}))
''' + source[end:]
        if args.original_order:
            source = source.replace('node_pool_uses.append((block_left, block_use_start, len(ops), -2))',
                                    'node_pool_uses.append((block_left-1, block_use_start, len(ops), -2))', 1)
        return builtins.compile(source, filename, mode)

    landing.compile = compile_banked
    m = landing.make_module(args.zero_war, source_ref='3d24666')
    m.BLOCKED_FINAL_CACHE_CHUNKS = args.cache
    class Builder(m.KernelBuilder):
        def schedule(self, ops):
            self.operations = ops
            super().schedule(ops)
    b = Builder()
    try:
        b.build_kernel(10, 2047, 256, 16)
    except AssertionError as error:
        if str(error) != 'Out of scratch space':
            raise
        print(json.dumps(dict(**vars(args), rejected='scratch', required=b.scratch_ptr)))
        return
    verify_emission(b)
    for seed in (123, 456, 789):
        check(b, seed)
    slots = Counter()
    for bundle in b.instrs:
        slots.update({engine: len(items) for engine, items in bundle.items()})
    lookup_times = [b.issue_cycles[i] for i,op in enumerate(b.operations)
                    if not op.get('is_setup', False) and op['round']>0 and op['engine']=='load'
                    and op['slot'][0] in ('load_offset','vload')]
    print(json.dumps(dict(**vars(args), cycles=len(b.instrs), scratch=b.scratch_ptr,
                          slots=slots, weighted=slots['valu']+slots['alu']/8,
                          first_lookup=min(lookup_times), last_lookup=max(lookup_times),
                          policy=b.schedule_policy)))


if __name__ == '__main__':
    main()
